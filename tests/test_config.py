"""Tests for config load/save and path expansion. No network."""

from __future__ import annotations

import json
from pathlib import Path

from oura_export import config as config_mod


def test_defaults_when_missing(tmp_path: Path):
    cfg = config_mod.load(tmp_path / "nope.json")
    assert cfg.email == ""
    assert cfg.headless is True
    assert cfg.code_source == "gmail"
    assert cfg.max_logins_per_day == 3


def test_roundtrip_and_comment_stripping(tmp_path: Path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({
        "//email": "this is a comment and must be ignored",
        "email": "me@example.com",
        "data_dir": "~/Somewhere",
        "poll_interval_seconds": 15,
    }), encoding="utf-8")

    cfg = config_mod.load(p)
    assert cfg.email == "me@example.com"
    assert cfg.poll_interval_seconds == 15
    # Unspecified keys fall back to defaults.
    assert cfg.notify is True

    # Save round-trips cleanly (no comment keys written).
    out = tmp_path / "out.json"
    config_mod.save(cfg, out)
    reloaded = json.loads(out.read_text())
    assert "//email" not in reloaded
    assert reloaded["email"] == "me@example.com"


def test_data_path_is_absolute(tmp_path: Path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"data_dir": "~/OuraData"}), encoding="utf-8")
    cfg = config_mod.load(p)
    assert cfg.data_path.is_absolute()
