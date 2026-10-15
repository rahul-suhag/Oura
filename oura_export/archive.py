"""Save, safely unzip, and organise a downloaded export.

Layout produced under ``data_dir``::

    raw/<YYYY-MM-DD>/oura_export_<YYYY-MM-DD>.zip     the untouched download
    extracted/<YYYY-MM-DD>/*.csv                      flattened CSVs
    latest/*.csv                                      copy of the newest extract

``latest/`` always mirrors the most recent extract so downstream tools (the R
analysis layer, the Files app on your phone) can point at one stable path.
"""

from __future__ import annotations

import os
import shutil
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass
class ArchiveResult:
    raw_zip: Path
    extracted_dir: Path
    latest_dir: Path
    csv_files: list[str]


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract guarding against zip-slip (members escaping ``dest``).

    A malformed or malicious archive could contain names like ``../../x``;
    we resolve each target and refuse anything outside ``dest``.
    """
    dest = dest.resolve()
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        if not str(target).startswith(str(dest) + os.sep) and target != dest:
            raise ValueError(f"Unsafe path in archive: {member.filename!r}")
    zf.extractall(dest)


def _flatten_csvs(src_root: Path, dest: Path) -> list[str]:
    """Copy every .csv found anywhere under ``src_root`` into ``dest`` (flat).

    Oura nests the CSVs inside a dated folder; downstream code is simpler if we
    present them all at one level. Names are unique within an export, so a flat
    copy is safe.
    """
    dest.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    for csv in sorted(src_root.rglob("*.csv")):
        shutil.copy2(csv, dest / csv.name)
        names.append(csv.name)
    return names


def _replace_dir(src: Path, dst: Path) -> None:
    """Make ``dst`` an exact copy of ``src`` (used for the ``latest/`` mirror)."""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def archive_zip(zip_path: Path, data_dir: Path, when: date | None = None) -> ArchiveResult:
    """Store, extract, and refresh ``latest/`` for one downloaded export zip."""
    when = when or date.today()
    stamp = when.isoformat()
    zip_path = Path(zip_path)

    raw_dir = data_dir / "raw" / stamp
    extracted_dir = data_dir / "extracted" / stamp
    latest_dir = data_dir / "latest"
    raw_dir.mkdir(parents=True, exist_ok=True)

    # 1. Keep the pristine download.
    raw_zip = raw_dir / f"oura_export_{stamp}.zip"
    shutil.copy2(zip_path, raw_zip)

    # 2. Unzip to a scratch area, then flatten CSVs into the dated extract dir.
    if extracted_dir.exists():
        shutil.rmtree(extracted_dir)
    scratch = extracted_dir / "_unzipped"
    scratch.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(raw_zip) as zf:
        _safe_extract(zf, scratch)
    csv_files = _flatten_csvs(scratch, extracted_dir)
    shutil.rmtree(scratch)

    # 3. Mirror into latest/.
    _replace_dir(extracted_dir, latest_dir)

    return ArchiveResult(
        raw_zip=raw_zip,
        extracted_dir=extracted_dir,
        latest_dir=latest_dir,
        csv_files=csv_files,
    )
