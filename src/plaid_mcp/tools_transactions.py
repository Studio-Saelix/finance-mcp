"""Accounts + balances + transactions tools."""

from __future__ import annotations

import time
from typing import Any

from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
from plaid.model.transactions_sync_request import TransactionsSyncRequest

from .client import get_client
from .errors import safe_provider_error
from .storage import Storage

# Plaid's /transactions/sync surfaces this on the first call after an item is linked:
# the historical transaction pull runs asynchronously. We gate the real fetch until
# it reaches HISTORICAL_UPDATE_COMPLETE, otherwise the sync will legitimately return
# zero transactions and callers think linking was broken.
_READY_STATUS = "HISTORICAL_UPDATE_COMPLETE"


def list_linked_institutions(storage: Storage) -> list[dict[str, Any]]:
    """Return every Plaid item currently linked, with account count."""
    items = storage.list_items()
    accounts = storage.list_accounts()
    counts: dict[str, int] = {}
    for a in accounts:
        counts[a["item_id"]] = counts.get(a["item_id"], 0) + 1
    for i in items:
        i["account_count"] = counts.get(i["item_id"], 0)
    return items


def remove_institution(
    storage: Storage, item_id: str, *, force_local_purge: bool = False
) -> dict[str, Any]:
    """Unlink upstream first; preserve local state when removal fails."""
    from plaid.model.item_remove_request import ItemRemoveRequest

    from .errors import safe_provider_error

    access_token = storage.get_runtime_token(item_id)
    if not access_token:
        return {"status": "not_found", "item_id": item_id}

    try:
        get_client().item_remove(ItemRemoveRequest(access_token=access_token))
    except Exception as e:  # noqa: BLE001
        if force_local_purge:
            storage.purge_item_state(item_id)
            return {"status": "locally_purged", "item_id": item_id,
                    "warning": safe_provider_error(e)}
        return {"status": "upstream_failed", "item_id": item_id,
                "error": safe_provider_error(e), "retryable": True}

    storage.purge_item_state(item_id)
    return {"status": "removed", "item_id": item_id}


def list_accounts(storage: Storage) -> list[dict[str, Any]]:
    """All accounts across every linked institution, from the local cache."""
    return storage.list_accounts()


def get_balances(storage: Storage, account_id: str | None = None) -> list[dict[str, Any]]:
    """Live balance lookup. Hits Plaid directly — not cached."""
    client = get_client()
    items = storage.list_items()

    out: list[dict[str, Any]] = []
    for item in items:
        access_token = storage.get_runtime_token(item["item_id"])
        if not access_token:
            continue
        try:
            resp = client.accounts_balance_get(
                AccountsBalanceGetRequest(access_token=access_token)
            )
        except Exception as e:  # noqa: BLE001
            storage.set_item_error(item["item_id"], safe_provider_error(e))
            continue

        for acct in resp["accounts"]:
            if account_id and acct["account_id"] != account_id:
                continue
            balances = acct.get("balances") or {}
            out.append(
                {
                    "account_id": acct["account_id"],
                    "institution_name": item.get("institution_name"),
                    "name": acct.get("name"),
                    "mask": acct.get("mask"),
                    "type": str(acct.get("type")) if acct.get("type") else None,
                    "subtype": str(acct.get("subtype")) if acct.get("subtype") else None,
                    "current": balances.get("current"),
                    "available": balances.get("available"),
                    "limit": balances.get("limit"),
                    "iso_currency": balances.get("iso_currency_code"),
                }
            )
    return out


def _sync_one_page(client: Any, access_token: str, cursor: str | None) -> dict[str, Any]:
    req = TransactionsSyncRequest(access_token=access_token)
    if cursor:
        req.cursor = cursor
    return client.transactions_sync(req)


def sync_transactions(
    storage: Storage,
    wait_for_ready: bool = True,
    wait_timeout_s: int = 60,
) -> dict[str, Any]:
    """Pull latest transactions from every linked item using /transactions/sync.

    Plaid's historical transaction pull is asynchronous after linking; the
    `/transactions/sync` response carries a ``transactions_update_status`` field.
    If ``wait_for_ready`` is True (default) and that status isn't
    ``HISTORICAL_UPDATE_COMPLETE``, this tool polls with backoff up to
    ``wait_timeout_s`` seconds before giving up. When the wait expires we still
    return what Plaid has so far, and surface ``status`` in the per-item result.

    Returns per-item stats on added/modified/removed transactions.
    """
    client = get_client()
    results: list[dict[str, Any]] = []

    for item in storage.list_items():
        item_id = item["item_id"]
        access_token = storage.get_runtime_token(item_id)
        if not access_token:
            continue

        added = modified = removed = 0
        cursor = storage.get_cursor(item_id)
        status: str | None = None
        probe_resp: dict[str, Any] | None = None
        error: str | None = None

        # If we've never synced this item, block briefly until Plaid's historical
        # pull is ready — otherwise the first call returns zero added.
        if wait_for_ready and cursor is None:
            deadline = time.time() + wait_timeout_s
            delay = 2.0
            while True:
                try:
                    probe_resp = _sync_one_page(client, access_token, None)
                except Exception as e:  # noqa: BLE001
                    error = safe_provider_error(e)
                    probe_resp = None
                    break
                status = probe_resp.get("transactions_update_status")
                # Proceed if Plaid says we're ready, if it didn't surface a status
                # at all (older responses / some products), or if it already has
                # data or a next page queued up.
                if (
                    status is None
                    or status == _READY_STATUS
                    or probe_resp.get("added")
                    or probe_resp.get("has_more")
                ):
                    break
                if time.time() >= deadline:
                    break
                time.sleep(delay)
                delay = min(delay * 1.5, 8.0)

        if error:
            storage.set_item_error(item_id, error)
            results.append(
                {
                    "item_id": item_id,
                    "institution_name": item.get("institution_name"),
                    "error": error,
                    "status": status,
                }
            )
            continue

        has_more = True
        fetch_error: str | None = None
        while has_more:
            # Reuse the probe's response for the first iteration so we don't
            # double-call Plaid on the initial sync.
            if probe_resp is not None:
                resp = probe_resp
                probe_resp = None
            else:
                try:
                    resp = _sync_one_page(client, access_token, cursor)
                except Exception as e:  # noqa: BLE001
                    fetch_error = safe_provider_error(e)
                    storage.set_item_error(item_id, fetch_error)
                    break

            status = resp.get("transactions_update_status") or status

            for tx in resp.get("added", []):
                storage.upsert_transaction(item_id, tx.to_dict())
                added += 1
            for tx in resp.get("modified", []):
                storage.upsert_transaction(item_id, tx.to_dict())
                modified += 1
            for tx in resp.get("removed", []):
                storage.delete_transaction(tx["transaction_id"])
                removed += 1

            cursor = resp.get("next_cursor")
            has_more = resp.get("has_more", False)
            if cursor:
                storage.set_cursor(item_id, cursor)

        entry: dict[str, Any] = {
            "item_id": item_id,
            "institution_name": item.get("institution_name"),
            "added": added,
            "modified": modified,
            "removed": removed,
            "status": status,
        }
        if fetch_error:
            entry["error"] = fetch_error
        results.append(entry)

    return {"items": results}


def refresh_transactions(
    storage: Storage,
    item_id: str | None = None,
) -> dict[str, Any]:
    """Ask Plaid to re-pull transactions from the bank on demand.

    Plaid normally refreshes each item on its own schedule (roughly every few
    hours). This calls /transactions/refresh to poke Plaid into pulling now,
    which is the right escape hatch when:
      - a user just made a purchase and wants to see it immediately
      - an institution has been slow to surface new transactions
      - you've re-linked after an item_error and want a full resync

    /transactions/refresh is asynchronous on Plaid's side: it returns
    immediately, then Plaid calls us back when new transactions land (via
    the DEFAULT_UPDATE webhook). With no webhook wired up, run
    sync_transactions again 30-60s later to pick up any new data.

    Pass item_id to refresh one institution; leave None to refresh all.
    Note: some institutions (especially smaller banks) don't support
    on-demand refresh and will surface PRODUCT_NOT_READY.
    """
    from plaid.model.transactions_refresh_request import TransactionsRefreshRequest

    client = get_client()
    items = [i for i in storage.list_items() if not item_id or i["item_id"] == item_id]
    if item_id and not items:
        return {"status": "not_found", "item_id": item_id}

    results: list[dict[str, Any]] = []
    for item in items:
        access_token = storage.get_runtime_token(item["item_id"])
        if not access_token:
            continue
        try:
            client.transactions_refresh(
                TransactionsRefreshRequest(access_token=access_token)
            )
            results.append(
                {
                    "item_id": item["item_id"],
                    "institution_name": item.get("institution_name"),
                    "status": "refresh_requested",
                }
            )
        except Exception as e:  # noqa: BLE001
            safe_error = safe_provider_error(e)
            storage.set_item_error(item["item_id"], safe_error)
            results.append(
                {
                    "item_id": item["item_id"],
                    "institution_name": item.get("institution_name"),
                    "error": safe_error,
                }
            )
    return {
        "items": results,
        "note": (
            "Refresh is asynchronous. Wait 30-60 seconds, then call "
            "sync_transactions to pull any new data Plaid found."
        ),
    }


def get_transactions(
    storage: Storage,
    start_date: str,
    end_date: str,
    account_id: str | None = None,
    category: str | None = None,
    merchant: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Query locally-cached synced transactions. Run sync_transactions first."""
    return storage.query_transactions(
        start_date=start_date,
        end_date=end_date,
        account_id=account_id,
        category=category,
        merchant=merchant,
        min_amount=min_amount,
        max_amount=max_amount,
        limit=limit,
    )


def search_transactions(
    storage: Storage,
    query: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fuzzy search across transaction name + merchant_name."""
    return storage.query_transactions(
        start_date=start_date,
        end_date=end_date,
        text=query,
        limit=limit,
    )


def spending_summary(
    storage: Storage,
    start_date: str,
    end_date: str,
    group_by: str = "category",
) -> list[dict[str, Any]]:
    """Aggregate spending between two dates. group_by: category|subcategory|merchant|account."""
    return storage.aggregate_transactions(start_date, end_date, group_by=group_by)
