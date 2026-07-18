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


if __name__ == "__main__":
    unittest.main()
