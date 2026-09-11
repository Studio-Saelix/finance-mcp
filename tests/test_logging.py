from __future__ import annotations

import logging

from plaid_mcp.logging_setup import RedactingFormatter, configure_logging


def test_redacts_all_credential_forms():
    formatter = RedactingFormatter("%(message)s")
    record = logging.LogRecord(
        "test", logging.INFO, "", 0,
        "access_token=fake-access public_token=fake-public link_token=fake-link "
        "plaid_secret=fake-secret master_key=fake-master ordinary message",
        (), None,
    )
    output = formatter.format(record)
    assert "fake-" not in output
    assert "[REDACTED]" in output
    assert formatter.format(
        logging.LogRecord("test", logging.INFO, "", 0, "ordinary message", (), None)
    ) == "ordinary message"


def test_logging_uses_stderr_and_private_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    path = tmp_path / "state" / "studio-saelix-finance" / "finance.log"
    configure_logging(path)
    logging.getLogger("plaid_mcp").info("sync_attempt access_token=fake")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[REDACTED]" in captured.err
    assert "fake" not in path.read_text()
