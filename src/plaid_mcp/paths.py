"""XDG paths owned by Studio Saelix Finance."""

from __future__ import annotations

import os
import stat
from pathlib import Path

APP_NAME = "studio-saelix-finance"


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP_NAME


def state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.toml"


def key_path() -> Path:
    return config_dir() / "master.key"


def db_path() -> Path:
    return data_dir() / "finance.db"


def log_path() -> Path:
    return state_dir() / "finance.log"


def lock_dir() -> Path:
    return state_dir() / "locks"


def ensure_private_dir(path: Path) -> None:
    """Create/check an application-managed owner-only directory."""
    if path.exists() and path.is_symlink():
        raise RuntimeError(f"Sensitive directory must not be a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid():
        raise RuntimeError(f"Sensitive directory is not owned by this user: {path}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        os.chmod(path, 0o700)
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise RuntimeError(f"Sensitive directory is not private: {path}")


def ensure_private_file(path: Path, *, create: bool = False) -> None:
    if path.exists() and path.is_symlink():
        raise RuntimeError(f"Sensitive file must not be a symlink: {path}")
    if create and not path.exists():
        path.touch(mode=0o600)
    if not path.exists() or not path.is_file():
        raise RuntimeError(f"Sensitive file is missing or not a file: {path}")
    info = path.stat()
    if info.st_uid != os.getuid():
        raise RuntimeError(f"Sensitive file is not owned by this user: {path}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        os.chmod(path, 0o600)
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise RuntimeError(f"Sensitive file is not private: {path}")
