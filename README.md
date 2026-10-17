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

A job that runs every few hours on your computer. Each run:

1. Opens the Membership Hub's export page in a hidden browser that keeps its
   login like your everyday browser does.
2. **If Oura has signed it out**, it signs back in by itself: enters your email,
   chooses "Email me a code" (never the passkey button), reads the 6-digit code
   from your Gmail, and types it in.
3. Collects a finished export, but only if it is genuinely new (it compares
   file fingerprints, so an old file is never saved as a new one).
4. Requests the next export about once a day, and waits a while for it. If Oura
   is slow, a later run collects it.

```
~/OuraData/
  STATUS.txt                                  # last run, readable from your phone
  raw/2026-10-15/oura_export_2026-10-15.zip   # untouched download
  extracted/2026-10-15/*.csv                  # that day's CSVs
  latest/*.csv                                # always the newest export
```

Point `data_dir` into iCloud Drive or OneDrive and the CSVs (and STATUS.txt)
show up on your iPhone.

## Requirements

- Python 3.11+ on the machine that runs the schedule (your always-on Windows
  desktop is the best host; a Mac works too, but use only one of them).
- An Oura account whose sign-in codes go to **Gmail**. (Oura now requires a
  membership for API access on Gen3 / Ring 4 / Ring 5, but the export used here
  is available to everyone.)

## Install

```bash
git clone https://github.com/rahul-suhag/Oura.git
cd Oura
python -m venv venv
venv\Scripts\activate                 # macOS: source venv/bin/activate
pip install -e .
python -m playwright install chromium
```

## One-time setup (about 10 minutes)

1. **Config**

   ```bash
   oura-export init --email you@gmail.com --data-dir "C:/Users/you/iCloudDrive/OuraData"
   ```

2. **Gmail app password** so the job can read Oura's code email.
   Turn on 2-Step Verification for your Google account if it isn't already,
   then create an app password at <https://myaccount.google.com/apppasswords>
   (name it "oura-export"). Then:

   ```bash
   oura-export set-mail-password
   ```

   It checks the password with Gmail before storing it in Windows Credential
   Manager / macOS Keychain. It is never written to a file.

3. **First sign-in**, with a visible browser so you can watch:

   ```bash
   oura-export login
   ```

   It fills in your email, picks "Email me a code" and reads the code from
   Gmail itself. If this works, unattended sign-in works.

4. **Schedule it** (pick one machine):

   - Windows: `powershell -ExecutionPolicy Bypass -File scripts\windows\Register-OuraExportTask.ps1`
   - macOS: edit the paths in `scripts/launchd/com.rahulsuhag.oura-export.plist`,
     copy it to `~/Library/LaunchAgents/` and `launchctl load` it.

Check on it any time with `oura-export status`, or open `STATUS.txt`.

## On your iPhone

After every fetch the job also writes **`dashboard.html`** (and `dashboard.svg`)
into the data folder: your latest readiness, sleep and activity scores, sleep
hours, HRV, heart rate, steps and more, with 90-day trends and a table view.
It is one self-contained file with no scripts and no outside links, so the
Files app shows it as-is, light or dark to match your phone.

1. Put `data_dir` inside iCloud Drive (or OneDrive) when you run `init`, e.g.
   `C:/Users/you/iCloudDrive/OuraData`.
2. On the iPhone: Files → iCloud Drive → OuraData → `dashboard.html`.
3. For a Home Screen icon: Shortcuts → + → add **"Get File"** (pick
   `dashboard.html`, turn off "Show Document Picker") → add **"Quick Look"** →
   name it "Oura" → ⋯ → **Add to Home Screen**. One tap opens the latest view.

If the HTML ever shows blank in a preview, open `dashboard.svg` instead; it is
the same charts as a plain image.

## Commands

| Command | What it does |
|---|---|
| `oura-export init` | write `~/.oura-export/config.json` |
| `oura-export set-mail-password` | check and store the Gmail app password |
| `oura-export login` | sign in once with a visible browser |
| `oura-export run` | one scheduled run (`fetch` is an alias); `--show-browser` to watch |
| `oura-export dashboard` | rebuild `dashboard.html`/`.svg` from the newest CSVs |
| `oura-export status` | settings, last run, whether sign-in is needed |

Exit codes for schedulers: 0 ok, 1 error, 2 sign-in needed, 3 another run busy.

## Analysis (R / data.table)

```bash
Rscript analysis/load_oura.R ~/OuraData/latest    # writes daily_merged.csv
Rscript analysis/eda_report.R ~/OuraData/latest   # EDA report in latest/eda/
```

See `analysis/README.md`. Requires R with `data.table` and `ggplot2`.

## Privacy & security

- Config, job state, logs and the browser profile live in `~/.oura-export/`;
  exports live in your `data_dir`. **Nothing personal is ever inside this repo**
  (`.gitignore` enforces it).
- `~/.oura-export/browser-profile` holds your Oura login: treat it like a password.
- The Gmail **app password** can read your whole mailbox. It is kept in your
  system keychain, the job only ever opens mail read-only (it never marks,
  moves or deletes anything), and you can revoke it at any time at
  <https://myaccount.google.com/apppasswords>. For tighter isolation, add a
  Gmail filter that forwards Oura's emails to a separate Gmail account and give
  the job that account's app password instead (set `imap_user` in the config).
- Unattended sign-in is capped at 3 attempts per 24 h so a problem can't flood
  your inbox or get the account locked.

## Status & limitations

- Tested: archive/config/state logic, reading codes from email (fake mailbox),
  and the full unattended loop (sign-in, re-sign-in after the login expires,
  request, wait, download, "old file is not new") against a fake copy of the
  Oura site in a real headless Chromium. `pytest` runs all of it.
- **Not yet verified against Oura's real pages.** The selectors come from
  Cracked-Oura and may be out of date. They all sit in one block at the top of
  `oura_export/hub.py`. Any failure saves a screenshot and the page HTML to
  `~/.oura-export/debug/` so it can be fixed quickly.
- Oura may limit how often an export can be requested. The default is about
  once a day (`min_hours_between_requests`).
- How frequently new data appears in the export is governed by when your ring
  last synced to the Oura app — this tool can't make data appear faster than that.

## License

MIT — see `LICENSE`.
