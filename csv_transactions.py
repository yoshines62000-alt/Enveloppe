"""Export/import CSV des transactions - pour sauvegarder un historique dans
un tableur ou migrer des transactions depuis un autre outil, sans dependre
d'un format bancaire proprietaire."""

from __future__ import annotations

import csv
import io
import math
from datetime import datetime
from pathlib import Path
from typing import Optional

# "IDVirementLie" et "Repartition" sont des colonnes additionnelles (voir
# D11/D12 de l'audit) permettant de survivre a un aller-retour export -> import
# sans perdre le lien de virement (transfer_id) ni le fractionnement d'une
# transaction - les deux etaient auparavant perdus silencieusement a la
# reimportation (la colonne "ID" existante, elle, sert deja de cle pour
# retrouver quelle ligne du fichier correspond a quelle transaction). Ajoutees
# en FIN de liste : un CSV externe (ou un ancien export d'Enveloppe, avant
# cette correction) qui ne les fournit pas reste importable a l'identique
# (DictReader/DictWriter adressent les colonnes par nom, pas par position).
CSV_HEADER = [
    "ID", "Date", "Compte", "Categorie", "Beneficiaire", "Memo", "Montant", "Pointee",
    "IDVirementLie", "Repartition",
]

# Nombre de lignes importees entre deux commits SQLite. Un commit par ligne
# (l'ancien comportement, via db.add_transaction) force une ecriture disque
# synchrone individuelle pour chaque transaction - mesure a l'audit : 9.86s
# pour importer 2000 lignes. Grouper les commits par lot conserve le meme
# comportement fonctionnel exact (chaque ligne reste validee/rejetee
# individuellement, voir la boucle ci-dessous) tout en supprimant le
# goulot d'etranglement disque ; un lot plutot qu'un unique commit final
# limite aussi la quantite de travail perdue si le processus est interrompu
# en plein import.
_COMMIT_BATCH_SIZE = 200


class CsvImportError(Exception):
    """Fichier CSV illisible ou dont l'entete ne correspond pas au format
    attendu - distinct des lignes individuelles invalides (voir
    import_transactions_csv), qui sont simplement ignorees et rapportees."""


def _category_cell(db, tx) -> str:
    """Texte de la colonne "Categorie" pour une transaction. Une transaction
    fractionnee a category_id = NULL par design (voir
    Database.set_transaction_splits : son montant est represente uniquement
    par ses lignes de transaction_splits) - ecrire tx["category_name"] tel
    quel produirait donc une colonne VIDE, sans aucune indication qu'il
    s'agissait d'un fractionnement : perte de donnee silencieuse a l'export
    (bug trouve a l'audit ; l'IHM, elle, affiche deja "Fractionnee (N)" dans
    ce cas - voir gui.py, _refresh_transactions). Si `db` est fourni, le
    detail categorie/montant de chaque part est inclus ; sinon (compatibilite
    d'appel), seul le decompte "Fractionnee (N)" est ecrit."""
    try:
        split_count = tx["split_count"]
    except (KeyError, IndexError):
        split_count = 0
    if not split_count:
        return tx["category_name"] or ""
    if db is None:
        return f"Fractionnee ({split_count})"
    splits = db.get_transaction_splits(tx["id"])
    detail = " / ".join(f"{s['category_name']} {s['amount']:.2f}" for s in splits)
    return f"Fractionnee ({split_count}) : {detail}" if detail else f"Fractionnee ({split_count})"


def _repartition_cell(db, tx) -> str:
    """Texte machine-lisible de la colonne "Repartition", permettant de
    reconstituer le fractionnement d'une transaction a la reimportation
    (voir _parse_repartition_cell / import_transactions_csv) - distinct de
    _category_cell (colonne "Categorie"), qui reste un texte pense pour la
    LECTURE humaine ("Fractionnee (2) : Epicerie -50.00 / Transport
    -14.00") et n'est volontairement pas reparse a l'import (format pas
    garanti sans ambiguite si un nom de categorie contient lui-meme " / ").
    Format : "Categorie::Montant" par part, separees par " | " (aucun des
    deux symboles n'est un delimiteur CSV standard, donc jamais besoin
    d'echappement supplementaire). Vide si la transaction n'est pas
    fractionnee, ou si `db` n'est pas fourni (memes conditions que
    _category_cell : sans acces a la base, le detail par part n'est pas
    disponible)."""
    try:
        split_count = tx["split_count"]
    except (KeyError, IndexError):
        split_count = 0
    if not split_count or db is None:
        return ""
    splits = db.get_transaction_splits(tx["id"])
    return " | ".join(f"{s['category_name']}::{s['amount']:.2f}" for s in splits)


_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t")


def _csv_safe(value: str) -> str:
    """Neutralise une cellule texte contre l'injection de formule CSV
    (OWASP CSV Injection) : Excel/LibreOffice interpretent comme une formule
    toute cellule commencant par =, +, - ou @ (ou une tabulation) a
    l'ouverture du fichier - un beneficiaire ou memo importe depuis un CSV
    externe non fiable (ou simplement mal saisi) pourrait ainsi executer du
    code au lieu de s'afficher comme texte. Prefixer d'une apostrophe force
    le tableur a traiter la cellule comme texte litteral tout en conservant
    sa valeur lisible (l'apostrophe n'apparait pas a l'affichage)."""
    if value and value.startswith(_CSV_FORMULA_TRIGGERS):
        return "'" + value
    return value


def export_transactions_csv(transactions: list, output_path: Path, db=None) -> None:
    """transactions : lignes issues de Database.list_transactions() (avec
    account_name/category_name/split_count deja joints). `db` (optionnel) :
    passe la base active pour inclure le detail categorie/montant de chaque
    part d'une transaction fractionnee dans la colonne "Categorie" (voir
    _category_cell) ainsi que dans la colonne "Repartition" (voir
    _repartition_cell, reexploitee a l'import) ; sans lui, seul
    "Fractionnee (N)" est ecrit et "Repartition" reste vide. La colonne
    "IDVirementLie" (l'id, dans ce meme fichier, de l'autre jambe d'un
    virement) permet de meme a l'import de relier a nouveau les deux
    jambes plutot que de les laisser redevenir deux transactions ordinaires
    non liees (voir import_transactions_csv)."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig (BOM) : Excel sous Windows n'affiche correctement les
    # caracteres accentues d'un CSV UTF-8 que si le BOM est present.
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        for tx in transactions:
            transfer_id = tx["transfer_id"]
            writer.writerow([
                tx["id"], tx["date"], _csv_safe(tx["account_name"]), _csv_safe(_category_cell(db, tx)),
                _csv_safe(tx["payee"]), _csv_safe(tx["memo"]), f"{tx['amount']:.2f}", "Oui" if tx["cleared"] else "Non",
                transfer_id if transfer_id is not None else "", _csv_safe(_repartition_cell(db, tx)),
            ])


def _duplicate_key(account_id: int, date: str, amount: float, payee: str) -> tuple:
    return (account_id, date, round(amount, 2), payee.strip().lower())


# Encodages tentes dans l'ordre a la lecture d'un CSV importe. utf-8-sig en
# premier : c'est le format ecrit par export_transactions_csv lui-meme (avec
# ou sans BOM, le "-sig" gere les deux), donc le cas le plus frequent reste
# le plus rapide a resoudre. cp1252 (Windows-1252) en repli : encodage par
# defaut de nombreux exports bancaires francais et des CSV produits par
# Excel sous Windows sans choix explicite d'UTF-8 - bug trouve a l'audit :
# un tel fichier levait auparavant une UnicodeDecodeError technique brute,
# non rattrapee par import_transactions_csv elle-meme (seul le filet de
# securite generique du thread worker GUI l'empechait de faire planter
# l'application, mais avec un message anglais non actionnable).
_CSV_ENCODINGS_TO_TRY = ("utf-8-sig", "cp1252")

# Formats de date acceptes en entree, dans cet ordre. YYYY-MM-DD (ISO) en
# premier : c'est le format ecrit par export_transactions_csv, garder son
# format prioritaire evite toute ambiguite sur les propres exports
# d'Enveloppe. DD/MM/YYYY et DD-MM-YYYY ensuite : le format par defaut de la
# quasi-totalite des banques et d'Excel en France - bug trouve a l'audit :
# ce format etait rejete pour CHAQUE ligne d'un CSV bancaire francais brut.
# Les separateurs (- vs /) etant differents d'un format a l'autre, aucune
# ambiguite n'est possible entre "2026-01-05" et "05-01-2026" : le premier
# echoue sur le format DD-MM-YYYY (jour a 4 chiffres invalide) et inversement.
_CSV_DATE_INPUT_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")


def _read_csv_text(input_path: Path) -> str:
    """Lit le contenu texte integral du CSV importe, en essayant plusieurs
    encodages courants (voir _CSV_ENCODINGS_TO_TRY) avant d'abandonner avec
    un CsvImportError au message clair et actionnable, plutot que de
    laisser remonter une UnicodeDecodeError technique brute jusqu'a
    l'appelant."""
    last_error = None
    for encoding in _CSV_ENCODINGS_TO_TRY:
        try:
            with open(input_path, "r", newline="", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError as exc:
            last_error = exc
    raise CsvImportError(
        "Impossible de lire ce fichier CSV : son encodage n'est ni de l'UTF-8, ni du "
        "Windows-1252/cp1252 (les deux formats les plus courants). Reenregistrez-le en "
        f"UTF-8 depuis Excel ou LibreOffice, puis reimportez-le. (Detail technique : {last_error})"
    )


def _detect_csv_delimiter(sample: str) -> str:
    """Devine le delimiteur du CSV importe : virgule (format ecrit par
    export_transactions_csv) ou point-virgule (format quasi systematique
    des exports bancaires francais et d'Excel France "Enregistrer sous >
    CSV", ou la virgule est deja le separateur decimal) - bug trouve a
    l'audit : un CSV point-virgule etait auparavant rejete integralement
    ("colonnes Date/Montant manquantes"), csv.DictReader utilisant la
    virgule par defaut sans jamais l'essayer. csv.Sniffer() est tente en
    premier, restreint aux delimiteurs plausibles (virgule/point-virgule/
    tabulation) pour eviter qu'il ne devine un caractere exotique sur un
    echantillon ambigu ; en cas d'echec (fichier trop court, colonne
    unique...), repli explicite sur un simple comptage de la ligne
    d'entete."""
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        pass
    first_line = sample.splitlines()[0] if sample.splitlines() else ""
    return ";" if first_line.count(";") > first_line.count(",") else ","


def _parse_csv_date(raw: str) -> Optional[str]:
    """Convertit la cellule "Date" d'une ligne CSV importee vers le format
    ISO (YYYY-MM-DD) attendu par la couche donnees, en essayant chaque
    format de _CSV_DATE_INPUT_FORMATS. Renvoie None si aucun format reconnu
    ne correspond (l'appelant traite alors la ligne comme invalide, ignoree
    et rapportee - meme traitement qu'un montant invalide)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in _CSV_DATE_INPUT_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_repartition_cell(cell: Optional[str], categories_by_name: dict) -> Optional[list]:
    """Parse la colonne "Repartition" (voir _repartition_cell) vers une
    liste de {"category_id", "amount"} exploitable par
    Database.set_transaction_splits, ou None si la cellule est vide ou ne
    correspond pas au format attendu ("Categorie::Montant" par part,
    separees par " | ") - y compris si une categorie citee n'existe plus
    dans la base cible (import vers une base sans les memes categories) :
    l'appelant laisse alors la transaction non fractionnee plutot que
    d'echouer, exactement comme pour une categorie simple inconnue."""
    cell = (cell or "").strip()
    if not cell:
        return None
    splits = []
    for part in cell.split("|"):
        part = part.strip()
        if not part or "::" not in part:
            return None
        name, amount_text = part.rsplit("::", 1)
        category_id = categories_by_name.get(name.strip().lower())
        if category_id is None:
            return None
        try:
            amount = float(amount_text.strip().replace(",", "."))
        except ValueError:
            return None
        if not math.isfinite(amount):
            return None
        splits.append({"category_id": category_id, "amount": amount})
    return splits if len(splits) >= 2 else None


def import_transactions_csv(
    db, input_path: Path, default_account_id: Optional[int] = None, skip_duplicates: bool = True,
) -> dict:
    """Importe des transactions depuis un CSV au meme format que celui
    genere par export_transactions_csv (colonne ID ignoree pour la creation
    elle-meme - une nouvelle ligne est toujours creee, jamais une mise a
    jour ; elle sert uniquement, avec "IDVirementLie", a relier les deux
    jambes d'un virement entre elles, voir plus bas). Une ligne dont le
    compte ou le montant est invalide est ignoree individuellement (et
    rapportee) plutot que de faire echouer tout l'import ; si aucun compte
    ne correspond au nom indique, `default_account_id` sert de repli quand
    fourni. Une categorie inconnue ou vide laisse la transaction non
    categorisee plutot que d'echouer, puisque une transaction sans
    categorie est deja un cas normal (ex : revenu).

    Robuste aux variantes les plus courantes d'un CSV bancaire francais
    brut (voir D18/D19/D20 de l'audit) : le delimiteur (virgule ou point-
    virgule), l'encodage (UTF-8 ou Windows-1252/cp1252) et le format de date
    (ISO, ou JJ/MM/AAAA et JJ-MM-AAAA) sont detectes/acceptes automatique-
    ment plutot que figes sur le seul format ecrit par Enveloppe elle-meme.

    Par defaut (skip_duplicates=True), une ligne dont le compte, la date, le
    montant et le beneficiaire correspondent exactement a une transaction
    deja presente est ignoree plutot qu'importee - sans cela, reimporter par
    erreur deux fois le meme fichier (ou un export qui chevauche un import
    precedent) doublerait silencieusement chaque transaction concernee,
    faussant d'autant les soldes de comptes et le reste a assigner."""
    input_path = Path(input_path)
    text = _read_csv_text(input_path)
    delimiter = _detect_csv_delimiter(text[:4096])
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if reader.fieldnames is None or "Date" not in reader.fieldnames or "Montant" not in reader.fieldnames:
        raise CsvImportError(
            "Format de fichier CSV non reconnu (colonnes Date/Montant manquantes)."
        )
    rows = list(reader)

    accounts_by_name = {a["name"].strip().lower(): a["id"] for a in db.list_accounts(include_archived=True)}
    categories_by_name = {c["name"].strip().lower(): c["id"] for c in db.list_categories(include_archived=True)}
    existing_keys = set()
    if skip_duplicates:
        existing_keys = {
            _duplicate_key(tx["account_id"], tx["date"], tx["amount"], tx["payee"]) for tx in db.list_transactions()
        }

    imported = 0
    skipped = []
    duplicates = []
    # csv_id (colonne "ID" du fichier source) -> {nouvel id de transaction,
    # csv_id de l'autre jambe du virement} - permet de relier les virements
    # APRES la boucle d'import (voir plus bas) : les deux lignes d'un meme
    # virement peuvent apparaitre dans n'importe quel ordre dans le fichier,
    # la reliaison ne peut donc se faire qu'une fois toutes les lignes lues.
    imported_by_csv_id = {}

    for line_number, row in enumerate(rows, start=2):  # ligne 1 = entete
        account_name = (row.get("Compte") or "").strip().lower()
        account_id = accounts_by_name.get(account_name, default_account_id)
        if account_id is None:
            skipped.append({"line": line_number, "reason": f"compte inconnu : '{row.get('Compte', '')}'"})
            continue

        category_name = (row.get("Categorie") or "").strip().lower()
        category_id = categories_by_name.get(category_name)

        try:
            amount = float((row.get("Montant") or "").replace(",", "."))
        except ValueError:
            skipped.append({"line": line_number, "reason": f"montant invalide : '{row.get('Montant', '')}'"})
            continue
        if not math.isfinite(amount):
            # float() accepte "inf"/"nan" sans lever d'exception (contrairement
            # a un texte non numerique, deja gere ci-dessus) - une ligne CSV
            # important un montant infini ou NaN corromprait durablement les
            # soldes (voir db._validate_amount) si elle n'etait pas rejetee
            # ici, individuellement, comme les autres montants malformes.
            skipped.append({"line": line_number, "reason": f"montant invalide : '{row.get('Montant', '')}' (doit etre un nombre fini)"})
            continue

        raw_date = row.get("Date", "")
        date = _parse_csv_date(raw_date)
        if date is None:
            skipped.append({
                "line": line_number,
                "reason": f"format de date invalide : '{raw_date}' (formats acceptes : AAAA-MM-JJ, JJ/MM/AAAA, JJ-MM-AAAA)",
            })
            continue

        payee = row.get("Beneficiaire", "")

        if skip_duplicates:
            key = _duplicate_key(account_id, date, amount, payee)
            if key in existing_keys:
                duplicates.append({
                    "line": line_number,
                    "reason": "transaction identique deja presente (meme compte/date/montant/beneficiaire)",
                })
                continue
            existing_keys.add(key)  # ecarte aussi les doublons internes au fichier importe lui-meme

        cleared_text = (row.get("Pointee") or "").strip().lower()
        cleared = cleared_text in ("oui", "yes", "true", "1")

        try:
            new_id = db.add_transaction_no_commit(
                account_id, date, amount, category_id=category_id, payee=payee,
                memo=row.get("Memo", ""), cleared=cleared,
            )
        except ValueError as exc:
            skipped.append({"line": line_number, "reason": str(exc)})
            continue

        # Fractionnement (voir D12 de l'audit) : reconstitue via
        # set_transaction_splits si la colonne "Repartition" contient un
        # motif exploitable dont la somme correspond au montant importe.
        # Ecrite uniquement par l'export d'Enveloppe lui-meme (voir
        # _repartition_cell) : absente/vide sur un CSV externe ou un export
        # sans acces a `db`, ce qui laisse simplement la transaction non
        # fractionnee, exactement comme avant ce correctif.
        splits = _parse_repartition_cell(row.get("Repartition"), categories_by_name)
        if splits is not None:
            total = round(sum(s["amount"] for s in splits), 2)
            if abs(total - round(amount, 2)) <= 0.01:
                try:
                    db.set_transaction_splits(new_id, splits)
                except ValueError:
                    pass  # incoherent (fichier modifie a la main...) : transaction importee non fractionnee

        csv_id = (row.get("ID") or "").strip()
        if csv_id:
            imported_by_csv_id[csv_id] = {
                "new_id": new_id, "linked_csv_id": (row.get("IDVirementLie") or "").strip(),
            }

        imported += 1
        if imported % _COMMIT_BATCH_SIZE == 0:
            db.conn.commit()

    # Reliaison des virements (voir D11 de l'audit) : ne relie que lorsque
    # les DEUX jambes d'origine sont presentes dans imported_by_csv_id (donc
    # effectivement importees, pas ignorees comme doublon ou comme ligne
    # invalide) ET se referencent mutuellement (protection contre un
    # fichier modifie a la main qui romprait l'appariement - dans ce cas on
    # prefere ne pas relier plutot que de mal relier). Ecrit directement via
    # UPDATE plutot que Database.add_transfer (qui CREE deux nouvelles
    # transactions) : les deux jambes existent deja, il ne s'agit que de les
    # relier l'une a l'autre.
    linked_pairs_done = set()
    for csv_id, entry in imported_by_csv_id.items():
        linked_csv_id = entry["linked_csv_id"]
        if not linked_csv_id or linked_csv_id == csv_id:
            continue
        partner = imported_by_csv_id.get(linked_csv_id)
        if partner is None or partner["linked_csv_id"] != csv_id:
            continue  # reference non mutuelle (fichier incoherent) : on ne relie pas au hasard
        pair_key = frozenset((csv_id, linked_csv_id))
        if pair_key in linked_pairs_done:
            continue
        linked_pairs_done.add(pair_key)
        db.conn.execute("UPDATE transactions SET transfer_id = ? WHERE id = ?", (partner["new_id"], entry["new_id"]))
        db.conn.execute("UPDATE transactions SET transfer_id = ? WHERE id = ?", (entry["new_id"], partner["new_id"]))

    db.conn.commit()  # valide le dernier lot (incomplet) d'insertions + la reliaison des virements

    return {"imported": imported, "skipped": skipped, "duplicates": duplicates}
