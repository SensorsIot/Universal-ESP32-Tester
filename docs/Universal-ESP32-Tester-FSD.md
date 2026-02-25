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
│  Serial Portal Pi       │              │  VM Host                        │
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
│  │  AP: <AP_IP>      │  │
│  │  STA / Scan       │  │
│  └───────────────────┘  │
│                         │                    ┌──────────────────┐
│  ┌───────────────────┐  │                    │  ESP32 DUT       │
│  │ BLE Proxy         │  │◄ ─ BLE (GATT) ─ ─ │  iOS-Keyboard    │
│  │ hci0 (onboard)    │  │                    │  BLE peripheral  │
│  │  Scan / Connect   │  │                    └──────────────────┘
│  │  Write GATT chars │  │
│  └───────────────────┘  │                    ┌──────────────────┐
│                         │                    │  ESP32 DUT       │
│  ┌───────────────────┐  │◄ ─ UDP :5555 ─ ─ ─│  debug logs      │
│  │ UDP Log Receiver  │  │                    └──────────────────┘
│  │  Port 5555        │  │
│  └───────────────────┘  │
│                         │
│  ┌───────────────────┐  │
│  │ Firmware Repo     │  │─── GET /firmware/<project>/<file>.bin
│  │ /var/lib/.../fw   │  │
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
| GPIO wiring | Pi GPIO 17 → DUT EN/RST (reset, active LOW); Pi GPIO 18 → DUT GPIO0/GPIO9 (boot select, active LOW); Pi GPIO 27 → Spare 1; Pi GPIO 22 → Spare 2 |

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
| portal.py (rfc2217-portal) | /usr/local/bin/rfc2217-portal | Web UI, HTTP API, proxy supervisor, hotplug handler, WiFi API, BLE API, UDP log, firmware serving |
| wifi_controller.py | /usr/local/bin/wifi_controller.py | WiFi instrument backend (AP, STA, scan, relay, events) |
| ble_controller.py | /usr/local/bin/ble_controller.py | BLE proxy backend (scan, connect, write GATT characteristics via bleak) |
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
| Captive | wlan0 joined DUT's portal AP as STA (Pi gets DHCP address, DUT at its SoftAP gateway) |
| AP | wlan0 running test AP (Pi at AP_IP, DUT connects via DHCP) |

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

**Used by:** flapping recovery (FR-007), integration tests

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

**Used by:** flapping recovery (FR-007), test verification

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
| **Human Interaction** | | |
| POST | /api/human-interaction | Block until operator confirms a physical action (FR-017) |
| GET | /api/human/status | Check if a human interaction request is pending |
| POST | /api/human/done | Operator confirms action complete (wakes blocked request) |
| POST | /api/human/cancel | Operator or test script cancels request |
| **GPIO** | | |
| POST | /api/gpio/set | Drive a Pi GPIO pin low/high or release to input (FR-018) |
| GET | /api/gpio/status | Read state of all actively driven GPIO pins (FR-018) |
| **Test Progress** | | |
| POST | /api/test/update | Push test session start, step, result, or end (FR-019) |
| GET | /api/test/progress | Poll current test session state (FR-019) |
| **Composite** | | |
| GET | /api/log | Activity log (timestamped entries, filterable with `?since=`) |
| POST | /api/enter-portal | Connect to DUT's captive portal SoftAP, submit WiFi credentials, then start local AP |

#### Enter-Portal Composite Operation

`POST /api/enter-portal` is a composite operation that connects to a DUT's
captive portal SoftAP, submits WiFi credentials to provision it, then starts
a local AP with those same credentials for the DUT to connect back to.

**Request body:**
```json
{"portal_ssid": "iOS-Keyboard-Setup", "ssid": "MyNetwork", "password": "secret123"}
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `portal_ssid` | No | `"iOS-Keyboard-Setup"` | The device's SoftAP name to connect to |
| `ssid` | Yes | — | WiFi SSID to provision on the device |
| `password` | No | — | WiFi password to provision on the device |

**Procedure:**
1. Join the DUT's captive portal SoftAP (`portal_ssid`) as a WiFi station
2. `POST` the WiFi credentials (`ssid`, `password`) to the DUT's captive portal at `/connect`
   on the DUT's built-in provisioning endpoint
3. Disconnect from the DUT's SoftAP
4. Start a local AP with the provisioned `ssid` and `password` so the DUT
   can connect back to the Pi after it reboots onto the new network

Each step is logged to the activity log.  Progress is observable via
`GET /api/log?since=<ts>`.

**Response:** `{"ok": true}` on success; `{"ok": false, "error": "..."}` on
failure (e.g., unable to join SoftAP, credential submission failed)

### FR-011 — AP Mode

The Pi's wlan0 runs hostapd + dnsmasq to create a SoftAP:

- **SSID/password/channel** configurable per `POST /api/wifi/ap_start`
- **IP addressing:** AP IP defaults to `192.168.4.1/24` (configurable via `WIFI_AP_IP` env var)
- **DHCP range:** defaults to `192.168.4.2` – `192.168.4.20`, 1-hour leases (configurable via `WIFI_DHCP_START`/`WIFI_DHCP_END` env vars)
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

### FR-017 — Human Interaction Request

Some test steps require physical actions that cannot be automated — pressing a
button, connecting a cable, power-cycling a device, repositioning an antenna.
The human interaction endpoint lets test scripts request operator assistance via
the web UI and block until the action is confirmed.

**Endpoint:** `POST /api/human-interaction`

**Request body:**
```json
{"message": "Connect the USB cable to port 2 and click Done", "timeout": 120}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| message | string | Yes | — | Free-text instruction displayed to operator |
| timeout | number | No | 120 | Max seconds to wait for confirmation |

**Behaviour:**

1. Server stores the message and creates a `threading.Event`
2. Server blocks the HTTP response on `event.wait(timeout)`
3. Web UI polls `GET /api/human/status` (every 2s via existing refresh loop)
   and shows a pulsing orange modal overlay with the message text
4. Operator performs the action, then clicks **Done** (`POST /api/human/done`)
   or **Cancel** (`POST /api/human/cancel`)
5. Done/Cancel sets the event — the blocked handler wakes and returns immediately
6. If timeout expires before confirmation, handler returns with `timeout: true`

**Response (confirmed):**
```json
{"ok": true, "confirmed": true}
```

**Response (cancelled):**
```json
{"ok": true, "confirmed": false}
```

**Response (timeout):**
```json
{"ok": true, "confirmed": false, "timeout": true}
```

**Concurrency:** Only one request can be pending at a time. A second request
while one is active returns `409 Conflict`. The portal uses
`ThreadingHTTPServer` so the blocked handler does not prevent other API
requests from being served.

**Driver method:**
```python
wt.human_interaction("Press the reset button and click Done", timeout=60)
# Returns True if confirmed, False if cancelled or timed out
```

**Activity log:** Each request, confirmation, cancellation, and timeout is
logged to the activity log.

### FR-018 — GPIO Control

Drive Pi GPIO pins from test scripts to control DUT hardware signals — for
example, holding DUT GPIO 2 low during boot to trigger captive portal mode
without requiring the rapid-reset approach or physical button presses.

**Pin allowlist:** Only these Pi GPIO pins may be controlled:

```
{5, 6, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27}
```

Requests for pins outside this set return HTTP 400.

**IMPORTANT — Always release pins when done:** GPIO pins must be released
back to input (high-Z) after use by sending `value: "z"`.  Leaving a pin
driven can interfere with DUT operation (e.g. holding GPIO 2 low permanently
would prevent the DUT from ever entering normal boot mode).

#### 18.1 Endpoints

**`POST /api/gpio/set`** — Drive a GPIO pin or release it

Request body:
```json
{"pin": 17, "value": 0}
```

| Field | Type | Required | Values | Description |
|-------|------|----------|--------|-------------|
| pin | int | Yes | See allowlist | Pi BCM GPIO pin number |
| value | int/string | Yes | `0`, `1`, `"z"` | 0 = drive low, 1 = drive high, "z" = release to input (high-Z) |

Response:
```json
{"ok": true, "pin": 17, "value": 0}
```

**`GET /api/gpio/status`** — Read state of all actively driven pins

Response:
```json
{"ok": true, "pins": {"17": {"direction": "output", "value": 0}}}
```

Pins that have been released (value `"z"`) do not appear in the response.

#### 18.2 Implementation

- **Lazy init:** `gpiod.Chip("/dev/gpiochip0")` is opened on first use
- **Thread-safe:** All GPIO operations are serialized via `_gpio_lock`
- **gpiod v2 API:** Uses `gpiod.line.Direction.OUTPUT`,
  `gpiod.line.Value.ACTIVE`/`INACTIVE`, `request_lines()`, `set_value()`,
  `get_value()`, `release()`
- **Resource management:** Releasing a pin (`"z"`) calls
  `LineRequest.release()` and removes the pin from the active set

#### 18.3 Captive Portal via GPIO

GPIO control provides an alternative approach to triggering captive portal
mode on the DUT (complementary to `POST /api/enter-portal` which handles
the WiFi provisioning flow after the device is already in portal mode):

1. `POST /api/gpio/set` `{"pin": 18, "value": 0}` — hold DUT boot pin (GPIO0/GPIO9) LOW
2. `POST /api/gpio/set` `{"pin": 17, "value": 0}` — pull DUT EN/RST LOW (reset)
3. Wait 100ms, then `POST /api/gpio/set` `{"pin": 17, "value": "z"}` — release reset; DUT boots into portal mode
4. Verify captive portal from serial output (look for `CAPTIVE PORTAL MODE TRIGGERED` or `AP Started:`)
5. `POST /api/gpio/set` `{"pin": 18, "value": "z"}` — release boot pin immediately

The `ok: true` response from `/api/gpio/set` confirms the pin is driven —
there is no need to poll `/api/gpio/status` to verify.

**Driver methods:**
```python
wt.gpio_set(18, 0)           # Hold DUT boot pin (GPIO0/GPIO9) LOW
wt.gpio_set(17, 0)           # Pull EN/RST LOW (reset)
time.sleep(0.1)
wt.gpio_set(17, "z")         # Release reset — DUT boots into portal mode
# Check serial output for portal confirmation
wt.gpio_set(18, "z")         # Release boot pin — ALWAYS do this when done
```

### FR-019 — Test Progress Tracking

Test scripts can push live progress updates to the portal web UI so
operators can monitor test execution without a terminal.

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /api/test/update | Push session start, step updates, results, or end |
| GET | /api/test/progress | Poll current test session state |

**Session lifecycle:**

1. `POST /api/test/update` with `{spec, phase, total}` — start session
2. `POST /api/test/update` with `{current: {id, name, step, manual}}` — update current test
3. `POST /api/test/update` with `{result: {id, name, result, details}}` — record result (PASS/FAIL/SKIP)
4. `POST /api/test/update` with `{end: true}` — end session

**Driver methods:**
```python
wt.test_start("Modbus Proxy v1.4", "Integration", total=58)
wt.test_step("TC-001", "WiFi Connect", "Joining AP...", manual=False)
wt.test_result("TC-001", "WiFi Connect", "PASS")
wt.test_end()
```

### FR-020 — UDP Log Receiver

ESP32 devices send debug logs over UDP (since their USB port is often
occupied by HID or other functions).  The Pi listens for these UDP log
packets and makes them available through the HTTP API and web UI.

**Configuration:**

| Constant | Value |
|----------|-------|
| UDP_LOG_PORT | `5555` (env: `UDP_LOG_PORT`) |
| UDP_LOG_MAX_LINES | `2000` |

**Behaviour:**

1. Portal spawns a background thread with a UDP socket bound to `0.0.0.0:5555`
2. Each received datagram is decoded as UTF-8, split by newlines
3. Lines are stored in a `collections.deque(maxlen=2000)` with timestamps
   and source IP
4. Lines are also forwarded to the activity log via `log_activity()`
5. The UDP socket thread is daemon — it exits when the portal exits

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /api/udplog | Retrieve buffered UDP log lines |
| DELETE | /api/udplog | Clear the UDP log buffer |

**GET /api/udplog** query parameters:

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| since | float | 0 | Only return lines with timestamp > since |
| source | string | (all) | Filter by source IP address |
| limit | int | 200 | Max lines to return |

**Response:**
```json
{
  "ok": true,
  "lines": [
    {"ts": 1740000000.123, "source": "<DUT_IP>", "line": "I (12345) wifi_mgr: Connected"},
    {"ts": 1740000000.456, "source": "<DUT_IP>", "line": "I (12346) ble_nus: Client connected"}
  ]
}
```

**Driver methods:**
```python
logs = wt.udplog(since=0, source="<DUT_IP>", limit=100)
wt.udplog_clear()
```

**Implementation notes:**
- Thread-safe: deque operations are atomic; timestamp+source stored per entry
- Non-blocking: UDP recv in a loop with 1s timeout for clean shutdown
- ESP32 remote_log.c sends to the configured host:port (default 192.168.0.87:5555)

### FR-021 — OTA Firmware Repository

The Pi serves firmware binaries over HTTP so ESP32 devices can perform
OTA updates from the local network.  This eliminates the need for
internet access or external hosting during development and testing.

**Configuration:**

| Constant | Value |
|----------|-------|
| FIRMWARE_DIR | `/var/lib/rfc2217/firmware` (env: `FIRMWARE_DIR`) |

**Directory layout:**
```
/var/lib/rfc2217/firmware/
├── ios-keyboard/
│   └── ios-keyboard.bin
├── modbus-proxy/
│   └── modbus-proxy.bin
└── ...
```

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /firmware/`<project>`/`<filename>` | Download firmware binary (used by ESP32 OTA) |
| GET | /api/firmware/list | List all available firmware files |
| POST | /api/firmware/upload | Upload a firmware binary |
| DELETE | /api/firmware/delete | Delete a firmware file |

**GET /firmware/`<project>`/`<filename>`**

Serves the raw binary file with `Content-Type: application/octet-stream`.
This is the URL the ESP32 OTA client points to, e.g.:
```
http://192.168.0.87:8080/firmware/ios-keyboard/ios-keyboard.bin
```

Path traversal is rejected (no `..` allowed in project or filename).

**GET /api/firmware/list** response:
```json
{
  "ok": true,
  "files": [
    {"project": "ios-keyboard", "filename": "ios-keyboard.bin", "size": 1048576, "modified": "2026-02-25T10:00:00+00:00"}
  ]
}
```

**POST /api/firmware/upload** body (multipart/form-data):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| project | string | Yes | Project subdirectory name |
| file | file | Yes | The firmware binary |

**Response:**
```json
{"ok": true, "project": "ios-keyboard", "filename": "ios-keyboard.bin", "size": 1048576}
```

**DELETE /api/firmware/delete** body:
```json
{"project": "ios-keyboard", "filename": "ios-keyboard.bin"}
```

**Driver methods:**
```python
files = wt.firmware_list()
wt.firmware_upload("ios-keyboard", "/path/to/ios-keyboard.bin")
wt.firmware_delete("ios-keyboard", "ios-keyboard.bin")
# ESP32 OTA URL: http://192.168.0.87:8080/firmware/ios-keyboard/ios-keyboard.bin
```

**End-to-end OTA workflow:**

The tester supports a complete remote OTA workflow for ESP32 devices
connected to its WiFi AP.  The HTTP relay (`POST /api/wifi/http`) bridges
the LAN and WiFi AP networks, allowing OTA to be triggered from any
client on the LAN.

1. **Upload firmware** to the tester's OTA repository:
   ```
   POST /api/firmware/upload  (multipart: project=ios-keyboard, file=ios-keyboard.bin)
   ```
2. **Verify** the firmware is downloadable at the serving URL:
   ```
   GET /firmware/ios-keyboard/ios-keyboard.bin
   ```
3. **Trigger OTA** on the ESP32 via the HTTP relay:
   ```
   POST /api/wifi/http  {"method":"POST", "url":"http://<DUT_IP>/ota"}
   ```
   The ESP32 must expose a `POST /ota` endpoint that calls `esp_ota_ops`
   to download from `http://192.168.0.87:8080/firmware/<project>/<file>.bin`.
4. **Monitor progress** via UDP logs:
   ```
   GET /api/udplog?source=<DUT_IP>
   ```
   The ESP32 logs OTA progress (download bytes, partition writes, reboot)
   which the tester captures on UDP port 5555.

**Prerequisites for the ESP32 device:**
- Connected to the tester's WiFi AP (via `POST /api/enter-portal` or manual provisioning)
- HTTP server running with a `POST /ota` trigger endpoint
- OTA URL configured to point at the tester's firmware repository

**Implementation notes:**
- Path traversal protection: reject `..` in both project and filename
- Directory auto-creation: project subdirectory created on first upload
- install.sh creates `/var/lib/rfc2217/firmware` with appropriate permissions
- Binary serving uses chunked reads (8 KB blocks) to avoid loading large
  files into memory

### FR-022 — BLE Proxy

The Pi's onboard Bluetooth radio acts as a BLE Central (client) that can
scan for, connect to, and send commands to BLE peripherals.  This enables
remote control of BLE devices (e.g., sending keystrokes to an ESP32
running the iOS-Keyboard firmware) from test scripts or AI agents via the
HTTP API.

The Pi is a **dumb BLE-to-HTTP bridge** — it handles only scan, connect,
disconnect, status, and raw byte writes.  All higher-level protocol logic
(command encoding, text diffing, chunking) is the responsibility of the
caller.

**Dependencies:**
- `bleak>=0.20.0` (Python async BLE library, uses BlueZ on Linux)
- BlueZ 5.43+ (standard on Raspberry Pi OS)

**Configuration:**

| Constant | Value |
|----------|-------|
| BLE_SCAN_TIMEOUT | `5.0` seconds (env: `BLE_SCAN_TIMEOUT`) |

**State model:**

| State | Description |
|-------|-------------|
| Idle | No BLE activity |
| Scanning | Actively scanning for BLE peripherals |
| Connected | Connected to a BLE peripheral |

State transitions:

| From | To | Trigger |
|------|----|---------|
| Idle | Scanning | `POST /api/ble/scan` |
| Scanning | Idle | Scan completes (timeout) |
| Idle | Connected | `POST /api/ble/connect` |
| Connected | Idle | `POST /api/ble/disconnect` or remote disconnect |

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /api/ble/scan | Scan for BLE peripherals, return list |
| POST | /api/ble/connect | Connect to a BLE peripheral by address |
| POST | /api/ble/disconnect | Disconnect from current peripheral |
| GET | /api/ble/status | Connection state and device info |
| POST | /api/ble/write | Write raw bytes to a GATT characteristic |

**POST /api/ble/scan** body (optional):
```json
{"timeout": 5.0, "name_filter": "iOS-Keyboard"}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| timeout | float | 5.0 | Scan duration in seconds |
| name_filter | string | (none) | Only return devices whose name contains this string |

**Response:**
```json
{
  "ok": true,
  "devices": [
    {"address": "1C:DB:D4:84:58:CC", "name": "iOS-Keyboard", "rssi": -45}
  ]
}
```

**POST /api/ble/connect** body:
```json
{"address": "1C:DB:D4:84:58:CC"}
```

**Response:**
```json
{
  "ok": true,
  "address": "1C:DB:D4:84:58:CC",
  "name": "iOS-Keyboard",
  "services": [
    {
      "uuid": "6e400001-b5a3-f393-e0a9-e50e24dcca9e",
      "characteristics": [
        {"uuid": "6e400002-b5a3-f393-e0a9-e50e24dcca9e", "properties": ["write", "write-without-response"]},
        {"uuid": "6e400003-b5a3-f393-e0a9-e50e24dcca9e", "properties": ["notify"]}
      ]
    }
  ]
}
```

**POST /api/ble/disconnect** — no body required.

**Response:**
```json
{"ok": true}
```

**GET /api/ble/status** response:
```json
{
  "ok": true,
  "state": "connected",
  "address": "1C:DB:D4:84:58:CC",
  "name": "iOS-Keyboard"
}
```

States: `"idle"`, `"scanning"`, `"connected"`.

**POST /api/ble/write** body:
```json
{"characteristic": "6e400002-b5a3-f393-e0a9-e50e24dcca9e", "data": "024865 6c6c6f", "response": true}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| characteristic | string | Yes | Target GATT characteristic UUID |
| data | string | Yes | Hex-encoded bytes to write |
| response | bool | No (default true) | Use write-with-response (true) or write-without-response (false) |

**Response:**
```json
{"ok": true, "bytes_written": 6}
```

**Error responses:**

| Condition | HTTP | Response |
|-----------|------|----------|
| Not connected | 409 | `{"ok": false, "error": "not connected"}` |
| Already connected | 409 | `{"ok": false, "error": "already connected to 1C:DB:D4:84:58:CC"}` |
| Device not found | 404 | `{"ok": false, "error": "device not found"}` |
| Write failed | 500 | `{"ok": false, "error": "write failed: ..."}` |
| Invalid hex data | 400 | `{"ok": false, "error": "invalid hex data"}` |

**Driver methods:**
```python
devices = wt.ble_scan(timeout=5.0, name_filter="iOS-Keyboard")
info = wt.ble_connect("1C:DB:D4:84:58:CC")
status = wt.ble_status()
wt.ble_write("6e400002-b5a3-f393-e0a9-e50e24dcca9e", bytes([0x02]) + b"Hello")
wt.ble_disconnect()
```

**Implementation notes:**
- `ble_controller.py` runs its own `asyncio` event loop in a background
  thread (bleak is async, portal is sync)
- Module-level lock (`_lock`) serializes all BLE operations
- Connection state tracked in module globals: `_client`, `_address`, `_name`
- Disconnect callback updates state automatically on remote disconnect
- Scan results are ephemeral (not cached)
- Only one BLE connection at a time (Raspberry Pi hardware limitation
  with single radio)

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
  triggers `POST /api/enter-portal` to connect to a DUT's captive portal
  SoftAP and submit WiFi credentials.  "Clear" button resets the display.  Log is polled every
  2 seconds via `GET /api/log?since=<last_ts>`.
- **Human interaction modal** — full-screen dark overlay with pulsing orange
  border, shown when a test script posts a human interaction request.
  Displays the operator instruction text with Done and Cancel buttons.
  Polled via `GET /api/human/status` as part of the auto-refresh cycle.
- **Test progress panel** — shown when a test session is active.  Displays
  spec name, phase, progress bar, current test step, and completed results
  (PASS/FAIL/SKIP with colour badges).  Polled via `GET /api/test/progress`.
- **Auto-refresh** — every 2 seconds via `setInterval`, fetches
  `/api/devices`, `/api/wifi/mode`, `/api/wifi/ap_status`, `/api/log`,
  `/api/human/status`, and `/api/test/progress`
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

| WT-700 | Human interaction confirm | Human Interaction | No |
| WT-701 | Human interaction cancel | Human Interaction | No |
| WT-702 | Human interaction timeout | Human Interaction | No |
| WT-703 | Concurrent request rejected | Human Interaction | No |
| WT-800 | GPIO set low | GPIO Control | No |
| WT-801 | GPIO set high | GPIO Control | No |
| WT-802 | GPIO release to input | GPIO Control | No |
| WT-803 | GPIO status shows active pins | GPIO Control | No |
| WT-804 | GPIO disallowed pin rejected | GPIO Control | No |
| WT-805 | GPIO invalid value rejected | GPIO Control | No |
| WT-806 | GPIO captive portal trigger | GPIO Control | Yes |
| WT-900 | Test progress start session | Test Progress | No |
| WT-901 | Test progress step update | Test Progress | No |
| WT-902 | Test progress result recording | Test Progress | No |
| WT-903 | Test progress end session | Test Progress | No |
| WT-1000 | UDP log receive single line | UDP Log | Yes |
| WT-1001 | UDP log receive from multiple sources | UDP Log | Yes |
| WT-1002 | UDP log filter by source | UDP Log | Yes |
| WT-1003 | UDP log filter by since | UDP Log | Yes |
| WT-1004 | UDP log clear | UDP Log | No |
| WT-1005 | UDP log buffer overflow (>2000 lines) | UDP Log | Yes |
| WT-1100 | Firmware upload | OTA Firmware | No |
| WT-1101 | Firmware list | OTA Firmware | No |
| WT-1102 | Firmware download | OTA Firmware | No |
| WT-1103 | Firmware delete | OTA Firmware | No |
| WT-1104 | Firmware path traversal rejected | OTA Firmware | No |
| WT-1105 | ESP32 OTA from Pi firmware repo | OTA Firmware | Yes |
| WT-1200 | BLE scan finds devices | BLE Proxy | Yes |
| WT-1201 | BLE scan with name filter | BLE Proxy | Yes |
| WT-1202 | BLE connect to device | BLE Proxy | Yes |
| WT-1203 | BLE status shows connected | BLE Proxy | Yes |
| WT-1204 | BLE write to characteristic | BLE Proxy | Yes |
| WT-1205 | BLE disconnect | BLE Proxy | Yes |
| WT-1206 | BLE write when not connected | BLE Proxy | No |
| WT-1207 | BLE double connect rejected | BLE Proxy | Yes |

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
| 6.1 | 2026-02-09 | Claude | Human interaction request (FR-017): blocking endpoint for test steps requiring physical operator actions; pulsing orange UI modal; ThreadingHTTPServer for concurrent requests; driver `human_interaction()` method; WT-700–703 test cases |
| 6.2 | 2026-02-09 | Claude | GPIO control (FR-018): drive Pi GPIO pins from test scripts to control DUT hardware signals (e.g. hold GPIO 2 low during boot for captive portal trigger); pin allowlist, lazy gpiod init, release-to-input lifecycle; WT-800–806 test cases. Test progress tracking (FR-019): live test session updates pushed to web UI; WT-900–903 test cases |
| 7.0 | 2026-02-25 | Claude | Three new services: UDP log receiver (FR-020) for ESP32 remote debug logs on port 5555; OTA firmware repository (FR-021) for serving .bin files to ESP32 OTA clients; BLE proxy (FR-022) for scan/connect/write to BLE peripherals via HTTP API using bleak. New deliverable: `ble_controller.py`. WT-1000–1207 test cases |

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

| Port | Protocol | Service |
|------|----------|---------|
| 8080 | TCP/HTTP | Web portal, REST API, firmware downloads |
| 4001 | TCP/RFC2217 | SLOT1 serial proxy |
| 4002 | TCP/RFC2217 | SLOT2 serial proxy |
| 4003 | TCP/RFC2217 | SLOT3 serial proxy |
| 5555 | UDP | ESP32 debug log receiver |

### A.10 WiFi Configuration Constants

| Constant | Value |
|----------|-------|
| WLAN_IF | `wlan0` (env: `WIFI_WLAN_IF`) |
| AP_IP | `192.168.4.1` (env: `WIFI_AP_IP`) |
| AP_NETMASK | `255.255.255.0` (env: `WIFI_AP_NETMASK`) |
| AP_SUBNET | `192.168.4.0/24` (env: `WIFI_AP_SUBNET`) |
| DHCP_RANGE_START | `192.168.4.2` (env: `WIFI_DHCP_START`) |
| DHCP_RANGE_END | `192.168.4.20` (env: `WIFI_DHCP_END`) |
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

**Human Interaction (v6.1):**
- [x] TASK-060: Implement `POST /api/human-interaction` with blocking Event (FR-017)
- [x] TASK-061: Implement `GET /api/human/status`, `POST /api/human/done`, `POST /api/human/cancel`
- [x] TASK-062: Human interaction modal in web UI (pulsing orange overlay, Done/Cancel)
- [x] TASK-063: Switch to `ThreadingHTTPServer` for concurrent request handling
- [x] TASK-064: Add `human_interaction()` method to `wifi_tester_driver.py`
- [x] TASK-065: Add `Cache-Control: no-cache` to UI HTML response

**GPIO Control (v6.2):**
- [x] TASK-070: Implement `POST /api/gpio/set` with pin allowlist and gpiod v2 API (FR-018)
- [x] TASK-071: Implement `GET /api/gpio/status` for active pin readback (FR-018)
- [x] TASK-072: Add `gpio_set()` and `gpio_get()` methods to `wifi_tester_driver.py`
- [ ] TASK-073: Implement WT-800–806 GPIO test cases in `test_instrument.py`

**Test Progress (v6.2):**
- [x] TASK-080: Implement `POST /api/test/update` and `GET /api/test/progress` (FR-019)
- [x] TASK-081: Test progress panel in web UI (progress bar, current step, results)
- [x] TASK-082: Add `test_start/step/result/end()` methods to `wifi_tester_driver.py`
- [ ] TASK-083: Implement WT-900–903 test progress test cases

**UDP Log Receiver (v7.0):**
- [ ] TASK-090: Implement UDP socket listener thread in portal.py (FR-020)
- [ ] TASK-091: Implement `GET /api/udplog` and `DELETE /api/udplog` endpoints
- [ ] TASK-092: Add `udplog()` and `udplog_clear()` methods to `wifi_tester_driver.py`
- [ ] TASK-093: Implement WT-1000–1005 UDP log test cases

**OTA Firmware Repository (v7.0):**
- [ ] TASK-100: Create firmware directory and path-safe file serving (FR-021)
- [ ] TASK-101: Implement `GET /firmware/<project>/<filename>` binary serving
- [ ] TASK-102: Implement `GET /api/firmware/list`, `POST /api/firmware/upload`, `DELETE /api/firmware/delete`
- [ ] TASK-103: Add `firmware_list/upload/delete()` methods to `wifi_tester_driver.py`
- [ ] TASK-104: Update install.sh to create firmware directory
- [ ] TASK-105: Implement WT-1100–1105 firmware test cases

**BLE Proxy (v7.0):**
- [ ] TASK-110: Create `ble_controller.py` with asyncio event loop thread (FR-022)
- [ ] TASK-111: Implement BLE scan with optional name filter
- [ ] TASK-112: Implement BLE connect/disconnect with state tracking
- [ ] TASK-113: Implement BLE write to GATT characteristic
- [ ] TASK-114: Add BLE API routes to portal.py (`/api/ble/*`)
- [ ] TASK-115: Add `ble_scan/connect/disconnect/status/write()` methods to `wifi_tester_driver.py`
- [ ] TASK-116: Update install.sh to install bleak dependency
- [ ] TASK-117: Implement WT-1200–1207 BLE proxy test cases

### C.2 Deliverables

| Deliverable | Description |
|-------------|-------------|
| `portal.py` | HTTP server with serial slot management, WiFi API, BLE API, UDP log, firmware serving, process supervision, hotplug handling |
| `wifi_controller.py` | WiFi instrument backend (hostapd, dnsmasq, wpa_supplicant, iw, HTTP relay) |
| `ble_controller.py` | BLE proxy backend (bleak, scan, connect, write to GATT characteristics) |
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
| `test_instrument.py` | Self-tests (WT-100 through WT-1207) |
