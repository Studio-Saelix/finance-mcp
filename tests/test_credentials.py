from __future__ import annotations

import pytest

from plaid_mcp.credentials import CredentialValidationError, validate_credential


@pytest.mark.parametrize(
    ("value", "sentinel"),
    [
        ("", None),
        ("secret-sentinel\x00credential", "secret-sentinel"),
        ("secret-sentinel\x7fcredential", "secret-sentinel"),
        ("secret-sentinel\rcredential", "secret-sentinel"),
        ("secret-sentinel\ncredential", "secret-sentinel"),
    ],
)
def test_credential_validation_rejects_empty_and_control_characters(value, sentinel):
    with pytest.raises(CredentialValidationError) as error:
        validate_credential(value, "Plaid client ID")
    if sentinel:
        assert sentinel not in str(error.value)


@pytest.mark.parametrize(
    ("value", "sentinel"),
    [
        (" secret-sentinel", "secret-sentinel"),
        ("secret-sentinel ", "secret-sentinel"),
        ("secret-sentinel-é", "secret-sentinel"),
    ],
)
def test_credential_validation_rejects_unsafe_transport_values(value, sentinel):
    with pytest.raises(CredentialValidationError) as error:
        validate_credential(value, "Plaid secret")
    assert sentinel not in str(error.value)


def test_credential_validation_preserves_valid_printable_value():
    assert validate_credential("plaid credential-123", "Plaid secret") == ("plaid credential-123")
