"""Export/import CSV des transactions - pour sauvegarder un historique dans
un tableur ou migrer des transactions depuis un autre outil, sans dependre
d'un format bancaire proprietaire."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Optional

CSV_HEADER = ["ID", "Date", "Compte", "Categorie", "Beneficiaire", "Memo", "Montant", "Pointee"]


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
    _category_cell) ; sans lui, seul "Fractionnee (N)" est ecrit."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig (BOM) : Excel sous Windows n'affiche correctement les
    # caracteres accentues d'un CSV UTF-8 que si le BOM est present.
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        for tx in transactions:
            writer.writerow([
                tx["id"], tx["date"], _csv_safe(tx["account_name"]), _csv_safe(_category_cell(db, tx)),
                _csv_safe(tx["payee"]), _csv_safe(tx["memo"]), f"{tx['amount']:.2f}", "Oui" if tx["cleared"] else "Non",
            ])


def _duplicate_key(account_id: int, date: str, amount: float, payee: str) -> tuple:
    return (account_id, date, round(amount, 2), payee.strip().lower())


def import_transactions_csv(
    db, input_path: Path, default_account_id: Optional[int] = None, skip_duplicates: bool = True,
) -> dict:
    """Importe des transactions depuis un CSV au meme format que celui
    genere par export_transactions_csv (colonne ID ignoree - une nouvelle
    ligne est toujours creee, jamais une mise a jour). Une ligne dont le
    compte ou le montant est invalide est ignoree individuellement (et
    rapportee) plutot que de faire echouer tout l'import ; si aucun compte
    ne correspond au nom indique, `default_account_id` sert de repli quand
    fourni. Une categorie inconnue ou vide laisse la transaction non
    categorisee plutot que d'echouer, puisque une transaction sans
    categorie est deja un cas normal (ex : revenu).

    Par defaut (skip_duplicates=True), une ligne dont le compte, la date, le
    montant et le beneficiaire correspondent exactement a une transaction
    deja presente est ignoree plutot qu'importee - sans cela, reimporter par
    erreur deux fois le meme fichier (ou un export qui chevauche un import
    precedent) doublerait silencieusement chaque transaction concernee,
    faussant d'autant les soldes de comptes et le reste a assigner."""
    input_path = Path(input_path)
    with open(input_path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
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

        date = row.get("Date", "")
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
            db.add_transaction(
                account_id, date, amount, category_id=category_id, payee=payee,
                memo=row.get("Memo", ""), cleared=cleared,
            )
            imported += 1
        except ValueError as exc:
            skipped.append({"line": line_number, "reason": str(exc)})

    return {"imported": imported, "skipped": skipped, "duplicates": duplicates}
