"""Small authenticated-encryption primitives for local credential storage."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
NONCE_BYTES = 12


class CredentialError(RuntimeError):
    """Raised when protected credential material is unavailable or invalid."""


class CredentialStoreError(CredentialError):
    """Raised when the encrypted credential store cannot be read safely."""


def create_key(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    key = AESGCM.generate_key(bit_length=256)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return key


def load_key(path: Path, *, create: bool = False) -> bytes:
    if not path.exists():
        if not create:
            raise CredentialError(f"Master key is unavailable: {path}")
        return create_key(path)
    if path.is_symlink() or not path.is_file():
        raise CredentialError("Master key path is not a regular file")
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise CredentialError("Master key permissions must be owner-only")
    key = path.read_bytes()
    if len(key) != KEY_BYTES:
        raise CredentialError("Master key has an invalid length")
    return key


def encrypt(key: bytes, value: str, *, purpose: str) -> tuple[bytes, bytes]:
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, value.encode(), purpose.encode())
    return nonce, ciphertext


def decrypt(key: bytes, nonce: bytes, ciphertext: bytes, *, purpose: str) -> str:
    try:
        value = AESGCM(key).decrypt(nonce, ciphertext, purpose.encode())
        return value.decode()
    except Exception as exc:  # noqa: BLE001
        raise CredentialError("Protected credential could not be decrypted") from exc


def load_database_secret(db_path: Path, key_path: Path, name: str) -> str | None:
    """Read one encrypted secret without exposing it through configuration files."""
    try:
        db_info = db_path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise CredentialStoreError("Credential store is unavailable") from None
    if not stat.S_ISREG(db_info.st_mode):
        raise CredentialStoreError("Credential store is unavailable")
    key = load_key(key_path)
    try:
        conn = sqlite3.connect(db_path)
        try:
            has_secrets_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'secrets'"
            ).fetchone()
            if has_secrets_table:
                row = conn.execute(
                    "SELECT nonce, ciphertext FROM secrets WHERE name = ?", (name,)
                ).fetchone()
            else:
                has_application_tables = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite_%' LIMIT 1"
                ).fetchone()
                if has_application_tables:
                    raise CredentialStoreError("Credential store is unavailable")
                row = None
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        raise CredentialStoreError("Credential store is unavailable") from None
    if not row:
        return None
    return decrypt(key, row[0], row[1], purpose=f"secret:{name}")
