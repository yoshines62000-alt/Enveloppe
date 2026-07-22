"""Smoke test de bout en bout pilotant la VRAIE GUI Tkinter d'Enveloppe :
vraie fenetre Tk, vrai EnveloppeApp, vrais widgets (Treeview, Notebook,
dialogues Toplevel, Entry, Combobox, Button.invoke()). Seuls
tkinter.messagebox/filedialog/simpledialog sont mockes (ce sont les seuls
points qui ouvriraient une vraie boite de dialogue modale bloquante) -
tout le reste du parcours utilisateur est reellement execute."""

import csv
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from tkinter import Tk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import budget as bg
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


def _press(widget, sequence):
    """Declenche le(s) callback(s) enregistre(s) par `widget.bind(sequence,
    ...)` (ex: `widget.bind("<Return>", ...)`), comme le ferait un vrai
    appui clavier.

    N'utilise PAS `widget.event_generate(sequence)` : verifie empiriquement
    peu fiable dans cet environnement de test - `setUp` cache la fenetre
    racine (`root.withdraw()`, pour ne pas faire clignoter de fenetre a
    chaque test), et un `Toplevel`/widget dont la racine est cachee reste
    lui-meme a l'etat Tk `withdrawn` (meme apres `grab_set`/`focus_force`) ;
    `event_generate` ne leve alors aucune erreur mais l'evenement clavier
    synthetique est silencieusement perdu avant d'atteindre les bindings.
    Rendre la fenetre reellement visible pour fiabiliser l'evenement
    (`deiconify` + `wait_visibility`) ne fonctionne pas non plus ici : ce
    process n'a pas de vrai bureau interactif (`wait_visibility()` ne rend
    jamais la main, confirmant l'absence de gestionnaire de fenetres reel
    dans cet environnement).

    A la place : lit le script Tcl que Tk associe reellement a `sequence`
    sur `widget` (`widget.tk.call("bind", widget, sequence)` - la meme
    information que retournerait `widget.bind(sequence)`), en extrait le nom
    de commande Tcl genere par tkinter pour le callback Python enregistre,
    et l'appelle directement avec des valeurs de substitution %-code neutres
    (cf. `tkinter.Misc._substitute` dans la bibliotheque standard, qui
    documente l'ordre et le type attendu de chacun des 19 champs - aucun des
    callbacks de cette application ne lit le contenu de l'objet `Event`
    recu, seul le fait d'etre appele compte). Deterministe et rapide,
    verifie sur des dizaines d'executions repetees : contrairement a
    `event_generate`, ne depend d'aucun etat de fenetre/focus au niveau du
    systeme."""
    script = widget.tk.call("bind", widget, sequence)
    funcids = re.findall(r"\[(\S+) %#", script)
    assert funcids, f"aucun binding {sequence!r} enregistre sur {widget!r}"
    keysym = sequence.strip("<>")
    dummy_args = (
        "0", "0", "0", "0", "0", "0", "0", "0", "0", "0",
        "", "0", keysym, "0", str(widget), "2", "0", "0", "0",
    )
    for funcid in funcids:
        if widget.tk.call(funcid, *dummy_args) == "break":
            break


def _click_button(dialog, label):
    for button in _collect_widgets(dialog, "TButton"):
        if button["text"] == label:
            button.invoke()
            return
    raise AssertionError(f"Bouton introuvable : {label!r}")


def _find_button(widget, label):
    for button in _collect_widgets(widget, "TButton"):
        if button["text"] == label:
            return button
    return None


def _find_label_by_textvariable(widget, var):
    """Cherche recursivement un ttk.Label relie a `var` (StringVar) via son
    option `textvariable` - plus fiable qu'une recherche par texte affiche,
    qui change au fil du temps (montants, mois...)."""
    for child in widget.winfo_children():
        if child.winfo_class() == "TLabel" and "textvariable" in child.keys() \
                and str(child.cget("textvariable")) == str(var):
            return child
        found = _find_label_by_textvariable(child, var)
        if found is not None:
            return found
    return None


def _find_entry_by_textvariable(widget, var):
    """Meme principe que _find_label_by_textvariable, pour un ttk.Entry ou
    ttk.Combobox relie a `var` via `textvariable` - permet de retrouver un
    champ de formulaire precis sans dependre de l'ordre de creation des
    widgets (fragile des qu'un champ est ajoute/retire du formulaire)."""
    for child in widget.winfo_children():
        if child.winfo_class() in ("TEntry", "TCombobox") and "textvariable" in child.keys() \
                and str(child.cget("textvariable")) == str(var):
            return child
        found = _find_entry_by_textvariable(child, var)
        if found is not None:
            return found
    return None


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

    # -- audit D24/D25/D26 : minsize + elements critiques jamais tronques ----
    #
    # D24 : aucun root.minsize() n'existait, la fenetre pouvait etre reduite
    # a n'importe quelle taille. D25 : consequence directe, l'indicateur
    # "Reste a assigner" (onglet Budget) sortait entierement du cadre visible
    # a 620x420 - une taille pourtant raisonnable, alors que le README le
    # decrit comme "toujours visible". D26 : le bouton "Pointer / depointer"
    # (onglet Transactions) s'affichait tronque en "Poin" des la taille de
    # fenetre PAR DEFAUT (1000x680), avant tout redimensionnement. Les tests
    # ci-dessous verrouillent les trois a la fois a la taille par defaut ET a
    # root.minsize() (la plus petite taille que l'utilisateur peut encore
    # atteindre).

    def _select_tab(self, tab):
        notebook = next(w for w in self.root.winfo_children() if w.winfo_class() == "TNotebook")
        notebook.select(tab)
        self.root.update_idletasks()
        self.root.update()

    def _shrink_to_minsize(self):
        min_w, min_h = self.root.wm_minsize()
        self.root.geometry(f"{min_w}x{min_h}")
        self.root.update_idletasks()
        self.root.update()

    def test_root_window_has_a_minimum_size_that_keeps_critical_elements_readable(self):
        # Valeurs mesurees (winfo_reqwidth) pour contenir la ligne de
        # navigation du Budget et la barre d'action des Transactions sans
        # troncature - voir gui.py, EnveloppeApp.__init__.
        min_w, min_h = self.root.wm_minsize()
        self.assertGreaterEqual(min_w, 900)
        self.assertGreaterEqual(min_h, 600)

    def test_ready_to_assign_indicator_is_fully_onscreen_at_default_window_size(self):
        self._select_tab(self.app.budget_tab)
        label = _find_label_by_textvariable(self.app.budget_tab, self.app.ready_to_assign_var)
        self.assertIsNotNone(label, "le label 'Reste a assigner' est introuvable")
        self.assertGreater(label.winfo_width(), 0, "l'indicateur 'Reste a assigner' a une largeur nulle (invisible)")
        right_edge = (label.winfo_rootx() - self.root.winfo_rootx()) + label.winfo_width()
        self.assertLessEqual(right_edge, self.root.winfo_width(), "l'indicateur deborde hors de la fenetre")
        self.assertIn("Reste a assigner", self.app.ready_to_assign_var.get())

    def test_ready_to_assign_indicator_is_fully_onscreen_at_minimum_window_size(self):
        self._shrink_to_minsize()
        self._select_tab(self.app.budget_tab)
        label = _find_label_by_textvariable(self.app.budget_tab, self.app.ready_to_assign_var)
        self.assertIsNotNone(label, "le label 'Reste a assigner' est introuvable")
        self.assertGreater(label.winfo_width(), 0, "l'indicateur 'Reste a assigner' a une largeur nulle (invisible)")
        right_edge = (label.winfo_rootx() - self.root.winfo_rootx()) + label.winfo_width()
        self.assertLessEqual(right_edge, self.root.winfo_width(), "l'indicateur deborde hors de la fenetre")

    def test_pointer_depointer_button_shows_its_full_label_at_default_window_size(self):
        self._select_tab(self.app.transactions_tab)
        button = _find_button(self.app.transactions_tab, "Pointer / depointer")
        self.assertIsNotNone(button, "le bouton 'Pointer / depointer' est introuvable")
        self.assertGreaterEqual(
            button.winfo_width(), button.winfo_reqwidth(),
            "le bouton 'Pointer / depointer' recoit moins que sa largeur demandee : texte tronque",
        )

    def test_pointer_depointer_button_shows_its_full_label_at_minimum_window_size(self):
        self._shrink_to_minsize()
        self._select_tab(self.app.transactions_tab)
        button = _find_button(self.app.transactions_tab, "Pointer / depointer")
        self.assertIsNotNone(button, "le bouton 'Pointer / depointer' est introuvable")
        self.assertGreaterEqual(
            button.winfo_width(), button.winfo_reqwidth(),
            "le bouton 'Pointer / depointer' recoit moins que sa largeur demandee : texte tronque",
        )

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

    # -- audit D14 : avertissement avant d'editer une transaction pointee ---
    #
    # Trouve a l'audit : le dialogue d'edition standard s'ouvrait pour
    # n'importe quelle transaction, y compris une transaction deja pointee
    # (rapprochee avec le releve bancaire), sans aucun avertissement -
    # le "Solde pointe" affiche pouvait donc silencieusement cesser de
    # refleter la realite bancaire verifiee. Le correctif avertit (au lieu
    # de bloquer) via un messagebox.askyesno avant d'ouvrir le dialogue.

    def test_editing_a_cleared_transaction_asks_for_confirmation_first(self):
        account_id = self.app.db.add_account("Compte", starting_balance=1000.0)
        tx_id = self.app.db.add_transaction(account_id, "2026-01-05", -30.0, payee="Epicerie", cleared=True)
        self.app._refresh_transactions()
        self.app.transactions_tree.selection_set(str(tx_id))

        with patch("tkinter.messagebox.askyesno", return_value=True) as mock_yes:
            dialog = self._new_dialog(lambda: self.app._edit_transaction())
        try:
            mock_yes.assert_called_once()
            warning_text = mock_yes.call_args[0][1]
            self.assertIn("pointee", warning_text.lower())
            self.assertEqual(dialog.title(), "Modifier la transaction")
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

    def test_declining_the_cleared_transaction_warning_cancels_the_edit(self):
        account_id = self.app.db.add_account("Compte", starting_balance=1000.0)
        tx_id = self.app.db.add_transaction(account_id, "2026-01-05", -30.0, payee="Epicerie", cleared=True)
        self.app._refresh_transactions()
        self.app.transactions_tree.selection_set(str(tx_id))

        before = set(self.root.winfo_children())
        with patch("tkinter.messagebox.askyesno", return_value=False) as mock_yes:
            self.app._edit_transaction()
        after = set(self.root.winfo_children()) - before
        mock_yes.assert_called_once()
        self.assertEqual(len(after), 0, "aucun dialogue d'edition ne doit s'ouvrir si l'utilisateur refuse")

    def test_editing_an_uncleared_transaction_does_not_ask_for_confirmation(self):
        account_id = self.app.db.add_account("Compte", starting_balance=1000.0)
        tx_id = self.app.db.add_transaction(account_id, "2026-01-05", -30.0, payee="Epicerie", cleared=False)
        self.app._refresh_transactions()
        self.app.transactions_tree.selection_set(str(tx_id))

        with patch("tkinter.messagebox.askyesno") as mock_yes:
            dialog = self._new_dialog(lambda: self.app._edit_transaction())
        try:
            mock_yes.assert_not_called()
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

    # -- audit D2 : transactions recurrentes vs compte/categorie archive ----
    #
    # Trouve a l'audit : generate_due_recurring_transactions continuait a
    # generer des transactions dans un compte/categorie archive, a chaque
    # ouverture, indefiniment et sans avertissement ("loyer fantome"). Le
    # correctif (db.py) arrete la generation ; ces tests verrouillent que la
    # GUI avertit bien l'utilisateur au lieu de rester silencieuse.

    def test_generate_recurring_now_warns_when_a_template_targets_an_archived_account(self):
        account_id = self.app.db.add_account("Compte", starting_balance=0.0)
        self.app.db.add_recurring_transaction(
            account_id, "2026-01-01", -50.0, "monthly", payee="Loyer (ancien logement)",
        )
        self.app.db.update_account(account_id, archived=1)

        self.app._generate_recurring_now()

        self.mock_warning.assert_called_once()
        message = self.mock_warning.call_args[0][1]
        self.assertIn("archive", message.lower())
        self.assertIn("Loyer (ancien logement)", message)
        self.mock_info.assert_not_called()
        self.assertEqual(self.app.db.list_transactions(), [])

    def test_generate_recurring_now_shows_plain_info_when_nothing_is_archived(self):
        account_id = self.app.db.add_account("Compte", starting_balance=0.0)
        self.app.db.add_recurring_transaction(account_id, "2026-01-01", -50.0, "monthly", payee="Loyer")

        self.app._generate_recurring_now()

        self.mock_info.assert_called_once()
        self.mock_warning.assert_not_called()

    def test_auto_generate_recurring_warns_at_startup_about_archived_targets_even_with_nothing_due(self):
        account_id = self.app.db.add_account("Compte", starting_balance=0.0)
        # Echeance tres future : rien n'est du, mais le modele reste bloque
        # par l'archivage - l'utilisateur doit quand meme en etre informe,
        # pas seulement quand une generation reelle a lieu.
        self.app.db.add_recurring_transaction(
            account_id, "2099-01-01", -50.0, "monthly", payee="Loyer (ancien logement)",
        )
        self.app.db.update_account(account_id, archived=1)

        self.app._auto_generate_recurring()

        self.mock_warning.assert_called_once()
        self.assertIn("archive", self.mock_warning.call_args[0][1].lower())

    def test_auto_generate_recurring_stays_silent_when_nothing_is_due_and_nothing_is_archived(self):
        account_id = self.app.db.add_account("Compte", starting_balance=0.0)
        self.app.db.add_recurring_transaction(account_id, "2099-01-01", -50.0, "monthly")

        self.app._auto_generate_recurring()

        self.mock_info.assert_not_called()
        self.mock_warning.assert_not_called()

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

    # -- audit D1 : categorie archivee a solde non nul, deplacable sans
    # desarchiver au prealable ------------------------------------------

    def test_move_between_envelopes_dialog_lists_an_archived_category_with_a_balance(self):
        # Trouve a l'audit : une categorie archivee avec un solde restant
        # etait bien visible (grisee, suffixe "(archivee)") dans l'onglet
        # Budget, mais totalement absente du dialogue "Deplacer entre
        # enveloppes..." (_category_choices() ne renvoyait que les
        # categories actives) - impossible d'en extraire l'argent sans
        # d'abord la desarchiver dans l'onglet Categories.
        self.app.db.add_account("Compte", starting_balance=1000.0)
        self.app.db.add_category("Loisirs")
        old_project_id = self.app.db.add_category("Ancien projet")
        self.app.db.set_budget_entry(old_project_id, self.app.current_month, 260.0)
        self.app.db.update_category(old_project_id, archived=1)
        self.app._refresh_budget()

        dialog = self._new_dialog(self.app._open_move_between_envelopes_dialog)
        try:
            combos = _collect_widgets(dialog, "TCombobox")
            self.assertEqual(len(combos), 2, "source et destination attendues")
            expected_label = f"{old_project_id} - Ancien projet (archivee)"
            for combo in combos:
                self.assertIn(
                    expected_label, combo.cget("values"),
                    "la categorie archivee a solde non nul doit rester choisissable",
                )
        finally:
            dialog.destroy()

    def test_moving_money_out_of_an_archived_category_via_the_dialog_actually_works(self):
        self.app.db.add_account("Compte", starting_balance=1000.0)
        fun_id = self.app.db.add_category("Loisirs")
        old_project_id = self.app.db.add_category("Ancien projet")
        self.app.db.set_budget_entry(old_project_id, self.app.current_month, 260.0)
        self.app.db.update_category(old_project_id, archived=1)
        self.app._refresh_budget()

        dialog = self._new_dialog(self.app._open_move_between_envelopes_dialog)
        try:
            from_combo, to_combo = _collect_widgets(dialog, "TCombobox")
            from_combo.set(f"{old_project_id} - Ancien projet (archivee)")
            to_combo.set(f"{fun_id} - Loisirs")
            amount_entry = _collect_widgets(dialog, "TEntry")[0]
            _set_entry(amount_entry, "100")
            _click_button(dialog, "Deplacer")
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

        self.assertEqual(bg.category_available(self.app.db, old_project_id, self.app.current_month), 160.0)
        self.assertEqual(bg.category_available(self.app.db, fun_id, self.app.current_month), 100.0)

    def test_move_between_envelopes_dialog_does_not_list_an_archived_category_with_a_zero_balance(self):
        # Une categorie archivee sans aucun solde restant n'a rien a
        # deplacer - elle continue de ne pas apparaitre, meme comportement
        # que l'onglet Budget (archived_with_balance).
        self.app.db.add_account("Compte", starting_balance=1000.0)
        self.app.db.add_category("Loisirs")
        self.app.db.add_category("Epicerie")
        empty_id = self.app.db.add_category("Categorie vide")
        self.app.db.update_category(empty_id, archived=1)
        self.app._refresh_budget()

        dialog = self._new_dialog(self.app._open_move_between_envelopes_dialog)
        try:
            combos = _collect_widgets(dialog, "TCombobox")
            unexpected_label = f"{empty_id} - Categorie vide (archivee)"
            for combo in combos:
                self.assertNotIn(unexpected_label, combo.cget("values"))
        finally:
            dialog.destroy()

    # -- audit D27 : abreviations de mois sans collision (Vue annuelle) -----

    def test_annual_view_headers_for_june_and_july_are_distinct(self):
        # Trouve a l'audit : les en-tetes de colonnes mensuelles tronquaient
        # naivement le libelle complet a 3 caracteres, produisant "Jui" pour
        # Juin ET pour Juillet - les deux colonnes etaient indiscernables.
        self.app.annual_year = 2026
        self.app._refresh_annual()
        june_heading = self.app.annual_tree.heading("2026-06")["text"]
        july_heading = self.app.annual_tree.heading("2026-07")["text"]
        self.assertNotEqual(june_heading, july_heading)
        self.assertEqual(june_heading, "Juin")
        self.assertEqual(july_heading, "Juil")

    # -- audit D31/D32 : dialogues de saisie de montant, virgule francaise --
    #
    # simpledialog.askfloat s'appuyait en interne sur self.tk.getdouble(),
    # qui REJETTE la virgule comme separateur decimal francais ('12,50' ->
    # TclError), incoherent avec _parse_float() utilise partout ailleurs
    # dans l'application. Remplace par _open_amount_edit_dialog (Toplevel
    # maison + _parse_float), qui accepte aussi bien la virgule que le
    # point, et affiche ses erreurs en francais au lieu du message natif Tk
    # en anglais ("Not a floating-point value...").

    def test_edit_budget_entry_dialog_accepts_french_decimal_comma_amounts(self):
        self.app.db.add_account("Compte", starting_balance=1000.0)
        cat_id = self.app.db.add_category("Epicerie")
        self.app._refresh_budget()
        self.app.budget_tree.selection_set(str(cat_id))

        dialog = self._new_dialog(self.app._edit_budget_entry)
        try:
            entry = _collect_widgets(dialog, "TEntry")[0]
            _set_entry(entry, "150,25")
            _click_button(dialog, "Enregistrer")
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

        self.assertEqual(self.app.db.get_budget_entry(cat_id, self.app.current_month), 150.25)

    def test_edit_budget_entry_dialog_rejects_invalid_input_with_a_french_message(self):
        self.app.db.add_account("Compte", starting_balance=1000.0)
        cat_id = self.app.db.add_category("Epicerie")
        self.app._refresh_budget()
        self.app.budget_tree.selection_set(str(cat_id))

        dialog = self._new_dialog(self.app._edit_budget_entry)
        try:
            entry = _collect_widgets(dialog, "TEntry")[0]
            _set_entry(entry, "pas un nombre")
            _click_button(dialog, "Enregistrer")
            self.mock_warning.assert_called_once()
            message = self.mock_warning.call_args[0][1]
            self.assertIn("nombre", message)
            self.assertNotIn("Illegal value", message)
            self.assertTrue(dialog.winfo_exists(), "le dialogue doit rester ouvert apres une saisie invalide")
        finally:
            dialog.destroy()

    def test_edit_category_goal_dialog_accepts_french_decimal_comma_amounts(self):
        cat_id = self.app.db.add_category("Vacances")
        self.app._refresh_categories()
        self.app.categories_tree.selection_set(str(cat_id))

        dialog = self._new_dialog(self.app._edit_category_goal)
        try:
            entry = _collect_widgets(dialog, "TEntry")[0]
            _set_entry(entry, "500,50")
            _click_button(dialog, "Enregistrer")
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

        self.assertEqual(self.app.db.get_category(cat_id)["savings_goal"], 500.5)

    def test_no_toplevel_dialog_uses_simpledialog_askfloat_anymore(self):
        # Audit D32 : constat re-verifie en debut de ce round d'audit -
        # deja resolu par effet de bord du correctif D31 (remplacement des
        # deux appels a simpledialog.askfloat par _open_amount_edit_dialog,
        # un Toplevel maison base sur _parse_float). Ce test verrouille
        # l'absence de regression : aucune invocation de
        # simpledialog.askfloat( ne doit jamais reapparaitre dans gui.py -
        # elle rejette la virgule decimale francaise et affiche ses erreurs
        # de validation nativement en anglais.
        source = Path(gui.__file__).read_text(encoding="utf-8")
        self.assertNotIn("askfloat(", source)

    # -- audit D33 : raccourcis clavier Entree (valider) / Echap (annuler) --
    #
    # Trouve a l'audit : aucun bind sur <Return>/<Escape> nulle part dans
    # gui.py - chaque ajout de transaction et chaque validation de dialogue
    # necessitait un clic de souris, et rien ne permettait de fermer un
    # dialogue au clavier sans passer par "Annuler" a la souris. Echap ferme
    # desormais chaque dialogue Toplevel sans rien enregistrer ; Entree
    # declenche l'action principale depuis n'importe quel champ du
    # dialogue/formulaire (verifie empiriquement : un KeyPress non consomme
    # par le widget qui a le focus remonte jusqu'au binding du Toplevel qui
    # le contient - un bind sur une Frame intermediaire ne recevrait rien,
    # d'ou le bind direct sur chaque Entry/Combobox des formulaires de la
    # fenetre principale).

    def test_escape_closes_the_amount_edit_dialog_without_saving(self):
        self.app.db.add_account("Compte", starting_balance=1000.0)
        cat_id = self.app.db.add_category("Epicerie")
        self.app._refresh_budget()
        self.app.budget_tree.selection_set(str(cat_id))

        dialog = self._new_dialog(self.app._edit_budget_entry)
        entry = _collect_widgets(dialog, "TEntry")[0]
        _set_entry(entry, "999")
        _press(dialog, "<Escape>")
        self.assertFalse(dialog.winfo_exists(), "Echap doit fermer le dialogue")
        self.assertEqual(self.app.db.get_budget_entry(cat_id, self.app.current_month), 0.0)

    def test_escape_closes_the_move_between_envelopes_dialog_without_saving(self):
        self.app.db.add_account("Compte", starting_balance=1000.0)
        cat_a = self.app.db.add_category("Loisirs")
        cat_b = self.app.db.add_category("Epicerie")
        self.app.db.set_budget_entry(cat_a, self.app.current_month, 100.0)

        dialog = self._new_dialog(self.app._open_move_between_envelopes_dialog)
        _press(dialog, "<Escape>")
        self.assertFalse(dialog.winfo_exists(), "Echap doit fermer le dialogue")
        self.assertEqual(self.app.db.get_budget_entry(cat_a, self.app.current_month), 100.0)
        self.assertEqual(self.app.db.get_budget_entry(cat_b, self.app.current_month), 0.0)

    def test_return_confirms_the_move_between_envelopes_dialog(self):
        self.app.db.add_account("Compte", starting_balance=1000.0)
        cat_a = self.app.db.add_category("Loisirs")
        cat_b = self.app.db.add_category("Epicerie")
        self.app.db.set_budget_entry(cat_a, self.app.current_month, 100.0)

        dialog = self._new_dialog(self.app._open_move_between_envelopes_dialog)
        try:
            combos = _collect_widgets(dialog, "TCombobox")
            from_label = next(l for l in combos[0]["values"] if l.startswith(f"{cat_a} - "))
            to_label = next(l for l in combos[1]["values"] if l.startswith(f"{cat_b} - "))
            combos[0].set(from_label)
            combos[1].set(to_label)
            amount_entry = _collect_widgets(dialog, "TEntry")[0]
            _set_entry(amount_entry, "40")
            # Le bind <Return> de ce dialogue est pose sur le Toplevel
            # (gui.py, `_open_move_between_envelopes_dialog`), pas sur un
            # champ particulier - il s'applique donc quel que soit le champ
            # en cours de saisie au moment de l'appui (verifie manuellement
            # a l'ecran ; non reproduit ici via un evenement clavier reel
            # depuis le champ, cf. docstring de `_press`).
            _press(dialog, "<Return>")
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

        self.assertEqual(self.app.db.get_budget_entry(cat_a, self.app.current_month), 60.0)
        self.assertEqual(self.app.db.get_budget_entry(cat_b, self.app.current_month), 40.0)

    def test_escape_closes_the_split_dialog_without_saving(self):
        account_id = self.app.db.add_account("Compte", starting_balance=1000.0)
        groceries = self.app.db.add_category("Epicerie")
        self.app.db.add_category("Maison")
        tx_id = self.app.db.add_transaction(account_id, "2026-01-05", -100.0, category_id=groceries)
        self.app._refresh_transactions()
        self.app.transactions_tree.selection_set(str(tx_id))

        dialog = self._new_dialog(self.app._open_split_dialog)
        _press(dialog, "<Escape>")
        self.assertFalse(dialog.winfo_exists(), "Echap doit fermer le dialogue")
        self.assertEqual(self.app.db.get_transaction_splits(tx_id), [])

    def test_return_confirms_the_split_dialog(self):
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
            groceries_label = next(l for l in combos[0]["values"] if l.startswith(f"{groceries} - "))
            household_label = next(l for l in combos[1]["values"] if l.startswith(f"{household} - "))
            combos[0].set(groceries_label)
            combos[1].set(household_label)
            _set_entry(entries[0], "-60")
            _set_entry(entries[2], "-40")
            _press(dialog, "<Return>")
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

        self.mock_warning.assert_not_called()
        splits = self.app.db.get_transaction_splits(tx_id)
        self.assertEqual(len(splits), 2)

    def test_escape_closes_the_transfer_dialog_without_saving(self):
        self.app.db.add_account("Courant", starting_balance=1000.0)
        self.app.db.add_account("Epargne", starting_balance=0.0)
        before_count = len(self.app.db.list_transactions())

        dialog = self._new_dialog(self.app._open_transfer_dialog)
        _press(dialog, "<Escape>")
        self.assertFalse(dialog.winfo_exists(), "Echap doit fermer le dialogue")
        self.assertEqual(len(self.app.db.list_transactions()), before_count)

    def test_return_confirms_the_transfer_dialog(self):
        self.app.db.add_account("Courant", starting_balance=1000.0)
        self.app.db.add_account("Epargne", starting_balance=0.0)
        before_count = len(self.app.db.list_transactions())

        dialog = self._new_dialog(self.app._open_transfer_dialog)
        try:
            entries = _collect_widgets(dialog, "TEntry")
            _set_entry(entries[1], "25")  # ordre de creation : date, montant, memo
            _press(dialog, "<Return>")
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

        self.mock_warning.assert_not_called()
        self.assertEqual(len(self.app.db.list_transactions()), before_count + 2)

    def test_escape_closes_the_edit_transaction_dialog_without_saving(self):
        account_id = self.app.db.add_account("Compte", starting_balance=1000.0)
        tx_id = self.app.db.add_transaction(account_id, "2026-01-05", -30.0, payee="Epicerie")
        self.app._refresh_transactions()
        self.app.transactions_tree.selection_set(str(tx_id))

        dialog = self._new_dialog(lambda: self.app._edit_transaction())
        _press(dialog, "<Escape>")
        self.assertFalse(dialog.winfo_exists(), "Echap doit fermer le dialogue")
        self.assertEqual(self.app.db.get_transaction(tx_id)["payee"], "Epicerie")

    def test_return_confirms_the_edit_transaction_dialog(self):
        account_id = self.app.db.add_account("Compte", starting_balance=1000.0)
        tx_id = self.app.db.add_transaction(account_id, "2026-01-05", -30.0, payee="Epicerie")
        self.app._refresh_transactions()
        self.app.transactions_tree.selection_set(str(tx_id))

        dialog = self._new_dialog(lambda: self.app._edit_transaction())
        try:
            # Ordre de creation des TEntry (pas de fractionnement ici, donc
            # la categorie est une TCombobox, pas un Label) : date,
            # beneficiaire, montant.
            entries = _collect_widgets(dialog, "TEntry")
            self.assertEqual(len(entries), 3)
            payee_entry = entries[1]
            _set_entry(payee_entry, "Supermarche")
            _press(dialog, "<Return>")
        finally:
            if dialog.winfo_exists():
                dialog.destroy()

        self.assertEqual(self.app.db.get_transaction(tx_id)["payee"], "Supermarche")

    def test_return_in_the_account_form_submits_it_from_the_balance_field(self):
        before_count = len(self.app.db.list_accounts())
        self.app.account_name_var.set("Nouveau compte")
        self.app.account_balance_var.set("100")
        entry = _find_entry_by_textvariable(self.app.accounts_tab, self.app.account_balance_var)
        self.assertIsNotNone(entry, "champ de solde de depart introuvable")
        _press(entry, "<Return>")
        self.assertEqual(len(self.app.db.list_accounts()), before_count + 1)

    def test_return_in_the_category_form_submits_it_from_the_name_field(self):
        before_count = len(self.app.db.list_categories())
        self.app.category_name_var.set("Nouvelle categorie")
        entry = _find_entry_by_textvariable(self.app.categories_tab, self.app.category_name_var)
        self.assertIsNotNone(entry, "champ de nom de categorie introuvable")
        _press(entry, "<Return>")
        self.assertEqual(len(self.app.db.list_categories()), before_count + 1)

    def test_return_in_the_transaction_form_submits_it_from_the_amount_field(self):
        account_id = self.app.db.add_account("Compte", starting_balance=1000.0)
        before_count = len(self.app.db.list_transactions())
        self.app.tx_account_var.set(f"{account_id} - Compte")
        self.app.tx_date_var.set("2026-01-05")
        self.app.tx_amount_var.set("-42")
        entry = _find_entry_by_textvariable(self.app.transactions_tab, self.app.tx_amount_var)
        self.assertIsNotNone(entry, "champ de montant introuvable")
        _press(entry, "<Return>")
        self.assertEqual(len(self.app.db.list_transactions()), before_count + 1)

    def test_return_in_the_recurring_form_submits_it_from_the_amount_field(self):
        account_id = self.app.db.add_account("Compte", starting_balance=1000.0)
        before_count = len(self.app.db.list_recurring_transactions())
        self.app.rec_account_var.set(f"{account_id} - Compte")
        self.app.rec_date_var.set("2026-01-05")
        self.app.rec_amount_var.set("-15")
        entry = _find_entry_by_textvariable(self.app.recurring_tab, self.app.rec_amount_var)
        self.assertIsNotNone(entry, "champ de montant introuvable")
        _press(entry, "<Return>")
        self.assertEqual(len(self.app.db.list_recurring_transactions()), before_count + 1)

    # -- audit D37 : solde de compte negatif mis en evidence visuellement ---
    #
    # Trouve a l'audit : contrairement au tableau Budget (categories en
    # depassement -> rouge, tag "overspent"), le tableau Comptes n'avait
    # aucun tag_configure - un compte a decouvert (solde negatif) s'affichait
    # exactement dans le meme style neutre qu'un solde positif.

    def test_account_with_a_negative_balance_gets_the_negative_tag(self):
        account_id = self.app.db.add_account("Compte", starting_balance=100.0)
        self.app.db.add_transaction(account_id, "2026-01-05", -350.0, payee="Loyer")
        self.app._refresh_accounts()
        self.assertLess(self.app.db.account_balance(account_id), 0)
        self.assertIn("negative", self.app.accounts_tree.item(str(account_id), "tags"))

    def test_account_with_a_positive_balance_does_not_get_the_negative_tag(self):
        account_id = self.app.db.add_account("Compte", starting_balance=100.0)
        self.app._refresh_accounts()
        self.assertGreater(self.app.db.account_balance(account_id), 0)
        self.assertNotIn("negative", self.app.accounts_tree.item(str(account_id), "tags"))

    def test_negative_tag_is_configured_with_the_same_red_used_for_overspent_categories(self):
        # Meme couleur que budget_tree.tag_configure("overspent", ...) -
        # convention de coloration coherente dans toute l'application pour
        # signaler "argent en probleme".
        overspent_color = self.app.budget_tree.tag_configure("overspent")["foreground"]
        negative_color = self.app.accounts_tree.tag_configure("negative")["foreground"]
        self.assertEqual(negative_color, overspent_color)

    def test_account_negative_tag_updates_when_the_balance_crosses_zero(self):
        account_id = self.app.db.add_account("Compte", starting_balance=100.0)
        tx_id = self.app.db.add_transaction(account_id, "2026-01-05", -350.0, payee="Loyer")
        self.app._refresh_accounts()
        self.assertIn("negative", self.app.accounts_tree.item(str(account_id), "tags"))

        # Le solde redevient positif (ex: correction de la transaction) : le
        # tag doit disparaitre au rafraichissement suivant plutot que de
        # rester colle indefiniment.
        self.app.db.update_transaction(tx_id, amount=50.0)
        self.app._refresh_accounts()
        self.assertNotIn("negative", self.app.accounts_tree.item(str(account_id), "tags"))


class DpiAwarenessTestCase(unittest.TestCase):
    """Audit D30 : le processus doit etre rendu explicitement Per-Monitor V2
    DPI Aware avant toute fenetre Tk, pour eviter un rendu flou sur les
    ecrans a mise a l'echelle superieure a 100% (125%/150%/200%, tres
    courant sur portables/ecrans modernes). Meme pattern deja applique et
    verifie sur le projet GuideExpress."""

    def test_configure_dpi_awareness_is_idempotent_and_does_not_raise(self):
        # Deja appele une fois a l'import de gui.py (niveau module) : un
        # second appel explicite ne doit rien refaire ni lever.
        gui._configure_dpi_awareness()
        gui._configure_dpi_awareness()
        self.assertTrue(gui._dpi_awareness_configured)

    @unittest.skipUnless(sys.platform == "win32", "verification specifique a l'API Win32")
    def test_process_is_actually_per_monitor_v2_dpi_aware_on_windows(self):
        import ctypes
        gui._configure_dpi_awareness()
        user32 = ctypes.windll.user32
        if not hasattr(user32, "GetThreadDpiAwarenessContext"):
            self.skipTest("GetThreadDpiAwarenessContext indisponible sur ce Windows (trop ancien)")
        current_context = user32.GetThreadDpiAwarenessContext()
        per_monitor_v2 = ctypes.c_void_p(-4)
        is_pm_v2 = bool(user32.AreDpiAwarenessContextsEqual(current_context, per_monitor_v2))
        self.assertTrue(is_pm_v2, "le processus devrait etre Per-Monitor V2 DPI Aware apres _configure_dpi_awareness()")


if __name__ == "__main__":
    unittest.main()
