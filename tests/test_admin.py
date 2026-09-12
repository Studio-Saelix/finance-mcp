from __future__ import annotations

import pytest
from click.testing import CliRunner
from plaid.exceptions import ApiException

from plaid_mcp.admin import main
from plaid_mcp.config import Config
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
    assert "Plaid client ID" not in second.output
    assert "Plaid secret" not in second.output
    db = tmp_path / "data" / "studio-saelix-finance" / "finance.db"
    assert b"secret-fake" not in db.read_bytes()


def test_init_recovers_missing_secret_and_status_is_redacted(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["init"], input="client-fake\nsecret-fake\n").exit_code == 0
    db = tmp_path / "data" / "studio-saelix-finance" / "finance.db"
    key = tmp_path / "config" / "studio-saelix-finance" / "master.key"
    store = Storage(db, key, create_key=False)
    store.delete_secret("plaid_secret_sandbox")
    store.close()
    recovered = runner.invoke(main, ["init"], input="replacement-secret\n")
    assert recovered.exit_code == 0, recovered.output
    status = runner.invoke(main, ["status"])
    assert status.exit_code == 0
    assert "sandbox" in status.output.lower()
    assert "secret-fake" not in status.output


def test_init_reprompts_for_malformed_new_client_id_without_echoing(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    result = CliRunner().invoke(
        main, ["init"], input="bad-client\x01id\nvalid-client\nvalid-secret\n"
    )
    assert result.exit_code == 0, result.output
    assert "contains unsupported/control characters" in result.output
    assert "bad-client" not in result.output
    assert "valid-client" not in result.output
    from plaid_mcp.paths import db_path, key_path

    storage = Storage(db_path(), key_path(), create_key=False)
    try:
        assert storage.get_secret("plaid_client_id") == "valid-client"
        assert storage.get_secret("plaid_secret_sandbox") == "valid-secret"
    finally:
        storage.close()


@pytest.mark.parametrize("environment", ["sandbox", "production"])
def test_init_repairs_malformed_stored_client_id_only(monkeypatch, tmp_path, environment):
    _isolated(monkeypatch, tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["init"], input="initial-client\nsandbox-secret\n").exit_code == 0
    if environment == "production":
        transitioned = runner.invoke(
            main,
            ["use-production"],
            input="ENABLE PRODUCTION\nproduction-secret\n",
        )
        assert transitioned.exit_code == 0, transitioned.output

    from plaid_mcp.paths import db_path, key_path

    storage = Storage(db_path(), key_path(), create_key=False)
    storage.save_secret("plaid_client_id", "malformed\x7fclient")
    if environment == "sandbox":
        storage.save_secret("plaid_secret_production", "other-env-secret")
    storage.save_item("kept-item", "kept-token", "institution", "Bank", [])
    storage.close()

    repaired = runner.invoke(main, ["init"], input="repaired-client\n")
    assert repaired.exit_code == 0, repaired.output
    assert "malformed" not in repaired.output
    assert Config.from_env(require_credentials=False).env == environment
    storage = Storage(db_path(), key_path(), create_key=False)
    try:
        assert storage.get_secret("plaid_client_id") == "repaired-client"
        assert storage.get_secret(f"plaid_secret_{environment}") == (
            "sandbox-secret" if environment == "sandbox" else "production-secret"
        )
        if environment == "sandbox":
            assert storage.get_secret("plaid_secret_production") == "other-env-secret"
        assert [item["item_id"] for item in storage.list_items()] == ["kept-item"]
        assert storage.get_runtime_token("kept-item") == "kept-token"
    finally:
        storage.close()


def test_init_repairs_malformed_active_secret_without_changing_other_secret(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["init"], input="client\nsandbox-secret\n").exit_code == 0
    transitioned = runner.invoke(
        main, ["use-production"], input="ENABLE PRODUCTION\nproduction-secret\n"
    )
    assert transitioned.exit_code == 0, transitioned.output
    from plaid_mcp.paths import db_path, key_path

    storage = Storage(db_path(), key_path(), create_key=False)
    storage.save_secret("plaid_secret_production", "bad\nproduction-secret")
    storage.save_item("production-item", "production-token", "institution", "Bank", [])
    storage.close()

    repaired = runner.invoke(main, ["init"], input="repaired-production-secret\n")
    assert repaired.exit_code == 0, repaired.output
    storage = Storage(db_path(), key_path(), create_key=False)
    try:
        assert storage.get_secret("plaid_secret_production") == "repaired-production-secret"
        assert storage.get_secret("plaid_secret_sandbox") == "sandbox-secret"
        assert [item["item_id"] for item in storage.list_items()] == ["production-item"]
    finally:
        storage.close()


def test_link_api_exception_is_concise_and_sanitized(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["init"], input="client\nsecret\n").exit_code == 0
    provider_body = (
        b"secret-sentinel access-token-sentinel public-token-sentinel link-token-sentinel"
    )
    error = ApiException(status=400, reason="Bad Request")
    error.body = provider_body

    def fail_link(*args, **kwargs):
        raise error

    monkeypatch.setattr("plaid_mcp.admin.create_hosted_link", fail_link)

    result = runner.invoke(main, ["link", "--no-open"])
    assert result.exit_code != 0
    assert "Error: Plaid request failed (HTTP 400)." in result.output
    assert "Traceback" not in result.output
    for sentinel in (
        "secret-sentinel",
        "access-token-sentinel",
        "public-token-sentinel",
        "link-token-sentinel",
    ):
        assert sentinel not in result.output
    log = tmp_path / "state" / "studio-saelix-finance" / "finance.log"
    log_text = log.read_text()
    for sentinel in (
        "secret-sentinel",
        "access-token-sentinel",
        "public-token-sentinel",
        "link-token-sentinel",
    ):
        assert sentinel not in log_text


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
    assert store.get_secret("plaid_secret_production") == "production-secret"
    store.close()


def test_init_preserves_production_after_transition(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["init"], input="client\nsandbox-secret\n").exit_code == 0
    transitioned = runner.invoke(
        main, ["use-production"], input="ENABLE PRODUCTION\nproduction-secret\n"
    )
    assert transitioned.exit_code == 0, transitioned.output
    initialized = runner.invoke(main, ["init"])
    assert initialized.exit_code == 0, initialized.output
    assert "Production" in initialized.output
    from plaid_mcp.config import Config

    assert Config.from_env(require_credentials=False).env == "production"


def test_production_item_survives_reinit(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["init"], input="client\nsandbox-secret\n").exit_code == 0
    assert (
        runner.invoke(
            main, ["use-production"], input="ENABLE PRODUCTION\nproduction-secret\n"
        ).exit_code
        == 0
    )
    from plaid_mcp.paths import db_path, key_path

    store = Storage(db_path(), key_path(), create_key=False)
    store.save_item("production-item", "production-token", "ins", "Bank", [])
    store.close()
    initialized = runner.invoke(main, ["init"])
    assert initialized.exit_code == 0, initialized.output
    store = Storage(db_path(), key_path(), create_key=False)
    assert [item["item_id"] for item in store.list_items()] == ["production-item"]
    store.close()
    from plaid_mcp.config import Config

    assert Config.from_env(require_credentials=False).env == "production"


def test_already_production_repairs_missing_secret_without_changing_items(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["init"], input="client\nsandbox-secret\n").exit_code == 0
    assert (
        runner.invoke(
            main, ["use-production"], input="ENABLE PRODUCTION\nproduction-secret\n"
        ).exit_code
        == 0
    )
    from plaid_mcp.paths import db_path, key_path

    store = Storage(db_path(), key_path(), create_key=False)
    store.save_item("production-item", "production-token", "ins", "Bank", [])
    store.delete_secret("plaid_secret_production")
    store.close()
    repaired = runner.invoke(main, ["use-production"], input="repaired-secret\n")
    assert repaired.exit_code == 0, repaired.output
    store = Storage(db_path(), key_path(), create_key=False)
    assert store.get_secret("plaid_secret_production") == "repaired-secret"
    assert [item["item_id"] for item in store.list_items()] == ["production-item"]
    store.close()


def test_production_init_repairs_only_production_secret_and_preserves_item(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["init"], input="client\nsandbox-secret\n").exit_code == 0
    assert (
        runner.invoke(
            main, ["use-production"], input="ENABLE PRODUCTION\nproduction-secret\n"
        ).exit_code
        == 0
    )
    from plaid_mcp.paths import db_path, key_path

    store = Storage(db_path(), key_path(), create_key=False)
    store.save_item("production-item", "production-token", "ins", "Bank", [])
    store.delete_secret("plaid_secret_production")
    store.close()
    repaired = runner.invoke(main, ["init"], input="repaired-production-secret\n")
    assert repaired.exit_code == 0, repaired.output
    from plaid_mcp.config import Config

    assert Config.from_env(require_credentials=False).env == "production"
    store = Storage(db_path(), key_path(), create_key=False)
    assert store.get_secret("plaid_secret_production") == "repaired-production-secret"
    assert store.get_secret("plaid_secret_sandbox") == "sandbox-secret"
    assert [item["item_id"] for item in store.list_items()] == ["production-item"]
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
