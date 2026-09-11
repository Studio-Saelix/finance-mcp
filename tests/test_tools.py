"""Unit tests for tool modules with a stubbed Plaid client.

These don't call Plaid — they swap ``get_client()`` for a MagicMock and assert
that the tools translate responses into our flattened dicts correctly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from plaid_mcp import tools_debt as td
from plaid_mcp import tools_transactions as tt
from plaid_mcp import tools_wealth as tw

# ---------- fixtures local to this file ---------------------------------------


@pytest.fixture
def linked_item(tmp_db):
    tmp_db.save_item("item_1", "access_tok_1", "ins_1", "Test Bank", ["transactions"])
    tmp_db.upsert_account(
        "item_1",
        {
            "account_id": "acct_1",
            "name": "Checking",
            "type": "depository",
            "subtype": "checking",
            "mask": "0000",
            "iso_currency": "USD",
        },
    )
    return tmp_db


# ---------- tools_transactions ------------------------------------------------


def test_list_linked_institutions_counts_accounts(linked_item):
    linked_item.upsert_account("item_1", {"account_id": "acct_2", "name": "Savings"})
    result = tt.list_linked_institutions(linked_item)
    assert len(result) == 1
    assert result[0]["account_count"] == 2
    assert result[0]["institution_name"] == "Test Bank"


def test_list_accounts_reads_from_cache(linked_item):
    result = tt.list_accounts(linked_item)
    assert len(result) == 1
    assert result[0]["account_id"] == "acct_1"


def test_get_balances_flattens_plaid_response(linked_item, mock_plaid_client):
    mock_plaid_client.accounts_balance_get.return_value = {
        "accounts": [
            {
                "account_id": "acct_1",
                "name": "Checking",
                "mask": "0000",
                "type": "depository",
                "subtype": "checking",
                "balances": {
                    "current": 1234.56,
                    "available": 1200.00,
                    "limit": None,
                    "iso_currency_code": "USD",
                },
            }
        ]
    }

    balances = tt.get_balances(linked_item)

    mock_plaid_client.accounts_balance_get.assert_called_once()
    assert len(balances) == 1
    assert balances[0]["current"] == 1234.56
    assert balances[0]["available"] == 1200.00
    assert balances[0]["iso_currency"] == "USD"
    assert balances[0]["institution_name"] == "Test Bank"


def test_get_balances_filters_by_account_id(linked_item, mock_plaid_client):
    mock_plaid_client.accounts_balance_get.return_value = {
        "accounts": [
            {"account_id": "acct_1", "balances": {"current": 1.0}},
            {"account_id": "acct_2", "balances": {"current": 2.0}},
        ]
    }
    balances = tt.get_balances(linked_item, account_id="acct_2")
    assert len(balances) == 1
    assert balances[0]["account_id"] == "acct_2"


def _sync_tx(transaction_id, amount=10.0, date_="2026-04-01"):
    # Plaid SDK responses wrap items as objects with .to_dict(); emulate that.
    return SimpleNamespace(
        to_dict=lambda: {
            "transaction_id": transaction_id,
            "account_id": "acct_1",
            "amount": amount,
            "iso_currency_code": "USD",
            "date": date_,
            "name": "COFFEE",
            "merchant_name": "Blue Bottle",
            "personal_finance_category": {
                "primary": "FOOD_AND_DRINK",
                "detailed": "FOOD_AND_DRINK_COFFEE",
            },
            "pending": False,
        }
    )


def test_sync_transactions_persists_added(linked_item, mock_plaid_client):
    mock_plaid_client.transactions_sync.return_value = {
        "added": [_sync_tx("tx1"), _sync_tx("tx2", amount=20)],
        "modified": [],
        "removed": [],
        "next_cursor": "cursor_after_first",
        "has_more": False,
    }

    result = tt.sync_transactions(linked_item)

    assert result["items"][0]["added"] == 2
    assert linked_item.get_cursor("item_1") == "cursor_after_first"

    rows = linked_item.query_transactions(start_date="2026-01-01", end_date="2026-12-31")
    assert {r["transaction_id"] for r in rows} == {"tx1", "tx2"}


def test_sync_transactions_handles_removed(linked_item, mock_plaid_client):
    # Pre-seed a transaction so we have something to remove.
    linked_item.upsert_transaction("item_1", {
        "transaction_id": "old_tx",
        "account_id": "acct_1",
        "amount": 1.0,
        "date": "2026-01-01",
        "name": "n",
        "merchant_name": "m",
        "personal_finance_category": {"primary": "OTHER", "detailed": "OTHER"},
        "pending": False,
        "iso_currency_code": "USD",
    })
    mock_plaid_client.transactions_sync.return_value = {
        "added": [],
        "modified": [],
        "removed": [{"transaction_id": "old_tx"}],
        "next_cursor": "c",
        "has_more": False,
    }
    tt.sync_transactions(linked_item)
    assert linked_item.query_transactions(start_date="2026-01-01", end_date="2026-12-31") == []


def test_sync_transactions_records_error(linked_item, mock_plaid_client):
    mock_plaid_client.transactions_sync.side_effect = RuntimeError("Plaid down")
    result = tt.sync_transactions(linked_item)
    assert "error" in result["items"][0]
    assert result["items"][0]["error"] == "Plaid read failed: RuntimeError"


def test_spending_summary_sums_by_category(linked_item, mock_plaid_client):
    mock_plaid_client.transactions_sync.return_value = {
        "added": [
            _sync_tx("tx1", amount=5),
            _sync_tx("tx2", amount=10),
        ],
        "modified": [],
        "removed": [],
        "next_cursor": "c",
        "has_more": False,
    }
    tt.sync_transactions(linked_item)

    summary = tt.spending_summary(
        linked_item, start_date="2026-01-01", end_date="2026-12-31", group_by="category"
    )
    food = next(r for r in summary if r["grp"] == "FOOD_AND_DRINK")
    assert food["total"] == 15.0
    assert food["count"] == 2


def test_refresh_transactions_hits_plaid_for_each_item(linked_item, mock_plaid_client):
    linked_item.save_item("item_2", "access_tok_2", "ins_2", "Other Bank", ["transactions"])
    mock_plaid_client.transactions_refresh.return_value = {}
    result = tt.refresh_transactions(linked_item)
    assert mock_plaid_client.transactions_refresh.call_count == 2
    assert {i["item_id"] for i in result["items"]} == {"item_1", "item_2"}
    assert all(i["status"] == "refresh_requested" for i in result["items"])


def test_refresh_transactions_filters_by_item_id(linked_item, mock_plaid_client):
    linked_item.save_item("item_2", "access_tok_2", "ins_2", "Other Bank", ["transactions"])
    mock_plaid_client.transactions_refresh.return_value = {}
    result = tt.refresh_transactions(linked_item, item_id="item_2")
    assert mock_plaid_client.transactions_refresh.call_count == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["item_id"] == "item_2"


def test_refresh_transactions_returns_not_found_for_unknown_item(linked_item, mock_plaid_client):
    result = tt.refresh_transactions(linked_item, item_id="ghost")
    assert result["status"] == "not_found"
    assert mock_plaid_client.transactions_refresh.call_count == 0


def test_refresh_transactions_surfaces_per_item_errors(linked_item, mock_plaid_client):
    mock_plaid_client.transactions_refresh.side_effect = RuntimeError("PRODUCT_NOT_READY")
    result = tt.refresh_transactions(linked_item)
    assert "error" in result["items"][0]
    assert result["items"][0]["error"] == "Plaid read failed: RuntimeError"


def test_remove_institution_deletes_locally_even_if_plaid_fails(linked_item, mock_plaid_client):
    mock_plaid_client.item_remove.side_effect = RuntimeError("network")
    result = tt.remove_institution(linked_item, "item_1")
    assert result["status"] == "upstream_failed"
    assert linked_item.get_runtime_token("item_1") == "access_tok_1"


def test_remove_institution_returns_not_found(linked_item, mock_plaid_client):
    result = tt.remove_institution(linked_item, "ghost_item")
    assert result["status"] == "not_found"


# ---------- tools_wealth ------------------------------------------------------


def test_get_holdings_joins_securities(linked_item, mock_plaid_client):
    mock_plaid_client.investments_holdings_get.return_value = {
        "securities": [
            SimpleNamespace(to_dict=lambda: {
                "security_id": "sec_1",
                "ticker_symbol": "AAPL",
                "name": "Apple Inc",
                "type": "equity",
            })
        ],
        "holdings": [
            SimpleNamespace(to_dict=lambda: {
                "account_id": "acct_1",
                "security_id": "sec_1",
                "quantity": 10.0,
                "institution_price": 180.0,
                "institution_value": 1800.0,
                "cost_basis": 1500.0,
                "iso_currency_code": "USD",
            })
        ],
    }

    result = tw.get_holdings(linked_item)
    assert len(result["holdings"]) == 1
    h = result["holdings"][0]
    assert h["ticker"] == "AAPL"
    assert h["value"] == 1800.0
    assert h["cost_basis"] == 1500.0


def test_get_liabilities_separates_categories(linked_item, mock_plaid_client):
    mock_plaid_client.liabilities_get.return_value = {
        "liabilities": {
            "credit": [
                SimpleNamespace(to_dict=lambda: {
                    "account_id": "cc_1",
                    "last_statement_balance": 500.0,
                    "minimum_payment_amount": 25.0,
                    "aprs": [
                        {
                            "apr_type": "purchase",
                            "apr_percentage": 19.99,
                            "balance_subject_to_apr": 500,
                        },
                    ],
                })
            ],
            "student": [],
            "mortgage": [
                SimpleNamespace(to_dict=lambda: {
                    "account_id": "mtg_1",
                    "origination_principal_amount": 400000,
                    "interest_rate": {"percentage": 6.5, "type": "fixed"},
                    "loan_term": "30 year",
                })
            ],
        }
    }

    result = tw.get_liabilities(linked_item)
    assert len(result["credit_cards"]) == 1
    assert result["credit_cards"][0]["aprs"][0]["percentage"] == 19.99
    assert result["student_loans"] == []
    assert len(result["mortgages"]) == 1
    assert result["mortgages"][0]["interest_rate_percentage"] == 6.5


def test_get_identity_extracts_contact_info(linked_item, mock_plaid_client):
    mock_plaid_client.identity_get.return_value = {
        "accounts": [
            SimpleNamespace(to_dict=lambda: {
                "account_id": "acct_1",
                "name": "Checking",
                "owners": [
                    {
                        "names": ["Alexandra Example"],
                        "emails": [{"data": "alex@example.com", "primary": True}],
                        "phone_numbers": [{"data": "+15555551234"}],
                        "addresses": [
                            {"data": {"city": "SF", "region": "CA", "postal_code": "94102",
                                      "country": "US"}}
                        ],
                    }
                ],
            })
        ]
    }
    result = tw.get_identity(linked_item)
    assert result["identities"][0]["owners"][0]["names"] == ["Alexandra Example"]
    assert result["identities"][0]["owners"][0]["emails"] == ["alex@example.com"]
    assert result["identities"][0]["owners"][0]["addresses"][0]["city"] == "SF"


def test_get_income_handles_not_enabled_gracefully(linked_item, mock_plaid_client):
    mock_plaid_client.credit_bank_income_get.side_effect = RuntimeError(
        "INCOME_NOT_ENABLED"
    )
    result = tw.get_income(linked_item)
    # Error is surfaced per-item but doesn't raise.
    assert result["income_streams"][0].get("error") == "Plaid read failed: RuntimeError"


# ---------- tools_debt --------------------------------------------------------


def _cc(
    account_id: str,
    balance: float,
    purchase_apr: float,
    minimum: float = 25.0,
    institution: str = "Bank",
):
    """Build a credit-card dict shaped the way tools_wealth.get_liabilities emits."""
    return {
        "account_id": account_id,
        "institution_name": institution,
        "last_statement_balance": balance,
        "minimum_payment_amount": minimum,
        "next_payment_due_date": None,
        "aprs": [
            {
                "type": "purchase_apr",
                "percentage": purchase_apr,
                "balance_subject_to_apr": balance,
            }
        ],
    }


def _patch_liabilities(monkeypatch, cards):
    """Stub tools_wealth.get_liabilities to return a fixed set of cards."""
    monkeypatch.setattr(
        td,
        "get_liabilities",
        lambda storage: {
            "credit_cards": cards,
            "student_loans": [],
            "mortgages": [],
        },
    )


def test_set_account_override_requires_at_least_one_field(tmp_db):
    with pytest.raises(ValueError, match="Provide at least one"):
        td.set_account_override(tmp_db, account_id="acct_1")


def test_set_account_override_roundtrip(tmp_db):
    result = td.set_account_override(
        tmp_db, account_id="aa_card", effective_apr=0.0, promo_expires="2027-01-01"
    )
    assert result["status"] == "saved"
    overrides = td.list_overrides(tmp_db)
    assert overrides[0]["account_id"] == "aa_card"
    assert overrides[0]["effective_apr"] == 0.0


def test_clear_account_override(tmp_db):
    td.set_account_override(tmp_db, account_id="aa_card", effective_apr=0.0)
    assert td.clear_account_override(tmp_db, "aa_card")["status"] == "removed"
    assert td.clear_account_override(tmp_db, "aa_card")["status"] == "not_found"


def test_add_external_debt_validates_inputs(tmp_db):
    with pytest.raises(ValueError, match="balance must be >= 0"):
        td.add_external_debt(tmp_db, name="Bad", balance=-1, apr=5)
    with pytest.raises(ValueError, match="apr must be >= 0"):
        td.add_external_debt(tmp_db, name="Bad", balance=100, apr=-1)


def test_add_and_list_external_debt(tmp_db):
    result = td.add_external_debt(
        tmp_db, name="Affirm Couch", balance=800.0, apr=0.0, minimum_payment=100.0
    )
    assert result["status"] == "added"
    debt_id = result["debt_id"]
    listed = td.list_external_debts(tmp_db)
    assert len(listed) == 1
    assert listed[0]["name"] == "Affirm Couch"
    assert listed[0]["debt_id"] == debt_id


def test_update_external_debt_partial(tmp_db):
    debt_id = td.add_external_debt(tmp_db, "X", 500.0, 5.0)["debt_id"]
    result = td.update_external_debt(tmp_db, debt_id, balance=250.0)
    assert result["status"] == "updated"
    debts = td.list_external_debts(tmp_db)
    assert debts[0]["balance"] == 250.0
    assert debts[0]["apr"] == 5.0  # untouched


def test_update_external_debt_no_changes(tmp_db):
    debt_id = td.add_external_debt(tmp_db, "X", 500.0, 5.0)["debt_id"]
    assert td.update_external_debt(tmp_db, debt_id)["status"] == "no_changes"


def test_remove_external_debt(tmp_db):
    debt_id = td.add_external_debt(tmp_db, "X", 500.0, 5.0)["debt_id"]
    assert td.remove_external_debt(tmp_db, debt_id)["status"] == "removed"
    assert td.remove_external_debt(tmp_db, debt_id)["status"] == "not_found"


def test_summarize_debt_avalanche_orders_by_apr(tmp_db, monkeypatch):
    _patch_liabilities(
        monkeypatch,
        [
            _cc("low", balance=500.0, purchase_apr=12.0),
            _cc("high", balance=1500.0, purchase_apr=24.0),
            _cc("mid", balance=2000.0, purchase_apr=18.0),
        ],
    )
    result = td.summarize_debt(tmp_db, strategy="avalanche")
    assert [d["account_id"] for d in result["debts"]] == ["high", "mid", "low"]
    assert result["projections"]["priority_debt"]["account_id"] == "high"


def test_summarize_debt_snowball_orders_by_balance(tmp_db, monkeypatch):
    _patch_liabilities(
        monkeypatch,
        [
            _cc("big", balance=5000.0, purchase_apr=12.0),
            _cc("small", balance=200.0, purchase_apr=24.0),
        ],
    )
    result = td.summarize_debt(tmp_db, strategy="snowball")
    assert [d["account_id"] for d in result["debts"]] == ["small", "big"]


def test_summarize_debt_override_overrides_plaid_apr(tmp_db, monkeypatch):
    """Core correctness test: Plaid says 26% but user says 0% promo — we trust the user."""
    _patch_liabilities(
        monkeypatch,
        [
            _cc("aa_card", balance=3000.0, purchase_apr=26.49),
            _cc("costco", balance=1000.0, purchase_apr=18.74),
        ],
    )
    # Tell the system the AA card is actually at 0% until 2027.
    td.set_account_override(
        tmp_db,
        account_id="aa_card",
        effective_apr=0.0,
        promo_expires="2027-01-01",
    )
    result = td.summarize_debt(tmp_db, strategy="avalanche", today="2026-04-20")
    # Avalanche should now attack Costco (18.74%), not the AA card (really 0%).
    assert result["debts"][0]["account_id"] == "costco"
    aa = next(d for d in result["debts"] if d["account_id"] == "aa_card")
    assert aa["effective_apr"] == 0.0
    assert "user override" in aa["apr_source"]


def test_summarize_debt_override_falls_back_after_expiration(tmp_db, monkeypatch):
    _patch_liabilities(
        monkeypatch, [_cc("aa_card", balance=3000.0, purchase_apr=26.49)]
    )
    td.set_account_override(
        tmp_db,
        account_id="aa_card",
        effective_apr=0.0,
        promo_expires="2026-01-01",  # already expired relative to today
    )
    result = td.summarize_debt(tmp_db, strategy="avalanche", today="2026-04-20")
    card = result["debts"][0]
    assert card["effective_apr"] == 26.49
    assert "expired" in card["apr_source"].lower()


def test_summarize_debt_flags_expiring_promo(tmp_db, monkeypatch):
    _patch_liabilities(
        monkeypatch, [_cc("aa_card", balance=3000.0, purchase_apr=26.49)]
    )
    td.set_account_override(
        tmp_db,
        account_id="aa_card",
        effective_apr=0.0,
        promo_expires="2026-05-15",  # ~25 days out from 2026-04-20
    )
    result = td.summarize_debt(tmp_db, strategy="avalanche", today="2026-04-20")
    assert any("expires in 25 days" in w for w in result["warnings"])


def test_summarize_debt_includes_external_debts(tmp_db, monkeypatch):
    _patch_liabilities(
        monkeypatch, [_cc("cc", balance=1000.0, purchase_apr=20.0)]
    )
    td.add_external_debt(
        tmp_db, name="Affirm Couch", balance=500.0, apr=0.0, minimum_payment=100.0
    )
    result = td.summarize_debt(tmp_db, strategy="avalanche")
    sources = {d["source"] for d in result["debts"]}
    assert sources == {"plaid", "external"}
    assert result["total_balance"] == 1500.0


def test_summarize_debt_skips_zero_balance_cards(tmp_db, monkeypatch):
    _patch_liabilities(
        monkeypatch,
        [
            _cc("active", balance=500.0, purchase_apr=18.0),
            _cc("paid_off", balance=0.0, purchase_apr=18.0),
        ],
    )
    result = td.summarize_debt(tmp_db, strategy="avalanche")
    ids = [d["account_id"] for d in result["debts"]]
    assert ids == ["active"]


def test_summarize_debt_warns_when_no_debts(tmp_db, monkeypatch):
    _patch_liabilities(monkeypatch, [])
    result = td.summarize_debt(tmp_db, strategy="avalanche")
    assert result["debts"] == []
    assert any("No debts" in w for w in result["warnings"])


def test_summarize_debt_rejects_unknown_strategy(tmp_db):
    with pytest.raises(ValueError, match="strategy must be"):
        td.summarize_debt(tmp_db, strategy="double-down")


def test_summarize_debt_projections_with_extra_payment(tmp_db, monkeypatch):
    _patch_liabilities(
        monkeypatch,
        [_cc("cc", balance=1000.0, purchase_apr=20.0, minimum=25.0)],
    )
    result = td.summarize_debt(
        tmp_db, strategy="avalanche", extra_monthly_payment=100.0
    )
    proj = result["projections"]
    # With extra payment, payoff should be shorter and cheaper.
    assert proj["with_extra_payment"]["months"] < proj["minimum_only"]["months"]
    assert proj["interest_saved_vs_minimum"] > 0


def test_summarize_debt_detects_never_pays_off(tmp_db, monkeypatch):
    """A $25/mo minimum on $10K @ 20% APR can't even cover monthly interest."""
    _patch_liabilities(
        monkeypatch,
        [_cc("cc", balance=10000.0, purchase_apr=20.0, minimum=25.0)],
    )
    result = td.summarize_debt(tmp_db, strategy="avalanche")
    assert result["projections"]["minimum_only"]["never_pays_off"] is True
