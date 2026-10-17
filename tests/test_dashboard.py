"""The no-script dashboard: well-formed, adaptive to missing columns, no JavaScript."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from make_synthetic_export import generate  # noqa: E402

from oura_export import dashboard
from oura_export.dashboard import METRICS, Metric


@pytest.fixture
def latest(tmp_path: Path) -> Path:
    d = tmp_path / "latest"
    generate(d, days=120, seed=7)
    return d


def metric(label: str) -> Metric:
    return next(m for m in METRICS if m.label == label)


def test_writes_html_and_svg_without_scripts(latest: Path, tmp_path: Path):
    out = tmp_path / "data"
    html_path = dashboard.write_dashboard(latest, out, now=datetime(2026, 10, 15, 9, 12))
    text = html_path.read_text()
    assert "<script" not in text.lower()                 # Files-app preview runs no JS
    assert "http" not in text.replace("http://www.w3.org/2000/svg", "")  # self-contained
    for label in ("Readiness score", "Sleep score", "Total sleep", "Sleeping HRV", "Steps",
                  "Blood oxygen", "High stress", "Cardiovascular age"):
        assert label in text, label
    assert text.count('<svg viewBox="0 0 640 210"') >= 8  # one trend panel per metric
    assert "Table view" in text and "<tbody>" in text
    assert "prefers-color-scheme: dark" in text          # dark palette is defined, not flipped

    svg = (out / "dashboard.svg").read_text()
    ET.fromstring(svg)                                   # well-formed XML
    assert svg.count("<g transform=") >= 8


def test_each_inline_panel_is_well_formed_svg(latest: Path, tmp_path: Path):
    text = dashboard.write_dashboard(latest, tmp_path / "data").read_text()
    start, n = 0, 0
    while (i := text.find('<svg viewBox="0 0 640 210"', start)) >= 0:
        j = text.find("</svg>", i) + len("</svg>")
        ET.fromstring(text[i:j].replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1))
        start, n = j, n + 1
    assert n >= 8


def test_sleep_metrics_come_from_the_long_sleep_session(latest: Path):
    tables = dashboard.read_tables(latest)
    nap_days = [d for d, rows in tables["sleepmodel"].items() if len(rows) > 1]
    assert nap_days, "fixture should contain days with a nap"
    d = nap_days[0]
    hours = dashboard.series_for(tables, metric("Total sleep"), [d])[0]
    long_row = next(r for r in tables["sleepmodel"][d] if r["type"] == "long_sleep")
    assert hours == pytest.approx(int(long_row["total_sleep_duration"]) / 3600)
    assert hours > 3                                      # not the nap


def test_spo2_json_cell_is_parsed(latest: Path):
    tables = dashboard.read_tables(latest)
    d = next(iter(tables["dailyspo2"]))
    v = dashboard.series_for(tables, metric("Blood oxygen"), [d])[0]
    assert v is not None and 85 < v < 100


@pytest.mark.parametrize("cell", [
    '{"average": 96.5}',
    "{'average': 96.5}",
    '"{""average"": 96.5}"',       # CSV-escaped quotes left in place
    'average=96.5',
])
def test_json_value_tolerates_quoting(cell):
    assert dashboard._json_value(cell, "average") == pytest.approx(96.5)


def test_missing_file_and_column_are_skipped(latest: Path, tmp_path: Path):
    (latest / "dailyspo2.csv").unlink()
    lines = (latest / "sleepmodel.csv").read_text().splitlines()
    header = lines[0].split(";")
    keep = [i for i, h in enumerate(header) if h != "average_hrv"]
    (latest / "sleepmodel.csv").write_text(
        "\n".join(";".join(l.split(";")[i] for i in keep) for l in lines) + "\n")

    text = dashboard.write_dashboard(latest, tmp_path / "data").read_text()
    assert "Blood oxygen" not in text and "Sleeping HRV" not in text
    assert "Sleep score" in text and "Total sleep" in text


def test_quoted_rows_and_gaps_are_handled(tmp_path: Path):
    latest = tmp_path / "latest"
    latest.mkdir()
    # Oura has been seen wrapping whole rows in quotes; days can be missing; so can values.
    (latest / "sleepmodel.csv").write_text(
        "day;type;total_sleep_duration;average_hrv;lowest_heart_rate\n"
        '"2026-10-01;long_sleep;25200;45;50"\n'
        '"2026-10-01;late_nap;1800;;"\n'
        "2026-10-03;long_sleep;;40;49\n"
        "2026-10-04;long_sleep;28800;;\n")
    tables = dashboard.read_tables(latest)
    assert len(tables["sleepmodel"][date(2026, 10, 1)]) == 2
    hours = dashboard.series_for(tables, metric("Total sleep"), [date(2026, 10, 1), date(2026, 10, 3)])
    assert hours[0] == pytest.approx(7.0) and hours[1] is None
    text = dashboard.write_dashboard(latest, tmp_path / "data").read_text()
    assert "Total sleep" in text and "8.0" in text        # latest reading, directly labelled


def test_rolling_mean_needs_enough_points():
    vals = [1.0, None, None, None, None, None, 2.0, 3.0, 4.0, 5.0]
    out = dashboard.rolling_mean(vals, window=7, min_points=4)
    assert out[6] is None                 # only 2 points in the window
    assert out[9] == pytest.approx(3.5)   # 2,3,4,5


def test_no_data_raises(tmp_path: Path):
    (tmp_path / "latest").mkdir()
    with pytest.raises(ValueError):
        dashboard.write_dashboard(tmp_path / "latest", tmp_path / "out")
