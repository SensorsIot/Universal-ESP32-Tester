"""
WiFi Controller — manages hostapd, dnsmasq, wpa_supplicant, iw scan, HTTP relay.

Used by the portal to provide WiFi test-instrument functionality via the Pi's
own wlan0 radio.  Mirrors the ESP32-C3 WiFi Tester command set.
"""

import base64
import json
import logging
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from queue import Empty, Queue

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WLAN_IF = os.environ.get("WIFI_WLAN_IF", "wlan0")
AP_IP = os.environ.get("WIFI_AP_IP", "192.168.4.1")
AP_NETMASK = os.environ.get("WIFI_AP_NETMASK", "255.255.255.0")
AP_SUBNET = os.environ.get("WIFI_AP_SUBNET", "192.168.4.0/24")
DHCP_RANGE_START = os.environ.get("WIFI_DHCP_START", "192.168.4.2")
DHCP_RANGE_END = os.environ.get("WIFI_DHCP_END", "192.168.4.20")
DHCP_LEASE_TIME = "1h"

WORK_DIR = "/tmp/wifi-tester"
HOSTAPD_CONF = os.path.join(WORK_DIR, "hostapd.conf")
DNSMASQ_CONF = os.path.join(WORK_DIR, "dnsmasq.conf")
DNSMASQ_LEASES = os.path.join(WORK_DIR, "dnsmasq.leases")
WPA_CONF = os.path.join(WORK_DIR, "wpa_supplicant.conf")
WPA_LOG = os.path.join(WORK_DIR, "wpa_supplicant.log")

VERSION = "1.0.0-pi"

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_ap_active = False
_ap_ssid = ""
_ap_password = ""
_ap_channel = 0
_ap_hostapd_proc = None
_ap_dnsmasq_proc = None

_sta_active = False
_sta_ssid = ""
_sta_wpa_proc = None
_saved_ap = None  # saved AP config to restore after sta_leave

_event_queue: Queue = Queue()
_stations: dict = {}  # mac -> {mac, ip}

_start_time = time.monotonic()

# Mode: "wifi-testing" (default) or "serial-interface"
_mode = "wifi-testing"
_mode_ssid = ""  # SSID when in serial-interface mode

_MODE_DISABLED_ERR = "WiFi testing disabled (Serial Interface mode)"


# ---------------------------------------------------------------------------
# Mode management
# ---------------------------------------------------------------------------

def get_mode():
    """Return current mode and connected SSID (if serial-interface)."""
    with _lock:
        result = {"mode": _mode}
        if _mode == "serial-interface":
            result["ssid"] = _mode_ssid
            # Try to read current IP
            try:
                out = _run(["ip", "-4", "addr", "show", WLAN_IF], check=False)
                m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
                if m:
                    result["ip"] = m.group(1)
            except Exception:
                pass
        return result


def set_mode(mode, ssid="", password=""):
    """Switch mode. Returns mode dict."""
    global _mode, _mode_ssid

    if mode not in ("wifi-testing", "serial-interface"):
        raise ValueError(f"Unknown mode: {mode}")

    with _lock:
        if mode == _mode:
            return {"mode": _mode}

        if mode == "serial-interface":
            if not ssid:
                raise ValueError("ssid required for serial-interface mode")
            # Stop any active AP/STA
            _stop_all_unlocked()
            _mode = "serial-interface"
            _mode_ssid = ssid

        elif mode == "wifi-testing":
            # Disconnect from WiFi, return to testing mode
            _stop_all_unlocked()
            _mode = "wifi-testing"
            _mode_ssid = ""

    # Connect wlan0 to WiFi outside the lock (for serial-interface mode)
    if mode == "serial-interface":
        try:
            sta_join(ssid, password, _internal=True)
        except Exception:
            # Revert on failure
            with _lock:
                _mode = "wifi-testing"
                _mode_ssid = ""
            raise

    return get_mode()


def _check_wifi_testing_mode():
    """Raise RuntimeError if not in wifi-testing mode."""
    if _mode != "wifi-testing":
        raise RuntimeError(_MODE_DISABLED_ERR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_work_dir():
    os.makedirs(WORK_DIR, exist_ok=True)


def _kill_proc(proc, timeout=5.0):
    """Terminate a subprocess, SIGKILL if it won't die."""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass


def _run(cmd, timeout=10, check=True):
    """Run a command, return stdout."""
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=check,
    )
    return result.stdout


def _kill_existing(name):
    """Kill any existing process by name (best effort)."""
    try:
        subprocess.run(
            ["pkill", "-f", name],
            capture_output=True, timeout=5, check=False,
        )
        time.sleep(0.3)
    except Exception:
        pass


def _release_wlan():
    """Ensure wlan0 is not managed by NetworkManager or wpa_supplicant."""
    # Kill any existing wpa_supplicant on wlan0
    try:
        subprocess.run(
            ["pkill", "-f", f"wpa_supplicant.*{WLAN_IF}"],
            capture_output=True, timeout=5, check=False,
        )
    except Exception:
        pass
    # Remove stale control interface socket (prevents "ctrl_iface exists" error)
    ctrl_path = f"/var/run/wpa_supplicant/{WLAN_IF}"
    try:
        os.remove(ctrl_path)
    except FileNotFoundError:
        pass
    except Exception:
        logger.warning("Could not remove %s", ctrl_path)
    # Bring interface down then up to reset state
    try:
        _run(["ip", "link", "set", WLAN_IF, "down"], check=False)
        time.sleep(0.2)
        _run(["ip", "link", "set", WLAN_IF, "up"], check=False)
    except Exception:
        pass


def _flush_addr():
    """Remove all IP addresses from wlan0."""
    try:
        _run(["ip", "addr", "flush", "dev", WLAN_IF], check=False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# AP Mode
# ---------------------------------------------------------------------------

def ap_start(ssid, password="", channel=6):
    """Start SoftAP on wlan0. Returns dict with ip."""
    global _ap_active, _ap_ssid, _ap_password, _ap_channel
    global _ap_hostapd_proc, _ap_dnsmasq_proc

    _check_wifi_testing_mode()
    with _lock:
        # Stop anything running first
        _stop_all_unlocked()

        _ensure_work_dir()

        # Write hostapd config
        hostapd_lines = [
            f"interface={WLAN_IF}",
            "driver=nl80211",
            f"ssid={ssid}",
            "hw_mode=g",
            f"channel={channel}",
            "wmm_enabled=0",
            "macaddr_acl=0",
            "auth_algs=1",
            "ignore_broadcast_ssid=0",
        ]
        if password:
            hostapd_lines += [
                "wpa=2",
                "wpa_key_mgmt=WPA-PSK",
                f"wpa_passphrase={password}",
                "rsn_pairwise=CCMP",
            ]

        with open(HOSTAPD_CONF, "w") as f:
            f.write("\n".join(hostapd_lines) + "\n")

        # Write dnsmasq config
        lease_script = "/usr/local/bin/wifi-lease-notify.sh"
        dnsmasq_lines = [
            f"interface={WLAN_IF}",
            "bind-interfaces",
            f"dhcp-range={DHCP_RANGE_START},{DHCP_RANGE_END},{AP_NETMASK},{DHCP_LEASE_TIME}",
            f"dhcp-leasefile={DNSMASQ_LEASES}",
            "no-resolv",
            "no-daemon",
            "log-dhcp",
        ]
        if os.path.exists(lease_script):
            dnsmasq_lines.append(f"dhcp-script={lease_script}")

        with open(DNSMASQ_CONF, "w") as f:
            f.write("\n".join(dnsmasq_lines) + "\n")

        # Release wlan and configure static IP
        _release_wlan()
        _flush_addr()
        _run(["ip", "addr", "add", f"{AP_IP}/24", "dev", WLAN_IF], check=False)
        _run(["ip", "link", "set", WLAN_IF, "up"], check=False)

        # Start hostapd
        _ap_hostapd_proc = subprocess.Popen(
            ["hostapd", HOSTAPD_CONF],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        # Wait for hostapd to initialise
        time.sleep(1.5)
        if _ap_hostapd_proc.poll() is not None:
            out = _ap_hostapd_proc.stdout.read().decode(errors="replace")
            raise RuntimeError(f"hostapd failed to start: {out[:500]}")

        # Start dnsmasq
        _ap_dnsmasq_proc = subprocess.Popen(
            ["dnsmasq", "-C", DNSMASQ_CONF],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        time.sleep(0.5)
        if _ap_dnsmasq_proc.poll() is not None:
            out = _ap_dnsmasq_proc.stdout.read().decode(errors="replace")
            _kill_proc(_ap_hostapd_proc)
            raise RuntimeError(f"dnsmasq failed to start: {out[:500]}")

        _ap_active = True
        _ap_ssid = ssid
        _ap_password = password
        _ap_channel = channel
        _stations.clear()

        logger.info("AP started: ssid=%s channel=%d ip=%s", ssid, channel, AP_IP)
        return {"ip": AP_IP}


def ap_stop():
    """Stop the SoftAP."""
    with _lock:
        _ap_stop_unlocked()


def _ap_stop_unlocked():
    global _ap_active, _ap_ssid, _ap_password, _ap_channel
    global _ap_hostapd_proc, _ap_dnsmasq_proc

    _kill_proc(_ap_dnsmasq_proc)
    _ap_dnsmasq_proc = None
    _kill_proc(_ap_hostapd_proc)
    _ap_hostapd_proc = None

    _ap_active = False
    _ap_ssid = ""
    _ap_password = ""
    _ap_channel = 0
    _stations.clear()

    _flush_addr()
    logger.info("AP stopped")


def ap_status():
    """Return AP status dict."""
    with _lock:
        return {
            "active": _ap_active,
            "ssid": _ap_ssid if _ap_active else "",
            "channel": _ap_channel if _ap_active else 0,
            "stations": list(_stations.values()) if _ap_active else [],
        }


# ---------------------------------------------------------------------------
# Station tracking (called by lease notify script via portal)
# ---------------------------------------------------------------------------

def handle_lease_event(action, mac, ip, hostname=""):
    """Called when dnsmasq sends a lease event."""
    mac = mac.lower()
    if action in ("add", "old"):
        _stations[mac] = {"mac": mac, "ip": ip}
        evt = {"type": "STA_CONNECT", "mac": mac, "ip": ip}
        if hostname:
            evt["hostname"] = hostname
        _event_queue.put(evt)
        logger.info("Station connected: mac=%s ip=%s", mac, ip)
    elif action == "del":
        _stations.pop(mac, None)
        _event_queue.put({"type": "STA_DISCONNECT", "mac": mac})
        logger.info("Station disconnected: mac=%s", mac)


# ---------------------------------------------------------------------------
# STA Mode
# ---------------------------------------------------------------------------

def sta_join(ssid, password="", timeout=15, _internal=False):
    """Join a WiFi network as a station. Returns dict with ip, gateway."""
    global _sta_active, _sta_ssid, _sta_wpa_proc, _saved_ap

    if not _internal:
        _check_wifi_testing_mode()
    with _lock:
        # Save AP config so sta_leave can restore it
        if _ap_active:
            _saved_ap = {"ssid": _ap_ssid, "password": _ap_password, "channel": _ap_channel}
            logger.info("Saved AP config for restore: ssid=%s channel=%d", _ap_ssid, _ap_channel)
        else:
            _saved_ap = None
        _stop_all_unlocked()
        _ensure_work_dir()

        _release_wlan()
        _flush_addr()
        _run(["ip", "link", "set", WLAN_IF, "up"], check=False)

        # Write wpa_supplicant config
        if password:
            # Use wpa_passphrase for proper encoding
            try:
                out = _run(["wpa_passphrase", ssid, password])
                # wpa_passphrase output lacks ctrl_interface — prepend it
                wpa_conf_content = 'ctrl_interface=/var/run/wpa_supplicant\n' + out
            except Exception:
                # Fallback to plain text config
                wpa_conf_content = (
                    'ctrl_interface=/var/run/wpa_supplicant\n'
                    'network={\n'
                    f'    ssid="{ssid}"\n'
                    f'    psk="{password}"\n'
                    '}\n'
                )
        else:
            wpa_conf_content = (
                'ctrl_interface=/var/run/wpa_supplicant\n'
                'network={\n'
                f'    ssid="{ssid}"\n'
                '    key_mgmt=NONE\n'
                '}\n'
            )

        with open(WPA_CONF, "w") as f:
            f.write(wpa_conf_content)

        # Start wpa_supplicant
        _sta_wpa_proc = subprocess.Popen(
            [
                "wpa_supplicant",
                "-i", WLAN_IF,
                "-c", WPA_CONF,
                "-B",  # background
                "-f", WPA_LOG,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        _sta_wpa_proc.wait(timeout=5)

        # Wait for connection with polling
        deadline = time.monotonic() + timeout
        connected = False
        while time.monotonic() < deadline:
            try:
                result = subprocess.run(
                    ["wpa_cli", "-i", WLAN_IF, "status"],
                    capture_output=True, text=True, timeout=3, check=False,
                )
                if "wpa_state=COMPLETED" in result.stdout:
                    connected = True
                    break
            except Exception:
                pass
            time.sleep(0.5)

        if not connected:
            _sta_stop_unlocked()
            raise RuntimeError(f"Failed to connect to '{ssid}' within {timeout}s")

        # Get IP via DHCP — try dhcpcd (Debian/Bookworm), dhclient, udhcpc
        # dhcpcd on Bookworm runs as a system daemon; -1 sends a control
        # command that returns immediately while the daemon does DHCP in the
        # background (including ARP probing which takes ~3s).
        try:
            _run(["/usr/sbin/dhcpcd", "-1", "-4", WLAN_IF], timeout=timeout, check=False)
        except Exception:
            try:
                _run(["dhclient", "-1", "-v", WLAN_IF], timeout=timeout, check=False)
            except Exception:
                try:
                    _run(["udhcpc", "-i", WLAN_IF, "-n", "-q"], timeout=timeout, check=False)
                except Exception:
                    pass

        # Poll for IPv4 address (dhcpcd ARP probing takes ~3s)
        ip_addr = ""
        gateway = ""
        deadline = time.monotonic() + min(timeout, 15)
        while time.monotonic() < deadline:
            time.sleep(1)
            try:
                out = _run(["ip", "-4", "addr", "show", WLAN_IF])
                m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
                if m:
                    ip_addr = m.group(1)
                    break
            except Exception:
                pass

        if ip_addr:
            try:
                out = _run(["ip", "route", "show", "dev", WLAN_IF])
                m = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", out)
                if m:
                    gateway = m.group(1)
            except Exception:
                pass

        if not ip_addr:
            _sta_stop_unlocked()
            raise RuntimeError(f"Connected to '{ssid}' but no IP obtained")

        _sta_active = True
        _sta_ssid = ssid
        logger.info("STA joined: ssid=%s ip=%s gw=%s", ssid, ip_addr, gateway)
        return {"ip": ip_addr, "gateway": gateway}


def sta_leave():
    """Disconnect from a WiFi network. Restores AP if one was active before sta_join."""
    global _saved_ap
    with _lock:
        _sta_stop_unlocked()
        saved = _saved_ap
        _saved_ap = None
    # Restore AP outside lock (ap_start acquires lock)
    if saved:
        logger.info("Restoring AP after sta_leave: ssid=%s channel=%d", saved["ssid"], saved["channel"])
        ap_start(saved["ssid"], password=saved["password"], channel=saved["channel"])


def _sta_stop_unlocked():
    global _sta_active, _sta_ssid, _sta_wpa_proc

    if _sta_wpa_proc is not None:
        _kill_proc(_sta_wpa_proc)
        _sta_wpa_proc = None

    # Kill any wpa_supplicant on our interface
    try:
        subprocess.run(
            ["pkill", "-f", f"wpa_supplicant.*{WLAN_IF}"],
            capture_output=True, timeout=5, check=False,
        )
    except Exception:
        pass

    # Remove stale control interface socket
    ctrl_path = f"/var/run/wpa_supplicant/{WLAN_IF}"
    try:
        os.remove(ctrl_path)
    except FileNotFoundError:
        pass
    except Exception:
        pass

    # Release DHCP — try dhcpcd (Debian/Bookworm), dhclient
    try:
        subprocess.run(
            ["/usr/sbin/dhcpcd", "--release", WLAN_IF],
            capture_output=True, timeout=5, check=False,
        )
    except Exception:
        try:
            subprocess.run(
                ["dhclient", "-r", WLAN_IF],
                capture_output=True, timeout=5, check=False,
            )
        except Exception:
            pass

    _flush_addr()
    _sta_active = False
    _sta_ssid = ""
    logger.info("STA disconnected")


# ---------------------------------------------------------------------------
# Combined stop
# ---------------------------------------------------------------------------

def _stop_all_unlocked():
    """Stop both AP and STA (caller holds _lock)."""
    _ap_stop_unlocked()
    _sta_stop_unlocked()


def shutdown():
    """Clean shutdown — stop everything."""
    global _mode, _mode_ssid
    with _lock:
        _stop_all_unlocked()
        _mode = "wifi-testing"
        _mode_ssid = ""
    logger.info("WiFi controller shut down")


# ---------------------------------------------------------------------------
# WiFi Scan
# ---------------------------------------------------------------------------

def scan():
    """Scan for WiFi networks using iw. Returns dict with networks list."""
    _check_wifi_testing_mode()
    # Ensure interface is up
    try:
        _run(["ip", "link", "set", WLAN_IF, "up"], check=False)
    except Exception:
        pass

    try:
        out = _run(
            ["iw", "dev", WLAN_IF, "scan", "-u"],
            timeout=15, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"networks": []}

    networks = []
    current = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("BSS "):
            if current.get("ssid"):
                networks.append(current)
            current = {"ssid": "", "rssi": 0, "auth": "OPEN"}
        elif line.startswith("SSID:"):
            ssid = line[5:].strip()
            current["ssid"] = ssid
        elif line.startswith("signal:"):
            # signal: -45.00 dBm
            m = re.search(r"(-?\d+\.?\d*)", line)
            if m:
                current["rssi"] = int(float(m.group(1)))
        elif "WPA" in line or "RSN" in line:
            current["auth"] = "WPA2" if "RSN" in line else "WPA"
        elif "WEP" in line:
            current["auth"] = "WEP"

    # Don't forget last entry
    if current.get("ssid"):
        networks.append(current)

    # Sort by signal strength (strongest first)
    networks.sort(key=lambda n: n.get("rssi", -100), reverse=True)
    return {"networks": networks}


# ---------------------------------------------------------------------------
# HTTP Relay
# ---------------------------------------------------------------------------

def http_relay(method, url, headers=None, body=None, timeout=10):
    """Perform an HTTP request from the Pi. Returns dict with status, headers, body."""
    _check_wifi_testing_mode()
    req_headers = headers or {}
    body_bytes = None
    if body:
        body_bytes = base64.b64decode(body)

    req = urllib.request.Request(
        url,
        data=body_bytes,
        headers=req_headers,
        method=method.upper(),
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_body = resp.read()
            resp_headers = dict(resp.getheaders())
            return {
                "status": resp.status,
                "headers": resp_headers,
                "body": base64.b64encode(resp_body).decode("ascii"),
            }
    except urllib.error.HTTPError as e:
        resp_body = e.read() if e.fp else b""
        resp_headers = dict(e.headers.items()) if e.headers else {}
        return {
            "status": e.code,
            "headers": resp_headers,
            "body": base64.b64encode(resp_body).decode("ascii"),
        }
    except urllib.error.URLError as e:
        raise RuntimeError(f"HTTP request failed: {e.reason}")
    except Exception as e:
        raise RuntimeError(f"HTTP request failed: {e}")


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def get_events(timeout=0):
    """Drain the event queue. If timeout > 0, long-poll for first event."""
    events = []

    if timeout > 0 and _event_queue.empty():
        try:
            evt = _event_queue.get(timeout=timeout)
            events.append(evt)
        except Empty:
            return events

    # Drain remaining
    while True:
        try:
            events.append(_event_queue.get_nowait())
        except Empty:
            break

    return events


# ---------------------------------------------------------------------------
# Ping
# ---------------------------------------------------------------------------

def ping():
    """Return version and uptime."""
    return {
        "fw_version": VERSION,
        "uptime": int(time.monotonic() - _start_time),
    }
