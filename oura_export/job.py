"""One scheduled run.

    1. Open the export page; if signed out, sign in using the emailed code.
    2. Collect a finished export (only if it is genuinely new).
    3. Request the next export once enough time has passed.
    4. Wait a while for it; if it isn't ready, a later run collects it.

Every run is short and self-contained, so a sleeping PC, a slow export (Oura
says up to 10 days) or a crash never leaves things stuck.
"""

from __future__ import annotations

import imaplib
import logging
import os
import shutil
import socket
import time
from datetime import datetime, timedelta

from . import archive, paths
from .codes import CodeSource, utcnow
from .config import Config
from .hub import Hub, HubError, PageState, open_browser
from .notify import notify
from .state import State

log = logging.getLogger("oura_export.job")

OK, ERROR, LOGIN_NEEDED, BUSY = 0, 1, 2, 3

# After a download turns out identical to the last export, don't fetch the
# same file again for this long within one run.
_REDOWNLOAD_BACKOFF_S = 600


class AlreadyRunning(RuntimeError):
    pass


class RunLock:
    """Stops a scheduled run and a manual `login` fighting over the browser profile."""

    def __init__(self, path, stale_after_s: float = 2 * 3600):
        self.path = path
        self.stale_after_s = stale_after_s

    def __enter__(self) -> "RunLock":
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age > self.stale_after_s:  # left behind by a crashed run
                    self.path.unlink(missing_ok=True)
                    continue
                raise AlreadyRunning(f"lock held: {self.path}")
        raise AlreadyRunning(f"lock held: {self.path}")

    def __exit__(self, *exc) -> None:
        self.path.unlink(missing_ok=True)


# --- public entry points ----------------------------------------------------

def run_once(cfg: Config, code_source: CodeSource | None, *, now: datetime | None = None,
             browser=open_browser, headless: bool | None = None) -> int:
    now = now or utcnow()
    try:
        with RunLock(paths.lock_file()):
            state = State.load(paths.state_file())
            code, result = _run(cfg, state, code_source, now, browser,
                                cfg.headless if headless is None else headless)
            state.last_run_at = now
            state.last_result = result
            state.save(paths.state_file())
            write_status(cfg, state)
    except AlreadyRunning:
        log.info("Another run is in progress; skipping this one.")
        return BUSY
    log.info("Run finished (exit %s): %s", code, result)
    if cfg.notify and (code != OK or "saved new export" in result):
        notify("Oura export", result[:200])
    return code


def interactive_login(cfg: Config, code_source: CodeSource, *, browser=open_browser,
                      headless: bool = False) -> int:
    """Sign in with a person present (visible browser by default)."""
    with RunLock(paths.lock_file()):
        state = State.load(paths.state_file())
        with browser(paths.profile_dir(), headless) as ctx:
            hub = Hub(ctx)
            ok = hub.goto_export_page() == "ready" or hub.login(cfg.email, code_source, code_timeout_s=600)
            if not ok:
                hub.capture_debug(paths.debug_dir(), "login-failed")
        state.login_needed = not ok
        if ok:
            state.login_attempts = []
        state.last_result = "signed in" if ok else "sign-in failed"
        state.save(paths.state_file())
        write_status(cfg, state)
    return OK if ok else LOGIN_NEEDED


# --- internals ----------------------------------------------------------------

def _run(cfg, state, code_source, now, browser, headless) -> tuple[int, str]:
    try:
        with browser(paths.profile_dir(), headless) as ctx:
            hub = Hub(ctx)
            try:
                if hub.goto_export_page() == "login":
                    problem = _sign_in(hub, cfg, state, code_source, now)
                    if problem:
                        return LOGIN_NEEDED, problem
                state.login_needed = False
                notes = _export_cycle(hub, cfg, state, now)
                return OK, "; ".join(notes) or "nothing to do"
            except Exception:
                hub.capture_debug(paths.debug_dir(), "error")
                raise
    except Exception as exc:  # noqa: BLE001 - reported to the scheduler via exit code
        log.exception("Run failed")
        return ERROR, f"ERROR: {exc}"


def _sign_in(hub: Hub, cfg: Config, state: State, code_source, now) -> str | None:
    """Sign in unattended. Returns None on success, else why it couldn't."""
    state.login_needed = True
    if code_source is None:
        # Don't trigger a code email nobody can read.
        return "LOGIN NEEDED: no way to read the sign-in code (run `oura-export set-mail-password` or `oura-export login`)"
    if not cfg.email:
        return "LOGIN NEEDED: no email in the config"
    recent = [t for t in state.login_attempts if now - t < timedelta(hours=24)]
    if len(recent) >= cfg.max_logins_per_day:
        state.login_attempts = recent
        return (f"LOGIN NEEDED: {len(recent)} sign-in attempts in the last 24 h; "
                "pausing to protect your account")
    state.login_attempts = recent + [now]
    try:
        ok = hub.login(cfg.email, code_source)
    except (HubError, imaplib.IMAP4.error) as exc:
        hub.capture_debug(paths.debug_dir(), "login-failed")
        return f"LOGIN NEEDED: {exc}"
    if not ok:
        hub.capture_debug(paths.debug_dir(), "login-failed")
        return "LOGIN NEEDED: sign-in did not complete (no code arrived, or Oura rejected it)"
    state.login_needed = False
    log.info("Signed in to Oura.")
    return None


def _export_cycle(hub: Hub, cfg: Config, state: State, now: datetime) -> list[str]:
    notes: list[str] = []
    page = hub.read_state()

    if not page.request_enabled and state.pending_since is None:
        # Oura is building an export this tool didn't ask for (e.g. you pressed
        # the button yourself). Adopt it so it gets collected.
        state.pending_since = now
        notes.append("Oura is already building an export; adopting it")

    notes += _collect_if_ready(hub, cfg, state, page, now)

    if state.pending_since and now - state.pending_since > timedelta(hours=cfg.give_up_after_hours):
        notes.append(f"gave up waiting after {cfg.give_up_after_hours:g} h")
        state.pending_since = None

    due = (state.last_request_at is None
           or now - state.last_request_at >= timedelta(hours=cfg.min_hours_between_requests))
    if state.pending_since is None and page.request_enabled and due:
        accepted = hub.request_export()
        state.last_request_at = state.pending_since = now
        notes.append("requested a new export" if accepted
                     else "pressed Request, but the button didn't change; will check")
    # Wait in-run only for a fresh request; an export Oura has been building for
    # hours is just checked once per run instead of holding the browser open.
    if state.pending_since is not None and now - state.pending_since <= timedelta(minutes=cfg.wait_minutes):
        notes += _wait_for_export(hub, cfg, state, now)
    elif state.pending_since is not None:
        notes.append(f"waiting for Oura since {state.pending_since.astimezone():%Y-%m-%d %H:%M}")
    return notes


def _collect_if_ready(hub: Hub, cfg: Config, state: State, page: PageState,
                      now: datetime) -> list[str]:
    # "Finished" = we're waiting on an export and the Request button is usable
    # again. On the very first run we also take whatever export is there.
    finished = state.pending_since is not None and page.request_enabled
    if not page.download_available or not (finished or state.last_export_sha256 is None):
        return []
    work = paths.work_dir()
    shutil.rmtree(work, ignore_errors=True)
    try:
        zip_path = hub.download(work)
        digest = archive.sha256_of(zip_path)
        if digest == state.last_export_sha256:
            # Oura may re-enable the button before the new file is in place.
            return ["download matches the previous export; still waiting for the new one"]
        result = archive.archive_zip(zip_path, cfg.data_path, now.astimezone().date())
        state.last_export_sha256 = digest
        state.last_new_export_at = now
        if finished:
            state.pending_since = None
        return [f"saved new export ({len(result.csv_files)} CSVs) to {result.latest_dir}"]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _wait_for_export(hub: Hub, cfg: Config, state: State, now: datetime) -> list[str]:
    notes: list[str] = []
    deadline = time.monotonic() + cfg.wait_minutes * 60
    next_download_ok = 0.0
    while state.pending_since is not None and time.monotonic() < deadline:
        time.sleep(cfg.poll_interval_seconds)
        if hub.goto_export_page() != "ready":
            raise HubError("Signed out while waiting for the export")
        page = hub.read_state()
        if time.monotonic() < next_download_ok:
            continue
        got = _collect_if_ready(hub, cfg, state, page, now)
        if got and got[0].startswith("download matches"):
            next_download_ok = time.monotonic() + _REDOWNLOAD_BACKOFF_S
        elif got:
            notes += got
    if state.pending_since is not None:
        notes.append("export not ready yet; a later run will collect it")
    return notes


def write_status(cfg: Config, state: State) -> None:
    """A plain STATUS.txt next to the data, readable from the phone."""
    def fmt(t: datetime | None) -> str:
        return t.astimezone().strftime("%Y-%m-%d %H:%M") if t else "-"

    lines = [f"Oura export status (updated {fmt(state.last_run_at)} on {socket.gethostname()})", ""]
    if state.login_needed:
        lines += ["ACTION NEEDED: the job could not sign in to Oura by itself.",
                  "On the computer that runs the schedule, run:  oura-export login", ""]
    lines += [f"Last result:           {state.last_result}",
              f"Last new export:       {fmt(state.last_new_export_at)}",
              f"Waiting on Oura since: {fmt(state.pending_since)}",
              f"Last request:          {fmt(state.last_request_at)}",
              "", "Newest CSVs are in the 'latest' folder."]
    cfg.data_path.mkdir(parents=True, exist_ok=True)
    (cfg.data_path / "STATUS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
