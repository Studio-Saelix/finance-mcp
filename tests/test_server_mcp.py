"""Runtime MCP contract and boundary tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from click.testing import CliRunner
from fastmcp import Client

from plaid_mcp import server as server_module
from plaid_mcp.admin import main
from plaid_mcp.config import Config
from plaid_mcp.providers.base import Account, Balance, Enrollment
from plaid_mcp.server import build_server

EXPECTED_TOOL_NAMES = {
    "list_accounts", "get_balances", "sync_transactions", "refresh_transactions",
    "get_transactions", "search_transactions", "spending_summary", "get_holdings",
    "get_investment_transactions", "get_liabilities",
}
FORBIDDEN_TOOL_NAMES = {
    "link_account", "complete_linking", "list_linked_institutions_tool",
    "remove_institution_tool", "get_identity", "get_income", "set_account_override_tool",
    "clear_account_override_tool", "list_overrides_tool", "add_external_debt_tool",
    "update_external_debt_tool", "remove_external_debt_tool", "list_external_debts_tool",
    "summarize_debt_tool", "teller", "serve",
}


async def test_runtime_exposes_exactly_approved_tools():
    async with Client(build_server()) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert names == EXPECTED_TOOL_NAMES
    assert names.isdisjoint(FORBIDDEN_TOOL_NAMES)


async def test_list_accounts_is_callable_through_mcp():
    async with Client(build_server()) as client:
        result = await client.call_tool("list_accounts", {})
    data = result.data if hasattr(result, "data") else result.structured_content
    assert data in ([], {"result": []}, None)


async def test_tool_schema_has_product_names_and_descriptions():
    async with Client(build_server()) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
    assert tools["spending_summary"].description
    assert "category" in tools["spending_summary"].description.lower()


def test_runtime_balance_recovery_clears_item_health_and_status(
    tmp_db, monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    enrollment = Enrollment("item_1", "ins_1", "Test Bank", "plaid")
    account = Account(
        "acct_1", "item_1", "Checking", None, "depository", "checking", "0000", "USD"
    )
    tmp_db.save_item("item_1", "access-token", "ins_1", "Test Bank", ["transactions"])
    tmp_db.upsert_account(
        "item_1", {"account_id": "acct_1", "name": "Checking", "type": "depository"}
    )
    provider = MagicMock()
    provider.get_balances.side_effect = [
        RuntimeError("temporary provider outage"),
        [Balance("acct_1", 12.0, 10.0, None, "USD")],
    ]
    provider.list_accounts.return_value = [account]
    monkeypatch.setattr(
        server_module,
        "_with_provider",
        lambda config, storage: (provider, [enrollment]),
    )
    config = Config(
        client_id="test",
        secret="test",
        db_path=tmp_db.db_path,
    )

    assert server_module._get_balances(config, tmp_db) == []
    assert tmp_db.list_items()[0]["last_error"] == "Plaid read failed: RuntimeError"

    result = server_module._get_balances(config, tmp_db)
    assert result[0]["current"] == 12.0
    assert tmp_db.list_items()[0]["last_error"] is None

    status = CliRunner().invoke(main, ["status"])
    assert status.exit_code == 0, status.output
    assert "health=ok" in status.output
