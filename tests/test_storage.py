"""Tests for the SQLite storage layer."""

from __future__ import annotations

import sqlite3

import pytest


def test_save_and_get_item(tmp_db):
    tmp_db.save_item(
        item_id="item_1",
        access_token="tok_1",
        institution_id="ins_1",
        institution_name="Test Bank",
        products=["transactions"],
    )
    assert tmp_db.get_runtime_token("item_1") == "tok_1"

    items = tmp_db.list_items()
    assert len(items) == 1
    assert items[0]["institution_name"] == "Test Bank"
    assert items[0]["products"] == ["transactions"]


def test_save_item_preserves_created_at_on_update(tmp_db):
    tmp_db.save_item("item_1", "tok_1", "ins_1", "Bank", ["transactions"])
    original = tmp_db.list_items()[0]["created_at"]
    # Re-save (e.g. token rotation) and verify created_at is stable.
    tmp_db.save_item("item_1", "tok_2", "ins_1", "Bank", ["transactions"])
    assert tmp_db.list_items()[0]["created_at"] == original
    assert tmp_db.get_runtime_token("item_1") == "tok_2"


def test_purge_item_state_removes_item_accounts_and_cursor(tmp_db):
    tmp_db.save_item("item_1", "tok_1", "ins_1", "Bank", ["transactions"])
    tmp_db.upsert_account("item_1", {"account_id": "acct_1", "name": "Checking"})
    tmp_db.set_cursor("item_1", "cursor_v1")

    tmp_db.purge_item_state("item_1")

    assert tmp_db.get_runtime_token("item_1") is None
    assert tmp_db.list_accounts() == []
    assert tmp_db.get_cursor("item_1") is None


def test_upsert_and_list_accounts(tmp_db):
    tmp_db.save_item("item_1", "tok_1", "ins_1", "Bank", ["transactions"])
    tmp_db.upsert_account("item_1", {
        "account_id": "acct_1",
        "name": "Checking",
        "official_name": "Plaid Checking",
        "type": "depository",
        "subtype": "checking",
        "mask": "0000",
        "iso_currency": "USD",
    })
    accounts = tmp_db.list_accounts()
    assert len(accounts) == 1
    assert accounts[0]["account_id"] == "acct_1"
    assert accounts[0]["institution_name"] == "Bank"


def test_cursor_roundtrip(tmp_db):
    tmp_db.save_item("item_1", "tok_1", None, None, None)
    assert tmp_db.get_cursor("item_1") is None
    tmp_db.set_cursor("item_1", "abc123")
    assert tmp_db.get_cursor("item_1") == "abc123"
    tmp_db.set_cursor("item_1", "def456")
    assert tmp_db.get_cursor("item_1") == "def456"


def _make_tx(
    transaction_id: str,
    account_id: str = "acct_1",
    amount: float = 10.0,
    date_: str = "2026-04-01",
    name: str = "COFFEE SHOP",
    merchant: str = "Blue Bottle",
    primary_category: str = "FOOD_AND_DRINK",
    detailed: str = "FOOD_AND_DRINK_COFFEE",
) -> dict:
    return {
        "transaction_id": transaction_id,
        "account_id": account_id,
        "amount": amount,
        "iso_currency_code": "USD",
        "date": date_,
        "name": name,
        "merchant_name": merchant,
        "personal_finance_category": {
            "primary": primary_category,
            "detailed": detailed,
        },
        "pending": False,
        "payment_channel": "in store",
    }


def test_query_transactions_filters(tmp_db):
    tmp_db.save_item("item_1", "tok_1", None, None, None)
    tmp_db.upsert_account("item_1", {"account_id": "acct_1", "name": "Checking"})

    tmp_db.upsert_transaction("item_1", _make_tx("tx1", amount=5.00, date_="2026-04-01"))
    tmp_db.upsert_transaction("item_1", _make_tx("tx2", amount=50.00, date_="2026-04-10",
                                                 merchant="Whole Foods",
                                                 primary_category="FOOD_AND_DRINK"))
    tmp_db.upsert_transaction("item_1", _make_tx("tx3", amount=500.00, date_="2026-03-01",
                                                 merchant="Apple", primary_category="SHOPPING"))

    # Date range filter
    rows = tmp_db.query_transactions(start_date="2026-04-01", end_date="2026-04-30")
    assert {r["transaction_id"] for r in rows} == {"tx1", "tx2"}

    # Category filter
    rows = tmp_db.query_transactions(
        start_date="2026-01-01", end_date="2026-12-31", category="SHOPPING"
    )
    assert [r["transaction_id"] for r in rows] == ["tx3"]

    # Amount range
    rows = tmp_db.query_transactions(
        start_date="2026-01-01", end_date="2026-12-31", min_amount=40, max_amount=100
    )
    assert [r["transaction_id"] for r in rows] == ["tx2"]

    # Text search across name / merchant_name
    rows = tmp_db.query_transactions(
        start_date="2026-01-01", end_date="2026-12-31", text="whole"
    )
    assert [r["transaction_id"] for r in rows] == ["tx2"]


def test_aggregate_transactions_sums_by_category(tmp_db):
    tmp_db.save_item("item_1", "tok_1", None, None, None)
    tmp_db.upsert_transaction("item_1", _make_tx("tx1", amount=5.00,
                                                 primary_category="FOOD_AND_DRINK"))
    tmp_db.upsert_transaction("item_1", _make_tx("tx2", amount=50.00,
                                                 primary_category="FOOD_AND_DRINK"))
    tmp_db.upsert_transaction("item_1", _make_tx("tx3", amount=100.00,
                                                 primary_category="SHOPPING"))

    result = tmp_db.aggregate_transactions("2026-01-01", "2026-12-31", group_by="category")

    by_grp = {r["grp"]: r for r in result}
    assert by_grp["FOOD_AND_DRINK"]["total"] == 55.00
    assert by_grp["FOOD_AND_DRINK"]["count"] == 2
    assert by_grp["SHOPPING"]["total"] == 100.00


def test_aggregate_invalid_group_by_raises(tmp_db):
    with pytest.raises(ValueError, match="group_by must be one of"):
        tmp_db.aggregate_transactions("2026-01-01", "2026-12-31", group_by="bogus")


def test_link_session_lifecycle(tmp_db):
    tmp_db.save_link_session("link_tok", "https://plaid.example/hosted")
    session = tmp_db.get_link_session("link_tok")
    assert session["status"] == "pending"
    assert session["hosted_url"] == "https://plaid.example/hosted"

    tmp_db.save_item("item_X", "tok_X", None, None, None)
    tmp_db.complete_link_session("link_tok", public_token="pub_X", item_id="item_X")
    session = tmp_db.get_link_session("link_tok")
    assert session["status"] == "completed"
    assert session["item_id"] == "item_X"
    assert session["hosted_url"] is None


def test_purge_item_state_removes_derived_rows_and_preserves_other_items(tmp_db):
    for item_id, account_id in (("item_1", "acct_1"), ("item_2", "acct_2")):
        tmp_db.save_item(item_id, f"token-{item_id}", None, None, ["transactions"])
        tmp_db.upsert_account(item_id, {"account_id": account_id, "name": "Checking"})
        tmp_db.set_cursor(item_id, f"cursor-{item_id}")
        tmp_db.upsert_transaction(
            item_id,
            {
                "transaction_id": f"tx-{item_id}",
                "account_id": account_id,
                "amount": 10,
                "date": "2026-04-01",
                "name": "test",
                "pending": False,
            },
        )
        link_token = f"link-{item_id}"
        tmp_db.save_link_session(link_token, f"https://example.test/{item_id}")
        tmp_db.complete_link_session(link_token, f"public-{item_id}", item_id)

    tmp_db.purge_item_state("item_1")

    assert tmp_db.get_runtime_token("item_1") is None
    assert tmp_db.get_cursor("item_1") is None
    assert [account["account_id"] for account in tmp_db.list_accounts()] == ["acct_2"]
    assert [
        row["transaction_id"] for row in tmp_db.query_transactions("2026-01-01", "2026-12-31")
    ] == ["tx-item_2"]
    assert tmp_db.get_link_session("link-item_1") is None
    remaining_session = tmp_db.get_link_session("link-item_2")
    assert remaining_session["status"] == "completed"
    assert remaining_session["hosted_url"] is None
    assert remaining_session["item_id"] == "item_2"


def test_purge_item_state_rolls_back_all_deletes_on_failure(tmp_db):
    tmp_db.save_item("item_1", "token-1", None, None, ["transactions"])
    tmp_db.upsert_account("item_1", {"account_id": "acct_1"})
    tmp_db.set_cursor("item_1", "cursor-1")
    tmp_db.upsert_transaction(
        "item_1",
        {"transaction_id": "tx-1", "account_id": "acct_1", "date": "2026-04-01"},
    )
    tmp_db.save_link_session("link-1", "https://example.test/1")
    tmp_db.complete_link_session("link-1", "public-1", "item_1")
    tmp_db._conn.execute(
        """CREATE TRIGGER reject_item_delete BEFORE DELETE ON items
           WHEN OLD.item_id = 'item_1'
           BEGIN SELECT RAISE(ABORT, 'test abort'); END"""
    )

    with pytest.raises(sqlite3.IntegrityError, match="test abort"):
        tmp_db.purge_item_state("item_1")

    assert tmp_db.get_runtime_token("item_1") == "token-1"
    assert tmp_db.get_cursor("item_1") == "cursor-1"
    assert [account["account_id"] for account in tmp_db.list_accounts()] == ["acct_1"]
    assert [
        row["transaction_id"] for row in tmp_db.query_transactions("2026-01-01", "2026-12-31")
    ] == ["tx-1"]
    assert tmp_db.get_link_session("link-1")["item_id"] == "item_1"


def test_delete_transaction(tmp_db):
    tmp_db.save_item("item_1", "tok_1", None, None, None)
    tmp_db.upsert_transaction("item_1", _make_tx("tx1"))
    assert len(tmp_db.query_transactions(start_date="2026-01-01", end_date="2026-12-31")) == 1

    tmp_db.delete_transaction("tx1")
    assert tmp_db.query_transactions(start_date="2026-01-01", end_date="2026-12-31") == []


def test_get_account_item(tmp_db):
    tmp_db.save_item("item_1", "tok_1", None, None, None)
    tmp_db.upsert_account("item_1", {"account_id": "acct_A", "name": "Savings"})
    assert tmp_db.get_account_item("acct_A") == "item_1"
    assert tmp_db.get_account_item("acct_missing") is None


# ---------- account overrides ------------------------------------------------


def test_account_override_upsert_and_get(tmp_db):
    tmp_db.save_account_override(
        "acct_1", effective_apr=0.0, promo_expires="2027-01-01", note="0% promo"
    )
    got = tmp_db.get_account_override("acct_1")
    assert got["effective_apr"] == 0.0
    assert got["promo_expires"] == "2027-01-01"
    assert got["note"] == "0% promo"


def test_account_override_partial_update_preserves_other_fields(tmp_db):
    tmp_db.save_account_override(
        "acct_1", effective_apr=0.0, promo_expires="2027-01-01", note="original"
    )
    # Update only the note — APR and promo date should stick via COALESCE.
    tmp_db.save_account_override("acct_1", note="updated")
    got = tmp_db.get_account_override("acct_1")
    assert got["effective_apr"] == 0.0
    assert got["promo_expires"] == "2027-01-01"
    assert got["note"] == "updated"


def test_clear_account_override_returns_bool(tmp_db):
    tmp_db.save_account_override("acct_1", effective_apr=12.5)
    assert tmp_db.clear_account_override("acct_1") is True
    assert tmp_db.get_account_override("acct_1") is None
    # Second call should be a no-op.
    assert tmp_db.clear_account_override("acct_1") is False


def test_list_account_overrides(tmp_db):
    tmp_db.save_account_override("acct_1", effective_apr=0.0)
    tmp_db.save_account_override("acct_2", effective_apr=18.5)
    result = tmp_db.list_account_overrides()
    assert {r["account_id"] for r in result} == {"acct_1", "acct_2"}


# ---------- external debts ---------------------------------------------------


def test_external_debt_lifecycle(tmp_db):
    tmp_db.add_external_debt(
        debt_id="ext_1",
        name="Affirm - Couch",
        balance=800.00,
        apr=0.0,
        minimum_payment=100.00,
        next_payment_due_date="2026-05-15",
        promo_expires="2027-06-01",
        note="0% Affirm financing",
    )
    debts = tmp_db.list_external_debts()
    assert len(debts) == 1
    assert debts[0]["name"] == "Affirm - Couch"
    assert debts[0]["balance"] == 800.00
    assert debts[0]["apr"] == 0.0


def test_external_debt_update_partial(tmp_db):
    tmp_db.add_external_debt(
        debt_id="ext_1",
        name="Medical",
        balance=1500.00,
        apr=0.0,
        minimum_payment=50.00,
    )
    assert tmp_db.update_external_debt("ext_1", balance=1200.00) is True
    debts = {d["debt_id"]: d for d in tmp_db.list_external_debts()}
    assert debts["ext_1"]["balance"] == 1200.00
    # Other fields untouched.
    assert debts["ext_1"]["name"] == "Medical"
    assert debts["ext_1"]["apr"] == 0.0


def test_external_debt_update_no_fields_returns_false(tmp_db):
    tmp_db.add_external_debt("ext_1", "X", 100.0, 5.0)
    assert tmp_db.update_external_debt("ext_1") is False  # no updates


def test_remove_external_debt(tmp_db):
    tmp_db.add_external_debt("ext_1", "X", 100.0, 5.0)
    assert tmp_db.remove_external_debt("ext_1") is True
    assert tmp_db.list_external_debts() == []
    assert tmp_db.remove_external_debt("ext_1") is False
