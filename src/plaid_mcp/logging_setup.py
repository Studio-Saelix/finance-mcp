"""Redacted operational logging that never writes to MCP stdout."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

from .paths import ensure_private_dir, ensure_private_file

_SECRET_PATTERN = re.compile(
    r"(?i)(access[_ -]?token|public[_ -]?token|link[_ -]?token|plaid[_ -]?secret|master[_ -]?key)"
    r"(?:\s*[:=]\s*)[^\s,;]+"
)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _SECRET_PATTERN.sub(r"\1=[REDACTED]", super().format(record))


def configure_logging(path: Path) -> None:
    ensure_private_dir(path.parent)
    ensure_private_file(path, create=True)
    logger = logging.getLogger("plaid_mcp")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
    formatter = RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(formatter)
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(stderr)
    logger.addHandler(file_handler)
