"""Interface Tkinter d'Enveloppe : comptes, categories, budget mensuel a
enveloppes (zero-based budgeting) et transactions, relies a la meme base
SQLite locale. Aucune connexion bancaire, aucun cloud - tout reste sur la
machine de l'utilisateur."""

from __future__ import annotations

import ctypes
import json
import math
import queue
import sys
import threading
import webbrowser
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, Y, StringVar, Tk, ttk, messagebox
from typing import Optional

import budget as bg
import update_checker
from csv_transactions import CsvImportError, export_transactions_csv, import_transactions_csv
from db import Database

APP_TITLE = "Enveloppe"
DONATE_URL = "https://ko-fi.com/yoshines62000"
APP_VERSION = "1.0.14"
UPDATE_REPO = "yoshines62000-alt/Enveloppe"
RELEASES_URL = f"https://github.com/{UPDATE_REPO}/releases/latest"


def _resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def _data_dir() -> Path:
    return Path.home() / "AppData" / "Roaming" / "Enveloppe"


_dpi_awareness_configured = False


def _configure_dpi_awareness() -> None:
    """Rend le processus explicitement "Per-Monitor V2 DPI Aware" AVANT
    toute creation de fenetre Tk (audit D30) : sans manifeste ni appel
    explicite, un executable PyInstaller n'est, par defaut, PAS declare
    sensible au DPI - Windows le traite alors comme "DPI-unaware" et
    applique un lissage bitmap a toute l'application des qu'un facteur
    d'echelle superieur a 100% est actif (125%/150%/200%, tres courant sur
    portables/ecrans modernes), produisant un rendu visiblement flou du
    texte et des icones.
    DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (-4) est disponible depuis
    Windows 10 1703 ; repli sur les API plus anciennes (Windows 8.1 puis
    Vista) pour rester fonctionnel sur un systeme plus ancien.
    Complementaire du manifeste embarque dans l'executable PyInstaller
    (Enveloppe.manifest, reference par Enveloppe.spec) qui couvre le meme
    besoin AVANT meme que Python ne demarre - le manifeste est la methode
    recommandee par Microsoft pour un executable natif ; cet appel ctypes
    reste un filet de securite actif meme lance depuis le code source
    (`python gui.py`, sans passer par l'exe empaquete). Meme pattern deja
    applique et verifie sur le projet GuideExpress.
    Idempotent (protege par `_dpi_awareness_configured`) : applique au
    moment de l'import du module, avant que main() ou un test ne puisse
    construire la premiere fenetre Tk() - un appel APRES la creation de la
    premiere fenetre Tk n'aurait aucun effet (la sensibilite DPI d'un
    processus Windows ne peut etre definie qu'une seule fois, avant toute
    fenetre)."""
    global _dpi_awareness_configured
    if _dpi_awareness_configured or sys.platform != "win32":
        return
    try:
        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            _dpi_awareness_configured = True
            return
    except (AttributeError, OSError):
        pass
    try:
        PROCESS_PER_MONITOR_DPI_AWARE = 2
        if ctypes.windll.shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE) == 0:
            _dpi_awareness_configured = True
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass  # echec silencieux : au pire, comportement pre-correctif (non DPI-aware) - jamais bloquant pour le reste de l'app
    _dpi_awareness_configured = True


# Applique au moment de l'import du module, avant que __main__ ou un test ne
# construise la premiere fenetre Tk() - voir la docstring de la fonction.
_configure_dpi_awareness()


class EnveloppeApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1000x680")
        # Sans minsize, la fenetre pouvait etre reduite a n'importe quelle
        # taille (verifie jusqu'a 400x300 lors de l'audit) : les intitules
        # d'onglets se tronquaient, et surtout l'indicateur "Reste a
        # assigner" (onglet Budget) pouvait sortir entierement du cadre
        # visible des ~620x420 de large. 950x620 est une taille mesuree
        # (winfo_reqwidth) pour que tous les elements critiques (onglets,
        # indicateur "Reste a assigner", boutons d'action dont "Pointer /
        # depointer") restent entierement lisibles avec de la marge, sans
        # troncature - voir tests/test_gui_smoke.py pour la verification
        # verrouillee.
        self.root.minsize(950, 620)

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
        ttk.Label(bottom_bar, text=f"v{APP_VERSION}", foreground="#666").pack(side=LEFT, padx=(8, 0), pady=4)
        self.update_status_var = StringVar(value="")
        self.update_status_label = ttk.Label(bottom_bar, textvariable=self.update_status_var, foreground="#666")
        self.update_status_label.pack(side=LEFT, padx=(6, 0), pady=4)
        donate_label = ttk.Label(bottom_bar, text="☕ Soutenir le projet", foreground="#0645AD", cursor="hand2")
        donate_label.pack(side=RIGHT, padx=8, pady=4)
        donate_label.bind("<Button-1>", lambda event: webbrowser.open(DONATE_URL))

        self._update_check_queue = queue.Queue()
        update_checker.start_update_check(APP_VERSION, UPDATE_REPO, self._update_check_queue)
        self.root.after(500, self._poll_update_check)

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
        # Avertit si un import CSV precedent a ete interrompu brutalement
        # (audit D44) avant de generer les transactions recurrentes -
        # differe via after() comme _auto_generate_recurring juste en
        # dessous, pour laisser la fenetre principale s'afficher d'abord
        # plutot que de faire apparaitre une boite de dialogue avant meme
        # que la fenetre ne soit peinte a l'ecran.
        self.root.after(100, self._check_stale_csv_import_marker)
        # Genere les transactions recurrentes dues avant que l'utilisateur ne
        # commence a consulter ses comptes, pour que les soldes affiches des
        # l'ouverture soient deja a jour. Differe via after() (pas
        # d'appel direct dans __init__) pour laisser la fenetre principale
        # s'afficher d'abord.
        self.root.after(200, self._auto_generate_recurring)

    def _poll_update_check(self):
        try:
            status, tag = self._update_check_queue.get_nowait()
        except queue.Empty:
            self.root.after(500, self._poll_update_check)
            return
        if status == "update_available":
            self.update_status_var.set(f"Mise a jour disponible : {tag} - Telecharger")
            self.update_status_label.configure(foreground="#0645AD", cursor="hand2")
            self.update_status_label.bind("<Button-1>", lambda event: webbrowser.open(RELEASES_URL))
        elif status == "up_to_date":
            self.update_status_var.set("A jour")
            self.update_status_label.configure(foreground="#1B7A1B", cursor="")
        # "check_failed" (hors ligne, GitHub inaccessible...) : on ne
        # revendique rien plutot que d'afficher a tort "a jour".

    # -- utilitaires communs --------------------------------------------------

    def _account_choices(self):
        accounts = self.db.list_accounts()
        return accounts, [f"{a['id']} - {a['name']}" for a in accounts]

    def _category_choices(self):
        categories = self.db.list_categories()
        return categories, [f"{c['id']} - {c['name']}" for c in categories]

    def _move_dialog_category_choices(self):
        """Categories proposees dans le dialogue "Deplacer entre
        enveloppes..." (audit D1) : les categories actives, PLUS toute
        categorie archivee qui a encore un solde non nul ce mois-ci (meme
        logique que _refresh_budget/archived_with_balance). Sans ceci,
        l'argent range dans une categorie archivee restait visible dans
        l'onglet Budget (grisee, suffixe "(archivee)") mais totalement
        bloque : impossible de le deplacer sans d'abord desarchiver la
        categorie dans l'onglet Categories, la deplacer, puis eventuellement
        la re-archiver - un detour non documente nulle part dans l'IHM.
        Les deux sens (source ET destination) beneficient de la meme liste,
        le suffixe "(archivee)" restant affiche pour que le choix soit
        explicite plutot que silencieux."""
        active_categories = self.db.list_categories(include_archived=False)
        active_ids = {c["id"] for c in active_categories}
        archived_with_balance = [
            c for c in self.db.list_categories(include_archived=True)
            if c["id"] not in active_ids and bg.category_available(self.db, c["id"], self.current_month) != 0
        ]
        categories = active_categories + archived_with_balance
        labels = [
            f"{c['id']} - {c['name']}" + (" (archivee)" if c["id"] not in active_ids else "")
            for c in categories
        ]
        return categories, labels

    @staticmethod
    def _parse_id(combo_value: str):
        if not combo_value:
            return None
        return int(combo_value.split(" - ", 1)[0])

    @staticmethod
    def _parse_float(text: str, field_label: str) -> float:
        try:
            value = float(text.strip().replace(",", ".") or 0)
        except ValueError:
            raise ValueError(f"{field_label} doit etre un nombre.")
        # "inf", "-inf" et "nan" sont acceptes par float() sans lever
        # d'exception, et corrompent irreversiblement les soldes (voir
        # db._validate_amount) - rejetes ici deja, au niveau de la saisie,
        # pour un message d'erreur clair au lieu de laisser planter le
        # callback Tkinter plus loin (IntegrityError non geree pour nan,
        # contamination silencieuse de tous les soldes pour inf).
        if not math.isfinite(value):
            raise ValueError(f"{field_label} doit etre un nombre fini.")
        return value

    @staticmethod
    def _parse_iso_date(text: str) -> str:
        """Valide qu'une date saisie est bien au format AAAA-MM-JJ (ISO) et
        renvoie le texte tel quel (deja strip()e) si valide, ou leve
        ValueError avec un message pret a afficher sinon. Factorise un motif
        auparavant duplique a l'identique dans _add_transaction,
        _edit_transaction (dialogue "on_save") et _add_recurring (audit
        D59) : un futur correctif touchant la validation de date (ex :
        accepter aussi JJ/MM/AAAA comme le fait deja l'import CSV, voir
        csv_transactions._parse_csv_date) n'aurait sinon besoin d'etre
        applique qu'a un seul endroit, plutot que de risquer d'en oublier un
        - risque deja materialise par D31/D32 (deux dialogues distincts
        avaient echappe a une correction similaire sur la virgule
        decimale)."""
        text = text.strip()
        from datetime import date as _date
        try:
            _date.fromisoformat(text)
        except ValueError:
            raise ValueError("La date doit etre au format AAAA-MM-JJ.")
        return text

    def _parse_nonzero_amount(self, text: str, field_label: str = "Le montant") -> float:
        """_parse_float() suivi du rejet d'un montant nul (une transaction,
        une recurrence... de montant 0 n'a pas de sens metier) - meme motif
        de factorisation que _parse_iso_date ci-dessus (audit D59), pour le
        second demi-pattern duplique dans les memes trois methodes."""
        amount = self._parse_float(text, field_label)
        if amount == 0:
            raise ValueError(f"{field_label} ne peut pas etre nul.")
        return amount

    def _open_amount_edit_dialog(self, prompt: str, initial_value, on_save):
        """Boite de saisie d'un montant, maison (Toplevel + Entry +
        _parse_float) - remplace tkinter.simpledialog.askfloat (audit
        D31/D32). askfloat s'appuie en interne sur self.tk.getdouble(), qui
        REJETTE la virgule comme separateur decimal francais ('12,50' leve
        TclError, alors que '12.50' passe) - incoherent avec _parse_float(),
        deja utilise partout ailleurs dans l'application (formulaires de
        transaction, fractionnement, virement...) pour accepter les deux.
        Ses messages d'erreur natifs sont aussi codes en dur en anglais
        ("Not a floating-point value. Please try again"), rompant la
        coherence 100% francaise du reste de l'IHM (D32) - remplaces ici par
        les messages de _parse_float, affiches via messagebox.showwarning
        comme partout ailleurs dans l'application.

        `on_save(value: float)` est appele avec le montant valide des que
        l'utilisateur confirme (bouton "Enregistrer" ou touche Entree) ; le
        dialogue se ferme alors automatiquement. Rien n'est appele si
        l'utilisateur annule (bouton "Annuler", touche Echap - audit D33 -
        ou fermeture de la fenetre)."""
        from tkinter import Toplevel

        dialog = Toplevel(self.root)
        dialog.title(APP_TITLE)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        ttk.Label(dialog, text=prompt, wraplength=320, justify="left").grid(
            row=0, column=0, sticky="w", padx=10, pady=(10, 5),
        )
        value_var = StringVar(value=str(initial_value) if initial_value else "0")
        entry = ttk.Entry(dialog, textvariable=value_var, width=15)
        entry.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 5))
        entry.focus_set()
        entry.select_range(0, "end")

        def on_confirm(event=None):
            try:
                value = self._parse_float(value_var.get(), "Le montant")
            except ValueError as exc:
                messagebox.showwarning(APP_TITLE, str(exc), parent=dialog)
                return
            dialog.destroy()
            on_save(value)

        buttons = ttk.Frame(dialog)
        buttons.grid(row=2, column=0, pady=10)
        ttk.Button(buttons, text="Enregistrer", command=on_confirm).pack(side=LEFT, padx=5)
        ttk.Button(buttons, text="Annuler", command=dialog.destroy).pack(side=LEFT, padx=5)
        entry.bind("<Return>", on_confirm)
        # Echap annule et ferme le dialogue, sans rien enregistrer (audit
        # D33) - lie sur le Toplevel plutot que sur le seul champ de saisie
        # pour rester actif meme si le focus est ailleurs dans le dialogue.
        dialog.bind("<Escape>", lambda e: dialog.destroy())

    # -- onglet Comptes ---------------------------------------------------------

    def _build_accounts_tab(self):
        frame = self.accounts_tab
        form = ttk.Frame(frame)
        form.pack(fill=X, padx=10, pady=10)

        self.account_name_var = StringVar()
        self.account_type_var = StringVar()
        self.account_balance_var = StringVar(value="0")

        ttk.Label(form, text="Nom").grid(row=0, column=0, sticky="w")
        name_entry = ttk.Entry(form, textvariable=self.account_name_var, width=25)
        name_entry.grid(row=0, column=1, padx=5)
        ttk.Label(form, text="Type (courant, epargne...)").grid(row=0, column=2, sticky="w")
        type_entry = ttk.Entry(form, textvariable=self.account_type_var, width=20)
        type_entry.grid(row=0, column=3, padx=5)
        ttk.Label(form, text="Solde de depart").grid(row=0, column=4, sticky="w")
        balance_entry = ttk.Entry(form, textvariable=self.account_balance_var, width=12)
        balance_entry.grid(row=0, column=5, padx=5)
        ttk.Button(form, text="Ajouter le compte", command=self._add_account).grid(row=0, column=6, padx=5)
        # Entree valide le formulaire depuis n'importe lequel de ses champs
        # (audit D33), a l'image du bouton "Ajouter le compte" - lie
        # individuellement sur chaque champ (pas sur `form`, qui ne fait
        # jamais partie des bindtags par defaut d'un widget qu'il contient :
        # verifie empiriquement, seuls le widget lui-meme, sa classe, le
        # Toplevel/fenetre racine qui le contient et "all" recoivent les
        # evenements clavier non consommes).
        for field in (name_entry, type_entry, balance_entry):
            field.bind("<Return>", lambda e: self._add_account())

        columns = ("id", "name", "type", "balance", "cleared_balance", "archived")
        self.accounts_tree = ttk.Treeview(frame, columns=columns, show="headings", height=14)
        for col, label, width in [
            ("id", "ID", 40), ("name", "Nom", 200), ("type", "Type", 140),
            ("balance", "Solde actuel", 120), ("cleared_balance", "Solde pointe", 120),
            ("archived", "Archive", 70),
        ]:
            self.accounts_tree.heading(col, text=label)
            self.accounts_tree.column(col, width=width, anchor="w", stretch=True)
        # Meme convention de coloration que le tableau Budget (tag
        # "overspent", _build_budget_tab) pour un compte a decouvert (audit
        # D37) : un solde negatif y est deja mis en evidence en rouge, mais
        # rien n'existait cote Comptes - un compte a decouvert (au moins
        # aussi grave qu'une enveloppe en depassement) s'affichait dans le
        # meme style neutre qu'un solde positif, sans signal visuel.
        self.accounts_tree.tag_configure("negative", foreground="#B00020")
        self.accounts_tree.pack(fill=BOTH, expand=True, padx=10, pady=(0, 5))

        # Ligne de total agrege (audit D5) : jusqu'ici, aucun total n'etait
        # jamais affiche dans cet onglet - la seule facon de verifier que la
        # somme des soldes de comptes correspondait bien a ce qu'on
        # attendait etait de les additionner mentalement, ou de se fier au
        # "Reste a assigner" de l'onglet Budget (qui n'est meme pas etiquete
        # comme "total des comptes"). Inclut les comptes archives (meme
        # perimetre que total_on_budget_balance/account_cleared_balance
        # somme sur list_accounts(include_archived=True)) : archiver un
        # compte ne fait jamais disparaitre l'argent qu'il contient
        # reellement du total, memes motifs que D1/D2/D3.
        self.accounts_total_var = StringVar()
        total_row = ttk.Frame(frame)
        total_row.pack(fill=X, padx=10, pady=(0, 5))
        ttk.Label(total_row, textvariable=self.accounts_total_var, font=("Segoe UI", 10, "bold")).pack(side=RIGHT)

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
        total_balance = 0.0
        total_cleared = 0.0
        for account in self.db.list_accounts(include_archived=True):
            balance = self.db.account_balance(account["id"])
            cleared_balance = self.db.account_cleared_balance(account["id"])
            total_balance += balance
            total_cleared += cleared_balance
            self.accounts_tree.insert("", END, iid=str(account["id"]), values=(
                account["id"], account["name"], account["type"],
                bg.format_amount(balance),
                bg.format_amount(cleared_balance),
                "Oui" if account["archived"] else "Non",
            ), tags=("negative",) if balance < 0 else ())
        self.accounts_total_var.set(
            f"Total : {bg.format_amount(round(total_balance, 2))} "
            f"(pointe : {bg.format_amount(round(total_cleared, 2))})"
        )
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
        name_entry = ttk.Entry(form, textvariable=self.category_name_var, width=25)
        name_entry.grid(row=0, column=1, padx=5)
        ttk.Label(form, text="Groupe (optionnel)").grid(row=0, column=2, sticky="w")
        group_entry = ttk.Entry(form, textvariable=self.category_group_var, width=20)
        group_entry.grid(row=0, column=3, padx=5)
        ttk.Label(form, text="Objectif d'epargne (optionnel)").grid(row=1, column=0, sticky="w", pady=(5, 0))
        goal_entry = ttk.Entry(form, textvariable=self.category_goal_var, width=12)
        goal_entry.grid(row=1, column=1, sticky="w", pady=(5, 0))
        ttk.Button(form, text="Ajouter la categorie", command=self._add_category).grid(row=1, column=4, pady=(5, 0))
        # Entree valide le formulaire depuis n'importe lequel de ses champs
        # (audit D33).
        for field in (name_entry, group_entry, goal_entry):
            field.bind("<Return>", lambda e: self._add_category())

        columns = ("id", "group", "name", "goal", "archived")
        self.categories_tree = ttk.Treeview(frame, columns=columns, show="headings", height=14)
        for col, label, width in [
            ("id", "ID", 40), ("group", "Groupe", 160), ("name", "Categorie", 200),
            ("goal", "Objectif d'epargne", 130), ("archived", "Archive", 70),
        ]:
            self.categories_tree.heading(col, text=label)
            self.categories_tree.column(col, width=width, anchor="w", stretch=True)
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

        def on_save(new_value):
            self.db.update_category(category_id, savings_goal=new_value if new_value > 0 else None)
            self._refresh_categories()
            self._refresh_budget()

        self._open_amount_edit_dialog(
            "Objectif d'epargne (laisser vide ou 0 pour aucun objectif) :",
            category["savings_goal"] or 0.0, on_save,
        )

    # -- onglet Budget ------------------------------------------------------------

    def _build_budget_tab(self):
        frame = self.budget_tab
        top = ttk.Frame(frame)
        top.pack(fill=X, padx=10, pady=(10, 0))

        ttk.Button(top, text="< Mois precedent", command=lambda: self._change_month(-1)).pack(side=LEFT)
        self.budget_month_var = StringVar()
        ttk.Label(top, textvariable=self.budget_month_var, font=("Segoe UI", 12, "bold")).pack(side=LEFT, padx=15)
        ttk.Button(top, text="Mois suivant >", command=lambda: self._change_month(1)).pack(side=LEFT)
        ttk.Button(top, text="Copier le budget du mois precedent", command=self._copy_previous_month_budget).pack(side=LEFT, padx=15)
        ttk.Button(top, text="Deplacer entre enveloppes...", command=self._open_move_between_envelopes_dialog).pack(side=LEFT)

        # "Reste a assigner" sur sa PROPRE ligne (audit D25) : le README le
        # decrit comme un indicateur "toujours visible", mais tant qu'il
        # cohabitait avec les boutons de navigation sur une seule ligne
        # (side=LEFT pour les boutons, side=RIGHT pour lui), pack() ne
        # renvoie jamais a la ligne - des que la largeur cumulee depassait
        # celle de la fenetre, cet indicateur (le dernier empaquete a
        # droite) sortait purement et simplement du cadre visible, y
        # compris a des tailles de fenetre raisonnables (620x420). En le
        # placant sur sa propre ligne pleine largeur, il ne concurrence
        # plus jamais les boutons pour l'espace horizontal et reste visible
        # tant que la fenetre depasse ~220px de large (voir aussi
        # root.minsize() dans __init__, qui empeche de toute facon de
        # descendre sous une largeur ou l'un ou l'autre poserait probleme).
        ready_row = ttk.Frame(frame)
        ready_row.pack(fill=X, padx=10, pady=(4, 10))
        self.ready_to_assign_var = StringVar()
        ready_label = ttk.Label(ready_row, textvariable=self.ready_to_assign_var, font=("Segoe UI", 12, "bold"))
        ready_label.pack(side=RIGHT)

        columns = ("group", "category", "budgeted", "activity", "available", "goal_progress")
        self.budget_tree = ttk.Treeview(frame, columns=columns, show="headings", height=18)
        # Categorie elargie a 220px (au lieu de 180) : le suffixe " (archivee)"
        # ajoute automatiquement au nom d'une categorie archivee a solde non
        # nul (voir archived_with_balance) allonge le libelle affiche, qui se
        # tronquait visuellement pour un nom de categorie deja un peu long
        # (audit D28 - ex : "Ancien projet (vacances 2023) (archiv...").
        # stretch=True explicite sur toutes les colonnes (audit D29) : deja
        # la valeur par defaut de ttk.Treeview, mais le rendre explicite
        # documente l'intention (repartition proportionnelle de l'espace
        # excedentaire entre colonnes plutot qu'une bande vide a droite du
        # tableau sur un grand ecran) independamment d'un futur changement
        # de comportement par defaut de Tk.
        for col, label, width in [
            ("group", "Groupe", 140), ("category", "Categorie", 220), ("budgeted", "Budgete", 110),
            ("activity", "Activite (ce mois)", 130), ("available", "Disponible", 110),
            ("goal_progress", "Objectif d'epargne", 150),
        ]:
            self.budget_tree.heading(col, text=label)
            self.budget_tree.column(col, width=width, anchor="w", stretch=True)
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
        all_categories = self.db.list_categories(include_archived=True)
        # bg.category_available calcule UNE SEULE FOIS par categorie pour
        # tout ce rafraichissement (audit D40) : ce dictionnaire est ensuite
        # reutilise pour l'affichage de chaque ligne ci-dessous, pour le
        # calcul du "Reste a assigner" (bg.ready_to_assign_from_available,
        # au lieu de rappeler bg.ready_to_assign qui refait exactement le
        # meme calcul en interne) et pour le comptage des enveloppes en
        # depassement (_refresh_overspent_summary) - auparavant jusqu'a 3
        # appels independants a category_available (donc 6 requetes SQL) par
        # categorie et par rafraichissement au lieu d'un seul (2 requetes).
        available_by_category = {
            c["id"]: bg.category_available(self.db, c["id"], self.current_month) for c in all_categories
        }
        archived_with_balance = [
            c for c in all_categories if c["id"] not in active_ids and available_by_category[c["id"]] != 0
        ]
        for category in active_categories + archived_with_balance:
            is_archived = category["id"] not in active_ids
            budgeted = self.db.get_budget_entry(category["id"], self.current_month)
            activity = bg.category_activity_for_month(self.db, category["id"], self.current_month)
            available = available_by_category[category["id"]]
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
        ready = bg.ready_to_assign_from_available(self.db, available_by_category)
        self.ready_to_assign_var.set(f"Reste a assigner : {bg.format_amount(ready)}")
        self._refresh_overspent_summary(available_by_category)

    def _refresh_overspent_summary(self, available_by_category: Optional[dict] = None):
        """Met a jour la banniere proactive de depassement (voir sa creation
        dans __init__). Compte toute categorie (active OU archivee : un
        depassement range dans une categorie archivee reste un depassement
        reel, meme motif que le coloriage "overspent" de l'onglet Budget, qui
        inclut deja archived_with_balance) dont le disponible du mois affiche
        est negatif.

        `available_by_category` (audit D40) : dictionnaire {category_id:
        disponible} DEJA calcule par l'appelant (typiquement _refresh_budget,
        qui en a de toute facon besoin pour l'affichage) - evite de rappeler
        bg.category_available une troisieme fois par categorie. Reste
        optionnel (calcule alors lui-meme, sur toutes les categories) pour
        que cette methode puisse toujours etre appelee independamment."""
        if available_by_category is None:
            available_by_category = {
                c["id"]: bg.category_available(self.db, c["id"], self.current_month)
                for c in self.db.list_categories(include_archived=True)
            }
        count = sum(1 for available in available_by_category.values() if available < 0)
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
        categories, category_labels = self._move_dialog_category_choices()
        if len(categories) < 2:
            messagebox.showwarning(APP_TITLE, "Il faut au moins deux categories pour deplacer de l'argent.")
            return

        # Pre-selectionne la categorie de la ligne choisie dans le tableau,
        # y compris si elle est archivee (audit D1) : une ligne archivee a
        # solde non nul apparait dans _move_dialog_category_choices() comme
        # n'importe quelle autre, donc peut etre pre-selectionnee de la
        # meme facon.
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
        # Entree valide, Echap annule (audit D33) - lies sur le Toplevel
        # plutot que sur un champ precis : un KeyPress non consomme par le
        # widget qui a le focus (Entry/Combobox readonly, verifie
        # empiriquement) remonte jusqu'au binding du Toplevel qui le
        # contient, donc actif quel que soit le champ en cours de saisie.
        dialog.bind("<Return>", lambda e: on_save())
        dialog.bind("<Escape>", lambda e: dialog.destroy())

    def _edit_budget_entry(self, event=None):
        selection = self.budget_tree.selection()
        if not selection:
            return
        category_id = int(selection[0])
        category = self.db.get_category(category_id)
        current = self.db.get_budget_entry(category_id, self.current_month)

        def on_save(new_value):
            self.db.set_budget_entry(category_id, self.current_month, round(new_value, 2))
            self._refresh_budget()

        self._open_amount_edit_dialog(
            f"Montant budgete pour '{category['name']}' en {bg.month_label(self.current_month)} :",
            current, on_save,
        )

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
        date_entry = ttk.Entry(form, textvariable=self.tx_date_var, width=12)
        date_entry.grid(row=0, column=5, padx=5)

        ttk.Label(form, text="Beneficiaire").grid(row=1, column=0, sticky="w", pady=(5, 0))
        payee_entry = ttk.Entry(form, textvariable=self.tx_payee_var, width=25)
        payee_entry.grid(row=1, column=1, columnspan=2, sticky="we", pady=(5, 0))
        ttk.Label(form, text="Montant (negatif = depense)").grid(row=1, column=3, sticky="w", pady=(5, 0))
        amount_entry = ttk.Entry(form, textvariable=self.tx_amount_var, width=12)
        amount_entry.grid(row=1, column=4, pady=(5, 0))
        ttk.Button(form, text="Ajouter", command=self._add_transaction).grid(row=1, column=5, pady=(5, 0))
        # Entree valide le formulaire d'ajout de transaction depuis
        # n'importe lequel de ses champs (audit D33 - priorite : c'est le
        # geste de saisie le plus repete de l'usage quotidien de l'app).
        for field in (
            self.tx_account_combo, self.tx_category_combo, date_entry, payee_entry, amount_entry,
        ):
            field.bind("<Return>", lambda e: self._add_transaction())

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
            self.transactions_tree.column(col, width=width, anchor="w", stretch=True)
        self.transactions_tree.pack(fill=BOTH, expand=True, padx=10, pady=(5, 5))
        self.transactions_tree.bind("<Double-1>", self._edit_transaction)

        # Astuce + statut d'import sur leur PROPRE ligne, separee de la barre
        # de boutons (audit D26) : quand ce texte cohabitait avec les 6
        # boutons d'action sur une seule ligne pack()ee, leur largeur
        # cumulee depassait deja l'espace disponible a la taille de fenetre
        # PAR DEFAUT (1000x680) - le bouton "Pointer / depointer" (dernier
        # empaquete a droite, donc le plus a gauche du groupe, colle contre
        # le texte de gauche) recevait alors moins que sa largeur demandee
        # et s'affichait tronque en "Poin". Deplacer ce texte hors de la
        # ligne de boutons libere assez d'espace pour que tous les boutons
        # d'action gardent leur libelle complet, y compris a root.minsize().
        hint_row = ttk.Frame(frame)
        hint_row.pack(fill=X, padx=10)
        ttk.Label(hint_row, text="Double-cliquez sur une ligne pour la modifier.", foreground="#666").pack(side=LEFT)
        self.import_status_var = StringVar(value="")
        ttk.Label(hint_row, textvariable=self.import_status_var, foreground="#666").pack(side=LEFT, padx=(10, 0))

        actions = ttk.Frame(frame)
        actions.pack(fill=X, padx=10, pady=(4, 10))
        ttk.Button(actions, text="Supprimer la transaction selectionnee", command=self._delete_transaction).pack(side=RIGHT)
        self.import_csv_button = ttk.Button(actions, text="Importer un CSV...", command=self._import_transactions_csv)
        self.import_csv_button.pack(side=RIGHT, padx=(0, 5))
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
                except ValueError as exc:
                    messagebox.showwarning(APP_TITLE, str(exc), parent=dialog)
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
        # Entree valide, Echap annule (audit D33).
        dialog.bind("<Return>", lambda e: on_save())
        dialog.bind("<Escape>", lambda e: dialog.destroy())

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

    def _csv_import_marker_path(self) -> Path:
        """Fichier "temoin" d'un import CSV en cours (audit D44) - voir
        _import_transactions_csv/_poll_csv_import (ecriture/suppression) et
        _check_stale_csv_import_marker (verification au demarrage). Place a
        cote du fichier de donnees actif plutot que dans un dossier temp
        systeme : garantit qu'il reste sur le meme volume/dossier utilisateur
        meme si l'application est deplacee, et evite toute collision entre
        plusieurs installations pointant vers des bases differentes."""
        return Path(self.db.path).parent / "import_csv_en_cours.json"

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

        # Fichier "temoin" ecrit AVANT de lancer l'import (audit D44) : grace
        # au mode WAL et aux commits par lots de import_transactions_csv,
        # une coupure (crash, coupure de courant) en plein import ne perd au
        # pire que les lignes du lot en cours (jusqu'a 199, deja borne et
        # acceptable) SANS corrompre le fichier de donnees - mais rien
        # jusqu'ici n'avertissait l'utilisateur, au lancement suivant, qu'un
        # import precedent avait ete interrompu avant d'avoir pu se
        # terminer normalement. Supprime par _poll_csv_import des que
        # l'import se termine (succes OU erreur geree normalement) : seule
        # une interruption BRUTALE (le processus meurt avant d'atteindre
        # _poll_csv_import) laisse ce fichier derriere elle, ce qui est
        # precisement le signal recherche. Ecriture best-effort (jamais
        # bloquante pour l'import lui-meme si le dossier est en lecture
        # seule ou temporairement indisponible).
        try:
            from datetime import datetime as _datetime
            self._csv_import_marker_path().write_text(
                json.dumps({"source_file": str(input_path), "started_at": _datetime.now().isoformat()}),
                encoding="utf-8",
            )
        except OSError:
            pass

        # Un import de plusieurs milliers de lignes reste une operation de
        # plusieurs secondes meme apres l'optimisation des commits (voir
        # csv_transactions.py) - execute sur un thread separe (jamais sur le
        # thread Tk) pour que la fenetre reste reactive, meme mecanisme
        # thread + queue.Queue + root.after(...) que update_checker.py /
        # _poll_update_check. Le worker ouvre sa PROPRE connexion Database
        # vers le meme fichier plutot que de reutiliser self.db.conn : une
        # connexion sqlite3 ne doit etre utilisee que depuis le thread qui
        # l'a creee, et self.db reste utilisable normalement (lecture seule,
        # tant que le bouton d'import est desactive) pendant que l'import
        # tourne.
        result_queue: "queue.Queue" = queue.Queue()

        def worker():
            try:
                import_db = Database(Path(self.db.path))
                try:
                    result = import_transactions_csv(import_db, Path(input_path), default_account_id=default_account_id)
                finally:
                    import_db.close()
                result_queue.put(("done", result))
            except CsvImportError as exc:
                result_queue.put(("error", str(exc)))
            except Exception as exc:  # filet de securite : ne jamais laisser le thread mourir en silence
                result_queue.put(("error", str(exc)))

        self.import_csv_button.configure(state="disabled")
        self.import_status_var.set("Import en cours...")
        threading.Thread(target=worker, daemon=True).start()
        self.root.after(100, self._poll_csv_import, result_queue)

    def _poll_csv_import(self, result_queue: "queue.Queue"):
        try:
            status, payload = result_queue.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_csv_import, result_queue)
            return

        self.import_csv_button.configure(state="normal")
        self.import_status_var.set("")
        # L'import s'est termine normalement (succes ou erreur geree) : le
        # fichier temoin n'a plus lieu d'etre (audit D44, voir
        # _import_transactions_csv). Best-effort, comme son ecriture.
        try:
            self._csv_import_marker_path().unlink(missing_ok=True)
        except OSError:
            pass

        if status == "error":
            messagebox.showwarning(APP_TITLE, payload)
            return

        result = payload
        self._refresh_transactions()
        self._refresh_accounts()
        self._refresh_budget()
        message = f"{result['imported']} transaction(s) importee(s)."
        if result["duplicates"]:
            # Detail ligne par ligne (compte/date/montant/beneficiaire), pas
            # seulement un compteur (audit D17) : la cle de doublon ignore
            # volontairement la categorie, donc deux depenses legitimement
            # distinctes (meme jour/compte/montant/beneficiaire, ex : deux
            # passages a la meme boulangerie) peuvent en theorie produire un
            # faux positif silencieusement ignore - ce detail permet une
            # verification manuelle rapide plutot qu'un chiffre opaque.
            message += f"\n{len(result['duplicates'])} doublon(s) ignore(s) (deja present(s)) :\n"
            message += "\n".join(
                f"  ligne {d['line']} : {d['account']} {d['date']} {bg.format_amount(d['amount'])} {d['payee']}"
                for d in result["duplicates"][:10]
            )
            if len(result["duplicates"]) > 10:
                message += f"\n  ... et {len(result['duplicates']) - 10} autre(s)."
        if result["skipped"]:
            message += f"\n{len(result['skipped'])} ligne(s) ignoree(s) :\n"
            message += "\n".join(f"  ligne {s['line']} : {s['reason']}" for s in result["skipped"][:10])
            if len(result["skipped"]) > 10:
                message += f"\n  ... et {len(result['skipped']) - 10} autre(s)."
        messagebox.showinfo(APP_TITLE, message)

    def _check_stale_csv_import_marker(self):
        """Avertit si un import CSV precedent ne s'est pas termine
        normalement (audit D44) - voir _csv_import_marker_path et son
        ecriture/suppression dans _import_transactions_csv/_poll_csv_import.
        Le fichier temoin ne survit a un lancement QUE si le processus a ete
        interrompu brutalement (coupure de courant, plantage) pendant
        l'import lui-meme : le mode WAL et les commits par lots de
        import_transactions_csv bornent deja la perte possible a moins de
        200 lignes et garantissent qu'aucune corruption du fichier de
        donnees ne survient - mais rien jusqu'ici n'informait l'utilisateur
        que cette situation s'etait produite ; il n'aurait pu le remarquer
        que par lui-meme, en recomptant ses transactions."""
        marker_path = self._csv_import_marker_path()
        if not marker_path.exists():
            return
        try:
            info = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        try:
            # Supprime immediatement, avant meme d'avertir : ne plus jamais
            # re-signaler le meme incident aux lancements suivants, meme si
            # l'utilisateur ferme la boite de dialogue sans la lire.
            marker_path.unlink(missing_ok=True)
        except OSError:
            pass
        source = info.get("source_file", "(fichier inconnu)")
        started_at = info.get("started_at", "date inconnue")
        messagebox.showwarning(
            APP_TITLE,
            "Un import CSV precedent ne semble pas s'etre termine normalement.\n\n"
            f"Fichier : {source}\nDemarre le : {started_at}\n\n"
            "Si l'application a ete interrompue brutalement (coupure de courant, "
            "plantage) pendant cet import, jusqu'a 199 lignes du fichier source "
            "ont pu ne pas etre importees. Verifiez le nombre de transactions "
            "importees et, si besoin, reimportez le meme fichier CSV : les "
            "transactions deja presentes seront automatiquement ignorees comme "
            "doublons.",
        )

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
        try:
            date_text = self._parse_iso_date(self.tx_date_var.get())
            amount = self._parse_nonzero_amount(self.tx_amount_var.get())
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
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
            new_cleared = 0 if tx["cleared"] else 1
            self.db.update_transaction(transaction_id, cleared=new_cleared)
            # Mise a jour incrementale de la seule cellule concernee (audit
            # D41) plutot qu'une reconstruction complete du Treeview (delete
            # + reinsertion de TOUTES les transactions du filtre courant a
            # chaque pointage) : pointer/depointer ne change jamais la date,
            # le compte ni le montant d'une ligne, donc jamais son rang dans
            # l'ordre d'affichage (date, id) - une simple mise a jour de
            # cellule est ici strictement equivalente a un rafraichissement
            # complet, sans reconstruire tout le tableau a chaque bascule
            # (potentiellement des dizaines de lignes lors d'un
            # rapprochement bancaire en multi-selection).
            self.transactions_tree.set(iid, "cleared", "Oui" if new_cleared else "-")
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
            removed_ids = [transaction_id, tx["transfer_id"]]
            self.db.delete_transfer_pair(transaction_id)
        else:
            if not messagebox.askyesno(APP_TITLE, "Supprimer cette transaction ?"):
                return
            removed_ids = [transaction_id]
            self.db.delete_transaction(transaction_id)
        # Suppression incrementale des seules lignes concernees (audit D41)
        # au lieu d'une reconstruction complete du Treeview : supprimer une
        # (ou deux, pour un virement) transaction ne change jamais l'ordre
        # relatif des transactions restantes - retirer uniquement la ou les
        # lignes supprimees reste donc strictement equivalent a un
        # rafraichissement complet. `exists()` protege le cas ou la jambe
        # liee d'un virement n'est pas dans le Treeview actuel (filtre par
        # compte actif sur l'AUTRE compte du virement).
        for removed_id in removed_ids:
            iid = str(removed_id)
            if self.transactions_tree.exists(iid):
                self.transactions_tree.delete(iid)
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
            except ValueError as exc:
                messagebox.showwarning(APP_TITLE, str(exc), parent=dialog)
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
        # Entree valide, Echap annule (audit D33).
        dialog.bind("<Return>", lambda e: on_save())
        dialog.bind("<Escape>", lambda e: dialog.destroy())

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
        if tx["cleared"]:
            # Trouve a l'audit : editer une transaction deja pointee
            # (rapprochee avec le releve bancaire) ne declenchait aucun
            # avertissement - le solde pointe continuait pourtant a inclure
            # cette transaction, dont le montant/la date pouvait desormais
            # ne plus correspondre a ce qui a ete verifie sur le releve,
            # sans que rien ne le signale. On avertit plutot que de bloquer
            # totalement l'edition (une correction de coquille apres coup
            # reste un besoin legitime) - l'utilisateur choisit en
            # connaissance de cause.
            if not messagebox.askyesno(
                APP_TITLE,
                "Cette transaction est pointee (rapprochee avec votre releve bancaire).\n"
                "La modifier peut fausser le solde pointe affiche.\n\n"
                "Continuer quand meme ?",
            ):
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
            try:
                date_text = self._parse_iso_date(date_var.get())
                amount = self._parse_nonzero_amount(amount_var.get())
            except ValueError as exc:
                messagebox.showwarning(APP_TITLE, str(exc))
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
        # Entree valide, Echap annule (audit D33).
        dialog.bind("<Return>", lambda e: on_save())
        dialog.bind("<Escape>", lambda e: dialog.destroy())

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
        frequency_combo = ttk.Combobox(
            form, textvariable=self.rec_frequency_var, width=14, state="readonly",
            values=list(self._RECURRING_FREQUENCY_LABELS.values()),
        )
        frequency_combo.grid(row=0, column=5, padx=5)

        ttk.Label(form, text="Beneficiaire").grid(row=1, column=0, sticky="w", pady=(5, 0))
        payee_entry = ttk.Entry(form, textvariable=self.rec_payee_var, width=25)
        payee_entry.grid(row=1, column=1, columnspan=2, sticky="we", pady=(5, 0))
        ttk.Label(form, text="Montant (negatif = depense)").grid(row=1, column=3, sticky="w", pady=(5, 0))
        amount_entry = ttk.Entry(form, textvariable=self.rec_amount_var, width=12)
        amount_entry.grid(row=1, column=4, pady=(5, 0))
        ttk.Label(form, text="Premiere echeance (AAAA-MM-JJ)").grid(row=2, column=0, sticky="w", pady=(5, 0))
        date_entry = ttk.Entry(form, textvariable=self.rec_date_var, width=12)
        date_entry.grid(row=2, column=1, sticky="w", pady=(5, 0))
        ttk.Button(form, text="Ajouter", command=self._add_recurring).grid(row=2, column=5, pady=(5, 0))
        # Entree valide le formulaire depuis n'importe lequel de ses champs
        # (audit D33).
        for field in (
            self.rec_account_combo, self.rec_category_combo, frequency_combo, payee_entry, amount_entry, date_entry,
        ):
            field.bind("<Return>", lambda e: self._add_recurring())

        columns = ("id", "next_date", "frequency", "account", "payee", "category", "amount")
        self.recurring_tree = ttk.Treeview(frame, columns=columns, show="headings", height=12)
        for col, label, width in [
            ("id", "ID", 40), ("next_date", "Prochaine echeance", 130), ("frequency", "Frequence", 100),
            ("account", "Compte", 140), ("payee", "Beneficiaire", 160),
            ("category", "Categorie", 140), ("amount", "Montant", 100),
        ]:
            self.recurring_tree.heading(col, text=label)
            self.recurring_tree.column(col, width=width, anchor="w", stretch=True)
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
        try:
            date_text = self._parse_iso_date(self.rec_date_var.get())
            amount = self._parse_nonzero_amount(self.rec_amount_var.get())
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
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

    def _archived_recurring_warning_text(self) -> str:
        """Message listant les modeles recurrents actifs cibles sur un
        compte/categorie archive(e) (voir db.list_recurring_transactions_
        targeting_archived) - ces modeles ne generent plus rien (voir
        generate_due_recurring_transactions), mais restent actifs sans
        que rien ne le signale ailleurs dans l'IHM ; ce message evite que
        l'utilisateur ne le decouvre par hasard, bien plus tard."""
        templates = self.db.list_recurring_transactions_targeting_archived()
        if not templates:
            return ""
        plural = "s" if len(templates) > 1 else ""
        lines = "\n".join(
            f"  - {t['payee'] or '(sans beneficiaire)'} ({t['account_name']}"
            f"{', ' + t['category_name'] if t['category_name'] else ''})"
            for t in templates
        )
        return (
            f"\n\n{len(templates)} modele{plural} recurrent{plural} actif{plural} cible{plural} sur un "
            f"compte ou une categorie archive(e) - plus aucune transaction n'y sera generee tant qu'il "
            f"reste archive :\n{lines}\n\nDesarchivez le compte/la categorie ou desactivez le modele pour "
            "corriger cette situation."
        )

    def _generate_recurring_now(self):
        created_ids = self.db.generate_due_recurring_transactions()
        self._refresh_recurring()
        self._refresh_transactions()
        self._refresh_accounts()
        self._refresh_budget()
        archived_warning = self._archived_recurring_warning_text()
        if created_ids:
            plural = "s" if len(created_ids) > 1 else ""
            message = f"{len(created_ids)} transaction{plural} recurrente{plural} generee{plural}."
        else:
            message = "Aucune echeance due pour le moment."
        if archived_warning:
            messagebox.showwarning(APP_TITLE, message + archived_warning)
        else:
            messagebox.showinfo(APP_TITLE, message)

    def _auto_generate_recurring(self):
        # Silencieux si rien n'est du ET qu'aucun modele n'est bloque par un
        # archivage (cas courant) : seule une generation reelle ou une
        # situation qui merite attention interrompt l'utilisateur a
        # l'ouverture.
        created_ids = self.db.generate_due_recurring_transactions()
        archived_warning = self._archived_recurring_warning_text()
        if not created_ids and not archived_warning:
            return
        self._refresh_recurring()
        self._refresh_transactions()
        self._refresh_accounts()
        self._refresh_budget()
        if created_ids:
            plural = "s" if len(created_ids) > 1 else ""
            message = f"{len(created_ids)} transaction{plural} recurrente{plural} generee{plural} automatiquement."
        else:
            message = "Aucune transaction recurrente generee."
        if archived_warning:
            messagebox.showwarning(APP_TITLE, message + archived_warning)
        else:
            messagebox.showinfo(APP_TITLE, message)

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
        self.reports_tree.column("category", width=180, anchor="w", stretch=True)
        for month in months:
            self.reports_tree.heading(month, text=bg.month_label(month))
            self.reports_tree.column(month, width=100, anchor="e", stretch=True)
        self.reports_tree.heading("total", text="Total")
        self.reports_tree.column("total", width=110, anchor="e", stretch=True)

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

        # bg.month_abbreviation (pas une troncature naive du libelle complet)
        # : audit D27, "Juin" et "Juillet" tronquaient auparavant tous deux
        # en "Jui", rendant les deux colonnes indiscernables.
        short_labels = [bg.month_abbreviation(m) for m in months]
        columns = ("category",) + tuple(months) + ("total",)
        self.annual_tree["columns"] = columns
        self.annual_tree.heading("category", text="Categorie")
        self.annual_tree.column("category", width=180, anchor="w", stretch=True)
        for month, label in zip(months, short_labels):
            self.annual_tree.heading(month, text=label)
            self.annual_tree.column(month, width=80, anchor="e", stretch=True)
        self.annual_tree.heading("total", text="Total")
        self.annual_tree.column("total", width=110, anchor="e", stretch=True)

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

        # Bouton "Restaurer..." dedie (audit D47) : jusqu'ici, restaurer une
        # sauvegarde exigeait de fermer l'application et de remplacer
        # manuellement le fichier enveloppe.sqlite a la main (toujours
        # possible, et toujours documente dans le README) - geste multi-
        # etapes sujet a erreur (mauvais fichier, mauvais dossier, oubli de
        # fermer l'app avant de copier). Ce bouton fait la meme chose depuis
        # l'IHM, sans quitter l'application : ferme proprement la connexion
        # active, copie le fichier choisi par-dessus, puis rouvre une
        # connexion et rafraichit tous les onglets - voir _restore_database.
        restore_frame = ttk.LabelFrame(frame, text="Restauration", padding=10)
        restore_frame.pack(fill=X, padx=10, pady=(0, 10))
        ttk.Label(
            restore_frame,
            text="Remplace toutes les donnees actuelles par le contenu d'un fichier de\n"
                 "sauvegarde choisi - operation irreversible, a utiliser avec precaution.",
            justify=LEFT,
        ).pack(anchor="w", pady=(0, 8))
        ttk.Button(restore_frame, text="Restaurer une sauvegarde...", command=self._restore_database).pack(anchor="w")

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
            "Pour restaurer : utilisez le bouton 'Restaurer une sauvegarde...' "
            "juste en dessous, ou fermez Enveloppe et remplacez manuellement le "
            "fichier de donnees actif par cette copie.",
        )

    def _restore_database(self):
        """Restaure une sauvegarde choisie par l'utilisateur par-dessus les
        donnees actives (audit D47), sans quitter l'application : ferme la
        connexion active, copie le fichier choisi par-dessus le fichier de
        donnees, rouvre une connexion sur ce meme chemin, puis rafraichit
        tous les onglets pour refleter les donnees restaurees."""
        import shutil
        import sqlite3
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Restaurer une sauvegarde", filetypes=[("Base SQLite", "*.sqlite"), ("Tous les fichiers", "*.*")],
        )
        if not path:
            return
        backup_path = Path(path)
        active_path = Path(self.db.path)
        try:
            if backup_path.resolve() == active_path.resolve():
                messagebox.showwarning(APP_TITLE, "Le fichier selectionne est deja le fichier de donnees actif.")
                return
        except OSError:
            pass

        # Validation minimale avant d'ecraser irreversiblement les donnees
        # actives : le fichier choisi doit au moins etre une base SQLite
        # exploitable comportant la table "accounts" - sans ce garde-fou, un
        # fichier quelconque (mauvaise extension, base d'une autre
        # application, fichier corrompu ou tronque) effacerait silencieusement
        # toutes les donnees actuelles. Ouverture en lecture seule (mode=ro)
        # pour ne jamais modifier accidentellement le fichier de sauvegarde
        # lui-meme au cours de cette simple verification.
        try:
            probe = sqlite3.connect(f"file:{backup_path.as_posix()}?mode=ro", uri=True)
            try:
                tables = {row[0] for row in probe.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                probe.close()
        except sqlite3.Error as exc:
            messagebox.showerror(APP_TITLE, f"Ce fichier n'est pas une base de donnees SQLite valide : {exc}")
            return
        if "accounts" not in tables:
            messagebox.showerror(
                APP_TITLE,
                "Ce fichier ne semble pas etre une sauvegarde Enveloppe valide "
                "(table 'accounts' introuvable).",
            )
            return

        if not messagebox.askyesno(
            APP_TITLE,
            "Restaurer cette sauvegarde va REMPLACER definitivement toutes les "
            "donnees actuelles (comptes, categories, budgets, transactions) par "
            "le contenu de ce fichier.\n\n"
            f"Fichier choisi :\n{backup_path}\n\n"
            "Cette action est irreversible (sauvegardez vos donnees actuelles au "
            "prealable si besoin). Continuer ?",
        ):
            return

        try:
            self.db.close()
            shutil.copy2(backup_path, active_path)
        except (OSError, sqlite3.Error) as exc:
            messagebox.showerror(APP_TITLE, f"Impossible de restaurer la sauvegarde : {exc}")
            self.db = Database(active_path)  # ne jamais laisser l'application sans connexion valide
            return
        self.db = Database(active_path)

        self.current_month = bg.current_month()
        self._refresh_accounts()
        self._refresh_categories()
        self._refresh_budget()
        self._refresh_transactions()
        self._refresh_recurring()
        self._refresh_reports()
        self._refresh_annual()
        messagebox.showinfo(APP_TITLE, "Sauvegarde restauree avec succes.")

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
