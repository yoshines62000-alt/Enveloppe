"""Tests pour csv_transactions.py : export/import CSV des transactions."""

import csv
import math
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

    def test_export_marks_split_transactions_with_detail_when_db_is_provided(self):
        # Trouve a l'audit : une transaction fractionnee a category_id NULL
        # par design (voir Database.set_transaction_splits), donc la colonne
        # Categorie de l'export etait auparavant totalement vide - aucune
        # indication qu'il s'agissait d'un fractionnement (perte de donnee
        # silencieuse), alors que l'IHM affiche deja "Fractionnee (2)".
        household_id = self.db.add_category("Maison")
        tx_id = self.db.add_transaction(
            self.account_id, "2026-01-05", -100.0, category_id=self.category_id, payee="Grand magasin",
        )
        self.db.set_transaction_splits(tx_id, [
            {"category_id": self.category_id, "amount": -60.0},
            {"category_id": household_id, "amount": -40.0},
        ])
        output = self.tmp / "export.csv"
        export_transactions_csv(self.db.list_transactions(), output, db=self.db)

        with open(output, "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        cell = rows[0]["Categorie"]
        self.assertIn("Fractionnee (2)", cell)
        self.assertIn("Epicerie -60.00", cell)
        self.assertIn("Maison -40.00", cell)

    def test_export_without_db_still_flags_split_transactions_without_detail(self):
        household_id = self.db.add_category("Maison")
        tx_id = self.db.add_transaction(self.account_id, "2026-01-05", -100.0, category_id=self.category_id)
        self.db.set_transaction_splits(tx_id, [
            {"category_id": self.category_id, "amount": -60.0},
            {"category_id": household_id, "amount": -40.0},
        ])
        output = self.tmp / "export.csv"
        export_transactions_csv(self.db.list_transactions(), output)  # pas de db fourni

        with open(output, "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["Categorie"], "Fractionnee (2)")

    def test_export_of_an_ordinary_transaction_is_unaffected_by_the_split_fix(self):
        self.db.add_transaction(self.account_id, "2026-01-05", -10.0, category_id=self.category_id)
        output = self.tmp / "export.csv"
        export_transactions_csv(self.db.list_transactions(), output, db=self.db)
        with open(output, "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["Categorie"], "Epicerie")

    def test_export_neutralizes_formula_injection_in_payee_and_memo(self):
        # Trouve a l'audit : un beneficiaire ou memo importe depuis un CSV
        # externe non fiable et commencant par =, +, - ou @ etait ecrit tel
        # quel a l'export, produisant une formule executable en clair a
        # l'ouverture dans Excel/LibreOffice (OWASP CSV Injection). Preuve
        # empirique de l'audit : import de "=CMD('calc.exe')" comme
        # beneficiaire, puis re-export, produisait exactement cette valeur
        # en clair dans le fichier de sortie.
        for trigger in ("=", "+", "-", "@"):
            with self.subTest(trigger=trigger):
                self.db.add_transaction(
                    self.account_id, "2026-01-05", -10.0, category_id=self.category_id,
                    payee=f"{trigger}CMD('calc.exe')", memo=f"{trigger}HYPERLINK(\"http://evil\")",
                )
                output = self.tmp / f"export_{ord(trigger)}.csv"
                export_transactions_csv(self.db.list_transactions(), output)
                with open(output, "r", encoding="utf-8-sig") as f:
                    rows = list(csv.DictReader(f))
                row = rows[-1]
                self.assertTrue(row["Beneficiaire"].startswith("'" + trigger))
                self.assertTrue(row["Memo"].startswith("'" + trigger))
                self.assertEqual(row["Beneficiaire"], f"'{trigger}CMD('calc.exe')")

    def test_export_does_not_alter_an_ordinary_payee_or_memo(self):
        self.db.add_transaction(
            self.account_id, "2026-01-05", -10.0, category_id=self.category_id,
            payee="Supermarche du coin", memo="Courses hebdomadaires",
        )
        output = self.tmp / "export.csv"
        export_transactions_csv(self.db.list_transactions(), output)
        with open(output, "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["Beneficiaire"], "Supermarche du coin")
        self.assertEqual(rows[0]["Memo"], "Courses hebdomadaires")

    # -- D11/D12 de l'audit : colonnes additionnelles pour survivre a un
    # aller-retour export -> import (voir ImportTransactionsCsvTestCase pour
    # la reconstruction effective a la reimportation) --

    def test_export_writes_the_linked_transfer_id_column_for_both_legs_of_a_transfer(self):
        checking_id = self.account_id
        savings_id = self.db.add_account("Livret A", starting_balance=0.0)
        out_id, in_id = self.db.add_transfer(checking_id, savings_id, "2026-01-05", 250.0)

        output = self.tmp / "export.csv"
        export_transactions_csv(self.db.list_transactions(), output, db=self.db)

        with open(output, "r", encoding="utf-8-sig") as f:
            rows = {int(r["ID"]): r for r in csv.DictReader(f)}
        self.assertEqual(rows[out_id]["IDVirementLie"], str(in_id))
        self.assertEqual(rows[in_id]["IDVirementLie"], str(out_id))

    def test_export_leaves_the_linked_transfer_id_column_empty_for_an_ordinary_transaction(self):
        self.db.add_transaction(self.account_id, "2026-01-05", -10.0, category_id=self.category_id)
        output = self.tmp / "export.csv"
        export_transactions_csv(self.db.list_transactions(), output, db=self.db)
        with open(output, "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["IDVirementLie"], "")

    def test_export_writes_a_machine_readable_repartition_column_for_split_transactions(self):
        household_id = self.db.add_category("Maison")
        tx_id = self.db.add_transaction(
            self.account_id, "2026-01-05", -100.0, category_id=self.category_id, payee="Grand magasin",
        )
        self.db.set_transaction_splits(tx_id, [
            {"category_id": self.category_id, "amount": -60.0},
            {"category_id": household_id, "amount": -40.0},
        ])
        output = self.tmp / "export.csv"
        export_transactions_csv(self.db.list_transactions(), output, db=self.db)

        with open(output, "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        cell = rows[0]["Repartition"]
        self.assertIn("Epicerie::-60.00", cell)
        self.assertIn("Maison::-40.00", cell)

    def test_export_leaves_the_repartition_column_empty_for_an_unsplit_transaction(self):
        self.db.add_transaction(self.account_id, "2026-01-05", -10.0, category_id=self.category_id)
        output = self.tmp / "export.csv"
        export_transactions_csv(self.db.list_transactions(), output, db=self.db)
        with open(output, "r", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["Repartition"], "")


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

    def test_roundtrip_export_then_import_preserves_the_transfer_link(self):
        # Regression D11 de l'audit : un virement reimporte redevenait
        # auparavant deux transactions ORDINAIRES et non liees (transfer_id
        # = NULL des deux cotes), perdant silencieusement toute la
        # protection anti-desynchronisation (edition/suppression bloquee -
        # voir D8-D10) que l'application construit pourtant pour les
        # virements crees directement dans l'IHM.
        savings_id = self.db.add_account("Livret A", starting_balance=0.0)
        self.db.add_transfer(self.account_id, savings_id, "2026-01-05", 250.0)

        export_path = self.tmp / "export.csv"
        export_transactions_csv(self.db.list_transactions(), export_path, db=self.db)

        other_db = Database(self.tmp / "other.sqlite")
        self.addCleanup(other_db.close)
        other_db.add_account("Compte courant", starting_balance=0.0)
        other_db.add_account("Livret A", starting_balance=0.0)

        result = import_transactions_csv(other_db, export_path)
        self.assertEqual(result["imported"], 2)
        transactions = other_db.list_transactions()
        self.assertEqual(len(transactions), 2)
        leg_out = next(t for t in transactions if t["amount"] < 0)
        leg_in = next(t for t in transactions if t["amount"] > 0)
        self.assertIsNotNone(leg_out["transfer_id"])
        self.assertIsNotNone(leg_in["transfer_id"])
        self.assertEqual(leg_out["transfer_id"], leg_in["id"])
        self.assertEqual(leg_in["transfer_id"], leg_out["id"])
        # La reliaison protege de nouveau contre la desynchronisation, comme
        # pour un virement cree directement dans l'IHM (voir D8-D10) :
        # supprimer une jambe doit de nouveau supprimer les deux.
        other_db.delete_transfer_pair(leg_out["id"])
        self.assertEqual(other_db.list_transactions(), [])

    def test_roundtrip_export_then_import_does_not_link_an_unrelated_transaction_to_a_transfer(self):
        # Le lien ne doit se creer QUE quand les deux jambes d'origine sont
        # bel et bien presentes dans le fichier reimporte - sinon une
        # transaction ordinaire arbitraire pourrait se retrouver liee a tort
        # a une autre simplement parce qu'elle partage la meme colonne "ID".
        savings_id = self.db.add_account("Livret A", starting_balance=0.0)
        self.db.add_transfer(self.account_id, savings_id, "2026-01-05", 250.0)
        self.db.add_transaction(self.account_id, "2026-01-06", -20.0, payee="Epicerie")

        export_path = self.tmp / "export.csv"
        export_transactions_csv(
            [t for t in self.db.list_transactions() if t["transfer_id"] is None], export_path, db=self.db,
        )

        result = import_transactions_csv(self.db, export_path, skip_duplicates=False)
        self.assertEqual(result["imported"], 1)
        new_tx = sorted(self.db.list_transactions(), key=lambda t: t["id"])[-1]
        self.assertIsNone(new_tx["transfer_id"])

    def test_roundtrip_export_then_import_preserves_a_split_transaction(self):
        # Regression D12 de l'audit : le montant total restait correct,
        # mais la repartition entre enveloppes (Epicerie/Maison...) etait
        # entierement perdue a la reimportation - la transaction redevenait
        # une seule ligne non categorisee, gonflant silencieusement le
        # reste a assigner par rapport a ce que l'utilisateur avait
        # reellement budgete/depense par categorie.
        household_id = self.db.add_category("Maison")
        tx_id = self.db.add_transaction(
            self.account_id, "2026-01-05", -100.0, category_id=self.category_id, payee="Grand magasin",
        )
        self.db.set_transaction_splits(tx_id, [
            {"category_id": self.category_id, "amount": -60.0},
            {"category_id": household_id, "amount": -40.0},
        ])

        export_path = self.tmp / "export.csv"
        export_transactions_csv(self.db.list_transactions(), export_path, db=self.db)

        other_db = Database(self.tmp / "other.sqlite")
        self.addCleanup(other_db.close)
        other_db.add_account("Compte courant", starting_balance=0.0)
        other_groceries_id = other_db.add_category("Epicerie")
        other_household_id = other_db.add_category("Maison")

        result = import_transactions_csv(other_db, export_path)
        self.assertEqual(result["imported"], 1)
        imported_tx = other_db.list_transactions()[0]
        self.assertEqual(imported_tx["amount"], -100.0)
        self.assertIsNone(imported_tx["category_id"])
        self.assertEqual(imported_tx["split_count"], 2)
        splits = {s["category_id"]: s["amount"] for s in other_db.get_transaction_splits(imported_tx["id"])}
        self.assertEqual(splits, {other_groceries_id: -60.0, other_household_id: -40.0})

    def test_roundtrip_import_leaves_the_transaction_unsplit_when_a_split_category_no_longer_exists(self):
        # Base cible sans une des categories du fractionnement d'origine :
        # on prefere laisser la transaction importee non fractionnee
        # (comportement de repli identique a une categorie simple inconnue)
        # plutot que d'echouer completement l'import de la ligne.
        household_id = self.db.add_category("Maison")
        tx_id = self.db.add_transaction(self.account_id, "2026-01-05", -100.0)
        self.db.set_transaction_splits(tx_id, [
            {"category_id": self.category_id, "amount": -60.0},
            {"category_id": household_id, "amount": -40.0},
        ])
        export_path = self.tmp / "export.csv"
        export_transactions_csv(self.db.list_transactions(), export_path, db=self.db)

        other_db = Database(self.tmp / "other.sqlite")
        self.addCleanup(other_db.close)
        other_db.add_account("Compte courant", starting_balance=0.0)
        # Ni "Epicerie" ni "Maison" ne sont recreees dans la base cible.

        result = import_transactions_csv(other_db, export_path)
        self.assertEqual(result["imported"], 1)
        imported_tx = other_db.list_transactions()[0]
        self.assertEqual(imported_tx["amount"], -100.0)
        self.assertEqual(imported_tx["split_count"], 0)

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

    def test_calendar_invalid_date_is_skipped_and_reported_instead_of_inserted_silently(self):
        # Trouve a l'audit : contrairement a la GUI (qui pre-valide via
        # date.fromisoformat() avant d'appeler db.add_transaction),
        # import_transactions_csv transmet la date brute du CSV sans garde-
        # fou - "2026-02-30" passait le regex de format de db.py (jour
        # 01-31) et s'inserait silencieusement, sans aucune exception, avant
        # que db._validate_date() ne fasse aussi une vraie verification
        # calendaire. Ce test verrouille que le chemin CSV est desormais
        # protege au meme niveau que la GUI.
        path = self._write_csv([["", "2026-02-30", "Compte courant", "", "", "", "-5.00", "Non"]])
        result = import_transactions_csv(self.db, path)
        self.assertEqual(result["imported"], 0)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(self.db.list_transactions(), [])  # aucune insertion silencieuse

    def test_infinite_amount_is_skipped_and_reported_without_aborting_the_whole_import(self):
        # Trouve a l'audit : contrairement a un texte non numerique (deja
        # gere par test_invalid_amount_is_skipped_and_reported...), float()
        # accepte "inf"/"-inf" sans lever d'exception - une ligne CSV avec un
        # tel montant aurait ete importee telle quelle et aurait contamine
        # irreversiblement account_balance/total_on_budget_balance/
        # ready_to_assign (ils deviennent inf pour toute la base).
        path = self._write_csv([
            ["", "2026-01-05", "Compte courant", "", "", "", "inf", "Non"],
            ["", "2026-01-06", "Compte courant", "", "", "", "-5.00", "Non"],
        ])
        result = import_transactions_csv(self.db, path)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("montant invalide", result["skipped"][0]["reason"])
        self.assertEqual(len(self.db.list_transactions()), 1)
        self.assertTrue(math.isfinite(self.db.account_balance(self.account_id)))

    def test_nan_amount_is_skipped_and_reported_without_aborting_the_whole_import(self):
        # Trouve a l'audit : float("nan") est converti en NULL par sqlite3
        # lors du binding, violant la contrainte NOT NULL de la colonne
        # amount - ce qui levait une sqlite3.IntegrityError non geree au
        # milieu de la boucle d'import, interrompant tout le reste du CSV.
        path = self._write_csv([
            ["", "2026-01-05", "Compte courant", "", "", "", "nan", "Non"],
            ["", "2026-01-06", "Compte courant", "", "", "", "-5.00", "Non"],
        ])
        result = import_transactions_csv(self.db, path)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("montant invalide", result["skipped"][0]["reason"])
        self.assertEqual(len(self.db.list_transactions()), 1)

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

    def test_reimporting_the_same_csv_a_second_time_skips_every_row_as_a_duplicate(self):
        # Trouve a l'audit : sans detection de doublon, reimporter par
        # erreur deux fois le meme fichier doublait silencieusement chaque
        # transaction (et donc les soldes de comptes).
        path = self._write_csv([
            ["", "2026-01-05", "Compte courant", "Epicerie", "Supermarche", "", "-30.00", "Non"],
        ])
        first = import_transactions_csv(self.db, path)
        self.assertEqual(first["imported"], 1)
        self.assertEqual(first["duplicates"], [])

        second = import_transactions_csv(self.db, path)
        self.assertEqual(second["imported"], 0)
        self.assertEqual(len(second["duplicates"]), 1)
        self.assertEqual(len(self.db.list_transactions()), 1)

    def test_duplicate_rows_within_the_same_file_are_only_imported_once(self):
        path = self._write_csv([
            ["", "2026-01-05", "Compte courant", "", "Supermarche", "", "-30.00", "Non"],
            ["", "2026-01-05", "Compte courant", "", "Supermarche", "", "-30.00", "Non"],
        ])
        result = import_transactions_csv(self.db, path)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(len(result["duplicates"]), 1)
        self.assertEqual(len(self.db.list_transactions()), 1)

    def test_a_transaction_with_a_different_amount_is_not_treated_as_a_duplicate(self):
        path = self._write_csv([
            ["", "2026-01-05", "Compte courant", "", "Supermarche", "", "-30.00", "Non"],
            ["", "2026-01-05", "Compte courant", "", "Supermarche", "", "-31.00", "Non"],
        ])
        result = import_transactions_csv(self.db, path)
        self.assertEqual(result["imported"], 2)
        self.assertEqual(result["duplicates"], [])

    def test_skip_duplicates_false_disables_duplicate_detection(self):
        path = self._write_csv([
            ["", "2026-01-05", "Compte courant", "", "Supermarche", "", "-30.00", "Non"],
        ])
        import_transactions_csv(self.db, path)
        result = import_transactions_csv(self.db, path, skip_duplicates=False)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(len(self.db.list_transactions()), 2)

    # -- optimisation audit Phase 3 : commits groupes au lieu d'un par ligne --

    def test_import_commits_by_batch_instead_of_once_per_row(self):
        # Trouve a l'audit : import_transactions_csv appelait auparavant
        # db.add_transaction (qui committe individuellement) pour CHAQUE
        # ligne - 2000 lignes = 2000 commits SQLite = 9.86s mesures a
        # l'audit, l'import etant en plus synchrone sur le thread Tk
        # principal (voir gui.py). Ce test verrouille que le nombre de
        # commits reste borne (proportionnel au nombre de LOTS, pas au
        # nombre de lignes) : 450 lignes / lots de 200 -> commits a la ligne
        # 200, a la ligne 400, puis un commit final pour les 50 restantes =
        # 3 commits, jamais 450.
        rows = [
            ["", f"2026-01-{(i % 28) + 1:02d}", "Compte courant", "", f"Ligne {i}", "", f"-{i % 50 + 1}.00", "Non"]
            for i in range(450)
        ]
        path = self._write_csv(rows)

        # sqlite3.Connection est un type C : ses attributs (dont `commit`)
        # sont en lecture seule et ne peuvent pas etre patches directement
        # (contrairement a un objet Python normal). On remplace donc
        # temporairement self.db.conn par un mince proxy qui compte les
        # appels a commit() et delegue tout le reste (execute, row_factory,
        # ...) a la vraie connexion.
        class _CommitCountingConnProxy:
            def __init__(self, real_conn):
                self._real_conn = real_conn
                self.commit_count = 0

            def commit(self):
                self.commit_count += 1
                self._real_conn.commit()

            def __getattr__(self, name):
                return getattr(self._real_conn, name)

        proxy = _CommitCountingConnProxy(self.db.conn)
        real_conn = self.db.conn
        self.db.conn = proxy
        try:
            result = import_transactions_csv(self.db, path)
        finally:
            self.db.conn = real_conn

        self.assertEqual(result["imported"], 450)
        self.assertEqual(proxy.commit_count, 3)
        self.assertLess(proxy.commit_count, result["imported"])

    def test_import_of_a_large_csv_is_still_functionally_correct_across_batch_boundaries(self):
        # Les commits groupes ne doivent rien changer au COMPORTEMENT :
        # chaque ligne reste validee/rejetee individuellement (montant non
        # fini rejete, doublon detecte), meme quand des lignes valides et
        # invalides sont melangees de part et d'autre d'une frontiere de lot
        # (ici la ligne 200, avec _COMMIT_BATCH_SIZE = 200).
        rows = []
        for i in range(210):
            if i == 199:
                rows.append(["", "2026-01-05", "Compte courant", "", "Ligne invalide", "", "inf", "Non"])
            else:
                rows.append(["", f"2026-01-{(i % 28) + 1:02d}", "Compte courant", "", f"Ligne {i}", "", "-1.00", "Non"])
        path = self._write_csv(rows)

        result = import_transactions_csv(self.db, path)

        self.assertEqual(result["imported"], 209)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("montant invalide", result["skipped"][0]["reason"])
        self.assertEqual(len(self.db.list_transactions()), 209)

    def test_importing_2000_rows_is_dramatically_faster_than_the_9_86s_audited_baseline(self):
        # Mesure empirique (pas juste un comptage de commits) : reproduit le
        # volume exact de l'audit (2000 lignes) et verifie que l'import
        # reste tres largement sous le temps mesure avant optimisation
        # (9.86s, commit par ligne, sans WAL). Seuil large (2s) pour rester
        # fiable sur une machine chargee tout en detectant une regression
        # qui ferait revenir a un commit par ligne.
        import time

        rows = [
            ["", f"2026-01-{(i % 28) + 1:02d}", "Compte courant", "", f"Ligne {i}", "", f"-{i % 500}.{i % 100:02d}", "Non"]
            for i in range(2000)
        ]
        path = self._write_csv(rows)

        started = time.perf_counter()
        result = import_transactions_csv(self.db, path)
        elapsed = time.perf_counter() - started

        self.assertEqual(result["imported"], 2000)
        self.assertLess(elapsed, 2.0, "import de 2000 lignes anormalement lent (regression possible du batching)")


class FrenchBankCsvImportTestCase(unittest.TestCase):
    """D18/D19/D20 de l'audit : l'import etait fige sur la virgule comme
    delimiteur, l'encodage utf-8-sig et le format de date ISO - un export
    brut, non retouche, d'une banque francaise typique (point-virgule,
    cp1252, dates JJ/MM/AAAA) echouait quasi systematiquement sur au moins
    un de ces trois points, alors meme que le montant a virgule decimale
    (le quatrieme piege classique) etait deja gere correctement. Ce fichier
    verrouille que ce cas d'usage reel fonctionne desormais de bout en
    bout, sans rien casser du format ecrit par Enveloppe elle-meme."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "test.sqlite")
        self.addCleanup(self.db.close)
        self.account_id = self.db.add_account("Compte courant", starting_balance=500.0)
        self.category_id = self.db.add_category("Alimentation")

    def _write_raw_csv(self, content: str, encoding: str) -> Path:
        path = self.tmp / "import.csv"
        path.write_bytes(content.encode(encoding))
        return path

    def test_realistic_french_bank_csv_semicolon_cp1252_ddmmyyyy_imports_successfully(self):
        # Reproduit exactement la preuve empirique du rapport d'audit (D18+
        # D19+D20 cumules) : point-virgule, cp1252, dates JJ/MM/AAAA,
        # montant a virgule decimale, caractere accentue dans le
        # beneficiaire ("Durane" avec un e accent aigu, code cp1252 0xE9).
        content = (
            "ID;Date;Compte;Categorie;Beneficiaire;Memo;Montant;Pointee\r\n"
            ";05/01/2026;Compte courant;Alimentation;Boulangerie Duran\xe9;Pain quotidien;-4,50;Non\r\n"
            ";20/01/2026;Compte courant;;Virement recu;Salaire janvier;1500,00;Oui\r\n"
        )
        path = self.tmp / "releve_banque.csv"
        path.write_bytes(content.encode("cp1252"))

        result = import_transactions_csv(self.db, path)

        self.assertEqual(result["imported"], 2)
        self.assertEqual(result["skipped"], [])
        transactions = sorted(self.db.list_transactions(), key=lambda t: t["date"])
        self.assertEqual(transactions[0]["date"], "2026-01-05")
        self.assertEqual(transactions[0]["amount"], -4.5)
        self.assertEqual(transactions[0]["payee"], "Boulangerie Duran\xe9")
        self.assertEqual(transactions[0]["category_id"], self.category_id)
        self.assertEqual(transactions[1]["date"], "2026-01-20")
        self.assertEqual(transactions[1]["amount"], 1500.0)
        self.assertEqual(transactions[1]["cleared"], 1)

    def test_semicolon_delimiter_alone_is_detected_and_imported(self):
        content = (
            "ID;Date;Compte;Categorie;Beneficiaire;Memo;Montant;Pointee\n"
            ";2026-01-05;Compte courant;;Supermarche;;-30.00;Non\n"
        )
        path = self._write_raw_csv(content, "utf-8-sig")
        result = import_transactions_csv(self.db, path)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(self.db.list_transactions()[0]["amount"], -30.0)

    def test_cp1252_encoding_alone_is_detected_and_imported(self):
        content = (
            "ID,Date,Compte,Categorie,Beneficiaire,Memo,Montant,Pointee\n"
            ",2026-01-05,Compte courant,,Caf\xe9 du March\xe9,,-12.30,Non\n"
        )
        path = self._write_raw_csv(content, "cp1252")
        result = import_transactions_csv(self.db, path)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(self.db.list_transactions()[0]["payee"], "Caf\xe9 du March\xe9")

    def test_ddmmyyyy_date_format_alone_is_accepted(self):
        content = (
            "ID,Date,Compte,Categorie,Beneficiaire,Memo,Montant,Pointee\n"
            ",31/12/2025,Compte courant,,Fin d'annee,,-1.00,Non\n"
        )
        path = self._write_raw_csv(content, "utf-8-sig")
        result = import_transactions_csv(self.db, path)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(self.db.list_transactions()[0]["date"], "2025-12-31")

    def test_dd_dash_mm_dash_yyyy_date_format_is_also_accepted(self):
        content = (
            "ID,Date,Compte,Categorie,Beneficiaire,Memo,Montant,Pointee\n"
            ",31-12-2025,Compte courant,,Fin d'annee,,-1.00,Non\n"
        )
        path = self._write_raw_csv(content, "utf-8-sig")
        result = import_transactions_csv(self.db, path)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(self.db.list_transactions()[0]["date"], "2025-12-31")

    def test_a_calendar_invalid_ddmmyyyy_date_is_skipped_and_reported_not_silently_inserted(self):
        content = (
            "ID,Date,Compte,Categorie,Beneficiaire,Memo,Montant,Pointee\n"
            ",31/02/2026,Compte courant,,Date impossible,,-1.00,Non\n"
        )
        path = self._write_raw_csv(content, "utf-8-sig")
        result = import_transactions_csv(self.db, path)
        self.assertEqual(result["imported"], 0)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(self.db.list_transactions(), [])

    def test_still_rejects_an_unreadable_encoding_with_a_clear_actionable_message_instead_of_a_raw_traceback(self):
        # Octet 0x81 : invalide en UTF-8 (octet de tete illegal) ET non
        # defini en cp1252 - garantit que les DEUX tentatives d'encodage
        # echouent, pour verrouiller le message de repli explicite plutot
        # qu'une UnicodeDecodeError technique brute remontee telle quelle
        # (bug trouve a l'audit, D19 - la GUI la rattrapait deja via son
        # filet de securite generique, mais avec un message anglais non
        # actionnable pour l'utilisateur).
        path = self.tmp / "import_illisible.csv"
        content = b"ID,Date,Compte,Categorie,Beneficiaire,Memo,Montant,Pointee\n,2026-01-05,Compte courant,,Bad\x81Name,,-1.00,Non\n"
        path.write_bytes(content)
        with self.assertRaises(CsvImportError) as ctx:
            import_transactions_csv(self.db, path)
        self.assertIn("encod", str(ctx.exception).lower())

    def test_our_own_export_format_still_imports_correctly_after_the_csv_robustness_rework(self):
        # Non-regression explicite : le format ecrit par
        # export_transactions_csv (virgule, utf-8-sig, dates ISO) doit
        # continuer a s'importer exactement comme avant ce chantier - la
        # detection automatique ne doit jamais degrader le cas nominal.
        self.db.add_transaction(self.account_id, "2026-03-14", -9.99, category_id=self.category_id, payee="Cafe")
        export_path = self.tmp / "export.csv"
        export_transactions_csv(self.db.list_transactions(), export_path, db=self.db)

        other_db = Database(self.tmp / "other.sqlite")
        self.addCleanup(other_db.close)
        other_db.add_account("Compte courant", starting_balance=0.0)
        other_db.add_category("Alimentation")

        result = import_transactions_csv(other_db, export_path)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(other_db.list_transactions()[0]["amount"], -9.99)


class DatabaseWalModeTestCase(unittest.TestCase):
    def test_database_enables_wal_journal_mode(self):
        # Complement a l'optimisation de import_transactions_csv : le mode
        # WAL evite qu'un commit force la reecriture synchrone du fichier de
        # donnees complet (le mode par defaut, DELETE, le fait a chaque
        # commit) - determinant pour un import qui committe par lots plutot
        # que par ligne mais reste base sur des commits reels.
        tmp = Path(tempfile.mkdtemp())
        db = Database(tmp / "test.sqlite")
        self.addCleanup(db.close)
        mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")


if __name__ == "__main__":
    unittest.main()
