"""PlaidProvider + build_provider factory tests — Plaid client mocked."""

from __future__ import annotations

import pytest

from plaid_mcp.config import Config
from plaid_mcp.providers import (
    Account,
    Balance,
    Capability,
    Enrollment,
    PlaidProvider,
    Transaction,
    build_provider,
)

# ---- helpers ----------------------------------------------------------


def _config(**overrides) -> Config:
    base = dict(
        client_id="test_client_id",
        secret="test_secret",
        env="sandbox",
        provider="plaid",
    )
    base.update(overrides)
    return Config(**base)


def _seed_item(
    storage,
    item_id: str = "item_1",
    account_ids: tuple[str, ...] = ("acct_1", "acct_2"),
) -> Enrollment:
    storage.save_item(
        item_id=item_id,
        access_token=f"access_{item_id}",
        institution_id="ins_1",
        institution_name="Test Bank",
        products=["transactions"],
    )
    for i, acct_id in enumerate(account_ids):
        storage.upsert_account(
            item_id,
            {
                "account_id": acct_id,
                "name": f"Account {i}",
                "official_name": f"Official {i}",
                "type": "depository",
                "subtype": "checking" if i == 0 else "savings",
                "mask": f"{i}{i}{i}{i}",
                "iso_currency": "USD",
            },
        )
    return Enrollment(
        id=item_id,
        institution_id="ins_1",
        institution_name="Test Bank",
        access_token=f"access_{item_id}",
        provider="plaid",
    )


# ---- capabilities -----------------------------------------------------


def test_capabilities_includes_investments_and_liabilities(tmp_db):
    p = PlaidProvider(tmp_db, _config())
    caps = p.capabilities()
    assert Capability.INVESTMENTS in caps
    assert Capability.LIABILITIES in caps
    assert Capability.TRANSACTIONS in caps
    assert not hasattr(p, "get_identity")


# ---- list_accounts ----------------------------------------------------


def test_list_accounts_returns_normalized_accounts_for_enrollment(tmp_db):
    enrollment = _seed_item(tmp_db)
    # Second item's accounts must NOT leak into the first's enrollment.
    _seed_item(tmp_db, item_id="item_other", account_ids=("acct_x", "acct_y"))

    p = PlaidProvider(tmp_db, _config())
    accounts = p.list_accounts(enrollment)

    assert len(accounts) == 2
    assert all(isinstance(a, Account) for a in accounts)
    ids = sorted(a.id for a in accounts)
    assert ids == ["acct_1", "acct_2"]
    for a in accounts:
        assert a.enrollment_id == "item_1"


def test_list_accounts_empty_for_unknown_enrollment(tmp_db):
    _seed_item(tmp_db)
    missing = Enrollment(
        id="does_not_exist",
        institution_id=None,
        institution_name=None,
        access_token="nope",
        provider="plaid",
    )
    p = PlaidProvider(tmp_db, _config())
    assert p.list_accounts(missing) == []


# ---- get_balances -----------------------------------------------------


def test_get_balances_calls_plaid_and_normalizes(tmp_db, mock_plaid_client):
    enrollment = _seed_item(tmp_db)
    mock_plaid_client.accounts_balance_get.return_value = {
        "accounts": [
            {
                "account_id": "acct_1",
                "balances": {
                    "current": 1500.00,
                    "available": 1450.00,
                    "limit": None,
                    "iso_currency_code": "USD",
                },
            },
            {
                "account_id": "acct_2",
                "balances": {
                    "current": 9876.54,
                    "available": 9876.54,
                    "limit": None,
                    "iso_currency_code": "USD",
                },
            },
        ]
    }

    p = PlaidProvider(tmp_db, _config())
    balances = p.get_balances(enrollment)

    mock_plaid_client.accounts_balance_get.assert_called_once()
    assert len(balances) == 2
    assert all(isinstance(b, Balance) for b in balances)
    by_id = {b.account_id: b for b in balances}
    assert by_id["acct_1"].current == 1500.00
    assert by_id["acct_1"].available == 1450.00
    assert by_id["acct_1"].iso_currency == "USD"
    assert by_id["acct_2"].current == 9876.54


# ---- get_transactions -------------------------------------------------


def _cache_tx(storage, item_id: str, account_id: str, tx_id: str, date: str, amount: float):
    storage.upsert_transaction(
        item_id,
        {
            "transaction_id": tx_id,
            "account_id": account_id,
            "amount": amount,
            "iso_currency_code": "USD",
            "date": date,
            "name": f"Purchase {tx_id}",
            "merchant_name": "Coffee Shop",
            "personal_finance_category": {
                "primary": "FOOD_AND_DRINK",
                "detailed": "FOOD_AND_DRINK_COFFEE",
            },
            "pending": False,
            "payment_channel": "in store",
        },
    )


def test_get_transactions_returns_cached_storage_rows(tmp_db):
    enrollment = _seed_item(tmp_db)
    _cache_tx(tmp_db, "item_1", "acct_1", "tx_a", "2026-03-10", 12.50)
    _cache_tx(tmp_db, "item_1", "acct_2", "tx_b", "2026-03-12", 40.00)
    # Another item's transactions shouldn't bleed in.
    _seed_item(tmp_db, item_id="item_other", account_ids=("acct_z",))
    _cache_tx(tmp_db, "item_other", "acct_z", "tx_z", "2026-03-11", 999.00)

    p = PlaidProvider(tmp_db, _config())
    txs = p.get_transactions(enrollment, "2026-03-01", "2026-03-31")

    ids = sorted(t.id for t in txs)
    assert ids == ["tx_a", "tx_b"]
    assert all(isinstance(t, Transaction) for t in txs)
    by_id = {t.id: t for t in txs}
    assert by_id["tx_a"].amount == 12.50
    assert by_id["tx_a"].category == "FOOD_AND_DRINK"
    assert by_id["tx_a"].merchant_name == "Coffee Shop"


def test_get_transactions_round_trips_raw_blob(tmp_db):
    """The cached ``raw`` JSON should resurface on Transaction.raw so callers
    that care about Plaid-native fields (e.g. counterparties, location) can
    reach past the normalized shape."""
    enrollment = _seed_item(tmp_db)
    _cache_tx(tmp_db, "item_1", "acct_1", "tx_a", "2026-03-10", 12.50)

    p = PlaidProvider(tmp_db, _config())
    [tx] = p.get_transactions(enrollment, "2026-03-01", "2026-03-31")
    assert tx.raw != {}
    assert tx.raw["transaction_id"] == "tx_a"
    assert tx.raw["personal_finance_category"]["primary"] == "FOOD_AND_DRINK"


def test_get_transactions_filter_by_account(tmp_db):
    enrollment = _seed_item(tmp_db)
    _cache_tx(tmp_db, "item_1", "acct_1", "tx_a", "2026-03-10", 12.50)
    _cache_tx(tmp_db, "item_1", "acct_2", "tx_b", "2026-03-12", 40.00)

    p = PlaidProvider(tmp_db, _config())
    txs = p.get_transactions(
        enrollment, "2026-03-01", "2026-03-31", account_id="acct_1"
    )
    assert [t.id for t in txs] == ["tx_a"]


def test_get_transactions_rejects_account_from_another_enrollment(tmp_db):
    enrollment = _seed_item(tmp_db)
    _seed_item(tmp_db, item_id="item_other", account_ids=("acct_z",))
    _cache_tx(tmp_db, "item_other", "acct_z", "tx_z", "2026-03-11", 999.00)

    p = PlaidProvider(tmp_db, _config())
    txs = p.get_transactions(
        enrollment, "2026-03-01", "2026-03-31", account_id="acct_z"
    )
    assert txs == []


# ---- factory ----------------------------------------------------------


def test_factory_returns_plaid_provider_when_provider_plaid(tmp_db):
    provider = build_provider(_config(provider="plaid"), storage=tmp_db)
    assert isinstance(provider, PlaidProvider)
    assert provider.name == "plaid"


def test_factory_raises_when_plaid_missing_storage():
    with pytest.raises(ValueError, match="Storage"):
        build_provider(_config(provider="plaid"), storage=None)


def test_factory_raises_for_unknown_provider(tmp_db):
    with pytest.raises(ValueError, match="Unknown provider"):
        build_provider(_config(provider="bogus"), storage=tmp_db)
