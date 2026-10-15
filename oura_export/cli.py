"""Command-line entry point.

    oura-export login     one-time, interactive: logs in, pastes emailed code
    oura-export fetch      scheduled: request a new export, wait, download, archive
    oura-export download   download the latest EXISTING export (no new request)
    oura-export status     show config + whether the saved session still works
    oura-export init       write a starter config.json

All commands exit non-zero on failure so a scheduler can detect problems.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from datetime import date
from pathlib import Path

from . import __version__, config as config_mod, paths
from .notify import notify


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(paths.state_dir() / "oura-export.log"),
        ],
    )


def _load_cfg(args) -> config_mod.Config:
    cfg = config_mod.load(Path(args.config) if args.config else None)
    if args.headful:
        cfg.headless = False
    return cfg


def cmd_init(args) -> int:
    path = Path(args.config) if args.config else paths.default_config_path()
    if path.exists() and not args.force:
        print(f"Config already exists at {path} (use --force to overwrite).")
        return 1
    cfg = config_mod.Config(**config_mod.DEFAULTS)
    if args.email:
        cfg.email = args.email
    config_mod.save(cfg, path)
    print(f"Wrote starter config to {path}. Edit it, then run `oura-export login`.")
    return 0


def cmd_status(args) -> int:
    cfg = _load_cfg(args)
    print(f"version       : {__version__}")
    print(f"email         : {cfg.email or '(not set)'}")
    print(f"data_dir      : {cfg.data_path}")
    print(f"session file  : {paths.session_path()} "
          f"({'present' if paths.session_path().exists() else 'missing'})")
    # Avoid launching a browser just for status unless asked.
    if args.check:
        from .hub import OuraHub
        with OuraHub(email=cfg.email, headless=cfg.headless) as hub:
            ok = hub.is_authenticated()
        print(f"session valid : {ok}")
        return 0 if ok else 2
    return 0


def _run_pipeline(cfg: config_mod.Config, request_new: bool) -> int:
    from .hub import OuraHub, SessionExpired, HubError
    from . import archive

    try:
        with OuraHub(email=cfg.email, headless=cfg.headless) as hub:
            if not hub.is_authenticated():
                raise SessionExpired("Saved session invalid. Run `oura-export login`.")

            if request_new:
                hub.request_export()
                ready = hub.wait_until_ready(
                    timeout_minutes=cfg.request_timeout_minutes,
                    poll_seconds=cfg.poll_interval_seconds,
                )
                if not ready:
                    raise HubError("Timed out waiting for Oura to build the export.")

            with tempfile.TemporaryDirectory() as tmp:
                zip_path = hub.download(Path(tmp) / "oura_export.zip")
                result = archive.archive_zip(zip_path, cfg.data_path, date.today())

        msg = f"{len(result.csv_files)} CSVs -> {result.latest_dir}"
        logging.getLogger("oura_export").info("Done: %s", msg)
        if cfg.notify:
            notify("Oura export updated", msg)
        print(msg)
        return 0

    except SessionExpired as e:
        logging.getLogger("oura_export").error("%s", e)
        if cfg.notify:
            notify("Oura export needs login", str(e))
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - top-level reporter for the scheduler
        logging.getLogger("oura_export").exception("Pipeline failed")
        if cfg.notify:
            notify("Oura export failed", str(e))
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def cmd_fetch(args) -> int:
    return _run_pipeline(_load_cfg(args), request_new=True)


def cmd_download(args) -> int:
    return _run_pipeline(_load_cfg(args), request_new=False)


def cmd_login(args) -> int:
    cfg = _load_cfg(args)
    if not cfg.email:
        print("Set your email first: `oura-export init --email you@example.com`.",
              file=sys.stderr)
        return 1
    from .hub import OuraHub, HubError

    def ask_code() -> str:
        return input("Enter the 6-digit code Oura emailed you: ")

    # Login is easiest to watch; default to headful unless explicitly headless.
    headless = cfg.headless and not args.headful and args.headless_login
    try:
        with OuraHub(email=cfg.email, headless=headless) as hub:
            hub.interactive_login(ask_code)
        print("Logged in. Session saved; scheduled `fetch` runs can now proceed.")
        return 0
    except HubError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="oura-export", description=__doc__)
    p.add_argument("--config", help="Path to config.json (default: ~/.oura-export/config.json)")
    p.add_argument("--headful", action="store_true", help="Show the browser window.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="Write a starter config.json")
    s.add_argument("--email")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("login", help="Interactive one-time login")
    s.add_argument("--headless-login", action="store_true",
                   help="Attempt login without showing the browser (advanced).")
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("fetch", help="Request a new export, wait, download, archive")
    s.set_defaults(func=cmd_fetch)

    s = sub.add_parser("download", help="Download the latest existing export only")
    s.set_defaults(func=cmd_download)

    s = sub.add_parser("status", help="Show config and session state")
    s.add_argument("--check", action="store_true", help="Also open a browser to test the session.")
    s.set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
