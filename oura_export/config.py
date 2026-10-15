"""Load/save the small JSON config.

JSON (not TOML) so we can both read and write with only the standard library.
Keys prefixed with "//" in config.example.json are comments and are ignored.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from . import paths

DEFAULTS: dict[str, Any] = {
    "email": "",
    "data_dir": "~/OuraData",
    "headless": True,
    "request_timeout_minutes": 30,
    "poll_interval_seconds": 60,
    "notify": True,
}


@dataclass
class Config:
    email: str
    data_dir: str
    headless: bool
    request_timeout_minutes: int
    poll_interval_seconds: int
    notify: bool

    @property
    def data_path(self) -> Path:
        """Base data directory, fully expanded to an absolute path."""
        return paths.expand(self.data_dir)


def _strip_comments(raw: dict[str, Any]) -> dict[str, Any]:
    # Drop "//..."-style comment keys used in the example file.
    return {k: v for k, v in raw.items() if not k.startswith("//")}


def load(path: Path | None = None) -> Config:
    """Load config, falling back to defaults for any missing key."""
    path = path or paths.default_config_path()
    data = dict(DEFAULTS)
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        data.update(_strip_comments(raw))
    # Keep only known fields so an old/extra key can't crash construction.
    known = {k: data[k] for k in DEFAULTS}
    return Config(**known)


def save(cfg: Config, path: Path | None = None) -> Path:
    path = path or paths.default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
    return path
