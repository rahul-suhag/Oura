"""Load/save the small JSON config.

JSON (not TOML) so we can both read and write with only the standard library.
Keys prefixed with "//" in config.example.json are comments and are ignored.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import paths

DEFAULTS: dict[str, Any] = {
    "email": "",
    "data_dir": "~/OuraData",
    "headless": True,
    # How the job gets Oura's emailed sign-in code: "gmail" (read it over IMAP
    # with an app password) or "prompt" (a person types it; no unattended sign-in).
    "code_source": "gmail",
    "imap_host": "imap.gmail.com",
    "imap_user": "",                      # blank = same as email
    "code_sender": "ouraring.com",        # matched against the From address
    # Request at most this often. 22 rather than 24 so a 3-hourly schedule
    # doesn't drift later every day.
    "min_hours_between_requests": 22,
    "wait_minutes": 45,                   # after requesting, wait this long in-run
    "poll_interval_seconds": 60,
    "give_up_after_hours": 48,            # stop waiting on an export after this
    "max_logins_per_day": 3,              # never flood your inbox or lock the account
    "notify": True,
}


@dataclass
class Config:
    email: str
    data_dir: str
    headless: bool
    code_source: str
    imap_host: str
    imap_user: str
    code_sender: str
    min_hours_between_requests: float
    wait_minutes: float
    poll_interval_seconds: float
    give_up_after_hours: float
    max_logins_per_day: int
    notify: bool

    @property
    def data_path(self) -> Path:
        """Base data directory, fully expanded to an absolute path."""
        return paths.expand(self.data_dir)


def _strip_comments(raw: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in raw.items() if not k.startswith("//")}


def load(path: Path | None = None) -> Config:
    """Load config, falling back to defaults for any missing key."""
    path = path or paths.default_config_path()
    data = dict(DEFAULTS)
    if path.exists():
        data.update(_strip_comments(json.loads(path.read_text(encoding="utf-8"))))
    if data["code_source"] not in ("gmail", "prompt"):
        raise ValueError(f"code_source must be 'gmail' or 'prompt', not {data['code_source']!r}")
    # Keep only known fields so an old/extra key can't crash construction.
    return Config(**{k: data[k] for k in DEFAULTS})


def save(cfg: Config, path: Path | None = None) -> Path:
    path = path or paths.default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
    return path
