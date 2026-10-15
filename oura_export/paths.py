"""Filesystem locations.

Two distinct trees, kept apart on purpose:

  * state_dir  -> ~/.oura-export       cookies/session + config (NEVER in the repo)
  * data_dir   -> config["data_dir"]   the downloaded exports (NEVER in the repo)

Keeping both outside the working tree is what guarantees that cloning or
publishing the repo cannot leak your credentials or health data.
"""

from __future__ import annotations

import os
from pathlib import Path


def state_dir() -> Path:
    """Where the login session and default config live.

    Honours OURA_EXPORT_HOME so tests (and power users) can redirect it.
    """
    override = os.environ.get("OURA_EXPORT_HOME")
    base = Path(override).expanduser() if override else Path.home() / ".oura-export"
    base.mkdir(parents=True, exist_ok=True)
    return base


def session_path() -> Path:
    """Playwright storage_state file (cookies). Treat as a bearer credential."""
    return state_dir() / "session.json"


def default_config_path() -> Path:
    return state_dir() / "config.json"


def expand(path: str | os.PathLike[str]) -> Path:
    """expanduser + expandvars, returned absolute."""
    return Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()
