"""End-to-end tests of the unattended loop against the fake Oura site.

Each run launches a real (headless) Chromium on a persistent profile, exactly
as the scheduler would; only the website and the mailbox are fakes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mock_oura import MailboxFromMock, MockOura, make_zip
from oura_export import config as config_mod, job, paths
from oura_export.hub import open_browser
from oura_export.state import State

V1 = make_zip({"oura_v1/dailysleep.csv": "day;score\n2026-10-13;80\n"})
V2 = make_zip({"oura_v2/dailysleep.csv": "day;score\n2026-10-13;80\n2026-10-14;85\n"})
T0 = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("OURA_EXPORT_HOME", str(tmp_path / "home"))
    c = config_mod.Config(**config_mod.DEFAULTS)
    c.email = "me@gmail.com"
    c.data_dir = str(tmp_path / "OuraData")
    c.poll_interval_seconds = 0.2
    c.wait_minutes = 0.25     # 15 s in-run wait budget keeps tests fast
    c.notify = False
    return c


def factory(oura: MockOura):
    def browser(profile_dir, headless, setup=None):
        return open_browser(profile_dir, headless=True, setup=oura.install)
    return browser


def latest_sleep(cfg) -> str:
    return (Path(cfg.data_dir) / "latest" / "dailysleep.csv").read_text()


def test_unattended_signin_collect_request_and_reuse(cfg):
    oura = MockOura(export=V1, build_loads=2)
    oura.next_export = V2
    mailbox = MailboxFromMock(oura)

    # Run 1: signed out -> signs in by itself, keeps the existing export,
    # requests a new one, waits, and collects it.
    assert job.run_once(cfg, mailbox, now=T0, browser=factory(oura)) == job.OK
    assert oura.counts["passkey"] == 0          # never touched the passkey button
    assert oura.counts["send_code"] == 1
    assert oura.counts["request"] == 1
    assert "2026-10-14;85" in latest_sleep(cfg)   # the NEW export is in latest/
    state = State.load(paths.state_file())
    assert state.pending_since is None and not state.login_needed

    # Run 2, three hours later: still signed in, nothing due, nothing downloaded.
    downloads = oura.counts["download"]
    assert job.run_once(cfg, mailbox, now=T0 + timedelta(hours=3), browser=factory(oura)) == job.OK
    assert oura.counts["send_code"] == 1          # login survived the browser restart
    assert oura.counts["request"] == 1
    assert oura.counts["download"] == downloads

    # Run 3, next day: requests again.
    oura.next_export = make_zip({"v3/dailysleep.csv": "day;score\n2026-10-15;90\n"})
    assert job.run_once(cfg, mailbox, now=T0 + timedelta(hours=23), browser=factory(oura)) == job.OK
    assert oura.counts["request"] == 2
    assert "2026-10-15;90" in latest_sleep(cfg)


def test_expired_login_is_renewed_without_a_person(cfg):
    oura = MockOura(export=V1)
    mailbox = MailboxFromMock(oura)
    assert job.run_once(cfg, mailbox, now=T0, browser=factory(oura)) == job.OK

    oura.revoke_sessions()   # Oura logs us out
    assert job.run_once(cfg, mailbox, now=T0 + timedelta(hours=3), browser=factory(oura)) == job.OK
    assert oura.counts["send_code"] == 2
    assert oura.counts["verify_ok"] == 2


def test_without_a_code_source_no_code_email_is_triggered(cfg):
    oura = MockOura(export=V1)
    assert job.run_once(cfg, None, now=T0, browser=factory(oura)) == job.LOGIN_NEEDED
    assert oura.counts["login_email"] == 0 and oura.counts["send_code"] == 0
    status = (Path(cfg.data_dir) / "STATUS.txt").read_text()
    assert "ACTION NEEDED" in status


def test_signin_attempts_are_capped_per_day(cfg):
    cfg.max_logins_per_day = 2
    oura = MockOura(export=V1)
    silent = MailboxFromMock(oura, deliver=False)   # the code never arrives
    for hours in (0, 1, 2):
        assert job.run_once(cfg, silent, now=T0 + timedelta(hours=hours),
                            browser=factory(oura)) == job.LOGIN_NEEDED
    assert oura.counts["send_code"] == 2              # third run didn't even try
    assert "pausing to protect your account" in State.load(paths.state_file()).last_result


def test_old_file_behind_reenabled_button_is_not_saved_as_new(cfg):
    cfg.wait_minutes = 0.1
    oura = MockOura(export=V1, build_loads=1)
    oura.hold_new_file = True       # button usable again, but still serving V1
    oura.next_export = V2
    mailbox = MailboxFromMock(oura)

    assert job.run_once(cfg, mailbox, now=T0, browser=factory(oura)) == job.OK
    state = State.load(paths.state_file())
    assert oura.counts["download"] >= 2                # it did re-check the file...
    assert "2026-10-14;85" not in latest_sleep(cfg)   # ...and didn't mistake V1 for new
    assert state.pending_since is not None             # still waiting for the real new file

    oura.release()                  # the new file finally lands
    assert job.run_once(cfg, mailbox, now=T0 + timedelta(hours=3), browser=factory(oura)) == job.OK
    assert "2026-10-14;85" in latest_sleep(cfg)
    assert State.load(paths.state_file()).pending_since is None


def test_concurrent_run_is_skipped(cfg):
    paths.lock_file().write_text("123")
    assert job.run_once(cfg, None, now=T0, browser=factory(MockOura(export=V1))) == job.BUSY
