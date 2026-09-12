"""Stdio-only runtime entry point.

Plaid enrollment/lifecycle source remains available for a future administrator
interface, but no administrator command is registered in this executable.
"""

from __future__ import annotations

import logging
import sys

from .crypto import CredentialError
from .logging_setup import configure_logging
from .paths import log_path
from .server import build_server


def main() -> None:
    configure_logging(log_path())
    logger = logging.getLogger("plaid_mcp")
    logger.info("runtime_start")
    try:
        try:
            server = build_server()
        except CredentialError:
            logger.error("runtime_start_failed reason=credential_store_unavailable")
            print("Finance MCP credential store is unavailable.", file=sys.stderr)
            raise SystemExit(1) from None
        server.run()
    finally:
        logger.info("runtime_stop")


if __name__ == "__main__":
    main()
