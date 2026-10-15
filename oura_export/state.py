"""What the scheduled job remembers between runs (a small JSON file)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

_TIMES = ("last_request_at", "pending_since", "last_new_export_at", "last_run_at")


@dataclass
class State:
    last_request_at: datetime | None = None     # when we last pressed "Request"
    pending_since: datetime | None = None       # an export is being built since then
    last_export_sha256: str | None = None       # fingerprint of the newest saved export
    last_new_export_at: datetime | None = None
    last_run_at: datetime | None = None
    last_result: str = "never run"
    login_needed: bool = False
    login_attempts: list[datetime] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "State":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in raw.items() if k in known}
        for key in _TIMES:
            if data.get(key):
                data[key] = datetime.fromisoformat(data[key])
        data["login_attempts"] = [datetime.fromisoformat(t) for t in data.get("login_attempts", [])]
        return cls(**data)

    def save(self, path: Path) -> None:
        data = asdict(self)
        for key in _TIMES:
            if data[key] is not None:
                data[key] = data[key].isoformat()
        data["login_attempts"] = [t.isoformat() for t in self.login_attempts]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)  # atomic: a crash never leaves half a file
