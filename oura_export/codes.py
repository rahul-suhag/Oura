"""Where the 6-digit sign-in code comes from.

Oura signs you in by emailing a one-time code. To sign back in unattended we
read that email ourselves. Two sources:

  * PromptCodeSource - a person types the code (used by `oura-export login`).
  * ImapCodeSource   - reads the newest Oura email over IMAP (Gmail with an app
                       password). Strictly read-only: the folder is opened with
                       EXAMINE and messages are fetched with BODY.PEEK, so
                       nothing is marked read, moved or deleted.
"""

from __future__ import annotations

import email
import email.policy
import html
import imaplib
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol

log = logging.getLogger("oura_export.codes")

KEYRING_SERVICE = "oura-export"
PASSWORD_ENV = "OURA_EXPORT_IMAP_PASSWORD"

# Oura's clock and the mail server's clock can disagree a little; accept emails
# that arrived slightly before we pressed the button.
_CLOCK_SKEW = timedelta(seconds=120)


class CodeSource(Protocol):
    interactive: bool

    def wait_for_code(self, sent_after: datetime, timeout_s: float) -> str | None: ...


# --- extracting the code from an email -------------------------------------

_STYLE_OR_SCRIPT = re.compile(r"<(style|script)\b.*?</\1\s*>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
# A code shortly after the word "code", allowing "123456" or "123 456".
_CODE_AFTER_WORD = re.compile(r"code\D{0,80}?(?<![\d#])(\d{3})[ \-]?(\d{3})(?!\d)", re.I | re.S)
# Any standalone 6-digit number; "#" excluded so CSS colours never match.
_ANY_CODE = re.compile(r"(?<![\d#])(\d{6})(?!\d)")


def extract_code(text: str) -> str | None:
    """Pull a 6-digit sign-in code out of an email's text (plain or HTML)."""
    plain = html.unescape(_TAG.sub(" ", _STYLE_OR_SCRIPT.sub(" ", text)))
    m = _CODE_AFTER_WORD.search(plain)
    if m:
        return m.group(1) + m.group(2)
    m = _ANY_CODE.search(plain)
    return m.group(1) if m else None


def message_text(raw: bytes) -> str:
    """Subject plus every text/plain and text/html part, decoded."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    chunks = [str(msg.get("subject", ""))]
    for part in msg.walk():
        if part.get_content_type() in ("text/plain", "text/html"):
            try:
                chunks.append(part.get_content())
            except (LookupError, UnicodeDecodeError):
                payload = part.get_payload(decode=True) or b""
                chunks.append(payload.decode("utf-8", "replace"))
    return "\n".join(chunks)


# --- sources ----------------------------------------------------------------

class PromptCodeSource:
    """A person at the keyboard types the code."""

    interactive = True

    def wait_for_code(self, sent_after: datetime, timeout_s: float = 600) -> str | None:
        typed = input("Enter the 6-digit code Oura just emailed you: ")
        return extract_code(typed) or (typed.strip() or None)


_INTERNALDATE = re.compile(rb'INTERNALDATE "([^"]+)"')
_LIST_LINE = re.compile(rb'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$')


@dataclass
class ImapCodeSource:
    host: str
    user: str
    password: str
    sender: str = "ouraring.com"
    poll_s: float = 10.0
    interactive: bool = False
    _used: set[str] = field(default_factory=set)

    def wait_for_code(self, sent_after: datetime, timeout_s: float = 180) -> str | None:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                code = self._newest_code(sent_after)
            except (imaplib.IMAP4.error, OSError) as exc:
                # A bad app password won't fix itself; anything else may be a blip.
                if "AUTHENTICATIONFAILED" in str(exc).upper():
                    raise
                log.warning("Mailbox check failed (%s); retrying.", exc)
                code = None
            if code:
                self._used.add(code)
                return code
            if time.monotonic() >= deadline:
                return None
            time.sleep(self.poll_s)

    def _newest_code(self, sent_after: datetime) -> str | None:
        with imaplib.IMAP4_SSL(self.host, 993, timeout=30) as conn:
            conn.login(self.user, self.password)
            # readonly=True issues EXAMINE: nothing in the mailbox can change.
            conn.select(self._search_folder(conn), readonly=True)
            since = (sent_after - timedelta(days=1)).strftime("%d-%b-%Y")
            _, data = conn.search(None, "SINCE", since, "FROM", f'"{self.sender}"')
            ids = data[0].split() if data and data[0] else []
            for mid in reversed(ids[-20:]):  # newest first, bounded
                _, parts = conn.fetch(mid, "(INTERNALDATE BODY.PEEK[])")
                header, raw = _fetch_parts(parts)
                if raw is None:
                    continue
                received = _parse_internaldate(header)
                if received is not None and received < sent_after - _CLOCK_SKEW:
                    break  # older than our request; everything after is older too
                code = extract_code(message_text(raw))
                if code and code not in self._used:
                    return code
        return None

    @staticmethod
    def _search_folder(conn: imaplib.IMAP4) -> str:
        """Gmail's "All Mail" (flagged \\All), so filters/archiving can't hide the email."""
        _, lines = conn.list()
        for line in lines or []:
            m = _LIST_LINE.match(line or b"")
            if m and b"\\All" in m.group("flags"):
                return m.group("name").decode()
        return "INBOX"


def _fetch_parts(parts) -> tuple[bytes, bytes | None]:
    for item in parts or []:
        if isinstance(item, tuple) and len(item) == 2:
            return item[0], item[1]
    return b"", None


def _parse_internaldate(header: bytes) -> datetime | None:
    m = _INTERNALDATE.search(header)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1).decode(), "%d-%b-%Y %H:%M:%S %z")
    except ValueError:
        return None


# --- choosing a source ------------------------------------------------------

def mail_password(user: str) -> str | None:
    """App password from the environment, else from the OS keychain."""
    env = os.environ.get(PASSWORD_ENV)
    if env:
        return env.replace(" ", "")
    try:
        import keyring  # optional: Windows Credential Manager / macOS Keychain
    except ImportError:
        return None
    try:
        stored = keyring.get_password(KEYRING_SERVICE, user)
    except Exception:  # noqa: BLE001 - no usable keychain backend
        return None
    return stored.replace(" ", "") if stored else None


def make_code_source(cfg, interactive: bool) -> CodeSource | None:
    """The best available way to get a code, or None if there is no way.

    Returning None tells the caller not to start a sign-in at all, so a
    scheduled run never triggers a code email that nobody can read.
    """
    if cfg.code_source == "gmail":
        user = cfg.imap_user or cfg.email
        password = mail_password(user) if user else None
        if password:
            return ImapCodeSource(host=cfg.imap_host, user=user, password=password,
                                  sender=cfg.code_sender)
        log.warning("No Gmail app password stored for %s; run `oura-export set-mail-password`.", user)
    return PromptCodeSource() if interactive else None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
