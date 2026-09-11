"""Runtime Plaid routing and structural authority tests."""

from __future__ import annotations

import pytest
from fastmcp import Client

from plaid_mcp.providers import PlaidProvider, build_provider
from plaid_mcp.server import build_server


def test_factory_is_plaid_only(tmp_db):
    provider = build_provider(type("Cfg", (), {"provider": "plaid"})(), tmp_db)
    assert isinstance(provider, PlaidProvider)


def test_factory_rejects_teller(tmp_db):
    with pytest.raises(ValueError, match="Unknown provider"):
        build_provider(type("Cfg", (), {"provider": "teller"})(), tmp_db)


def test_runtime_provider_has_no_lifecycle_authority(tmp_db, monkeypatch):
    monkeypatch.setenv("PLAID_CLIENT_ID", "test")
    monkeypatch.setenv("PLAID_SECRET", "test")
    provider = build_provider(type("Cfg", (), {"provider": "plaid"})(), tmp_db)
    assert not hasattr(provider, "begin_enrollment")
    assert not hasattr(provider, "complete_enrollment")
    assert not hasattr(provider, "remove_enrollment")
    assert not hasattr(provider, "item_remove")


def test_runtime_server_builds_without_admin_or_payment_imports():
    server = build_server()
    assert server is not None
    runtime_globals = build_server.__globals__
    assert "link" not in runtime_globals
    assert "tools_debt" not in runtime_globals
    assert "payments" not in runtime_globals


async def test_runtime_read_execution_cannot_reach_lifecycle_calls(
    tmp_db, mock_plaid_client, monkeypatch
):
    """A real MCP read call only reaches the Plaid read API."""
    monkeypatch.setenv("PLAID_MCP_DB", str(tmp_db.db_path))
    tmp_db.save_item("item_1", "access_token", "ins_1", "Test Bank", ["transactions"])
    mock_plaid_client.accounts_balance_get.return_value = {"accounts": []}
    lifecycle_methods = (
        "item_remove",
        "link_token_create",
        "link_token_get",
        "item_public_token_exchange",
    )

    async with Client(build_server()) as client:
        await client.call_tool("get_balances", {})

    mock_plaid_client.accounts_balance_get.assert_called_once()
    for method_name in lifecycle_methods:
        getattr(mock_plaid_client, method_name).assert_not_called()
