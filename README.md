# Oura Export

Scheduled, local download of **your own** Oura data — the full CSV export Oura
lets you request under data-portability — so you can keep and analyse your
history without a subscription.

> **Clean-room, personal use.** This project automates the official
> [Membership Hub export](https://membership.ouraring.com/data-export) page while
> logged in as you, and writes files to your own disk. It contains no Oura code
> and does **not** decompile, instrument, or reverse-engineer the Oura app. The
> analysis layer is built from the exported data itself. Not affiliated with
> Oura. Use on your own account and data.

## What it does

1. `login` — one-time, interactive. Logs into the Membership Hub with the
   passwordless email-code flow and saves the session cookie locally.
2. `fetch` — scheduled. Requests a fresh export, waits for Oura to build it,
   downloads the zip, and unpacks it into a tidy folder tree.
3. An R (`data.table`) layer turns the CSVs into a merged daily table for analysis.

```
~/OuraData/
  raw/2026-10-15/oura_export_2026-10-15.zip   # untouched download
  extracted/2026-10-15/*.csv                  # that day's CSVs
  latest/*.csv                                # always the newest export
```

On macOS you can set `data_dir` into iCloud Drive so the CSVs appear in the
Files app on your iPhone.

## Requirements

- Python 3.11+
- An Oura account (any ring). *Note:* Oura now requires a membership for **API**
  access on Gen3/Ring 4/Ring 5, but the **export used here is available to all
  users** — that is the whole point of this tool.

## Install

```bash
git clone https://github.com/rahul-suhag/oura.git
cd oura
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -e .
python -m playwright install chromium                 # one-time browser download
```

## First run

```bash
oura-export init --email you@example.com   # writes ~/.oura-export/config.json
oura-export login                          # opens a browser; paste the emailed code
oura-export fetch                          # request + download + unpack
oura-export status                         # see config and session state
```

`login` is the only step that needs you present — Oura emails a 6-digit code and
there is no password. The saved session lets scheduled `fetch` runs proceed
unattended **until the cookie expires**, at which point `fetch` stops and tells
you (and sends a desktop notification) to run `login` again. We deliberately do
**not** read your mailbox to re-login automatically.

## Scheduling

Your Windows desktop is the more reliable host (it's almost always on), but both
are set up to catch up a missed run after wake/power-on.

**macOS (launchd):** edit the executable path in
`scripts/launchd/com.rahulsuhag.oura-export.plist`, copy it to
`~/Library/LaunchAgents/`, then `launchctl load` it. (launchd runs a missed job
when the Mac next wakes — better than cron for a laptop.)

**Windows (Task Scheduler):** in an elevated PowerShell:

```powershell
.\scripts\windows\Register-OuraExportTask.ps1 -OuraExportExe "C:\path\to\venv\Scripts\oura-export.exe" -At "11:00"
```

## Analysis (R / data.table)

```bash
Rscript analysis/load_oura.R ~/OuraData/latest    # writes daily_merged.csv
```

See `analysis/README.md`. Requires R with `data.table` installed.

## Privacy & security

- Your email, the session cookie, and all downloaded data live under
  `~/.oura-export/` and your `data_dir` — **never inside this repo**. `.gitignore`
  enforces that, so the repo stays safe to keep private (recommended) or even
  public without leaking anything.
- The session cookie is a bearer credential; treat `~/.oura-export/session.json`
  like a password.

## Status & limitations

- The archive/config logic is covered by unit tests (`pytest`).
- **The live login and download have not been verified end-to-end** — the build
  environment has no network access to Oura. Expect to run `oura-export login
  --headful` once to confirm the selectors match the current page. The login and
  export selectors are isolated and commented in `oura_export/hub.py` under
  `>>> Adjust here <<<` markers for easy fixing if Oura changes the page.
- Oura may rate-limit how often an export can be requested. Daily is the default;
  if Oura throttles, increase the interval.
- How frequently new data appears in the export is governed by when your ring
  last synced to the Oura app — this tool can't make data appear faster than that.

## License

MIT — see `LICENSE`.
