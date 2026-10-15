"""Drive the Oura Membership Hub data-export page with Playwright.

Scope: this logs in AS YOU and requests/downloads YOUR OWN export. It is a thin
UI automation of a page you can operate by hand; it contains no Oura code.

Login model (as of 2026): Oura uses a passwordless flow:
    email  ->  choose "Email me a code"  ->  type the 6-digit code.
There is no password. A passkey option also appears on the method screen; we
must NOT click that. (Cracked-Oura's issue #20 crashed here because it matched
the passkey and the email-code buttons together via a shared attribute. We
select by accessible name instead.)

Because the code is delivered by email, unattended re-login is impossible
without mailbox access, which we deliberately avoid. So:
  * `interactive_login()` is run by a human who pastes the code.
  * `fetch`/`download` reuse the saved cookie and, if it has expired, raise
    SessionExpired rather than trying to log in.

NOTE: selectors below are best-effort and were written WITHOUT access to the
live DOM. If Oura changes the page, run `oura-export login --headful` to watch,
and adjust the locators in the clearly marked blocks.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import (
    sync_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
)

from . import paths

log = logging.getLogger("oura_export.hub")

BASE_URL = "https://membership.ouraring.com"
EXPORT_URL = f"{BASE_URL}/data-export"


class SessionExpired(RuntimeError):
    """Raised when a non-interactive run finds the saved login is no longer valid."""


class HubError(RuntimeError):
    """Any other automation failure (page changed, timeout, etc.)."""


class OuraHub:
    def __init__(self, email: str = "", headless: bool = True):
        self.email = email
        self.headless = headless
        self._pw = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    # -- lifecycle ----------------------------------------------------------
    def __enter__(self) -> "OuraHub":
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=self.headless)
        state = str(paths.session_path()) if paths.session_path().exists() else None
        self.context = self.browser.new_context(
            storage_state=state,
            viewport={"width": 1440, "height": 900},
            accept_downloads=True,
        )
        self.page = self.context.new_page()
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def _save_session(self) -> None:
        assert self.context
        self.context.storage_state(path=str(paths.session_path()))
        log.info("Saved session to %s", paths.session_path())

    # -- login state -------------------------------------------------------
    def _looks_logged_in(self) -> bool:
        """True when we are on a Hub page that is not the login/auth screen."""
        url = (self.page.url if self.page else "").lower()
        return BASE_URL.lower() in url and "login" not in url and "authn" not in url

    def is_authenticated(self) -> bool:
        """Navigate to the export page and report whether the session is valid."""
        assert self.page
        self.page.goto(EXPORT_URL, timeout=60_000, wait_until="domcontentloaded")
        self._settle()
        return "/data-export" in self.page.url.lower() and self._looks_logged_in()

    def _settle(self, ms: int = 8_000) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=ms)
        except PWTimeout:
            pass

    # -- interactive login -------------------------------------------------
    def interactive_login(self, code_provider: Callable[[], str]) -> None:
        """Full passwordless login. ``code_provider`` returns the emailed code.

        Called only from the `login` command, where a human is present.
        """
        assert self.page
        if self.is_authenticated():
            log.info("Already logged in.")
            self._save_session()
            return

        self.page.goto(f"{BASE_URL}/login", timeout=60_000, wait_until="domcontentloaded")
        self._settle()

        # --- enter email --------------------------------------------------
        email_box = self.page.locator(
            "input[name='username'], input[name='email'], input[type='email']"
        ).first
        email_box.wait_for(state="visible", timeout=20_000)
        email_box.fill(self.email)
        self._submit()
        self._settle()

        # --- choose "Email me a code" (NOT passkey) -----------------------
        # >>> Adjust here if Oura renames the button. <<<
        self._choose_email_code_method()
        self._settle()

        # --- enter the 6-digit code --------------------------------------
        code = code_provider().strip()
        code_box = self.page.locator(
            "input[name='otp'], input[name='code'], input[name='verification_code'], "
            "#otp-code, input[autocomplete='one-time-code']"
        ).first
        code_box.wait_for(state="visible", timeout=20_000)
        code_box.fill(code)
        self._submit()
        self._settle()

        if not self.is_authenticated():
            raise HubError(
                "Login did not complete. Re-run with --headful to watch, and "
                "check the code/selectors."
            )
        self._save_session()
        log.info("Login successful.")

    def _choose_email_code_method(self) -> None:
        """Click the 'Email me a code' option if a method chooser is shown.

        If the account goes straight to the code entry (no chooser), this is a
        no-op. We match by accessible name so we never hit the passkey button.
        """
        page = self.page
        # If a code input is already present, there's no chooser to handle.
        if page.locator("input[autocomplete='one-time-code'], input[name='otp'], #otp-code").count():
            return
        email_btn = page.get_by_role(
            "button", name=re.compile(r"email.*code|send.*code|code.*email", re.I)
        )
        if email_btn.count():
            email_btn.first.click()
            return
        # Fallback: a link/text rather than a <button>.
        link = page.get_by_text(re.compile(r"email me a code", re.I))
        if link.count():
            link.first.click()

    def _submit(self) -> None:
        """Press the form's primary button, falling back to Enter."""
        btn = self.page.get_by_role("button", name=re.compile(r"continue|next|submit|verify|log ?in", re.I))
        if btn.count() and btn.first.is_enabled():
            btn.first.click()
        else:
            self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(1_500)

    # -- export flow -------------------------------------------------------
    def request_export(self) -> None:
        """Click 'Request your data' if it is available (idempotent-ish).

        Raises SessionExpired if we were bounced to login.
        """
        assert self.page
        if not self.is_authenticated():
            raise SessionExpired("Saved Oura session is no longer valid. Run `oura-export login`.")

        # >>> Adjust here if Oura renames the request button. <<<
        req = self.page.get_by_role("button", name=re.compile(r"request.*data|request.*export", re.I))
        if not req.count():
            log.warning("Request button not found; the page may already be processing.")
            return
        req = req.first
        try:
            req.wait_for(state="visible", timeout=10_000)
        except PWTimeout:
            return
        # A disabled button means an export is already being generated.
        if req.is_disabled():
            log.info("Request button disabled -> export already in progress.")
            return
        req.click()
        self.page.wait_for_timeout(2_000)
        log.info("Requested a new export.")

    def _download_locator(self):
        """Locator for the 'Download data' control, however Oura labels it."""
        return self.page.get_by_role(
            "button", name=re.compile(r"download", re.I)
        ).or_(self.page.get_by_role("link", name=re.compile(r"download", re.I)))

    def wait_until_ready(self, timeout_minutes: int, poll_seconds: int) -> bool:
        """Poll the page until a Download control appears and is enabled."""
        assert self.page
        deadline = time.monotonic() + timeout_minutes * 60
        while time.monotonic() < deadline:
            dl = self._download_locator()
            if dl.count() and dl.first.is_visible() and dl.first.is_enabled():
                return True
            log.info("Export not ready yet; re-checking in %ss.", poll_seconds)
            time.sleep(poll_seconds)
            self.page.reload(wait_until="domcontentloaded")
            self._settle()
            if not self._looks_logged_in():
                raise SessionExpired("Session expired while waiting for the export.")
        return False

    def download(self, dest_zip: Path) -> Path:
        """Click the Download control and save the resulting zip to ``dest_zip``."""
        assert self.page
        dl = self._download_locator()
        if not (dl.count() and dl.first.is_visible()):
            raise HubError("No download control on the page.")
        with self.page.expect_download(timeout=120_000) as info:
            dl.first.click()
        download = info.value
        dest_zip.parent.mkdir(parents=True, exist_ok=True)
        download.save_as(str(dest_zip))
        log.info("Downloaded export to %s", dest_zip)
        return dest_zip
