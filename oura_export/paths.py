"""Filesystem locations.

Two distinct trees, kept apart on purpose:

  * state_dir  -> ~/.oura-export      browser profile, config, job state, logs
  * data_dir   -> config["data_dir"]  the downloaded exports

Both live outside the working tree, which is what guarantees that cloning or
publishing the repo cannot leak your login or your health data.
"""

from __future__ import annotations

import os
from pathlib import Path


def state_dir() -> Path:
    """Honours OURA_EXPORT_HOME so tests (and power users) can redirect it."""
    override = os.environ.get("OURA_EXPORT_HOME")
    base = Path(override).expanduser() if override else Path.home() / ".oura-export"
    base.mkdir(parents=True, exist_ok=True)
    return base


def default_config_path() -> Path:
    return state_dir() / "config.json"


def profile_dir() -> Path:
    """Persistent Chromium profile. Holds your Oura login: treat like a password."""
    return state_dir() / "browser-profile"


def state_file() -> Path:
    return state_dir() / "state.json"


def lock_file() -> Path:
    return state_dir() / "run.lock"


def debug_dir() -> Path:
    return state_dir() / "debug"


def work_dir() -> Path:
    return state_dir() / "incoming"


def expand(path: str | os.PathLike[str]) -> Path:
    """expanduser + expandvars, returned absolute."""
    return Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()
