"""Reading the sign-in code from email. No network: imaplib is faked."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

from oura_export import codes
from oura_export.codes import ImapCodeSource, extract_code


@pytest.mark.parametrize("text, expected", [
    ("Your Oura sign-in code is 482913. It expires in 10 minutes.", "482913"),
    ("Verification code: 482 913", "482913"),
    ("<style>.a{color:#333333}</style><p>Use this code</p><b>705218</b>", "705218"),
    ("<td style='color:#123456'>Code</td><td>990011</td>", "990011"),
    ("Order 20261015 shipped", None),          # 8 digits is not a code
    ("No code here.", None),
])
def test_extract_code(text, expected):
    assert extract_code(text) == expected


def _email(code: str, subject: str = "Your Oura code") -> bytes:
    msg = EmailMessage()
    msg["From"] = "Oura <no-reply@ouraring.com>"
    msg["Subject"] = subject
    msg.set_content(f"Hi,\n\nYour code is {code}.\n")
    msg.add_alternative(f"<html><body><p>Your code is <b>{code}</b></p></body></html>", subtype="html")
    return bytes(msg)


class FakeIMAP:
    """Just enough of imaplib.IMAP4_SSL; records how it was used."""

    messages: list[tuple[datetime, bytes]] = []
    log: list[tuple] = []

    def __init__(self, host, port, timeout=None):
        self.log.append(("connect", host, port))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        self.log.append(("login", user))

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"',
                      b'(\\All \\HasNoChildren) "/" "[Gmail]/All Mail"']

    def select(self, mailbox, readonly=False):
        self.log.append(("select", mailbox, readonly))
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        self.log.append(("search",) + criteria)
        return "OK", [" ".join(str(i + 1) for i in range(len(self.messages))).encode()]

    def fetch(self, mid, spec):
        self.log.append(("fetch", spec))
        when, raw = self.messages[int(mid) - 1]
        header = f'{int(mid)} (INTERNALDATE "{when:%d-%b-%Y %H:%M:%S %z}" BODY[] {{{len(raw)}}}'.encode()
        return "OK", [(header, raw), b")"]


@pytest.fixture
def fake_imap(monkeypatch):
    FakeIMAP.messages, FakeIMAP.log = [], []
    monkeypatch.setattr(codes.imaplib, "IMAP4_SSL", FakeIMAP)
    return FakeIMAP


def test_imap_source_reads_newest_code_read_only(fake_imap):
    sent_after = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
    fake_imap.messages = [
        (sent_after - timedelta(hours=5), _email("111111")),   # stale, from an earlier sign-in
        (sent_after + timedelta(seconds=20), _email("222222")),
    ]
    src = ImapCodeSource(host="imap.gmail.com", user="me@gmail.com", password="x", poll_s=0)
    assert src.wait_for_code(sent_after, timeout_s=0) == "222222"

    assert ("select", '"[Gmail]/All Mail"', True) in fake_imap.log      # EXAMINE, never SELECT
    assert all(spec == "(INTERNALDATE BODY.PEEK[])" for kind, *rest in fake_imap.log
               if kind == "fetch" for spec in rest)                      # never marks as read
    assert any(e[0] == "search" and '"ouraring.com"' in e for e in fake_imap.log)


def test_imap_source_ignores_codes_older_than_the_request(fake_imap):
    sent_after = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
    fake_imap.messages = [(sent_after - timedelta(hours=1), _email("111111"))]
    src = ImapCodeSource(host="imap.gmail.com", user="me@gmail.com", password="x", poll_s=0)
    assert src.wait_for_code(sent_after, timeout_s=0) is None


def test_make_code_source_without_password_is_none_when_unattended(monkeypatch):
    monkeypatch.delenv(codes.PASSWORD_ENV, raising=False)
    monkeypatch.setattr(codes, "mail_password", lambda user: None)

    class Cfg:
        code_source, imap_user, email = "gmail", "", "me@gmail.com"
        imap_host, code_sender = "imap.gmail.com", "ouraring.com"

    assert codes.make_code_source(Cfg, interactive=False) is None
    assert isinstance(codes.make_code_source(Cfg, interactive=True), codes.PromptCodeSource)
