"""Shared helpers for the MCP server layer.

Centralizes the provider-agnostic plumbing used by multiple tools:

- ``list_enrollments`` — resolve the set of ``Enrollment`` objects to query,
  based on the Plaid SQLite ``items`` cache.

Keeping this logic out of ``server.py`` keeps access-token resolution inside
the runtime provider plumbing and away from MCP return values.
"""

from __future__ import annotations

from .config import Config
from .providers import Capability, Enrollment, Provider
from .storage import Storage


def list_enrollments(config: Config, storage: Storage) -> list[Enrollment]:
    """Return every active ``Enrollment`` for the configured provider.

    Plaid: one metadata-only enrollment per ``items`` row. Credential
    decryption happens in provider calls, not during enumeration.
    """
    provider_name = (config.provider or "plaid").strip().lower()
    if provider_name == "plaid":
        out: list[Enrollment] = []
        items = {i["item_id"]: i for i in storage.list_items()}
        for item_id, item in items.items():
            out.append(
                Enrollment(
                    id=item_id,
                    institution_id=item.get("institution_id"),
                    institution_name=item.get("institution_name"),
                    provider="plaid",
                )
            )
        return out

    raise RuntimeError(
        f"Unknown provider {provider_name!r}; set PROVIDER=plaid."
    )


def require_capability(provider: Provider, capability: Capability) -> None:
    """Retained helper for future provider expansion; runtime is Plaid-only."""
    caps = provider.capabilities()
    if capability in caps:
        return
    raise RuntimeError(
        f"Tool requires the {capability.value!r} capability; the active provider "
        f"({provider.name!r}) does not support it. "
        "Configure a provider that supports this capability and retry."
    )
