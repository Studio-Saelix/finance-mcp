"""Local SQLite persistence for access tokens, account cache, and synced transactions."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .crypto import CredentialError, decrypt, encrypt, load_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    item_id           TEXT PRIMARY KEY,
    access_token_nonce BLOB NOT NULL,
    access_token_ciphertext BLOB NOT NULL,
    institution_id    TEXT,
    institution_name  TEXT,
    products          TEXT,          -- JSON array
    created_at        TEXT NOT NULL,
    last_error        TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id     TEXT PRIMARY KEY,
    item_id        TEXT NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
    name           TEXT,
    official_name  TEXT,
    type           TEXT,
    subtype        TEXT,
    mask           TEXT,
    iso_currency   TEXT,
    updated_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_accounts_item ON accounts(item_id);

CREATE TABLE IF NOT EXISTS sync_cursors (
    item_id     TEXT PRIMARY KEY REFERENCES items(item_id) ON DELETE CASCADE,
    cursor      TEXT,
    last_sync   TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id  TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    amount          REAL,
    iso_currency    TEXT,
    date            TEXT,
    authorized_date TEXT,
    name            TEXT,
    merchant_name   TEXT,
    category        TEXT,
    subcategory     TEXT,
    pending         INTEGER,
    payment_channel TEXT,
    raw             TEXT
);
CREATE INDEX IF NOT EXISTS idx_tx_account ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_merchant ON transactions(merchant_name);
CREATE INDEX IF NOT EXISTS idx_tx_category ON transactions(category);

CREATE TABLE IF NOT EXISTS link_sessions (
    link_token_hash TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    status        TEXT NOT NULL,     -- pending | completed | expired
    hosted_url    TEXT,
    item_id       TEXT
);

CREATE TABLE IF NOT EXISTS secrets (
    name          TEXT PRIMARY KEY,
    nonce         BLOB NOT NULL,
    ciphertext    BLOB NOT NULL,
    updated_at    TEXT NOT NULL
);

-- User-supplied corrections to APR / promo data that Plaid doesn't surface
-- reliably (common: Citi cards often ship purchase_apr but omit special_apr
-- for a 0% promo). These overrides take precedence in debt analysis.
CREATE TABLE IF NOT EXISTS account_overrides (
    account_id      TEXT PRIMARY KEY,
    effective_apr   REAL,
    promo_expires   TEXT,
    note            TEXT,
    updated_at      TEXT
);

-- Debts the user carries that aren't behind any linked Plaid item — BNPL,
-- personal loans at non-linkable lenders, medical, etc. Modeled loosely
-- like credit cards so summarize_debt can rank them alongside real items.
CREATE TABLE IF NOT EXISTS external_debts (
    debt_id                TEXT PRIMARY KEY,
    name                   TEXT NOT NULL,
    balance                REAL NOT NULL,
    apr                    REAL NOT NULL,
    minimum_payment        REAL DEFAULT 0,
    next_payment_due_date  TEXT,
    promo_expires          TEXT,
    note                   TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    """SQLite-backed store.

    FastMCP dispatches tool handlers from a thread pool, so the underlying
    connection is opened with ``check_same_thread=False``. All writes go
    through a lock to serialize access.
    """

    def __init__(self, db_path: Path, key_path: Path | None = None, *, create_key: bool = True):
        import threading

        self.db_path = db_path
        self.key_path = key_path or db_path.parent / "master.key"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.db_path.parent.is_symlink():
            raise CredentialError("Database directory must not be a symlink")
        os.chmod(self.db_path.parent, 0o700)
        self.key = load_key(self.key_path, create=create_key)
        self._conn = sqlite3.connect(
            str(db_path), isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._lock = threading.RLock()
        self._conn.executescript(SCHEMA)
        columns = {r[1] for r in self._conn.execute("PRAGMA table_info(items)")}
        if "access_token" in columns:
            raise CredentialError(
                "Legacy plaintext credentials detected; reset and relink explicitly"
            )
        try:
            os.chmod(db_path, 0o600)
        except OSError:
            pass  # Windows or other fs without chmod semantics
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{db_path}{suffix}")
            if sidecar.exists():
                os.chmod(sidecar, 0o600)

    # ---- item / token management ------------------------------------------------

    def save_item(
        self,
        item_id: str,
        access_token: str,
        institution_id: str | None,
        institution_name: str | None,
        products: list[str] | None,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO items
               (item_id, access_token_nonce, access_token_ciphertext, institution_id,
                institution_name, products, created_at)
               VALUES (?, ?, ?, ?, ?, ?, COALESCE(
                   (SELECT created_at FROM items WHERE item_id = ?), ?
               ))""",
            (item_id,
                *encrypt(self.key, access_token, purpose=f"item:{item_id}"),
                institution_id,
                institution_name,
                json.dumps(products or []),
                item_id,
                _utcnow(),
            ),
        )

    def get_runtime_token(self, item_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT access_token_nonce, access_token_ciphertext FROM items "
            "WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        return decrypt(self.key, row[0], row[1], purpose=f"item:{item_id}") if row else None

    def save_secret(self, name: str, value: str) -> None:
        nonce, ciphertext = encrypt(self.key, value, purpose=f"secret:{name}")
        self._conn.execute(
            "INSERT OR REPLACE INTO secrets(name, nonce, ciphertext, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (name, nonce, ciphertext, _utcnow()),
        )

    def get_secret(self, name: str) -> str | None:
        row = self._conn.execute(
            "SELECT nonce, ciphertext FROM secrets WHERE name = ?", (name,)
        ).fetchone()
        return decrypt(self.key, row[0], row[1], purpose=f"secret:{name}") if row else None

    def list_items(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT item_id, institution_id, institution_name, products, created_at, last_error "
            "FROM items ORDER BY created_at"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["products"] = json.loads(d["products"] or "[]")
            out.append(d)
        return out

    def delete_item(self, item_id: str) -> None:
        self._conn.execute("DELETE FROM items WHERE item_id = ?", (item_id,))

    def set_item_error(self, item_id: str, error: str | None) -> None:
        self._conn.execute(
            "UPDATE items SET last_error = ? WHERE item_id = ?", (error, item_id)
        )

    # ---- account cache ----------------------------------------------------------

    def upsert_account(self, item_id: str, account: dict[str, Any]) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO accounts
               (account_id, item_id, name, official_name, type, subtype, mask,
                iso_currency, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                account.get("account_id"),
                item_id,
                account.get("name"),
                account.get("official_name"),
                account.get("type"),
                account.get("subtype"),
                account.get("mask"),
                account.get("iso_currency"),
                _utcnow(),
            ),
        )

    def list_accounts(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT a.*, i.institution_name
               FROM accounts a JOIN items i ON a.item_id = i.item_id
               ORDER BY i.institution_name, a.name"""
        ).fetchall()
        return [dict(r) for r in rows]

    def count_accounts(self, item_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS count FROM accounts WHERE item_id = ?", (item_id,)
        ).fetchone()
        return int(row["count"])

    def get_account_item(self, account_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT item_id FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        return row["item_id"] if row else None

    # ---- transaction sync -------------------------------------------------------

    def get_cursor(self, item_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT cursor FROM sync_cursors WHERE item_id = ?", (item_id,)
        ).fetchone()
        return row["cursor"] if row and row["cursor"] else None

    def set_cursor(self, item_id: str, cursor: str) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO sync_cursors (item_id, cursor, last_sync)
               VALUES (?, ?, ?)""",
            (item_id, cursor, _utcnow()),
        )

    def upsert_transaction(self, item_id: str, tx: dict[str, Any]) -> None:
        pfc = tx.get("personal_finance_category") or {}
        self._conn.execute(
            """INSERT OR REPLACE INTO transactions
               (transaction_id, account_id, item_id, amount, iso_currency,
                date, authorized_date, name, merchant_name, category, subcategory,
                pending, payment_channel, raw)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tx.get("transaction_id"),
                tx.get("account_id"),
                item_id,
                tx.get("amount"),
                tx.get("iso_currency_code"),
                str(tx.get("date")) if tx.get("date") else None,
                str(tx.get("authorized_date")) if tx.get("authorized_date") else None,
                tx.get("name"),
                tx.get("merchant_name"),
                pfc.get("primary"),
                pfc.get("detailed"),
                1 if tx.get("pending") else 0,
                tx.get("payment_channel"),
                json.dumps(tx, default=str),
            ),
        )

    def delete_transaction(self, transaction_id: str) -> None:
        self._conn.execute(
            "DELETE FROM transactions WHERE transaction_id = ?", (transaction_id,)
        )

    def query_transactions(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        account_id: str | None = None,
        category: str | None = None,
        merchant: str | None = None,
        min_amount: float | None = None,
        max_amount: float | None = None,
        text: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if start_date:
            clauses.append("date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("date <= ?")
            params.append(end_date)
        if account_id:
            clauses.append("account_id = ?")
            params.append(account_id)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if merchant:
            clauses.append("merchant_name LIKE ?")
            params.append(f"%{merchant}%")
        if min_amount is not None:
            clauses.append("amount >= ?")
            params.append(min_amount)
        if max_amount is not None:
            clauses.append("amount <= ?")
            params.append(max_amount)
        if text:
            clauses.append("(name LIKE ? OR merchant_name LIKE ?)")
            params.extend([f"%{text}%", f"%{text}%"])

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT transaction_id, account_id, amount, iso_currency, date, "
            "authorized_date, name, merchant_name, category, subcategory, "
            "pending, payment_channel, raw "
            f"FROM transactions {where} "
            "ORDER BY date DESC, transaction_id LIMIT ?"
        )
        params.append(limit)
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def aggregate_transactions(
        self,
        start_date: str,
        end_date: str,
        group_by: str = "category",
    ) -> list[dict[str, Any]]:
        col_map = {
            "category": "category",
            "subcategory": "subcategory",
            "merchant": "merchant_name",
            "account": "account_id",
        }
        if group_by not in col_map:
            raise ValueError(f"group_by must be one of {list(col_map)}")
        col = col_map[group_by]
        rows = self._conn.execute(
            f"""SELECT COALESCE({col}, '(uncategorized)') AS grp,
                       COUNT(*) AS count,
                       ROUND(SUM(amount), 2) AS total
                FROM transactions
                WHERE date BETWEEN ? AND ? AND pending = 0
                GROUP BY grp
                ORDER BY total DESC""",
            (start_date, end_date),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- link sessions ----------------------------------------------------------

    def save_link_session(self, link_token: str, hosted_url: str | None) -> None:
        token_hash = hashlib.sha256(link_token.encode()).hexdigest()
        self._conn.execute(
            """INSERT OR REPLACE INTO link_sessions
               (link_token_hash, created_at, status, hosted_url)
               VALUES (?, ?, 'pending', ?)""",
            (token_hash, _utcnow(), hosted_url),
        )

    def complete_link_session(
        self, link_token: str, public_token: str, item_id: str
    ) -> None:
        token_hash = hashlib.sha256(link_token.encode()).hexdigest()
        self._conn.execute(
            """UPDATE link_sessions
               SET status = 'completed', item_id = ?
               WHERE link_token_hash = ?""",
            (item_id, token_hash),
        )

    def get_link_session(self, link_token: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM link_sessions WHERE link_token_hash = ?",
            (hashlib.sha256(link_token.encode()).hexdigest(),),
        ).fetchone()
        return dict(row) if row else None

    # ---- account overrides ------------------------------------------------------

    def save_account_override(
        self,
        account_id: str,
        effective_apr: float | None = None,
        promo_expires: str | None = None,
        note: str | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO account_overrides
                   (account_id, effective_apr, promo_expires, note, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(account_id) DO UPDATE SET
                   effective_apr =
                       COALESCE(excluded.effective_apr, account_overrides.effective_apr),
                   promo_expires =
                       COALESCE(excluded.promo_expires, account_overrides.promo_expires),
                   note          = COALESCE(excluded.note, account_overrides.note),
                   updated_at    = excluded.updated_at""",
            (account_id, effective_apr, promo_expires, note, _utcnow()),
        )

    def clear_account_override(self, account_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM account_overrides WHERE account_id = ?", (account_id,)
        )
        return cur.rowcount > 0

    def get_account_override(self, account_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM account_overrides WHERE account_id = ?", (account_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_account_overrides(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM account_overrides ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- external debts ---------------------------------------------------------

    def add_external_debt(
        self,
        debt_id: str,
        name: str,
        balance: float,
        apr: float,
        minimum_payment: float = 0.0,
        next_payment_due_date: str | None = None,
        promo_expires: str | None = None,
        note: str | None = None,
    ) -> None:
        now = _utcnow()
        self._conn.execute(
            """INSERT INTO external_debts
               (debt_id, name, balance, apr, minimum_payment,
                next_payment_due_date, promo_expires, note, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                debt_id,
                name,
                balance,
                apr,
                minimum_payment,
                next_payment_due_date,
                promo_expires,
                note,
                now,
                now,
            ),
        )

    def update_external_debt(self, debt_id: str, **fields: Any) -> bool:
        allowed = {
            "name",
            "balance",
            "apr",
            "minimum_payment",
            "next_payment_due_date",
            "promo_expires",
            "note",
        }
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [_utcnow(), debt_id]
        cur = self._conn.execute(
            f"UPDATE external_debts SET {set_clause}, updated_at = ? WHERE debt_id = ?",
            params,
        )
        return cur.rowcount > 0

    def remove_external_debt(self, debt_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM external_debts WHERE debt_id = ?", (debt_id,)
        )
        return cur.rowcount > 0

    def list_external_debts(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM external_debts ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- lifecycle --------------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            self._conn.execute("BEGIN")
            yield self._conn
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self._conn.close()
