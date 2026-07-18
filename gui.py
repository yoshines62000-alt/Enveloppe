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

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=BOTH, expand=True, padx=8, pady=8)

        self.accounts_tab = ttk.Frame(notebook)
        self.categories_tab = ttk.Frame(notebook)
        self.budget_tab = ttk.Frame(notebook)
        self.transactions_tab = ttk.Frame(notebook)

        notebook.add(self.accounts_tab, text="Comptes")
        notebook.add(self.categories_tab, text="Categories")
        notebook.add(self.budget_tab, text="Budget")
        notebook.add(self.transactions_tab, text="Transactions")

        self._build_accounts_tab()
        self._build_categories_tab()
        self._build_budget_tab()
        self._build_transactions_tab()

        self._refresh_accounts()
        self._refresh_categories()
        self._refresh_budget()
        self._refresh_transactions()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

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

        columns = ("id", "name", "type", "balance", "archived")
        self.accounts_tree = ttk.Treeview(frame, columns=columns, show="headings", height=14)
        for col, label, width in [
            ("id", "ID", 40), ("name", "Nom", 200), ("type", "Type", 140),
            ("balance", "Solde actuel", 120), ("archived", "Archive", 70),
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

        ttk.Label(form, text="Nom de la categorie").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.category_name_var, width=25).grid(row=0, column=1, padx=5)
        ttk.Label(form, text="Groupe (optionnel)").grid(row=0, column=2, sticky="w")
        ttk.Entry(form, textvariable=self.category_group_var, width=20).grid(row=0, column=3, padx=5)
        ttk.Button(form, text="Ajouter la categorie", command=self._add_category).grid(row=0, column=4, padx=5)

        columns = ("id", "group", "name", "archived")
        self.categories_tree = ttk.Treeview(frame, columns=columns, show="headings", height=14)
        for col, label, width in [
            ("id", "ID", 40), ("group", "Groupe", 160), ("name", "Categorie", 200), ("archived", "Archive", 70),
        ]:
            self.categories_tree.heading(col, text=label)
            self.categories_tree.column(col, width=width, anchor="w")
        self.categories_tree.pack(fill=BOTH, expand=True, padx=10, pady=(0, 5))

        actions = ttk.Frame(frame)
        actions.pack(fill=X, padx=10, pady=(0, 10))
        ttk.Button(actions, text="Archiver / desarchiver", command=self._toggle_category_archived).pack(side=LEFT)

    def _add_category(self):
        name = self.category_name_var.get().strip()
        if not name:
            messagebox.showwarning(APP_TITLE, "Le nom de la categorie est obligatoire.")
            return
        self.db.add_category(name, self.category_group_var.get().strip())
        self.category_name_var.set("")
        self.category_group_var.set("")
        self._refresh_categories()
        self._refresh_budget()
        self._refresh_transactions()

    def _refresh_categories(self):
        self.categories_tree.delete(*self.categories_tree.get_children())
        for category in self.db.list_categories(include_archived=True):
            self.categories_tree.insert("", END, iid=str(category["id"]), values=(
                category["id"], category["group_name"] or "-", category["name"],
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

    # -- onglet Budget ------------------------------------------------------------

    def _build_budget_tab(self):
        frame = self.budget_tab
        top = ttk.Frame(frame)
        top.pack(fill=X, padx=10, pady=10)

        ttk.Button(top, text="< Mois precedent", command=lambda: self._change_month(-1)).pack(side=LEFT)
        self.budget_month_var = StringVar()
        ttk.Label(top, textvariable=self.budget_month_var, font=("Segoe UI", 12, "bold")).pack(side=LEFT, padx=15)
        ttk.Button(top, text="Mois suivant >", command=lambda: self._change_month(1)).pack(side=LEFT)

        self.ready_to_assign_var = StringVar()
        ready_label = ttk.Label(top, textvariable=self.ready_to_assign_var, font=("Segoe UI", 12, "bold"))
        ready_label.pack(side=RIGHT, padx=10)

        columns = ("group", "category", "budgeted", "activity", "available")
        self.budget_tree = ttk.Treeview(frame, columns=columns, show="headings", height=18)
        for col, label, width in [
            ("group", "Groupe", 140), ("category", "Categorie", 180), ("budgeted", "Budgete", 110),
            ("activity", "Activite (ce mois)", 130), ("available", "Disponible", 110),
        ]:
            self.budget_tree.heading(col, text=label)
            self.budget_tree.column(col, width=width, anchor="w")
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
        for category in self.db.list_categories():
            budgeted = self.db.get_budget_entry(category["id"], self.current_month)
            activity = bg.category_activity_for_month(self.db, category["id"], self.current_month)
            available = bg.category_available(self.db, category["id"], self.current_month)
            self.budget_tree.insert("", END, iid=str(category["id"]), values=(
                category["group_name"] or "-", category["name"],
                bg.format_amount(budgeted), bg.format_amount(activity), bg.format_amount(available),
            ))
        ready = bg.ready_to_assign(self.db, self.current_month)
        self.ready_to_assign_var.set(f"Reste a assigner : {bg.format_amount(ready)}")

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

        columns = ("id", "date", "account", "payee", "category", "amount")
        self.transactions_tree = ttk.Treeview(frame, columns=columns, show="headings", height=14)
        for col, label, width in [
            ("id", "ID", 40), ("date", "Date", 100), ("account", "Compte", 140),
            ("payee", "Beneficiaire", 180), ("category", "Categorie", 140), ("amount", "Montant", 100),
        ]:
            self.transactions_tree.heading(col, text=label)
            self.transactions_tree.column(col, width=width, anchor="w")
        self.transactions_tree.pack(fill=BOTH, expand=True, padx=10, pady=(5, 5))

        actions = ttk.Frame(frame)
        actions.pack(fill=X, padx=10, pady=(0, 10))
        ttk.Button(actions, text="Supprimer la transaction selectionnee", command=self._delete_transaction).pack(side=LEFT)

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
            self.transactions_tree.insert("", END, iid=str(tx["id"]), values=(
                tx["id"], tx["date"], tx["account_name"], tx["payee"],
                tx["category_name"] or "-", bg.format_amount(tx["amount"]),
            ))

    def _delete_transaction(self):
        selection = self.transactions_tree.selection()
        if not selection:
            messagebox.showinfo(APP_TITLE, "Selectionnez une transaction d'abord.")
            return
        if not messagebox.askyesno(APP_TITLE, "Supprimer cette transaction ?"):
            return
        self.db.delete_transaction(int(selection[0]))
        self._refresh_transactions()
        self._refresh_accounts()
        self._refresh_budget()

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
