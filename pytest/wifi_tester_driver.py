"""HTTP driver for the WiFi Tester instrument (Pi-based).

Communicates with the Pi's portal HTTP API.  The Pi's wlan0 radio acts as
the test instrument; eth0 provides LAN connectivity.
"""

import base64
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ── Response object (mimics requests.Response) ───────────────────────


@dataclass
class Response:
    """HTTP response returned by the relay, mimicking requests.Response."""

    status_code: int = 0
    headers: dict = field(default_factory=dict)
    _body_bytes: bytes = b""

    @property
    def text(self) -> str:
        return self._body_bytes.decode("utf-8", errors="replace")

    def json(self) -> dict:
        return json.loads(self._body_bytes)

    @property
    def content(self) -> bytes:
        return self._body_bytes


# ── Exceptions ───────────────────────────────────────────────────────


class WiFiTesterError(Exception):
    """Base exception for WiFi Tester errors."""


class CommandError(WiFiTesterError):
    """Portal returned ok=false."""

    def __init__(self, command: str, payload: dict):
        self.command = command
        self.payload = payload
        msg = payload.get("error", "Unknown error")
        super().__init__(f"{command}: {msg}")


class CommandTimeout(WiFiTesterError):
    """No response received within timeout."""


# ── Driver ───────────────────────────────────────────────────────────


class WiFiTesterDriver:
    """HTTP driver for the WiFi Tester (Pi backend)."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    # ── Lifecycle ────────────────────────────────────────────────────

    def open(self) -> None:
        """No-op for HTTP driver (no persistent connection)."""

    def close(self) -> None:
        """No-op for HTTP driver."""

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()

    # ── HTTP helpers ─────────────────────────────────────────────────

    def _api_get(self, path: str, timeout: float = 10) -> dict:
        """GET an API endpoint, return parsed JSON."""
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.URLError as e:
            raise CommandTimeout(f"GET {path}: {e}")
        except Exception as e:
            raise CommandTimeout(f"GET {path}: {e}")

        if not data.get("ok", False):
            cmd = path.split("/")[-1]
            raise CommandError(cmd, data)
        return data

    def _api_post(self, path: str, body: Optional[dict] = None,
                  timeout: float = 10) -> dict:
        """POST JSON to an API endpoint, return parsed JSON."""
        url = f"{self.base_url}{path}"
        data_bytes = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(
            url, data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.URLError as e:
            raise CommandTimeout(f"POST {path}: {e}")
        except Exception as e:
            raise CommandTimeout(f"POST {path}: {e}")

        if not data.get("ok", False):
            cmd = path.split("/")[-1]
            raise CommandError(cmd, data)
        return data

    # ── Mode management ──────────────────────────────────────────────

    def get_mode(self) -> dict:
        result = self._api_get("/api/wifi/mode", timeout=5)
        return {k: v for k, v in result.items() if k != "ok"}

    def set_mode(self, mode: str, ssid: str = "",
                 password: str = "") -> dict:
        args: dict = {"mode": mode}
        if ssid:
            args["ssid"] = ssid
        if password:
            args["pass"] = password
        result = self._api_post("/api/wifi/mode", args, timeout=30)
        return {k: v for k, v in result.items() if k != "ok"}

    # ── AP management ────────────────────────────────────────────────

    def ap_start(self, ssid: str, password: str = "",
                 channel: int = 6) -> dict:
        args = {"ssid": ssid, "channel": channel}
        if password:
            args["pass"] = password
        result = self._api_post("/api/wifi/ap_start", args, timeout=10)
        return {k: v for k, v in result.items() if k != "ok"}

    def ap_stop(self) -> None:
        self._api_post("/api/wifi/ap_stop", timeout=10)

    def ap_status(self) -> dict:
        result = self._api_get("/api/wifi/ap_status", timeout=10)
        return {k: v for k, v in result.items() if k != "ok"}

    # ── STA management ───────────────────────────────────────────────

    def sta_join(self, ssid: str, password: str = "",
                 timeout: int = 15) -> dict:
        args = {"ssid": ssid, "timeout": timeout}
        if password:
            args["pass"] = password
        result = self._api_post("/api/wifi/sta_join", args, timeout=timeout + 10)
        return {k: v for k, v in result.items() if k != "ok"}

    def sta_leave(self) -> None:
        self._api_post("/api/wifi/sta_leave", timeout=10)

    # ── HTTP relay ───────────────────────────────────────────────────

    def http_request(self, method: str, url: str,
                     headers: Optional[dict] = None,
                     body: Optional[bytes] = None,
                     timeout: int = 10) -> Response:
        args: dict = {"method": method, "url": url, "timeout": timeout}
        if headers:
            args["headers"] = headers
        if body:
            args["body"] = base64.b64encode(body).decode("ascii")

        result = self._api_post("/api/wifi/http", args, timeout=timeout + 10)

        resp_body = b""
        if result.get("body"):
            resp_body = base64.b64decode(result["body"])

        return Response(
            status_code=result.get("status", 0),
            headers=result.get("headers", {}),
            _body_bytes=resp_body,
        )

    def http_get(self, url: str, **kwargs) -> Response:
        return self.http_request("GET", url, **kwargs)

    def http_post(self, url: str, json_data: Optional[dict] = None,
                  **kwargs) -> Response:
        if json_data is not None:
            body = json.dumps(json_data).encode("utf-8")
            headers = kwargs.pop("headers", {})
            headers.setdefault("Content-Type", "application/json")
            return self.http_request("POST", url, headers=headers,
                                     body=body, **kwargs)
        return self.http_request("POST", url, **kwargs)

    # ── WiFi scanning ────────────────────────────────────────────────

    def scan(self) -> dict:
        result = self._api_get("/api/wifi/scan", timeout=20)
        return {k: v for k, v in result.items() if k != "ok"}

    # ── Events ───────────────────────────────────────────────────────

    def wait_for_event(self, event_type: str,
                       timeout: float = 30) -> dict:
        """Wait for a specific event type via long-polling."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"No {event_type} event within {timeout}s"
                )
            poll_timeout = min(remaining, 5)
            try:
                result = self._api_get(
                    f"/api/wifi/events?timeout={poll_timeout}",
                    timeout=poll_timeout + 5,
                )
            except CommandTimeout:
                continue

            for evt in result.get("events", []):
                if evt.get("type") == event_type:
                    return evt

    def wait_for_station(self, timeout: float = 30) -> dict:
        """Shortcut for waiting for a STA_CONNECT event."""
        return self.wait_for_event("STA_CONNECT", timeout=timeout)

    def drain_events(self) -> list:
        """Return and clear all queued events."""
        try:
            result = self._api_get("/api/wifi/events", timeout=5)
            return result.get("events", [])
        except (CommandTimeout, CommandError):
            return []

    # ── Utility ──────────────────────────────────────────────────────

    def ping(self) -> dict:
        result = self._api_get("/api/wifi/ping", timeout=5)
        return {k: v for k, v in result.items() if k != "ok"}

    def reset(self) -> None:
        """No-op for Pi backend (no hardware to reset)."""
