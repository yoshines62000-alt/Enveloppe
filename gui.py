"""Interface Tkinter d'Enveloppe : comptes, categories, budget mensuel a
enveloppes (zero-based budgeting) et transactions, relies a la meme base
SQLite locale. Aucune connexion bancaire, aucun cloud - tout reste sur la
machine de l'utilisateur."""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, Y, StringVar, Tk, ttk, messagebox

import budget as bg
from csv_transactions import CsvImportError, export_transactions_csv, import_transactions_csv
from db import Database

APP_TITLE = "Enveloppe"
DONATE_URL = "https://ko-fi.com/yoshines62000"


def _resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def _data_dir() -> Path:
    return Path.home() / "AppData" / "Roaming" / "Enveloppe"


class EnveloppeApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1000x680")

        self.db = Database(_data_dir() / "enveloppe.sqlite")
        self.current_month = bg.current_month()

        icon_path = _resource_path("icon.ico")
        if icon_path.exists():
            try:
                self.root.iconbitmap(str(icon_path))
            except Exception:
                pass

        bottom_bar = ttk.Frame(self.root)
        bottom_bar.pack(fill=X, side="bottom")
        donate_label = ttk.Label(bottom_bar, text="☕ Soutenir le projet", foreground="#0645AD", cursor="hand2")
        donate_label.pack(side=RIGHT, padx=8, pady=4)
        donate_label.bind("<Button-1>", lambda event: webbrowser.open(DONATE_URL))

        # Banniere de depassement : placee HORS du notebook (au-dessus), pour
        # rester visible des l'ouverture de l'app quel que soit l'onglet
        # affiche - contrairement au coloriage en rouge des lignes de
        # l'onglet Budget (tag "overspent"), qui n'est visible que si
        # l'utilisateur consulte cet onglet precis. Meme pattern que
        # _refresh_overdue_summary de TempoFacture (calcul proactif au
        # demarrage), voir _refresh_overspent_summary.
        self.overspent_summary_var = StringVar(value="")
        overspent_label = ttk.Label(
            self.root, textvariable=self.overspent_summary_var,
            foreground="#B00020", font=("Segoe UI", 10, "bold"),
        )
        overspent_label.pack(fill=X, padx=10, pady=(6, 0))

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=BOTH, expand=True, padx=8, pady=8)

        self.accounts_tab = ttk.Frame(notebook)
        self.categories_tab = ttk.Frame(notebook)
        self.budget_tab = ttk.Frame(notebook)
        self.transactions_tab = ttk.Frame(notebook)
        self.reports_tab = ttk.Frame(notebook)
        self.annual_tab = ttk.Frame(notebook)
        self.recurring_tab = ttk.Frame(notebook)
        self.settings_tab = ttk.Frame(notebook)

        notebook.add(self.accounts_tab, text="Comptes")
        notebook.add(self.categories_tab, text="Categories")
        notebook.add(self.budget_tab, text="Budget")
        notebook.add(self.transactions_tab, text="Transactions")
        notebook.add(self.recurring_tab, text="Recurrentes")
        notebook.add(self.reports_tab, text="Rapports")
        notebook.add(self.annual_tab, text="Vue annuelle")
        notebook.add(self.settings_tab, text="Parametres")

        self._build_accounts_tab()
        self._build_categories_tab()
        self._build_budget_tab()
        self._build_transactions_tab()
        self._build_recurring_tab()
        self._build_reports_tab()
        self._build_annual_tab()
        self._build_settings_tab()

        self._refresh_accounts()
        self._refresh_categories()
        self._refresh_budget()
        self._refresh_transactions()
        self._refresh_recurring()
        self._refresh_reports()
        self._refresh_annual()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # Genere les transactions recurrentes dues avant que l'utilisateur ne
        # commence a consulter ses comptes, pour que les soldes affiches des
        # l'ouverture soient deja a jour. Differe via after() (pas
        # d'appel direct dans __init__) pour laisser la fenetre principale
        # s'afficher d'abord.
        self.root.after(200, self._auto_generate_recurring)

    # -- utilitaires communs --------------------------------------------------

    def _account_choices(self):
        accounts = self.db.list_accounts()
        return accounts, [f"{a['id']} - {a['name']}" for a in accounts]

    def _category_choices(self):
        categories = self.db.list_categories()
        return categories, [f"{c['id']} - {c['name']}" for c in categories]

    @staticmethod
    def _parse_id(combo_value: str):
        if not combo_value:
            return None
        return int(combo_value.split(" - ", 1)[0])

    @staticmethod
    def _parse_float(text: str, field_label: str) -> float:
        try:
            return float(text.strip().replace(",", ".") or 0)
        except ValueError:
            raise ValueError(f"{field_label} doit etre un nombre.")

    # -- onglet Comptes ---------------------------------------------------------

    def _build_accounts_tab(self):
        frame = self.accounts_tab
        form = ttk.Frame(frame)
        form.pack(fill=X, padx=10, pady=10)

        self.account_name_var = StringVar()
        self.account_type_var = StringVar()
        self.account_balance_var = StringVar(value="0")

        ttk.Label(form, text="Nom").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.account_name_var, width=25).grid(row=0, column=1, padx=5)
        ttk.Label(form, text="Type (courant, epargne...)").grid(row=0, column=2, sticky="w")
        ttk.Entry(form, textvariable=self.account_type_var, width=20).grid(row=0, column=3, padx=5)
        ttk.Label(form, text="Solde de depart").grid(row=0, column=4, sticky="w")
        ttk.Entry(form, textvariable=self.account_balance_var, width=12).grid(row=0, column=5, padx=5)
        ttk.Button(form, text="Ajouter le compte", command=self._add_account).grid(row=0, column=6, padx=5)

        columns = ("id", "name", "type", "balance", "cleared_balance", "archived")
        self.accounts_tree = ttk.Treeview(frame, columns=columns, show="headings", height=14)
        for col, label, width in [
            ("id", "ID", 40), ("name", "Nom", 200), ("type", "Type", 140),
            ("balance", "Solde actuel", 120), ("cleared_balance", "Solde pointe", 120),
            ("archived", "Archive", 70),
        ]:
            self.accounts_tree.heading(col, text=label)
            self.accounts_tree.column(col, width=width, anchor="w")
        self.accounts_tree.pack(fill=BOTH, expand=True, padx=10, pady=(0, 5))

        actions = ttk.Frame(frame)
        actions.pack(fill=X, padx=10, pady=(0, 10))
        ttk.Button(actions, text="Archiver / desarchiver", command=self._toggle_account_archived).pack(side=LEFT)

    def _add_account(self):
        name = self.account_name_var.get().strip()
        if not name:
            messagebox.showwarning(APP_TITLE, "Le nom du compte est obligatoire.")
            return
        try:
            balance = self._parse_float(self.account_balance_var.get(), "Le solde de depart")
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return
        self.db.add_account(name, self.account_type_var.get().strip(), balance)
        self.account_name_var.set("")
        self.account_type_var.set("")
        self.account_balance_var.set("0")
        self._refresh_accounts()
        self._refresh_transactions()
        self._refresh_budget()

    def _refresh_accounts(self):
        self.accounts_tree.delete(*self.accounts_tree.get_children())
        for account in self.db.list_accounts(include_archived=True):
            self.accounts_tree.insert("", END, iid=str(account["id"]), values=(
                account["id"], account["name"], account["type"],
                bg.format_amount(self.db.account_balance(account["id"])),
                bg.format_amount(self.db.account_cleared_balance(account["id"])),
                "Oui" if account["archived"] else "Non",
            ))
        self._refresh_transaction_account_choices()

    def _toggle_account_archived(self):
        selection = self.accounts_tree.selection()
        if not selection:
            messagebox.showinfo(APP_TITLE, "Selectionnez un compte d'abord.")
            return
        account_id = int(selection[0])
        account = self.db.get_account(account_id)
        self.db.update_account(account_id, archived=0 if account["archived"] else 1)
        self._refresh_accounts()
        self._refresh_budget()
        self._refresh_transactions()

    # -- onglet Categories --------------------------------------------------------

    def _build_categories_tab(self):
        frame = self.categories_tab
        form = ttk.Frame(frame)
        form.pack(fill=X, padx=10, pady=10)

        self.category_name_var = StringVar()
        self.category_group_var = StringVar()
        self.category_goal_var = StringVar()

        ttk.Label(form, text="Nom de la categorie").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.category_name_var, width=25).grid(row=0, column=1, padx=5)
        ttk.Label(form, text="Groupe (optionnel)").grid(row=0, column=2, sticky="w")
        ttk.Entry(form, textvariable=self.category_group_var, width=20).grid(row=0, column=3, padx=5)
        ttk.Label(form, text="Objectif d'epargne (optionnel)").grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Entry(form, textvariable=self.category_goal_var, width=12).grid(row=1, column=1, sticky="w", pady=(5, 0))
        ttk.Button(form, text="Ajouter la categorie", command=self._add_category).grid(row=1, column=4, pady=(5, 0))

        columns = ("id", "group", "name", "goal", "archived")
        self.categories_tree = ttk.Treeview(frame, columns=columns, show="headings", height=14)
        for col, label, width in [
            ("id", "ID", 40), ("group", "Groupe", 160), ("name", "Categorie", 200),
            ("goal", "Objectif d'epargne", 130), ("archived", "Archive", 70),
        ]:
            self.categories_tree.heading(col, text=label)
            self.categories_tree.column(col, width=width, anchor="w")
        self.categories_tree.pack(fill=BOTH, expand=True, padx=10, pady=(0, 5))
        self.categories_tree.bind("<Double-1>", self._edit_category_goal)

        actions = ttk.Frame(frame)
        actions.pack(fill=X, padx=10, pady=(0, 10))
        ttk.Button(actions, text="Archiver / desarchiver", command=self._toggle_category_archived).pack(side=LEFT)
        ttk.Label(
            actions, text="Double-cliquez sur une ligne pour modifier son objectif d'epargne.", foreground="#666",
        ).pack(side=LEFT, padx=10)

    def _add_category(self):
        name = self.category_name_var.get().strip()
        if not name:
            messagebox.showwarning(APP_TITLE, "Le nom de la categorie est obligatoire.")
            return
        goal_text = self.category_goal_var.get().strip()
        goal = None
        if goal_text:
            try:
                goal = self._parse_float(goal_text, "L'objectif d'epargne")
            except ValueError as exc:
                messagebox.showwarning(APP_TITLE, str(exc))
                return
            if goal <= 0:
                messagebox.showwarning(APP_TITLE, "L'objectif d'epargne doit etre superieur a zero.")
                return
        self.db.add_category(name, self.category_group_var.get().strip(), savings_goal=goal)
        self.category_name_var.set("")
        self.category_group_var.set("")
        self.category_goal_var.set("")
        self._refresh_categories()
        self._refresh_budget()
        self._refresh_transactions()

    def _refresh_categories(self):
        self.categories_tree.delete(*self.categories_tree.get_children())
        for category in self.db.list_categories(include_archived=True):
            goal = category["savings_goal"]
            self.categories_tree.insert("", END, iid=str(category["id"]), values=(
                category["id"], category["group_name"] or "-", category["name"],
                bg.format_amount(goal) if goal else "-",
                "Oui" if category["archived"] else "Non",
            ))
        self._refresh_transaction_category_choices()

    def _toggle_category_archived(self):
        selection = self.categories_tree.selection()
        if not selection:
            messagebox.showinfo(APP_TITLE, "Selectionnez une categorie d'abord.")
            return
        category_id = int(selection[0])
        category = self.db.get_category(category_id)
        self.db.update_category(category_id, archived=0 if category["archived"] else 1)
        self._refresh_categories()
        self._refresh_budget()
        self._refresh_transactions()

    def _edit_category_goal(self, event=None):
        selection = self.categories_tree.selection()
        if not selection:
            return
        category_id = int(selection[0])
        category = self.db.get_category(category_id)

        from tkinter import simpledialog
        new_value = simpledialog.askfloat(
            APP_TITLE, "Objectif d'epargne (laisser vide ou 0 pour aucun objectif) :",
            initialvalue=category["savings_goal"] or 0.0, parent=self.root,
        )
        if new_value is None:
            return
        self.db.update_category(category_id, savings_goal=new_value if new_value > 0 else None)
        self._refresh_categories()
        self._refresh_budget()

    # -- onglet Budget ------------------------------------------------------------

    def _build_budget_tab(self):
        frame = self.budget_tab
        top = ttk.Frame(frame)
        top.pack(fill=X, padx=10, pady=10)

        ttk.Button(top, text="< Mois precedent", command=lambda: self._change_month(-1)).pack(side=LEFT)
        self.budget_month_var = StringVar()
        ttk.Label(top, textvariable=self.budget_month_var, font=("Segoe UI", 12, "bold")).pack(side=LEFT, padx=15)
        ttk.Button(top, text="Mois suivant >", command=lambda: self._change_month(1)).pack(side=LEFT)
        ttk.Button(top, text="Copier le budget du mois precedent", command=self._copy_previous_month_budget).pack(side=LEFT, padx=15)
        ttk.Button(top, text="Deplacer entre enveloppes...", command=self._open_move_between_envelopes_dialog).pack(side=LEFT)

        self.ready_to_assign_var = StringVar()
        ready_label = ttk.Label(top, textvariable=self.ready_to_assign_var, font=("Segoe UI", 12, "bold"))
        ready_label.pack(side=RIGHT, padx=10)

        columns = ("group", "category", "budgeted", "activity", "available", "goal_progress")
        self.budget_tree = ttk.Treeview(frame, columns=columns, show="headings", height=18)
        for col, label, width in [
            ("group", "Groupe", 140), ("category", "Categorie", 180), ("budgeted", "Budgete", 110),
            ("activity", "Activite (ce mois)", 130), ("available", "Disponible", 110),
            ("goal_progress", "Objectif d'epargne", 150),
        ]:
            self.budget_tree.heading(col, text=label)
            self.budget_tree.column(col, width=width, anchor="w")
        self.budget_tree.tag_configure("overspent", foreground="#B00020")
        self.budget_tree.tag_configure("archived", foreground="#888888")
        self.budget_tree.pack(fill=BOTH, expand=True, padx=10, pady=(0, 5))
        self.budget_tree.bind("<Double-1>", self._edit_budget_entry)

        ttk.Label(
            frame, text="Double-cliquez sur une ligne pour modifier le montant budgete de ce mois.",
            foreground="#666",
        ).pack(anchor="w", padx=10, pady=(0, 10))

    def _change_month(self, delta: int):
        self.current_month = bg.shift_month(self.current_month, delta)
        self._refresh_budget()

    def _refresh_budget(self):
        self.budget_month_var.set(bg.month_label(self.current_month))
        self.budget_tree.delete(*self.budget_tree.get_children())
        # Les categories archivees ne sont pas proposees pour de nouvelles
        # saisies, mais si un solde y est encore "range" (report d'un mois
        # ou elle etait encore active), il doit rester visible ici - sinon
        # cet argent disparait de la vue tout en continuant a compter dans
        # le reste a assigner (budget.ready_to_assign inclut aussi les
        # categories archivees), ce qui le rendrait invisible et
        # irrecuperable depuis l'interface.
        active_categories = self.db.list_categories(include_archived=False)
        active_ids = {c["id"] for c in active_categories}
        archived_with_balance = [
            c for c in self.db.list_categories(include_archived=True)
            if c["id"] not in active_ids and bg.category_available(self.db, c["id"], self.current_month) != 0
        ]
        for category in active_categories + archived_with_balance:
            is_archived = category["id"] not in active_ids
            budgeted = self.db.get_budget_entry(category["id"], self.current_month)
            activity = bg.category_activity_for_month(self.db, category["id"], self.current_month)
            available = bg.category_available(self.db, category["id"], self.current_month)
            name = category["name"] + (" (archivee)" if is_archived else "")
            tags = []
            if available < 0:
                tags.append("overspent")
            if is_archived:
                tags.append("archived")
            progress = bg.savings_goal_progress(available, category["savings_goal"])
            if progress is None:
                progress_text = "-"
            elif progress["reached"]:
                progress_text = f"Atteint ({bg.format_amount(progress['goal'])})"
            else:
                progress_text = f"{progress['percent']:.0f}% de {bg.format_amount(progress['goal'])}"
            self.budget_tree.insert("", END, iid=str(category["id"]), values=(
                category["group_name"] or "-", name,
                bg.format_amount(budgeted), bg.format_amount(activity), bg.format_amount(available),
                progress_text,
            ), tags=tuple(tags))
        ready = bg.ready_to_assign(self.db, self.current_month)
        self.ready_to_assign_var.set(f"Reste a assigner : {bg.format_amount(ready)}")
        self._refresh_overspent_summary()

    def _refresh_overspent_summary(self):
        """Met a jour la banniere proactive de depassement (voir sa creation
        dans __init__). Compte toute categorie (active OU archivee : un
        depassement range dans une categorie archivee reste un depassement
        reel, meme motif que le coloriage "overspent" de l'onglet Budget, qui
        inclut deja archived_with_balance) dont le disponible du mois affiche
        est negatif."""
        count = sum(
            1 for category in self.db.list_categories(include_archived=True)
            if bg.category_available(self.db, category["id"], self.current_month) < 0
        )
        if not count:
            self.overspent_summary_var.set("")
            return
        plural = "s" if count > 1 else ""
        self.overspent_summary_var.set(f"{count} enveloppe{plural} en depassement ce mois-ci")

    def _copy_previous_month_budget(self):
        previous_month = bg.shift_month(self.current_month, -1)
        copied = 0
        for category in self.db.list_categories():
            already_set = self.db.get_budget_entry(category["id"], self.current_month)
            if already_set:
                continue  # ne jamais ecraser une saisie deja faite pour ce mois
            previous_amount = self.db.get_budget_entry(category["id"], previous_month)
            if previous_amount:
                self.db.set_budget_entry(category["id"], self.current_month, previous_amount)
                copied += 1
        self._refresh_budget()
        if copied:
            messagebox.showinfo(APP_TITLE, f"{copied} categorie(s) mise(s) a jour depuis {bg.month_label(previous_month)}.")
        else:
            messagebox.showinfo(APP_TITLE, "Rien a copier : aucune assignation trouvee le mois precedent, ou tout est deja assigne ce mois-ci.")

    def _open_move_between_envelopes_dialog(self):
        categories, category_labels = self._category_choices()
        if len(categories) < 2:
            messagebox.showwarning(APP_TITLE, "Il faut au moins deux categories pour deplacer de l'argent.")
            return

        # Pre-selectionne la categorie de la ligne choisie dans le tableau,
        # si elle est encore active : une ligne archivee peut apparaitre
        # dans le budget (solde restant) mais n'est plus proposee pour de
        # nouveaux mouvements, comme pour toute nouvelle saisie.
        selection = self.budget_tree.selection()
        default_from = category_labels[0]
        if selection:
            selected_label = next((l for l in category_labels if l.startswith(f"{selection[0]} - ")), None)
            if selected_label is not None:
                default_from = selected_label

        from tkinter import Toplevel

        dialog = Toplevel(self.root)
        dialog.title("Deplacer entre enveloppes")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        from_var = StringVar(value=default_from)
        to_var = StringVar()
        amount_var = StringVar()

        ttk.Label(
            dialog, text=f"Mois : {bg.month_label(self.current_month)}", font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 0))
        ttk.Label(dialog, text="Categorie source").grid(row=1, column=0, sticky="w", padx=10, pady=(5, 0))
        ttk.Combobox(dialog, textvariable=from_var, values=category_labels, width=25, state="readonly").grid(row=1, column=1, padx=10, pady=(5, 0))
        ttk.Label(dialog, text="Categorie destination").grid(row=2, column=0, sticky="w", padx=10, pady=(5, 0))
        ttk.Combobox(dialog, textvariable=to_var, values=category_labels, width=25, state="readonly").grid(row=2, column=1, padx=10, pady=(5, 0))
        ttk.Label(dialog, text="Montant").grid(row=3, column=0, sticky="w", padx=10, pady=(5, 0))
        ttk.Entry(dialog, textvariable=amount_var, width=15).grid(row=3, column=1, sticky="w", padx=10, pady=(5, 0))
        ttk.Label(
            dialog,
            text="Deplacer plus que le disponible est permis : le 'Budgete' de la source devient negatif, a combler plus tard.",
            foreground="#666", wraplength=320,
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=(5, 0))

        def on_save():
            from_id = self._parse_id(from_var.get())
            to_id = self._parse_id(to_var.get())
            if from_id is None or to_id is None:
                messagebox.showwarning(APP_TITLE, "Choisissez une categorie source et une destination.", parent=dialog)
                return
            try:
                amount = self._parse_float(amount_var.get(), "Le montant")
                bg.move_between_envelopes(self.db, from_id, to_id, self.current_month, amount)
            except ValueError as exc:
                messagebox.showwarning(APP_TITLE, str(exc), parent=dialog)
                return
            dialog.destroy()
            self._refresh_budget()

        buttons = ttk.Frame(dialog)
        buttons.grid(row=5, column=0, columnspan=2, pady=10)
        ttk.Button(buttons, text="Deplacer", command=on_save).pack(side=LEFT, padx=5)
        ttk.Button(buttons, text="Annuler", command=dialog.destroy).pack(side=LEFT, padx=5)

    def _edit_budget_entry(self, event=None):
        selection = self.budget_tree.selection()
        if not selection:
            return
        category_id = int(selection[0])
        category = self.db.get_category(category_id)
        current = self.db.get_budget_entry(category_id, self.current_month)

        from tkinter import simpledialog
        new_value = simpledialog.askfloat(
            APP_TITLE, f"Montant budgete pour '{category['name']}' en {bg.month_label(self.current_month)} :",
            initialvalue=current, parent=self.root,
        )
        if new_value is None:
            return
        self.db.set_budget_entry(category_id, self.current_month, round(new_value, 2))
        self._refresh_budget()

    # -- onglet Transactions --------------------------------------------------------

    def _build_transactions_tab(self):
        frame = self.transactions_tab
        form = ttk.Frame(frame)
        form.pack(fill=X, padx=10, pady=10)

        self.tx_account_var = StringVar()
        self.tx_category_var = StringVar()
        self.tx_date_var = StringVar(value=__import__("datetime").date.today().isoformat())
        self.tx_payee_var = StringVar()
        self.tx_amount_var = StringVar()

        ttk.Label(form, text="Compte").grid(row=0, column=0, sticky="w")
        self.tx_account_combo = ttk.Combobox(form, textvariable=self.tx_account_var, width=20, state="readonly")
        self.tx_account_combo.grid(row=0, column=1, padx=5)
        ttk.Label(form, text="Categorie").grid(row=0, column=2, sticky="w")
        self.tx_category_combo = ttk.Combobox(form, textvariable=self.tx_category_var, width=20, state="readonly")
        self.tx_category_combo.grid(row=0, column=3, padx=5)
        ttk.Label(form, text="Date (AAAA-MM-JJ)").grid(row=0, column=4, sticky="w")
        ttk.Entry(form, textvariable=self.tx_date_var, width=12).grid(row=0, column=5, padx=5)

        ttk.Label(form, text="Beneficiaire").grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Entry(form, textvariable=self.tx_payee_var, width=25).grid(row=1, column=1, columnspan=2, sticky="we", pady=(5, 0))
        ttk.Label(form, text="Montant (negatif = depense)").grid(row=1, column=3, sticky="w", pady=(5, 0))
        ttk.Entry(form, textvariable=self.tx_amount_var, width=12).grid(row=1, column=4, pady=(5, 0))
        ttk.Button(form, text="Ajouter", command=self._add_transaction).grid(row=1, column=5, pady=(5, 0))

        filter_frame = ttk.Frame(frame)
        filter_frame.pack(fill=X, padx=10)
        ttk.Label(filter_frame, text="Filtrer par compte :").pack(side=LEFT)
        self.tx_filter_account_var = StringVar()
        self.tx_filter_combo = ttk.Combobox(filter_frame, textvariable=self.tx_filter_account_var, width=20, state="readonly")
        self.tx_filter_combo.pack(side=LEFT, padx=5)
        self.tx_filter_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_transactions())
        ttk.Button(filter_frame, text="Tous les comptes", command=self._clear_transaction_filter).pack(side=LEFT, padx=5)

        columns = ("id", "date", "account", "payee", "category", "amount", "cleared")
        self.transactions_tree = ttk.Treeview(frame, columns=columns, show="headings", height=14)
        for col, label, width in [
            ("id", "ID", 40), ("date", "Date", 100), ("account", "Compte", 140),
            ("payee", "Beneficiaire", 180), ("category", "Categorie", 140), ("amount", "Montant", 100),
            ("cleared", "Pointee", 70),
        ]:
            self.transactions_tree.heading(col, text=label)
            self.transactions_tree.column(col, width=width, anchor="w")
        self.transactions_tree.pack(fill=BOTH, expand=True, padx=10, pady=(5, 5))
        self.transactions_tree.bind("<Double-1>", self._edit_transaction)

        actions = ttk.Frame(frame)
        actions.pack(fill=X, padx=10, pady=(0, 10))
        ttk.Label(actions, text="Double-cliquez sur une ligne pour la modifier.", foreground="#666").pack(side=LEFT)
        ttk.Button(actions, text="Supprimer la transaction selectionnee", command=self._delete_transaction).pack(side=RIGHT)
        ttk.Button(actions, text="Importer un CSV...", command=self._import_transactions_csv).pack(side=RIGHT, padx=(0, 5))
        ttk.Button(actions, text="Exporter en CSV...", command=self._export_transactions_csv).pack(side=RIGHT, padx=(0, 5))
        ttk.Button(actions, text="Virement entre comptes...", command=self._open_transfer_dialog).pack(side=RIGHT, padx=(0, 5))
        ttk.Button(actions, text="Fractionner...", command=self._open_split_dialog).pack(side=RIGHT, padx=(0, 5))
        ttk.Button(actions, text="Pointer / depointer", command=self._toggle_transaction_cleared).pack(side=RIGHT, padx=(0, 5))

    def _open_split_dialog(self):
        selection = self.transactions_tree.selection()
        if not selection:
            messagebox.showinfo(APP_TITLE, "Selectionnez une transaction d'abord.")
            return
        transaction_id = int(selection[0])
        tx = self.db.get_transaction(transaction_id)
        if tx is None:
            return
        if tx["transfer_id"] is not None:
            messagebox.showwarning(APP_TITLE, "Une jambe de virement ne peut pas etre fractionnee.")
            return

        category_rows, category_labels = self._category_choices()
        existing_splits = self.db.get_transaction_splits(transaction_id)

        # Une categorie archivee deja utilisee dans ce fractionnement doit
        # rester selectionnable ici (sinon sa ligne apparaitrait vide au
        # rechargement du dialogue, et Enregistrer la supprimerait
        # silencieusement du fractionnement - bug trouve a l'audit), meme si
        # elle n'apparait plus dans les listes de saisie pour une NOUVELLE
        # part (self._category_choices() ne renvoie que les actives).
        known_ids = {row["id"] for row in category_rows}
        for split in existing_splits:
            if split["category_id"] not in known_ids:
                archived_category = self.db.get_category(split["category_id"])
                if archived_category is not None:
                    category_rows = category_rows + [archived_category]
                    category_labels = category_labels + [f"{archived_category['id']} - {archived_category['name']} (archivee)"]
                    known_ids.add(archived_category["id"])

        from tkinter import Toplevel

        dialog = Toplevel(self.root)
        dialog.title("Fractionner la transaction")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(
            dialog, text=f"Montant total a repartir : {bg.format_amount(tx['amount'])}",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 5))

        rows_frame = ttk.Frame(dialog)
        rows_frame.grid(row=1, column=0, columnspan=3, padx=10)
        split_rows = []

        def add_row(category_value="", amount_value="", memo_value=""):
            row_index = len(split_rows)
            category_var = StringVar(value=category_value)
            amount_var = StringVar(value=amount_value)
            memo_var = StringVar(value=memo_value)
            combo = ttk.Combobox(rows_frame, textvariable=category_var, values=category_labels, width=22, state="readonly")
            combo.grid(row=row_index, column=0, padx=(0, 5), pady=2)
            amount_entry = ttk.Entry(rows_frame, textvariable=amount_var, width=10)
            amount_entry.grid(row=row_index, column=1, padx=5, pady=2)
            memo_entry = ttk.Entry(rows_frame, textvariable=memo_var, width=18)
            memo_entry.grid(row=row_index, column=2, padx=5, pady=2)
            split_rows.append({"category_var": category_var, "amount_var": amount_var, "memo_var": memo_var})

        if existing_splits:
            for split in existing_splits:
                label = next((l for l, r in zip(category_labels, category_rows) if r["id"] == split["category_id"]), "")
                add_row(label, f"{split['amount']:.2f}", split["memo"])
        else:
            add_row()
            add_row()

        ttk.Button(dialog, text="+ Ajouter une part", command=add_row).grid(row=2, column=0, sticky="w", padx=10, pady=(5, 0))

        def on_save():
            splits = []
            for row in split_rows:
                if not row["category_var"].get():
                    continue
                try:
                    amount = self._parse_float(row["amount_var"].get(), "Chaque part")
                except ValueError:
                    messagebox.showwarning(APP_TITLE, "Chaque part doit avoir un montant numerique.", parent=dialog)
                    return
                splits.append({
                    "category_id": self._parse_id(row["category_var"].get()),
                    "amount": amount, "memo": row["memo_var"].get().strip(),
                })
            try:
                self.db.set_transaction_splits(transaction_id, splits)
            except ValueError as exc:
                messagebox.showwarning(APP_TITLE, str(exc), parent=dialog)
                return
            dialog.destroy()
            self._refresh_transactions()
            self._refresh_budget()

        buttons = ttk.Frame(dialog)
        buttons.grid(row=3, column=0, columnspan=3, pady=10)
        ttk.Button(buttons, text="Enregistrer le fractionnement", command=on_save).pack(side=LEFT, padx=5)
        ttk.Button(buttons, text="Annuler", command=dialog.destroy).pack(side=LEFT, padx=5)

    def _export_transactions_csv(self):
        from tkinter import filedialog

        output = filedialog.asksaveasfilename(
            title="Exporter les transactions", initialfile="transactions.csv", defaultextension=".csv",
            filetypes=[("Fichier CSV", "*.csv")],
        )
        if not output:
            return
        account_id = self._parse_id(self.tx_filter_account_var.get()) if self.tx_filter_account_var.get() and self.tx_filter_account_var.get() != "Tous" else None
        export_transactions_csv(self.db.list_transactions(account_id=account_id), Path(output), db=self.db)
        messagebox.showinfo(APP_TITLE, f"Transactions exportees : {Path(output).name}")

    def _import_transactions_csv(self):
        from tkinter import filedialog

        input_path = filedialog.askopenfilename(title="Importer des transactions", filetypes=[("Fichier CSV", "*.csv")])
        if not input_path:
            return
        default_account_id = None
        accounts = self.db.list_accounts()
        if len(accounts) == 1:
            # Repli naturel quand un seul compte existe : un CSV exporte
            # depuis un autre outil ne connait pas forcement son nom exact.
            default_account_id = accounts[0]["id"]
        try:
            result = import_transactions_csv(self.db, Path(input_path), default_account_id=default_account_id)
        except CsvImportError as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return
        self._refresh_transactions()
        self._refresh_accounts()
        self._refresh_budget()
        message = f"{result['imported']} transaction(s) importee(s)."
        if result["duplicates"]:
            message += f"\n{len(result['duplicates'])} doublon(s) ignore(s) (deja present(s))."
        if result["skipped"]:
            message += f"\n{len(result['skipped'])} ligne(s) ignoree(s) :\n"
            message += "\n".join(f"  ligne {s['line']} : {s['reason']}" for s in result["skipped"][:10])
            if len(result["skipped"]) > 10:
                message += f"\n  ... et {len(result['skipped']) - 10} autre(s)."
        messagebox.showinfo(APP_TITLE, message)

    def _refresh_transaction_account_choices(self):
        accounts, labels = self._account_choices()
        self.tx_account_combo["values"] = labels
        self.tx_filter_combo["values"] = ["Tous"] + labels
        # Une combobox readonly ne valide pas d'elle-meme la variable qui lui
        # est liee : si le compte selectionne vient d'etre archive (donc
        # retire de `labels`), le texte reste affiche tel quel et une
        # nouvelle transaction pourrait encore lui etre rattachee.
        if self.tx_account_var.get() not in labels:
            self.tx_account_var.set("")
        if self.tx_filter_account_var.get() not in labels and self.tx_filter_account_var.get() != "Tous":
            self.tx_filter_account_var.set("")

    def _refresh_transaction_category_choices(self):
        categories, labels = self._category_choices()
        self.tx_category_combo["values"] = labels
        if self.tx_category_var.get() not in labels:
            self.tx_category_var.set("")

    def _clear_transaction_filter(self):
        self.tx_filter_account_var.set("")
        self._refresh_transactions()

    def _add_transaction(self):
        account_id = self._parse_id(self.tx_account_var.get())
        if account_id is None:
            messagebox.showwarning(APP_TITLE, "Choisissez un compte.")
            return
        category_id = self._parse_id(self.tx_category_var.get())
        date_text = self.tx_date_var.get().strip()
        try:
            from datetime import date as _date
            _date.fromisoformat(date_text)
        except ValueError:
            messagebox.showwarning(APP_TITLE, "La date doit etre au format AAAA-MM-JJ.")
            return
        try:
            amount = self._parse_float(self.tx_amount_var.get(), "Le montant")
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return
        if amount == 0:
            messagebox.showwarning(APP_TITLE, "Le montant ne peut pas etre nul.")
            return
        self.db.add_transaction(account_id, date_text, amount, category_id=category_id, payee=self.tx_payee_var.get())
        self.tx_payee_var.set("")
        self.tx_amount_var.set("")
        self._refresh_transactions()
        self._refresh_accounts()
        self._refresh_budget()

    def _refresh_transactions(self):
        self.transactions_tree.delete(*self.transactions_tree.get_children())
        filter_value = self.tx_filter_account_var.get()
        account_id = None
        if filter_value and filter_value != "Tous":
            account_id = self._parse_id(filter_value)
        for tx in self.db.list_transactions(account_id=account_id):
            if tx["transfer_id"] is not None:
                category_label = "Virement"
            elif tx["split_count"]:
                category_label = f"Fractionnee ({tx['split_count']})"
            else:
                category_label = tx["category_name"] or "-"
            self.transactions_tree.insert("", END, iid=str(tx["id"]), values=(
                tx["id"], tx["date"], tx["account_name"], tx["payee"],
                category_label, bg.format_amount(tx["amount"]),
                "Oui" if tx["cleared"] else "-",
            ))

    def _toggle_transaction_cleared(self):
        selection = self.transactions_tree.selection()
        if not selection:
            messagebox.showinfo(APP_TITLE, "Selectionnez une transaction d'abord.")
            return
        # Bascule CHAQUE ligne selectionnee individuellement (pas seulement
        # la premiere) : un utilisateur qui multi-selectionne plusieurs
        # transactions pour un rapprochement bancaire s'attend a toutes les
        # voir pointees d'un coup, pas seulement la premiere de la selection
        # (bug trouve a l'audit).
        for iid in selection:
            transaction_id = int(iid)
            tx = self.db.get_transaction(transaction_id)
            if tx is None:
                continue
            self.db.update_transaction(transaction_id, cleared=0 if tx["cleared"] else 1)
        self._refresh_transactions()
        self._refresh_accounts()

    def _delete_transaction(self):
        selection = self.transactions_tree.selection()
        if not selection:
            messagebox.showinfo(APP_TITLE, "Selectionnez une transaction d'abord.")
            return
        transaction_id = int(selection[0])
        tx = self.db.get_transaction(transaction_id)
        if tx is not None and tx["transfer_id"] is not None:
            if not messagebox.askyesno(
                APP_TITLE,
                "Cette transaction fait partie d'un virement entre deux comptes.\n"
                "Supprimer les deux jambes liees du virement ?",
            ):
                return
            self.db.delete_transfer_pair(transaction_id)
        else:
            if not messagebox.askyesno(APP_TITLE, "Supprimer cette transaction ?"):
                return
            self.db.delete_transaction(transaction_id)
        self._refresh_transactions()
        self._refresh_accounts()
        self._refresh_budget()

    def _open_transfer_dialog(self):
        accounts = self.db.list_accounts()
        if len(accounts) < 2:
            messagebox.showwarning(APP_TITLE, "Il faut au moins deux comptes pour effectuer un virement.")
            return
        account_labels = [f"{a['id']} - {a['name']}" for a in accounts]

        from tkinter import Toplevel

        dialog = Toplevel(self.root)
        dialog.title("Virement entre comptes")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        from_var = StringVar(value=account_labels[0])
        to_var = StringVar(value=account_labels[1])
        date_var = StringVar(value=__import__("datetime").date.today().isoformat())
        amount_var = StringVar()
        memo_var = StringVar()

        ttk.Label(dialog, text="Compte source").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 0))
        ttk.Combobox(dialog, textvariable=from_var, values=account_labels, width=25, state="readonly").grid(row=0, column=1, padx=10, pady=(10, 0))
        ttk.Label(dialog, text="Compte destination").grid(row=1, column=0, sticky="w", padx=10, pady=(5, 0))
        ttk.Combobox(dialog, textvariable=to_var, values=account_labels, width=25, state="readonly").grid(row=1, column=1, padx=10, pady=(5, 0))
        ttk.Label(dialog, text="Date (AAAA-MM-JJ)").grid(row=2, column=0, sticky="w", padx=10, pady=(5, 0))
        ttk.Entry(dialog, textvariable=date_var, width=15).grid(row=2, column=1, sticky="w", padx=10, pady=(5, 0))
        ttk.Label(dialog, text="Montant").grid(row=3, column=0, sticky="w", padx=10, pady=(5, 0))
        ttk.Entry(dialog, textvariable=amount_var, width=15).grid(row=3, column=1, sticky="w", padx=10, pady=(5, 0))
        ttk.Label(dialog, text="Memo (optionnel)").grid(row=4, column=0, sticky="w", padx=10, pady=(5, 0))
        ttk.Entry(dialog, textvariable=memo_var, width=25).grid(row=4, column=1, padx=10, pady=(5, 0))

        def on_save():
            from_id = self._parse_id(from_var.get())
            to_id = self._parse_id(to_var.get())
            try:
                amount = self._parse_float(amount_var.get(), "Le montant")
            except ValueError:
                messagebox.showwarning(APP_TITLE, "Le montant doit etre un nombre.", parent=dialog)
                return
            date_text = date_var.get().strip()
            try:
                self.db.add_transfer(from_id, to_id, date_text, amount, memo=memo_var.get().strip())
            except ValueError as exc:
                messagebox.showwarning(APP_TITLE, str(exc), parent=dialog)
                return
            dialog.destroy()
            self._refresh_transactions()
            self._refresh_accounts()
            self._refresh_budget()

        buttons = ttk.Frame(dialog)
        buttons.grid(row=5, column=0, columnspan=2, pady=10)
        ttk.Button(buttons, text="Effectuer le virement", command=on_save).pack(side=LEFT, padx=5)
        ttk.Button(buttons, text="Annuler", command=dialog.destroy).pack(side=LEFT, padx=5)

    def _edit_transaction(self, event=None):
        selection = self.transactions_tree.selection()
        if not selection:
            return
        transaction_id = int(selection[0])
        tx = self.db.get_transaction(transaction_id)
        if tx is None:
            return
        if tx["transfer_id"] is not None:
            # Ce dialogue generique ne sait pas repercuter un changement sur
            # l'autre jambe (compte/montant/date lies) - le modifier ici
            # desynchroniserait silencieusement le virement.
            messagebox.showwarning(
                APP_TITLE,
                "Cette transaction fait partie d'un virement entre comptes.\n"
                "Supprimez le virement (bouton 'Supprimer la transaction selectionnee') "
                "puis recreez-le pour le modifier.",
            )
            return
        existing_splits = self.db.get_transaction_splits(transaction_id)
        is_split = bool(existing_splits)

        from tkinter import Toplevel

        dialog = Toplevel(self.root)
        dialog.title("Modifier la transaction")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        _, account_labels = self._account_choices()
        _, category_labels = self._category_choices()
        account_label = next((l for l in account_labels if l.startswith(f"{tx['account_id']} - ")), "")
        category_label = ""
        if tx["category_id"] is not None:
            category_label = next((l for l in category_labels if l.startswith(f"{tx['category_id']} - ")), "")

        account_var = StringVar(value=account_label)
        category_var = StringVar(value=category_label)
        date_var = StringVar(value=tx["date"])
        payee_var = StringVar(value=tx["payee"])
        amount_var = StringVar(value=str(tx["amount"]))

        ttk.Label(dialog, text="Compte").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 0))
        account_combo = ttk.Combobox(dialog, textvariable=account_var, values=account_labels, width=25, state="readonly")
        account_combo.grid(row=0, column=1, padx=10, pady=(10, 0))
        ttk.Label(dialog, text="Categorie").grid(row=1, column=0, sticky="w", padx=10)
        if is_split:
            # La categorie est portee par les lignes de fractionnement, pas
            # par la transaction elle-meme : ne jamais envoyer category_id a
            # update_transaction ici (voir on_save), sous peine d'effacer
            # silencieusement le fractionnement existant (bug d'audit).
            ttk.Label(
                dialog, text=f"Fractionnee sur {len(existing_splits)} categories - "
                "utilisez 'Fractionner...' pour la modifier", foreground="#666", wraplength=220,
            ).grid(row=1, column=1, sticky="w", padx=10)
        else:
            category_combo = ttk.Combobox(dialog, textvariable=category_var, values=category_labels, width=25, state="readonly")
            category_combo.grid(row=1, column=1, padx=10)
        ttk.Label(dialog, text="Date (AAAA-MM-JJ)").grid(row=2, column=0, sticky="w", padx=10)
        ttk.Entry(dialog, textvariable=date_var, width=15).grid(row=2, column=1, sticky="w", padx=10)
        ttk.Label(dialog, text="Beneficiaire").grid(row=3, column=0, sticky="w", padx=10)
        ttk.Entry(dialog, textvariable=payee_var, width=25).grid(row=3, column=1, padx=10)
        ttk.Label(dialog, text="Montant").grid(row=4, column=0, sticky="w", padx=10)
        ttk.Entry(dialog, textvariable=amount_var, width=15).grid(row=4, column=1, sticky="w", padx=10, pady=(0, 10))

        def on_save():
            account_id = self._parse_id(account_var.get())
            if account_id is None:
                messagebox.showwarning(APP_TITLE, "Choisissez un compte.")
                return
            date_text = date_var.get().strip()
            try:
                from datetime import date as _date
                _date.fromisoformat(date_text)
            except ValueError:
                messagebox.showwarning(APP_TITLE, "La date doit etre au format AAAA-MM-JJ.")
                return
            try:
                amount = self._parse_float(amount_var.get(), "Le montant")
            except ValueError as exc:
                messagebox.showwarning(APP_TITLE, str(exc))
                return
            if amount == 0:
                messagebox.showwarning(APP_TITLE, "Le montant ne peut pas etre nul.")
                return
            update_kwargs = dict(
                account_id=account_id, date=date_text, payee=payee_var.get().strip(), amount=amount,
            )
            if not is_split:
                update_kwargs["category_id"] = self._parse_id(category_var.get())
            self.db.update_transaction(transaction_id, **update_kwargs)
            dialog.destroy()
            self._refresh_transactions()
            self._refresh_accounts()
            self._refresh_budget()

        buttons = ttk.Frame(dialog)
        buttons.grid(row=5, column=0, columnspan=2, pady=(0, 10))
        ttk.Button(buttons, text="Enregistrer", command=on_save).pack(side=LEFT, padx=5)
        ttk.Button(buttons, text="Annuler", command=dialog.destroy).pack(side=LEFT, padx=5)

    # -- onglet Recurrentes -----------------------------------------------------

    _RECURRING_FREQUENCY_LABELS = {"weekly": "Hebdomadaire", "monthly": "Mensuelle", "yearly": "Annuelle"}
    _RECURRING_FREQUENCY_VALUES = {v: k for k, v in _RECURRING_FREQUENCY_LABELS.items()}

    def _build_recurring_tab(self):
        frame = self.recurring_tab
        form = ttk.Frame(frame)
        form.pack(fill=X, padx=10, pady=10)

        self.rec_account_var = StringVar()
        self.rec_category_var = StringVar()
        self.rec_frequency_var = StringVar(value="Mensuelle")
        from datetime import date as _date
        self.rec_date_var = StringVar(value=_date.today().isoformat())
        self.rec_payee_var = StringVar()
        self.rec_amount_var = StringVar()

        ttk.Label(form, text="Compte").grid(row=0, column=0, sticky="w")
        self.rec_account_combo = ttk.Combobox(form, textvariable=self.rec_account_var, width=20, state="readonly")
        self.rec_account_combo.grid(row=0, column=1, padx=5)
        ttk.Label(form, text="Categorie").grid(row=0, column=2, sticky="w")
        self.rec_category_combo = ttk.Combobox(form, textvariable=self.rec_category_var, width=20, state="readonly")
        self.rec_category_combo.grid(row=0, column=3, padx=5)
        ttk.Label(form, text="Frequence").grid(row=0, column=4, sticky="w")
        ttk.Combobox(
            form, textvariable=self.rec_frequency_var, width=14, state="readonly",
            values=list(self._RECURRING_FREQUENCY_LABELS.values()),
        ).grid(row=0, column=5, padx=5)

        ttk.Label(form, text="Beneficiaire").grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Entry(form, textvariable=self.rec_payee_var, width=25).grid(row=1, column=1, columnspan=2, sticky="we", pady=(5, 0))
        ttk.Label(form, text="Montant (negatif = depense)").grid(row=1, column=3, sticky="w", pady=(5, 0))
        ttk.Entry(form, textvariable=self.rec_amount_var, width=12).grid(row=1, column=4, pady=(5, 0))
        ttk.Label(form, text="Premiere echeance (AAAA-MM-JJ)").grid(row=2, column=0, sticky="w", pady=(5, 0))
        ttk.Entry(form, textvariable=self.rec_date_var, width=12).grid(row=2, column=1, sticky="w", pady=(5, 0))
        ttk.Button(form, text="Ajouter", command=self._add_recurring).grid(row=2, column=5, pady=(5, 0))

        columns = ("id", "next_date", "frequency", "account", "payee", "category", "amount")
        self.recurring_tree = ttk.Treeview(frame, columns=columns, show="headings", height=12)
        for col, label, width in [
            ("id", "ID", 40), ("next_date", "Prochaine echeance", 130), ("frequency", "Frequence", 100),
            ("account", "Compte", 140), ("payee", "Beneficiaire", 160),
            ("category", "Categorie", 140), ("amount", "Montant", 100),
        ]:
            self.recurring_tree.heading(col, text=label)
            self.recurring_tree.column(col, width=width, anchor="w")
        self.recurring_tree.pack(fill=BOTH, expand=True, padx=10, pady=(5, 5))

        actions = ttk.Frame(frame)
        actions.pack(fill=X, padx=10, pady=(0, 10))
        ttk.Label(
            actions,
            text="Les echeances dues sont generees automatiquement a l'ouverture. Aucune suppression de compte/categorie n'est jamais automatique.",
            foreground="#666",
        ).pack(side=LEFT)
        ttk.Button(actions, text="Generer maintenant", command=self._generate_recurring_now).pack(side=RIGHT)
        ttk.Button(actions, text="Supprimer le modele", command=self._delete_recurring).pack(side=RIGHT, padx=(0, 5))

    def _refresh_recurring_choices(self):
        _, account_labels = self._account_choices()
        self.rec_account_combo["values"] = account_labels
        _, category_labels = self._category_choices()
        self.rec_category_combo["values"] = category_labels

    def _refresh_recurring(self):
        self._refresh_recurring_choices()
        self.recurring_tree.delete(*self.recurring_tree.get_children())
        for template in self.db.list_recurring_transactions():
            self.recurring_tree.insert("", END, iid=str(template["id"]), values=(
                template["id"], template["next_date"],
                self._RECURRING_FREQUENCY_LABELS.get(template["frequency"], template["frequency"]),
                template["account_name"], template["payee"],
                template["category_name"] or "", f"{template['amount']:.2f}",
            ))

    def _add_recurring(self):
        account_id = self._parse_id(self.rec_account_var.get())
        if account_id is None:
            messagebox.showwarning(APP_TITLE, "Choisissez un compte.")
            return
        category_id = self._parse_id(self.rec_category_var.get())
        date_text = self.rec_date_var.get().strip()
        try:
            from datetime import date as _date
            _date.fromisoformat(date_text)
        except ValueError:
            messagebox.showwarning(APP_TITLE, "La date doit etre au format AAAA-MM-JJ.")
            return
        try:
            amount = self._parse_float(self.rec_amount_var.get(), "Le montant")
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return
        if amount == 0:
            messagebox.showwarning(APP_TITLE, "Le montant ne peut pas etre nul.")
            return
        frequency = self._RECURRING_FREQUENCY_VALUES.get(self.rec_frequency_var.get(), "monthly")
        self.db.add_recurring_transaction(
            account_id, date_text, amount, frequency, category_id=category_id, payee=self.rec_payee_var.get(),
        )
        self.rec_payee_var.set("")
        self.rec_amount_var.set("")
        self._refresh_recurring()

    def _delete_recurring(self):
        selection = self.recurring_tree.selection()
        if not selection:
            messagebox.showinfo(APP_TITLE, "Selectionnez un modele d'abord.")
            return
        if not messagebox.askyesno(APP_TITLE, "Supprimer ce modele de transaction recurrente ?"):
            return
        self.db.delete_recurring_transaction(int(selection[0]))
        self._refresh_recurring()

    def _generate_recurring_now(self):
        created_ids = self.db.generate_due_recurring_transactions()
        self._refresh_recurring()
        self._refresh_transactions()
        self._refresh_accounts()
        self._refresh_budget()
        if created_ids:
            plural = "s" if len(created_ids) > 1 else ""
            messagebox.showinfo(APP_TITLE, f"{len(created_ids)} transaction{plural} recurrente{plural} generee{plural}.")
        else:
            messagebox.showinfo(APP_TITLE, "Aucune echeance due pour le moment.")

    def _auto_generate_recurring(self):
        # Silencieux si rien n'est du (cas courant) : seule une generation
        # reelle merite d'interrompre l'utilisateur a l'ouverture.
        created_ids = self.db.generate_due_recurring_transactions()
        if not created_ids:
            return
        self._refresh_recurring()
        self._refresh_transactions()
        self._refresh_accounts()
        self._refresh_budget()
        plural = "s" if len(created_ids) > 1 else ""
        messagebox.showinfo(
            APP_TITLE, f"{len(created_ids)} transaction{plural} recurrente{plural} generee{plural} automatiquement."
        )

    # -- onglet Rapports -------------------------------------------------------

    def _build_reports_tab(self):
        frame = self.reports_tab
        top = ttk.Frame(frame)
        top.pack(fill=X, padx=10, pady=10)

        ttk.Label(top, text="Periode :").pack(side=LEFT)
        self.reports_num_months_var = StringVar(value="6")
        self.reports_period_combo = ttk.Combobox(
            top, textvariable=self.reports_num_months_var, width=18, state="readonly",
            values=["3 derniers mois", "6 derniers mois", "12 derniers mois"],
        )
        self.reports_period_combo.current(1)
        self.reports_period_combo.pack(side=LEFT, padx=5)
        self.reports_period_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_reports())
        ttk.Button(top, text="Actualiser", command=self._refresh_reports).pack(side=LEFT, padx=10)

        self.reports_tree = ttk.Treeview(frame, show="headings", height=18)
        self.reports_tree.pack(fill=BOTH, expand=True, padx=10, pady=(0, 5))

        ttk.Label(
            frame,
            text="Depenses reelles par categorie et par mois (les remboursements et revenus ne sont pas comptes).",
            foreground="#666",
        ).pack(anchor="w", padx=10, pady=(0, 10))

    def _reports_num_months(self) -> int:
        return {0: 3, 1: 6, 2: 12}.get(self.reports_period_combo.current(), 6)

    def _refresh_reports(self):
        report = bg.spending_report(self.db, end_month=self.current_month, num_months=self._reports_num_months())
        months = report["months"]

        columns = ("category",) + tuple(months) + ("total",)
        self.reports_tree["columns"] = columns
        self.reports_tree.heading("category", text="Categorie")
        self.reports_tree.column("category", width=180, anchor="w")
        for month in months:
            self.reports_tree.heading(month, text=bg.month_label(month))
            self.reports_tree.column(month, width=100, anchor="e")
        self.reports_tree.heading("total", text="Total")
        self.reports_tree.column("total", width=110, anchor="e")

        self.reports_tree.delete(*self.reports_tree.get_children())
        for row in report["rows"]:
            values = (row["name"],) + tuple(bg.format_amount(row["amounts"][m]) for m in months) + (bg.format_amount(row["total"]),)
            self.reports_tree.insert("", END, values=values)

    # -- onglet Vue annuelle ----------------------------------------------------

    def _build_annual_tab(self):
        frame = self.annual_tab
        self.annual_year = int(self.current_month[:4])

        top = ttk.Frame(frame)
        top.pack(fill=X, padx=10, pady=10)
        ttk.Button(top, text="< Annee precedente", command=lambda: self._change_annual_year(-1)).pack(side=LEFT)
        self.annual_year_var = StringVar()
        ttk.Label(top, textvariable=self.annual_year_var, font=("Segoe UI", 12, "bold")).pack(side=LEFT, padx=15)
        ttk.Button(top, text="Annee suivante >", command=lambda: self._change_annual_year(1)).pack(side=LEFT)

        self.annual_tree = ttk.Treeview(frame, show="headings", height=18)
        self.annual_tree.pack(fill=BOTH, expand=True, padx=10, pady=(0, 5))

        ttk.Label(
            frame, text="Montant assigne a chaque categorie, mois par mois, sur toute l'annee.",
            foreground="#666",
        ).pack(anchor="w", padx=10, pady=(0, 10))

    def _change_annual_year(self, delta: int):
        self.annual_year += delta
        self._refresh_annual()

    def _refresh_annual(self):
        overview = bg.annual_budget_overview(self.db, self.annual_year)
        months = overview["months"]
        self.annual_year_var.set(str(self.annual_year))

        short_labels = [bg.month_label(m).split(" ")[0][:3] for m in months]
        columns = ("category",) + tuple(months) + ("total",)
        self.annual_tree["columns"] = columns
        self.annual_tree.heading("category", text="Categorie")
        self.annual_tree.column("category", width=180, anchor="w")
        for month, label in zip(months, short_labels):
            self.annual_tree.heading(month, text=label)
            self.annual_tree.column(month, width=80, anchor="e")
        self.annual_tree.heading("total", text="Total")
        self.annual_tree.column("total", width=110, anchor="e")

        self.annual_tree.delete(*self.annual_tree.get_children())
        for row in overview["rows"]:
            values = (row["name"],) + tuple(bg.format_amount(row["amounts"][m]) for m in months) + (bg.format_amount(row["total"]),)
            self.annual_tree.insert("", END, values=values)

    # -- onglet Parametres ------------------------------------------------------

    def _build_settings_tab(self):
        frame = self.settings_tab
        ttk.Label(
            frame,
            text="Toutes les donnees sont stockees localement sur cet ordinateur,\n"
                 "aucune connexion internet ni compte n'est necessaire.",
            justify=LEFT,
        ).pack(anchor="w", padx=10, pady=20)

        backup_frame = ttk.LabelFrame(frame, text="Sauvegarde", padding=10)
        backup_frame.pack(fill=X, padx=10, pady=(0, 10))
        ttk.Label(
            backup_frame,
            text="Tous vos comptes, categories, budgets et transactions tiennent dans un\n"
                 "seul fichier : sauvegardez-le regulierement pour ne rien perdre en cas\n"
                 "de probleme disque.",
            justify=LEFT,
        ).pack(anchor="w", pady=(0, 8))
        buttons = ttk.Frame(backup_frame)
        buttons.pack(anchor="w")
        ttk.Button(buttons, text="Sauvegarder les donnees...", command=self._backup_database).pack(side=LEFT)
        ttk.Button(buttons, text="Ouvrir le dossier de donnees", command=self._open_data_dir).pack(side=LEFT, padx=(6, 0))

    def _backup_database(self):
        from datetime import date
        from tkinter import filedialog

        default_name = f"enveloppe-sauvegarde-{date.today().isoformat()}.sqlite"
        path = filedialog.asksaveasfilename(
            title="Sauvegarder les donnees", initialfile=default_name,
            defaultextension=".sqlite", filetypes=[("Base SQLite", "*.sqlite")],
        )
        if not path:
            return
        import sqlite3
        try:
            self.db.backup_to(Path(path))
        except (OSError, ValueError, sqlite3.Error) as exc:
            # sqlite3.Error en plus d'OSError/ValueError : sqlite3.connect()
            # sur une destination inaccessible (dossier disparu, lecteur
            # demonte, chemin verrouille) leve un sqlite3.OperationalError,
            # qui n'est PAS une sous-classe d'OSError - sans cette clause, ce
            # cas pourtant plausible (cle USB retiree entre l'ouverture du
            # dialogue et le clic) remontait comme un plantage non gere au
            # lieu d'un message clair (meme motif que TempoFacture.Database.
            # backup_to / _backup_database).
            messagebox.showerror(APP_TITLE, f"Impossible d'enregistrer la sauvegarde : {exc}")
            return
        messagebox.showinfo(
            APP_TITLE,
            f"Sauvegarde enregistree :\n{path}\n\n"
            "Pour restaurer : fermez Enveloppe, puis remplacez le fichier de donnees "
            "actif par cette copie.",
        )

    def _open_data_dir(self):
        import os
        os.startfile(_data_dir())  # nosec - ouverture Explorateur Windows d'un dossier local

    # -- fermeture ------------------------------------------------------------

    def _on_close(self):
        self.db.close()
        self.root.destroy()


def main():
    root = Tk()
    EnveloppeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
