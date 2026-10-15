"""Tests for the archive/extract logic. No network, no browser."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import pytest

from oura_export.archive import archive_zip, _safe_extract


def _make_export_zip(path: Path) -> None:
    """Build a synthetic Oura-style export: CSVs nested in a dated folder."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("oura_2026-10-15/dailysleep.csv", "day;score\n2026-10-14;88\n")
        zf.writestr("oura_2026-10-15/dailyactivity.csv", "day;steps\n2026-10-14;9000\n")


def test_archive_zip_layout(tmp_path: Path):
    src_zip = tmp_path / "download.zip"
    _make_export_zip(src_zip)
    data_dir = tmp_path / "OuraData"

    result = archive_zip(src_zip, data_dir, when=date(2026, 10, 15))

    # Raw zip preserved under a dated folder.
    assert result.raw_zip == data_dir / "raw" / "2026-10-15" / "oura_export_2026-10-15.zip"
    assert result.raw_zip.exists()

    # CSVs flattened into the dated extract dir.
    assert sorted(result.csv_files) == ["dailyactivity.csv", "dailysleep.csv"]
    assert (result.extracted_dir / "dailysleep.csv").exists()
    assert not (result.extracted_dir / "_unzipped").exists()  # scratch cleaned up

    # latest/ mirrors the newest extract.
    assert (result.latest_dir / "dailysleep.csv").read_text().startswith("day;score")


def test_latest_is_replaced_not_merged(tmp_path: Path):
    data_dir = tmp_path / "OuraData"

    old = tmp_path / "old.zip"
    with zipfile.ZipFile(old, "w") as zf:
        zf.writestr("x/stale.csv", "a;b\n1;2\n")
    archive_zip(old, data_dir, when=date(2026, 10, 14))
    assert (data_dir / "latest" / "stale.csv").exists()

    new = tmp_path / "new.zip"
    _make_export_zip(new)
    archive_zip(new, data_dir, when=date(2026, 10, 15))

    # The stale file from the previous export must not linger in latest/.
    assert not (data_dir / "latest" / "stale.csv").exists()
    assert (data_dir / "latest" / "dailysleep.csv").exists()
    # But the older dated extract is still kept as history.
    assert (data_dir / "extracted" / "2026-10-14" / "stale.csv").exists()


def test_safe_extract_blocks_zip_slip(tmp_path: Path):
    evil = tmp_path / "evil.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.csv", "pwn\n")
    evil.write_bytes(buf.getvalue())

    dest = tmp_path / "out"
    dest.mkdir()
    with zipfile.ZipFile(evil) as zf:
        with pytest.raises(ValueError, match="Unsafe path"):
            _safe_extract(zf, dest)
