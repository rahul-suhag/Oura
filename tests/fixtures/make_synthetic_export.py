#!/usr/bin/env python3
"""Generate a realistic *synthetic* Oura export for testing the analysis layer.

Fake data (seeded RNG) in the layout of the real export as documented by
Cracked-Oura's importer: semicolon-separated, one row per day in the daily
files, one row per sleep *session* in sleepmodel.csv (naps included), JSON in
the contributors and spo2_percentage columns, deliberate missing days, and a
few injected outliers so the EDA has something to find.

Usage:
    python make_synthetic_export.py OUTDIR [--days 180 --seed 42]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    # Oura uses ';'. csv.writer quotes fields that contain quotes (the JSON ones).
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(header)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    generate(args.outdir, args.days, args.seed)


def generate(out: Path, days: int = 180, seed: int = 42) -> None:
    """Write the synthetic export into ``out`` (also importable from tests)."""
    rng = random.Random(seed)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    start = date.today() - timedelta(days=days - 1)
    all_days = [start + timedelta(days=i) for i in range(days)]

    sleep, sessions, readiness, activity, spo2, stress, cv_age = [], [], [], [], [], [], []
    vascular = 34

    for i, d in enumerate(all_days):
        iso = d.isoformat()
        ts = f"{iso}T04:00:00+00:00"
        weekend = d.weekday() >= 5
        if i % 23 == 0:            # ring not worn: no rows at all for this day
            continue

        # --- sleep: one long_sleep session, sometimes a nap ---------------------
        base_h = 7.2 + (0.6 if weekend else 0.0) + 0.4 * math.sin(i / 7.0)
        total = int(max(3.5, rng.gauss(base_h, 0.8)) * 3600)
        if i == 5:
            total = 16 * 3600                       # injected outlier: 16 h "sleep"
        hr_avg = round(rng.gauss(54, 4), 1)
        hr_low = int(hr_avg - rng.uniform(3, 8))
        hrv = int(max(10, rng.gauss(45, 12)))
        bed_start = datetime.combine(d - timedelta(days=1), datetime.min.time()) + timedelta(hours=23, minutes=rng.randint(0, 59))
        bed_end = bed_start + timedelta(seconds=total + rng.randint(600, 2400))
        sessions.append([f"s{i}", iso, "long_sleep", bed_start.isoformat(), bed_end.isoformat(),
                         int(rng.gauss(88, 5)), rng.randint(300, 1500), total,
                         int(total * 0.2), int(total * 0.22), int(total * 0.58),
                         int(rng.uniform(600, 2400)), hr_avg, hrv, hr_low, 1, int((bed_end - bed_start).total_seconds())])
        if rng.random() < 0.15:                      # an afternoon nap
            nap = rng.randint(900, 3600)
            sessions.append([f"n{i}", iso, "late_nap", f"{iso}T14:00:00", f"{iso}T15:00:00",
                             None, None, nap, None, None, None, None, None, None, None, 1, nap + 300])
        sleep_score = int(min(100, max(30, rng.gauss(82 - (6 if not weekend else 0), 8))))
        sleep.append([f"d{i}", iso, sleep_score,
                      json.dumps({"deep_sleep": rng.randint(60, 100), "efficiency": rng.randint(70, 100),
                                  "total_sleep": rng.randint(50, 100)}), ts])

        # --- readiness ---------------------------------------------------------------
        readiness.append([f"r{i}", iso, int(min(100, max(30, rng.gauss(80, 9)))),
                          round(rng.gauss(0.0, 0.25), 2), round(rng.gauss(0.0, 0.15), 2),
                          json.dumps({"hrv_balance": rng.randint(50, 100), "resting_heart_rate": rng.randint(50, 100)}), ts])

        # --- activity ------------------------------------------------------------------
        steps = int(max(0, rng.gauss(8500 - (2500 if weekend else 0), 3000)))
        if i == 10:
            steps = 95000                             # injected outlier: impossible step count
        activity.append([f"a{i}", iso, int(min(100, max(20, rng.gauss(78, 11)))), steps,
                         int(2000 + steps * 0.03), int(steps * rng.uniform(0.03, 0.05)),
                         round(rng.uniform(1.3, 1.8), 2), int(steps * 0.7),
                         json.dumps({"meet_daily_targets": rng.randint(40, 100)}), ts])

        # --- the rest -----------------------------------------------------------------------
        spo2.append([f"o{i}", iso, json.dumps({"average": round(rng.gauss(96.5, 1.0), 1)}), rng.randint(0, 8)])
        stress.append([f"t{i}", iso, int(max(0, rng.gauss(5400, 2400))), int(max(0, rng.gauss(14400, 3600))),
                       rng.choice(["normal", "restored", "stressful"])])
        if rng.random() < 0.02:
            vascular += rng.choice([-1, 1])
        cv_age.append([f"c{i}", iso, vascular])

    _write_csv(out / "dailysleep.csv", ["id", "day", "score", "contributors", "timestamp"], sleep)
    _write_csv(out / "sleepmodel.csv",
               ["id", "day", "type", "bedtime_start", "bedtime_end", "efficiency", "latency",
                "total_sleep_duration", "deep_sleep_duration", "rem_sleep_duration", "light_sleep_duration",
                "awake_time", "average_heart_rate", "average_hrv", "lowest_heart_rate", "period", "time_in_bed"],
               sessions)
    _write_csv(out / "dailyreadiness.csv",
               ["id", "day", "score", "temperature_deviation", "temperature_trend_deviation", "contributors", "timestamp"],
               readiness)
    _write_csv(out / "dailyactivity.csv",
               ["id", "day", "score", "steps", "total_calories", "active_calories", "average_met_minutes",
                "equivalent_walking_distance", "contributors", "timestamp"], activity)
    _write_csv(out / "dailyspo2.csv", ["id", "day", "spo2_percentage", "breathing_disturbance_index"], spo2)
    _write_csv(out / "dailystress.csv", ["id", "day", "stress_high", "recovery_high", "day_summary"], stress)
    _write_csv(out / "dailycardiovascularage.csv", ["id", "day", "vascular_age"], cv_age)

    print(f"Wrote synthetic export to {out} ({len(sleep)} days with data)")


if __name__ == "__main__":
    main()
