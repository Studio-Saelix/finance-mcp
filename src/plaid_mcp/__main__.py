"""Stdio-only runtime entry point.

Plaid enrollment/lifecycle source remains available for a future administrator
interface, but no administrator command is registered in this executable.
"""

from __future__ import annotations

from .server import build_server


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
