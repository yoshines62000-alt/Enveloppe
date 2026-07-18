"""Export/import CSV des transactions - pour sauvegarder un historique dans
un tableur ou migrer des transactions depuis un autre outil, sans dependre
d'un format bancaire proprietaire."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

CSV_HEADER = ["ID", "Date", "Compte", "Categorie", "Beneficiaire", "Memo", "Montant", "Pointee"]


class CsvImportError(Exception):
    """Fichier CSV illisible ou dont l'entete ne correspond pas au format
    attendu - distinct des lignes individuelles invalides (voir
    import_transactions_csv), qui sont simplement ignorees et rapportees."""


def export_transactions_csv(transactions: list, output_path: Path) -> None:
    """transactions : lignes issues de Database.list_transactions() (avec
    account_name/category_name deja jointes)."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig (BOM) : Excel sous Windows n'affiche correctement les
    # caracteres accentues d'un CSV UTF-8 que si le BOM est present.
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        for tx in transactions:
            writer.writerow([
                tx["id"], tx["date"], tx["account_name"], tx["category_name"] or "",
                tx["payee"], tx["memo"], f"{tx['amount']:.2f}", "Oui" if tx["cleared"] else "Non",
            ])


def import_transactions_csv(db, input_path: Path, default_account_id: Optional[int] = None) -> dict:
    """Importe des transactions depuis un CSV au meme format que celui
    genere par export_transactions_csv (colonne ID ignoree - une nouvelle
    ligne est toujours creee, jamais une mise a jour). Une ligne dont le
    compte ou le montant est invalide est ignoree individuellement (et
    rapportee) plutot que de faire echouer tout l'import ; si aucun compte
    ne correspond au nom indique, `default_account_id` sert de repli quand
    fourni. Une categorie inconnue ou vide laisse la transaction non
    categorisee plutot que d'echouer, puisque une transaction sans
    categorie est deja un cas normal (ex : revenu)."""
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

    imported = 0
    skipped = []
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

        cleared_text = (row.get("Pointee") or "").strip().lower()
        cleared = cleared_text in ("oui", "yes", "true", "1")

        try:
            db.add_transaction(
                account_id, row.get("Date", ""), amount,
                category_id=category_id, payee=row.get("Beneficiaire", ""), memo=row.get("Memo", ""),
                cleared=cleared,
            )
            imported += 1
        except ValueError as exc:
            skipped.append({"line": line_number, "reason": str(exc)})

    return {"imported": imported, "skipped": skipped}
