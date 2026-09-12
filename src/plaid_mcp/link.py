"""Plaid Link helpers — creates a Hosted Link URL and exchanges the public_token
returned after the user completes the flow.

The flow is:
    1. create_hosted_link()          -> (link_token, hosted_url)
    2. user visits hosted_url in browser, completes OAuth with their bank
    3. complete_link(link_token)     -> exchanges public_token for access_token,
                                        persists item + accounts
"""

from __future__ import annotations

import time
from typing import Any

from plaid.model.item_public_token_exchange_request import (
    ItemPublicTokenExchangeRequest,
)
from plaid.model.link_token_create_hosted_link import LinkTokenCreateHostedLink
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.link_token_get_request import LinkTokenGetRequest

from .client import get_client
from .config import Config
from .storage import Storage


def create_hosted_link(
    storage: Storage,
    config: Config | None = None,
    user_id: str = "plaid-mcp-user",
) -> dict[str, Any]:
    """Create a Hosted Link session and return the URL the user should visit."""
    cfg = config or Config.from_env()
    client = get_client()

    # Required products must be supported by the bank or Link rejects it.
    # Optional products go in ``required_if_supported_products`` so we still
    # pull richer data (investments from Fidelity, liabilities from a credit-card
    # issuer, etc.) without blocking a plain checking account from linking.
    kwargs: dict[str, Any] = dict(
        client_name=cfg.client_name,
        language="en",
        country_codes=cfg.as_country_codes(),
        products=cfg.as_products(),
        user=LinkTokenCreateRequestUser(client_user_id=user_id),
        hosted_link=LinkTokenCreateHostedLink(),
    )
    optional = cfg.as_optional_products()
    if optional:
        kwargs["required_if_supported_products"] = optional
    if cfg.webhook_url:
        kwargs["webhook"] = cfg.webhook_url

    request = LinkTokenCreateRequest(**kwargs)

    response = client.link_token_create(request)
    link_token = response["link_token"]
    hosted_url = response.get("hosted_link_url")
    storage.save_link_session(link_token, hosted_url)

    return {
        "link_token": link_token,
        "hosted_url": hosted_url,
        "expiration": str(response.get("expiration")),
    }


def _poll_for_public_token(link_token: str, timeout_s: int = 300) -> str | None:
    """Poll Plaid's /link/token/get for the public_token after user completes Link.

    Per Plaid's Hosted Link docs, the response shape is::

        link_sessions[].results.item_add_results[]:
          - public_token
          - accounts[]
          - institution

    Webhooks are the recommended production path (SESSION_FINISHED event); this
    polling fallback is fine for local CLI use and small deployments.
    """
    client = get_client()
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        resp = client.link_token_get(LinkTokenGetRequest(link_token=link_token))
        for session in resp.get("link_sessions") or []:
            results = session.get("results") or {}
            # Modern shape (Multi-Item-aware): item_add_results is a list.
            for item_add in results.get("item_add_results") or []:
                public_token = item_add.get("public_token")
                if public_token:
                    return public_token
            # Legacy shape kept as a fallback for older accounts still on on_success.
            on_success = session.get("on_success") or {}
            public_token = on_success.get("public_token")
            if public_token:
                return public_token
        time.sleep(2)

    return None


def complete_link(
    storage: Storage,
    link_token: str,
    timeout_s: int = 300,
) -> dict[str, Any]:
    """Finalize linking: poll for public_token, exchange it, persist item + accounts."""
    from plaid.model.accounts_get_request import AccountsGetRequest
    from plaid.model.item_get_request import ItemGetRequest

    client = get_client()

    session = storage.get_link_session(link_token)
    if not session:
        raise RuntimeError("No pending Link session. Run the administrator link command first.")

    public_token = _poll_for_public_token(link_token, timeout_s=timeout_s)
    if not public_token:
        return {
            "status": "pending",
            "message": (
                "Link not yet completed in the browser. Finish the flow at "
                f"{session['hosted_url']} and try again."
            ),
        }

    exchange = client.item_public_token_exchange(
        ItemPublicTokenExchangeRequest(public_token=public_token)
    )
    access_token = exchange["access_token"]
    item_id = exchange["item_id"]

    # Fetch item + accounts so we can cache institution info and account list.
    item_info = client.item_get(ItemGetRequest(access_token=access_token))
    institution_id = item_info["item"].get("institution_id")
    institution_name = None

    if institution_id:
        from plaid.model.institutions_get_by_id_request import (
            InstitutionsGetByIdRequest,
        )

        inst = client.institutions_get_by_id(
            InstitutionsGetByIdRequest(
                institution_id=institution_id,
                country_codes=Config.from_env().as_country_codes(),
            )
        )
        institution_name = inst["institution"].get("name")

    storage.save_item(
        item_id=item_id,
        access_token=access_token,
        institution_id=institution_id,
        institution_name=institution_name,
        products=[str(p) for p in (item_info["item"].get("products") or [])],
    )

    accounts = client.accounts_get(AccountsGetRequest(access_token=access_token))
    for acct in accounts["accounts"]:
        storage.upsert_account(
            item_id,
            {
                "account_id": acct["account_id"],
                "name": acct.get("name"),
                "official_name": acct.get("official_name"),
                "type": str(acct.get("type")) if acct.get("type") else None,
                "subtype": str(acct.get("subtype")) if acct.get("subtype") else None,
                "mask": acct.get("mask"),
                "iso_currency": (acct.get("balances") or {}).get("iso_currency_code"),
            },
        )

    storage.complete_link_session(link_token, public_token, item_id)

    return {
        "status": "completed",
        "item_id": item_id,
        "institution_name": institution_name,
        "accounts": len(accounts["accounts"]),
    }
