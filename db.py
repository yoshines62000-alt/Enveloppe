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
        """)
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

    def total_on_budget_balance(self) -> float:
        # Inclut les comptes archives : "archiver" ne fait que les masquer
        # des listes deroulantes de saisie, jamais disparaitre l'argent
        # qu'ils contiennent reellement - sinon archiver un compte non-vide
        # fausserait instantanement le "reste a assigner" (voir budget.py:
        # ready_to_assign) sans qu'aucun argent n'ait bouge.
        return round(sum(self.account_balance(a["id"]) for a in self.list_accounts(include_archived=True)), 2)

    # -- categories -------------------------------------------------------------

    def add_category(self, name: str, group_name: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO categories (name, group_name, created_at) VALUES (?, ?, ?)",
            (name.strip(), group_name.strip(), _now_iso()),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_category(self, category_id: int, **fields) -> None:
        allowed = {"name", "group_name", "archived"}
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
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        self.conn.execute(f"UPDATE transactions SET {set_clause} WHERE id = ?", (*updates.values(), transaction_id))
        self.conn.commit()

    def delete_transaction(self, transaction_id: int) -> None:
        self.conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
        self.conn.commit()

    def get_transaction(self, transaction_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()

    def list_transactions(
        self, account_id: Optional[int] = None, category_id: Optional[int] = None,
        up_to_month: Optional[str] = None,
    ) -> list:
        query = """
            SELECT transactions.*, accounts.name AS account_name, categories.name AS category_name
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
        row = self.conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE category_id = ? AND substr(date, 1, 7) <= ?",
            (category_id, month),
        ).fetchone()
        return round(row[0], 2)

    def sum_transactions_for_month(self, category_id: int, month: str) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE category_id = ? AND substr(date, 1, 7) = ?",
            (category_id, month),
        ).fetchone()
        return round(row[0], 2)
