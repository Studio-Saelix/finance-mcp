from __future__ import annotations

from click.testing import CliRunner

from plaid_mcp.admin import main
from plaid_mcp.storage import Storage


def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("PLAID_MCP_DB", raising=False)
    monkeypatch.delenv("PLAID_MASTER_KEY", raising=False)
    monkeypatch.delenv("PLAID_MCP_ALLOW_ENV_SECRETS", raising=False)
    monkeypatch.delenv("PLAID_ENV", raising=False)


def test_init_is_idempotent_and_hides_credentials(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    runner = CliRunner()
    first = runner.invoke(main, ["init"], input="client-fake\nsecret-fake\n")
    assert first.exit_code == 0, first.output
    assert "client-fake" not in first.output and "secret-fake" not in first.output
    second = runner.invoke(main, ["init"])
    assert second.exit_code == 0, second.output
    db = tmp_path / "data" / "studio-saelix-finance" / "finance.db"
    assert b"secret-fake" not in db.read_bytes()


def test_init_recovers_missing_secret_and_status_is_redacted(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["init"], input="client-fake\nsecret-fake\n").exit_code == 0
    db = tmp_path / "data" / "studio-saelix-finance" / "finance.db"
    key = tmp_path / "config" / "studio-saelix-finance" / "master.key"
    store = Storage(db, key, create_key=False)
    store.delete_secret("plaid_secret")
    store.close()
    recovered = runner.invoke(main, ["init"], input="replacement-secret\n")
    assert recovered.exit_code == 0, recovered.output
    status = runner.invoke(main, ["status"])
    assert status.exit_code == 0
    assert "sandbox" in status.output.lower()
    assert "secret-fake" not in status.output


def test_production_transition_requires_empty_sandbox_and_new_secret(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["init"], input="client-fake\nsandbox-secret\n").exit_code == 0
    db = tmp_path / "data" / "studio-saelix-finance" / "finance.db"
    key = tmp_path / "config" / "studio-saelix-finance" / "master.key"
    store = Storage(db, key, create_key=False)
    store.save_item("sandbox-item", "sandbox-token", "ins", "Bank", [])
    store.close()
    blocked = runner.invoke(main, ["use-production"], input="ENABLE PRODUCTION\n")
    assert blocked.exit_code != 0 and "Unlink all Sandbox" in blocked.output
    store = Storage(db, key, create_key=False)
    store.delete_item("sandbox-item")
    store.close()
    changed = runner.invoke(
        main, ["use-production"], input="ENABLE PRODUCTION\nproduction-secret\n"
    )
    assert changed.exit_code == 0, changed.output
    assert "production-secret" not in changed.output
    store = Storage(db, key, create_key=False)
    assert store.get_secret("plaid_secret") == "production-secret"
    store.close()


def test_unlink_confirmation_and_failure_preserve_state(monkeypatch, tmp_path, mock_plaid_client):
    _isolated(monkeypatch, tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["init"], input="client\nsecret\n").exit_code == 0
    from plaid_mcp.paths import db_path, key_path
    store = Storage(db_path(), key_path(), create_key=False)
    store.save_item("item-1", "access-token", "ins", "Bank", [])
    store.close()
    mock_plaid_client.item_remove.side_effect = RuntimeError("provider detail")
    result = runner.invoke(main, ["unlink", "item-1"], input="y\n")
    assert result.exit_code != 0 and "preserved" in result.output
    store = Storage(db_path(), key_path(), create_key=False)
    assert store.get_runtime_token("item-1") == "access-token"
    store.close()


def test_force_local_purge_requires_explicit_phrase(monkeypatch, tmp_path, mock_plaid_client):
    _isolated(monkeypatch, tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["init"], input="client\nsecret\n").exit_code == 0
    from plaid_mcp.paths import db_path, key_path
    store = Storage(db_path(), key_path(), create_key=False)
    store.save_item("item-1", "access-token", "ins", "Bank", [])
    store.close()
    mock_plaid_client.item_remove.side_effect = RuntimeError("provider detail")
    result = runner.invoke(
        main, ["unlink", "item-1", "--force-local-purge"], input="PURGE item-1\n"
    )
    assert result.exit_code == 0
    store = Storage(db_path(), key_path(), create_key=False)
    assert store.list_items() == []
    store.close()
