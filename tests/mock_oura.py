"""A stand-in for membership.ouraring.com, served through Playwright routing.

It copies the page structure the real Hub had according to Cracked-Oura
(data-testid="pageSubtitle" followed by the Request button, an
aria-label="Download data" button) and the two-button sign-in step reported in
Cracked-Oura issue #20 ("Use passkey" and "Email me a code", both
name="selectedId"). It is NOT a recording of the real site; it exists so the
unattended logic can be tested end to end without network access.
"""

from __future__ import annotations

import io
import random
import zipfile
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlparse


def make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


LOGIN_HTML = r"""<!doctype html><html><body><main id="app"></main><script>
const app = document.getElementById('app');
function step1() {
  app.innerHTML = '<form id="f"><label>Email <input name="username" type="email"></label>' +
                  '<button type="submit">Continue</button></form>';
  document.getElementById('f').onsubmit = async (e) => {
    e.preventDefault();
    await fetch('/api/login-email', {method: 'POST', body: document.querySelector('[name=username]').value});
    step2();
  };
}
function step2() {
  app.innerHTML = '<button name="selectedId" id="passkey-button">Use passkey</button>' +
                  '<button name="selectedId" id="email-button">Email me a code</button>';
  document.getElementById('passkey-button').onclick = async () => {
    await fetch('/api/passkey', {method: 'POST'});
    app.innerHTML = '<p>Waiting for your passkey...</p>';
  };
  document.getElementById('email-button').onclick = async () => {
    await fetch('/api/send-code', {method: 'POST'});
    step3();
  };
}
function step3() {
  app.innerHTML = '<form id="o"><label>Code <input name="otp" autocomplete="one-time-code"></label>' +
                  '<button type="submit">Verify</button></form>';
  document.getElementById('o').onsubmit = async (e) => {
    e.preventDefault();
    const r = await fetch('/api/verify', {method: 'POST', body: document.querySelector('[name=otp]').value});
    if (r.ok) {
      const token = await r.text();
      document.cookie = 'oura_session=' + token + '; path=/; max-age=2592000; secure';
      location.href = '/';
    } else {
      app.insertAdjacentHTML('beforeend', '<p>Invalid code</p>');
    }
  };
}
step1();
</script></body></html>"""

REDIRECT_TO_LOGIN = "<!doctype html><script>location.replace('/login')</script>"
HOME_HTML = "<!doctype html><main><h1>Membership Hub</h1><a href='/data-export'>Export data</a></main>"


def export_html(processing: bool, has_export: bool) -> str:
    download = ('<button id="dl" aria-label="Download data">Download</button>'
                if has_export else "")
    return f"""<!doctype html><html><body><main>
<h1>Data export</h1>
<p data-testid="pageSubtitle">Request a copy of your Oura data.</p>
<button id="req" {'disabled' if processing else ''}>Request your data</button>
{download}
</main><script>
const req = document.getElementById('req');
req.onclick = async () => {{ req.disabled = true; await fetch('/api/request', {{method: 'POST'}}); }};
const dl = document.getElementById('dl');
if (dl) dl.onclick = () => {{ location.href = '/download/oura-data.zip'; }};
</script></body></html>"""


class MockOura:
    def __init__(self, export: bytes | None, build_loads: int = 2):
        self.export = export
        self.next_export: bytes | None = None   # what the next request produces
        self.processing = False
        self.build_loads = build_loads          # export-page loads until a request finishes
        # When True, the Request button comes back but the OLD file is still
        # served until release() is called (the case that must not fool us).
        self.hold_new_file = False
        self._loads_left = 0
        self.token = "t1"
        self.current_code: str | None = None
        self.code_sent_at: datetime | None = None
        self.counts: Counter[str] = Counter()

    def revoke_sessions(self) -> None:
        """Simulate the saved login expiring."""
        self.token = f"t{random.randrange(10**6)}"

    def install(self, ctx) -> None:
        ctx.route("https://membership.ouraring.com/**", self._handle)

    def _handle(self, route) -> None:
        req = route.request
        path = urlparse(req.url).path
        signed_in = f"oura_session={self.token}" in (req.header_value("cookie") or "")

        def html(body: str) -> None:
            route.fulfill(status=200, content_type="text/html", body=body)

        if path == "/login":
            return html(LOGIN_HTML)
        if path == "/api/login-email":
            self.counts["login_email"] += 1
            return route.fulfill(status=204)
        if path == "/api/passkey":
            self.counts["passkey"] += 1
            return route.fulfill(status=204)
        if path == "/api/send-code":
            self.counts["send_code"] += 1
            self.current_code = f"{random.randrange(10**6):06d}"
            self.code_sent_at = datetime.now(timezone.utc)
            return route.fulfill(status=204)
        if path == "/api/verify":
            if req.post_data == self.current_code:
                self.counts["verify_ok"] += 1
                return route.fulfill(status=200, body=self.token)
            self.counts["verify_bad"] += 1
            return route.fulfill(status=401)
        if not signed_in:
            if path.startswith(("/api/", "/download/")):
                return route.fulfill(status=401)
            return html(REDIRECT_TO_LOGIN)
        if path in ("", "/"):
            return html(HOME_HTML)
        if path == "/data-export":
            self._advance_build()
            return html(export_html(self.processing, self.export is not None))
        if path == "/api/request":
            self.counts["request"] += 1
            self.processing = True
            self._loads_left = self.build_loads
            return route.fulfill(status=204)
        if path == "/download/oura-data.zip" and self.export is not None:
            self.counts["download"] += 1
            return route.fulfill(status=200, body=self.export, headers={
                "Content-Type": "application/zip",
                "Content-Disposition": 'attachment; filename="oura-data.zip"'})
        return route.fulfill(status=404, body="not found")

    def _advance_build(self) -> None:
        """Each export-page load moves a pending build one step forward."""
        if self.processing:
            self._loads_left -= 1
            if self._loads_left <= 0:
                self.processing = False
                if not self.hold_new_file:
                    self.release()

    def release(self) -> None:
        """The newly built file becomes the one served."""
        self.hold_new_file = False
        if self.next_export is not None:
            self.export, self.next_export = self.next_export, None


class MailboxFromMock:
    """Stands in for Gmail: hands over the code the mock site just 'emailed'."""

    interactive = False

    def __init__(self, oura: MockOura, deliver: bool = True):
        self.oura = oura
        self.deliver = deliver
        self.calls = 0

    def wait_for_code(self, sent_after: datetime, timeout_s: float) -> str | None:
        self.calls += 1
        sent = self.oura.code_sent_at
        if self.deliver and sent is not None and sent >= sent_after:
            return self.oura.current_code
        return None
