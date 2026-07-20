"""Couche donnees d'Enveloppe (SQLite, sans dependance externe).

Toutes les dates sont stockees au format ISO (YYYY-MM-DD) et les mois au
format YYYY-MM, pour permettre des comparaisons lexicographiques directes
(pas besoin de parser pour comparer/trier chronologiquement).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_MONTH_FORMAT_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_DATE_FORMAT_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_month(month: str) -> None:
    # Le format strict YYYY-MM (zero-pad) est indispensable : toutes les
    # comparaisons de mois dans ce module sont lexicographiques (month <= ?),
    # un mois non conforme (ex : "2026-1") les corromprait silencieusement.
    if not _MONTH_FORMAT_RE.match(month):
        raise ValueError(f"Format de mois invalide : {month!r} (attendu YYYY-MM)")


def _validate_date(date_str: str) -> None:
    if not _DATE_FORMAT_RE.match(date_str):
        raise ValueError(f"Format de date invalide : {date_str!r} (attendu YYYY-MM-DD)")


class Database:
    """Enveloppe fine autour de sqlite3 : une connexion, un schema, des
    methodes CRUD explicites. Pas d'ORM."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def close(self) -> None:
        self.conn.close()

    def _create_schema(self) -> None:
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT '',
            starting_balance REAL NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            group_name TEXT NOT NULL DEFAULT '',
            archived INTEGER NOT NULL DEFAULT 0,
            savings_goal REAL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS budget_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL REFERENCES categories(id),
            month TEXT NOT NULL,
            assigned REAL NOT NULL DEFAULT 0,
            UNIQUE(category_id, month)
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            category_id INTEGER REFERENCES categories(id),
            date TEXT NOT NULL,
            payee TEXT NOT NULL DEFAULT '',
            memo TEXT NOT NULL DEFAULT '',
            amount REAL NOT NULL,
            cleared INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS transaction_splits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER NOT NULL REFERENCES transactions(id),
            category_id INTEGER NOT NULL REFERENCES categories(id),
            amount REAL NOT NULL,
            memo TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS recurring_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            category_id INTEGER REFERENCES categories(id),
            payee TEXT NOT NULL DEFAULT '',
            memo TEXT NOT NULL DEFAULT '',
            amount REAL NOT NULL,
            frequency TEXT NOT NULL,
            next_date TEXT NOT NULL,
            anchor_day INTEGER,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        """)
        self.conn.commit()
        # transfer_id a ete ajoutee apres la sortie initiale : les bases
        # SQLite existantes ne sont pas recreees par CREATE TABLE IF NOT
        # EXISTS, d'ou cette migration additive explicite (idempotente).
        self._add_column_if_missing("transactions", "transfer_id", "INTEGER REFERENCES transactions(id)")
        self._add_column_if_missing("categories", "savings_goal", "REAL")
        self._add_column_if_missing("recurring_transactions", "anchor_day", "INTEGER")

    def _add_column_if_missing(self, table: str, column: str, definition: str) -> None:
        existing = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            self.conn.commit()

    # -- comptes --------------------------------------------------------------

    def add_account(self, name: str, type_: str = "", starting_balance: float = 0.0) -> int:
        cur = self.conn.execute(
            "INSERT INTO accounts (name, type, starting_balance, created_at) VALUES (?, ?, ?, ?)",
            (name.strip(), type_.strip(), starting_balance, _now_iso()),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_account(self, account_id: int, **fields) -> None:
        allowed = {"name", "type", "starting_balance", "archived"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        self.conn.execute(f"UPDATE accounts SET {set_clause} WHERE id = ?", (*updates.values(), account_id))
        self.conn.commit()

    def get_account(self, account_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()

    def list_accounts(self, include_archived: bool = False) -> list:
        query = "SELECT * FROM accounts"
        if not include_archived:
            query += " WHERE archived = 0"
        query += " ORDER BY name COLLATE NOCASE"
        return self.conn.execute(query).fetchall()

    def account_balance(self, account_id: int) -> float:
        account = self.get_account(account_id)
        if account is None:
            return 0.0
        row = self.conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE account_id = ?", (account_id,)
        ).fetchone()
        return round(account["starting_balance"] + row[0], 2)

    def account_cleared_balance(self, account_id: int) -> float:
        """Solde pointe du compte : ne compte que les transactions marquees
        `cleared` (rapprochees avec le releve bancaire). Meme structure que
        account_balance, avec le filtre `cleared = 1` en plus - permet de
        verifier que le solde de l'application colle a la realite bancaire
        sans attendre que TOUT soit pointe."""
        account = self.get_account(account_id)
        if account is None:
            return 0.0
        row = self.conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE account_id = ? AND cleared = 1", (account_id,)
        ).fetchone()
        return round(account["starting_balance"] + row[0], 2)

    def total_on_budget_balance(self) -> float:
        # Inclut les comptes archives : "archiver" ne fait que les masquer
        # des listes deroulantes de saisie, jamais disparaitre l'argent
        # qu'ils contiennent reellement - sinon archiver un compte non-vide
        # fausserait instantanement le "reste a assigner" (voir budget.py:
        # ready_to_assign) sans qu'aucun argent n'ait bouge.
        return round(sum(self.account_balance(a["id"]) for a in self.list_accounts(include_archived=True)), 2)

    # -- categories -------------------------------------------------------------

    def add_category(self, name: str, group_name: str = "", savings_goal: Optional[float] = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO categories (name, group_name, savings_goal, created_at) VALUES (?, ?, ?, ?)",
            (name.strip(), group_name.strip(), savings_goal, _now_iso()),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_category(self, category_id: int, **fields) -> None:
        allowed = {"name", "group_name", "archived", "savings_goal"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        self.conn.execute(f"UPDATE categories SET {set_clause} WHERE id = ?", (*updates.values(), category_id))
        self.conn.commit()

    def get_category(self, category_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()

    def list_categories(self, include_archived: bool = False) -> list:
        query = "SELECT * FROM categories"
        if not include_archived:
            query += " WHERE archived = 0"
        query += " ORDER BY group_name COLLATE NOCASE, name COLLATE NOCASE"
        return self.conn.execute(query).fetchall()

    # -- budget (assignations mensuelles) -----------------------------------------

    def set_budget_entry(self, category_id: int, month: str, assigned: float) -> None:
        _validate_month(month)
        self.conn.execute(
            """INSERT INTO budget_entries (category_id, month, assigned) VALUES (?, ?, ?)
               ON CONFLICT(category_id, month) DO UPDATE SET assigned = excluded.assigned""",
            (category_id, month, assigned),
        )
        self.conn.commit()

    def get_budget_entry(self, category_id: int, month: str) -> float:
        row = self.conn.execute(
            "SELECT assigned FROM budget_entries WHERE category_id = ? AND month = ?", (category_id, month)
        ).fetchone()
        return row["assigned"] if row else 0.0

    def sum_assigned_up_to(self, category_id: int, month: str) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(assigned), 0) FROM budget_entries WHERE category_id = ? AND month <= ?",
            (category_id, month),
        ).fetchone()
        return round(row[0], 2)

    def total_assigned_all_time(self) -> float:
        row = self.conn.execute("SELECT COALESCE(SUM(assigned), 0) FROM budget_entries").fetchone()
        return round(row[0], 2)

    # -- transactions ---------------------------------------------------------------

    def add_transaction(
        self, account_id: int, date: str, amount: float,
        category_id: Optional[int] = None, payee: str = "", memo: str = "", cleared: bool = False,
    ) -> int:
        _validate_date(date)
        cur = self.conn.execute(
            """INSERT INTO transactions (account_id, category_id, date, payee, memo, amount, cleared, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, category_id, date, payee.strip(), memo.strip(), amount, int(cleared), _now_iso()),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_transaction(self, transaction_id: int, **fields) -> None:
        allowed = {"account_id", "category_id", "date", "payee", "memo", "amount", "cleared"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        if "date" in updates:
            _validate_date(updates["date"])
        should_clear_splits = "category_id" in updates
        if not should_clear_splits and "amount" in updates:
            # Un appelant peut renvoyer "amount" sans que sa valeur ait
            # reellement change (ex: le dialogue d'edition standard renvoie
            # toujours tous les champs, meme non modifies) - ne pas effacer
            # un fractionnement existant dans ce cas. Seul un changement
            # reel du montant invaliderait la somme des parts existantes
            # (qui doit toujours correspondre exactement au montant total,
            # voir set_transaction_splits).
            current = self.get_transaction(transaction_id)
            if current is not None and round(current["amount"], 2) != round(float(updates["amount"]), 2):
                should_clear_splits = True
        if should_clear_splits:
            # Assigner une categorie unique et etre fractionnee sur
            # plusieurs categories sont mutuellement exclusifs (category_id)
            # - on ne laisse jamais les deux coexister silencieusement.
            self.clear_transaction_splits(transaction_id)
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        self.conn.execute(f"UPDATE transactions SET {set_clause} WHERE id = ?", (*updates.values(), transaction_id))
        self.conn.commit()

    def delete_transaction(self, transaction_id: int) -> None:
        tx = self.get_transaction(transaction_id)
        if tx is not None and tx["transfer_id"] is not None:
            # Ne supprime que cette jambe : on delie l'autre jambe plutot
            # que de la laisser pointer vers une ligne desormais inexistante
            # (voir delete_transfer_pair pour supprimer les deux ensemble).
            self.conn.execute("UPDATE transactions SET transfer_id = NULL WHERE id = ?", (tx["transfer_id"],))
        self.conn.execute("DELETE FROM transaction_splits WHERE transaction_id = ?", (transaction_id,))
        self.conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
        self.conn.commit()

    def set_transaction_splits(self, transaction_id: int, splits: list) -> None:
        """Fractionne une transaction sur plusieurs categories. `splits` :
        liste de {"category_id", "amount", "memo"}. La somme des montants
        doit correspondre exactement (a l'arrondi pres) au montant total de
        la transaction - sinon l'argent fractionne ne balancerait plus avec
        le solde reel du compte. Remplace tout fractionnement existant, et
        met category_id de la transaction a NULL (son montant est
        desormais represente uniquement par les lignes de splits)."""
        tx = self.get_transaction(transaction_id)
        if tx is None:
            raise ValueError(f"Transaction introuvable : {transaction_id}")
        if len(splits) < 2:
            raise ValueError("Un fractionnement necessite au moins deux categories.")
        total = round(sum(s["amount"] for s in splits), 2)
        if abs(total - round(tx["amount"], 2)) > 0.01:
            raise ValueError(
                f"La somme des parts ({total:.2f}) ne correspond pas au montant de la transaction ({tx['amount']:.2f})."
            )
        self.conn.execute("DELETE FROM transaction_splits WHERE transaction_id = ?", (transaction_id,))
        for split in splits:
            self.conn.execute(
                "INSERT INTO transaction_splits (transaction_id, category_id, amount, memo) VALUES (?, ?, ?, ?)",
                (transaction_id, split["category_id"], split["amount"], split.get("memo", "").strip()),
            )
        self.conn.execute("UPDATE transactions SET category_id = NULL WHERE id = ?", (transaction_id,))
        self.conn.commit()

    def get_transaction_splits(self, transaction_id: int) -> list:
        return self.conn.execute(
            """SELECT transaction_splits.*, categories.name AS category_name
               FROM transaction_splits
               JOIN categories ON categories.id = transaction_splits.category_id
               WHERE transaction_id = ?
               ORDER BY transaction_splits.id""",
            (transaction_id,),
        ).fetchall()

    def clear_transaction_splits(self, transaction_id: int) -> None:
        self.conn.execute("DELETE FROM transaction_splits WHERE transaction_id = ?", (transaction_id,))
        self.conn.commit()

    def get_transaction(self, transaction_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()

    def add_transfer(self, from_account_id: int, to_account_id: int, date: str, amount: float, memo: str = "") -> tuple:
        """Cree un virement entre deux comptes sous la forme de deux
        transactions liees (transfer_id reciproque) : une sortie (montant
        negatif) sur le compte source, une entree (montant positif) sur le
        compte destination. Aucune des deux n'est rattachee a une
        categorie - un virement entre ses propres comptes ne doit jamais
        affecter le budget a enveloppes, seulement les soldes des comptes.
        Renvoie (id_transaction_sortie, id_transaction_entree)."""
        if from_account_id == to_account_id:
            raise ValueError("Le compte source et le compte destination doivent etre differents.")
        amount = abs(amount)
        if amount == 0:
            raise ValueError("Le montant du virement ne peut pas etre nul.")
        from_account = self.get_account(from_account_id)
        to_account = self.get_account(to_account_id)
        out_id = self.add_transaction(
            from_account_id, date, -amount, payee=f"Virement vers {to_account['name']}", memo=memo,
        )
        in_id = self.add_transaction(
            to_account_id, date, amount, payee=f"Virement depuis {from_account['name']}", memo=memo,
        )
        self.conn.execute("UPDATE transactions SET transfer_id = ? WHERE id = ?", (in_id, out_id))
        self.conn.execute("UPDATE transactions SET transfer_id = ? WHERE id = ?", (out_id, in_id))
        self.conn.commit()
        return out_id, in_id

    def delete_transfer_pair(self, transaction_id: int) -> None:
        """Supprime une transaction et, si elle fait partie d'un virement,
        sa jambe liee egalement. Si ce n'est pas un virement, se comporte
        comme delete_transaction."""
        tx = self.get_transaction(transaction_id)
        if tx is None:
            return
        partner_id = tx["transfer_id"]
        self.delete_transaction(transaction_id)
        if partner_id is not None:
            self.delete_transaction(partner_id)

    # -- transactions recurrentes -----------------------------------------

    _RECURRING_FREQUENCIES = ("weekly", "monthly", "yearly")

    def add_recurring_transaction(
        self, account_id: int, date: str, amount: float, frequency: str,
        category_id: Optional[int] = None, payee: str = "", memo: str = "",
    ) -> int:
        _validate_date(date)
        if frequency not in self._RECURRING_FREQUENCIES:
            raise ValueError(f"Frequence invalide : {frequency!r}")
        # anchor_day fige le jour du mois VOULU (ex: 31), independamment de
        # ce que next_date devient au fil des avancements - voir
        # _advance_date : sans lui, une premiere echeance tombant sur un
        # mois court (ex: 31 janvier -> 28 fevrier) ferait glisser TOUTES
        # les echeances suivantes sur le 28, y compris dans un mois qui
        # compte pourtant 31 jours (bug trouve a l'audit).
        from datetime import date as _date_cls
        anchor_day = _date_cls.fromisoformat(date).day
        cur = self.conn.execute(
            """INSERT INTO recurring_transactions
               (account_id, category_id, payee, memo, amount, frequency, next_date, anchor_day, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, category_id, payee.strip(), memo.strip(), amount, frequency, date, anchor_day, _now_iso()),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_recurring_transactions(self, include_inactive: bool = False) -> list:
        query = """
            SELECT recurring_transactions.*, accounts.name AS account_name, categories.name AS category_name
            FROM recurring_transactions
            JOIN accounts ON accounts.id = recurring_transactions.account_id
            LEFT JOIN categories ON categories.id = recurring_transactions.category_id
        """
        if not include_inactive:
            query += " WHERE recurring_transactions.active = 1"
        query += " ORDER BY recurring_transactions.next_date"
        return self.conn.execute(query).fetchall()

    def update_recurring_transaction(self, recurring_id: int, **fields) -> None:
        allowed = {
            "account_id", "category_id", "payee", "memo", "amount", "frequency",
            "next_date", "anchor_day", "active",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        if "frequency" in updates and updates["frequency"] not in self._RECURRING_FREQUENCIES:
            raise ValueError(f"Frequence invalide : {updates['frequency']!r}")
        if "next_date" in updates:
            _validate_date(updates["next_date"])
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        self.conn.execute(
            f"UPDATE recurring_transactions SET {set_clause} WHERE id = ?", (*updates.values(), recurring_id)
        )
        self.conn.commit()

    def delete_recurring_transaction(self, recurring_id: int) -> None:
        self.conn.execute("DELETE FROM recurring_transactions WHERE id = ?", (recurring_id,))
        self.conn.commit()

    @staticmethod
    def _advance_date(date_str: str, frequency: str, anchor_day: Optional[int] = None) -> str:
        """Calcule la prochaine echeance apres `date_str` pour une frequence
        donnee. Le mensuel/annuel ramene toujours le jour au dernier jour du
        mois cible s'il deborde (ex: 31 janvier + mensuel -> 28/29 fevrier,
        jamais une ValueError ni un glissement silencieux vers mars).

        `anchor_day` (le jour du mois VOULU, ex: 31) est le jour cible a
        chaque avancement - PAS `current.day` (le jour du dernier
        `date_str` calcule). Sans cette distinction, un premier
        rapprochement force par un mois court (31 janvier -> 28 fevrier)
        ferait deriver TOUTES les echeances suivantes sur le 28,
        indefiniment, meme un mois qui compte 31 jours (bug trouve a
        l'audit). Si `anchor_day` est omis (compatibilite/tests directs de
        cette fonction), on retombe sur `current.day` comme avant."""
        import calendar
        from datetime import date as _date, timedelta as _timedelta

        current = _date.fromisoformat(date_str)
        if frequency == "weekly":
            return (current + _timedelta(days=7)).isoformat()
        if frequency == "monthly":
            month = current.month + 1
            year = current.year + (1 if month > 12 else 0)
            month = month if month <= 12 else 1
        elif frequency == "yearly":
            month = current.month
            year = current.year + 1
        else:
            raise ValueError(f"Frequence invalide : {frequency!r}")
        last_day = calendar.monthrange(year, month)[1]
        day = min(anchor_day if anchor_day is not None else current.day, last_day)
        return _date(year, month, day).isoformat()

    def generate_due_recurring_transactions(self, as_of: Optional[str] = None) -> list:
        """Cree une vraie transaction pour chaque echeance passee (ou du
        jour) de chaque modele actif, et avance next_date jusqu'a depasser
        `as_of` - en rattrapant plusieurs occurrences manquees d'un coup si
        l'application n'a pas ete ouverte depuis un moment (ex: 2 loyers
        mensuels manques generent bien 2 transactions, pas une seule).
        Renvoie la liste des ids de transactions creees."""
        if as_of is None:
            # date.today() (heure LOCALE), pas datetime.now(timezone.utc) :
            # toutes les autres dates "du jour" de l'application (mois
            # budgetaire courant via budget.current_month(), date par
            # defaut d'une nouvelle transaction) utilisent deja l'heure
            # locale de l'utilisateur - comparer ici a la date UTC aurait pu
            # generer une echeance jusqu'a un jour trop tot ou trop tard
            # selon le fuseau horaire (bug trouve a l'audit).
            from datetime import date as _date_cls
            as_of = _date_cls.today().isoformat()
        created_ids = []
        for template in self.list_recurring_transactions():
            next_date = template["next_date"]
            while next_date <= as_of:
                new_id = self.add_transaction(
                    template["account_id"], next_date, template["amount"],
                    category_id=template["category_id"], payee=template["payee"], memo=template["memo"],
                )
                created_ids.append(new_id)
                next_date = self._advance_date(next_date, template["frequency"], template["anchor_day"])
            if next_date != template["next_date"]:
                self.update_recurring_transaction(template["id"], next_date=next_date)
        return created_ids

    def list_transactions(
        self, account_id: Optional[int] = None, category_id: Optional[int] = None,
        up_to_month: Optional[str] = None,
    ) -> list:
        query = """
            SELECT transactions.*, accounts.name AS account_name, categories.name AS category_name,
                   (SELECT COUNT(*) FROM transaction_splits WHERE transaction_splits.transaction_id = transactions.id)
                       AS split_count
            FROM transactions
            JOIN accounts ON accounts.id = transactions.account_id
            LEFT JOIN categories ON categories.id = transactions.category_id
            WHERE 1=1
        """
        params = []
        if account_id is not None:
            query += " AND transactions.account_id = ?"
            params.append(account_id)
        if category_id is not None:
            query += " AND transactions.category_id = ?"
            params.append(category_id)
        if up_to_month is not None:
            query += " AND substr(transactions.date, 1, 7) <= ?"
            params.append(up_to_month)
        query += " ORDER BY transactions.date, transactions.id"
        return self.conn.execute(query, params).fetchall()

    def sum_transactions_up_to(self, category_id: int, month: str) -> float:
        # Une transaction fractionnee n'a plus de category_id propre (voir
        # set_transaction_splits) : sa contribution a chaque enveloppe
        # passe entierement par transaction_splits, d'ou l'UNION ci-dessous
        # plutot qu'une simple somme sur transactions.category_id.
        row = self.conn.execute(
            """SELECT COALESCE(SUM(amount), 0) FROM (
                SELECT amount FROM transactions WHERE category_id = ? AND substr(date, 1, 7) <= ?
                UNION ALL
                SELECT transaction_splits.amount FROM transaction_splits
                JOIN transactions ON transactions.id = transaction_splits.transaction_id
                WHERE transaction_splits.category_id = ? AND substr(transactions.date, 1, 7) <= ?
            )""",
            (category_id, month, category_id, month),
        ).fetchone()
        return round(row[0], 2)

    def sum_transactions_for_month(self, category_id: int, month: str) -> float:
        row = self.conn.execute(
            """SELECT COALESCE(SUM(amount), 0) FROM (
                SELECT amount FROM transactions WHERE category_id = ? AND substr(date, 1, 7) = ?
                UNION ALL
                SELECT transaction_splits.amount FROM transaction_splits
                JOIN transactions ON transactions.id = transaction_splits.transaction_id
                WHERE transaction_splits.category_id = ? AND substr(transactions.date, 1, 7) = ?
            )""",
            (category_id, month, category_id, month),
        ).fetchone()
        return round(row[0], 2)
