"""Smoke test de bout en bout pilotant la VRAIE GUI Tkinter d'Enveloppe :
vraie fenetre Tk, vrai EnveloppeApp, vrais widgets (Treeview, Notebook,
dialogues Toplevel, Entry, Combobox, Button.invoke()). Seuls
tkinter.messagebox/filedialog/simpledialog sont mockes (ce sont les seuls
points qui ouvriraient une vraie boite de dialogue modale bloquante) -
tout le reste du parcours utilisateur est reellement execute."""

import csv
import sys
import tempfile
import time
import unittest
from pathlib import Path
from tkinter import Tk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gui


def _collect_widgets(widget, cls_name):
    """Widgets de classe Tk `cls_name` (ex: 'TEntry', 'TButton') sous
    `widget`, en profondeur, dans l'ordre de creation."""
    found = []
    for child in widget.winfo_children():
        if child.winfo_class() == cls_name:
            found.append(child)
        found.extend(_collect_widgets(child, cls_name))
    return found


def _set_entry(entry, text):
    entry.delete(0, "end")
    entry.insert(0, text)


def _click_button(dialog, label):
    for button in _collect_widgets(dialog, "TButton"):
        if button["text"] == label:
            button.invoke()
            return
    raise AssertionError(f"Bouton introuvable : {label!r}")


class GuiSmokeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        with patch.object(gui, "_data_dir", return_value=self.tmp):
            self.app = gui.EnveloppeApp(self.root)
        self.addCleanup(self.app.db.close)

        # Les trois seuls points mockes (voir docstring du module) : evite
        # qu'une vraie boite de dialogue modale bloque le test.
        self.warning_patcher = patch("tkinter.messagebox.showwarning")
        self.info_patcher = patch("tkinter.messagebox.showinfo")
        self.mock_warning = self.warning_patcher.start()
        self.mock_info = self.info_patcher.start()
        self.addCleanup(self.warning_patcher.stop)
        self.addCleanup(self.info_patcher.stop)

    def _new_dialog(self, action):
        before = set(self.root.winfo_children())
        action()
        after = set(self.root.winfo_children()) - before
        self.assertEqual(len(after), 1, "un seul nouveau dialogue attendu")
        return after.pop()

    # -- item 4 : onglet Parametres / sauvegarde -----------------------------

    def test_settings_tab_exists_with_backup_and_open_folder_buttons(self):
        labels = {b["text"] for b in _collect_widgets(self.app.settings_tab, "TButton")}
        self.assertIn("Sauvegarder les donnees...", labels)
        self.assertIn("Ouvrir le dossier de donnees", labels)

    def test_backup_button_writes_a_working_copy_of_the_active_database(self):
        self.app.db.add_account("Compte", starting_balance=250.0)
        dest = self.tmp / "copie.sqlite"
        with patch("tkinter.filedialog.asksaveasfilename", return_value=str(dest)):
            self.app._backup_database()
        self.assertTrue(dest.exists())
        self.mock_info.assert_called_once()

        from db import Database
        restored = Database(dest)
        try:
            names = [a["name"] for a in restored.list_accounts()]
            self.assertIn("Compte", names)
        finally:
            restored.close()

    def test_backup_button_shows_an_error_instead_of_crashing_when_the_destination_is_invalid(self):
        bogus = self.tmp / "dossier_inexistant" / "copie.sqlite"
        with patch("tkinter.filedialog.asksaveasfilename", return_value=str(bogus)), \
             patch("tkinter.messagebox.showerror") as mock_error:
            self.app._backup_database()
        mock_error.assert_called_once()

    # -- item 6 : banniere de depassement ------------------------------------

    def test_overspent_banner_is_empty_when_nothing_is_overspent(self):
        self.app.db.add_account("Compte", starting_balance=1000.0)
        cat = self.app.db.add_category("Epicerie")
        self.app.db.set_budget_entry(cat, self.app.current_month, 100.0)
        self.app._refresh_budget()
        self.assertEqual(self.app.overspent_summary_var.get(), "")

    def test_overspent_banner_reports_categories_over_budget_for_the_displayed_month(self):
        account_id = self.app.db.add_account("Compte", starting_balance=1000.0)
        cat = self.app.db.add_category("Epicerie")
        self.app.db.set_budget_entry(cat, self.app.current_month, 100.0)
        self.app.db.add_transaction(
            account_id, f"{self.app.current_month}-05", -150.0, category_id=cat, payee="Trop depense",
        )
        self.app._refresh_budget()
        self.assertIn("1 enveloppe", self.app.overspent_summary_var.get())
        self.assertIn("depassement", self.app.overspent_summary_var.get())

    def test_overspent_banner_is_visible_outside_the_notebook_from_startup(self):
        # La banniere doit exister comme widget racine independant du
        # notebook, pas a l'interieur de l'onglet Budget - sinon elle ne
        # serait visible qu'en consultant cet onglet (le defaut audite).
        self.assertNotIn(self.app.budget_tab, self.root.winfo_children())
        root_labels_vars = {
            str(w.cget("textvariable")) for w in self.root.winfo_children() if w.winfo_class() == "TLabel"
        }
        self.assertIn(str(self.app.overspent_summary_var), root_labels_vars)

    # -- item 3 : virgule decimale francaise dans les dialogues --------------

    def test_split_dialog_accepts_french_decimal_comma_amounts(self):
        account_id = self.app.db.add_account("Compte", starting_balance=1000.0)
        groceries = self.app.db.add_category("Epicerie")
        household = self.app.db.add_category("Maison")
        tx_id = self.app.db.add_transaction(account_id, "2026-01-05", -100.0, category_id=groceries)
        self.app._refresh_transactions()
        self.app.transactions_tree.selection_set(str(tx_id))

        dialog = self._new_dialog(self.app._open_split_dialog)
        try:
            combos = _collect_widgets(dialog, "TCombobox")
            entries = _collect_widgets(dialog, "TEntry")
            # 2 lignes vides pre-remplies : [combo0, combo1], [amount0, memo0, amount1, memo1]
            self.assertEqual(len(combos), 2)
            self.assertEqual(len(entries), 4)

            groceries_label = next(l for l in combos[0]["values"] if l.startswith(f"{groceries} - "))
            household_label = next(l for l in combos[1]["values"] if l.startswith(f"{household} - "))
            combos[0].set(groceries_label)
            combos[1].set(household_label)
            _set_entry(entries[0], "-60,00")  # virgule francaise : float() brut rejette ceci
            _set_entry(entries[2], "-40,00")

            _click_button(dialog, "Enregistrer le fractionnement")
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

        self.mock_warning.assert_not_called()
        splits = self.app.db.get_transaction_splits(tx_id)
        self.assertEqual(len(splits), 2)
        amounts = sorted(s["amount"] for s in splits)
        self.assertEqual(amounts, [-60.0, -40.0])

    def test_transfer_dialog_accepts_french_decimal_comma_amount(self):
        account_a = self.app.db.add_account("Courant", starting_balance=1000.0)
        account_b = self.app.db.add_account("Epargne", starting_balance=0.0)
        before_count = len(self.app.db.list_transactions())

        dialog = self._new_dialog(self.app._open_transfer_dialog)
        try:
            entries = _collect_widgets(dialog, "TEntry")
            # ordre de creation : date, montant, memo
            self.assertEqual(len(entries), 3)
            _set_entry(entries[1], "25,50")  # virgule francaise
            _click_button(dialog, "Effectuer le virement")
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

        self.mock_warning.assert_not_called()
        transactions = self.app.db.list_transactions()
        self.assertEqual(len(transactions), before_count + 2)
        amounts = sorted(abs(tx["amount"]) for tx in transactions)
        self.assertEqual(amounts[-1], 25.5)

    # -- correctif audit : montant infini/NaN saisi dans la GUI --------------

    def test_entering_an_infinite_amount_in_the_transaction_form_shows_a_clear_warning_instead_of_crashing(self):
        # Trouve a l'audit : float("inf") passait float() sans lever
        # d'exception (contrairement a un texte non numerique), et aurait
        # contamine irreversiblement account_balance/ready_to_assign. Ce test
        # pilote la vraie saisie GUI (vrai StringVar, vrai bouton) pour
        # verrouiller que la saisie est desormais rejetee avec un message
        # clair, sans planter le callback ni inserer de transaction.
        account_id = self.app.db.add_account("Compte", starting_balance=1000.0)
        self.app.tx_account_var.set(f"{account_id} - Compte")
        self.app.tx_date_var.set("2026-01-05")
        self.app.tx_amount_var.set("inf")
        before_count = len(self.app.db.list_transactions())

        self.app._add_transaction()

        self.mock_warning.assert_called_once()
        warning_message = self.mock_warning.call_args[0][1]
        self.assertIn("fini", warning_message)
        self.assertEqual(len(self.app.db.list_transactions()), before_count)
        self.assertEqual(self.app.db.account_balance(account_id), 1000.0)

    def test_entering_a_nan_amount_in_the_transaction_form_shows_a_clear_warning_instead_of_crashing(self):
        # Trouve a l'audit : float("nan") aurait viole la contrainte NOT NULL
        # de la colonne amount (sqlite3 le convertit en NULL au binding) et
        # leve une sqlite3.IntegrityError non geree dans le callback Tkinter.
        account_id = self.app.db.add_account("Compte", starting_balance=1000.0)
        self.app.tx_account_var.set(f"{account_id} - Compte")
        self.app.tx_date_var.set("2026-01-05")
        self.app.tx_amount_var.set("nan")
        before_count = len(self.app.db.list_transactions())

        self.app._add_transaction()  # ne doit pas lever d'exception

        self.mock_warning.assert_called_once()
        warning_message = self.mock_warning.call_args[0][1]
        self.assertIn("fini", warning_message)
        self.assertEqual(len(self.app.db.list_transactions()), before_count)
        self.assertEqual(self.app.db.account_balance(account_id), 1000.0)

    # -- optimisation audit Phase 3 : import CSV en arriere-plan ------------

    def _pump_until(self, condition, timeout=5.0):
        """Fait tourner la vraie boucle d'evenements Tk (root.update()) par
        petites tranches jusqu'a ce que `condition()` devienne vraie -
        equivalent d'une attente active du thread de fond, sans jamais
        appeler mainloop() (qui bloquerait le test)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if condition():
                return
            time.sleep(0.01)
        raise AssertionError("condition non atteinte avant le timeout")

    def _write_import_csv(self, rows):
        path = self.tmp / "import.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["ID", "Date", "Compte", "Categorie", "Beneficiaire", "Memo", "Montant", "Pointee"])
            writer.writerows(rows)
        return path

    def test_import_csv_button_returns_immediately_and_disables_itself_while_a_slow_import_runs(self):
        # Trouve a l'audit : _import_transactions_csv appelait
        # import_transactions_csv directement sur le thread Tk principal -
        # un gros CSV (plusieurs milliers de lignes, cas d'usage cite par le
        # README) gelait donc totalement l'interface pendant toute la duree
        # de l'import (mesure a l'audit : 9.86s pour 2000 lignes, sans le
        # moindre retour visuel). Ce test simule un import lent (patch de
        # gui.import_transactions_csv) et verrouille que l'appel du bouton
        # revient immediatement (le vrai travail part sur un thread separe)
        # et que le bouton se desactive avec un message "Import en cours..."
        # le temps que ca tourne.
        self.app.db.add_account("Compte", starting_balance=0.0)
        csv_path = self._write_import_csv([["", "2026-01-05", "Compte", "", "Test", "", "-10.00", "Non"]])

        def slow_import(db, input_path, default_account_id=None, skip_duplicates=True):
            time.sleep(0.3)
            return {"imported": 1, "skipped": [], "duplicates": []}

        with patch("tkinter.filedialog.askopenfilename", return_value=str(csv_path)), \
             patch.object(gui, "import_transactions_csv", side_effect=slow_import):
            started = time.monotonic()
            self.app._import_transactions_csv()
            elapsed = time.monotonic() - started

            self.assertLess(
                elapsed, 0.2, "_import_transactions_csv doit revenir immediatement, le travail part sur un thread",
            )
            self.assertEqual(str(self.app.import_csv_button["state"]), "disabled")
            self.assertEqual(self.app.import_status_var.get(), "Import en cours...")

            self._pump_until(lambda: self.mock_info.called)

        self.assertEqual(str(self.app.import_csv_button["state"]), "normal")
        self.assertEqual(self.app.import_status_var.get(), "")

    def test_ui_event_loop_keeps_responding_while_a_slow_import_runs_in_the_background(self):
        # Preuve empirique (meme methode que les autres audits) que la
        # boucle d'evenements Tk continue de "battre" pendant l'import :
        # programme des tics via root.after() pendant que le worker dort, et
        # verifie que plusieurs tics ont bien eu lieu avant la fin de
        # l'import - impossible si le thread Tk principal etait bloque dans
        # l'appel a import_transactions_csv comme avant l'optimisation.
        self.app.db.add_account("Compte", starting_balance=0.0)
        csv_path = self._write_import_csv([["", "2026-01-05", "Compte", "", "Test", "", "-10.00", "Non"]])

        def slow_import(db, input_path, default_account_id=None, skip_duplicates=True):
            time.sleep(0.4)
            return {"imported": 1, "skipped": [], "duplicates": []}

        tick_count = [0]

        def tick():
            tick_count[0] += 1
            self.root.after(20, tick)

        with patch("tkinter.filedialog.askopenfilename", return_value=str(csv_path)), \
             patch.object(gui, "import_transactions_csv", side_effect=slow_import):
            self.app._import_transactions_csv()
            self.root.after(20, tick)
            self._pump_until(lambda: self.mock_info.called)

        self.assertGreater(tick_count[0], 3, "la boucle d'evenements Tk semble bloquee pendant l'import")

    def test_import_csv_button_actually_imports_transactions_via_the_background_thread(self):
        # Meme parcours que l'ancien import synchrone, mais desormais via le
        # thread + queue.Queue + root.after(...) : verrouille que le
        # resultat final (transactions importees, rafraichissement de
        # l'IHM, message recapitulatif) reste identique une fois l'import
        # termine.
        self.app.db.add_account("Compte", starting_balance=100.0)
        csv_path = self._write_import_csv([
            ["", "2026-01-05", "Compte", "", "Test", "", "-10.00", "Non"],
            ["", "2026-01-06", "Compte", "", "Test 2", "", "-5.00", "Non"],
        ])

        with patch("tkinter.filedialog.askopenfilename", return_value=str(csv_path)):
            self.app._import_transactions_csv()
            self._pump_until(lambda: self.mock_info.called)

        self.mock_info.assert_called_once()
        message = self.mock_info.call_args[0][1]
        self.assertIn("2 transaction(s) importee(s)", message)
        self.assertEqual(len(self.app.db.list_transactions()), 2)


if __name__ == "__main__":
    unittest.main()
