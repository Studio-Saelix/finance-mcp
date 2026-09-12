from __future__ import annotations

import os

import pytest

from plaid_mcp.config import Config
from plaid_mcp.crypto import CredentialError
from plaid_mcp.paths import ensure_private_dir
from plaid_mcp.storage import Storage


def test_sensitive_directory_rejects_symlink(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(RuntimeError, match="must not be a symlink"):
        ensure_private_dir(link)


def test_database_rejects_symlink(tmp_path):
    real = tmp_path / "real.db"
    real.touch()
    link = tmp_path / "finance.db"
    link.symlink_to(real)
    with pytest.raises(CredentialError, match="must not be a symlink"):
        Storage(link, tmp_path / "master.key")


def test_normal_operation_ignores_environment_security_overrides(monkeypatch, tmp_path):
    config_home = tmp_path / "config"
    app_config = config_home / "studio-saelix-finance"
    app_config.mkdir(parents=True)
    (app_config / "config.toml").write_text('plaid_env = "production"\n')
    os.chmod(app_config / "config.toml", 0o600)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("PLAID_ENV", "sandbox")
    monkeypatch.setenv("PLAID_MCP_DB", str(tmp_path / "unsafe.db"))
    monkeypatch.setenv("PLAID_MASTER_KEY", str(tmp_path / "unsafe.key"))
    monkeypatch.delenv("PLAID_MCP_ALLOW_ENV_SECRETS", raising=False)
    cfg = Config.from_env(require_credentials=False)
    assert cfg.env == "production"
    assert cfg.db_path != tmp_path / "unsafe.db"
    assert cfg.master_key_path != tmp_path / "unsafe.key"
