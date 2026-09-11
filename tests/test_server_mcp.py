"""Runtime MCP contract and boundary tests."""

from __future__ import annotations

from fastmcp import Client

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
