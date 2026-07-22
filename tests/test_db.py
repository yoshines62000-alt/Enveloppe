"""Tests pour db.py : schema SQLite, CRUD comptes/categories/transactions,
calcul du solde de compte, assignations budgetaires."""

import math
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

    def test_account_cleared_balance_only_counts_cleared_transactions(self):
        account_id = self.db.add_account("Compte courant", starting_balance=1000.0)
        self.db.add_transaction(account_id, "2026-01-05", -50.0, payee="Epicerie", cleared=True)
        self.db.add_transaction(account_id, "2026-01-10", 200.0, payee="Salaire", cleared=False)
        self.assertEqual(self.db.account_cleared_balance(account_id), 950.0)
        self.assertEqual(self.db.account_balance(account_id), 1150.0)  # solde total, lui, inclut tout

    def test_account_cleared_balance_equals_starting_balance_when_nothing_is_cleared(self):
        account_id = self.db.add_account("Compte courant", starting_balance=500.0)
        self.db.add_transaction(account_id, "2026-01-05", -50.0, cleared=False)
        self.assertEqual(self.db.account_cleared_balance(account_id), 500.0)

    def test_toggling_cleared_via_update_transaction_preserves_existing_splits(self):
        account_id = self.db.add_account("Compte courant")
        groceries = self.db.add_category("Epicerie")
        household = self.db.add_category("Maison")
        tx_id = self.db.add_transaction(account_id, "2026-01-05", -100.0)
        self.db.set_transaction_splits(tx_id, [
            {"category_id": groceries, "amount": -60.0},
            {"category_id": household, "amount": -40.0},
        ])
        self.db.update_transaction(tx_id, cleared=1)
        self.assertEqual(len(self.db.get_transaction_splits(tx_id)), 2)
        self.assertTrue(self.db.get_transaction(tx_id)["cleared"])

    def test_each_leg_of_a_transfer_can_be_pointed_independently(self):
        account_a = self.db.add_account("A")
        account_b = self.db.add_account("B")
        out_id, in_id = self.db.add_transfer(account_a, account_b, "2026-01-05", 100.0)
        self.db.update_transaction(out_id, cleared=1)
        self.assertTrue(self.db.get_transaction(out_id)["cleared"])
        self.assertFalse(self.db.get_transaction(in_id)["cleared"])

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

    def test_add_category_with_savings_goal(self):
        category_id = self.db.add_category("Vacances", savings_goal=2000.0)
        category = self.db.get_category(category_id)
        self.assertEqual(category["savings_goal"], 2000.0)

    def test_add_category_without_savings_goal_defaults_to_none(self):
        category_id = self.db.add_category("Epicerie")
        category = self.db.get_category(category_id)
        self.assertIsNone(category["savings_goal"])

    def test_update_category_sets_and_clears_savings_goal(self):
        category_id = self.db.add_category("Vacances")
        self.db.update_category(category_id, savings_goal=1500.0)
        self.assertEqual(self.db.get_category(category_id)["savings_goal"], 1500.0)
        self.db.update_category(category_id, savings_goal=None)
        self.assertIsNone(self.db.get_category(category_id)["savings_goal"])

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

    def test_add_transaction_rejects_calendar_invalid_dates(self):
        # Trouve a l'audit : le regex de format seul acceptait "2026-02-30"
        # (jour 01-31, sans verifier la longueur reelle du mois) et l'aurait
        # insere silencieusement, sans aucune exception - notamment via
        # import_transactions_csv, qui ne pre-valide pas la date avant de
        # l'envoyer a add_transaction (contrairement a la GUI).
        account_id = self.db.add_account("A")
        for invalid_date in ("2026-02-30", "2026-04-31", "2025-02-29", "2026-06-31", "2026-11-31"):
            with self.subTest(date=invalid_date):
                with self.assertRaises(ValueError):
                    self.db.add_transaction(account_id, invalid_date, -10.0)
        self.assertEqual(self.db.list_transactions(), [])  # aucune insertion silencieuse

    def test_add_transaction_accepts_a_leap_day_on_a_leap_year(self):
        account_id = self.db.add_account("A")
        transaction_id = self.db.add_transaction(account_id, "2028-02-29", -10.0)
        self.assertIsNotNone(self.db.get_transaction(transaction_id))

    def test_update_transaction_rejects_calendar_invalid_date(self):
        account_id = self.db.add_account("A")
        transaction_id = self.db.add_transaction(account_id, "2026-01-05", -10.0)
        with self.assertRaises(ValueError):
            self.db.update_transaction(transaction_id, date="2026-02-30")
        # La date d'origine, valide, n'a pas ete alteree par la tentative.
        self.assertEqual(self.db.get_transaction(transaction_id)["date"], "2026-01-05")

    def test_add_recurring_transaction_rejects_calendar_invalid_first_due_date(self):
        account_id = self.db.add_account("A")
        with self.assertRaises(ValueError):
            self.db.add_recurring_transaction(account_id, "2026-02-30", -800.0, "monthly")

    def test_add_transaction_rejects_infinite_amount(self):
        # Trouve a l'audit : float("inf") passait tel quel (aucune
        # verification de finitude), et contaminait irreversiblement
        # account_balance/total_on_budget_balance/ready_to_assign (ils
        # deviennent inf pour toute la base, sans aucune exception).
        account_id = self.db.add_account("A")
        with self.assertRaises(ValueError):
            self.db.add_transaction(account_id, "2026-01-05", float("inf"))
        with self.assertRaises(ValueError):
            self.db.add_transaction(account_id, "2026-01-05", float("-inf"))
        self.assertEqual(self.db.list_transactions(), [])  # aucune insertion silencieuse

    def test_add_transaction_rejects_nan_amount(self):
        # Trouve a l'audit : float("nan") est converti en NULL par sqlite3
        # lors du binding, ce qui violait la contrainte NOT NULL de la
        # colonne amount et levait une sqlite3.IntegrityError non geree.
        account_id = self.db.add_account("A")
        with self.assertRaises(ValueError):
            self.db.add_transaction(account_id, "2026-01-05", float("nan"))
        self.assertEqual(self.db.list_transactions(), [])

    def test_update_transaction_rejects_non_finite_amount(self):
        account_id = self.db.add_account("A")
        transaction_id = self.db.add_transaction(account_id, "2026-01-05", -10.0)
        with self.assertRaises(ValueError):
            self.db.update_transaction(transaction_id, amount=float("inf"))
        with self.assertRaises(ValueError):
            self.db.update_transaction(transaction_id, amount=float("nan"))
        # Le montant d'origine, valide, n'a pas ete alteree par la tentative.
        self.assertEqual(self.db.get_transaction(transaction_id)["amount"], -10.0)

    def test_balances_stay_intact_after_rejected_non_finite_amounts(self):
        # Verrouille l'invariant reste-a-assigner (dont total_on_budget_balance
        # et account_balance sont des composantes) : une tentative de saisie
        # d'un montant infini/NaN, meme rejetee, ne doit laisser aucune trace
        # de corruption sur les soldes agreges.
        account_id = self.db.add_account("A", starting_balance=1000.0)
        category_id = self.db.add_category("Epicerie")
        self.db.set_budget_entry(category_id, "2026-01", 200.0)
        balance_before = self.db.account_balance(account_id)
        total_before = self.db.total_on_budget_balance()
        for bad_amount in (float("inf"), float("-inf"), float("nan")):
            with self.assertRaises(ValueError):
                self.db.add_transaction(account_id, "2026-01-05", bad_amount, category_id=category_id)
        self.assertEqual(self.db.account_balance(account_id), balance_before)
        self.assertEqual(self.db.total_on_budget_balance(), total_before)
        self.assertTrue(math.isfinite(self.db.account_balance(account_id)))
        self.assertTrue(math.isfinite(self.db.total_on_budget_balance()))

    # -- backup_to (item 4 : sauvegarde/restauration) ------------------------

    def test_backup_to_creates_a_readable_copy_with_the_same_data(self):
        account_id = self.db.add_account("Compte", starting_balance=500.0)
        self.db.add_transaction(account_id, "2026-01-05", -20.0, payee="Test")
        dest = self.tmp / "copie.sqlite"
        self.db.backup_to(dest)
        self.assertTrue(dest.exists())

        copy = Database(dest)
        try:
            self.assertEqual(len(copy.list_accounts()), 1)
            self.assertEqual(copy.account_balance(account_id), 480.0)
        finally:
            copy.close()

    def test_backup_to_does_not_lock_or_alter_the_source_database(self):
        account_id = self.db.add_account("Compte", starting_balance=100.0)
        dest = self.tmp / "copie.sqlite"
        self.db.backup_to(dest)
        # La connexion source doit rester pleinement utilisable apres coup.
        self.db.add_transaction(account_id, "2026-01-05", -5.0)
        self.assertEqual(self.db.account_balance(account_id), 95.0)

    def test_backup_to_rejects_writing_over_the_active_database_file_itself(self):
        with self.assertRaises(ValueError):
            self.db.backup_to(self.db.path)

    def test_backup_to_rejects_a_resolved_alias_of_the_active_database_file(self):
        alias = self.db.path.parent / ".." / self.db.path.parent.name / self.db.path.name
        with self.assertRaises(ValueError):
            self.db.backup_to(alias)

    def test_backup_to_rejects_a_hard_link_to_the_active_database_file(self):
        # Un lien physique (hard link) vers le meme fichier n'est pas
        # detecte par la comparaison de chemins RESOLUS (resolve()) puisque
        # ce n'est pas un point de reparse a suivre - seul os.path.samefile
        # (identite de fichier) l'attrape. Sans cette deuxieme verification,
        # sqlite3 tenterait d'ouvrir une seconde connexion vers le fichier
        # deja ouvert par self.conn et resterait bloque indefiniment.
        import os
        hard_link = self.tmp / "lien.sqlite"
        try:
            os.link(self.db.path, hard_link)
        except OSError:
            self.skipTest("le systeme de fichiers ne supporte pas les liens physiques ici")
        with self.assertRaises(ValueError):
            self.db.backup_to(hard_link)

    def test_backup_to_a_brand_new_destination_path_succeeds(self):
        dest = self.tmp / "sous_dossier_absent" / "copie.sqlite"
        # Le dossier n'existe pas encore : sqlite3.connect() le cree-t-il ?
        # Non - mais le test verifie ici seulement le cas nominal (dossier
        # deja existant), le cas dossier absent est couvert cote GUI
        # (test_backup_button_shows_an_error_instead_of_crashing_...).
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.db.backup_to(dest)
        self.assertTrue(dest.exists())

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

    # -- filtres de date sargables (item 5 : substr() -> comparaison directe) --
    # sum_transactions_up_to/sum_transactions_for_month/list_transactions(up_to_month)
    # comparaient auparavant substr(date,1,7) a un mois ; ils comparent
    # maintenant `date` directement a des bornes de mois (voir
    # db._month_range_bounds), pour exploiter idx_transactions_category_date.
    # Ces tests verrouillent l'equivalence stricte de comportement, en
    # particulier aux bornes ou une regression de calcul de bornes se
    # verrait immediatement (changement d'annee, mois a 2 chiffres).

    def test_sum_transactions_up_to_excludes_the_first_day_of_the_following_month(self):
        account_id = self.db.add_account("A")
        cat = self.db.add_category("Epicerie")
        self.db.add_transaction(account_id, "2026-01-31", -20.0, category_id=cat)
        self.db.add_transaction(account_id, "2026-02-01", -30.0, category_id=cat)
        self.assertEqual(self.db.sum_transactions_up_to(cat, "2026-01"), -20.0)
        self.assertEqual(self.db.sum_transactions_up_to(cat, "2026-02"), -50.0)

    def test_sum_transactions_up_to_handles_the_december_to_january_year_rollover(self):
        account_id = self.db.add_account("A")
        cat = self.db.add_category("Epicerie")
        self.db.add_transaction(account_id, "2025-12-31", -20.0, category_id=cat)
        self.db.add_transaction(account_id, "2026-01-01", -30.0, category_id=cat)
        self.assertEqual(self.db.sum_transactions_up_to(cat, "2025-12"), -20.0)
        self.assertEqual(self.db.sum_transactions_up_to(cat, "2026-01"), -50.0)

    def test_sum_transactions_for_month_excludes_neighboring_months_at_the_year_rollover(self):
        account_id = self.db.add_account("A")
        cat = self.db.add_category("Epicerie")
        self.db.add_transaction(account_id, "2025-12-31", -20.0, category_id=cat)
        self.db.add_transaction(account_id, "2026-01-01", -30.0, category_id=cat)
        self.db.add_transaction(account_id, "2026-01-31", -5.0, category_id=cat)
        self.assertEqual(self.db.sum_transactions_for_month(cat, "2025-12"), -20.0)
        self.assertEqual(self.db.sum_transactions_for_month(cat, "2026-01"), -35.0)

    def test_list_transactions_up_to_month_excludes_the_following_month(self):
        account_id = self.db.add_account("A")
        self.db.add_transaction(account_id, "2026-01-31", -20.0)
        self.db.add_transaction(account_id, "2026-02-01", -30.0)
        self.assertEqual(len(self.db.list_transactions(up_to_month="2026-01")), 1)
        self.assertEqual(len(self.db.list_transactions(up_to_month="2026-02")), 2)

    def test_sum_transactions_up_to_still_sums_split_amounts_at_month_boundaries(self):
        account_id = self.db.add_account("A")
        cat = self.db.add_category("Epicerie")
        other = self.db.add_category("Maison")
        tx_id = self.db.add_transaction(account_id, "2026-01-31", -100.0, category_id=cat)
        self.db.set_transaction_splits(tx_id, [
            {"category_id": cat, "amount": -60.0},
            {"category_id": other, "amount": -40.0},
        ])
        self.assertEqual(self.db.sum_transactions_up_to(cat, "2026-01"), -60.0)
        self.assertEqual(self.db.sum_transactions_up_to(cat, "2025-12"), 0.0)

    def test_idx_transactions_category_date_index_exists(self):
        indexes = {row["name"] for row in self.db.conn.execute("PRAGMA index_list(transactions)")}
        self.assertIn("idx_transactions_category_date", indexes)

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

    def test_changing_the_amount_via_update_transaction_clears_an_existing_split(self):
        # Trouve a l'audit : changer uniquement le montant total (sans
        # toucher a category_id) laissait les parts existantes inchangees,
        # desynchronisees de la somme reelle de la transaction - leur somme
        # ne correspondrait alors plus a l'invariant verifie par
        # set_transaction_splits.
        self.db.set_transaction_splits(self.transaction_id, [
            {"category_id": self.groceries_id, "amount": -60.0},
            {"category_id": self.household_id, "amount": -40.0},
        ])
        self.db.update_transaction(self.transaction_id, amount=-150.0)
        self.assertEqual(self.db.get_transaction_splits(self.transaction_id), [])
        self.assertEqual(self.db.get_transaction(self.transaction_id)["amount"], -150.0)

    def test_changing_only_the_payee_on_a_split_transaction_preserves_the_split(self):
        self.db.set_transaction_splits(self.transaction_id, [
            {"category_id": self.groceries_id, "amount": -60.0},
            {"category_id": self.household_id, "amount": -40.0},
        ])
        self.db.update_transaction(self.transaction_id, payee="Grand magasin (corrige)")
        splits = self.db.get_transaction_splits(self.transaction_id)
        self.assertEqual(len(splits), 2)
        self.assertEqual(self.db.get_transaction(self.transaction_id)["payee"], "Grand magasin (corrige)")

    def test_resubmitting_the_same_amount_on_a_split_transaction_preserves_the_split(self):
        # Un appelant (le dialogue d'edition standard) peut renvoyer "amount"
        # dans chaque sauvegarde meme si l'utilisateur n'a pas touche ce
        # champ - seul un changement REEL de valeur doit effacer le split.
        self.db.set_transaction_splits(self.transaction_id, [
            {"category_id": self.groceries_id, "amount": -60.0},
            {"category_id": self.household_id, "amount": -40.0},
        ])
        unchanged_amount = self.db.get_transaction(self.transaction_id)["amount"]
        self.db.update_transaction(self.transaction_id, amount=unchanged_amount, payee="Corrige")
        splits = self.db.get_transaction_splits(self.transaction_id)
        self.assertEqual(len(splits), 2)

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


class RecurringTransactionTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "test.sqlite")
        self.addCleanup(self.db.close)
        self.account_id = self.db.add_account("Compte courant", starting_balance=0.0)
        self.category_id = self.db.add_category("Loyer")

    def test_add_and_list_recurring_transaction(self):
        rec_id = self.db.add_recurring_transaction(
            self.account_id, "2026-01-01", -800.0, "monthly",
            category_id=self.category_id, payee="Proprietaire",
        )
        templates = self.db.list_recurring_transactions()
        self.assertEqual(len(templates), 1)
        self.assertEqual(templates[0]["id"], rec_id)
        self.assertEqual(templates[0]["next_date"], "2026-01-01")

    def test_add_recurring_transaction_rejects_invalid_frequency(self):
        with self.assertRaises(ValueError):
            self.db.add_recurring_transaction(self.account_id, "2026-01-01", -800.0, "daily")

    def test_generate_due_creates_a_real_transaction_and_advances_next_date(self):
        self.db.add_recurring_transaction(
            self.account_id, "2026-01-01", -800.0, "monthly",
            category_id=self.category_id, payee="Proprietaire",
        )
        created_ids = self.db.generate_due_recurring_transactions(as_of="2026-01-15")
        self.assertEqual(len(created_ids), 1)
        tx = self.db.get_transaction(created_ids[0])
        self.assertEqual(tx["date"], "2026-01-01")
        self.assertEqual(tx["amount"], -800.0)
        self.assertEqual(tx["payee"], "Proprietaire")
        template = self.db.list_recurring_transactions()[0]
        self.assertEqual(template["next_date"], "2026-02-01")

    def test_generate_due_catches_up_multiple_missed_occurrences(self):
        self.db.add_recurring_transaction(self.account_id, "2026-01-01", -800.0, "monthly")
        # L'app n'a pas ete ouverte depuis 3 mois : les 3 echeances manquees
        # doivent toutes etre generees, pas seulement la derniere.
        created_ids = self.db.generate_due_recurring_transactions(as_of="2026-03-15")
        self.assertEqual(len(created_ids), 3)
        dates = sorted(self.db.get_transaction(tid)["date"] for tid in created_ids)
        self.assertEqual(dates, ["2026-01-01", "2026-02-01", "2026-03-01"])

    def test_generate_due_does_nothing_when_next_date_is_in_the_future(self):
        self.db.add_recurring_transaction(self.account_id, "2026-06-01", -800.0, "monthly")
        created_ids = self.db.generate_due_recurring_transactions(as_of="2026-01-15")
        self.assertEqual(created_ids, [])

    def test_generate_due_ignores_inactive_templates(self):
        rec_id = self.db.add_recurring_transaction(self.account_id, "2026-01-01", -800.0, "monthly")
        self.db.update_recurring_transaction(rec_id, active=0)
        created_ids = self.db.generate_due_recurring_transactions(as_of="2026-01-15")
        self.assertEqual(created_ids, [])

    def test_advance_date_monthly_clamps_to_last_day_of_shorter_month(self):
        # 31 janvier + mensuel ne doit jamais lever d'exception ni sauter a
        # mars : le jour est ramene au dernier jour de fevrier.
        self.assertEqual(Database._advance_date("2026-01-31", "monthly"), "2026-02-28")

    def test_advance_date_yearly_handles_leap_day(self):
        self.assertEqual(Database._advance_date("2028-02-29", "yearly"), "2029-02-28")

    def test_advance_date_weekly_adds_seven_days(self):
        self.assertEqual(Database._advance_date("2026-01-01", "weekly"), "2026-01-08")

    def test_advance_date_with_anchor_day_recovers_after_a_short_month(self):
        # Regression trouvee a l'audit : sans anchor_day, une fois clampe
        # sur un mois court (28), le jour restait bloque a 28 indefiniment,
        # meme dans un mois qui compte 31 jours. Avec anchor_day=31, le
        # rendez-vous revient bien au 31 des que le mois le permet.
        after_february = Database._advance_date("2026-01-31", "monthly", anchor_day=31)
        self.assertEqual(after_february, "2026-02-28")
        after_march = Database._advance_date(after_february, "monthly", anchor_day=31)
        self.assertEqual(after_march, "2026-03-31")

    def test_advance_date_without_anchor_day_keeps_the_old_ratcheting_behavior(self):
        # Compatibilite : appeler _advance_date sans anchor_day (comme le
        # faisaient les tests/appels avant cette correction) reste inchange.
        after_february = Database._advance_date("2026-01-31", "monthly")
        after_march = Database._advance_date(after_february, "monthly")
        self.assertEqual(after_march, "2026-03-28")

    def test_generate_due_recurring_transactions_never_drifts_the_day_of_month(self):
        # Bout en bout : un loyer du 31 doit rester au 31 chaque mois qui le
        # permet, meme apres avoir traverse fevrier.
        self.db.add_recurring_transaction(self.account_id, "2026-01-31", -800.0, "monthly")
        self.db.generate_due_recurring_transactions(as_of="2026-04-01")
        template = self.db.list_recurring_transactions()[0]
        self.assertEqual(template["next_date"], "2026-04-30")  # avril n'a que 30 jours

    def test_generate_due_recurring_transactions_defaults_to_local_today(self):
        # Regression trouvee a l'audit : as_of par defaut utilisait la date
        # UTC, incoherente avec le reste de l'application (budget.current_
        # month(), dates de transaction) qui utilise toujours l'heure locale.
        import datetime as _datetime_module
        rec_id = self.db.add_recurring_transaction(self.account_id, "2000-01-01", -800.0, "monthly")
        created_ids = self.db.generate_due_recurring_transactions()  # as_of implicite
        self.assertGreater(len(created_ids), 0)
        template = self.db.list_recurring_transactions()[0]
        self.assertLessEqual(template["next_date"], (_datetime_module.date.today() + _datetime_module.timedelta(days=31)).isoformat())

    def test_generate_due_never_generates_into_an_archived_account(self):
        # Regression D2 de l'audit : reproduit le "loyer fantome" - un
        # compte archive continuait auparavant a recevoir de nouvelles
        # transactions recurrentes indefiniment, a chaque appel, sans
        # aucun garde-fou ni avertissement.
        rec_id = self.db.add_recurring_transaction(
            self.account_id, "2026-01-01", -50.0, "monthly", payee="Loyer (ancien logement)",
        )
        self.db.update_account(self.account_id, archived=1)
        created_ids = self.db.generate_due_recurring_transactions(as_of="2026-07-15")
        self.assertEqual(created_ids, [])
        self.assertEqual(self.db.list_transactions(), [])
        # next_date n'avance pas non plus : des que le compte est
        # desarchive, les echeances manquees doivent pouvoir se rattraper
        # normalement plutot que rester bloquees a la premiere echeance.
        template = self.db.list_recurring_transactions()[0]
        self.assertEqual(template["next_date"], "2026-01-01")
        self.assertEqual(rec_id, template["id"])

    def test_generate_due_never_generates_into_an_archived_category(self):
        rec_id = self.db.add_recurring_transaction(
            self.account_id, "2026-01-01", -50.0, "monthly", category_id=self.category_id,
        )
        self.db.update_category(self.category_id, archived=1)
        created_ids = self.db.generate_due_recurring_transactions(as_of="2026-07-15")
        self.assertEqual(created_ids, [])
        self.assertEqual(self.db.list_transactions(), [])
        self.assertEqual(rec_id, self.db.list_recurring_transactions()[0]["id"])

    def test_generate_due_resumes_normally_once_the_account_is_unarchived(self):
        self.db.add_recurring_transaction(self.account_id, "2026-01-01", -50.0, "monthly")
        self.db.update_account(self.account_id, archived=1)
        self.db.generate_due_recurring_transactions(as_of="2026-03-15")
        self.assertEqual(self.db.list_transactions(), [])

        self.db.update_account(self.account_id, archived=0)
        created_ids = self.db.generate_due_recurring_transactions(as_of="2026-03-15")
        # Rattrape les 3 echeances manquees d'un coup, exactement comme un
        # modele ordinaire qui n'aurait jamais ete bloque (voir
        # test_generate_due_catches_up_multiple_missed_occurrences).
        self.assertEqual(len(created_ids), 3)

    def test_generate_due_still_generates_for_unrelated_active_templates(self):
        # La garde ne doit affecter QUE les modeles cibles sur un compte/
        # categorie archive - un modele ordinaire, sur un autre compte,
        # continue de generer normalement.
        other_account_id = self.db.add_account("Livret A", starting_balance=0.0)
        self.db.add_recurring_transaction(self.account_id, "2026-01-01", -50.0, "monthly")
        self.db.add_recurring_transaction(other_account_id, "2026-01-01", 20.0, "monthly")
        self.db.update_account(self.account_id, archived=1)
        created_ids = self.db.generate_due_recurring_transactions(as_of="2026-01-15")
        self.assertEqual(len(created_ids), 1)
        tx = self.db.get_transaction(created_ids[0])
        self.assertEqual(tx["account_id"], other_account_id)

    def test_list_recurring_transactions_targeting_archived_reports_only_the_blocked_ones(self):
        other_account_id = self.db.add_account("Livret A", starting_balance=0.0)
        blocked_id = self.db.add_recurring_transaction(
            self.account_id, "2026-01-01", -50.0, "monthly", payee="Loyer (ancien logement)",
        )
        self.db.add_recurring_transaction(other_account_id, "2026-01-01", -10.0, "monthly", payee="Toujours actif")
        self.assertEqual(self.db.list_recurring_transactions_targeting_archived(), [])

        self.db.update_account(self.account_id, archived=1)
        blocked = self.db.list_recurring_transactions_targeting_archived()
        self.assertEqual([t["id"] for t in blocked], [blocked_id])

    def test_list_recurring_transactions_targeting_archived_ignores_inactive_templates(self):
        rec_id = self.db.add_recurring_transaction(self.account_id, "2026-01-01", -50.0, "monthly")
        self.db.update_recurring_transaction(rec_id, active=0)
        self.db.update_account(self.account_id, archived=1)
        # list_recurring_transactions_targeting_archived s'appuie sur
        # list_recurring_transactions() (actifs uniquement, par defaut) :
        # un modele deja desactive n'a pas besoin d'etre signale, il ne
        # generera de toute facon rien.
        self.assertEqual(self.db.list_recurring_transactions_targeting_archived(), [])

    def test_delete_recurring_transaction_removes_it(self):
        rec_id = self.db.add_recurring_transaction(self.account_id, "2026-01-01", -800.0, "monthly")
        self.db.delete_recurring_transaction(rec_id)
        self.assertEqual(self.db.list_recurring_transactions(), [])

    def test_update_recurring_transaction_rejects_invalid_frequency(self):
        rec_id = self.db.add_recurring_transaction(self.account_id, "2026-01-01", -800.0, "monthly")
        with self.assertRaises(ValueError):
            self.db.update_recurring_transaction(rec_id, frequency="daily")


if __name__ == "__main__":
    unittest.main()
