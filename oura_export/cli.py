"""Command-line entry point.

    oura-export init               write a starter config.json
    oura-export set-mail-password  store your Gmail app password in the OS keychain
    oura-export login              sign in once with a visible browser
    oura-export run                one scheduled run (alias: fetch)
    oura-export dashboard          rebuild dashboard.html/.svg from the newest CSVs
    oura-export status             show settings and the last run

Exit codes (for schedulers): 0 ok, 1 error, 2 sign-in needed, 3 another run busy.
"""

from __future__ import annotations

import argparse
import getpass
import imaplib
import logging
import sys
from pathlib import Path

from . import __version__, codes, config as config_mod, job, paths
from .state import State


def _setup_logging() -> None:
    handlers: list[logging.Handler] = [logging.FileHandler(paths.state_dir() / "oura-export.log")]
    if sys.stderr is not None:  # pythonw.exe (Windows scheduler) has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def _load_cfg(args) -> config_mod.Config:
    return config_mod.load(Path(args.config) if args.config else None)


def cmd_init(args) -> int:
    path = Path(args.config) if args.config else paths.default_config_path()
    if path.exists() and not args.force:
        print(f"Config already exists at {path} (use --force to overwrite).")
        return 1
    cfg = config_mod.Config(**config_mod.DEFAULTS)
    if args.email:
        cfg.email = args.email
    if args.data_dir:
        cfg.data_dir = args.data_dir
    config_mod.save(cfg, path)
    print(f"Wrote {path}.\nNext: oura-export set-mail-password, then oura-export login.")
    return 0


def cmd_set_mail_password(args) -> int:
    cfg = _load_cfg(args)
    user = cfg.imap_user or cfg.email
    if not user:
        print("Set your email first: oura-export init --email you@gmail.com", file=sys.stderr)
        return 1
    try:
        import keyring
    except ImportError:
        print(f"The 'keyring' package is missing; set {codes.PASSWORD_ENV} instead.", file=sys.stderr)
        return 1
    password = getpass.getpass(f"Gmail app password for {user} (typing is hidden): ").replace(" ", "")
    if not password:
        print("Nothing entered; unchanged.")
        return 1
    try:  # check it before storing, so a typo shows up now rather than at 3 a.m.
        with imaplib.IMAP4_SSL(cfg.imap_host, 993, timeout=30) as conn:
            conn.login(user, password)
    except (imaplib.IMAP4.error, OSError) as exc:
        print(f"Gmail rejected that password ({exc}). Not stored.", file=sys.stderr)
        return 1
    keyring.set_password(codes.KEYRING_SERVICE, user, password)
    print("Gmail accepted it. Stored in your system keychain (not in any file).")
    return 0


def cmd_login(args) -> int:
    cfg = _load_cfg(args)
    if not cfg.email:
        print("Set your email first: oura-export init --email you@gmail.com", file=sys.stderr)
        return 1
    # Use the mailbox if it's set up, so this also proves the unattended path works.
    source = codes.make_code_source(cfg, interactive=True)
    print("Signing in to Oura" + (" (reading the code from Gmail)..." if isinstance(source, codes.ImapCodeSource)
                                  else "; you'll be asked for the emailed code..."))
    code = job.interactive_login(cfg, source, headless=args.headless)
    print("Signed in. Scheduled runs will reuse this login." if code == job.OK
          else f"Sign-in failed. Screenshots are in {paths.debug_dir()}")
    return code


def cmd_run(args) -> int:
    cfg = _load_cfg(args)
    source = codes.make_code_source(cfg, interactive=args.interactive)
    return job.run_once(cfg, source, headless=False if args.show_browser else None)


def cmd_dashboard(args) -> int:
    """Rebuild the dashboard from the newest CSVs (also runs after every fetch)."""
    from . import dashboard
    cfg = _load_cfg(args)
    latest = Path(args.latest) if args.latest else cfg.data_path / "latest"
    out = Path(args.out) if args.out else cfg.data_path
    try:
        path = dashboard.write_dashboard(latest, out)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {path} and {path.with_suffix('.svg')}")
    return 0


def cmd_status(args) -> int:
    cfg = _load_cfg(args)
    state = State.load(paths.state_file())
    user = cfg.imap_user or cfg.email
    print(f"version            : {__version__}")
    print(f"email              : {cfg.email or '(not set)'}")
    print(f"data_dir           : {cfg.data_path}")
    print(f"code source        : {cfg.code_source}"
          + (f" ({'password stored' if user and codes.mail_password(user) else 'NO password stored'})"
             if cfg.code_source == 'gmail' else ""))
    print(f"browser profile    : {paths.profile_dir()} "
          f"({'present' if paths.profile_dir().exists() else 'missing'})")
    print(f"last run           : {state.last_run_at or '-'}")
    print(f"last result        : {state.last_result}")
    print(f"last new export    : {state.last_new_export_at or '-'}")
    print(f"waiting since      : {state.pending_since or '-'}")
    print(f"sign-in needed     : {state.login_needed}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="oura-export", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help="Path to config.json (default: ~/.oura-export/config.json)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="Write a starter config.json")
    s.add_argument("--email")
    s.add_argument("--data-dir")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("set-mail-password", help="Store and check your Gmail app password")
    s.set_defaults(func=cmd_set_mail_password)

    s = sub.add_parser("login", help="Sign in once (visible browser)")
    s.add_argument("--headless", action="store_true", help="Don't show the browser.")
    s.set_defaults(func=cmd_login)

    for name in ("run", "fetch"):
        s = sub.add_parser(name, help="One scheduled run" + (" (alias of run)" if name == "fetch" else ""))
        s.add_argument("--show-browser", action="store_true", help="Show the browser (debugging).")
        s.add_argument("--interactive", action="store_true",
                       help="If sign-in is needed and Gmail isn't set up, ask for the code.")
        s.set_defaults(func=cmd_run)

    s = sub.add_parser("dashboard", help="Rebuild dashboard.html/.svg from the newest CSVs")
    s.add_argument("--latest", help="Folder of CSVs (default: <data_dir>/latest)")
    s.add_argument("--out", help="Where to write (default: <data_dir>)")
    s.set_defaults(func=cmd_dashboard)

    s = sub.add_parser("status", help="Show settings and the last run")
    s.set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
