"""Tests for config env parsing."""

from __future__ import annotations

import pytest

from plaid_mcp.config import Config


def test_from_env_happy_path():
    cfg = Config.from_env()
    assert cfg.client_id == "test_client_id"
    assert cfg.secret == "test_secret"
    assert cfg.env == "sandbox"
    assert cfg.products == ["transactions", "investments", "liabilities"]
    assert cfg.country_codes == ["US"]
    assert cfg.client_name == "Studio Saelix Finance MCP"
    assert str(cfg.db_path).endswith("plaid-test.db")
    assert "identity" not in cfg.optional_products


def test_config_default_optional_products_exclude_identity():
    cfg = Config(client_id="test", secret="test")
    assert cfg.optional_products == ["investments", "liabilities"]


def test_host_mapping():
    cfg = Config.from_env()
    assert cfg.host == "https://sandbox.plaid.com"


def test_missing_credentials_raises(monkeypatch):
    monkeypatch.delenv("PLAID_CLIENT_ID")
    with pytest.raises(RuntimeError, match="PLAID_CLIENT_ID"):
        Config.from_env()


def test_invalid_env_raises_on_host_access(monkeypatch):
    monkeypatch.setenv("PLAID_ENV", "production-staging")
    cfg = Config.from_env()
    with pytest.raises(ValueError, match="PLAID_ENV must be one of"):
        _ = cfg.host


def test_country_codes_uppercased(monkeypatch):
    monkeypatch.setenv("PLAID_COUNTRY_CODES", " us, ca ,gb ")
    cfg = Config.from_env()
    assert cfg.country_codes == ["US", "CA", "GB"]


def test_products_lowercased_and_trimmed(monkeypatch):
    monkeypatch.setenv("PLAID_PRODUCTS", " Transactions ,INVESTMENTS, ")
    cfg = Config.from_env()
    assert cfg.products == ["transactions", "investments"]
