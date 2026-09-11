"""The stdio runtime MCP server with a read-only capability surface."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from .config import Config
from .errors import safe_provider_error
from .providers import build_provider
from .server_helpers import list_enrollments
from .storage import Storage
from .tools_transactions import (
    refresh_transactions as refresh_transactions_impl,
)
from .tools_transactions import (
    search_transactions as search_transactions_impl,
)
from .tools_transactions import (
    spending_summary as spending_summary_impl,
)
from .tools_transactions import (
    sync_transactions as sync_transactions_impl,
)
from .tools_wealth import (
    get_holdings as get_holdings_impl,
)
from .tools_wealth import (
    get_investment_transactions as get_investment_transactions_impl,
)
from .tools_wealth import (
    get_liabilities as get_liabilities_impl,
)


def build_server() -> FastMCP:
    config = Config.from_env()
    storage = Storage(config.db_path)
    if config.provider != "plaid":
        raise RuntimeError("The runtime supports only PROVIDER=plaid.")

    mcp = FastMCP(
        "studio-saelix-finance-mcp",
        instructions=(
            "Read-only financial data access through Plaid. Local writes are "
            "limited to transaction/account cache, sync cursors, and sanitized "
            "retrieval errors. Call sync_transactions before querying cached data."
        ),
    )

    @mcp.tool
    def list_accounts() -> list[dict[str, Any]]:
        """List every cached account across linked Plaid institutions."""
        return _list_accounts(config, storage)

    @mcp.tool
    def get_balances(account_id: str | None = None) -> list[dict[str, Any]]:
        """Read current balances, optionally filtered by account_id."""
        return _get_balances(config, storage, account_id)

    @mcp.tool
    def sync_transactions(wait_for_ready: bool = True, wait_timeout_seconds: int = 60):
        """Synchronize Plaid transactions into the local read cache."""
        return sync_transactions_impl(
            storage, wait_for_ready=wait_for_ready, wait_timeout_s=wait_timeout_seconds
        )

    @mcp.tool
    def refresh_transactions(item_id: str | None = None):
        """Request Plaid to refresh source transactions for later retrieval."""
        return refresh_transactions_impl(storage, item_id=item_id)

    @mcp.tool
    def get_transactions(
        start_date: str,
        end_date: str,
        account_id: str | None = None,
        category: str | None = None,
        merchant: str | None = None,
        min_amount: float | None = None,
        max_amount: float | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Query synchronized transactions from the local read cache."""
        return storage.query_transactions(
            start_date=start_date, end_date=end_date, account_id=account_id,
            category=category, merchant=merchant, min_amount=min_amount,
            max_amount=max_amount, limit=limit,
        )

    @mcp.tool
    def search_transactions(
        query: str,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
    ):
        """Search synchronized transactions by name or merchant."""
        return search_transactions_impl(
            storage, query=query, start_date=start_date, end_date=end_date, limit=limit
        )

    @mcp.tool
    def spending_summary(start_date: str, end_date: str, group_by: str = "category"):
        """Aggregate synchronized spending by category, merchant, or account."""
        return spending_summary_impl(
            storage, start_date=start_date, end_date=end_date, group_by=group_by
        )

    @mcp.tool
    def get_holdings(account_id: str | None = None):
        """Read current Plaid investment holdings."""
        return get_holdings_impl(storage, account_id=account_id)

    @mcp.tool
    def get_investment_transactions(
        start_date: str, end_date: str, account_id: str | None = None, limit: int = 250
    ):
        """Read Plaid investment transactions for a date range."""
        return get_investment_transactions_impl(
            storage, start_date=start_date, end_date=end_date,
            account_id=account_id, limit=limit,
        )

    @mcp.tool
    def get_liabilities():
        """Read Plaid-reported liabilities without local APR overrides."""
        return get_liabilities_impl(storage)

    return mcp


def _with_provider(config: Config, storage: Storage):
    provider = build_provider(config, storage)
    try:
        return provider, list_enrollments(config, storage)
    except Exception:
        _close(provider)
        raise


def _list_accounts(config: Config, storage: Storage) -> list[dict[str, Any]]:
    provider, enrollments = _with_provider(config, storage)
    try:
        return [
            {
                "account_id": account.id,
                "item_id": enrollment.id,
                "institution_name": enrollment.institution_name,
                "name": account.name,
                "official_name": account.official_name,
                "type": account.type,
                "subtype": account.subtype,
                "mask": account.mask,
                "iso_currency": account.iso_currency,
            }
            for enrollment in enrollments
            for account in provider.list_accounts(enrollment)
        ]
    finally:
        _close(provider)


def _get_balances(
    config: Config, storage: Storage, account_id: str | None = None
) -> list[dict[str, Any]]:
    provider, enrollments = _with_provider(config, storage)
    try:
        out = []
        for enrollment in enrollments:
            try:
                balances = provider.get_balances(enrollment)
            except Exception as exc:  # noqa: BLE001
                storage.set_item_error(enrollment.id, safe_provider_error(exc))
                continue
            accounts = {a.id: a for a in provider.list_accounts(enrollment)}
            for balance in balances:
                if account_id and balance.account_id != account_id:
                    continue
                account = accounts.get(balance.account_id)
                out.append({
                    "account_id": balance.account_id,
                    "institution_name": enrollment.institution_name,
                    "name": account.name if account else None,
                    "mask": account.mask if account else None,
                    "type": account.type if account else None,
                    "subtype": account.subtype if account else None,
                    "current": balance.current,
                    "available": balance.available,
                    "limit": balance.limit,
                    "iso_currency": balance.iso_currency,
                })
        return out
    finally:
        _close(provider)


def _close(provider: Any) -> None:
    close = getattr(provider, "close", None)
    if callable(close):
        close()
