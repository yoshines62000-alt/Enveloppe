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


class MoveBetweenEnvelopesTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "test.sqlite")
        self.addCleanup(self.db.close)
        self.account_id = self.db.add_account("Compte", starting_balance=1000.0)
        self.groceries_id = self.db.add_category("Epicerie")
        self.fun_id = self.db.add_category("Loisirs")
        self.db.set_budget_entry(self.groceries_id, "2026-01", 300.0)
        self.db.set_budget_entry(self.fun_id, "2026-01", 100.0)

    def test_move_shifts_available_by_exactly_the_amount_on_each_side(self):
        bg.move_between_envelopes(self.db, self.groceries_id, self.fun_id, "2026-01", 50.0)
        self.assertEqual(bg.category_available(self.db, self.groceries_id, "2026-01"), 250.0)
        self.assertEqual(bg.category_available(self.db, self.fun_id, "2026-01"), 150.0)

    def test_move_adjusts_the_month_assignments_symmetrically(self):
        bg.move_between_envelopes(self.db, self.groceries_id, self.fun_id, "2026-01", 50.0)
        self.assertEqual(self.db.get_budget_entry(self.groceries_id, "2026-01"), 250.0)
        self.assertEqual(self.db.get_budget_entry(self.fun_id, "2026-01"), 150.0)

    def test_move_does_not_change_ready_to_assign(self):
        # Un deplacement ne cree ni ne detruit d'argent : la somme des
        # assignations du mois est inchangee par construction.
        before = bg.ready_to_assign(self.db, "2026-01")
        bg.move_between_envelopes(self.db, self.groceries_id, self.fun_id, "2026-01", 50.0)
        after = bg.ready_to_assign(self.db, "2026-01")
        self.assertEqual(before, after)

    def test_move_rejects_the_same_category_on_both_sides(self):
        with self.assertRaises(ValueError):
            bg.move_between_envelopes(self.db, self.groceries_id, self.groceries_id, "2026-01", 50.0)

    def test_move_rejects_a_zero_or_negative_amount(self):
        with self.assertRaises(ValueError):
            bg.move_between_envelopes(self.db, self.groceries_id, self.fun_id, "2026-01", 0.0)
        with self.assertRaises(ValueError):
            bg.move_between_envelopes(self.db, self.groceries_id, self.fun_id, "2026-01", -50.0)

    def test_moving_more_than_available_is_allowed_and_goes_negative(self):
        # Comportement YNAB assume : on peut vider une enveloppe au-dela de
        # son solde, elle passe en negatif et devra etre comblee - jamais
        # d'erreur bloquante ici.
        bg.move_between_envelopes(self.db, self.groceries_id, self.fun_id, "2026-01", 500.0)
        self.assertEqual(self.db.get_budget_entry(self.groceries_id, "2026-01"), -200.0)
        self.assertEqual(bg.category_available(self.db, self.groceries_id, "2026-01"), -200.0)
        self.assertEqual(bg.category_available(self.db, self.fun_id, "2026-01"), 600.0)

    def test_move_rejects_an_infinite_amount(self):
        # Regression trouvee a l'audit : float("inf") passe le garde-fou
        # "amount > 0" (inf > 0 est vrai) et corrompait ready_to_assign en
        # NaN, un etat non affichable et non recuperable sans edition
        # manuelle de la base.
        with self.assertRaises(ValueError):
            bg.move_between_envelopes(self.db, self.groceries_id, self.fun_id, "2026-01", float("inf"))
        with self.assertRaises(ValueError):
            bg.move_between_envelopes(self.db, self.groceries_id, self.fun_id, "2026-01", float("nan"))
        # Aucun etat corrompu apres le rejet.
        self.assertEqual(self.db.get_budget_entry(self.groceries_id, "2026-01"), 300.0)

    def test_move_commits_both_writes_exactly_once(self):
        # Regression trouvee a l'audit : deux appels separes a
        # set_budget_entry (chacun committant independamment via deux
        # transactions SQLite distinctes) laissaient un etat durablement
        # incoherent si le processus etait interrompu entre les deux -
        # reproduit et confirme par l'agent d'audit (un solde fantome
        # survivait a la fermeture/reouverture de la base). Verifie ici
        # directement le mecanisme du correctif : move_budget_entries ne
        # doit appeler conn.commit() qu'UNE seule fois pour les deux
        # ecritures, jamais deux. sqlite3.Connection est un type C immuable
        # (ni patch.object ni assignation d'attribut d'instance ne
        # fonctionnent dessus) : on remplace donc self.db.conn par un fin
        # wrapper qui delegue tout sauf commit(), qu'il compte.
        class _CommitCounter:
            def __init__(self, real_conn):
                self._real_conn = real_conn
                self.commit_calls = 0

            def commit(self):
                self.commit_calls += 1
                return self._real_conn.commit()

            def __getattr__(self, name):
                return getattr(self._real_conn, name)

        counter = _CommitCounter(self.db.conn)
        self.db.conn = counter
        self.db.move_budget_entries(self.groceries_id, self.fun_id, "2026-01", 50.0)
        self.assertEqual(counter.commit_calls, 1)
        self.assertEqual(self.db.get_budget_entry(self.groceries_id, "2026-01"), 250.0)
        self.assertEqual(self.db.get_budget_entry(self.fun_id, "2026-01"), 150.0)


class FormatAmountTestCase(unittest.TestCase):
    def test_format_amount_uses_currency_code_and_space_separator(self):
        self.assertEqual(bg.format_amount(1234.5), "1 234.50 EUR")

    def test_format_amount_negative(self):
        self.assertEqual(bg.format_amount(-42.0), "-42.00 EUR")


class SavingsGoalProgressTestCase(unittest.TestCase):
    def test_returns_none_when_no_goal_is_set(self):
        self.assertIsNone(bg.savings_goal_progress(500.0, None))

    def test_returns_none_when_goal_is_zero_or_negative(self):
        self.assertIsNone(bg.savings_goal_progress(500.0, 0.0))
        self.assertIsNone(bg.savings_goal_progress(500.0, -100.0))

    def test_computes_percent_progress_toward_goal(self):
        progress = bg.savings_goal_progress(600.0, 1000.0)
        self.assertEqual(progress["percent"], 60)
        self.assertFalse(progress["reached"])

    def test_negative_available_clamps_to_zero_percent_not_negative(self):
        progress = bg.savings_goal_progress(-200.0, 1000.0)
        self.assertEqual(progress["percent"], 0)
        self.assertFalse(progress["reached"])

    def test_reaching_the_goal_is_flagged_and_capped_at_100_percent(self):
        progress = bg.savings_goal_progress(1500.0, 1000.0)
        self.assertEqual(progress["percent"], 100)
        self.assertTrue(progress["reached"])

    def test_exactly_at_goal_is_reached(self):
        progress = bg.savings_goal_progress(1000.0, 1000.0)
        self.assertTrue(progress["reached"])


class SpendingReportTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "test.sqlite")
        self.addCleanup(self.db.close)
        self.account_id = self.db.add_account("Compte", starting_balance=1000.0)
        self.groceries_id = self.db.add_category("Epicerie")
        self.fun_id = self.db.add_category("Loisirs")

    def test_report_covers_the_requested_number_of_months_ending_at_end_month(self):
        report = bg.spending_report(self.db, end_month="2026-03", num_months=3)
        self.assertEqual(report["months"], ["2026-01", "2026-02", "2026-03"])

    def test_report_sums_spending_per_category_per_month(self):
        self.db.add_transaction(self.account_id, "2026-01-05", -30.0, category_id=self.groceries_id)
        self.db.add_transaction(self.account_id, "2026-01-20", -20.0, category_id=self.groceries_id)
        self.db.add_transaction(self.account_id, "2026-02-10", -15.0, category_id=self.groceries_id)
        report = bg.spending_report(self.db, end_month="2026-02", num_months=2)
        row = next(r for r in report["rows"] if r["category_id"] == self.groceries_id)
        self.assertEqual(row["amounts"], {"2026-01": 50.0, "2026-02": 15.0})
        self.assertEqual(row["total"], 65.0)

    def test_a_refund_does_not_turn_into_negative_spending(self):
        # Un remboursement (montant positif) range dans une categorie ne
        # doit jamais faire apparaitre une "depense negative" - au pire,
        # aucune depense ce mois-la.
        self.db.add_transaction(self.account_id, "2026-01-05", 40.0, category_id=self.groceries_id)
        report = bg.spending_report(self.db, end_month="2026-01", num_months=1)
        self.assertNotIn(self.groceries_id, [r["category_id"] for r in report["rows"]])

    def test_a_category_with_no_spending_on_the_period_is_omitted(self):
        report = bg.spending_report(self.db, end_month="2026-01", num_months=1)
        self.assertEqual(report["rows"], [])

    def test_rows_are_sorted_by_total_spending_descending(self):
        self.db.add_transaction(self.account_id, "2026-01-05", -10.0, category_id=self.groceries_id)
        self.db.add_transaction(self.account_id, "2026-01-06", -50.0, category_id=self.fun_id)
        report = bg.spending_report(self.db, end_month="2026-01", num_months=1)
        self.assertEqual([r["category_id"] for r in report["rows"]], [self.fun_id, self.groceries_id])

    def test_an_archived_category_with_past_spending_still_appears(self):
        self.db.add_transaction(self.account_id, "2026-01-05", -25.0, category_id=self.groceries_id)
        self.db.update_category(self.groceries_id, archived=True)
        report = bg.spending_report(self.db, end_month="2026-01", num_months=1)
        self.assertEqual([r["category_id"] for r in report["rows"]], [self.groceries_id])

    def test_defaults_to_the_current_month_when_end_month_is_omitted(self):
        report = bg.spending_report(self.db, num_months=1)
        self.assertEqual(report["months"], [bg.current_month()])


class AnnualBudgetOverviewTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "test.sqlite")
        self.addCleanup(self.db.close)
        self.groceries_id = self.db.add_category("Epicerie")
        self.fun_id = self.db.add_category("Loisirs")

    def test_overview_covers_all_12_months_of_the_given_year(self):
        self.db.set_budget_entry(self.groceries_id, "2026-01", 100.0)
        overview = bg.annual_budget_overview(self.db, 2026)
        self.assertEqual(overview["months"], [f"2026-{m:02d}" for m in range(1, 13)])

    def test_a_category_never_assigned_all_year_is_omitted(self):
        overview = bg.annual_budget_overview(self.db, 2026)
        self.assertEqual(overview["rows"], [])

    def test_total_sums_the_12_monthly_assignments(self):
        self.db.set_budget_entry(self.groceries_id, "2026-01", 100.0)
        self.db.set_budget_entry(self.groceries_id, "2026-06", 150.0)
        overview = bg.annual_budget_overview(self.db, 2026)
        row = next(r for r in overview["rows"] if r["category_id"] == self.groceries_id)
        self.assertEqual(row["amounts"]["2026-01"], 100.0)
        self.assertEqual(row["amounts"]["2026-06"], 150.0)
        self.assertEqual(row["amounts"]["2026-03"], 0.0)
        self.assertEqual(row["total"], 250.0)

    def test_assignments_from_a_different_year_do_not_leak_into_the_overview(self):
        self.db.set_budget_entry(self.groceries_id, "2025-06", 999.0)
        overview = bg.annual_budget_overview(self.db, 2026)
        self.assertEqual(overview["rows"], [])

    def test_rows_are_sorted_by_group_then_by_name(self):
        self.db.update_category(self.groceries_id, group_name="Vie quotidienne")
        self.db.update_category(self.fun_id, group_name="Extras")
        self.db.set_budget_entry(self.groceries_id, "2026-01", 100.0)
        self.db.set_budget_entry(self.fun_id, "2026-01", 50.0)
        overview = bg.annual_budget_overview(self.db, 2026)
        self.assertEqual([r["category_id"] for r in overview["rows"]], [self.fun_id, self.groceries_id])


if __name__ == "__main__":
    unittest.main()
