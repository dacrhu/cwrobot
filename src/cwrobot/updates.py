"""Update check against the latest GitHub Release.

fetch_latest_release() does one blocking HTTPS request, so callers run it off
the GUI thread (see UpdateChecker). Every failure -- offline, rate limit,
unexpected payload -- is swallowed into None: a missing update notice is
never worth bothering the operator over.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import threading
import urllib.request

from PySide6.QtCore import QObject, Signal

LATEST_RELEASE_API_URL = "https://api.github.com/repos/dacrhu/cwrobot/releases/latest"
RELEASES_PAGE_URL = "https://github.com/dacrhu/cwrobot/releases"
_REQUEST_TIMEOUT_S = 8

log = logging.getLogger(__name__)


def _ssl_context() -> ssl.SSLContext:
    # Frozen builds (PyInstaller on macOS especially) often have no usable
    # system CA store, so prefer certifi's bundle when it's available.
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def parse_version(text: str) -> tuple[int, ...] | None:
    """'v1.0.19' / '1.0.19' -> (1, 0, 19); None if there's no numeric version."""
    match = re.match(r"\s*v?(\d+(?:\.\d+)*)", text)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest_tag: str, current_version: str) -> bool:
    latest = parse_version(latest_tag)
    current = parse_version(current_version)
    if latest is None or current is None:
        return False
    return latest > current


def fetch_latest_release() -> tuple[str, str] | None:
    """Return (tag_name, html_url) of the latest release, or None on any failure."""
    request = urllib.request.Request(
        LATEST_RELEASE_API_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "cwrobot-update-check"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_S, context=_ssl_context()) as response:
            data = json.load(response)
        tag = str(data["tag_name"])
        url = str(data.get("html_url") or RELEASES_PAGE_URL)
    except Exception:  # noqa: BLE001 -- see module docstring
        log.warning("Update check failed", exc_info=True)
        return None
    return tag, url


class UpdateChecker(QObject):
    """Runs the check on a daemon thread; emits update_available(tag, url)
    (on the receiver's thread, via Qt's queued connection) only when a newer
    release exists."""

    update_available = Signal(str, str)

    def __init__(self, current_version: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._current_version = current_version

    def start(self) -> None:
        threading.Thread(target=self._run, name="cwrobot-update-check", daemon=True).start()

    def _run(self) -> None:
        result = fetch_latest_release()
        if result is not None and is_newer(result[0], self._current_version):
            self.update_available.emit(*result)
