"""Best-effort desktop notifications. Never raises."""

from __future__ import annotations

import shutil
import subprocess
import sys


def notify(title: str, message: str) -> None:
    try:
        if sys.platform == "darwin":
            # Escape double quotes for the AppleScript string literals.
            t = title.replace('"', '\\"')
            m = message.replace('"', '\\"')
            subprocess.run(
                ["osascript", "-e", f'display notification "{m}" with title "{t}"'],
                check=False,
                capture_output=True,
            )
        elif sys.platform.startswith("win"):
            ps = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
                "ContentType = WindowsRuntime] > $null; "
                f"Write-Output '{title}: {message}'"
            )
            if shutil.which("powershell"):
                subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               check=False, capture_output=True)
        # On Linux/headless, silently do nothing.
    except Exception:
        pass
