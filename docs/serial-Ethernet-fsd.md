# Serial-via-Ethernet Functional Specification Document

## 1. Overview

### 1.1 Purpose
Expose USB serial devices (ESP32, Arduino) from a Raspberry Pi to network clients using RFC2217 protocol, with automatic device management, persistent port assignment, and serial traffic logging.

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
│  │ ESP32 #1  │──┼─ :4001 ───┼──│ rfc2217://:4001     │    │
│  └───────────┘  │           │  └─────────────────────┘    │
│  ┌───────────┐  │           │  ┌─────────────────────┐    │
│  │ ESP32 #2  │──┼─ :4002 ───┼──│ Container B         │    │
│  └───────────┘  │           │  │ rfc2217://:4002     │    │
│                 │           │  └─────────────────────┘    │
│  Web Portal ────┼─ :8080    │                             │
└─────────────────┘           └─────────────────────────────┘
```

### 1.3 Components

| Component | Location | Purpose |
|-----------|----------|---------|
| rfc2217-portal | /usr/local/bin/ | Web UI and API for device management |
| serial_proxy.py | /usr/local/bin/ | RFC2217 server with logging |
| 99-rfc2217.rules | /etc/udev/rules.d/ | Auto-start on device plug/unplug |
| rfc2217-hotplug.sh | /usr/local/bin/ | Hotplug event handler |
| devices.conf | /etc/rfc2217/ | Device-to-port mappings |

---

## 2. Functional Requirements

### 2.1 Persistent Port Assignment (FR-001)

**Current Behavior (Problem):**
- Ports are assigned based on tty device name (e.g., /dev/ttyACM0 -> 4001)
- When ESP32 resets or reconnects, tty name may change (ttyACM0 -> ttyACM1)
- This causes the device to get a different port, breaking container configurations

**Required Behavior:**
- Ports MUST be assigned based on device serial number, not tty name
- When a device reconnects, it MUST get the same port it had before
- Config file format: `serial_number=port` instead of `tty=port`
- Fallback to tty-based assignment only if device has no serial number

**Acceptance Criteria:**
- [ ] Device with serial "ABC123" always gets port 4001 regardless of tty name
- [ ] Config file uses serial numbers as keys
- [ ] Backward compatible with old tty-based config entries
- [ ] Devices without serial numbers fall back to tty-based assignment

### 2.2 Automatic Service Start (FR-002)

**Required Behavior:**
- When USB device is plugged in, RFC2217 server starts automatically
- When USB device is unplugged, server stops gracefully
- On Pi boot, all connected devices start automatically

**Acceptance Criteria:**
- [ ] udev rule triggers hotplug script on device add/remove
- [ ] Hotplug script calls portal API to start/stop server
- [ ] Portal service starts on boot (systemd)
- [ ] All connected devices start on portal startup

### 2.3 Device Discovery API (FR-003)

**Required Behavior:**
- Containers can query available devices via HTTP API
- API returns device info including serial number, product name, port, status

**API Endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| /api/devices | GET | List all devices with status |
| /api/discover | GET | List running devices (for containers) |
| /api/start | POST | Start server for device |
| /api/stop | POST | Stop server for device |
| /api/info | GET | Get Pi IP and system info |

**Response Format (GET /api/devices):**
```json
{
  "devices": [
    {
      "tty": "/dev/ttyACM0",
      "serial": "94:A9:90:47:5B:48",
      "product": "USB JTAG/serial debug unit",
      "port": 4001,
      "running": true,
      "url": "rfc2217://192.168.0.87:4001"
    }
  ]
}
```

### 2.4 Serial Traffic Logging (FR-004)

**Required Behavior:**
- All serial traffic is logged with timestamps
- Log files named by device identifier and date
- Logs accessible via API and filesystem

**Log Location:** /var/log/serial/
**Log Format:** `[timestamp] [direction] message`

### 2.5 Web Portal (FR-005)

**Required Behavior:**
- Display all connected devices
- Show device status (running/stopped)
- Start/stop individual devices
- Copy RFC2217 URL to clipboard
- Display usage examples

---

## 3. Technical Specifications

### 3.1 Port Assignment Algorithm

```python
def assign_port(device_info, config):
    # 1. Try serial number first (persistent)
    serial = device_info.get('serial', '').replace(':', '_')
    if serial and serial in config:
        return config[serial]

    # 2. Fallback to tty (legacy)
    tty = device_info['tty']
    if tty in config:
        return config[tty]

    # 3. Assign next available port
    used_ports = set(config.values())
    port = 4001
    while port in used_ports:
        port += 1

    # 4. Save with serial number as key (or tty if no serial)
    key = serial if serial else tty
    config[key] = port
    save_config(config)
    return port
```

### 3.2 Config File Format

**New Format (serial-based):**
```
# /etc/rfc2217/devices.conf
# Format: serial_number=port or tty=port (legacy)
94_A9_90_47_5B_48=4001
A5069RR4=4002
/dev/ttyUSB2=4003
```

### 3.3 udev Rules

```
# /etc/udev/rules.d/99-rfc2217.rules
ACTION=="add", SUBSYSTEM=="tty", KERNEL=="ttyACM*", RUN+="/usr/local/bin/rfc2217-hotplug.sh add /dev/%k"
ACTION=="remove", SUBSYSTEM=="tty", KERNEL=="ttyACM*", RUN+="/usr/local/bin/rfc2217-hotplug.sh remove /dev/%k"
ACTION=="add", SUBSYSTEM=="tty", KERNEL=="ttyUSB*", RUN+="/usr/local/bin/rfc2217-hotplug.sh add /dev/%k"
ACTION=="remove", SUBSYSTEM=="tty", KERNEL=="ttyUSB*", RUN+="/usr/local/bin/rfc2217-hotplug.sh remove /dev/%k"
```

### 3.4 Network Ports

| Port | Service |
|------|---------|
| 8080 | Web portal and API |
| 4001 | First RFC2217 device |
| 4002 | Second RFC2217 device |
| 4001+ | Additional devices |

---

## 4. Test Cases

### TC-001: Persistent Port on Device Reset
1. Connect ESP32 with serial "ABC123"
2. Verify assigned port (e.g., 4001)
3. Reset ESP32 (press reset button)
4. Verify device gets same port 4001
5. **Pass:** Port unchanged after reset

### TC-002: Persistent Port on Reconnect
1. Connect ESP32 with serial "ABC123", gets port 4001
2. Disconnect USB cable
3. Reconnect USB cable
4. Verify device gets port 4001 again
5. **Pass:** Port unchanged after reconnect

### TC-003: Persistent Port with Different TTY
1. Connect ESP32 as /dev/ttyACM0, gets port 4001
2. Disconnect
3. Connect different device to /dev/ttyACM0
4. Reconnect original ESP32 (now /dev/ttyACM1)
5. Verify original ESP32 still gets port 4001
6. **Pass:** Port based on serial, not tty

### TC-004: Auto-Start on Plug
1. Ensure portal is running
2. Plug in ESP32
3. Wait 2 seconds
4. Check /api/devices
5. **Pass:** Device shows running=true

### TC-005: Auto-Stop on Unplug
1. Have running device on port 4001
2. Unplug device
3. Check /api/devices
4. **Pass:** Device no longer listed or shows running=false

### TC-006: Container Discovery
1. Start RFC2217 server for device
2. From container: curl http://192.168.0.87:8080/api/discover
3. **Pass:** Response includes device with correct URL

### TC-007: Boot Persistence
1. Connect devices, note port assignments
2. Reboot Pi
3. Verify devices get same ports after boot
4. **Pass:** Port assignments survive reboot

---

## 5. Implementation Tasks

- [x] **TASK-001:** Modify portal.py to use serial numbers as config keys
- [x] **TASK-002:** Update assign_port() to prefer serial number
- [x] **TASK-003:** Update read_config/write_config for new format
- [x] **TASK-004:** Add migration for existing tty-based config
- [x] **TASK-005:** Fix udev rule to call correct hotplug script name
- [ ] **TASK-006:** Test all test cases
- [x] **TASK-007:** Update documentation
- [x] **TASK-008:** Deploy to Serial Pi (192.168.0.87)

---

## 6. Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-05 | Claude | Initial FSD |
| 1.1 | 2026-02-05 | Claude | Implemented FR-001 (persistent port assignment) |
