# Serial-via-Ethernet Functional Specification Document

## 1. Overview

### 1.1 Purpose
Expose USB serial devices (ESP32, Arduino) from a Raspberry Pi to network clients using RFC2217 protocol, with event-driven device management, slot-based port assignment, and serial traffic logging.

### 1.2 System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Network (192.168.0.x)                        │
└─────────────────────────────────────────────────────────────────────┘
         │                              │
         │                              │
         ▼                              ▼
┌─────────────────┐           ┌─────────────────────────────┐
│  Serial Pi      │           │  VM Host (192.168.0.160)    │
│  192.168.0.87   │           │                             │
│                 │           │  ┌─────────────────────┐    │
│  ┌───────────┐  │           │  │ Container A         │    │
│  │ SLOT1     │──┼─ :4001 ───┼──│ rfc2217://:4001     │    │
│  └───────────┘  │           │  └─────────────────────┘    │
│  ┌───────────┐  │           │  ┌─────────────────────┐    │
│  │ SLOT2     │──┼─ :4002 ───┼──│ Container B         │    │
│  └───────────┘  │           │  │ rfc2217://:4002     │    │
│  ┌───────────┐  │           │  └─────────────────────┘    │
│  │ SLOT3     │──┼─ :4003    │                             │
│  └───────────┘  │           │                             │
│  ┌───────────┐  │           │                             │
│  │ SLOT4     │──┼─ :4004    │                             │
│  └───────────┘  │           │                             │
│                 │           │                             │
│  Web Portal ────┼─ :8080    │                             │
└─────────────────┘           └─────────────────────────────┘
```

### 1.3 Hardware

| Component | Details |
|-----------|---------|
| Raspberry Pi | 192.168.0.87 (Serial Pi) |
| USB Hub | 4-port hub for device connections |
| Devices | ESP32, Arduino, or any USB serial device |

### 1.4 Components

| Component | Location | Purpose |
|-----------|----------|---------|
| rfc2217-portal | /usr/local/bin/rfc2217-portal | Web UI, API, process supervisor |
| serial_proxy.py | /usr/local/bin/serial_proxy.py | RFC2217 server with logging |
| rfc2217-hotplug | /usr/local/bin/rfc2217-hotplug | Event handler (udev → portal) |
| rfc2217-learn-slots | /usr/local/bin/rfc2217-learn-slots | Slot configuration helper |
| 99-rfc2217.rules | /etc/udev/rules.d/ | udev rules for hotplug |
| rfc2217-hotplug@.service | /etc/systemd/system/ | systemd template for hotplug |
| slots.json | /etc/rfc2217/slots.json | Slot-to-port mapping |

---

## 2. Definitions

### 2.1 Entities

| Entity | Description |
|--------|-------------|
| **Slot** | Represents one physical connector position on the USB hub |
| **slot_key** | Stable identifier for physical port topology (derived from udev `ID_PATH`) |
| **devnode** | Current tty device path (e.g., `/dev/ttyACM0`) - may change on reconnect |
| **serial_proxy** | Process implementing RFC2217 server for a local serial device |
| **gen** (generation) | Monotonically increasing integer per slot event stream |

### 2.2 Key Principle: Slot-Based Identity

**The system keys on physical connector position, NOT on:**
- `/dev/ttyACMx` (changes on reconnect)
- Device serial number (two identical boards would conflict)
- VID/PID/model (not unique)

**The system keys on:**
- `slot_key` = udev `ID_PATH` (identifies physical USB port topology)

This ensures:
- Same physical connector → same TCP port (always)
- Device can be swapped → same TCP port
- Two identical boards → different TCP ports (different slots)

---

## 3. Functional Requirements

### 3.1 Event-Driven Hotplug (FR-001)

**Unplug Flow:**
1. udev emits `remove` event for the serial device
2. Hotplug handler determines `slot_key` from event
3. Hotplug handler increments generation counter
4. Hotplug handler calls Portal API `POST /api/stop` with `{slot_key, gen}`
5. Portal stops the `serial_proxy` process for that slot (idempotent)
6. Slot state becomes `running=false`, `devnode=null`

**Plug Flow:**
1. udev emits `add` event for the serial device
2. Hotplug handler determines `slot_key` and `devnode` from event
3. Hotplug handler increments generation counter
4. Hotplug handler waits for device to settle (readiness checks, not fixed sleep)
5. Hotplug handler calls Portal API `POST /api/start` with `{slot_key, devnode, gen}`
6. Portal looks up assigned TCP port for that slot
7. Portal starts `serial_proxy` bound to `devnode` on assigned TCP port
8. Slot state becomes `running=true`

### 3.2 Slot Configuration (FR-002)

**Required Behavior:**
- Static configuration maps `slot_key` → `{label, tcp_port}`
- Configuration file: `/etc/rfc2217/slots.json`
- Learning tool helps discover `slot_key` values for each physical connector

**Configuration Format:**
```json
{
  "slots": [
    {"label": "SLOT1", "slot_key": "platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.1:1.0", "tcp_port": 4001},
    {"label": "SLOT2", "slot_key": "platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.2:1.0", "tcp_port": 4002},
    {"label": "SLOT3", "slot_key": "platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.3:1.0", "tcp_port": 4003},
    {"label": "SLOT4", "slot_key": "platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.4:1.0", "tcp_port": 4004}
  ]
}
```

### 3.3 Device Discovery API (FR-003)

**API Endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| /api/devices | GET | List all slots with status |
| /api/start | POST | Start server for slot |
| /api/stop | POST | Stop server for slot |
| /api/info | GET | Get Pi IP and system info |

**Request Format (POST /api/start):**
```json
{
  "slot_key": "platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.3:1.0",
  "devnode": "/dev/ttyACM0",
  "gen": 42
}
```

**Request Format (POST /api/stop):**
```json
{
  "slot_key": "platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.3:1.0",
  "gen": 43
}
```

**Response Format (GET /api/devices):**
```json
{
  "slots": [
    {
      "label": "SLOT1",
      "slot_key": "platform-...-usb-0:1.1:1.0",
      "tcp_port": 4001,
      "running": true,
      "devnode": "/dev/ttyACM0",
      "pid": 1234,
      "url": "rfc2217://192.168.0.87:4001",
      "last_gen": 42
    },
    {
      "label": "SLOT2",
      "slot_key": "platform-...-usb-0:1.2:1.0",
      "tcp_port": 4002,
      "running": false,
      "devnode": null,
      "pid": null,
      "url": "rfc2217://192.168.0.87:4002",
      "last_gen": 0
    }
  ]
}
```

### 3.4 Serial Traffic Logging (FR-004)

**Required Behavior:**
- All serial traffic logged with timestamps
- Log files in `/var/log/serial/`
- Log format: `[timestamp] [direction] data`

### 3.5 Web Portal (FR-005)

**Required Behavior:**
- Display all 4 slots (always visible, even if empty)
- Show slot status (running/stopped)
- Show current devnode when running
- Start/stop individual slots
- Copy RFC2217 URL to clipboard
- Display connection examples

---

## 4. Technical Specifications

### 4.1 Slot Key Derivation

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

### 4.2 Generation Tracking

Each slot maintains a generation counter to handle race conditions:

```python
# Storage: /run/rfc2217/gen/<slot_key_hash>.txt

def get_next_gen(slot_key):
    """Increment and return generation number for slot."""
    path = f"/run/rfc2217/gen/{hash(slot_key)}.txt"
    gen = int(read_file(path) or 0)
    gen += 1
    write_file(path, str(gen))
    return gen
```

**Portal applies request only if:**
```python
if request.gen >= slot.last_gen:
    slot.last_gen = request.gen
    # Process request
else:
    # Ignore stale request (return OK with ignored=true)
```

### 4.3 API Idempotency

**POST /api/start semantics:**
- If slot running with same devnode: return OK (no restart)
- If slot running with different devnode: restart cleanly
- If slot not running: start
- Never fails if already in desired state

**POST /api/stop semantics:**
- If slot not running: return OK
- If running: stop
- Never fails if already in desired state

### 4.4 Per-Slot Locking

Portal serializes operations per slot:

```python
# Lock file: /run/rfc2217/locks/<slot_key_hash>.lock

with flock(f"/run/rfc2217/locks/{hash(slot_key)}.lock"):
    # Only one start/stop operation per slot at a time
    process_request(slot_key, request)
```

### 4.5 Device Settle Checks

Instead of fixed sleep, hotplug handler checks readiness:

```python
def wait_for_device(devnode, timeout=5.0):
    """Wait for device to be usable."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(devnode):
            try:
                # Try to open device
                fd = os.open(devnode, os.O_RDWR | os.O_NONBLOCK)
                os.close(fd)
                return True
            except OSError:
                pass
        time.sleep(0.1)
    return False
```

### 4.6 udev Rules

```
# /etc/udev/rules.d/99-rfc2217.rules
# Trigger systemd service for serial device hotplug

ACTION=="add", SUBSYSTEM=="tty", KERNEL=="ttyACM*", TAG+="systemd", ENV{SYSTEMD_WANTS}="rfc2217-hotplug@add-%k.service"
ACTION=="remove", SUBSYSTEM=="tty", KERNEL=="ttyACM*", RUN+="/usr/local/bin/rfc2217-hotplug remove %E{DEVNAME} %E{ID_PATH}"

ACTION=="add", SUBSYSTEM=="tty", KERNEL=="ttyUSB*", TAG+="systemd", ENV{SYSTEMD_WANTS}="rfc2217-hotplug@add-%k.service"
ACTION=="remove", SUBSYSTEM=="tty", KERNEL=="ttyUSB*", RUN+="/usr/local/bin/rfc2217-hotplug remove %E{DEVNAME} %E{ID_PATH}"
```

### 4.7 systemd Service Template

```ini
# /etc/systemd/system/rfc2217-hotplug@.service
[Unit]
Description=RFC2217 Hotplug Handler for %i
After=network.target rfc2217-portal.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/rfc2217-hotplug add /dev/%i
Environment=DEVNAME=/dev/%i
StandardOutput=journal
StandardError=journal
```

### 4.8 Network Ports

| Port | Service |
|------|---------|
| 8080 | Web portal and API |
| 4001 | SLOT1 RFC2217 |
| 4002 | SLOT2 RFC2217 |
| 4003 | SLOT3 RFC2217 |
| 4004 | SLOT4 RFC2217 |

---

## 5. Non-Functional Requirements

### 5.1 Must Tolerate

| Scenario | How Handled |
|----------|-------------|
| `/dev/ttyACM0` → `/dev/ttyACM1` renaming | slot_key unchanged (based on physical port) |
| Duplicate udev events | API idempotency, generation check |
| "Remove after add" races (USB reset) | Generation monotonicity prevents late stop |
| Two identical boards | Different slot_keys (different physical connectors) |
| Hub/Pi reboot | Static config preserves port assignments |

### 5.2 Determinism

- Same physical connector → same TCP port (always)
- Configuration survives reboots
- No dynamic port assignment

### 5.3 Reliability

- Portal API must be idempotent
- Actions serialized per slot (locking)
- Stale events ignored via generation check

---

## 6. Slot Learning Workflow

### 6.1 Tool: rfc2217-learn-slots

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

### 6.2 Initial Setup Procedure

1. Start with empty slots.json
2. Plug device into first hub connector
3. Run `rfc2217-learn-slots`, note the `ID_PATH`
4. Add to config as SLOT1 with tcp_port 4001
5. Repeat for each hub connector
6. Restart portal service

---

## 7. Edge Cases

| Case | Behavior |
|------|----------|
| Two identical boards | Works - different slot_keys (different physical connectors) |
| Device re-enumeration (USB reset) | Generation check prevents late stop from killing new start |
| Duplicate events | Idempotency prevents flapping |
| Unknown slot_key | Portal returns 404, logs for diagnostics |
| Hub topology changed | Must re-learn and update config |
| Device not ready | Settle checks with timeout, then fail |

---

## 8. Test Cases

### TC-001: Plug into SLOT3
1. Ensure portal running, SLOT3 configured for port 4003
2. Plug ESP32 into physical connector mapped to SLOT3
3. Within 5 seconds: `GET /api/devices`
4. **Pass:** SLOT3 shows `running=true`, `devnode` set, `tcp_port=4003`

### TC-002: Unplug from SLOT3
1. Have device running in SLOT3
2. Unplug device
3. Within 2 seconds: `GET /api/devices`
4. **Pass:** SLOT3 shows `running=false`, `devnode=null`

### TC-003: Replug into SLOT3
1. Unplug device from SLOT3
2. Replug into same physical connector
3. **Pass:** SLOT3 `running=true`, same `tcp_port=4003`, devnode may differ

### TC-004: Two Identical Boards
1. Plug identical ESP32 into SLOT1
2. Plug identical ESP32 into SLOT2
3. **Pass:** Both running on different TCP ports (4001, 4002)

### TC-005: USB Reset Race
1. Have device running in SLOT1
2. Force USB reset (quick unplug/replug)
3. **Pass:** No "stuck stopped" state; generation logic handles race

### TC-006: Devnode Renaming
1. Plug device into SLOT1 as `/dev/ttyACM0`
2. Unplug
3. Plug different device (gets `/dev/ttyACM0`)
4. Replug original device (now `/dev/ttyACM1`)
5. **Pass:** Original device still on SLOT1's port (4001)

### TC-007: Boot Persistence
1. Configure slots, plug devices
2. Reboot Pi
3. **Pass:** Same slots get same ports after boot

### TC-008: Unknown Slot
1. Plug device into unconfigured hub connector
2. **Pass:** Portal logs "unknown slot_key", no crash

---

## 9. Implementation Tasks

- [ ] **TASK-001:** Create slot-based configuration loader
- [ ] **TASK-002:** Implement generation tracking in hotplug handler
- [ ] **TASK-003:** Implement per-slot locking in portal
- [ ] **TASK-004:** Update portal API for slot_key/gen parameters
- [ ] **TASK-005:** Implement device settle checks (replace fixed sleep)
- [ ] **TASK-006:** Create systemd service template for hotplug
- [ ] **TASK-007:** Update udev rules for systemd integration
- [ ] **TASK-008:** Create rfc2217-learn-slots tool
- [ ] **TASK-009:** Update web UI to show slot-based view
- [ ] **TASK-010:** Test all test cases
- [ ] **TASK-011:** Deploy to Serial Pi (192.168.0.87)

---

## 10. Deliverables

| Deliverable | Description |
|-------------|-------------|
| `rfc2217-portal` | HTTP server with slot management, process supervision |
| `rfc2217-hotplug` | Event handler: derives slot_key, manages gen, calls API |
| `rfc2217-learn-slots` | CLI tool to discover slot_key for physical connectors |
| `99-rfc2217.rules` | udev rules for systemd integration |
| `rfc2217-hotplug@.service` | systemd template unit |
| `slots.json` | Slot configuration file |
| Installation docs | Setup and slot learning instructions |

---

## 11. Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-05 | Claude | Initial FSD |
| 1.1 | 2026-02-05 | Claude | Implemented serial-based port assignment |
| 1.2 | 2026-02-05 | Claude | Testing complete for serial-based approach |
| 2.0 | 2026-02-05 | Claude | Major rewrite: event-driven slot-based architecture |
