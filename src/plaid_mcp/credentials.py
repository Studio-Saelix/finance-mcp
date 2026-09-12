"""Validation for credentials sent through Plaid's HTTP authentication path."""

from __future__ import annotations


class CredentialValidationError(ValueError):
    """A credential is not safe to persist or send to Plaid."""


def validate_credential(value: str, label: str) -> str:
    """Return a credential unchanged, or raise a value-free validation error."""
    if not isinstance(value, str) or not value:
        raise CredentialValidationError(f"{label} must not be empty")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise CredentialValidationError(f"{label} contains unsupported/control characters")
    if value != value.strip():
        raise CredentialValidationError(f"{label} must not have leading or trailing whitespace")
    if not value.isascii():
        raise CredentialValidationError(
            f"{label} must use printable ASCII for safe HTTP authentication"
        )
    return value
