"""Tests pour db.py : schema SQLite, CRUD comptes/categories/transactions,
calcul du solde de compte, assignations budgetaires."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import Database


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "test.sqlite")
        self.addCleanup(self.db.close)

    def test_add_and_get_account(self):
        account_id = self.db.add_account("Compte courant", "checking", 1000.0)
        account = self.db.get_account(account_id)
        self.assertEqual(account["name"], "Compte courant")
        self.assertEqual(account["starting_balance"], 1000.0)

    def test_account_balance_reflects_starting_balance_and_transactions(self):
        account_id = self.db.add_account("Compte courant", starting_balance=1000.0)
        self.db.add_transaction(account_id, "2026-01-05", -50.0, payee="Epicerie")
        self.db.add_transaction(account_id, "2026-01-10", 200.0, payee="Salaire")
        self.assertEqual(self.db.account_balance(account_id), 1150.0)

    def test_list_accounts_excludes_archived_by_default(self):
        active = self.db.add_account("Actif")
        archived = self.db.add_account("Archive")
        self.db.update_account(archived, archived=1)
        names = [a["name"] for a in self.db.list_accounts()]
        self.assertIn("Actif", names)
        self.assertNotIn("Archive", names)

    def test_total_on_budget_balance_sums_all_active_accounts(self):
        self.db.add_account("A", starting_balance=100.0)
        self.db.add_account("B", starting_balance=250.0)
        self.assertEqual(self.db.total_on_budget_balance(), 350.0)

    def test_add_and_list_categories_ordered_by_group_then_name(self):
        self.db.add_category("Loyer", group_name="Obligations")
        self.db.add_category("Epicerie", group_name="Obligations")
        self.db.add_category("Cinema", group_name="Loisirs")
        names = [c["name"] for c in self.db.list_categories()]
        self.assertEqual(names, ["Cinema", "Epicerie", "Loyer"])  # Loisirs < Obligations alphabetiquement

    def test_set_and_get_budget_entry_upserts(self):
        category_id = self.db.add_category("Epicerie")
        self.db.set_budget_entry(category_id, "2026-01", 300.0)
        self.assertEqual(self.db.get_budget_entry(category_id, "2026-01"), 300.0)
        self.db.set_budget_entry(category_id, "2026-01", 350.0)
        self.assertEqual(self.db.get_budget_entry(category_id, "2026-01"), 350.0)

    def test_set_budget_entry_rejects_non_zero_padded_month(self):
        category_id = self.db.add_category("Epicerie")
        with self.assertRaises(ValueError):
            self.db.set_budget_entry(category_id, "2026-1", 100.0)

    def test_add_transaction_rejects_malformed_date(self):
        account_id = self.db.add_account("A")
        with self.assertRaises(ValueError):
            self.db.add_transaction(account_id, "2026-1-5", -10.0)

    def test_update_transaction_rejects_malformed_date(self):
        account_id = self.db.add_account("A")
        transaction_id = self.db.add_transaction(account_id, "2026-01-05", -10.0)
        with self.assertRaises(ValueError):
            self.db.update_transaction(transaction_id, date="not-a-date")

    def test_total_on_budget_balance_includes_archived_accounts(self):
        # "Archiver" ne fait que masquer un compte des listes de saisie ; son
        # argent reste reel et doit continuer a compter dans le total,
        # sinon le "reste a assigner" se desynchronise sans qu'aucun argent
        # n'ait bouge.
        account_id = self.db.add_account("Compte", starting_balance=500.0)
        self.db.update_account(account_id, archived=1)
        self.assertEqual(self.db.total_on_budget_balance(), 500.0)

    def test_get_budget_entry_defaults_to_zero(self):
        category_id = self.db.add_category("Epicerie")
        self.assertEqual(self.db.get_budget_entry(category_id, "2026-05"), 0.0)

    def test_sum_assigned_up_to_is_cumulative_across_months(self):
        category_id = self.db.add_category("Epicerie")
        self.db.set_budget_entry(category_id, "2026-01", 300.0)
        self.db.set_budget_entry(category_id, "2026-02", 300.0)
        self.db.set_budget_entry(category_id, "2026-03", 300.0)
        self.assertEqual(self.db.sum_assigned_up_to(category_id, "2026-02"), 600.0)
        self.assertEqual(self.db.sum_assigned_up_to(category_id, "2026-03"), 900.0)

    def test_total_assigned_all_time_sums_every_category_and_month(self):
        cat_a = self.db.add_category("A")
        cat_b = self.db.add_category("B")
        self.db.set_budget_entry(cat_a, "2026-01", 100.0)
        self.db.set_budget_entry(cat_b, "2026-01", 50.0)
        self.assertEqual(self.db.total_assigned_all_time(), 150.0)

    def test_transactions_filtered_by_account_and_category(self):
        account_a = self.db.add_account("A")
        account_b = self.db.add_account("B")
        cat_food = self.db.add_category("Epicerie")
        cat_fun = self.db.add_category("Loisirs")
        self.db.add_transaction(account_a, "2026-01-05", -20.0, category_id=cat_food)
        self.db.add_transaction(account_b, "2026-01-06", -30.0, category_id=cat_fun)

        by_account = self.db.list_transactions(account_id=account_a)
        self.assertEqual(len(by_account), 1)
        by_category = self.db.list_transactions(category_id=cat_fun)
        self.assertEqual(len(by_category), 1)
        self.assertEqual(by_category[0]["account_name"], "B")

    def test_sum_transactions_up_to_is_cumulative(self):
        account_id = self.db.add_account("A")
        cat = self.db.add_category("Epicerie")
        self.db.add_transaction(account_id, "2026-01-05", -20.0, category_id=cat)
        self.db.add_transaction(account_id, "2026-02-05", -30.0, category_id=cat)
        self.assertEqual(self.db.sum_transactions_up_to(cat, "2026-01"), -20.0)
        self.assertEqual(self.db.sum_transactions_up_to(cat, "2026-02"), -50.0)

    def test_sum_transactions_for_month_is_not_cumulative(self):
        account_id = self.db.add_account("A")
        cat = self.db.add_category("Epicerie")
        self.db.add_transaction(account_id, "2026-01-05", -20.0, category_id=cat)
        self.db.add_transaction(account_id, "2026-02-05", -30.0, category_id=cat)
        self.assertEqual(self.db.sum_transactions_for_month(cat, "2026-02"), -30.0)

    def test_update_and_delete_transaction(self):
        account_id = self.db.add_account("A")
        cat = self.db.add_category("Epicerie")
        transaction_id = self.db.add_transaction(account_id, "2026-01-05", -20.0, category_id=cat)
        self.db.update_transaction(transaction_id, amount=-25.0, memo="Correction")
        updated = self.db.get_transaction(transaction_id)
        self.assertEqual(updated["amount"], -25.0)
        self.assertEqual(updated["memo"], "Correction")

        self.db.delete_transaction(transaction_id)
        self.assertIsNone(self.db.get_transaction(transaction_id))


class TransferTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "test.sqlite")
        self.addCleanup(self.db.close)
        self.checking_id = self.db.add_account("Courant", starting_balance=1000.0)
        self.savings_id = self.db.add_account("Epargne", starting_balance=200.0)

    def test_add_transfer_creates_two_linked_transactions_with_opposite_amounts(self):
        out_id, in_id = self.db.add_transfer(self.checking_id, self.savings_id, "2026-01-05", 100.0)
        out_tx = self.db.get_transaction(out_id)
        in_tx = self.db.get_transaction(in_id)
        self.assertEqual(out_tx["amount"], -100.0)
        self.assertEqual(in_tx["amount"], 100.0)
        self.assertEqual(out_tx["transfer_id"], in_id)
        self.assertEqual(in_tx["transfer_id"], out_id)

    def test_transfer_legs_are_never_attached_to_a_category(self):
        out_id, in_id = self.db.add_transfer(self.checking_id, self.savings_id, "2026-01-05", 100.0)
        self.assertIsNone(self.db.get_transaction(out_id)["category_id"])
        self.assertIsNone(self.db.get_transaction(in_id)["category_id"])

    def test_transfer_moves_money_between_accounts_without_changing_the_total_balance(self):
        total_before = self.db.total_on_budget_balance()
        self.db.add_transfer(self.checking_id, self.savings_id, "2026-01-05", 100.0)
        self.assertEqual(self.db.account_balance(self.checking_id), 900.0)
        self.assertEqual(self.db.account_balance(self.savings_id), 300.0)
        self.assertEqual(self.db.total_on_budget_balance(), total_before)

    def test_add_transfer_rejects_the_same_account_on_both_sides(self):
        with self.assertRaises(ValueError):
            self.db.add_transfer(self.checking_id, self.checking_id, "2026-01-05", 50.0)

    def test_add_transfer_rejects_a_zero_amount(self):
        with self.assertRaises(ValueError):
            self.db.add_transfer(self.checking_id, self.savings_id, "2026-01-05", 0.0)

    def test_add_transfer_accepts_a_negative_amount_as_an_absolute_value(self):
        out_id, in_id = self.db.add_transfer(self.checking_id, self.savings_id, "2026-01-05", -100.0)
        self.assertEqual(self.db.get_transaction(out_id)["amount"], -100.0)
        self.assertEqual(self.db.get_transaction(in_id)["amount"], 100.0)

    def test_delete_transfer_pair_removes_both_legs(self):
        out_id, in_id = self.db.add_transfer(self.checking_id, self.savings_id, "2026-01-05", 100.0)
        self.db.delete_transfer_pair(out_id)
        self.assertIsNone(self.db.get_transaction(out_id))
        self.assertIsNone(self.db.get_transaction(in_id))

    def test_deleting_a_single_leg_unlinks_the_remaining_leg_instead_of_leaving_a_dangling_reference(self):
        out_id, in_id = self.db.add_transfer(self.checking_id, self.savings_id, "2026-01-05", 100.0)
        self.db.delete_transaction(out_id)
        remaining = self.db.get_transaction(in_id)
        self.assertIsNotNone(remaining)
        self.assertIsNone(remaining["transfer_id"])

    def test_delete_transfer_pair_on_an_ordinary_transaction_behaves_like_delete_transaction(self):
        transaction_id = self.db.add_transaction(self.checking_id, "2026-01-05", -20.0)
        self.db.delete_transfer_pair(transaction_id)
        self.assertIsNone(self.db.get_transaction(transaction_id))

    def test_reopening_a_pre_transfer_database_file_adds_the_missing_column(self):
        # Simule une base de donnees creee avant l'ajout de transfer_id : la
        # colonne ne doit pas empecher la reouverture, ni faire planter les
        # anciennes transactions deja stockees.
        self.db.close()
        import sqlite3

        old_style_path = self.tmp / "old.sqlite"
        conn = sqlite3.connect(str(old_style_path))
        conn.executescript("""
            CREATE TABLE accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, type TEXT NOT NULL DEFAULT '',
                starting_balance REAL NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
            );
            CREATE TABLE categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, group_name TEXT NOT NULL DEFAULT '',
                archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
            );
            CREATE TABLE budget_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, category_id INTEGER NOT NULL, month TEXT NOT NULL,
                assigned REAL NOT NULL DEFAULT 0, UNIQUE(category_id, month)
            );
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL, category_id INTEGER,
                date TEXT NOT NULL, payee TEXT NOT NULL DEFAULT '', memo TEXT NOT NULL DEFAULT '',
                amount REAL NOT NULL, cleared INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
            );
        """)
        conn.execute(
            "INSERT INTO accounts (name, starting_balance, created_at) VALUES ('Ancien compte', 500.0, '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO transactions (account_id, date, payee, amount, created_at) "
            "VALUES (1, '2026-01-05', 'Ancienne depense', -20.0, '2026-01-05')"
        )
        conn.commit()
        conn.close()

        reopened = Database(old_style_path)
        self.addCleanup(reopened.close)
        transactions = reopened.list_transactions()
        self.assertEqual(len(transactions), 1)
        self.assertIsNone(transactions[0]["transfer_id"])
        # La base migree doit rester utilisable normalement, y compris pour
        # de nouveaux virements.
        savings_id = reopened.add_account("Nouvelle epargne")
        reopened.add_transfer(1, savings_id, "2026-01-10", 50.0)


class TransactionSplitTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "test.sqlite")
        self.addCleanup(self.db.close)
        self.account_id = self.db.add_account("Compte", starting_balance=1000.0)
        self.groceries_id = self.db.add_category("Epicerie")
        self.household_id = self.db.add_category("Maison")
        self.transaction_id = self.db.add_transaction(
            self.account_id, "2026-01-05", -100.0, category_id=self.groceries_id, payee="Grand magasin",
        )

    def test_setting_splits_clears_the_transaction_own_category(self):
        self.db.set_transaction_splits(self.transaction_id, [
            {"category_id": self.groceries_id, "amount": -60.0},
            {"category_id": self.household_id, "amount": -40.0},
        ])
        tx = self.db.get_transaction(self.transaction_id)
        self.assertIsNone(tx["category_id"])

    def test_split_amounts_must_sum_to_the_transaction_amount(self):
        with self.assertRaises(ValueError):
            self.db.set_transaction_splits(self.transaction_id, [
                {"category_id": self.groceries_id, "amount": -60.0},
                {"category_id": self.household_id, "amount": -30.0},  # ne totalise que -90, pas -100
            ])

    def test_a_single_split_is_rejected_as_not_a_real_split(self):
        with self.assertRaises(ValueError):
            self.db.set_transaction_splits(self.transaction_id, [{"category_id": self.groceries_id, "amount": -100.0}])

    def test_get_transaction_splits_returns_the_stored_rows_with_category_name(self):
        self.db.set_transaction_splits(self.transaction_id, [
            {"category_id": self.groceries_id, "amount": -60.0, "memo": "Nourriture"},
            {"category_id": self.household_id, "amount": -40.0},
        ])
        splits = self.db.get_transaction_splits(self.transaction_id)
        self.assertEqual(len(splits), 2)
        self.assertEqual(splits[0]["category_name"], "Epicerie")
        self.assertEqual(splits[0]["memo"], "Nourriture")

    def test_budget_activity_sums_splits_instead_of_the_absent_category_id(self):
        self.db.set_transaction_splits(self.transaction_id, [
            {"category_id": self.groceries_id, "amount": -60.0},
            {"category_id": self.household_id, "amount": -40.0},
        ])
        self.assertEqual(self.db.sum_transactions_for_month(self.groceries_id, "2026-01"), -60.0)
        self.assertEqual(self.db.sum_transactions_for_month(self.household_id, "2026-01"), -40.0)
        self.assertEqual(self.db.sum_transactions_up_to(self.groceries_id, "2026-01"), -60.0)

    def test_a_split_transaction_is_not_double_counted_via_its_own_now_null_category(self):
        # Avant fractionnement, toute la depense comptait sur Epicerie ;
        # apres, seule la part fractionnee doit compter - jamais les deux.
        before = self.db.sum_transactions_for_month(self.groceries_id, "2026-01")
        self.assertEqual(before, -100.0)
        self.db.set_transaction_splits(self.transaction_id, [
            {"category_id": self.groceries_id, "amount": -60.0},
            {"category_id": self.household_id, "amount": -40.0},
        ])
        after = self.db.sum_transactions_for_month(self.groceries_id, "2026-01")
        self.assertEqual(after, -60.0)

    def test_re_splitting_replaces_the_previous_split_rather_than_accumulating(self):
        self.db.set_transaction_splits(self.transaction_id, [
            {"category_id": self.groceries_id, "amount": -60.0},
            {"category_id": self.household_id, "amount": -40.0},
        ])
        self.db.set_transaction_splits(self.transaction_id, [
            {"category_id": self.groceries_id, "amount": -100.0},
            {"category_id": self.household_id, "amount": 0.0},
        ])
        splits = self.db.get_transaction_splits(self.transaction_id)
        self.assertEqual(len(splits), 2)
        self.assertEqual(self.db.sum_transactions_for_month(self.groceries_id, "2026-01"), -100.0)

    def test_deleting_a_split_transaction_removes_its_split_rows_too(self):
        self.db.set_transaction_splits(self.transaction_id, [
            {"category_id": self.groceries_id, "amount": -60.0},
            {"category_id": self.household_id, "amount": -40.0},
        ])
        self.db.delete_transaction(self.transaction_id)
        self.assertEqual(self.db.get_transaction_splits(self.transaction_id), [])

    def test_assigning_a_new_category_via_update_transaction_clears_an_existing_split(self):
        self.db.set_transaction_splits(self.transaction_id, [
            {"category_id": self.groceries_id, "amount": -60.0},
            {"category_id": self.household_id, "amount": -40.0},
        ])
        self.db.update_transaction(self.transaction_id, category_id=self.household_id)
        self.assertEqual(self.db.get_transaction_splits(self.transaction_id), [])
        self.assertEqual(self.db.get_transaction(self.transaction_id)["category_id"], self.household_id)

    def test_list_transactions_reports_a_split_count_for_split_transactions(self):
        self.db.set_transaction_splits(self.transaction_id, [
            {"category_id": self.groceries_id, "amount": -60.0},
            {"category_id": self.household_id, "amount": -40.0},
        ])
        tx = self.db.list_transactions()[0]
        self.assertEqual(tx["split_count"], 2)

    def test_list_transactions_reports_zero_split_count_for_an_ordinary_transaction(self):
        tx = self.db.list_transactions()[0]
        self.assertEqual(tx["split_count"], 0)

    def test_clear_transaction_splits_removes_the_splits_without_restoring_a_category(self):
        self.db.set_transaction_splits(self.transaction_id, [
            {"category_id": self.groceries_id, "amount": -60.0},
            {"category_id": self.household_id, "amount": -40.0},
        ])
        self.db.clear_transaction_splits(self.transaction_id)
        self.assertEqual(self.db.get_transaction_splits(self.transaction_id), [])
        self.assertIsNone(self.db.get_transaction(self.transaction_id)["category_id"])


if __name__ == "__main__":
    unittest.main()
