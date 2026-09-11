"""Credential-at-rest and failure-closed tests."""

from __future__ import annotations

import sqlite3

import pytest

from plaid_mcp.crypto import CredentialError, create_key


def test_access_token_and_secrets_are_encrypted(tmp_db):
    tmp_db.save_secret("plaid_secret", "fake-secret")
    tmp_db.save_item("item_1", "fake-access-token", "ins", "Bank", ["transactions"])
    raw = sqlite3.connect(tmp_db.db_path)
    try:
        rows = raw.execute("SELECT * FROM items").fetchone()
        secret = raw.execute("SELECT * FROM secrets").fetchone()
    finally:
        raw.close()
    assert b"fake-access-token" not in repr(rows).encode()
    assert b"fake-secret" not in repr(secret).encode()
    assert tmp_db.get_runtime_token("item_1") == "fake-access-token"


def test_wrong_key_fails_closed(tmp_db, tmp_path):
    tmp_db.save_item("item_1", "fake-access-token", None, None, None)
    wrong_key = tmp_path / "wrong.key"
    create_key(wrong_key)
    from plaid_mcp.storage import Storage

    other = Storage(tmp_db.db_path, wrong_key, create_key=False)
    try:
        with pytest.raises(CredentialError):
            other.get_runtime_token("item_1")
    finally:
        other.close()
