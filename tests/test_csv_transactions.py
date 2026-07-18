"""Tests pour csv_transactions.py : export/import CSV des transactions."""

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import Database
from csv_transactions import CsvImportError, export_transactions_csv, import_transactions_csv


class ExportTransactionsCsvTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "test.sqlite")
        self.addCleanup(self.db.close)
        self.account_id = self.db.add_account("Compte courant", starting_balance=500.0)
        self.category_id = self.db.add_category("Epicerie")

    def test_export_writes_one_row_per_transaction_with_joined_names(self):
        self.db.add_transaction(self.account_id, "2026-01-05", -42.5, category_id=self.category_id, payee="Supermarche")
        output = self.tmp / "export.csv"
        export_transactions_csv(self.db.list_transactions(), output)

        with open(output, "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Compte"], "Compte courant")
        self.assertEqual(rows[0]["Categorie"], "Epicerie")
        self.assertEqual(rows[0]["Montant"], "-42.50")

    def test_export_with_no_transactions_writes_only_the_header(self):
        output = self.tmp / "empty.csv"
        export_transactions_csv([], output)
        with open(output, "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows, [])

    def test_export_creates_missing_parent_directories(self):
        output = self.tmp / "sous_dossier" / "export.csv"
        export_transactions_csv([], output)
        self.assertTrue(output.exists())


class ImportTransactionsCsvTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "test.sqlite")
        self.addCleanup(self.db.close)
        self.account_id = self.db.add_account("Compte courant", starting_balance=500.0)
        self.category_id = self.db.add_category("Epicerie")

    def _write_csv(self, rows, header=None):
        path = self.tmp / "import.csv"
        header = header or ["ID", "Date", "Compte", "Categorie", "Beneficiaire", "Memo", "Montant", "Pointee"]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)
        return path

    def test_import_creates_transactions_matched_by_account_and_category_name(self):
        path = self._write_csv([["", "2026-01-05", "Compte courant", "Epicerie", "Supermarche", "", "-30.00", "Non"]])
        result = import_transactions_csv(self.db, path)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["skipped"], [])
        transactions = self.db.list_transactions()
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]["amount"], -30.0)
        self.assertEqual(transactions[0]["category_id"], self.category_id)

    def test_roundtrip_export_then_import_recreates_equivalent_transactions(self):
        self.db.add_transaction(self.account_id, "2026-02-10", -15.75, category_id=self.category_id, payee="Boulangerie")
        export_path = self.tmp / "export.csv"
        export_transactions_csv(self.db.list_transactions(), export_path)

        other_db = Database(self.tmp / "other.sqlite")
        self.addCleanup(other_db.close)
        other_account_id = other_db.add_account("Compte courant", starting_balance=0.0)
        other_db.add_category("Epicerie")

        result = import_transactions_csv(other_db, export_path)
        self.assertEqual(result["imported"], 1)
        imported_tx = other_db.list_transactions()[0]
        self.assertEqual(imported_tx["amount"], -15.75)
        self.assertEqual(imported_tx["payee"], "Boulangerie")
        self.assertEqual(imported_tx["account_id"], other_account_id)

    def test_unknown_account_falls_back_to_default_account_id_when_provided(self):
        path = self._write_csv([["", "2026-01-05", "Compte inexistant", "", "", "", "-10.00", "Non"]])
        result = import_transactions_csv(self.db, path, default_account_id=self.account_id)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(self.db.list_transactions()[0]["account_id"], self.account_id)

    def test_unknown_account_without_default_is_skipped_and_reported(self):
        path = self._write_csv([["", "2026-01-05", "Compte inexistant", "", "", "", "-10.00", "Non"]])
        result = import_transactions_csv(self.db, path)
        self.assertEqual(result["imported"], 0)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("compte inconnu", result["skipped"][0]["reason"])

    def test_unknown_category_leaves_the_transaction_uncategorized_instead_of_skipping(self):
        path = self._write_csv([["", "2026-01-05", "Compte courant", "Categorie fantome", "", "", "-10.00", "Non"]])
        result = import_transactions_csv(self.db, path)
        self.assertEqual(result["imported"], 1)
        self.assertIsNone(self.db.list_transactions()[0]["category_id"])

    def test_invalid_amount_is_skipped_and_reported_without_aborting_the_whole_import(self):
        path = self._write_csv([
            ["", "2026-01-05", "Compte courant", "", "", "", "pas-un-nombre", "Non"],
            ["", "2026-01-06", "Compte courant", "", "", "", "-5.00", "Non"],
        ])
        result = import_transactions_csv(self.db, path)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("montant invalide", result["skipped"][0]["reason"])

    def test_invalid_date_is_skipped_and_reported(self):
        path = self._write_csv([["", "pas-une-date", "Compte courant", "", "", "", "-5.00", "Non"]])
        result = import_transactions_csv(self.db, path)
        self.assertEqual(result["imported"], 0)
        self.assertEqual(len(result["skipped"]), 1)

    def test_cleared_column_accepts_oui_non_case_insensitively(self):
        path = self._write_csv([
            ["", "2026-01-05", "Compte courant", "", "", "", "-5.00", "OUI"],
            ["", "2026-01-06", "Compte courant", "", "", "", "-5.00", "non"],
        ])
        import_transactions_csv(self.db, path)
        transactions = sorted(self.db.list_transactions(), key=lambda t: t["date"])
        self.assertEqual(transactions[0]["cleared"], 1)
        self.assertEqual(transactions[1]["cleared"], 0)

    def test_file_with_wrong_header_raises_csv_import_error(self):
        path = self._write_csv([["1", "2", "3"]], header=["Colonne A", "Colonne B", "Colonne C"])
        with self.assertRaises(CsvImportError):
            import_transactions_csv(self.db, path)


if __name__ == "__main__":
    unittest.main()
