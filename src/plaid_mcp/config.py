"""Configuration — loads env vars and produces Plaid SDK enum lists."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# Plaid retired the Development environment in late 2024; new accounts get a
# Sandbox + a limited Production trial. We keep `development` as an alias that
# points at Production so older configs still work, but treat Production as the
# primary "real data" environment.
_PLAID_ENVS = {
    "sandbox": "https://sandbox.plaid.com",
    "production": "https://production.plaid.com",
    "development": "https://production.plaid.com",  # legacy alias
}


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(path))).resolve()


@dataclass
class Config:
    client_id: str
    secret: str
    env: str = "production"
    # Products Plaid MUST satisfy at link time. Every institution linked has to
    # support all of these, otherwise the Link flow rejects the bank.
    products: list[str] = field(default_factory=lambda: ["transactions"])
    # "Nice-to-have" products: requested if the institution supports them, and
    # the link still succeeds if not. Keeps a single server config usable
    # across banks (Citi = no investments), brokers (Fidelity = no liabilities),
    # etc. without requiring separate .env files per bank.
    optional_products: list[str] = field(
        default_factory=lambda: ["investments", "liabilities"]
    )
    country_codes: list[str] = field(default_factory=lambda: ["US"])
    client_name: str = "Studio Saelix Finance MCP"
    db_path: Path = field(default_factory=lambda: _expand("~/.plaid-mcp/plaid.db"))
    webhook_url: str | None = None

    # The runtime reads financial data through Plaid.
    provider: str = "plaid"

    @property
    def host(self) -> str:
        try:
            return _PLAID_ENVS[self.env]
        except KeyError as e:
            raise ValueError(
                f"PLAID_ENV must be one of {list(_PLAID_ENVS)} (got {self.env!r})"
            ) from e

    @classmethod
    def from_env(cls) -> Config:
        provider = os.getenv("PROVIDER", "plaid").strip().lower()

        # Plaid credentials are required for the runtime.
        client_id = os.getenv("PLAID_CLIENT_ID", "").strip()
        secret = os.getenv("PLAID_SECRET", "").strip()
        if provider == "plaid" and (not client_id or not secret):
            raise RuntimeError(
                "PLAID_CLIENT_ID and PLAID_SECRET must be set. "
                "Copy .env.example to .env and fill in your credentials."
            )

        env = os.getenv("PLAID_ENV", "production").strip().lower()
        products = [
            p.strip().lower()
            for p in os.getenv("PLAID_PRODUCTS", "transactions").split(",")
            if p.strip()
        ]
        optional_products = [
            p.strip().lower()
            for p in os.getenv(
                "PLAID_OPTIONAL_PRODUCTS", "investments,liabilities"
            ).split(",")
            if p.strip()
        ]
        # Anything already in the required list shouldn't appear in optional.
        optional_products = [p for p in optional_products if p not in products]
        country_codes = [
            c.strip().upper()
            for c in os.getenv("PLAID_COUNTRY_CODES", "US").split(",")
            if c.strip()
        ]

        return cls(
            client_id=client_id,
            secret=secret,
            env=env,
            products=products,
            optional_products=optional_products,
            country_codes=country_codes,
            client_name=os.getenv("PLAID_CLIENT_NAME", "Studio Saelix Finance MCP"),
            db_path=_expand(os.getenv("PLAID_MCP_DB", "~/.plaid-mcp/plaid.db")),
            webhook_url=os.getenv("PLAID_WEBHOOK_URL") or None,
            provider=provider,
        )

    def as_products(self):  # -> list[plaid.model.products.Products]
        from plaid.model.products import Products

        return [Products(p) for p in self.products]

    def as_optional_products(self):  # -> list[plaid.model.products.Products]
        from plaid.model.products import Products

        return [Products(p) for p in self.optional_products]

    def as_country_codes(self):  # -> list[plaid.model.country_code.CountryCode]
        from plaid.model.country_code import CountryCode

        return [CountryCode(c) for c in self.country_codes]
