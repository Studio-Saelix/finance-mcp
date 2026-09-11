"""Stdio-only runtime entry point.

Plaid enrollment/lifecycle source remains available for a future administrator
interface, but no administrator command is registered in this executable.
"""

from __future__ import annotations

from .logging_setup import configure_logging
from .paths import log_path
from .server import build_server


def main() -> None:
    configure_logging(log_path())
    build_server().run()


if __name__ == "__main__":
    main()
