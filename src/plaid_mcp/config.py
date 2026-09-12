"""Configuration — loads env vars and produces Plaid SDK enum lists."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import tomllib

from .paths import config_dir, config_path, ensure_private_dir, ensure_private_file, key_path
from .paths import db_path as default_db_path

# Plaid retired the Development environment in late 2024; new accounts get a
# Sandbox + a limited Production trial. We keep `development` as an alias that
# points at Production so older configs still work, but treat Production as the
# primary "real data" environment.
_PLAID_ENVS = {
    "sandbox": "https://sandbox.plaid.com",
    "production": "https://production.plaid.com",
    "development": "https://production.plaid.com",  # legacy alias
}
_CREDENTIAL_ENVS = frozenset(("sandbox", "production"))


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(path))).resolve()


@dataclass
class Config:
    client_id: str = ""
    secret: str = ""
    env: str = "sandbox"
    # Products Plaid MUST satisfy at link time. Every institution linked has to
    # support all of these, otherwise the Link flow rejects the bank.
    products: list[str] = field(default_factory=lambda: ["transactions"])
    # "Nice-to-have" products: requested if the institution supports them, and
    # the link still succeeds if not. Keeps a single server config usable
    # across banks (Citi = no investments), brokers (Fidelity = no liabilities),
    # etc. without requiring separate .env files per bank.
    optional_products: list[str] = field(default_factory=lambda: ["investments", "liabilities"])
    country_codes: list[str] = field(default_factory=lambda: ["US"])
    client_name: str = "Studio Saelix Finance MCP"
    db_path: Path = field(default_factory=default_db_path)
    master_key_path: Path = field(default_factory=key_path)
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

    @property
    def credential_secret_name(self) -> str:
        if self.env not in _CREDENTIAL_ENVS:
            raise ValueError(
                f"Credential storage does not support environment {self.env!r}; "
                "use sandbox or production"
            )
        return f"plaid_secret_{self.env}"

    @classmethod
    def from_env(cls, *, require_credentials: bool = True) -> Config:
        test_overrides = os.getenv("PLAID_MCP_ALLOW_ENV_SECRETS") == "1"
        provider = os.getenv("PROVIDER", "plaid").strip().lower()
        file_config: dict = {}
        cfg_path = config_path()
        try:
            ensure_private_dir(config_dir())
        except RuntimeError as exc:
            raise RuntimeError(str(exc)) from exc
        if cfg_path.exists():
            try:
                ensure_private_file(cfg_path)
            except RuntimeError as exc:
                raise RuntimeError(str(exc)) from exc
            with cfg_path.open("rb") as fh:
                file_config = tomllib.load(fh)

        client_id = ""
        secret = ""
        env = (os.getenv("PLAID_ENV") if test_overrides else None) or file_config.get(
            "plaid_env", "sandbox"
        )
        env = env.strip().lower()
        config_db = (
            _expand(os.getenv("PLAID_MCP_DB"))
            if test_overrides and os.getenv("PLAID_MCP_DB")
            else default_db_path()
        )
        config_key = (
            _expand(os.getenv("PLAID_MASTER_KEY"))
            if test_overrides and os.getenv("PLAID_MASTER_KEY")
            else key_path()
        )

        def setting(name: str, default: str) -> str:
            return os.getenv(name, default) if test_overrides else default

        products = [
            p.strip().lower()
            for p in setting("PLAID_PRODUCTS", file_config.get("products", "transactions")).split(
                ","
            )
            if p.strip()
        ]
        optional_products = [
            p.strip().lower()
            for p in setting(
                "PLAID_OPTIONAL_PRODUCTS",
                file_config.get("optional_products", "investments,liabilities"),
            ).split(",")
            if p.strip()
        ]
        # Anything already in the required list shouldn't appear in optional.
        optional_products = [p for p in optional_products if p not in products]
        country_codes = [
            c.strip().upper()
            for c in setting("PLAID_COUNTRY_CODES", file_config.get("country_codes", "CA")).split(
                ","
            )
            if c.strip()
        ]

        cfg = cls(
            client_id=client_id,
            secret=secret,
            env=env,
            products=products,
            optional_products=optional_products,
            country_codes=country_codes,
            client_name=setting(
                "PLAID_CLIENT_NAME", file_config.get("client_name", "Studio Saelix Finance MCP")
            ),
            db_path=config_db,
            master_key_path=config_key,
            webhook_url=(os.getenv("PLAID_WEBHOOK_URL") if test_overrides else None) or None,
            provider=provider,
        )
        from .crypto import CredentialError, CredentialStoreError, load_database_secret

        try:
            cfg.client_id = (
                load_database_secret(cfg.db_path, cfg.master_key_path, "plaid_client_id") or ""
            )
            if cfg.env in _CREDENTIAL_ENVS:
                cfg.secret = (
                    load_database_secret(
                        cfg.db_path, cfg.master_key_path, f"plaid_secret_{cfg.env}"
                    )
                    or ""
                )
        except CredentialStoreError:
            raise
        except CredentialError:
            if require_credentials:
                raise
        # Environment credentials are intentionally test/development-only and
        # require an explicit opt-in; Hermes never needs this path.
        if test_overrides:
            cfg.client_id = os.getenv("PLAID_CLIENT_ID", "")
            cfg.secret = os.getenv("PLAID_SECRET", "")
        if require_credentials and (not cfg.client_id or not cfg.secret):
            raise RuntimeError("Finance MCP is not initialized. Run `studio-saelix-finance init`.")
        if require_credentials:
            from .credentials import CredentialValidationError, validate_credential

            try:
                validate_credential(cfg.client_id, "Plaid client ID")
                validate_credential(cfg.secret, "Plaid secret")
            except CredentialValidationError as exc:
                raise RuntimeError(str(exc)) from exc
        return cfg

    def as_products(self):  # -> list[plaid.model.products.Products]
        from plaid.model.products import Products

        return [Products(p) for p in self.products]

    def as_optional_products(self):  # -> list[plaid.model.products.Products]
        from plaid.model.products import Products

        return [Products(p) for p in self.optional_products]

    def as_country_codes(self):  # -> list[plaid.model.country_code.CountryCode]
        from plaid.model.country_code import CountryCode

        return [CountryCode(c) for c in self.country_codes]
