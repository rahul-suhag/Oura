"""Drive the Oura Membership Hub with Playwright: sign in, request, download.

Scope: this acts AS YOU on a page you could operate by hand, to fetch YOUR
OWN export. It contains no Oura code.

Sign-in model (2026): passwordless. Email -> "Email me a code" -> 6-digit code.
The same screen also offers "Use passkey"; we must never click that one
(Cracked-Oura issue #20 crashed because both buttons share name="selectedId").
We pick buttons by their visible name instead.

A persistent browser profile keeps cookies, local storage and IndexedDB between
runs, the same way your everyday browser stays signed in.
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Locator,
    Page,
    TimeoutError as PWTimeout,
    sync_playwright,
)

from .codes import CodeSource, utcnow

log = logging.getLogger("oura_export.hub")

BASE_URL = "https://membership.ouraring.com"
EXPORT_URL = f"{BASE_URL}/data-export"

# ---------------------------------------------------------------------------
# >>> SELECTORS: the only place to edit if Oura changes its pages. <<<
# Sources: Cracked-Oura (March 2026) and its issue #20 (Sept 2026). Not yet
# confirmed against the live site.
# ---------------------------------------------------------------------------
EMAIL_INPUT = "input[name='username'], input[name='email'], input[type='email']"
CODE_INPUT = ("input[name='otp'], #otp-code, input[name='verification_code'], "
              "input[name='code'], input[autocomplete='one-time-code']")
PASSWORD_INPUT = "input[type='password']"
METHOD_BUTTONS = "button[name='selectedId']"
EMAIL_CODE_NAME = re.compile(r"e-?mail.*code|send.*code", re.I)
SUBMIT_NAME = re.compile(r"^(continue|next|submit|verify|sign in|log in)$", re.I)
REQUEST_BUTTON = '[data-testid="pageSubtitle"] + button'
REQUEST_NAME = re.compile(r"request", re.I)
DOWNLOAD_BUTTON = "button[aria-label='Download data']"
DOWNLOAD_NAME = re.compile(r"download", re.I)
# ---------------------------------------------------------------------------


class HubError(RuntimeError):
    """The page did not behave as expected (changed layout, timeout, ...)."""


@dataclass(frozen=True)
class PageState:
    request_enabled: bool
    download_available: bool


@contextmanager
def open_browser(profile_dir: Path, headless: bool,
                 setup: Callable[[BrowserContext], None] | None = None) -> Iterator[BrowserContext]:
    """Launch Chromium on a persistent profile.

    channel="chromium" uses the full Chromium build for headless runs too, so
    the visible sign-in and the hidden scheduled runs share one browser and
    one profile format.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(profile_dir), channel="chromium", headless=headless,
            accept_downloads=True, viewport={"width": 1280, "height": 900},
        )
        try:
            if setup is not None:
                setup(ctx)  # tests install a fake Oura here
            yield ctx
        finally:
            ctx.close()


class Hub:
    def __init__(self, ctx: BrowserContext):
        self.page: Page = ctx.pages[0] if ctx.pages else ctx.new_page()

    # -- locators ------------------------------------------------------------
    def _request_button(self) -> Locator:
        p = self.page
        return p.locator(REQUEST_BUTTON).or_(p.get_by_role("button", name=REQUEST_NAME)).first

    def _download_control(self) -> Locator:
        p = self.page
        return (p.locator(DOWNLOAD_BUTTON)
                .or_(p.get_by_role("button", name=DOWNLOAD_NAME))
                .or_(p.get_by_role("link", name=DOWNLOAD_NAME))).first

    def _visible(self, selector: str) -> bool:
        return self.page.locator(selector).first.is_visible()

    def _login_form_visible(self) -> bool:
        # Judged by visible controls, not the URL: redirect URLs after a
        # successful sign-in also contain words like "auth".
        return (self._visible(EMAIL_INPUT) or self._visible(CODE_INPUT)
                or self._visible(METHOD_BUTTONS))

    def _export_page_ready(self) -> bool:
        return ("/data-export" in self.page.url
                and (self._request_button().is_visible() or self._download_control().is_visible()))

    def _settle(self, extra_ms: int = 3_000) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=10_000)
        except PWTimeout:
            pass
        # The Hub renders buttons before wiring them up; give it a moment.
        self.page.wait_for_timeout(extra_ms)

    # -- navigation ----------------------------------------------------------
    def goto_export_page(self, timeout_s: float = 60) -> str:
        """Open the export page. Returns "ready" or "login"."""
        self.page.goto(EXPORT_URL, wait_until="domcontentloaded", timeout=60_000)
        deadline = time.monotonic() + timeout_s
        home_retries = 0
        while time.monotonic() < deadline:
            try:
                if self._export_page_ready():
                    self._settle()
                    return "ready"
                if self._login_form_visible():
                    return "login"
                if self.page.url.rstrip("/") == BASE_URL and home_retries < 3:
                    # Signed-in users sometimes land on the Hub home first.
                    home_retries += 1
                    self.page.goto(EXPORT_URL, wait_until="domcontentloaded", timeout=60_000)
            except PlaywrightError:
                pass  # page navigated mid-check; look again
            self.page.wait_for_timeout(500)
        raise HubError(f"Data export page never loaded (stuck at {self.page.url})")

    # -- sign-in -------------------------------------------------------------
    def login(self, email: str, code_source: CodeSource, code_timeout_s: float = 180) -> bool:
        """Passwordless sign-in. True once the export page is reachable."""
        page = self.page
        if not self._visible(EMAIL_INPUT):
            page.locator(EMAIL_INPUT).first.wait_for(state="visible", timeout=30_000)

        # Codes emailed before this moment belong to someone else's attempt.
        sent_after = utcnow()
        page.locator(EMAIL_INPUT).first.fill(email)
        self._submit()

        # Either a method chooser (passkey / email code) or straight to the code box.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self._visible(CODE_INPUT):
                break
            if self._visible(PASSWORD_INPUT):
                raise HubError("Oura asked for a password; only the emailed-code sign-in is supported.")
            choice = page.locator(METHOD_BUTTONS).filter(has_text=EMAIL_CODE_NAME)
            if not choice.count():
                choice = page.get_by_role("button", name=EMAIL_CODE_NAME)
            if choice.count() and choice.first.is_visible():
                choice.first.click()
                page.locator(CODE_INPUT).first.wait_for(state="visible", timeout=30_000)
                break
            page.wait_for_timeout(500)
        if not self._visible(CODE_INPUT):
            raise HubError("Never reached the code entry step.")

        code = code_source.wait_for_code(sent_after, code_timeout_s)
        if not code:
            log.warning("No sign-in code arrived in time.")
            return False
        page.locator(CODE_INPUT).first.fill(code)
        self._submit()
        try:
            return self.goto_export_page() == "ready"
        except HubError:
            return False

    def _submit(self) -> None:
        """Press the form's main button: by name, else by type, else Enter."""
        for btn in (self.page.get_by_role("button", name=SUBMIT_NAME),
                    self.page.locator("button[type='submit']")):
            if btn.count() and btn.first.is_visible() and btn.first.is_enabled():
                btn.first.click()
                break
        else:
            self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(1_500)

    # -- export page ---------------------------------------------------------
    def read_state(self) -> PageState:
        btn = self._request_button()
        if btn.is_visible():
            enabled = btn.is_enabled(timeout=2_000) and btn.get_attribute("aria-disabled", timeout=2_000) != "true"
        else:
            enabled = False  # replaced by a "processing" message, presumably
        return PageState(request_enabled=enabled, download_available=self._download_control().is_visible())

    def request_export(self, timeout_s: float = 20) -> bool:
        """Press Request. True if the button then went disabled (request accepted)."""
        btn = self._request_button()
        btn.click(timeout=10_000)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not self.read_state().request_enabled:
                return True
            self.page.wait_for_timeout(500)
        return False

    def download(self, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        with self.page.expect_download(timeout=300_000) as info:
            self._download_control().click(timeout=10_000)
        dl = info.value
        target = dest_dir / (dl.suggested_filename or "oura-export.zip")
        dl.save_as(target)
        return target

    # -- diagnostics ---------------------------------------------------------
    def capture_debug(self, debug_dir: Path, label: str, keep: int = 20) -> Path | None:
        """Screenshot + HTML of the current page, for fixing selectors later.

        Saved only on this machine. The HTML can include your name and email.
        """
        try:
            debug_dir.mkdir(parents=True, exist_ok=True)
            base = debug_dir / f"{datetime.now():%Y%m%d-%H%M%S}_{label}"
            self.page.screenshot(path=f"{base}.png", full_page=True)
            Path(f"{base}.html").write_text(self.page.content(), encoding="utf-8")
            files = sorted(debug_dir.glob("*.*"), key=lambda p: p.stat().st_mtime)
            for old in files[: max(0, len(files) - 2 * keep)]:
                old.unlink(missing_ok=True)
            return base
        except (PlaywrightError, OSError):
            return None
