# Serial Portal — Functional Specification Document

## 1. Overview

### 1.1 Purpose

Combined serial interface and WiFi test instrument running on a single
Raspberry Pi Zero W.  The serial interface exposes USB serial devices to
network clients via RFC2217 protocol with event-driven hotplug and slot-based
port assignment.  The WiFi tester uses the Pi's onboard wlan0 radio as a
test instrument — starting SoftAP, joining networks, scanning, relaying HTTP,
and reporting station events — all controlled over the same HTTP API.

### 1.2 System Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          Network (192.168.0.x)                           │
└──────────────────────────────────────────────────────────────────────────┘
       │  eth0 (USB Ethernet)                          │
       │                                               │
       ▼                                               ▼
┌─────────────────────────┐              ┌─────────────────────────────────┐
│  Serial Portal Pi       │              │  VM Host (192.168.0.160)        │
│  192.168.0.87           │              │                                 │
│                         │              │  ┌─────────────────────┐        │
│  ┌───────────┐          │              │  │ Container A         │        │
│  │ SLOT1     │──────────┼─ :4001 ──────┼──│ rfc2217://:4001     │        │
│  └───────────┘          │              │  └─────────────────────┘        │
│  ┌───────────┐          │              │  ┌─────────────────────┐        │
│  │ SLOT2     │──────────┼─ :4002 ──────┼──│ Container B         │        │
│  └───────────┘          │              │  │ rfc2217://:4002     │        │
│  ┌───────────┐          │              │  └─────────────────────┘        │
│  │ SLOT3     │──────────┼─ :4003       │                                 │
│  └───────────┘          │              └─────────────────────────────────┘
│                         │
│  ┌───────────────────┐  │
│  │ WiFi Tester       │  │
│  │ wlan0 (onboard)   │  │
│  │  AP: 192.168.4.1  │  │
│  │  STA / Scan       │  │
│  └───────────────────┘  │
│                         │
│  Web Portal ────────────┼─ :8080
└─────────────────────────┘
```

### 1.3 Hardware

| Component | Details |
|-----------|---------|
| Raspberry Pi Zero W | 192.168.0.87, onboard wlan0 radio |
| USB Hub | 3-port hub connected to single USB port |
| USB Ethernet adapter | eth0 — wired LAN for management and serial traffic |
| Devices | ESP32, Arduino, or any USB serial device |

### 1.4 Operating Modes

The system operates in one of two modes at any time:

| Mode | Default | eth0 | wlan0 | Serial | WiFi Tester |
|------|---------|------|-------|--------|-------------|
| **WiFi-Testing** | Yes | LAN (management + serial) | Test instrument (AP/STA/scan) | Active | Active |
| **Serial Interface** | No | LAN (management + serial) | Joins WiFi for additional LAN | Active | Disabled |

- **WiFi-Testing** (default): eth0 provides wired LAN connectivity.  wlan0 is
  dedicated to the WiFi test instrument — it can start a SoftAP, join external
  networks, scan, and relay HTTP.  Both serial slots and WiFi tester are active.

- **Serial Interface**: wlan0 joins a user-specified WiFi network to provide
  wireless LAN connectivity (useful when no wired Ethernet is available).
  Serial slots remain active.  WiFi tester endpoints return an error.

Mode is switched via `POST /api/wifi/mode` or the web UI toggle.

### 1.5 Components

| Component | Location | Purpose |
|-----------|----------|---------|
| portal.py (rfc2217-portal) | /usr/local/bin/rfc2217-portal | Web UI, HTTP API, proxy supervisor, hotplug handler, WiFi API |
| wifi_controller.py | /usr/local/bin/wifi_controller.py | WiFi instrument backend (AP, STA, scan, relay, events) |
| plain_rfc2217_server.py | /usr/local/bin/plain_rfc2217_server.py | RFC2217 server with direct DTR/RTS passthrough (all devices) |
| ~~esp_rfc2217_server.py~~ | removed | Deprecated — breaks C3 native USB and classic ESP32 over RFC2217 |
| ~~serial_proxy.py~~ | removed | Deprecated — replaced by plain_rfc2217_server.py |
| rfc2217-udev-notify.sh | /usr/local/bin/rfc2217-udev-notify.sh | Posts udev events to portal API |
| wifi-lease-notify.sh | /usr/local/bin/wifi-lease-notify.sh | Posts dnsmasq DHCP lease events to portal API |
| rfc2217-learn-slots | /usr/local/bin/rfc2217-learn-slots | Slot configuration helper |
| 99-rfc2217-hotplug.rules | /etc/udev/rules.d/ | udev rules for hotplug |
| slots.json | /etc/rfc2217/slots.json | Slot-to-port mapping |
| wifi_tester_driver.py | pytest/ | HTTP test driver for the WiFi instrument |
| conftest.py | pytest/ | Pytest fixtures and CLI options |
| test_instrument.py | pytest/ | WiFi tester self-tests (WT-xxx) |

### 1.6 State Model

The system provides two independent services — Serial and WiFi — each with
its own state machine.  Serial operates per slot; WiFi operates on wlan0.

**Serial Service (per slot):**

| State | Description |
|-------|-------------|
| Absent | No USB device in this slot |
| Idle | Device present, proxy running, no active operation |
| Flashing | External tool (esptool) using RFC2217 proxy — reset/monitor blocked |
| Resetting | DTR/RTS reset in progress — proxy stopped, direct serial in use |
| Monitoring | Reading serial output for pattern matching |
| Flapping | USB connect/disconnect cycling detected — needs recovery |

State transitions:

| From | To | Trigger |
|------|----|---------|
| Absent | Idle | Hotplug add + proxy start |
| Idle | Absent | Hotplug remove |
| Idle | Flashing | External RFC2217 client connects (esptool) |
| Flashing | Idle | Client disconnects, proxy restarts via hotplug |
| Idle | Resetting | `POST /api/serial/reset` — stops proxy, opens direct serial, sends DTR/RTS |
| Resetting | Idle | Reset complete, proxy restarts via hotplug |
| Idle | Monitoring | `POST /api/serial/monitor` — reads serial via RFC2217 (non-exclusive) |
| Monitoring | Idle | Pattern matched or timeout expired |
| Idle | Flapping | 6+ hotplug events in 30s |
| Flapping | Idle | Recovery reset succeeds or cooldown expires |

**WiFi Service (wlan0):**

| State | Description |
|-------|-------------|
| Idle | wlan0 not in use for testing |
| Captive | wlan0 joined DUT's portal AP as STA (Pi at 192.168.4.x, DUT at 192.168.4.1) |
| AP | wlan0 running test AP (Pi at 192.168.4.1, DUT connects at 192.168.4.x) |

State transitions:

| From | To | Trigger |
|------|----|---------|
| Idle | Captive | `POST /api/wifi/sta_join` to DUT's captive portal AP |
| Captive | Idle | `POST /api/wifi/sta_leave` |
| Idle | AP | `POST /api/wifi/ap_start` |
| Captive | AP | `POST /api/wifi/ap_start` (stops STA, starts AP) |
| AP | Idle | `POST /api/wifi/ap_stop` |
| AP | Captive | `POST /api/wifi/sta_join` (stops AP, joins network) |

**Note:** Serial-interface mode (wlan0 for LAN) is a separate operating mode
that disables the WiFi test service entirely (see §1.4).

---

## 2. Definitions

| Entity | Description |
|--------|-------------|
| **Slot** | One physical connector position on the USB hub |
| **slot_key** | Stable identifier for physical port topology (derived from udev `ID_PATH`) |
| **devnode** | Current tty device path (e.g., `/dev/ttyACM0`) — may change on reconnect |
| **proxy** | RFC2217 server process for a serial device: `plain_rfc2217_server.py` for all devices (direct DTR/RTS passthrough) |
| **seq** (sequence) | Global monotonically increasing counter, incremented on every hotplug event |
| **Mode** | Operating mode: `wifi-testing` (wlan0 = instrument) or `serial-interface` (wlan0 = LAN) |

### Key Principle: Slot-Based Identity

The system keys on physical connector position, NOT on `/dev/ttyACMx`
(changes on reconnect), serial number (two identical boards would conflict),
or VID/PID (not unique).

`slot_key` = udev `ID_PATH` ensures:
- Same physical connector → same TCP port (always)
- Device can be swapped → same TCP port
- Two identical boards → different TCP ports (different slots)

---

## 3. Serial Interface

### FR-001 — Event-Driven Hotplug

**Plug flow:**
1. udev emits `add` event for the serial device
2. udev rule invokes `rfc2217-udev-notify.sh` via `systemd-run --no-block`
3. Notify script sends `POST /api/hotplug` with `{action, devnode, id_path, devpath}`
4. Portal determines `slot_key` from `id_path` (or `devpath` fallback)
5. Portal increments global `seq_counter`, records event metadata on the slot
6. Portal spawns a background thread that acquires the slot lock, waits for the device to settle, then starts the proxy bound to `devnode` on the configured TCP port
7. Slot state becomes `running=true`, `present=true`

**Unplug flow:**
1. udev emits `remove` event
2–4. Same notification path as plug
5. Portal increments `seq_counter`, records metadata
6. Portal stops the proxy process in a **background thread** (non-blocking,
   so the single-threaded HTTP server can immediately process the subsequent
   `add` event from USB re-enumeration)
7. Slot state becomes `running=false`, `present=false`

**USB re-enumeration (esptool reset/flash):**
When esptool performs a watchdog reset or flash operation, the ESP32-C3's
USB-Serial/JTAG controller disconnects and reconnects.  This triggers a
`remove` → `add` hotplug sequence.  The portal handles this automatically:
the proxy is stopped on `remove` and restarted on `add` (with the 2s
ttyACM boot delay).  No manual intervention is required.

**Boot scan:** On startup, portal scans `/dev/ttyACM*` and `/dev/ttyUSB*`,
queries `udevadm info` for each, and starts proxies for any device matching a
configured slot.

### FR-002 — Slot Configuration

Static configuration maps `slot_key` → `{label, tcp_port}`.

Configuration file: `/etc/rfc2217/slots.json`

```json
{
  "slots": [
    {"label": "SLOT1", "slot_key": "platform-3f980000.usb-usb-0:1.1:1.0", "tcp_port": 4001},
    {"label": "SLOT2", "slot_key": "platform-3f980000.usb-usb-0:1.3:1.0", "tcp_port": 4002},
    {"label": "SLOT3", "slot_key": "platform-3f980000.usb-usb-0:1.4:1.0", "tcp_port": 4003}
  ]
}
```

### FR-003 — Serial API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /api/devices | List all slots with status |
| POST | /api/hotplug | Receive udev hotplug event (add/remove) |
| POST | /api/start | Manually start proxy for a slot |
| POST | /api/stop | Manually stop proxy for a slot |
| GET | /api/info | Pi IP, hostname, slot counts |
| POST | /api/serial/reset | Reset device via DTR/RTS (FR-008) |
| POST | /api/serial/monitor | Read serial output with pattern match (FR-009) |

**GET /api/devices** returns:

```json
{
  "slots": [
    {
      "label": "SLOT1",
      "slot_key": "platform-...-usb-0:1.1:1.0",
      "tcp_port": 4001,
      "present": true,
      "running": true,
      "devnode": "/dev/ttyACM0",
      "pid": 1234,
      "url": "rfc2217://192.168.0.87:4001",
      "seq": 5,
      "last_action": "add",
      "last_event_ts": "2026-02-05T12:34:56+00:00",
      "last_error": null,
      "flapping": false,
      "state": "idle"
    }
  ],
  "host_ip": "192.168.0.87",
  "hostname": "192.168.0.87"
}
```

**POST /api/hotplug** body: `{action, devnode, id_path, devpath}`.

**POST /api/start** body: `{slot_key, devnode}`.

**POST /api/stop** body: `{slot_key}`.

### FR-004 — Serial Traffic Logging

- Removed.  `serial_proxy.py` (which provided traffic logging) has been
  deprecated in favour of `plain_rfc2217_server.py`.
- Serial traffic is observable via RFC2217 clients (e.g. pyserial).

### FR-005 — Web Portal (Serial Section)

- Display all 3 slots (always visible, even if empty)
- Show slot status: RUNNING / PRESENT / EMPTY
- Show current devnode and PID when running
- Copy RFC2217 URL to clipboard (hostname and IP variants)
- Start/stop individual slots
- Display connection examples

### FR-006 — ESP32-C3 Native USB-Serial/JTAG Support

ESP32-C3 (and ESP32-S3) chips with native USB use a built-in USB-Serial/JTAG
controller that maps to `/dev/ttyACM*` on Linux (CDC ACM class).  This differs
fundamentally from UART bridge chips (CP2102, CH340 → `/dev/ttyUSB*`) in how
DTR/RTS signals are interpreted.

#### 6.1 USB-Serial/JTAG Signal Mapping

| Signal | GPIO | Function |
|--------|------|----------|
| DTR | GPIO9 | Boot strap: DTR=1 → GPIO9 LOW → **download mode** |
| RTS | CHIP_EN | Reset: RTS=1 → chip held in **reset** |

The Linux `cdc_acm` kernel driver asserts **both DTR=1 and RTS=1** in
`acm_port_activate()` on every port open.  This puts the chip into download
mode during the boot-sensitive phase.

#### 6.2 Proxy Selection

The portal uses `plain_rfc2217_server.py` for **all** device types:

| devnode | Device Type | Server |
|---------|-------------|--------|
| `/dev/ttyACM*` | Native USB (CDC ACM) | `plain_rfc2217_server.py` |
| `/dev/ttyUSB*` | UART bridge (CP2102/CH340) | `plain_rfc2217_server.py` |

**Why not `esp_rfc2217_server.py`?**  Espressif's `EspPortManager` intercepts
DTR/RTS and replaces them with its own reset sequence (`ClassicReset` /
`HardReset`) in a separate thread.  This breaks ESP32-C3 native USB, and
testing confirmed it also fails for classic ESP32 UART bridges over RFC2217.
`plain_rfc2217_server.py` passes DTR/RTS directly — esptool on the client
side already implements the correct reset sequences for each chip type.

#### 6.3 Controlled Boot Sequence (plain_rfc2217_server.py)

When `plain_rfc2217_server.py` opens the serial port, it performs a controlled
boot sequence to ensure the chip boots in SPI mode (not download mode):

```python
ser = serial.serial_for_url(port, do_not_open=True, exclusive=False)
ser.timeout = 3
ser.dtr = False   # Pre-set: GPIO9 HIGH (SPI boot)
ser.rts = False   # Pre-set: not in reset
ser.open()
# Linux cdc_acm still asserts DTR+RTS on open, but pyserial immediately
# applies the pre-set values in _reconfigure_port()

# Clear HUPCL to prevent DTR assertion on close
attrs = termios.tcgetattr(ser.fd)
attrs[2] &= ~termios.HUPCL
termios.tcsetattr(ser.fd, termios.TCSANOW, attrs)

ser.dtr = False   # GPIO9 HIGH — select SPI boot
time.sleep(0.1)   # Let USB-JTAG controller latch DTR=0
ser.rts = False   # Release reset — chip boots normally
time.sleep(0.1)
```

#### 6.4 Device Settle Check (ttyACM)

For ttyACM devices, `wait_for_device()` checks only that the device node
exists — it does **not** call `os.open()`, because opening the port would
assert DTR/RTS and put the chip into download mode:

```python
def wait_for_device(devnode, timeout=5.0):
    is_native_usb = devnode and "ttyACM" in devnode
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(devnode):
            if is_native_usb:
                return True  # Don't open — avoids DTR reset
            # ttyUSB: probe with open as before
            try:
                fd = os.open(devnode, os.O_RDWR | os.O_NONBLOCK)
                os.close(fd)
                return True
            except OSError:
                pass
        time.sleep(0.1)
    return False
```

#### 6.5 Hotplug Boot Delay (ttyACM)

When a ttyACM device is hotplugged (USB re-enumeration after reset/flash),
the portal delays proxy startup by `NATIVE_USB_BOOT_DELAY_S` (2 seconds)
to allow the chip to boot past the download-mode-sensitive phase before the
proxy opens the serial port:

```python
NATIVE_USB_BOOT_DELAY_S = 2

def _bg_start(s=slot, lk=lock, dn=devnode):
    if dn and "ttyACM" in dn:
        time.sleep(NATIVE_USB_BOOT_DELAY_S)
    with lk:
        # ... start proxy
```

#### 6.6 Reset Types (Core vs System)

| Reset Type | Mechanism | Re-samples GPIO9? | Result on USB-Serial/JTAG |
|------------|-----------|-------------------|---------------------------|
| Core reset | RTS toggle (DTR/RTS sequence) | **No** | Stays in current boot mode |
| System reset | Watchdog timer (RTC WDT) | **Yes** | Boots based on physical pin state |

**Critical:** After entering download mode, only a **system reset** (watchdog)
can return the chip to SPI boot mode.  Core reset (RTS toggle) keeps the chip
in download mode because GPIO9 is not re-sampled.

#### 6.7 Flashing via RFC2217

Flashing works via RFC2217 through `plain_rfc2217_server` for all device
types.  No SSH to the Pi is needed — esptool's DTR/RTS sequences pass
through directly.

**ESP32-C3 (native USB, ttyACM):**

```bash
python3 -m esptool --chip esp32c3 \
  --port "rfc2217://192.168.0.87:4001" \
  --before=usb-reset --after=watchdog-reset \
  write_flash 0x10000 firmware.bin
```

**Classic ESP32 (UART bridge, ttyUSB):**

```bash
python3 -m esptool --chip esp32 \
  --port "rfc2217://192.168.0.87:4001" \
  --before=default-reset --after=hard-reset \
  write_flash 0x10000 firmware.bin
```

**Key esptool flags by device type:**

| Device | `--before` | `--after` |
|--------|-----------|----------|
| ESP32-C3 (ttyACM) | `usb-reset` | `watchdog-reset` |
| ESP32 (ttyUSB) | `default-reset` | `hard-reset` |

**Note:** A harmless RFC2217 parameter negotiation error may appear at the
end of flashing — the flash and reset still complete successfully.

#### 6.8 RFC2217 Client Best Practices (ttyACM)

When connecting to an ESP32-C3 via RFC2217, the client must prevent DTR
assertion during connection negotiation:

```python
ser = serial.serial_for_url('rfc2217://192.168.0.87:4001', do_not_open=True)
ser.baudrate = 115200
ser.timeout = 2
ser.dtr = False   # CRITICAL: prevents download mode
ser.rts = False   # CRITICAL: prevents reset
ser.open()
```

**Never** use `serial.Serial('rfc2217://...')` directly — it opens the port
immediately and the RFC2217 negotiation may toggle DTR/RTS.

### FR-008 — Serial Reset

Reset a device via DTR/RTS signals, providing a clean boot cycle without
requiring SSH access to the Pi.

**Endpoint:** `POST /api/serial/reset`

**Request body:**
```json
{"slot": "SLOT2"}
```

**Procedure:**
1. Stop the RFC2217 proxy for the slot
2. Open direct serial (`/dev/ttyACMx`) with `dtr=False, rts=False`
3. Send DTR/RTS reset pulse: DTR=1, RTS=1 for 50ms, then release both
4. Wait for device to boot — read serial until first output line or 5s timeout
5. Close serial connection
6. Wait `NATIVE_USB_BOOT_DELAY_S` (2s), then restart the proxy (DTR/RTS reset
   does not cause USB re-enumeration, so hotplug won't restart it automatically)

**Response:**
```json
{"ok": true, "output": ["ESP-ROM:esp32c3-api1-20210207", "Boot count: 1"]}
```

**Error:** Returns `{"ok": false, "error": "..."}` if slot not found, device
not present, or serial open fails.

**Used by:** enter-portal (§4), flapping recovery (FR-007), integration tests

### FR-009 — Serial Monitor

Read serial output from a device, optionally waiting for a pattern match.
Uses the RFC2217 proxy (non-exclusive) so the proxy stays running.

**Endpoint:** `POST /api/serial/monitor`

**Request body:**
```json
{"slot": "SLOT2", "pattern": "Boot count", "timeout": 10}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| slot | string | Yes | — | Slot label (e.g. "SLOT2") |
| pattern | string | No | null | Substring to match in serial output |
| timeout | number | No | 10 | Max seconds to wait |

**Procedure:**
1. Connect to the slot's RFC2217 proxy (non-exclusive read)
2. Read serial lines until pattern is matched or timeout expires
3. Return all captured output and match result

**Response (pattern matched):**
```json
{"ok": true, "matched": true, "line": "Boot count: 1", "output": ["ESP-ROM:...", "Boot count: 1"]}
```

**Response (timeout, no pattern):**
```json
{"ok": true, "matched": false, "line": null, "output": ["line1", "line2"]}
```

**Used by:** enter-portal (§4), flapping recovery (FR-007), test verification

### FR-007 — USB Flap Detection

When a device enters a boot loop (crash → reboot → crash every ~2-3s), the
Pi sees rapid USB connect/disconnect cycles.  Without protection, the portal
spawns a new proxy thread for every "add" event, overwhelming the system.

#### 7.1 Detection

```python
FLAP_WINDOW_S = 30       # Look at events within this window
FLAP_THRESHOLD = 6       # 6 events in 30s = 3 connect/disconnect cycles
FLAP_COOLDOWN_S = 30     # Wait 30s of quiet before retrying
```

Each slot tracks `_event_times[]` — timestamps of recent hotplug events.
When the count within the window exceeds the threshold, the slot enters
`flapping=true` state.

#### 7.2 Suppression

While `flapping=true`:
- Proxy starts are **suppressed** (no new processes spawned)
- Running proxy is **stopped** (it would die on next disconnect anyway)
- `last_error` is set to describe the flapping condition
- `flapping` field is exposed in `/api/devices` JSON

#### 7.3 Recovery

When flapping is detected, the portal attempts active recovery using
serial reset (FR-008) and serial monitor (FR-009):

1. Wait for device to be present (next hotplug `add` event)
2. Call serial reset (`POST /api/serial/reset`) — stops proxy, sends
   DTR/RTS pulse, reads initial boot output
3. Call serial monitor (`POST /api/serial/monitor`) — watch for normal
   boot indicators (e.g. application startup message)
4. If device boots normally → clear flapping flag, proxy restarts via
   hotplug re-enumeration
5. If boot loop continues (device disconnects again within cooldown) →
   re-enter flapping state, log error

**Fallback:** If no hotplug event arrives within `FLAP_COOLDOWN_S` (30s),
the flapping flag is cleared passively and normal proxy startup resumes
on the next hotplug add.

#### 7.4 Web UI

Flapping slots display a red "FLAPPING" status badge and a warning message:
> Device is boot-looping (rapid USB connect/disconnect). Proxy start suppressed
> until device stabilises.

Other slots are unaffected and continue operating normally.

---

## 4. WiFi Service

### FR-010 — API Summary

Complete API for both Serial and WiFi services.  WiFi tester endpoints (all
except `/api/wifi/mode` and `/api/wifi/ping`) return `{"ok": false, "error":
"WiFi testing disabled (Serial Interface mode)"}` when the system is in
serial-interface mode.

| Method | Endpoint | Description |
|--------|----------|-------------|
| **Serial** | | |
| GET | /api/devices | List all slots with status |
| POST | /api/hotplug | Receive udev hotplug event (add/remove) |
| POST | /api/start | Manually start proxy for a slot |
| POST | /api/stop | Manually stop proxy for a slot |
| GET | /api/info | Pi IP, hostname, slot counts |
| POST | /api/serial/reset | Reset device via DTR/RTS (FR-008) |
| POST | /api/serial/monitor | Read serial output with pattern match (FR-009) |
| **WiFi** | | |
| GET | /api/wifi/ping | Version and uptime |
| GET | /api/wifi/mode | Current operating mode |
| POST | /api/wifi/mode | Switch operating mode |
| POST | /api/wifi/ap_start | Start SoftAP (WiFi state → AP) |
| POST | /api/wifi/ap_stop | Stop SoftAP (WiFi state → Idle) |
| GET | /api/wifi/ap_status | AP status, SSID, channel, stations |
| POST | /api/wifi/sta_join | Join WiFi network as station (WiFi state → Captive) |
| POST | /api/wifi/sta_leave | Disconnect from WiFi network (WiFi state → Idle) |
| GET | /api/wifi/scan | Scan for WiFi networks |
| POST | /api/wifi/http | HTTP relay through Pi's radio |
| GET | /api/wifi/events | Event queue (long-poll supported) |
| POST | /api/wifi/lease_event | Receive dnsmasq lease callback |
| **Composite** | | |
| GET | /api/log | Activity log (timestamped entries, filterable with `?since=`) |
| POST | /api/enter-portal | Trigger DUT captive portal via serial reset/monitor sequence |

#### Enter-Portal Composite Operation

`POST /api/enter-portal` is a composite operation built on serial reset
(FR-008) and serial monitor (FR-009).  It forces a DUT into captive portal
mode by performing rapid reboots until the boot counter exceeds the portal
threshold.

**Request body:**
```json
{"slot": "SLOT2"}
```

**Procedure (runs in background thread):**
1. `serial.reset(slot)` — clean boot the device
2. `serial.monitor(slot, "Boot count reset to 0")` — confirm NVS was reset
   or normal boot
3. Loop N times (N = portal threshold, typically 3):
   a. `serial.reset(slot)` — reboot
   b. `serial.monitor(slot, "Boot count:")` — confirm boot count incremented
4. `serial.monitor(slot, "PORTAL mode")` — confirm device entered captive
   portal mode

Each step is logged to the activity log.  Progress is observable via
`GET /api/log?since=<ts>`.

**Response:** `{"ok": true}` (operation runs asynchronously; monitor log for
progress)

### FR-011 — AP Mode

The Pi's wlan0 runs hostapd + dnsmasq to create a SoftAP:

- **SSID/password/channel** configurable per `POST /api/wifi/ap_start`
- **IP addressing:** AP IP is `192.168.4.1/24`
- **DHCP range:** `192.168.4.2` – `192.168.4.20`, 1-hour leases
- **Station tracking:** dnsmasq calls `wifi-lease-notify.sh` on DHCP events
  (add/old/del), which posts to `POST /api/wifi/lease_event`.  The portal
  maintains an in-memory station table `{mac, ip}` and emits STA_CONNECT /
  STA_DISCONNECT events.
- **AP status** (`GET /api/wifi/ap_status`): returns `{active, ssid, channel, stations[]}`
- Starting AP while AP is already running restarts with new configuration
- AP and STA are mutually exclusive — starting one stops the other

### FR-012 — Captive Mode (STA)

Join an external WiFi network (typically a DUT's captive portal AP) using
wpa_supplicant + DHCP:

- `POST /api/wifi/sta_join` with `{ssid, pass, timeout}`
- Portal writes wpa_supplicant.conf (with `ctrl_interface=` prepended for
  `wpa_cli` compatibility), starts wpa_supplicant, polls `wpa_cli status`
  until `wpa_state=COMPLETED`, then obtains IP via `dhcpcd -1 -4` (or
  `dhclient`/`udhcpc` fallback)
- Stale wpa_supplicant control sockets (`/var/run/wpa_supplicant/wlan0`) are
  cleaned up before each start to prevent "ctrl_iface exists" errors
- Returns `{ip, gateway}` on success; raises error on timeout or no IP
- `POST /api/wifi/sta_leave` disconnects and releases DHCP
- STA and AP are mutually exclusive — starting STA stops the AP

### FR-013 — WiFi Scan

- `GET /api/wifi/scan` uses `iw dev wlan0 scan -u`
- Returns `{networks: [{ssid, rssi, auth}, ...]}` sorted by signal strength
- `auth` is one of: `OPEN`, `WPA`, `WPA2`, `WEP`
- Scan works while AP is running (the AP's own SSID is excluded from results)

### FR-014 — HTTP Relay

Proxy HTTP requests through the Pi's radio so tests can reach devices on the
WiFi side of the network:

- `POST /api/wifi/http` with `{method, url, headers, body, timeout}`
- Request body is base64-encoded; response body is returned base64-encoded
- Returns `{status, headers, body}`
- Works in both AP mode (reaching devices at 192.168.4.x) and STA mode
  (reaching the external network)

### FR-015 — Event System

- Events: `STA_CONNECT` (mac, ip, hostname) and `STA_DISCONNECT` (mac)
- `GET /api/wifi/events` drains the event queue
- Long-poll: `GET /api/wifi/events?timeout=N` blocks up to N seconds if queue
  is empty, returning immediately when an event arrives

### FR-016 — Mode Switching

- `POST /api/wifi/mode` with `{mode, ssid?, pass?}`
- Switching to `serial-interface` requires `ssid` (and optional `pass`);
  stops any active AP/STA, then joins the specified WiFi network via
  wpa_supplicant + DHCP on wlan0
- Switching to `wifi-testing` disconnects wlan0 from WiFi, returns wlan0 to
  instrument duty
- Mode switch failure (e.g., can't join WiFi) reverts to `wifi-testing`
- `GET /api/wifi/mode` returns `{mode}` (and `ssid`, `ip` when in
  serial-interface mode)
- While in serial-interface mode, tester endpoints (`ap_start`, `ap_stop`,
  `sta_join`, `sta_leave`, `scan`, `http`) return a guard error

---

## 5. Web Portal

The portal serves a single-page HTML UI at `GET /` (port 8080):

- **Serial slot cards** — one card per configured slot showing label, status
  badge (RUNNING/PRESENT/EMPTY), devnode, PID, and copyable RFC2217 URL
- **WiFi Tester section** — mode toggle (WiFi-Testing / Serial Interface),
  AP status (SSID, channel, station count), and mode-specific information
- **Mode toggle** — clicking "Serial Interface" prompts for SSID/password;
  clicking "WiFi-Testing" switches back immediately
- **Activity Log** — scrollable log panel showing timestamped entries for
  hotplug events, WiFi tester operations (sta_join, sta_leave, scan, HTTP
  relay), and enter-portal sequence steps.  Entries are categorised (info,
  ok, error, step) with colour coding.  "Enter Captive Portal" button
  triggers `POST /api/enter-portal` to run rapid-reset sequence on a
  selected slot.  "Clear" button resets the display.  Log is polled every
  2 seconds via `GET /api/log?since=<last_ts>`.
- **Auto-refresh** — every 2 seconds via `setInterval`, fetches
  `/api/devices`, `/api/wifi/mode`, `/api/wifi/ap_status`, and `/api/log`
- **Title** — shows `{hostname} — Serial Portal` when hostname is available

---

## 6. Non-Functional Requirements

### 6.1 Must Tolerate

| Scenario | How Handled |
|----------|-------------|
| `/dev/ttyACM0` → `/dev/ttyACM1` renaming | slot_key unchanged (based on physical port) |
| Duplicate udev events | API idempotency, per-slot locking |
| "Remove after add" races (USB reset) | Per-slot locking serializes operations; sequence counter aids diagnostics |
| Two identical boards | Different slot_keys (different physical connectors) |
| Hub/Pi reboot | Static config preserves port assignments; boot scan starts proxies |

### 6.2 Determinism

- Same physical connector → same TCP port (always)
- Configuration survives reboots
- No dynamic port assignment

### 6.3 Reliability

- Portal API must be idempotent
- Actions serialized per slot (threading.Lock)
- Stale events prevented via per-slot locking; sequence counter for observability

### 6.4 WiFi Mutual Exclusivity

- AP and STA are mutually exclusive — starting one stops the other
- Mode guard prevents tester endpoints from running in serial-interface mode;
  guarded endpoints return HTTP 200 with `{"ok": false, "error": "WiFi testing
  disabled (Serial Interface mode)"}`

### 6.5 Edge Cases

| Case | Behavior |
|------|----------|
| Two identical boards | Works — different slot_keys (different physical connectors) |
| Device re-enumeration (USB reset) | Per-slot locking serializes add/remove; background thread restart is safe |
| Duplicate events | Idempotency prevents flapping |
| Unknown slot_key | Portal tracks the slot (present, seq) but does not start a proxy; logged for diagnostics |
| Hub topology changed | Must re-learn slots and update config |
| Device not ready | Settle checks with timeout, then fail with `last_error` |
| ttyACM DTR trap | `wait_for_device()` skips `os.open()` for ttyACM; proxy uses controlled boot sequence (FR-006) |
| Boot loop (USB flapping) | Flap detection suppresses proxy restarts; clears after cooldown (FR-007) |
| ESP32-C3 stuck in download mode | Run esptool on Pi with `--after=watchdog-reset` to trigger system reset (FR-006.6) |
| udev PrivateNetwork blocking curl | udev runs RUN+ handlers in a network-isolated sandbox (`PrivateNetwork=yes`). Direct `curl` to localhost silently fails. Fix: wrap the notify script with `systemd-run --no-block` in the udev rule so it runs outside the sandbox. |

---

## 7. Test Cases

### 7.1 Serial Tests

| ID | Name | Pass Criteria |
|----|------|---------------|
| TC-001 | Plug into SLOT3 | SLOT3 shows `running=true`, `devnode` set, `tcp_port=4003` within 5 s |
| TC-002 | Unplug from SLOT3 | SLOT3 shows `running=false`, `devnode=null` within 2 s |
| TC-003 | Replug into SLOT3 | SLOT3 `running=true`, same `tcp_port=4003`, devnode may differ |
| TC-004 | Two identical boards | Both running on different TCP ports (4001, 4002) |
| TC-005 | USB reset race | No "stuck stopped" state; per-slot locking serializes events |
| TC-006 | Devnode renaming | Original device still on SLOT1's port (4001) after renumbering |
| TC-007 | Boot persistence | Same slots get same ports after reboot |
| TC-008 | Unknown slot | Portal logs "unknown slot_key", no crash |

### 7.2 WiFi Tester Tests

Tests are implemented in `pytest/test_instrument.py` and run via:
```
pytest test_instrument.py --wt-url http://<pi-ip>:8080
```

Add `--run-dut` to include tests that require a WiFi device under test.

| ID | Name | Category | Requires DUT |
|----|------|----------|:------------:|
| WT-100 | Ping response | Basic Protocol | No |
| WT-104 | Rapid commands | Basic Protocol | No |
| WT-200 | Start AP | SoftAP | No |
| WT-201 | Start open AP | SoftAP | No |
| WT-202 | Stop AP | SoftAP | No |
| WT-203 | Stop when not running | SoftAP | No |
| WT-204 | Restart AP new config | SoftAP | No |
| WT-205 | AP status when running | SoftAP | No |
| WT-206 | AP status when stopped | SoftAP | No |
| WT-207 | Max SSID length (32) | SoftAP | No |
| WT-208 | Channel selection | SoftAP | No |
| WT-300 | Station connect event | Station Events | Yes |
| WT-301 | Station disconnect event | Station Events | Yes |
| WT-302 | Station in AP status | Station Events | Yes |
| WT-303 | IP matches event | Station Events | Yes |
| WT-400 | Join open network | STA Mode | Yes |
| WT-401 | Join WPA2 network | STA Mode | Yes |
| WT-402 | Wrong password | STA Mode | Yes |
| WT-403 | Nonexistent SSID | STA Mode | No |
| WT-404 | Leave STA | STA Mode | Yes |
| WT-405 | AP stops during STA | STA Mode | Yes |
| WT-500 | GET request | HTTP Relay | Yes |
| WT-501 | POST with body | HTTP Relay | Yes |
| WT-502 | Custom headers | HTTP Relay | Yes |
| WT-503 | Connection refused | HTTP Relay | No* |
| WT-504 | Request timeout | HTTP Relay | No* |
| WT-505 | Large response | HTTP Relay | Yes |
| WT-506 | HTTP via STA mode | HTTP Relay | Yes |
| WT-600 | Scan finds networks | WiFi Scan | No |
| WT-601 | Scan returns fields | WiFi Scan | No |
| WT-602 | Own AP excluded | WiFi Scan | No |
| WT-603 | Scan while AP running | WiFi Scan | No |

\* WT-503/504 require a running AP (wifi_network fixture) but not a physical DUT.

---

## 8. Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-05 | Claude | Initial FSD (serial only) |
| 1.1 | 2026-02-05 | Claude | Implemented serial-based port assignment |
| 1.2 | 2026-02-05 | Claude | Testing complete for serial-based approach |
| 2.0 | 2026-02-05 | Claude | Major rewrite: event-driven slot-based architecture |
| 3.0 | 2026-02-05 | Claude | Portal v3: direct hotplug handling, in-memory seq + locking, systemd-run udev |
| 4.0 | 2026-02-07 | Claude | WiFi Tester integration: combined Serial + WiFi FSD, two operating modes, appendices for technical details |
| 5.0 | 2026-02-07 | Claude | ESP32-C3 native USB support: FR-006 (ttyACM handling, plain RFC2217 server, controlled boot sequence, USB reset types, flashing via SSH), FR-007 (USB flap detection), updated edge cases and device settle checks |
| 5.1 | 2026-02-08 | Claude | plain_rfc2217_server for ALL devices (ttyACM and ttyUSB); esp_rfc2217_server deprecated; flashing via RFC2217 works for both chip types (no SSH needed); updated proxy selection, flashing docs, deliverables |
| 5.3 | 2026-02-08 | Claude | Activity log system (`GET /api/log`, `POST /api/enter-portal` for captive portal trigger via rapid resets); WiFi tester fixes (stale wpa_supplicant socket cleanup, `ctrl_interface=` in wpa_passphrase output, `dhcpcd` DHCP client support); activity logging for hotplug events and WiFi tester operations; activity log UI panel with colour-coded entries |
| 5.2 | 2026-02-08 | Claude | Removed esp_rfc2217_server.py and serial_proxy.py (no longer installed); proxy auto-restart after esptool USB re-enumeration (background stop_proxy, BrokenPipeError fix, curl timeout 10s); FR-004 logging removed; updated deliverables |
| 6.0 | 2026-02-08 | Claude | Service separation — Serial and WiFi as independent services with state models (§1.6); serial reset (FR-008) and serial monitor (FR-009) as first-class API operations; flapping recovery via active reset; WiFi section renamed to WiFi Service with states Idle/Captive/AP; enter-portal rewritten as composite serial operation; consolidated API table (FR-010) |

---

## Appendix A: Technical Details

### A.1 Slot Key Derivation

```python
def get_slot_key(udev_env):
    """Derive slot_key from udev environment variables."""
    # Preferred: ID_PATH (stable across reboots)
    if 'ID_PATH' in udev_env and udev_env['ID_PATH']:
        return udev_env['ID_PATH']

    # Fallback: DEVPATH (less stable but usable)
    if 'DEVPATH' in udev_env:
        return udev_env['DEVPATH']

    raise ValueError("Cannot determine slot_key: no ID_PATH or DEVPATH")
```

### A.2 Sequence Counter

The portal owns a single global monotonic `seq_counter` in memory (no files
on disk).  Every hotplug event increments the counter and stamps the affected
slot:

```python
# Module-level state (in portal.py)
seq_counter: int = 0

# Inside _handle_hotplug:
seq_counter += 1
slot["seq"] = seq_counter
slot["last_action"] = action       # "add" or "remove"
slot["last_event_ts"] = datetime.now(timezone.utc).isoformat()
```

The sequence number provides a total ordering of events for diagnostics.
Because the portal processes hotplug requests serially per slot (via per-slot
locks), stale-event races are prevented by locking rather than by comparing
counters.

### A.3 API Idempotency

**POST /api/start semantics:**
- If slot running with same devnode: return OK (no restart)
- If slot running with different devnode: restart cleanly
- If slot not running: start
- Never fails if already in desired state

**POST /api/stop semantics:**
- If slot not running: return OK
- If running: stop
- Never fails if already in desired state

### A.4 Per-Slot Locking

Portal serializes operations per slot using in-memory `threading.Lock` objects:

```python
# Each slot dict holds its own lock (created at config load time)
slot["_lock"] = threading.Lock()

# Usage (e.g., inside hotplug add handler):
with slot["_lock"]:
    stop_proxy(slot)   # stop old proxy if running
    start_proxy(slot)  # start new proxy
```

No file-based locks or `/run/rfc2217/locks/` directory is used.

### A.5 Device Settle Checks

The portal's `start_proxy` function performs settle checks inline (no separate
handler).  It polls the device node before launching the proxy:

```python
def wait_for_device(devnode, timeout=5.0):
    """Wait for device to be usable (called inside portal)."""
    is_native_usb = devnode and "ttyACM" in devnode
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(devnode):
            if is_native_usb:
                return True  # Don't open — avoids DTR reset (see FR-006)
            try:
                fd = os.open(devnode, os.O_RDWR | os.O_NONBLOCK)
                os.close(fd)
                return True
            except OSError:
                pass
        time.sleep(0.1)
    return False
```

**ttyACM devices:** Only checks file existence — `os.open()` is skipped
because the Linux `cdc_acm` driver asserts DTR+RTS on open, which puts
ESP32-C3 native USB devices into download mode (see FR-006.4).

**ttyUSB devices:** Probes with `os.open()` as before — UART bridge chips
are not affected by DTR on open.

If the device does not settle within the timeout, the slot's `last_error` is
set and the proxy is not started.

### A.6 udev Rules

```
# /etc/udev/rules.d/99-rfc2217-hotplug.rules
# Notify portal of USB serial add/remove events.
# systemd-run escapes udev's PrivateNetwork sandbox so curl can reach localhost.

ACTION=="add", SUBSYSTEM=="tty", KERNEL=="ttyACM*", RUN+="/usr/bin/systemd-run --no-block /usr/local/bin/rfc2217-udev-notify.sh %E{ACTION} %E{DEVNAME} %E{ID_PATH} %E{DEVPATH}"
ACTION=="remove", SUBSYSTEM=="tty", KERNEL=="ttyACM*", RUN+="/usr/bin/systemd-run --no-block /usr/local/bin/rfc2217-udev-notify.sh %E{ACTION} %E{DEVNAME} %E{ID_PATH} %E{DEVPATH}"
ACTION=="add", SUBSYSTEM=="tty", KERNEL=="ttyUSB*", RUN+="/usr/bin/systemd-run --no-block /usr/local/bin/rfc2217-udev-notify.sh %E{ACTION} %E{DEVNAME} %E{ID_PATH} %E{DEVPATH}"
ACTION=="remove", SUBSYSTEM=="tty", KERNEL=="ttyUSB*", RUN+="/usr/bin/systemd-run --no-block /usr/local/bin/rfc2217-udev-notify.sh %E{ACTION} %E{DEVNAME} %E{ID_PATH} %E{DEVPATH}"
```

The udev notify script posts a JSON payload to the portal:

```bash
#!/bin/bash
# /usr/local/bin/rfc2217-udev-notify.sh
# Args: ACTION DEVNAME ID_PATH DEVPATH

curl -m 10 -s -X POST http://127.0.0.1:8080/api/hotplug \
  -H 'Content-Type: application/json' \
  -d "{\"action\":\"$1\",\"devnode\":\"$2\",\"id_path\":\"${3:-}\",\"devpath\":\"$4\"}" \
  || true
```

### A.7 WiFi Lease Notify Script

dnsmasq calls this script on DHCP lease events (add/old/del):

```bash
#!/bin/sh
# /usr/local/bin/wifi-lease-notify.sh
# Args: ACTION MAC IP HOSTNAME

curl -s -X POST -H "Content-Type: application/json" \
     -d "{\"action\":\"${1}\",\"mac\":\"${2}\",\"ip\":\"${3}\",\"hostname\":\"${4:-}\"}" \
     --max-time 2 "http://127.0.0.1:8080/api/wifi/lease_event" >/dev/null 2>&1 || true
```

### A.8 systemd Service

The portal runs as a long-lived systemd service.  udev events are delivered
via `systemd-run` and the notify script (see A.6).

```ini
# /etc/systemd/system/rfc2217-portal.service
[Unit]
Description=RFC2217 Portal
After=network.target

[Service]
ExecStart=/usr/bin/python3 /usr/local/bin/rfc2217-portal
Restart=on-failure
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### A.9 Network Ports

| Port | Service |
|------|---------|
| 8080 | Web portal and API |
| 4001 | SLOT1 RFC2217 |
| 4002 | SLOT2 RFC2217 |
| 4003 | SLOT3 RFC2217 |

### A.10 WiFi Configuration Constants

| Constant | Value |
|----------|-------|
| WLAN_IF | `wlan0` (env: `WIFI_WLAN_IF`) |
| AP_IP | `192.168.4.1` |
| AP_NETMASK | `255.255.255.0` |
| AP_SUBNET | `192.168.4.0/24` |
| DHCP_RANGE_START | `192.168.4.2` |
| DHCP_RANGE_END | `192.168.4.20` |
| DHCP_LEASE_TIME | `1h` |
| WORK_DIR | `/tmp/wifi-tester` |
| VERSION | `1.0.0-pi` |

---

## Appendix B: Slot Learning Workflow

### B.1 Tool: rfc2217-learn-slots

```bash
$ rfc2217-learn-slots
Plug a device into the USB hub connector you want to identify...

Detected device:
  DEVNAME:  /dev/ttyACM0
  ID_PATH:  platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.3:1.0
  DEVPATH:  /devices/platform/scb/fd500000.pcie/.../ttyACM0
  BY-PATH:  /dev/serial/by-path/platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.3:1.0

Add this to /etc/rfc2217/slots.json:
  {"label": "SLOT?", "slot_key": "platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.3:1.0", "tcp_port": 400?}
```

### B.2 Initial Setup Procedure

1. Start with empty `slots.json`
2. Plug device into first hub connector
3. Run `rfc2217-learn-slots`, note the `ID_PATH`
4. Add to config as SLOT1 with `tcp_port: 4001`
5. Repeat for each hub connector
6. Restart portal service

---

## Appendix C: Implementation Tasks & Deliverables

### C.1 Tasks

**Serial:**
- [x] TASK-001: Create slot-based configuration loader
- [x] TASK-002: Implement sequence counter in portal
- [x] TASK-003: Implement per-slot locking (threading.Lock)
- [x] TASK-004: Implement POST /api/hotplug endpoint
- [x] TASK-005: Implement device settle checks in start_proxy
- [x] TASK-006: Create rfc2217-udev-notify.sh script
- [x] TASK-007: Create 99-rfc2217-hotplug.rules (systemd-run based)
- [x] TASK-008: Create rfc2217-learn-slots tool
- [x] TASK-009: Update web UI to show slot-based view
- [x] TASK-010: Boot scan for already-plugged devices
- [ ] TASK-011: Test all test cases
- [ ] TASK-012: Deploy to Serial Pi (192.168.0.87)

**Serial Services (v6.0):**
- [ ] TASK-050: Implement `POST /api/serial/reset` (FR-008)
- [ ] TASK-051: Implement `POST /api/serial/monitor` (FR-009)
- [ ] TASK-052: Rewrite enter-portal as composite serial operation
- [ ] TASK-053: Update flapping recovery to use serial reset (FR-007.3)

**Native USB (ESP32-C3):**
- [x] TASK-030: Create plain_rfc2217_server.py for ttyACM devices
- [x] TASK-031: Auto-detect ttyACM vs ttyUSB and select proxy server
- [x] TASK-032: Controlled boot sequence in plain_rfc2217_server.py
- [x] TASK-033: Skip os.open() in wait_for_device() for ttyACM
- [x] TASK-034: Add NATIVE_USB_BOOT_DELAY_S hotplug delay for ttyACM
- [x] TASK-035: USB flap detection (FLAP_WINDOW/THRESHOLD/COOLDOWN)
- [x] TASK-036: Flap detection UI (red FLAPPING badge + warning)

**WiFi:**
- [x] TASK-020: Implement wifi_controller.py (AP, STA, scan, relay, events)
- [x] TASK-021: Add WiFi API routes to portal.py
- [x] TASK-022: Implement mode switching (wifi-testing / serial-interface)
- [x] TASK-023: Create wifi-lease-notify.sh for dnsmasq callbacks
- [x] TASK-024: Create wifi_tester_driver.py (HTTP test driver)
- [x] TASK-025: Create conftest.py + test_instrument.py (WT-xxx tests)
- [x] TASK-026: Add WiFi section to web UI with mode toggle
- [x] TASK-027: Activity log system (deque, `log_activity()`, `GET /api/log`)
- [x] TASK-028: Enter-portal endpoint (`POST /api/enter-portal`, rapid-reset via serial)
- [x] TASK-029: Activity log UI panel with enter-portal button
- [x] TASK-040: WiFi Tester stale wpa_supplicant socket cleanup
- [x] TASK-041: wpa_passphrase ctrl_interface fix for wpa_cli compatibility

### C.2 Deliverables

| Deliverable | Description |
|-------------|-------------|
| `portal.py` | HTTP server with serial slot management, WiFi API, process supervision, hotplug handling |
| `wifi_controller.py` | WiFi instrument backend (hostapd, dnsmasq, wpa_supplicant, iw, HTTP relay) |
| `plain_rfc2217_server.py` | RFC2217 server with direct DTR/RTS passthrough (all devices) |
| ~~`esp_rfc2217_server.py`~~ | Removed — breaks C3 native USB and classic ESP32 over RFC2217 |
| ~~`serial_proxy.py`~~ | Removed — replaced by plain_rfc2217_server.py |
| `rfc2217-udev-notify.sh` | Posts udev events to portal API via curl |
| `wifi-lease-notify.sh` | Posts dnsmasq DHCP lease events to portal API |
| `rfc2217-learn-slots` | CLI tool to discover slot_key for physical connectors |
| `99-rfc2217-hotplug.rules` | udev rules using systemd-run to invoke notify script |
| `rfc2217-portal.service` | systemd unit for the portal |
| `slots.json` | Slot configuration file |
| `wifi_tester_driver.py` | HTTP driver for running WT-xxx tests against the instrument |
| `conftest.py` | Pytest fixtures (`wifi_tester`, `wifi_network`, `--wt-url`, `--run-dut`) |
| `test_instrument.py` | WiFi tester self-tests (32 test cases, WT-100 through WT-603) |
