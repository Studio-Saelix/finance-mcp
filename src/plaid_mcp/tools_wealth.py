"""Investments, liabilities, identity, and income tools."""

from __future__ import annotations

from typing import Any

from .client import get_client
from .errors import safe_provider_error
from .storage import Storage


def _iter_items(storage: Storage):
    for item in storage.list_items():
        token = storage.get_runtime_token(item["item_id"])
        if token:
            yield item, token


def _date_str(d: Any) -> str | None:
    """Stringify a date-ish Plaid field, or None when the field is missing."""
    return str(d) if d else None


# ---- Investments ----------------------------------------------------------------


def get_holdings(storage: Storage, account_id: str | None = None) -> dict[str, Any]:
    """Current investment positions across every brokerage-type item."""
    from plaid.model.investments_holdings_get_request import (
        InvestmentsHoldingsGetRequest,
    )

    client = get_client()
    securities_by_id: dict[str, dict[str, Any]] = {}
    holdings: list[dict[str, Any]] = []

    for item, token in _iter_items(storage):
        try:
            resp = client.investments_holdings_get(
                InvestmentsHoldingsGetRequest(access_token=token)
            )
        except Exception as e:  # noqa: BLE001
            storage.set_item_error(item["item_id"], safe_provider_error(e))
            continue

        for sec in resp.get("securities", []):
            sec_dict = sec.to_dict() if hasattr(sec, "to_dict") else dict(sec)
            securities_by_id[sec_dict["security_id"]] = sec_dict

        for h in resp.get("holdings", []):
            h = h.to_dict() if hasattr(h, "to_dict") else dict(h)
            if account_id and h.get("account_id") != account_id:
                continue
            sec = securities_by_id.get(h.get("security_id", ""), {})
            holdings.append(
                {
                    "account_id": h.get("account_id"),
                    "institution_name": item.get("institution_name"),
                    "ticker": sec.get("ticker_symbol"),
                    "name": sec.get("name"),
                    "type": sec.get("type"),
                    "quantity": h.get("quantity"),
                    "price": h.get("institution_price"),
                    "value": h.get("institution_value"),
                    "cost_basis": h.get("cost_basis"),
                    "iso_currency": h.get("iso_currency_code"),
                }
            )
        storage.set_item_error(item["item_id"], None)
    return {"holdings": holdings}


def get_investment_transactions(
    storage: Storage,
    start_date: str,
    end_date: str,
    account_id: str | None = None,
    limit: int = 250,
) -> dict[str, Any]:
    """Buys, sells, dividends, fees across brokerage items."""
    from plaid.model.investments_transactions_get_request import (
        InvestmentsTransactionsGetRequest,
    )
    from plaid.model.investments_transactions_get_request_options import (
        InvestmentsTransactionsGetRequestOptions,
    )

    client = get_client()
    transactions: list[dict[str, Any]] = []
    securities: dict[str, dict[str, Any]] = {}

    for item, token in _iter_items(storage):
        options = InvestmentsTransactionsGetRequestOptions(count=min(limit, 500))
        try:
            resp = client.investments_transactions_get(
                InvestmentsTransactionsGetRequest(
                    access_token=token,
                    start_date=start_date,
                    end_date=end_date,
                    options=options,
                )
            )
        except Exception as e:  # noqa: BLE001
            storage.set_item_error(item["item_id"], safe_provider_error(e))
            continue

        for sec in resp.get("securities", []):
            sec_dict = sec.to_dict() if hasattr(sec, "to_dict") else dict(sec)
            securities[sec_dict["security_id"]] = sec_dict

        for tx in resp.get("investment_transactions", []):
            tx = tx.to_dict() if hasattr(tx, "to_dict") else dict(tx)
            if account_id and tx.get("account_id") != account_id:
                continue
            sec = securities.get(tx.get("security_id", ""), {})
            transactions.append(
                {
                    "date": str(tx.get("date")),
                    "account_id": tx.get("account_id"),
                    "institution_name": item.get("institution_name"),
                    "type": tx.get("type"),
                    "subtype": tx.get("subtype"),
                    "ticker": sec.get("ticker_symbol"),
                    "name": tx.get("name"),
                    "quantity": tx.get("quantity"),
                    "price": tx.get("price"),
                    "amount": tx.get("amount"),
                    "fees": tx.get("fees"),
                    "iso_currency": tx.get("iso_currency_code"),
                }
            )
        storage.set_item_error(item["item_id"], None)

    return {"transactions": transactions}


# ---- Liabilities ----------------------------------------------------------------


def get_liabilities(storage: Storage) -> dict[str, Any]:
    """Credit cards, student loans, mortgages with APR / balances / due dates."""
    from plaid.model.liabilities_get_request import LiabilitiesGetRequest

    client = get_client()
    credit: list[dict[str, Any]] = []
    student: list[dict[str, Any]] = []
    mortgage: list[dict[str, Any]] = []

    for item, token in _iter_items(storage):
        try:
            resp = client.liabilities_get(LiabilitiesGetRequest(access_token=token))
        except Exception as e:  # noqa: BLE001
            storage.set_item_error(item["item_id"], safe_provider_error(e))
            continue

        liabilities = (resp.get("liabilities") or {})
        for cc in liabilities.get("credit", []) or []:
            cc = cc.to_dict() if hasattr(cc, "to_dict") else dict(cc)
            aprs = cc.get("aprs") or []
            credit.append(
                {
                    "account_id": cc.get("account_id"),
                    "institution_name": item.get("institution_name"),
                    "last_statement_balance": cc.get("last_statement_balance"),
                    "last_payment_amount": cc.get("last_payment_amount"),
                    "last_payment_date": _date_str(cc.get("last_payment_date")),
                    "minimum_payment_amount": cc.get("minimum_payment_amount"),
                    "next_payment_due_date": _date_str(cc.get("next_payment_due_date")),
                    "aprs": [
                        {
                            "type": a.get("apr_type"),
                            "percentage": a.get("apr_percentage"),
                            "balance_subject_to_apr": a.get("balance_subject_to_apr"),
                        }
                        for a in aprs
                    ],
                }
            )

        for sl in liabilities.get("student", []) or []:
            sl = sl.to_dict() if hasattr(sl, "to_dict") else dict(sl)
            student.append(
                {
                    "account_id": sl.get("account_id"),
                    "institution_name": item.get("institution_name"),
                    "outstanding_balance": sl.get("outstanding_balance"),
                    "origination_principal": sl.get("origination_principal_amount"),
                    "origination_date": _date_str(sl.get("origination_date")),
                    "interest_rate_percentage": sl.get("interest_rate_percentage"),
                    "loan_status": (sl.get("loan_status") or {}).get("type"),
                    "minimum_payment_amount": sl.get("minimum_payment_amount"),
                    "next_payment_due_date": _date_str(sl.get("next_payment_due_date")),
                    "expected_payoff_date": _date_str(sl.get("expected_payoff_date")),
                    "servicer": sl.get("servicer_address"),
                }
            )

        for m in liabilities.get("mortgage", []) or []:
            m = m.to_dict() if hasattr(m, "to_dict") else dict(m)
            mortgage.append(
                {
                    "account_id": m.get("account_id"),
                    "institution_name": item.get("institution_name"),
                    "origination_principal": m.get("origination_principal_amount"),
                    "origination_date": _date_str(m.get("origination_date")),
                    "interest_rate_percentage":
                        (m.get("interest_rate") or {}).get("percentage"),
                    "interest_rate_type": (m.get("interest_rate") or {}).get("type"),
                    "loan_term": m.get("loan_term"),
                    "maturity_date": _date_str(m.get("maturity_date")),
                    "next_monthly_payment": m.get("next_monthly_payment"),
                    "next_payment_due_date": _date_str(m.get("next_payment_due_date")),
                    "past_due_amount": m.get("past_due_amount"),
                }
            )
        storage.set_item_error(item["item_id"], None)

    return {"credit_cards": credit, "student_loans": student, "mortgages": mortgage}


# ---- Identity -------------------------------------------------------------------


def get_identity(storage: Storage, account_id: str | None = None) -> dict[str, Any]:
    """Account holder name, email, phone, address as reported by each institution."""
    from plaid.model.identity_get_request import IdentityGetRequest

    client = get_client()
    out: list[dict[str, Any]] = []

    for item, token in _iter_items(storage):
        try:
            resp = client.identity_get(IdentityGetRequest(access_token=token))
        except Exception as e:  # noqa: BLE001
            storage.set_item_error(item["item_id"], safe_provider_error(e))
            continue

        for acct in resp.get("accounts", []):
            acct = acct.to_dict() if hasattr(acct, "to_dict") else dict(acct)
            if account_id and acct.get("account_id") != account_id:
                continue
            owners = acct.get("owners") or []
            out.append(
                {
                    "account_id": acct.get("account_id"),
                    "institution_name": item.get("institution_name"),
                    "account_name": acct.get("name"),
                    "owners": [
                        {
                            "names": o.get("names"),
                            "emails": [e.get("data") for e in (o.get("emails") or [])],
                            "phone_numbers": [
                                p.get("data") for p in (o.get("phone_numbers") or [])
                            ],
                            "addresses": [
                                {
                                    "city": (a.get("data") or {}).get("city"),
                                    "region": (a.get("data") or {}).get("region"),
                                    "postal_code": (a.get("data") or {}).get("postal_code"),
                                    "country": (a.get("data") or {}).get("country"),
                                }
                                for a in (o.get("addresses") or [])
                            ],
                        }
                        for o in owners
                    ],
                }
            )
        storage.set_item_error(item["item_id"], None)
    return {"identities": out}


# ---- Income ---------------------------------------------------------------------


def get_income(storage: Storage) -> dict[str, Any]:
    """Best-effort income summary using the bank-income product.

    Requires that you've enabled Income via Plaid and requested it during Link.
    Falls back to an empty list if unsupported.
    """
    try:
        from plaid.model.credit_bank_income_get_request import (
            CreditBankIncomeGetRequest,
        )
    except Exception:
        return {"income_streams": [], "note": "Income product SDK not available"}

    client = get_client()
    out: list[dict[str, Any]] = []

    for item, token in _iter_items(storage):
        try:
            resp = client.credit_bank_income_get(
                CreditBankIncomeGetRequest(access_token=token)
            )
        except Exception as e:  # noqa: BLE001
            # Income often isn't enabled for Development — don't treat as fatal.
            out.append(
                {
                    "item_id": item["item_id"],
                    "institution_name": item.get("institution_name"),
                    "error": safe_provider_error(e),
                }
            )
            continue

        bank_income = resp.get("bank_income", []) or []
        for bi in bank_income:
            bi = bi.to_dict() if hasattr(bi, "to_dict") else dict(bi)
            for stream in bi.get("bank_income_sources", []) or []:
                out.append(
                    {
                        "institution_name": item.get("institution_name"),
                        "source": stream.get("income_source_id"),
                        "name": stream.get("income_description"),
                        "category": stream.get("income_category"),
                        "pay_frequency": stream.get("pay_frequency"),
                        "total_amount": stream.get("total_amount"),
                        "average_monthly": stream.get("average_monthly_income_amount"),
                        "start_date": _date_str(stream.get("start_date")),
                        "end_date": _date_str(stream.get("end_date")),
                    }
                )
    return {"income_streams": out}
