"""Stable, non-sensitive provider errors returned by runtime tools."""

from __future__ import annotations


def safe_provider_error(exc: Exception, provider: str = "Plaid") -> str:
    return f"{provider} read failed: {type(exc).__name__}"
