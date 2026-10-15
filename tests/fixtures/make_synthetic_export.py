#!/usr/bin/env python3
"""Generate a realistic *synthetic* Oura export for testing the analysis layer.

This is fake data (seeded RNG), used only to exercise load_oura.R / eda_report.R
without needing a real export. Semicolon-separated, with the real file and column
names, deliberate missing days, and a few injected outliers so the EDA has
something to find.

Usage:
    python make_synthetic_export.py OUTDIR [--days 180 --seed 42]
"""
from __future__ import annotations

import argparse
import math
import random
from datetime import date, timedelta
from pathlib import Path


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    # Oura uses ';' as the delimiter and no quoting for simple values.
    lines = [";".join(header)]
    for r in rows:
        lines.append(";".join("" if v is None else str(v) for v in r))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    out = args.outdir
    out.mkdir(parents=True, exist_ok=True)
    start = date.today() - timedelta(days=args.days - 1)
    days = [start + timedelta(days=i) for i in range(args.days)]

    sleep_rows, readiness_rows, activity_rows, spo2_rows = [], [], [], []

    for i, d in enumerate(days):
        iso = d.isoformat()
        # A slow weekly rhythm + noise; weekends sleep a bit longer.
        weekend = d.weekday() >= 5
        base_sleep_h = 7.2 + (0.6 if weekend else 0.0) + 0.4 * math.sin(i / 7.0)
        sleep_seconds = int(max(3.5, rng.gauss(base_sleep_h, 0.8)) * 3600)
        sleep_score = int(min(100, max(30, rng.gauss(82 - (6 if not weekend else 0), 8))))
        hr_avg = round(rng.gauss(54, 4), 1)
        hrv = int(max(10, rng.gauss(45, 12)))

        # Missing day: ring not worn ~ every 23rd day (gap handling test).
        worn = (i % 23) != 0

        if worn:
            sleep_rows.append([iso, sleep_score, sleep_seconds, hr_avg, hrv])
            readiness_rows.append([
                iso,
                int(min(100, max(30, rng.gauss(80, 9)))),
                round(rng.gauss(36.5, 0.15), 2),   # temperature_deviation-ish
            ])
            steps = int(max(0, rng.gauss(8500 - (2500 if weekend else 0), 3000)))
            activity_rows.append([
                iso,
                int(min(100, max(20, rng.gauss(78, 11)))),
                steps,
                int(steps * rng.uniform(0.03, 0.05)),  # active_calories ~ steps
            ])
            spo2_rows.append([iso, round(rng.gauss(96.5, 1.0), 1)])

    # Inject a couple of obvious outliers for the EDA to flag.
    if len(activity_rows) > 10:
        activity_rows[10][2] = 95000          # impossible step count
    if len(sleep_rows) > 5:
        sleep_rows[5][2] = int(16 * 3600)     # 16h "sleep"

    _write_csv(out / "dailysleep.csv",
               ["day", "score", "total_sleep_duration", "average_heart_rate", "average_hrv"],
               sleep_rows)
    _write_csv(out / "dailyreadiness.csv",
               ["day", "score", "temperature"], readiness_rows)
    _write_csv(out / "dailyactivity.csv",
               ["day", "score", "steps", "active_calories"], activity_rows)
    _write_csv(out / "dailyspo2.csv",
               ["day", "spo2_percentage"], spo2_rows)

    print(f"Wrote synthetic export to {out} ({len(sleep_rows)} sleep days)")


if __name__ == "__main__":
    main()
