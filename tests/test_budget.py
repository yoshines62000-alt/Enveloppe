"""Tests pour budget.py : navigation de mois, report d'enveloppe (rollover),
calcul de 'reste a assigner' - toute la logique zero-based budgeting."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import Database
import budget as bg


class MonthHelpersTestCase(unittest.TestCase):
    def test_shift_month_forward_within_year(self):
        self.assertEqual(bg.shift_month("2026-01", 1), "2026-02")

    def test_shift_month_forward_across_year_boundary(self):
        self.assertEqual(bg.shift_month("2026-12", 1), "2027-01")

    def test_shift_month_backward_across_year_boundary(self):
        self.assertEqual(bg.shift_month("2026-01", -1), "2025-12")

    def test_shift_month_by_multiple_months(self):
        self.assertEqual(bg.shift_month("2026-01", 14), "2027-03")

    def test_shift_month_rejects_invalid_format(self):
        with self.assertRaises(ValueError):
            bg.shift_month("not-a-month", 1)

    def test_month_key_extracts_year_month_from_iso_date(self):
        self.assertEqual(bg.month_key("2026-03-15T10:00:00+00:00"), "2026-03")

    def test_month_label_is_human_readable(self):
        self.assertEqual(bg.month_label("2026-01"), "Janvier 2026")
        self.assertEqual(bg.month_label("2026-12"), "Decembre 2026")

    def test_month_label_rejects_month_out_of_range(self):
        with self.assertRaises(ValueError):
            bg.month_label("2026-13")
        with self.assertRaises(ValueError):
            bg.month_label("2026-00")

    def test_shift_month_rejects_month_out_of_range(self):
        with self.assertRaises(ValueError):
            bg.shift_month("2026-13", 1)
        with self.assertRaises(ValueError):
            bg.shift_month("2026-00", 1)


class CategoryAvailableTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "test.sqlite")
        self.addCleanup(self.db.close)
        self.account_id = self.db.add_account("Compte", starting_balance=1000.0)
        self.category_id = self.db.add_category("Epicerie")

    def test_unspent_envelope_rolls_over_to_next_month(self):
        self.db.set_budget_entry(self.category_id, "2026-01", 300.0)
        self.db.add_transaction(self.account_id, "2026-01-10", -100.0, category_id=self.category_id)
        # Rien assigne en fevrier : le solde restant de janvier (200) doit
        # se reporter automatiquement.
        available_jan = bg.category_available(self.db, self.category_id, "2026-01")
        available_feb = bg.category_available(self.db, self.category_id, "2026-02")
        self.assertEqual(available_jan, 200.0)
        self.assertEqual(available_feb, 200.0)

    def test_overspending_carries_over_as_a_negative_balance(self):
        self.db.set_budget_entry(self.category_id, "2026-01", 100.0)
        self.db.add_transaction(self.account_id, "2026-01-10", -150.0, category_id=self.category_id)
        available_jan = bg.category_available(self.db, self.category_id, "2026-01")
        available_feb = bg.category_available(self.db, self.category_id, "2026-02")
        self.assertEqual(available_jan, -50.0)
        self.assertEqual(available_feb, -50.0)  # reste negatif tant que rien n'est reassigne

    def test_assigning_more_next_month_absorbs_previous_overspending(self):
        self.db.set_budget_entry(self.category_id, "2026-01", 100.0)
        self.db.add_transaction(self.account_id, "2026-01-10", -150.0, category_id=self.category_id)
        self.db.set_budget_entry(self.category_id, "2026-02", 100.0)
        available_feb = bg.category_available(self.db, self.category_id, "2026-02")
        self.assertEqual(available_feb, 50.0)  # -50 + 100 assigne en fevrier

    def test_activity_for_month_is_not_cumulative(self):
        self.db.add_transaction(self.account_id, "2026-01-10", -40.0, category_id=self.category_id)
        self.db.add_transaction(self.account_id, "2026-02-10", -60.0, category_id=self.category_id)
        self.assertEqual(bg.category_activity_for_month(self.db, self.category_id, "2026-01"), -40.0)
        self.assertEqual(bg.category_activity_for_month(self.db, self.category_id, "2026-02"), -60.0)

    def test_future_month_with_no_activity_shows_pure_rollover(self):
        self.db.set_budget_entry(self.category_id, "2026-01", 300.0)
        available_far_future = bg.category_available(self.db, self.category_id, "2027-06")
        self.assertEqual(available_far_future, 300.0)


class ReadyToAssignTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "test.sqlite")
        self.addCleanup(self.db.close)

    def test_ready_to_assign_is_total_balance_when_nothing_assigned(self):
        self.db.add_account("Compte", starting_balance=1000.0)
        self.assertEqual(bg.ready_to_assign(self.db), 1000.0)

    def test_ready_to_assign_decreases_as_money_is_assigned(self):
        self.db.add_account("Compte", starting_balance=1000.0)
        category_id = self.db.add_category("Epicerie")
        self.db.set_budget_entry(category_id, "2026-01", 300.0)
        self.assertEqual(bg.ready_to_assign(self.db), 700.0)

    def test_unassigned_income_increases_ready_to_assign(self):
        account_id = self.db.add_account("Compte", starting_balance=0.0)
        # Un revenu (salaire) sans categorie : augmente l'argent disponible
        # mais n'est assigne a aucune enveloppe.
        self.db.add_transaction(account_id, "2026-01-01", 2000.0, payee="Salaire")
        self.assertEqual(bg.ready_to_assign(self.db), 2000.0)

    def test_archiving_a_nonempty_account_does_not_change_ready_to_assign(self):
        # Archiver un compte ne fait que le masquer des listes de saisie -
        # l'argent qu'il contient reste reel et doit continuer a compter.
        account_id = self.db.add_account("Compte", starting_balance=1000.0)
        category_id = self.db.add_category("Epicerie")
        self.db.set_budget_entry(category_id, "2026-01", 300.0)
        before = bg.ready_to_assign(self.db, "2026-01")
        self.db.update_account(account_id, archived=1)
        after = bg.ready_to_assign(self.db, "2026-01")
        self.assertEqual(before, after)

    def test_archiving_a_nonempty_category_does_not_change_ready_to_assign(self):
        self.db.add_account("Compte", starting_balance=1000.0)
        category_id = self.db.add_category("Epicerie")
        self.db.set_budget_entry(category_id, "2026-01", 300.0)
        before = bg.ready_to_assign(self.db, "2026-01")
        self.db.update_category(category_id, archived=1)
        after = bg.ready_to_assign(self.db, "2026-01")
        self.assertEqual(before, after)

    def test_spending_from_an_envelope_does_not_change_ready_to_assign(self):
        # Ready to Assign ne bouge que par (assignation / argent total) - pas
        # par une depense, qui deplace juste de l'argent deja assigne.
        account_id = self.db.add_account("Compte", starting_balance=1000.0)
        category_id = self.db.add_category("Epicerie")
        self.db.set_budget_entry(category_id, "2026-01", 300.0)
        before = bg.ready_to_assign(self.db)
        self.db.add_transaction(account_id, "2026-01-05", -50.0, category_id=category_id)
        after = bg.ready_to_assign(self.db)
        self.assertEqual(before, after)


class FormatAmountTestCase(unittest.TestCase):
    def test_format_amount_uses_currency_code_and_space_separator(self):
        self.assertEqual(bg.format_amount(1234.5), "1 234.50 EUR")

    def test_format_amount_negative(self):
        self.assertEqual(bg.format_amount(-42.0), "-42.00 EUR")


if __name__ == "__main__":
    unittest.main()
