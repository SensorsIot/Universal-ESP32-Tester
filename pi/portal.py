#!/usr/bin/env python3
"""
RFC2217 Portal v4 — Proxy Supervisor with Serial Services

HTTP server that tracks USB serial device hotplug events and manages
plain_rfc2217_server.py lifecycle.  On hotplug add → start proxy; on remove → stop it.
Slot configuration is loaded from slots.json.
"""

import http.server
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import wifi_controller

PORT = 8080
CONFIG_FILE = os.environ.get("RFC2217_CONFIG", "/etc/rfc2217/slots.json")
PROXY_EXE = "/usr/local/bin/plain_rfc2217_server.py"

# Flap detection — suppress proxy restarts during USB connect/disconnect storms
FLAP_WINDOW_S = 30       # Look at events within this window
FLAP_THRESHOLD = 6        # 6 events in 30s = 3 connect/disconnect cycles
FLAP_COOLDOWN_S = 30      # After flapping, wait 30s of quiet before retry

# Native USB (ttyACM) boot delay — let ESP32-C3 boot past download-mode window
# before opening the port (Linux cdc_acm asserts DTR+RTS on open, which triggers
# the USB-Serial/JTAG controller's auto-download if the chip is still in early boot)
NATIVE_USB_BOOT_DELAY_S = 2

# Slot states (per-slot lifecycle, exposed in /api/devices)
STATE_ABSENT     = "absent"
STATE_IDLE       = "idle"
STATE_RESETTING  = "resetting"
STATE_MONITORING = "monitoring"
STATE_FLAPPING   = "flapping"

# Module-level state
slots: dict[str, dict] = {}
seq_counter: int = 0
host_ip: str = "127.0.0.1"  # refreshed periodically; see _refresh_host_ip()
hostname: str = "localhost"

# Activity log — recent operations visible in UI
import collections
activity_log: collections.deque = collections.deque(maxlen=200)
_enter_portal_running: bool = False

# Human interaction — test scripts block on POST /api/human-interaction
# until the operator clicks Done/Cancel on the web UI.
_human_event: threading.Event | None = None
_human_confirmed: bool = False
_human_message: str | None = None
_human_lock = threading.Lock()

# Test progress — test scripts push updates via POST /api/test/update,
# UI polls via GET /api/test/progress.
_test_lock = threading.Lock()
_test_session = None  # dict or None; see _handle_test_update for schema

# GPIO control — drive Pi GPIO pins from test scripts (e.g. hold DUT GPIO low)
import gpiod

_gpio_lock = threading.Lock()
_gpio_chip = None       # gpiod.Chip, opened lazily
_gpio_requests = {}     # pin -> gpiod.LineRequest
_gpio_directions = {}   # pin -> "output" | "input"
GPIO_ALLOWED = {5, 6, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26}


def _gpio_set(pin, value):
    """Set a GPIO pin: value=0 (low), 1 (high), or "z" (input with pull-up)."""
    global _gpio_chip
    with _gpio_lock:
        if _gpio_chip is None:
            _gpio_chip = gpiod.Chip("/dev/gpiochip0")

        if value == "z":
            # Switch to input with pull-up (not floating)
            if pin in _gpio_requests:
                _gpio_requests[pin].release()
                del _gpio_requests[pin]
            _gpio_requests[pin] = _gpio_chip.request_lines(
                consumer="serial-portal",
                config={pin: gpiod.LineSettings(
                    direction=gpiod.line.Direction.INPUT,
                    bias=gpiod.line.Bias.PULL_UP,
                )},
            )
            _gpio_directions[pin] = "input"
            return

        gval = gpiod.line.Value.ACTIVE if value else gpiod.line.Value.INACTIVE

        # Request as output if not already, or reconfigure if switching from input
        if pin not in _gpio_requests or _gpio_directions.get(pin) == "input":
            if pin in _gpio_requests:
                _gpio_requests[pin].release()
                del _gpio_requests[pin]
            _gpio_requests[pin] = _gpio_chip.request_lines(
                consumer="serial-portal",
                config={pin: gpiod.LineSettings(
                    direction=gpiod.line.Direction.OUTPUT,
                    output_value=gval,
                )},
            )
        else:
            _gpio_requests[pin].set_value(pin, gval)
        _gpio_directions[pin] = "output"


def log_activity(msg: str, cat: str = "info"):
    """Append a timestamped entry to the activity log."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "msg": msg,
        "cat": cat,  # info, ok, error, step
    }
    activity_log.append(entry)
    print(f"[activity] [{cat}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict[str, dict]:
    """Parse slots.json and return pre-populated slots dict keyed by slot_key."""
    result: dict[str, dict] = {}
    try:
        with open(path) as f:
            cfg = json.load(f)
        for entry in cfg.get("slots", []):
            key = entry["slot_key"]
            result[key] = {
                "label": entry["label"],
                "slot_key": key,
                "tcp_port": entry["tcp_port"],
                "present": False,
                "running": False,
                "pid": None,
                "devnode": None,
                "seq": 0,
                "last_action": None,
                "last_event_ts": None,
                "url": None,
                "last_error": None,
                "flapping": False,
                "state": STATE_ABSENT,
                "_event_times": [],
                "_lock": threading.Lock(),
            }
        print(f"[portal] loaded {len(result)} slot(s) from {path}", flush=True)
    except FileNotFoundError:
        print(f"[portal] config not found: {path} (starting with no slots)", flush=True)
    except Exception as exc:
        print(f"[portal] error loading config: {exc}", flush=True)
    return result


def get_host_ip() -> str:
    """Detect host IP, preferring eth0 (wired management interface)."""
    # Prefer eth0 — the wired management interface
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", "eth0"],
            timeout=2, stderr=subprocess.DEVNULL,
        ).decode()
        for part in out.split():
            if "/" in part:
                ip = part.split("/")[0]
                if ip and not ip.startswith("127."):
                    return ip
    except Exception:
        pass
    # Fallback: UDP socket trick (picks default-route interface)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _refresh_host_ip():
    """Re-resolve host IP; update global and running slot URLs if it changed."""
    global host_ip
    new_ip = get_host_ip()
    if new_ip != host_ip:
        old = host_ip
        host_ip = new_ip
        for slot in slots.values():
            if slot["running"] and slot["tcp_port"]:
                slot["url"] = f"rfc2217://{host_ip}:{slot['tcp_port']}"
        print(f"[portal] host_ip changed: {old} -> {host_ip}", flush=True)


def get_hostname() -> str:
    """Get the system hostname (used for mDNS / display)."""
    return socket.gethostname()


def wait_for_device(devnode: str, timeout: float = 5.0) -> bool:
    """Wait until the device node exists and is accessible.

    For ttyACM (native USB CDC) devices, only check file existence —
    os.open() asserts DTR+RTS via the cdc_acm driver, which resets
    ESP32-C3 into download mode during the boot window.
    """
    is_native_usb = devnode and "ttyACM" in devnode
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(devnode):
            if is_native_usb:
                return True  # Don't open — avoids DTR reset
            try:
                fd = os.open(devnode, os.O_RDWR | os.O_NONBLOCK)
                os.close(fd)
                return True
            except OSError:
                pass
        time.sleep(0.1)
    return False


def is_port_listening(port: int) -> bool:
    """Quick TCP connect check on localhost."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        result = s.connect_ex(("127.0.0.1", port))
        s.close()
        return result == 0
    except Exception:
        return False


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start_proxy(slot: dict) -> bool:
    """Start plain_rfc2217_server for *slot*.  Returns True on success."""
    devnode = slot["devnode"]
    tcp_port = slot["tcp_port"]
    label = slot["label"]

    if not os.path.exists(PROXY_EXE):
        slot["last_error"] = f"Proxy executable not found: {PROXY_EXE}"
        print(f"[portal] {label}: {slot['last_error']}", flush=True)
        return False

    # Settle — done *before* acquiring lock (caller holds lock already)
    if not wait_for_device(devnode):
        slot["last_error"] = f"Device {devnode} not ready after settle timeout"
        print(f"[portal] {label}: {slot['last_error']}", flush=True)
        return False

    cmd = ["python3", PROXY_EXE, "-p", str(tcp_port), devnode]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        slot["last_error"] = str(exc)
        print(f"[portal] {label}: popen failed: {exc}", flush=True)
        return False

    # Brief pause then check it didn't die immediately
    time.sleep(0.5)
    if proc.poll() is not None:
        slot["last_error"] = f"Proxy exited immediately (code {proc.returncode})"
        print(f"[portal] {label}: {slot['last_error']}", flush=True)
        return False

    # Wait up to 2 s for port to be listening
    for _ in range(20):
        if is_port_listening(tcp_port):
            slot["running"] = True
            slot["pid"] = proc.pid
            slot["last_error"] = None
            slot["url"] = f"rfc2217://{host_ip}:{tcp_port}"
            slot["state"] = STATE_IDLE
            print(
                f"[portal] {label}: proxy started (pid {proc.pid}, port {tcp_port})",
                flush=True,
            )
            return True
        time.sleep(0.1)

    # Port never came up — kill the process
    _stop_pid(proc.pid)
    slot["last_error"] = "Proxy started but port not listening"
    print(f"[portal] {label}: {slot['last_error']}", flush=True)
    return False


def _stop_pid(pid: int, timeout: float = 5.0):
    """SIGTERM, wait, SIGKILL fallback."""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _is_process_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def stop_proxy(slot: dict) -> bool:
    """Stop proxy for *slot*.  Returns True if stopped (or already stopped)."""
    label = slot["label"]
    pid = slot["pid"]
    if pid and _is_process_alive(pid):
        print(f"[portal] {label}: stopping proxy (pid {pid})", flush=True)
        _stop_pid(pid)
    slot["running"] = False
    slot["pid"] = None
    slot["url"] = None
    slot["last_error"] = None
    return True


def _make_dynamic_slot(slot_key: str) -> dict:
    """Create a minimal slot dict for an unknown (unconfigured) slot_key."""
    return {
        "label": None,
        "slot_key": slot_key,
        "tcp_port": None,
        "present": False,
        "running": False,
        "pid": None,
        "devnode": None,
        "seq": 0,
        "last_action": None,
        "last_event_ts": None,
        "url": None,
        "last_error": None,
        "flapping": False,
        "state": STATE_ABSENT,
        "_event_times": [],
        "_lock": threading.Lock(),
    }


def scan_existing_devices():
    """Scan for already-plugged-in USB serial devices and start proxies.

    Called once at startup so devices present at boot are recognized
    without requiring a hotplug event.
    """
    import glob as _glob
    import subprocess as _sp

    devnodes = sorted(_glob.glob("/dev/ttyACM*") + _glob.glob("/dev/ttyUSB*"))
    if not devnodes:
        print("[portal] boot scan: no USB serial devices found", flush=True)
        return

    print(f"[portal] boot scan: found {len(devnodes)} device(s)", flush=True)
    for devnode in devnodes:
        # Get ID_PATH from udevadm
        try:
            out = _sp.check_output(
                ["udevadm", "info", "-q", "property", "-n", devnode],
                text=True, timeout=5,
            )
        except Exception as exc:
            print(f"[portal] boot scan: udevadm failed for {devnode}: {exc}", flush=True)
            continue

        props = {}
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                props[k] = v

        id_path = props.get("ID_PATH", "")
        devpath = props.get("DEVPATH", "")
        slot_key = id_path if id_path else devpath
        if not slot_key:
            print(f"[portal] boot scan: no slot_key for {devnode}, skipping", flush=True)
            continue

        if slot_key not in slots:
            slots[slot_key] = _make_dynamic_slot(slot_key)
            print(f"[portal] boot scan: unknown slot_key={slot_key} (tracked, no proxy)", flush=True)

        slot = slots[slot_key]
        slot["present"] = True
        slot["devnode"] = devnode
        slot["state"] = STATE_IDLE

        if slot["tcp_port"] is not None and not slot["running"]:
            print(f"[portal] boot scan: starting proxy for {slot['label']} ({devnode})", flush=True)
            with slot["_lock"]:
                start_proxy(slot)


def _refresh_slot_health(slot: dict):
    """Check that a slot's proxy is still alive; mark dead if not."""
    if slot["running"] and slot["pid"]:
        if not _is_process_alive(slot["pid"]):
            slot["running"] = False
            slot["pid"] = None
            slot["url"] = None
            slot["last_error"] = "Process died"
            slot["state"] = STATE_IDLE if slot["present"] else STATE_ABSENT


def _slot_info(slot: dict) -> dict:
    """Return a JSON-safe copy of a slot (excludes _lock)."""
    return {k: v for k, v in slot.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Serial Services — reset and monitor (FR-008, FR-009)
# ---------------------------------------------------------------------------

def _find_slot_by_label(label: str) -> dict | None:
    """Find a configured slot by its human-readable label."""
    for s in slots.values():
        if s["label"] == label:
            return s
    return None


def _read_serial_lines(ser, pattern: str | None, timeout: float) -> tuple[list[str], str | None]:
    """Read serial lines until pattern matched or timeout.

    Returns (lines, matched_line) where matched_line is None if no match.
    """
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    buf = b""
    while time.monotonic() < deadline:
        chunk = ser.read(512)
        if chunk:
            buf += chunk
            text = buf.decode("utf-8", errors="replace")
            new_lines = text.split("\n")
            # Last element may be incomplete — keep in buf
            if not text.endswith("\n"):
                buf = new_lines.pop().encode("utf-8", errors="replace")
            else:
                buf = b""
            for line in new_lines:
                stripped = line.strip()
                if stripped:
                    lines.append(stripped)
                    if pattern and pattern in stripped:
                        return lines, stripped
    # Process any remaining buffer
    if buf:
        stripped = buf.decode("utf-8", errors="replace").strip()
        if stripped:
            lines.append(stripped)
            if pattern and pattern in stripped:
                return lines, stripped
    return lines, None


def serial_reset(slot: dict) -> dict:
    """FR-008: Reset device via DTR/RTS.  Stops proxy, opens direct serial,
    sends reset pulse, reads initial boot output, closes.  Proxy restarts
    via hotplug re-enumeration.

    Returns {"ok": True/False, "output": [...], "error": "..."}.
    """
    import serial as pyserial

    label = slot["label"]
    devnode = slot.get("devnode")

    if not devnode:
        return {"ok": False, "error": f"{label}: no device node"}
    if not slot.get("present"):
        return {"ok": False, "error": f"{label}: device not present"}

    # Stop the proxy so we can open direct serial
    with slot["_lock"]:
        stop_proxy(slot)
        slot["state"] = STATE_RESETTING

    # Open direct serial with DTR/RTS safe
    try:
        ser = pyserial.Serial(devnode, 115200, timeout=0.1)
        ser.dtr = False
        ser.rts = False
        time.sleep(0.1)
        ser.read(8192)  # drain
    except Exception as e:
        slot["state"] = STATE_IDLE if slot["present"] else STATE_ABSENT
        return {"ok": False, "error": f"Cannot open {devnode}: {e}"}

    # Send DTR/RTS reset pulse
    ser.dtr = True
    time.sleep(0.05)
    ser.dtr = False
    time.sleep(0.05)
    ser.rts = True
    time.sleep(0.05)
    ser.rts = False

    # Read boot output (up to 5s)
    lines, _ = _read_serial_lines(ser, None, timeout=5.0)
    ser.close()

    # Restart the proxy — DTR/RTS resets don't cause USB re-enumeration
    # (the chip reboots but ttyACM stays), so hotplug won't restart it.
    time.sleep(NATIVE_USB_BOOT_DELAY_S)
    with slot["_lock"]:
        if not slot["running"]:
            start_proxy(slot)
        # start_proxy sets STATE_IDLE on success; set it here if proxy failed
        if slot["state"] == STATE_RESETTING:
            slot["state"] = STATE_IDLE if slot["present"] else STATE_ABSENT

    return {"ok": True, "output": lines}


def serial_monitor(slot: dict, pattern: str | None = None,
                   timeout: float = 10.0) -> dict:
    """FR-009: Read serial output via RFC2217 proxy (non-exclusive).

    Returns {"ok": True, "matched": True/False, "line": "...", "output": [...]}.
    """
    import serial as pyserial

    label = slot["label"]
    tcp_port = slot.get("tcp_port")

    if not tcp_port:
        return {"ok": False, "error": f"{label}: no tcp_port configured"}
    if not slot.get("running"):
        return {"ok": False, "error": f"{label}: proxy not running"}

    rfc2217_url = f"rfc2217://127.0.0.1:{tcp_port}"
    try:
        ser = pyserial.serial_for_url(rfc2217_url, do_not_open=True)
        ser.baudrate = 115200
        ser.timeout = 0.1
        ser.dtr = False
        ser.rts = False
        ser.open()
    except Exception as e:
        return {"ok": False, "error": f"Cannot connect to {rfc2217_url}: {e}"}

    slot["state"] = STATE_MONITORING
    try:
        lines, matched_line = _read_serial_lines(ser, pattern, timeout)
    finally:
        try:
            ser.close()
        except Exception:
            pass
        slot["state"] = STATE_IDLE if slot["present"] else STATE_ABSENT

    return {
        "ok": True,
        "matched": matched_line is not None,
        "line": matched_line,
        "output": lines,
    }


# ---------------------------------------------------------------------------
# Enter-portal — composite serial operation (FR-008 + FR-009)
# ---------------------------------------------------------------------------

def _do_enter_portal(slot: dict, num_resets: int = 3):
    """Reset an ESP32 device repeatedly to trigger captive portal mode.

    Uses direct serial (FR-008 serial_reset) for the reset pulses, keeping
    the serial connection open across resets for rapid cycling.  This is
    a composite operation built on the serial service primitives.
    """
    import serial as pyserial
    import re

    label = slot["label"]
    devnode = slot.get("devnode")

    if not devnode:
        log_activity(f"{label}: no device node — is the device plugged in?", "error")
        return

    def _send_reset(ser):
        """Send DTR/RTS reset pulse."""
        ser.dtr = True;  time.sleep(0.05);  ser.dtr = False
        time.sleep(0.05)
        ser.rts = True;  time.sleep(0.05);  ser.rts = False

    def _parse_ap_info(lines):
        """Extract SSID and password from 'AP Started: ...' serial line."""
        for l in lines:
            if "AP Started:" not in l:
                continue
            ssid = pw = ""
            m = re.search(r"SSID=(\S+)", l)
            if m:
                ssid = m.group(1).rstrip(",")
            m = re.search(r"Pass=(\S+)", l)
            if m:
                pw = m.group(1).rstrip(",")
            return ssid, pw
        return "", ""

    def _parse_wifi_info(lines):
        """Extract SSID and IP from WiFi connect serial lines."""
        for l in lines:
            if "WiFi connected" not in l:
                continue
            m = re.search(r"IP:\s*(\S+)", l)
            ip = m.group(1) if m else "?"
            via = "NVS" if "via NVS" in l else "fallback"
            return ip, via
        return "", ""

    # Stop proxy so we can use direct serial for rapid resets
    with slot["_lock"]:
        stop_proxy(slot)
        slot["state"] = STATE_RESETTING

    # -- Open direct serial (not RFC2217) --
    log_activity(f"Opening {label} direct serial ({devnode})...", "step")
    try:
        ser = pyserial.Serial(devnode, 115200, timeout=0.1)
        ser.dtr = False
        ser.rts = False
        time.sleep(0.1)
        ser.read(8192)  # drain
    except Exception as e:
        log_activity(f"Cannot open {devnode}: {e}", "error")
        slot["state"] = STATE_IDLE if slot["present"] else STATE_ABSENT
        return

    # -- Step 1: clean boot (reset boot counter) --
    log_activity(f"serial.reset({label}) — clean boot...", "step")
    _send_reset(ser)
    lines, matched = _read_serial_lines(ser, "Boot count reset to 0", timeout=15)
    ip, via = _parse_wifi_info(lines)
    if ip:
        log_activity(f"{label} in NORMAL mode — WiFi ({via}) IP: {ip}", "ok")
    else:
        log_activity(f"{label} clean boot done", "info")
    time.sleep(1)

    # -- Step 2: N rapid resets --
    log_activity(f"Sending {num_resets} rapid resets to trigger captive portal...", "step")
    ser.read(8192)  # drain
    for i in range(1, num_resets + 1):
        log_activity(f"serial.reset({label}) — reset {i}/{num_resets}", "step")
        _send_reset(ser)
        lines, matched = _read_serial_lines(ser, "Boot count:", timeout=5)
        boot_line = [l for l in lines if "Boot count:" in l and "threshold" in l]
        if boot_line:
            log_activity(f"serial.monitor({label}) — {boot_line[-1]}", "info")
        else:
            log_activity(f"Reset {i}/{num_resets} — no boot count detected", "error")
            ser.close()
            slot["state"] = STATE_IDLE if slot["present"] else STATE_ABSENT
            return

        if i < num_resets:
            time.sleep(0.3)  # minimal gap before next reset

    # -- Step 3: check for portal mode --
    log_activity(f"serial.monitor({label}, 'PORTAL mode') — waiting...", "step")
    lines2, matched = _read_serial_lines(ser, "PORTAL mode", timeout=10)
    all_lines = lines + lines2
    if matched:
        ssid, pw = _parse_ap_info(all_lines)
        log_activity(
            f"{label} in CAPTIVE PORTAL mode — "
            f"SSID: {ssid}  Password: {pw}",
            "ok",
        )
    else:
        ip, via = _parse_wifi_info(all_lines)
        if ip:
            log_activity(
                f"{label} stayed in NORMAL mode (IP: {ip}) — "
                f"resets too slow, WiFi connected before portal threshold",
                "error",
            )
        else:
            log_activity(f"{label} — portal mode not detected", "error")

    ser.close()
    slot["state"] = STATE_IDLE if slot["present"] else STATE_ABSENT


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"[portal] {self.address_string()} {fmt % args}", flush=True)

    # -- helpers --

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            pass  # Client disconnected before reading response

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return None
        return json.loads(self.rfile.read(length))

    # -- routes --

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/devices":
            self._handle_get_devices()
        elif path == "/api/info":
            self._handle_get_info()
        elif path == "/api/wifi/ping":
            self._handle_wifi_ping()
        elif path == "/api/wifi/mode":
            self._handle_wifi_mode_get()
        elif path == "/api/wifi/ap_status":
            self._handle_wifi_ap_status()
        elif path == "/api/wifi/scan":
            self._handle_wifi_scan()
        elif path == "/api/wifi/events":
            qs = parse_qs(parsed.query)
            self._handle_wifi_events(qs)
        elif path == "/api/log":
            qs = parse_qs(parsed.query)
            self._handle_get_log(qs)
        elif path == "/api/human/status":
            self._handle_human_status()
        elif path == "/api/test/progress":
            self._handle_test_progress()
        elif path == "/api/gpio/status":
            self._handle_gpio_status()
        elif path in ("/", "/index.html"):
            self._serve_ui()
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/hotplug":
            self._handle_hotplug()
        elif path == "/api/serial/reset":
            self._handle_serial_reset()
        elif path == "/api/serial/monitor":
            self._handle_serial_monitor()
        elif path == "/api/enter-portal":
            self._handle_enter_portal()
        elif path == "/api/start":
            self._handle_start()
        elif path == "/api/stop":
            self._handle_stop()
        elif path == "/api/wifi/mode":
            self._handle_wifi_mode_post()
        elif path == "/api/wifi/ap_start":
            self._handle_wifi_ap_start()
        elif path == "/api/wifi/ap_stop":
            self._handle_wifi_ap_stop()
        elif path == "/api/wifi/sta_join":
            self._handle_wifi_sta_join()
        elif path == "/api/wifi/sta_leave":
            self._handle_wifi_sta_leave()
        elif path == "/api/wifi/http":
            self._handle_wifi_http()
        elif path == "/api/wifi/lease_event":
            self._handle_wifi_lease_event()
        elif path == "/api/human-interaction":
            self._handle_human_interaction()
        elif path == "/api/human/done":
            self._handle_human_done()
        elif path == "/api/human/cancel":
            self._handle_human_cancel()
        elif path == "/api/test/update":
            self._handle_test_update()
        elif path == "/api/gpio/set":
            self._handle_gpio_set()
        else:
            self._send_json({"error": "not found"}, 404)

    # -- handlers --

    def _handle_get_devices(self):
        _refresh_host_ip()
        infos = []
        for slot in slots.values():
            _refresh_slot_health(slot)
            infos.append(_slot_info(slot))
        self._send_json({"slots": infos, "host_ip": host_ip, "hostname": hostname})

    def _handle_get_info(self):
        _refresh_host_ip()
        self._send_json({
            "host_ip": host_ip,
            "hostname": hostname,
            "slots_configured": sum(1 for s in slots.values() if s["tcp_port"] is not None),
            "slots_running": sum(1 for s in slots.values() if s["running"]),
        })

    def _handle_hotplug(self):
        global seq_counter

        body = self._read_json()
        if body is None:
            self._send_json({"ok": False, "error": "empty body"}, 400)
            return

        action = body.get("action")
        devnode = body.get("devnode")
        id_path = body.get("id_path", "")
        devpath = body.get("devpath", "")

        if not action:
            self._send_json({"ok": False, "error": "missing action"}, 400)
            return

        slot_key = id_path if id_path else devpath
        if not slot_key:
            self._send_json({"ok": False, "error": "missing id_path and devpath"}, 400)
            return

        # Look up or create slot
        if slot_key not in slots:
            slots[slot_key] = _make_dynamic_slot(slot_key)

        slot = slots[slot_key]
        lock = slot["_lock"]

        # Update event bookkeeping (always, even for unknown slots)
        seq_counter += 1
        slot["seq"] = seq_counter
        slot["last_action"] = action
        slot["last_event_ts"] = datetime.now(timezone.utc).isoformat()

        label = slot["label"] or slot_key[-20:]
        configured = slot["tcp_port"] is not None

        # -- Flap detection --
        now = time.time()
        slot["_event_times"].append(now)
        # Prune events older than window
        slot["_event_times"] = [t for t in slot["_event_times"] if now - t < FLAP_WINDOW_S]

        # Recovery: if already flapping, check if device has been quiet long enough
        if slot["flapping"]:
            if len(slot["_event_times"]) < 2:
                # All previous events aged out of window — quiet for >= FLAP_WINDOW_S
                slot["flapping"] = False
                slot["last_error"] = None
                slot["state"] = STATE_IDLE if slot["present"] else STATE_ABSENT
                print(f'[portal] {label}: USB flapping cleared (events aged out)', flush=True)
            else:
                gap = slot["_event_times"][-1] - slot["_event_times"][-2]
                if gap >= FLAP_COOLDOWN_S:
                    slot["flapping"] = False
                    slot["last_error"] = None
                    slot["state"] = STATE_IDLE if slot["present"] else STATE_ABSENT
                    print(f'[portal] {label}: USB flapping cleared (quiet for {gap:.0f}s)', flush=True)

        # Detect new flapping
        if not slot["flapping"] and len(slot["_event_times"]) >= FLAP_THRESHOLD:
            slot["flapping"] = True
            slot["state"] = STATE_FLAPPING
            slot["last_error"] = "USB flapping detected — device is connect/disconnect cycling"
            print(f'[portal] {label}: USB flapping detected ({len(slot["_event_times"])} events in {FLAP_WINDOW_S}s)', flush=True)
            # Stop proxy proactively if running
            if slot["running"] and slot["pid"]:
                with lock:
                    stop_proxy(slot)
                slot["last_error"] = "USB flapping detected — device is connect/disconnect cycling"

        if action == "add":
            slot["present"] = True
            slot["devnode"] = devnode
            if not slot["flapping"]:
                slot["state"] = STATE_IDLE

            if slot["flapping"]:
                pass  # No proxy start; UI shows warning
            elif configured:
                # Start proxy in a background thread so we don't block the
                # HTTP response for the settle + port-listen check.
                def _bg_start(s=slot, lk=lock, dn=devnode):
                    # Native USB (ttyACM): delay before opening port so the
                    # chip boots past the download-mode-sensitive phase.
                    if dn and "ttyACM" in dn:
                        time.sleep(NATIVE_USB_BOOT_DELAY_S)
                    with lk:
                        if s["flapping"]:
                            return  # Flapping detected while queued
                        # Stop existing proxy first if still running
                        if s["running"] and s["pid"]:
                            stop_proxy(s)
                        start_proxy(s)
                        # If flapping was detected during start_proxy, restore its error
                        if s["flapping"]:
                            s["last_error"] = "USB flapping detected \u2014 device is connect/disconnect cycling"
                threading.Thread(target=_bg_start, daemon=True).start()
            else:
                print(
                    f"[portal] hotplug: unknown slot_key={slot_key} "
                    f"(tracked, no proxy)",
                    flush=True,
                )

        elif action == "remove":
            slot["present"] = False
            slot["state"] = STATE_ABSENT
            if configured and slot["running"]:
                def _bg_stop(s=slot, lk=lock):
                    with lk:
                        stop_proxy(s)
                threading.Thread(target=_bg_stop, daemon=True).start()

        log_activity(
            f"USB {action}: {label} ({devnode or '?'})",
            "ok" if action == "add" else "info",
        )
        print(
            f"[portal] hotplug: {action} slot_key={slot_key} "
            f"devnode={devnode} seq={seq_counter}",
            flush=True,
        )

        self._send_json({
            "ok": True,
            "slot_key": slot_key,
            "seq": seq_counter,
            "accepted": configured,
            "flapping": slot["flapping"],
        })

    def _handle_start(self):
        body = self._read_json()
        if body is None:
            self._send_json({"ok": False, "error": "empty body"}, 400)
            return

        slot_key = body.get("slot_key")
        devnode = body.get("devnode")
        if not slot_key or not devnode:
            self._send_json({"ok": False, "error": "missing slot_key or devnode"}, 400)
            return

        if slot_key not in slots:
            self._send_json({"ok": False, "error": "unknown slot_key"}, 404)
            return

        slot = slots[slot_key]
        with slot["_lock"]:
            if slot["running"] and slot["pid"]:
                stop_proxy(slot)
            slot["devnode"] = devnode
            slot["present"] = True
            ok = start_proxy(slot)
            # start_proxy sets STATE_IDLE on success; ensure idle on failure too
            if not ok and slot["state"] not in (STATE_IDLE, STATE_FLAPPING):
                slot["state"] = STATE_IDLE
        self._send_json({"ok": ok, "slot_key": slot_key, "running": slot["running"]})

    def _handle_stop(self):
        body = self._read_json()
        if body is None:
            self._send_json({"ok": False, "error": "empty body"}, 400)
            return

        slot_key = body.get("slot_key")
        if not slot_key:
            self._send_json({"ok": False, "error": "missing slot_key"}, 400)
            return

        if slot_key not in slots:
            self._send_json({"ok": False, "error": "unknown slot_key"}, 404)
            return

        slot = slots[slot_key]
        with slot["_lock"]:
            stop_proxy(slot)
            slot["state"] = STATE_IDLE if slot["present"] else STATE_ABSENT
        self._send_json({"ok": True, "slot_key": slot_key, "running": False})

    # -- WiFi handlers --

    def _handle_wifi_ping(self):
        self._send_json({"ok": True, **wifi_controller.ping()})

    def _handle_wifi_mode_get(self):
        self._send_json({"ok": True, **wifi_controller.get_mode()})

    def _handle_wifi_mode_post(self):
        body = self._read_json()
        if body is None:
            self._send_json({"ok": False, "error": "empty body"}, 400)
            return
        mode = body.get("mode")
        if mode not in ("wifi-testing", "serial-interface"):
            self._send_json({"ok": False, "error": "mode must be 'wifi-testing' or 'serial-interface'"}, 400)
            return
        ssid = body.get("ssid", "")
        password = body.get("pass", "")
        try:
            result = wifi_controller.set_mode(mode, ssid, password)
            self._send_json({"ok": True, **result})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)})

    def _handle_wifi_ap_start(self):
        body = self._read_json()
        if body is None:
            self._send_json({"ok": False, "error": "empty body"}, 400)
            return
        ssid = body.get("ssid")
        if not ssid:
            self._send_json({"ok": False, "error": "missing ssid"}, 400)
            return
        password = body.get("pass", "")
        channel = body.get("channel", 6)
        try:
            result = wifi_controller.ap_start(ssid, password, channel)
            self._send_json({"ok": True, **result})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)})

    def _handle_wifi_ap_stop(self):
        try:
            wifi_controller.ap_stop()
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)})

    def _handle_wifi_ap_status(self):
        self._send_json({"ok": True, **wifi_controller.ap_status()})

    def _handle_wifi_sta_join(self):
        body = self._read_json()
        if body is None:
            self._send_json({"ok": False, "error": "empty body"}, 400)
            return
        ssid = body.get("ssid")
        if not ssid:
            self._send_json({"ok": False, "error": "missing ssid"}, 400)
            return
        password = body.get("pass", "")
        timeout = body.get("timeout", 15)
        log_activity(f"WiFi STA joining '{ssid}'...", "step")
        try:
            result = wifi_controller.sta_join(ssid, password, timeout)
            log_activity(f"WiFi STA connected to '{ssid}' — IP: {result.get('ip', '?')}", "ok")
            self._send_json({"ok": True, **result})
        except Exception as e:
            log_activity(f"WiFi STA join failed: {e}", "error")
            self._send_json({"ok": False, "error": str(e)})

    def _handle_wifi_sta_leave(self):
        log_activity("WiFi STA disconnecting", "step")
        try:
            wifi_controller.sta_leave()
            log_activity("WiFi STA disconnected", "ok")
            self._send_json({"ok": True})
        except Exception as e:
            log_activity(f"WiFi STA leave failed: {e}", "error")
            self._send_json({"ok": False, "error": str(e)})

    def _handle_wifi_http(self):
        body = self._read_json()
        if body is None:
            self._send_json({"ok": False, "error": "empty body"}, 400)
            return
        method = body.get("method", "GET")
        url = body.get("url")
        if not url:
            self._send_json({"ok": False, "error": "missing url"}, 400)
            return
        headers = body.get("headers")
        req_body = body.get("body")  # base64 encoded
        timeout = body.get("timeout", 10)
        log_activity(f"HTTP relay {method} {url}", "step")
        try:
            result = wifi_controller.http_relay(method, url, headers, req_body, timeout)
            log_activity(f"HTTP relay {method} {url} — {result.get('status', '?')}", "ok")
            self._send_json({"ok": True, **result})
        except Exception as e:
            log_activity(f"HTTP relay failed: {e}", "error")
            self._send_json({"ok": False, "error": str(e)})

    def _handle_wifi_scan(self):
        log_activity("WiFi scanning...", "step")
        try:
            result = wifi_controller.scan()
            n = len(result.get("networks", []))
            log_activity(f"WiFi scan found {n} networks", "ok")
            self._send_json({"ok": True, **result})
        except Exception as e:
            log_activity(f"WiFi scan failed: {e}", "error")
            self._send_json({"ok": False, "error": str(e)})

    def _handle_wifi_events(self, qs):
        timeout = 0
        if "timeout" in qs:
            try:
                timeout = float(qs["timeout"][0])
            except (ValueError, IndexError):
                pass
        events = wifi_controller.get_events(timeout)
        self._send_json({"ok": True, "events": events})

    def _handle_wifi_lease_event(self):
        body = self._read_json()
        if body is None:
            self._send_json({"ok": False, "error": "empty body"}, 400)
            return
        action = body.get("action", "")
        mac = body.get("mac", "")
        ip = body.get("ip", "")
        hostname = body.get("hostname", "")
        if not action or not mac:
            self._send_json({"ok": False, "error": "missing action or mac"}, 400)
            return
        wifi_controller.handle_lease_event(action, mac, ip, hostname)
        self._send_json({"ok": True})

    # -- serial services (FR-008, FR-009) --

    def _handle_serial_reset(self):
        body = self._read_json() or {}
        slot_label = body.get("slot")
        if not slot_label:
            self._send_json({"ok": False, "error": "missing 'slot' field"}, 400)
            return
        slot = _find_slot_by_label(slot_label)
        if not slot:
            self._send_json({"ok": False, "error": f"slot '{slot_label}' not found"})
            return
        log_activity(f"serial.reset({slot_label})", "step")
        result = serial_reset(slot)
        if result["ok"]:
            log_activity(f"serial.reset({slot_label}) — done, {len(result.get('output', []))} lines", "ok")
        else:
            log_activity(f"serial.reset({slot_label}) — {result.get('error', 'failed')}", "error")
        self._send_json(result)

    def _handle_serial_monitor(self):
        body = self._read_json() or {}
        slot_label = body.get("slot")
        if not slot_label:
            self._send_json({"ok": False, "error": "missing 'slot' field"}, 400)
            return
        slot = _find_slot_by_label(slot_label)
        if not slot:
            self._send_json({"ok": False, "error": f"slot '{slot_label}' not found"})
            return
        pattern = body.get("pattern")
        timeout = float(body.get("timeout", 10))
        log_activity(f"serial.monitor({slot_label}, pattern={pattern!r}, timeout={timeout})", "step")
        result = serial_monitor(slot, pattern, timeout)
        if result["ok"]:
            if result.get("matched"):
                log_activity(f"serial.monitor({slot_label}) — matched: {result['line']}", "ok")
            else:
                log_activity(f"serial.monitor({slot_label}) — timeout, no match", "info")
        else:
            log_activity(f"serial.monitor({slot_label}) — {result.get('error', 'failed')}", "error")
        self._send_json(result)

    # -- activity log & enter-portal --

    def _handle_get_log(self, qs):
        since = qs.get("since", [None])[0]
        entries = list(activity_log)
        if since:
            entries = [e for e in entries if e["ts"] > since]
        self._send_json({"ok": True, "entries": entries})

    def _handle_enter_portal(self):
        global _enter_portal_running
        body = self._read_json() or {}
        slot_label = body.get("slot", "SLOT2")
        num_resets = int(body.get("resets", 3))

        if _enter_portal_running:
            self._send_json({"ok": False, "error": "enter-portal already running"})
            return

        # Find slot by label
        target_slot = _find_slot_by_label(slot_label)
        if not target_slot:
            self._send_json({"ok": False, "error": f"slot '{slot_label}' not found"})
            return
        if not target_slot["tcp_port"]:
            self._send_json({"ok": False, "error": f"slot '{slot_label}' has no tcp_port"})
            return

        _enter_portal_running = True
        log_activity(f"Enter-portal started for {slot_label}", "step")

        def _bg_enter_portal(slot, n_resets):
            global _enter_portal_running
            try:
                _do_enter_portal(slot, n_resets)
            except Exception as e:
                log_activity(f"Enter-portal error: {e}", "error")
            finally:
                _enter_portal_running = False

        threading.Thread(
            target=_bg_enter_portal,
            args=(target_slot, num_resets),
            daemon=True,
        ).start()

        self._send_json({"ok": True, "message": "enter-portal started in background"})

    # -- human interaction handlers (event-driven, blocking) --

    def _handle_human_interaction(self):
        """Blocking endpoint — stays open until human clicks Done/Cancel or timeout."""
        global _human_event, _human_confirmed, _human_message

        body = self._read_json()
        if not body or not body.get("message"):
            self._send_json({"ok": False, "error": "missing message"}, 400)
            return
        timeout = float(body.get("timeout", 120))

        with _human_lock:
            if _human_event is not None:
                self._send_json({"ok": False, "error": "another request pending"}, 409)
                return
            _human_event = threading.Event()
            _human_confirmed = False
            _human_message = body["message"]

        log_activity(f"Human interaction: {body['message']}", "step")

        # Block here until Done/Cancel or timeout
        responded = _human_event.wait(timeout=timeout)

        with _human_lock:
            confirmed = _human_confirmed
            msg = _human_message
            _human_event = None
            _human_message = None

        if responded:
            cat = "ok" if confirmed else "info"
            log_activity(f"Human {'confirmed' if confirmed else 'cancelled'}: {msg}", cat)
            self._send_json({"ok": True, "confirmed": confirmed})
        else:
            log_activity(f"Human interaction timed out: {msg}", "error")
            self._send_json({"ok": True, "confirmed": False, "timeout": True})

    def _handle_human_status(self):
        """UI polls this to show/hide the modal."""
        with _human_lock:
            if _human_event is not None and not _human_event.is_set():
                self._send_json({"ok": True, "pending": True, "message": _human_message})
            else:
                self._send_json({"ok": True, "pending": False, "message": ""})

    def _handle_human_done(self):
        """UI Done button — wakes the blocking handler with confirmed=True."""
        global _human_confirmed
        with _human_lock:
            if _human_event is None or _human_event.is_set():
                self._send_json({"ok": False, "error": "no pending request"})
                return
            _human_confirmed = True
            _human_event.set()
        self._send_json({"ok": True})

    def _handle_human_cancel(self):
        """UI Cancel button — wakes the blocking handler with confirmed=False."""
        global _human_confirmed
        with _human_lock:
            if _human_event is None or _human_event.is_set():
                self._send_json({"ok": False, "error": "no pending request"})
                return
            _human_confirmed = False
            _human_event.set()
        self._send_json({"ok": True})

    # -- test progress handlers --

    def _handle_test_progress(self):
        """GET /api/test/progress — UI polls this for test session state."""
        with _test_lock:
            if _test_session is None:
                self._send_json({"ok": True, "active": False})
            else:
                self._send_json({"ok": True, "active": True, **_test_session})

    def _handle_test_update(self):
        """POST /api/test/update — test scripts push progress updates."""
        global _test_session

        body = self._read_json()
        if not body:
            self._send_json({"ok": False, "error": "empty body"}, 400)
            return

        with _test_lock:
            # End session
            if body.get("end"):
                _test_session = None
                self._send_json({"ok": True})
                return

            # Start session (spec field present)
            if "spec" in body:
                _test_session = {
                    "spec": body["spec"],
                    "phase": body.get("phase", ""),
                    "total": body.get("total", 0),
                    "completed": [],
                    "current": None,
                }

            if _test_session is None:
                self._send_json({"ok": False, "error": "no active session"}, 400)
                return

            # Update phase if provided
            if "phase" in body and "spec" not in body:
                _test_session["phase"] = body["phase"]

            # Update total if provided
            if "total" in body and "spec" not in body:
                _test_session["total"] = body["total"]

            # Update current test
            if "current" in body:
                _test_session["current"] = body["current"]

            # Record a result
            if "result" in body:
                _test_session["completed"].append(body["result"])
                _test_session["current"] = None

        self._send_json({"ok": True})

    # -- GPIO handlers --

    def _handle_gpio_set(self):
        body = self._read_json()
        if not body:
            self._send_json({"ok": False, "error": "empty body"}, 400)
            return
        pin = body.get("pin")
        value = body.get("value")
        if pin is None or value is None:
            self._send_json({"ok": False, "error": "missing pin or value"}, 400)
            return
        if not isinstance(pin, int) or pin not in GPIO_ALLOWED:
            self._send_json({"ok": False, "error": f"pin {pin} not in allowed set"}, 400)
            return
        if value not in (0, 1, "z"):
            self._send_json({"ok": False, "error": "value must be 0, 1, or 'z'"}, 400)
            return
        try:
            _gpio_set(pin, value)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)})
            return
        self._send_json({"ok": True, "pin": pin, "value": value})

    def _handle_gpio_status(self):
        pins = {}
        with _gpio_lock:
            for pin, req in _gpio_requests.items():
                try:
                    val = req.get_value(pin)
                    # Track direction from our own state
                    direction = _gpio_directions.get(pin, "unknown")
                    pins[str(pin)] = {"direction": direction, "value": val.value}
                except Exception:
                    pins[str(pin)] = {"direction": "unknown", "value": None}
        self._send_json({"ok": True, "pins": pins})

    def _serve_ui(self):
        html = _UI_HTML
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


_UI_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RFC2217 Serial Portal</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        html { height: 100%; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            min-height: 100vh;
            padding: 20px;
            display: flex; flex-direction: column;
        }
        h1 { text-align: center; margin-bottom: 30px; color: #00d4ff; }
        h2 { color: #00d4ff; margin: 30px 0 15px; text-align: center; }
        .main-content {
            max-width: 1000px; margin: 0 auto; width: 100%;
            display: flex; flex-direction: column; flex: 1; min-height: 0;
        }
        .slots {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
        }
        .slot {
            background: #16213e; border-radius: 12px; padding: 20px;
            border: 2px solid #0f3460; transition: all 0.3s;
        }
        .slot.idle { border-color: #00d4ff; box-shadow: 0 0 20px rgba(0,212,255,0.2); }
        .slot.running { border-color: #00d4ff; box-shadow: 0 0 20px rgba(0,212,255,0.2); }
        .slot.resetting { border-color: #e67e22; box-shadow: 0 0 20px rgba(230,126,34,0.2); }
        .slot.monitoring { border-color: #9b59b6; box-shadow: 0 0 20px rgba(155,89,182,0.2); }
        .slot.flapping { border-color: #e74c3c; background: #1a0000; }
        .slot.absent { border-color: #333; }
        .slot.present { border-color: #555; }
        .slot-header {
            display: flex; justify-content: space-between;
            align-items: center; margin-bottom: 15px;
        }
        .slot-label { font-size: 1.4em; font-weight: bold; }
        .status {
            padding: 4px 12px; border-radius: 20px;
            font-size: 0.85em; font-weight: bold;
        }
        .status.idle { background: #00d4ff; color: #1a1a2e; }
        .status.running { background: #00d4ff; color: #1a1a2e; }
        .status.resetting { background: #e67e22; color: #fff; }
        .status.monitoring { background: #9b59b6; color: #fff; }
        .status.flapping { background: #e74c3c; color: #fff; }
        .status.absent { background: #333; color: #666; }
        .status.present { background: #555; color: #ccc; }
        .status.stopped { background: #333; color: #666; }
        .slot-info { font-size: 0.9em; color: #aaa; margin-bottom: 15px; }
        .slot-info div { margin: 5px 0; }
        .slot-info span { color: #00d4ff; font-family: monospace; }
        .url-box {
            background: #0f3460; padding: 10px; border-radius: 8px;
            font-family: monospace; font-size: 0.9em;
            word-break: break-all; cursor: pointer; transition: background 0.2s;
        }
        .url-box:hover { background: #1a4a7a; }
        .url-box.empty { color: #666; cursor: default; }
        .copied { background: #00d4ff !important; color: #1a1a2e !important; }
        .error { color: #ff6b6b; font-size: 0.85em; margin-top: 10px; }
        .flap-warning {
            color: #e74c3c; font-weight: bold; padding: 6px 10px;
            background: rgba(231,76,60,0.15); border-radius: 4px; margin-top: 8px;
        }
        .info { text-align: center; color: #666; margin-top: 30px; font-size: 0.85em; }
        /* Activity log */
        .log-section {
            margin: 20px 0 0;
            background: #16213e; border-radius: 12px; padding: 20px;
            border: 2px solid #0f3460;
            display: flex; flex-direction: column;
            flex: 1; min-height: 0;
        }
        .log-section h2 { margin: 0 0 10px; font-size: 1.1em; color: #eee; flex-shrink: 0; }
        .log-entries {
            background: #0a0a1a; border-radius: 8px; padding: 10px;
            flex: 1; overflow-y: auto; font-family: monospace;
            font-size: 0.82em; line-height: 1.6;
        }
        .log-entries:empty::after { content: 'No activity yet'; color: #555; }
        .log-entry { white-space: pre-wrap; word-break: break-all; }
        .log-entry .ts { color: #555; }
        .log-entry.cat-info { color: #aaa; }
        .log-entry.cat-step { color: #00d4ff; }
        .log-entry.cat-ok { color: #2ecc71; }
        .log-entry.cat-error { color: #ff6b6b; }
        .log-actions { margin-top: 10px; display: flex; gap: 8px; }
        .log-actions button {
            background: #0f3460; color: #aaa; border: 1px solid #333;
            padding: 6px 14px; border-radius: 6px; cursor: pointer;
            font-size: 0.85em; transition: all 0.2s;
        }
        .log-actions button:hover { background: #1a4a7a; color: #eee; }
        .log-actions button.primary { background: #00d4ff; color: #1a1a2e; border-color: #00d4ff; font-weight: bold; }
        .log-actions button.primary:hover { background: #00b8d9; }
        .log-actions button:disabled { background: #333; color: #555; cursor: not-allowed; }
        /* Human interaction request overlay */
        .human-overlay {
            display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.8); z-index: 9999;
            justify-content: center; align-items: center;
        }
        .human-overlay.visible { display: flex; }
        .human-modal {
            background: #1a1a2e; border: 3px solid #ff8c00; border-radius: 16px;
            padding: 40px; max-width: 520px; width: 90%; text-align: center;
            animation: pulse-border 2s ease-in-out infinite;
        }
        @keyframes pulse-border {
            0%, 100% { border-color: #ff8c00; box-shadow: 0 0 20px rgba(255,140,0,0.3); }
            50% { border-color: #ffa500; box-shadow: 0 0 40px rgba(255,165,0,0.6); }
        }
        .human-modal h2 { color: #ff8c00; margin: 0 0 10px; font-size: 1.4em; }
        .human-modal .human-message { color: #eee; font-size: 1.2em; margin: 20px 0 25px; line-height: 1.5; }
        .human-modal .human-status { color: #aaa; font-size: 0.9em; margin: 10px 0; min-height: 1.2em; }
        .human-modal .human-buttons { display: flex; gap: 15px; justify-content: center; }
        .human-modal .btn-done {
            background: #28a745; color: #fff; border: none; padding: 12px 40px;
            border-radius: 8px; font-size: 1.1em; font-weight: bold; cursor: pointer;
        }
        .human-modal .btn-done:hover { background: #218838; }
        .human-modal .btn-cancel {
            background: #555; color: #ccc; border: none; padding: 12px 30px;
            border-radius: 8px; font-size: 1em; cursor: pointer;
        }
        .human-modal .btn-cancel:hover { background: #666; }
        /* Test progress panel */
        .test-section { margin: 20px 0 0; }
        .test-progress { background: #16213e; border-radius: 12px; padding: 20px; border: 2px solid #0f3460; }
        .test-header { font-size: 1.1em; color: #e0e0e0; margin-bottom: 10px; }
        .test-bar-container { background: #333; border-radius: 4px; height: 8px; margin-bottom: 8px; }
        .test-bar { background: #28a745; height: 100%; border-radius: 4px; transition: width 0.3s; }
        .test-counter { color: #999; font-size: 0.9em; margin-bottom: 12px; }
        .test-current { padding: 12px; border-radius: 6px; margin-bottom: 12px;
            background: #1a3a1a; border-left: 4px solid #28a745; }
        .test-current.manual { background: #3a2a00; border-left: 4px solid #f0a030;
            animation: manual-pulse 2s ease-in-out infinite; }
        @keyframes manual-pulse {
            0%, 100% { border-left-color: #f0a030; }
            50% { border-left-color: #ff6600; }
        }
        .test-current .test-id { font-weight: bold; color: #fff; }
        .test-current .test-step { color: #ccc; margin-top: 4px; }
        .test-results { max-height: 200px; overflow-y: auto; }
        .test-result { display: flex; gap: 10px; padding: 4px 0; font-size: 0.9em; color: #aaa; }
        .test-result .badge { font-weight: bold; min-width: 40px; }
        .test-result .badge.pass { color: #28a745; }
        .test-result .badge.fail { color: #dc3545; }
        .test-result .badge.skip { color: #ffc107; }
    </style>
</head>
<body>
    <h1 id="title">RFC2217 Serial Portal</h1>
    <div class="main-content">
    <div class="slots" id="slots"></div>
    <div class="test-section" id="test-section">
        <h2>Test Progress</h2>
        <div class="test-progress">
            <div class="test-header" id="test-header"></div>
            <div class="test-bar-container">
                <div class="test-bar" id="test-bar"></div>
            </div>
            <div class="test-counter" id="test-counter"></div>
            <div class="test-current" id="test-current"></div>
            <div class="test-results" id="test-results"></div>
        </div>
    </div>
    <div class="log-section">
        <h2>Activity Log</h2>
        <div class="log-entries" id="log-entries"></div>
        <div class="log-actions">
            <button onclick="clearLog()">Clear</button>
        </div>
    </div>
    <div class="info" id="info">Auto-refresh every 2 seconds</div>
    </div><!-- /main-content -->
    <div class="human-overlay" id="human-overlay">
        <div class="human-modal">
            <h2>Action Required</h2>
            <div class="human-message" id="human-message"></div>
            <div class="human-status" id="human-status"></div>
            <div class="human-buttons">
                <button class="btn-done" id="btn-human-done">Done</button>
                <button class="btn-cancel" id="btn-human-cancel">Cancel</button>
            </div>
        </div>
    </div>
<script>
let hostName = '';
let hostIp = '';
async function fetchDevices() {
    try {
        const resp = await fetch('/api/devices');
        const data = await resp.json();
        hostName = data.hostname || '';
        hostIp = data.host_ip || '';
        if (hostName) {
            document.getElementById('title').textContent = hostName + ' — Serial Portal';
            document.title = hostName + ' — Serial Portal';
        }
        renderSlots(data.slots);
        document.getElementById('info').textContent =
            'Hostname: ' + hostName + '  |  IP: ' + hostIp + '  |  Auto-refresh every 2s';
    } catch (e) {
        console.error('Error fetching devices:', e);
    }
}


function slotStatus(s) {
    if (s.state) return s.state;
    // Fallback for older portal without state field
    if (s.flapping) return 'flapping';
    if (s.running) return 'idle';
    if (s.present) return 'idle';
    return 'absent';
}
function statusLabel(s) {
    const st = slotStatus(s);
    return st.toUpperCase();
}

function renderSlots(slots) {
    const el = document.getElementById('slots');
    el.innerHTML = slots.map(s => {
        const st = slotStatus(s);
        const label = s.label || s.slot_key.slice(-20);
        const ipUrl = s.url || '';
        const copyTarget = ipUrl;
        return `
        <div class="slot ${st}">
            <div class="slot-header">
                <div class="slot-label">${label}</div>
                <div class="status ${st}">${statusLabel(s)}</div>
            </div>
            <div class="slot-info">
                <div>Port: <span>${s.tcp_port || '-'}</span></div>
                <div>Device: <span>${s.devnode || 'None'}</span></div>
                ${s.pid ? '<div>PID: <span>' + s.pid + '</span></div>' : ''}
            </div>
            <div class="url-box ${s.running || st === 'idle' ? '' : 'empty'}"
                 onclick="${s.running || st === 'idle' ? "copyUrl('" + copyTarget + "',this)" : ''}">
                ${s.running || st === 'idle' ? ipUrl || 'Proxy running' : (s.present || st === 'resetting' || st === 'monitoring' ? 'Device present, proxy not running' : 'No device connected')}
            </div>
            ${s.last_error ? '<div class="error">Error: ' + s.last_error + '</div>' : ''}
            ${s.flapping ? '<div class="flap-warning">&#9888; Device is boot-looping (rapid USB connect/disconnect). Proxy start suppressed until device stabilises.</div>' : ''}
        </div>`;
    }).join('');
}


function copyUrl(url, el) {
    navigator.clipboard.writeText(url);
    el.classList.add('copied');
    el.textContent = 'Copied!';
    setTimeout(() => { el.classList.remove('copied'); el.textContent = url; }, 1000);
}

let lastLogTs = '';

async function fetchLog() {
    try {
        const url = lastLogTs ? '/api/log?since=' + encodeURIComponent(lastLogTs) : '/api/log';
        const resp = await fetch(url);
        const data = await resp.json();
        if (data.entries && data.entries.length > 0) {
            const el = document.getElementById('log-entries');
            for (const e of data.entries) {
                const div = document.createElement('div');
                div.className = 'log-entry cat-' + (e.cat || 'info');
                const t = new Date(e.ts);
                const ts = t.toLocaleTimeString();
                div.innerHTML = '<span class="ts">' + ts + '</span> ' + e.msg;
                el.appendChild(div);
                lastLogTs = e.ts;
            }
            el.scrollTop = el.scrollHeight;
        }
    } catch (e) { /* ignore */ }
}

async function enterPortal() {
    const btn = document.getElementById('btn-enter-portal');
    // Find first running slot
    let slotLabel = 'SLOT2';
    try {
        const resp = await fetch('/api/devices');
        const data = await resp.json();
        const running = data.slots.find(s => s.running);
        if (running) slotLabel = running.label;
    } catch (e) { /* use default */ }
    const slot = prompt('Slot to enter captive portal:', slotLabel);
    if (!slot) return;
    btn.disabled = true;
    btn.textContent = 'Running...';
    try {
        await fetch('/api/enter-portal', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({slot: slot})
        });
    } catch (e) {
        alert('Error: ' + e);
    }
    // Re-enable after 30s (operation runs in background)
    setTimeout(() => { btn.disabled = false; btn.textContent = 'Enter Captive Portal'; }, 30000);
}

function clearLog() {
    document.getElementById('log-entries').innerHTML = '';
    lastLogTs = '';
}

let humanPending = false;

async function fetchHuman() {
    try {
        const resp = await fetch('/api/human/status');
        const data = await resp.json();
        const overlay = document.getElementById('human-overlay');
        if (data.pending) {
            if (!humanPending) {
                document.getElementById('human-message').textContent = data.message;
                document.getElementById('human-status').textContent = '';
                overlay.classList.add('visible');
            }
            humanPending = true;
        } else {
            if (humanPending) {
                overlay.classList.remove('visible');
                document.getElementById('human-status').textContent = '';
            }
            humanPending = false;
        }
    } catch (e) { /* ignore */ }
}

document.getElementById('btn-human-done').addEventListener('click', async function() {
    const btn = this;
    const statusEl = document.getElementById('human-status');
    btn.disabled = true;
    btn.textContent = 'Sending...';
    statusEl.textContent = '';
    try {
        const resp = await fetch('/api/human/done', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: '{}'
        });
        const data = await resp.json();
        if (data.ok) {
            document.getElementById('human-overlay').classList.remove('visible');
            humanPending = false;
        } else {
            statusEl.textContent = data.error || 'Failed';
        }
    } catch (e) { statusEl.textContent = 'Error: ' + e; }
    btn.disabled = false;
    btn.textContent = 'Done';
});

document.getElementById('btn-human-cancel').addEventListener('click', async function() {
    const btn = this;
    btn.disabled = true;
    try {
        const resp = await fetch('/api/human/cancel', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: '{}'
        });
        const data = await resp.json();
        if (data && data.ok) {
            document.getElementById('human-overlay').classList.remove('visible');
            humanPending = false;
        }
    } catch (e) { /* ignore */ }
    btn.disabled = false;
});

async function fetchTestProgress() {
    try {
        const resp = await fetch('/api/test/progress');
        const data = await resp.json();

        if (!data.active) {
            document.getElementById('test-header').textContent = 'No test session active';
            document.getElementById('test-bar').style.width = '0%';
            document.getElementById('test-counter').textContent = '';
            document.getElementById('test-current').style.display = 'none';
            document.getElementById('test-results').innerHTML = '';
            return;
        }

        document.getElementById('test-header').textContent = data.spec + ' — ' + data.phase;
        const done = data.completed.length;
        const pct = data.total > 0 ? (done / data.total * 100) : 0;
        document.getElementById('test-bar').style.width = pct + '%';
        document.getElementById('test-counter').textContent = done + ' / ' + data.total + ' completed';

        const cur = document.getElementById('test-current');
        if (data.current) {
            cur.style.display = '';
            cur.className = 'test-current' + (data.current.manual ? ' manual' : '');
            cur.innerHTML = '<div class="test-id">' + data.current.id + ': ' + data.current.name + '</div>'
                + '<div class="test-step">' + data.current.step + '</div>';
        } else {
            cur.style.display = 'none';
        }

        const res = document.getElementById('test-results');
        res.innerHTML = data.completed.slice().reverse().map(function(r) {
            return '<div class="test-result"><span class="badge ' + r.result.toLowerCase() + '">'
                + r.result + '</span><span>' + r.id + ': ' + r.name + '</span>'
                + (r.details ? '<span style="color:#666"> — ' + r.details + '</span>' : '')
                + '</div>';
        }).join('');
    } catch (e) { /* ignore */ }
}

async function refresh() {
    await Promise.all([fetchDevices(), fetchLog(), fetchHuman(), fetchTestProgress()]);
}
refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global slots, host_ip, hostname

    slots = load_config(CONFIG_FILE)
    host_ip = get_host_ip()
    hostname = get_hostname()

    # Pre-compute URLs for configured slots
    for slot in slots.values():
        if slot["tcp_port"]:
            slot["url"] = f"rfc2217://{host_ip}:{slot['tcp_port']}"

    # Scan for devices already plugged in at boot
    scan_existing_devices()

    addr = ("", PORT)
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    httpd = http.server.ThreadingHTTPServer(addr, Handler)
    print(
        f"[portal] v4 listening on http://0.0.0.0:{PORT}  "
        f"host_ip={host_ip}  hostname={hostname}",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("[portal] shutting down", flush=True)
        wifi_controller.shutdown()
        # Stop all running proxies
        for slot in slots.values():
            if slot["running"] and slot["pid"]:
                stop_proxy(slot)
        httpd.server_close()


if __name__ == "__main__":
    sys.exit(main() or 0)
