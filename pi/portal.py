#!/usr/bin/env python3
"""
RFC2217 Portal v3 — Proxy Supervisor

HTTP server that tracks USB serial device hotplug events and manages
serial_proxy.py lifecycle.  On hotplug add → start proxy; on remove → stop it.
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
PROXY_PATHS = [
    "/usr/local/bin/esp_rfc2217_server.py",
    "/usr/local/bin/serial_proxy.py",
    "/usr/local/bin/serial-proxy",
]
LOG_DIR = "/var/log/serial"

# Module-level state
slots: dict[str, dict] = {}
seq_counter: int = 0
host_ip: str = "127.0.0.1"  # refreshed periodically; see _refresh_host_ip()
hostname: str = "localhost"


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
                "_lock": threading.Lock(),
            }
        print(f"[portal] loaded {len(result)} slot(s) from {path}", flush=True)
    except FileNotFoundError:
        print(f"[portal] config not found: {path} (starting with no slots)", flush=True)
    except Exception as exc:
        print(f"[portal] error loading config: {exc}", flush=True)
    return result


def get_host_ip() -> str:
    """Detect host IP via UDP socket trick."""
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


def _find_proxy_exe() -> str | None:
    for p in PROXY_PATHS:
        if os.path.exists(p):
            return p
    return None


def wait_for_device(devnode: str, timeout: float = 5.0) -> bool:
    """Poll os.open() until the device node is ready."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(devnode):
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
    """Start serial_proxy for *slot*.  Returns True on success."""
    devnode = slot["devnode"]
    tcp_port = slot["tcp_port"]
    label = slot["label"]

    proxy_exe = _find_proxy_exe()
    if not proxy_exe:
        slot["last_error"] = "No serial proxy executable found"
        print(f"[portal] {label}: {slot['last_error']}", flush=True)
        return False

    # Settle — done *before* acquiring lock (caller holds lock already)
    if not wait_for_device(devnode):
        slot["last_error"] = f"Device {devnode} not ready after settle timeout"
        print(f"[portal] {label}: {slot['last_error']}", flush=True)
        return False

    cmd = ["python3", proxy_exe, "-p", str(tcp_port)]
    if "serial_proxy" in proxy_exe:
        cmd.extend(["-l", LOG_DIR])
    cmd.append(devnode)

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


def _slot_info(slot: dict) -> dict:
    """Return a JSON-safe copy of a slot (excludes _lock)."""
    return {k: v for k, v in slot.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"[portal] {self.address_string()} {fmt % args}", flush=True)

    # -- helpers --

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

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
        elif path in ("/", "/index.html"):
            self._serve_ui()
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/hotplug":
            self._handle_hotplug()
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

        configured = slot["tcp_port"] is not None

        if action == "add":
            slot["present"] = True
            slot["devnode"] = devnode

            if configured:
                # Start proxy in a background thread so we don't block the
                # HTTP response for the settle + port-listen check.
                def _bg_start(s=slot, lk=lock):
                    with lk:
                        # Stop existing proxy first if still running
                        if s["running"] and s["pid"]:
                            stop_proxy(s)
                        start_proxy(s)
                threading.Thread(target=_bg_start, daemon=True).start()
            else:
                print(
                    f"[portal] hotplug: unknown slot_key={slot_key} "
                    f"(tracked, no proxy)",
                    flush=True,
                )

        elif action == "remove":
            slot["present"] = False
            if configured and slot["running"]:
                with lock:
                    stop_proxy(slot)

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
        try:
            result = wifi_controller.sta_join(ssid, password, timeout)
            self._send_json({"ok": True, **result})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)})

    def _handle_wifi_sta_leave(self):
        try:
            wifi_controller.sta_leave()
            self._send_json({"ok": True})
        except Exception as e:
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
        try:
            result = wifi_controller.http_relay(method, url, headers, req_body, timeout)
            self._send_json({"ok": True, **result})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)})

    def _handle_wifi_scan(self):
        try:
            result = wifi_controller.scan()
            self._send_json({"ok": True, **result})
        except Exception as e:
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

    def _serve_ui(self):
        html = _UI_HTML
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
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
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            min-height: 100vh;
            padding: 20px;
        }
        h1 { text-align: center; margin-bottom: 30px; color: #00d4ff; }
        h2 { color: #00d4ff; margin: 30px 0 15px; text-align: center; }
        .slots {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px; max-width: 1000px; margin: 0 auto;
        }
        .slot {
            background: #16213e; border-radius: 12px; padding: 20px;
            border: 2px solid #0f3460; transition: all 0.3s;
        }
        .slot.running { border-color: #00d4ff; box-shadow: 0 0 20px rgba(0,212,255,0.2); }
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
        .status.running { background: #00d4ff; color: #1a1a2e; }
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
        .info { text-align: center; color: #666; margin-top: 30px; font-size: 0.85em; }
        /* WiFi Tester section */
        .wifi-section {
            max-width: 1000px; margin: 20px auto 0;
            background: #16213e; border-radius: 12px; padding: 20px;
            border: 2px solid #0f3460;
        }
        .wifi-section.active { border-color: #00d4ff; box-shadow: 0 0 20px rgba(0,212,255,0.2); }
        .mode-toggle {
            display: flex; gap: 0; margin-bottom: 20px;
            border-radius: 8px; overflow: hidden; border: 1px solid #0f3460;
        }
        .mode-btn {
            flex: 1; padding: 10px 15px; border: none;
            background: #0f3460; color: #aaa; cursor: pointer;
            font-size: 0.95em; font-weight: bold; transition: all 0.2s;
        }
        .mode-btn.active { background: #00d4ff; color: #1a1a2e; }
        .mode-btn:hover:not(.active) { background: #1a4a7a; color: #eee; }
        .wifi-status { font-size: 0.9em; color: #aaa; }
        .wifi-status div { margin: 5px 0; }
        .wifi-status span { color: #00d4ff; font-family: monospace; }
        .wifi-form { margin-top: 15px; }
        .wifi-form input {
            background: #0f3460; border: 1px solid #333; color: #eee;
            padding: 8px 12px; border-radius: 6px; margin-right: 8px;
            font-size: 0.9em; width: 180px;
        }
        .wifi-form button {
            background: #00d4ff; color: #1a1a2e; border: none;
            padding: 8px 20px; border-radius: 6px; cursor: pointer;
            font-weight: bold; font-size: 0.9em;
        }
        .wifi-form button:hover { background: #00b8d9; }
        .wifi-form button:disabled { background: #555; color: #888; cursor: not-allowed; }
    </style>
</head>
<body>
    <h1 id="title">RFC2217 Serial Portal</h1>
    <div class="slots" id="slots"></div>
    <h2>WiFi Tester</h2>
    <div class="wifi-section" id="wifi-section">
        <div class="mode-toggle">
            <button class="mode-btn active" id="btn-wifi-testing"
                    onclick="switchMode('wifi-testing')">WiFi-Testing</button>
            <button class="mode-btn" id="btn-serial-interface"
                    onclick="switchMode('serial-interface')">Serial Interface</button>
        </div>
        <div id="wifi-content"></div>
    </div>
    <div class="info" id="info">Auto-refresh every 2 seconds</div>
<script>
let hostName = '';
let hostIp = '';
let currentMode = 'wifi-testing';
let switching = false;

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
    } catch (e) {
        console.error('Error fetching devices:', e);
    }
}

async function fetchWifi() {
    try {
        const [modeResp, apResp] = await Promise.all([
            fetch('/api/wifi/mode'),
            fetch('/api/wifi/ap_status')
        ]);
        const modeData = await modeResp.json();
        const apData = await apResp.json();
        if (!switching) {
            currentMode = modeData.mode || 'wifi-testing';
            renderWifi(modeData, apData);
        }
        document.getElementById('info').textContent =
            'Hostname: ' + hostName + '  |  IP: ' + hostIp + '  |  Auto-refresh every 2s';
    } catch (e) {
        console.error('Error fetching wifi:', e);
    }
}

function slotStatus(s) {
    if (s.running) return 'running';
    if (s.present) return 'present';
    return 'stopped';
}
function statusLabel(s) {
    if (s.running) return 'RUNNING';
    if (s.present) return 'PRESENT';
    return 'EMPTY';
}

function renderSlots(slots) {
    const el = document.getElementById('slots');
    el.innerHTML = slots.map(s => {
        const st = slotStatus(s);
        const label = s.label || s.slot_key.slice(-20);
        const hostnameUrl = s.running && hostName ? 'rfc2217://' + hostName + ':' + s.tcp_port : '';
        const ipUrl = s.url || '';
        const copyTarget = hostnameUrl || ipUrl;
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
            <div class="url-box ${s.running ? '' : 'empty'}"
                 onclick="${s.running ? "copyUrl('" + copyTarget + "',this)" : ''}">
                ${s.running ? hostnameUrl + '<br><small style=\\'color:#888\\'>' + ipUrl + '</small>' : (s.present ? 'Device present, proxy not running' : 'No device connected')}
            </div>
            ${s.last_error ? '<div class="error">Error: ' + s.last_error + '</div>' : ''}
        </div>`;
    }).join('');
}

function renderWifi(modeData, apData) {
    const section = document.getElementById('wifi-section');
    const btnWT = document.getElementById('btn-wifi-testing');
    const btnSI = document.getElementById('btn-serial-interface');
    const content = document.getElementById('wifi-content');

    const mode = modeData.mode || 'wifi-testing';
    btnWT.className = 'mode-btn' + (mode === 'wifi-testing' ? ' active' : '');
    btnSI.className = 'mode-btn' + (mode === 'serial-interface' ? ' active' : '');

    if (mode === 'wifi-testing') {
        section.className = 'wifi-section' + (apData.active ? ' active' : '');
        let html = '<div class="wifi-status">';
        html += '<div>Mode: <span>WiFi-Testing</span> (wlan0 = test instrument)</div>';
        if (apData.active) {
            html += '<div>AP: <span>' + apData.ssid + '</span> (channel ' + apData.channel + ')</div>';
            const cnt = apData.stations ? apData.stations.length : 0;
            html += '<div>Stations: <span>' + cnt + '</span></div>';
        } else {
            html += '<div>AP: <span style="color:#666">inactive</span></div>';
        }
        html += '</div>';
        content.innerHTML = html;
    } else {
        section.className = 'wifi-section active';
        let html = '<div class="wifi-status">';
        html += '<div>Mode: <span>Serial Interface</span> (wlan0 = LAN, WiFi testing disabled)</div>';
        if (modeData.ssid) {
            html += '<div>Connected: <span>' + modeData.ssid + '</span>';
            if (modeData.ip) html += ' (' + modeData.ip + ')';
            html += '</div>';
        }
        html += '</div>';
        content.innerHTML = html;
    }
}

async function switchMode(mode) {
    if (switching || mode === currentMode) return;
    if (mode === 'serial-interface') {
        const ssid = prompt('WiFi SSID to connect:');
        if (!ssid) return;
        const pass = prompt('WiFi password (leave empty for open):') || '';
        switching = true;
        document.getElementById('btn-serial-interface').textContent = 'Switching...';
        try {
            const resp = await fetch('/api/wifi/mode', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({mode: 'serial-interface', ssid: ssid, pass: pass})
            });
            const data = await resp.json();
            if (!data.ok) alert('Error: ' + data.error);
        } catch (e) {
            alert('Error switching mode: ' + e);
        }
        switching = false;
        document.getElementById('btn-serial-interface').textContent = 'Serial Interface';
    } else {
        switching = true;
        document.getElementById('btn-wifi-testing').textContent = 'Switching...';
        try {
            const resp = await fetch('/api/wifi/mode', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({mode: 'wifi-testing'})
            });
            const data = await resp.json();
            if (!data.ok) alert('Error: ' + data.error);
        } catch (e) {
            alert('Error switching mode: ' + e);
        }
        switching = false;
        document.getElementById('btn-wifi-testing').textContent = 'WiFi-Testing';
    }
    fetchWifi();
}

function copyUrl(url, el) {
    navigator.clipboard.writeText(url);
    el.classList.add('copied');
    el.textContent = 'Copied!';
    setTimeout(() => { el.classList.remove('copied'); el.textContent = url; }, 1000);
}

async function refresh() {
    await Promise.all([fetchDevices(), fetchWifi()]);
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

    os.makedirs(LOG_DIR, exist_ok=True)

    # Scan for devices already plugged in at boot
    scan_existing_devices()

    addr = ("", PORT)
    http.server.HTTPServer.allow_reuse_address = True
    httpd = http.server.HTTPServer(addr, Handler)
    print(
        f"[portal] v3 listening on http://0.0.0.0:{PORT}  "
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
