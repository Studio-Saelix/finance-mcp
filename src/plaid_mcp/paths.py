"""XDG paths owned by Studio Saelix Finance."""

from __future__ import annotations

import os
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
