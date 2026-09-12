"""Credential-at-rest and failure-closed tests."""

from __future__ import annotations

import sqlite3

import pytest

from plaid_mcp.crypto import (
    CredentialError,
    CredentialStoreError,
    create_key,
    load_database_secret,
)


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


def test_load_secret_from_uninitialized_database_returns_none(tmp_path):
    db_path = tmp_path / "bootstrap.db"
    sqlite3.connect(db_path).close()
    key_path = tmp_path / "bootstrap-master.key"
    create_key(key_path)

    assert load_database_secret(db_path, key_path, "plaid_client_id") is None


def test_missing_secrets_table_in_existing_app_database_fails_closed(tmp_path):
    db_path = tmp_path / "partial.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE items (item_id TEXT PRIMARY KEY)")
    conn.close()
    key_path = tmp_path / "partial-master.key"
    create_key(key_path)

    with pytest.raises(CredentialStoreError, match="Credential store is unavailable"):
        load_database_secret(db_path, key_path, "plaid_client_id")


def test_sqlite_operational_error_is_a_sanitized_store_failure(monkeypatch, tmp_path):
    db_path = tmp_path / "existing.db"
    db_path.touch()
    key_path = tmp_path / "diagnostic-master.key"
    create_key(key_path)

    def fail_open(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr("plaid_mcp.crypto.sqlite3.connect", fail_open)
    with pytest.raises(CredentialStoreError, match="Credential store is unavailable") as err:
        load_database_secret(db_path, key_path, "plaid_client_id")
    assert "disk I/O error" not in str(err.value)
