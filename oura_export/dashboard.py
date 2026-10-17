"""Write a self-contained dashboard (dashboard.html + dashboard.svg) beside the data.

Made for the iPhone Files app, whose preview renders HTML and SVG but runs no
scripts: everything is inline CSS and inline SVG, with no JavaScript and no
external files. Opened in a desktop browser the same file also shows hover
readouts (native SVG <title> tooltips).

Visual rules follow the dataviz method:
  * one data hue (blue, slot 1); daily values in a de-emphasis gray, the
    7-day average in the accent ("emphasis" form), one series per panel, so
    no legend boxes; the key is stated once in the header
  * 2px lines, >=8px end marker with a 2px surface ring, hairline solid grid,
    selective labels (only the latest value), text never in the data colour
  * a hero figure, stat tiles with 12-point sparklines, a table view twin
  * light and dark palettes both defined (the phone's setting picks one)
Deviations forced by the no-script target: no filter row (fixed 90-day
window) and tooltips only where the viewer supports hover.
"""

from __future__ import annotations

import csv
import html
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import fmean

log = logging.getLogger("oura_export.dashboard")

WINDOW_DAYS = 90
SPARK_POINTS = 12


# --- what to show ---------------------------------------------------------------
# File and column names follow Oura's export as documented by Cracked-Oura's
# importer (March 2026). A metric whose file or column is missing is skipped,
# so the page adapts to whatever the export contains.
#
# Shape notes: dailysleep/dailyreadiness/dailyactivity hold one row per day
# with the score; sleep duration, HRV and heart rate live in sleepmodel.csv,
# which has one row per sleep *session* (naps included) so we take the
# long_sleep row; dailyspo2's spo2_percentage is a JSON object {"average": x}.

@dataclass(frozen=True)
class Metric:
    file: str            # CSV name without .csv
    column: str
    label: str
    unit: str = ""
    scale: float = 1.0   # raw value multiplier (seconds -> hours = 1/3600)
    decimals: int = 0
    up_is_good: bool | None = True   # None: neither direction is "good"
    json_key: str | None = None      # the column holds JSON; take this key
    pick: str = "first"              # which row when a day has several: "first" | "long_sleep"

    def fmt(self, v: float | None) -> str:
        if v is None or not math.isfinite(v):
            return "–"
        if self.decimals == 0 and abs(v) >= 10_000:
            return f"{v / 1000:.1f}K"
        return f"{v:,.{self.decimals}f}"


METRICS: tuple[Metric, ...] = (
    Metric("dailyreadiness", "score", "Readiness score"),
    Metric("dailysleep", "score", "Sleep score"),
    Metric("dailyactivity", "score", "Activity score"),
    Metric("sleepmodel", "total_sleep_duration", "Total sleep", "h", 1 / 3600, 1, pick="long_sleep"),
    Metric("sleepmodel", "average_hrv", "Sleeping HRV", "ms", pick="long_sleep"),
    Metric("sleepmodel", "lowest_heart_rate", "Lowest sleeping HR", "bpm", up_is_good=False, pick="long_sleep"),
    Metric("dailyactivity", "steps", "Steps"),
    Metric("dailyspo2", "spo2_percentage", "Blood oxygen", "%", decimals=1, json_key="average"),
    Metric("dailyreadiness", "temperature_deviation", "Temp. deviation", "°C", decimals=2, up_is_good=None),
    Metric("dailystress", "stress_high", "High stress", "min", 1 / 60, up_is_good=False),
    Metric("dailycardiovascularage", "vascular_age", "Cardiovascular age", "yrs", up_is_good=False),
)


# --- reading the CSVs -----------------------------------------------------------

def _parse_day(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip().strip('"')[:10])
    except ValueError:
        return None


def _num(raw: str | None) -> float | None:
    if raw is None:
        return None
    raw = raw.strip().strip('"')
    if raw in ("", "NA", "null", "None"):
        return None
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _split(line: str, n_fields: int) -> list[str]:
    """Split one Oura CSV line. Oura sometimes wraps a whole row in quotes."""
    line = line.rstrip("\r\n")
    if n_fields > 1 and line.startswith('"') and line.endswith('"') and line.count(";") >= n_fields - 1:
        line = line[1:-1]
    return next(csv.reader([line], delimiter=";"))


Tables = dict[str, dict[date, list[dict[str, str]]]]


def read_tables(latest_dir: Path) -> Tables:
    """{csv stem: {day: [row, ...]}} for every CSV that has a `day` column.

    A list per day because sleepmodel.csv has one row per sleep session.
    """
    tables: Tables = {}
    for path in sorted(Path(latest_dir).glob("*.csv")):
        with open(path, encoding="utf-8-sig", newline="") as f:
            text = f.read()
        if not text.strip():
            continue
        # JSON columns can contain newlines inside quotes, so split with csv.
        records = list(csv.reader(text.splitlines(True), delimiter=";"))
        header = [h.strip().strip('"') for h in records[0]]
        if "day" not in header:
            continue
        rows: dict[date, list[dict[str, str]]] = {}
        for fields in records[1:]:
            if not any(f.strip() for f in fields):
                continue
            if len(fields) == 1 and fields[0].count(";") >= len(header) - 1:
                fields = _split(fields[0], len(header))   # whole row was quoted
            fields += [""] * (len(header) - len(fields))
            row = dict(zip(header, fields))
            d = _parse_day(row.get("day"))
            if d:
                rows.setdefault(d, []).append(row)
        tables[path.stem] = rows
    return tables


_JSON_NUMBER = re.compile(r"(-?\d+(?:\.\d+)?)")


def _json_value(raw: str, key: str) -> float | None:
    """Pull ``key`` out of a JSON-ish cell, tolerating odd quoting."""
    raw = raw.strip().strip('"')
    for candidate in (raw, raw.replace("'", '"'), raw.replace('""', '"')):
        try:
            obj = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return _num(str(obj.get(key, "")))
        break
    m = re.search(re.escape(key) + r"\W+" + _JSON_NUMBER.pattern, raw)
    return _num(m.group(1)) if m else None


def _pick_row(rows: list[dict[str, str]], m: Metric) -> dict[str, str] | None:
    if not rows:
        return None
    if m.pick == "long_sleep":
        longs = [r for r in rows if r.get("type", "").strip().strip('"') == "long_sleep"]
        pool = longs or rows
        return max(pool, key=lambda r: _num(r.get("total_sleep_duration")) or 0.0)
    return rows[0]


def value_of(row: dict[str, str] | None, m: Metric) -> float | None:
    if row is None:
        return None
    raw = row.get(m.column)
    if raw is None:
        return None
    v = _json_value(raw, m.json_key) if m.json_key else _num(raw)
    return None if v is None else v * m.scale


def series_for(tables: Tables, m: Metric, days: list[date]) -> list[float | None]:
    by_day = tables.get(m.file, {})
    return [value_of(_pick_row(by_day.get(d, []), m), m) for d in days]


def rolling_mean(values: list[float | None], window: int = 7, min_points: int = 4) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        chunk = [v for v in values[max(0, i - window + 1): i + 1] if v is not None]
        out.append(fmean(chunk) if len(chunk) >= min_points else None)
    return out


def latest_and_delta(values: list[float | None], days: list[date]):
    """(latest value, its day, change vs the mean of the 7 days before it)."""
    idx = next((i for i in range(len(values) - 1, -1, -1) if values[i] is not None), None)
    if idx is None:
        return None, None, None
    prev = [v for v in values[max(0, idx - 7): idx] if v is not None]
    delta = values[idx] - fmean(prev) if len(prev) >= 3 else None
    return values[idx], days[idx], delta


# --- SVG helpers --------------------------------------------------------------------

def _nice_ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw)
    start = math.floor(lo / step) * step
    ticks = []
    t = start
    while t <= hi + step * 0.5:
        if t >= lo - step * 0.5:
            ticks.append(round(t, 10))
        t += step
    return ticks


def _tick_label(v: float, m: Metric) -> str:
    if abs(v) >= 1000 and m.decimals == 0:
        return f"{v / 1000:g}K"
    return f"{v:g}" if m.decimals == 0 else f"{v:.{m.decimals}f}"


def _path(points: list[tuple[float, float] | None]) -> str:
    d, pen = [], False
    for p in points:
        if p is None:
            pen = False
            continue
        d.append(f"{'L' if pen else 'M'}{p[0]:.1f} {p[1]:.1f}")
        pen = True
    return " ".join(d)


# Panel geometry (SVG user units; the SVG scales to its container).
PW, PH = 640, 210
L, R, T, B = 44, 60, 16, 30


def panel_body(m: Metric, days: list[date], raw: list[float | None], mean: list[float | None]) -> str:
    """Inner SVG for one metric, drawn inside a PW x PH box."""
    present = [v for v in raw + mean if v is not None]
    if not present:
        return ""
    lo, hi = min(present), max(present)
    pad = (hi - lo) * 0.08 or (abs(hi) * 0.05 or 1)
    ticks = _nice_ticks(lo - pad, hi + pad)
    y0, y1 = min(ticks[0], lo - pad), max(ticks[-1], hi + pad)
    n = len(days)
    plot_w, plot_h = PW - L - R, PH - T - B
    step = plot_w / max(n - 1, 1)

    def x(i: int) -> float:
        return L + i * step

    def y(v: float) -> float:
        return T + (y1 - v) / (y1 - y0) * plot_h

    parts = []
    # Hairline grid + tick labels (muted ink, tabular figures).
    for t in ticks:
        if y0 <= t <= y1:
            parts.append(f'<line class="grid" x1="{L}" x2="{PW - R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>')
            parts.append(f'<text class="tick" x="{L - 8}" y="{y(t) + 4:.1f}" text-anchor="end">'
                         f'{html.escape(_tick_label(t, m))}</text>')
    parts.append(f'<line class="axis" x1="{L}" x2="{PW - R}" y1="{T + plot_h}" y2="{T + plot_h}"/>')
    # Month starts on the x axis (or the ends, for a short window).
    marks = [i for i, d in enumerate(days) if d.day == 1] or [0, n - 1]
    for i in marks:
        parts.append(f'<text class="tick" x="{x(i):.1f}" y="{PH - 8}" text-anchor="middle">'
                     f'{days[i]:%b}</text>' if days[i].day == 1 else
                     f'<text class="tick" x="{x(i):.1f}" y="{PH - 8}" text-anchor="middle">'
                     f'{days[i]:%-d %b}</text>')
    # Lines: daily values (gray) under the 7-day average (accent).
    parts.append(f'<path class="raw" d="{_path([None if v is None else (x(i), y(v)) for i, v in enumerate(raw)])}"/>')
    parts.append(f'<path class="mean" d="{_path([None if v is None else (x(i), y(v)) for i, v in enumerate(mean)])}"/>')
    # End marker + the one direct label: the latest reading.
    last = next((i for i in range(n - 1, -1, -1) if raw[i] is not None), None)
    if last is not None:
        parts.append(f'<circle class="end" cx="{x(last):.1f}" cy="{y(raw[last]):.1f}" r="4"/>')
        parts.append(f'<text class="label" x="{x(last) + 10:.1f}" y="{y(raw[last]) + 4:.1f}">'
                     f'{html.escape(m.fmt(raw[last]))}{html.escape(m.unit)}</text>')
    # Hover readouts (desktop browsers): one wide hit band per day.
    for i, d in enumerate(days):
        if raw[i] is None and mean[i] is None:
            continue
        tip = f"{d:%a %-d %b %Y}: {m.fmt(raw[i])}{m.unit}"
        if mean[i] is not None:
            tip += f" (7-day avg {m.fmt(mean[i])}{m.unit})"
        parts.append(f'<rect class="hit" x="{x(i) - step / 2:.1f}" y="{T}" width="{step:.1f}" height="{plot_h}">'
                     f'<title>{html.escape(tip)}</title></rect>')
    return "\n".join(parts)


def sparkline(values: list[float | None]) -> str:
    pts = [v for v in values[-SPARK_POINTS:]]
    present = [v for v in pts if v is not None]
    if len(present) < 2:
        return ""
    lo, hi = min(present), max(present)
    w, h, pad = 96, 28, 4
    stepx = (w - 2 * pad) / max(len(pts) - 1, 1)

    def pt(i, v):
        yy = h - pad - ((v - lo) / (hi - lo) * (h - 2 * pad) if hi > lo else (h - 2 * pad) / 2)
        return (pad + i * stepx, yy)

    points = [None if v is None else pt(i, v) for i, v in enumerate(pts)]
    last = next((i for i in range(len(pts) - 1, -1, -1) if pts[i] is not None), None)
    prev = next((i for i in range(last - 1, -1, -1) if pts[i] is not None), None) if last is not None else None
    out = [f'<svg class="spark" viewBox="0 0 {w} {h}" aria-hidden="true">',
           f'<path class="raw" d="{_path(points)}"/>']
    if prev is not None:  # current period in the accent
        out.append(f'<path class="mean" d="{_path([points[prev], points[last]])}"/>')
    if last is not None:
        out.append(f'<circle class="end" cx="{points[last][0]:.1f}" cy="{points[last][1]:.1f}" r="3.5"/>')
    out.append("</svg>")
    return "".join(out)


# --- page assembly ---------------------------------------------------------------------

CSS = """
:root { color-scheme: light dark; }
.viz { --surface:#fcfcfb; --page:#f9f9f7; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
       --grid:#e1e0d9; --axis:#c3c2b7; --accent:#2a78d6; --good:#0ca30c; --bad:#d03b3b;
       --ring:rgba(11,11,11,0.10); color-scheme:light; }
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .viz { --surface:#1a1a19; --page:#0d0d0d; --ink:#f0efec;
       --ink2:#c3c2b7; --grid:#2c2c2a; --axis:#383835; --accent:#3987e5; --ring:rgba(255,255,255,0.10);
       color-scheme:dark; }
}
:root[data-theme="dark"] .viz { --surface:#1a1a19; --page:#0d0d0d; --ink:#f0efec; --ink2:#c3c2b7;
       --grid:#2c2c2a; --axis:#383835; --accent:#3987e5; --ring:rgba(255,255,255,0.10); color-scheme:dark; }
html, body { margin:0; }
.viz { background:var(--page); color:var(--ink); font:15px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;
       padding:20px 16px 40px; min-height:100vh; }
.wrap { max-width:720px; margin:0 auto; }
h1 { font-size:22px; margin:0 0 2px; }
.sub { color:var(--ink2); margin:0 0 14px; font-size:13px; }
.key { display:flex; gap:16px; color:var(--ink2); font-size:13px; margin:0 0 18px; flex-wrap:wrap; }
.key i { display:inline-block; width:18px; height:2px; vertical-align:middle; margin-right:6px; border-radius:1px; }
.key .k-raw { background:var(--muted); } .key .k-mean { background:var(--accent); }
.card { background:var(--surface); border:1px solid var(--ring); border-radius:12px; padding:14px 16px; margin:0 0 12px; }
.hero .label { color:var(--ink2); font-size:14px; }
.hero .value { font-size:56px; font-weight:600; line-height:1.05; letter-spacing:-0.01em; }
.hero .value small, .tile .value small { font-size:0.45em; font-weight:500; color:var(--ink2); margin-left:2px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:12px; margin:0 0 12px; }
.tile { background:var(--surface); border:1px solid var(--ring); border-radius:12px; padding:12px 14px; }
.tile .label { color:var(--ink2); font-size:13px; }
.tile .value { font-size:26px; font-weight:600; line-height:1.15; margin:2px 0; }
.delta { color:var(--ink2); font-size:12.5px; }
.delta b { font-weight:600; color:var(--ink); }
.delta .g { color:var(--good); } .delta .b { color:var(--bad); } .delta .n { color:var(--muted); }
.spark { width:96px; height:28px; display:block; margin-top:6px; }
.panel h2 { font-size:15px; font-weight:600; margin:0 0 6px; }
.panel svg { width:100%; height:auto; display:block; }
.grid { stroke:var(--grid); stroke-width:1; }
.axis { stroke:var(--axis); stroke-width:1; }
.tick { fill:var(--muted); font-size:11px; font-variant-numeric:tabular-nums; }
.raw { fill:none; stroke:var(--muted); stroke-width:2; stroke-linejoin:round; stroke-linecap:round; }
.spark .raw { stroke-width:1.5; }
.mean { fill:none; stroke:var(--accent); stroke-width:2; stroke-linejoin:round; stroke-linecap:round; }
.end { fill:var(--accent); stroke:var(--surface); stroke-width:2; }
.label { fill:var(--ink); font-size:12px; font-weight:600; }
.hit { fill:transparent; }
details { margin-top:8px; }
summary { cursor:pointer; color:var(--ink2); font-size:14px; }
table { border-collapse:collapse; width:100%; margin-top:10px; font-size:12.5px; font-variant-numeric:tabular-nums; }
th, td { text-align:right; padding:5px 6px; border-bottom:1px solid var(--grid); white-space:nowrap; }
th:first-child, td:first-child { text-align:left; }
th { color:var(--ink2); font-weight:600; }
.foot { color:var(--muted); font-size:12px; margin-top:18px; }
"""


def _delta_html(m: Metric, delta: float | None) -> str:
    if delta is None:
        return '<div class="delta"><span class="n">–</span> not enough history</div>'
    delta = round(delta, m.decimals)   # judge direction on what is shown, so no "−0"
    sign = "+" if delta > 0 else ("−" if delta < 0 else "±")
    arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "▬")
    if m.up_is_good is None or delta == 0:
        cls = "n"
    else:
        cls = "g" if (delta > 0) == m.up_is_good else "b"
    return (f'<div class="delta"><span class="{cls}" aria-hidden="true">{arrow}</span> '
            f'<b>{sign}{html.escape(m.fmt(abs(delta)))}{html.escape(m.unit)}</b> vs prior 7d</div>')


def write_dashboard(latest_dir: Path, out_dir: Path, *, window_days: int = WINDOW_DAYS,
                    now: datetime | None = None) -> Path:
    """Write dashboard.html and dashboard.svg into ``out_dir``. Returns the HTML path."""
    latest_dir, out_dir = Path(latest_dir), Path(out_dir)
    now = now or datetime.now()
    tables = read_tables(latest_dir)
    all_days = [d for rows in tables.values() for d in rows]
    if not all_days:
        raise ValueError(f"No daily CSV data found in {latest_dir}")
    end = max(all_days)
    days = [end - timedelta(days=window_days - 1 - i) for i in range(window_days)]

    metrics = [m for m in METRICS if m.file in tables
               and any(value_of(r, m) is not None
                       for rows in tables[m.file].values() for r in rows)]
    data = {m: series_for(tables, m, days) for m in metrics}
    means = {m: rolling_mean(data[m]) for m in metrics}

    hero = metrics[0] if metrics else None
    body: list[str] = []
    body.append(f'<h1>Oura</h1><p class="sub">Last {window_days} days to {end:%A %-d %B %Y} · '
                f'generated {now:%-d %b %H:%M} · fetch status in STATUS.txt</p>')
    body.append('<div class="key"><span><i class="k-raw"></i>daily value</span>'
                '<span><i class="k-mean"></i>7-day average</span></div>')

    if hero is not None:
        v, d, delta = latest_and_delta(data[hero], days)
        body.append(f'<div class="card hero"><div class="label">{html.escape(hero.label)}'
                    f'{" · " + d.strftime("%a %-d %b") if d else ""}</div>'
                    f'<div class="value">{html.escape(hero.fmt(v))}<small>{html.escape(hero.unit)}</small></div>'
                    f'{_delta_html(hero, delta)}</div>')

    tiles = [f'<div class="tile"><div class="label">{html.escape(m.label)}</div>'
             f'<div class="value">{html.escape(m.fmt(latest_and_delta(data[m], days)[0]))}'
             f'<small>{html.escape(m.unit)}</small></div>'
             f'{_delta_html(m, latest_and_delta(data[m], days)[2])}{sparkline(data[m])}</div>'
             for m in metrics[1:]]
    if tiles:
        body.append('<div class="tiles">' + "".join(tiles) + "</div>")

    svg_panels: list[str] = []
    for m in metrics:
        inner = panel_body(m, days, data[m], means[m])
        if not inner:
            continue
        body.append(f'<div class="card panel"><h2>{html.escape(m.label)}'
                    f'{" (" + html.escape(m.unit) + ")" if m.unit else ""}</h2>'
                    f'<svg viewBox="0 0 {PW} {PH}" role="img" aria-label="{html.escape(m.label)} over time">'
                    f'{inner}</svg></div>')
        svg_panels.append((m, inner))

    # Table view: the accessibility twin, newest day first, collapsed by default.
    head = "".join(f"<th>{html.escape(m.label)}{(' (' + html.escape(m.unit) + ')') if m.unit else ''}</th>"
                   for m in metrics)
    rows = []
    for i in range(len(days) - 1, -1, -1):
        if all(data[m][i] is None for m in metrics):
            continue
        cells = "".join(f"<td>{html.escape(m.fmt(data[m][i]))}</td>" for m in metrics)
        rows.append(f"<tr><td>{days[i]:%Y-%m-%d}</td>{cells}</tr>")
    body.append(f'<details><summary>Table view ({len(rows)} days with data)</summary>'
                f'<table><thead><tr><th>Day</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></details>')
    body.append('<p class="foot">Built from your own Oura data export. Durations in the CSVs are in seconds; '
                'shown here in hours. Not affiliated with Oura.</p>')

    html_doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width, initial-scale=1">'
                f'<title>Oura dashboard</title><style>{CSS}</style></head>'
                f'<body class="viz"><div class="wrap">{"".join(body)}</div></body></html>')
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "dashboard.html"
    html_path.write_text(html_doc, encoding="utf-8")
    (out_dir / "dashboard.svg").write_text(_standalone_svg(svg_panels, end, now), encoding="utf-8")
    log.info("Wrote %s", html_path)
    return html_path


def _standalone_svg(panels, end: date, now: datetime) -> str:
    """The same panels in one SVG file: a fallback the Files app always previews."""
    title_h, gap = 30, 14
    total_h = 40 + len(panels) * (PH + title_h + gap)
    style = ("<style>svg{--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;"
             "--axis:#c3c2b7;--accent:#2a78d6}"
             "@media (prefers-color-scheme:dark){svg{--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;"
             "--grid:#2c2c2a;--axis:#383835;--accent:#3987e5}}"
             ".bg{fill:var(--surface)}.h{fill:var(--ink);font:600 15px system-ui,sans-serif}"
             ".s{fill:var(--ink2);font:12px system-ui,sans-serif}"
             ".grid{stroke:var(--grid)}.axis{stroke:var(--axis)}"
             ".tick{fill:var(--muted);font:11px system-ui,sans-serif}"
             ".raw{fill:none;stroke:var(--muted);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}"
             ".mean{fill:none;stroke:var(--accent);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}"
             ".end{fill:var(--accent);stroke:var(--surface);stroke-width:2}"
             ".label{fill:var(--ink);font:600 12px system-ui,sans-serif}.hit{fill:transparent}</style>")
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {PW} {total_h}" width="{PW}" height="{total_h}">',
           style, f'<rect class="bg" width="{PW}" height="{total_h}"/>',
           f'<text class="h" x="16" y="24">Oura · last {WINDOW_DAYS} days to {end:%-d %b %Y}</text>']
    yoff = 40
    for m, inner in panels:
        label = f"{m.label}{' (' + m.unit + ')' if m.unit else ''}"
        out.append(f'<text class="s" x="16" y="{yoff + 18}">{html.escape(label)}</text>')
        out.append(f'<g transform="translate(0,{yoff + title_h})">{inner}</g>')
        yoff += PH + title_h + gap
    out.append("</svg>")
    return "\n".join(out)
