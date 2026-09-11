"""Plaid read-provider factory used by the stdio runtime."""

from __future__ import annotations

from ..config import Config
from ..storage import Storage
from .base import Provider


def build_provider(config: Config, storage: Storage | None = None) -> Provider:
    provider_name = (config.provider or "plaid").strip().lower()

    if provider_name == "plaid":
        from .plaid import PlaidProvider

        if storage is None:
            raise ValueError(
                "PlaidProvider requires a Storage instance — "
                "pass storage=Storage(config.db_path) to build_provider()."
            )
        return PlaidProvider(storage, config)

    raise ValueError(
        f"Unknown provider {provider_name!r}; set PROVIDER=plaid."
    )
