"""Plaid provider — wraps the existing Plaid SDK helpers behind the Provider Protocol.

The runtime adapter contains only read operations. Plaid Link and Item
lifecycle operations remain in the separate administrator-facing modules.

The Plaid SDK enforces a persistent access_token per item, and items are already
the natural enrollment boundary in our SQLite schema — so ``Enrollment.id`` is
simply the ``item_id``. Accounts, balances, and transactions are derived off
that binding.

Sign convention:
  Plaid reports ``amount`` as positive when money leaves a depository account
  (i.e. a charge or spend). Our normalized ``Transaction.amount`` follows the
  same convention, so we pass the value through unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest

from .. import client as client_mod
from ..config import Config
from ..storage import Storage
from .base import (
    Account,
    Balance,
    Capability,
    Enrollment,
    Transaction,
)


class PlaidError(RuntimeError):
    """Raised when a Plaid operation can't be completed (e.g. link not ready)."""


class PlaidProvider:
    """Provider impl against Plaid. Thin wrapper over existing helpers."""

    name = "plaid"

    def __init__(self, storage: Storage, config: Config):
        self.storage = storage
        self.config = config

    # ---- capabilities ---------------------------------------------------

    def capabilities(self) -> set[Capability]:
        return {
            Capability.ACCOUNTS,
            Capability.BALANCES,
            Capability.TRANSACTIONS,
            Capability.INVESTMENTS,
            Capability.LIABILITIES,
        }

    # ---- reads ----------------------------------------------------------

    def list_accounts(self, enrollment: Enrollment) -> list[Account]:
        """Read from the local account cache (populated at link time)."""
        rows = self.storage.list_accounts()
        return [
            _to_account(r)
            for r in rows
            if r.get("item_id") == enrollment.id
        ]

    def get_balances(self, enrollment: Enrollment) -> list[Balance]:
        """Live Plaid /accounts/balance/get for just this enrollment."""
        client = client_mod.get_client()
        resp = client.accounts_balance_get(
            AccountsBalanceGetRequest(access_token=enrollment.access_token)
        )
        return [_to_balance(a) for a in resp.get("accounts", [])]

    def get_transactions(
        self,
        enrollment: Enrollment,
        start_date: str,
        end_date: str,
        account_id: str | None = None,
    ) -> list[Transaction]:
        """Reads from the locally-cached synced transactions.

        Callers are expected to run ``sync_transactions`` ahead of time. Rows
        are filtered to this enrollment by walking accounts that belong to the
        item, since ``query_transactions`` doesn't filter by item_id directly.
        """
        own_account_ids = {
            r["account_id"]
            for r in self.storage.list_accounts()
            if r.get("item_id") == enrollment.id
        }
        if account_id is not None:
            if account_id not in own_account_ids:
                return []
            rows = self.storage.query_transactions(
                start_date=start_date,
                end_date=end_date,
                account_id=account_id,
            )
        else:
            rows = self.storage.query_transactions(
                start_date=start_date,
                end_date=end_date,
            )
            rows = [r for r in rows if r.get("account_id") in own_account_ids]
        return [_to_transaction(r) for r in rows]

# ---- normalizers -------------------------------------------------------


def _to_account(row: dict[str, Any]) -> Account:
    return Account(
        id=row["account_id"],
        enrollment_id=row["item_id"],
        name=row.get("name"),
        official_name=row.get("official_name"),
        type=row.get("type"),
        subtype=row.get("subtype"),
        mask=row.get("mask"),
        iso_currency=row.get("iso_currency"),
    )

def _to_balance(acct: dict[str, Any]) -> Balance:
    balances = acct.get("balances") or {}
    return Balance(
        account_id=acct["account_id"],
        current=balances.get("current"),
        available=balances.get("available"),
        limit=balances.get("limit"),
        iso_currency=balances.get("iso_currency_code"),
    )


def _to_transaction(row: dict[str, Any]) -> Transaction:
    raw_blob = row.get("raw")
    parsed_raw: dict[str, Any] = {}
    if isinstance(raw_blob, str) and raw_blob:
        try:
            loaded = json.loads(raw_blob)
            if isinstance(loaded, dict):
                parsed_raw = loaded
        except json.JSONDecodeError:
            parsed_raw = {}

    return Transaction(
        id=row["transaction_id"],
        account_id=row["account_id"],
        amount=float(row.get("amount") or 0.0),
        iso_currency=row.get("iso_currency"),
        date=row.get("date") or "",
        authorized_date=row.get("authorized_date"),
        name=row.get("name"),
        merchant_name=row.get("merchant_name"),
        category=row.get("category"),
        subcategory=row.get("subcategory"),
        pending=bool(row.get("pending")),
        payment_channel=row.get("payment_channel"),
        raw=parsed_raw,
    )
