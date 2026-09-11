"""Shared pytest fixtures.

The autouse ``_env`` fixture sets fake Plaid credentials and points
``PLAID_MCP_DB`` at a throwaway path, so every test starts from a clean
state with no real network calls unless explicitly opted in.

Sandbox tests load real credentials from ``.env.test`` at the repo root
if present; otherwise they're marked as skipped.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from dotenv import load_dotenv

from plaid_mcp.storage import Storage

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_TEST = REPO_ROOT / ".env.test"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    """Give every test fake credentials + an isolated DB, unless it's a sandbox test."""
    monkeypatch.setenv("PROVIDER", "plaid")
    monkeypatch.setenv("PLAID_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("PLAID_SECRET", "test_secret")
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    monkeypatch.setenv("PLAID_PRODUCTS", "transactions,investments,liabilities")
    monkeypatch.setenv("PLAID_COUNTRY_CODES", "US")
    monkeypatch.setenv("PLAID_MCP_DB", str(tmp_path / "plaid-test.db"))
    monkeypatch.delenv("PLAID_WEBHOOK_URL", raising=False)

    # Clear the Plaid client lru_cache between tests.
    from plaid_mcp import client as client_mod

    client_mod._cached.cache_clear()


@pytest.fixture
def tmp_db(tmp_path) -> Storage:
    """Fresh SQLite store rooted in a tmp dir. Callers can mutate freely."""
    db_path = tmp_path / "plaid-test.db"
    storage = Storage(db_path)
    try:
        yield storage
    finally:
        storage.close()


@pytest.fixture
def mock_plaid_client(monkeypatch) -> MagicMock:
    """Replace plaid_mcp.client.get_client everywhere with a MagicMock.

    Tests can set .return_value on any method, e.g.:
        mock_plaid_client.accounts_get.return_value = {"accounts": [...]}
    """
    client = MagicMock(name="plaid_client")
    # Patch in all the modules that resolve get_client at call time. The
    # providers/plaid.py module imports `plaid_mcp.client` as a module and
    # calls ``client_mod.get_client()``, so patching the attribute on the
    # `client` module covers it — but we include it explicitly anyway so
    # future refactors that import the name directly are caught.
    for module in (
        "plaid_mcp.client",
        "plaid_mcp.link",
        "plaid_mcp.tools_transactions",
        "plaid_mcp.tools_wealth",
        "plaid_mcp.providers.plaid",
    ):
        monkeypatch.setattr(f"{module}.get_client", lambda: client, raising=False)
    return client


@pytest.fixture
def sandbox_creds():
    """Load sandbox creds from .env.test, or skip the test."""
    if not ENV_TEST.exists():
        pytest.skip(
            f"{ENV_TEST.name} not found at repo root — create it with sandbox "
            "PLAID_CLIENT_ID and PLAID_SECRET to run sandbox tests."
        )
    load_dotenv(ENV_TEST, override=True)
    client_id = os.getenv("PLAID_CLIENT_ID")
    secret = os.getenv("PLAID_SECRET")
    if not client_id or not secret:
        pytest.skip("PLAID_CLIENT_ID and PLAID_SECRET must be set in .env.test")
    # Force sandbox env regardless of what .env.test says.
    os.environ["PLAID_ENV"] = "sandbox"
    return {"client_id": client_id, "secret": secret}
