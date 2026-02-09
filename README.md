# Universal ESP32 Tester

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform](https://img.shields.io/badge/Platform-Raspberry%20Pi-red.svg)](https://www.raspberrypi.org/)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/)
[![RFC2217](https://img.shields.io/badge/Protocol-RFC2217-green.svg)](https://datatracker.ietf.org/doc/html/rfc2217)
[![ESP32](https://img.shields.io/badge/Devices-ESP32%20%7C%20Arduino-brightgreen.svg)](https://www.espressif.com/)
[![pytest](https://img.shields.io/badge/Testing-pytest-orange.svg)](https://pytest.org/)

## 🎯 The Problem

Testing ESP32 firmware is a manual nightmare. You flash over a cable, press physical buttons to trigger modes, stare at a serial monitor to see what happened, and restart everything between tests. Automated testing of WiFi, captive portals, MQTT, and GPIO interactions seems impossible — each one requires hands-on intervention.

Meanwhile, if your ESP32s are plugged into a Proxmox host, only one VM can access each USB controller — you can't split devices across containers.

## 💡 The Solution

Turn a **$15 Raspberry Pi Zero W** into a complete ESP32 test instrument. The Pi shares serial ports over the network, runs a WiFi access point for the device under test, relays HTTP to devices on its network, controls GPIO pins to simulate button presses, and provides a real-time test progress panel — all through a single HTTP API.

From a pytest script, you can flash firmware, trigger captive portal mode, provision WiFi credentials, verify MQTT connections, and check REST APIs — with zero human interaction:

```python
ut = ESP32TesterDriver("http://192.168.0.87:8080")

# Reset DUT, start test AP, wait for it to connect
ut.serial_reset("SLOT2")
ut.ap_start("TestAP", "password123")
station = ut.wait_for_station(timeout=30)

# Talk to the DUT through the Pi's radio
resp = ut.http_get(f"http://{station['ip']}/api/status")
assert resp.json()["wifi_connected"] is True
```

## 📋 What It Does

- **Share serial ports** — plug in any USB serial device (ESP32, Arduino, etc.) and it's available over the network via RFC2217 on a fixed TCP port
- **Flash firmware remotely** — works with esptool, PlatformIO, and ESP-IDF over the network
- **Run a WiFi test AP** — start/stop a SoftAP on the Pi's radio, DUTs connect to it for isolated testing
- **Relay HTTP to DUTs** — proxy HTTP requests through the Pi's radio to devices on its AP network
- **Control GPIO pins** — drive Pi GPIO pins to simulate button presses on the DUT (e.g., trigger captive portal mode without touching hardware)
- **Reset and monitor serial** — reset DUTs via DTR/RTS and watch serial output for specific patterns
- **Scan WiFi networks** — see what's broadcasting, verify DUT AP is visible
- **Join DUT networks** — connect to a DUT's captive portal AP as a station to test provisioning flows
- **Track test progress** — real-time panel on the web UI showing which test is running, what step it's on, pass/fail results
- **Request human interaction** — block a test until an operator confirms a physical action (cable swap, power cycle)
- **Web portal** at port 8080 — see connected devices, WiFi status, test progress, copy connection URLs
- **pytest driver included** — `WiFiTesterDriver` class with methods for every capability
- One client per serial device at a time (RFC2217 protocol limitation)
- Slot-based identity — TCP ports are tied to physical USB connectors, not devices. Swap boards freely.

---

## 📡 How It Works

```
                                           Proxmox / Dev Machine
                                          ┌────────────────────────────────┐
┌────────────────────────┐                │  ┌──────────────────────────┐  │
│ Raspberry Pi Zero W    │                │  │ Container / VM           │  │
│ 192.168.0.87           │    eth0        │  │                          │  │
│                        │◄───────────────┼──│  rfc2217://:4001 (serial)│  │
│ ┌────────────────────┐ │                │  │  HTTP API :8080 (control)│  │
│ │ USB Hub            │ │                │  │  pytest + driver         │  │
│ │  SLOT1 ─── :4001   │ │                │  └──────────────────────────┘  │
│ │  SLOT2 ─── :4002   │ │                └────────────────────────────────┘
│ │  SLOT3 ─── :4003   │ │
│ └────────────────────┘ │
│                        │
│ ┌────────────────────┐ │    WiFi (192.168.4.x)
│ │ wlan0 Radio        │ │◄ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
│ │  AP: 192.168.4.1   │ │                    ┌──────┴───────┐
│ │  STA / Scan        │ │                    │  ESP32 DUT   │
│ │  HTTP Relay        │ │                    │  192.168.4.x │
│ └────────────────────┘ │                    └──────────────┘
│                        │
│ ┌────────────────────┐ │
│ │ GPIO (BCM)         │ │ ── wire ──► DUT GPIO 2 (portal trigger)
│ │  Pin 17 → DUT      │ │
│ └────────────────────┘ │
│                        │
│ Web Portal ─── :8080   │
└────────────────────────┘
```

The Pi serves three roles simultaneously:

1. **Serial proxy** — each USB slot gets a fixed TCP port via RFC2217. Plug/unplug is handled automatically by udev hotplug events.
2. **WiFi test instrument** — the Pi's onboard wlan0 radio acts as a programmable AP or station. DUTs connect to the Pi's AP for isolated testing; the Pi relays HTTP to them.
3. **GPIO controller** — Pi GPIO pins wired to DUT pins can simulate button presses during boot to trigger hardware-level behavior (captive portal, factory reset).

---

## ⚡ Installation & Usage

### Prerequisites

- Raspberry Pi (Zero W, 3, 4, or 5) with Raspberry Pi OS
- USB Ethernet adapter (eth0 for wired LAN — wlan0 is reserved for testing)
- USB hub (if more than one device)
- Python 3.9+

### 🚀 Quick Start

```bash
git clone https://github.com/SensorsIot/Universal-ESP32-Tester.git
cd Universal-ESP32-Tester/pi
bash install.sh
```

After installation, discover your USB port slots:

```bash
rfc2217-learn-slots
```

Review and edit the slot configuration:

```bash
sudo nano /etc/rfc2217/slots.json
```

### 🔧 Configuration

Slot configuration maps physical USB ports to TCP ports in `/etc/rfc2217/slots.json`:

```json
{
  "slots": [
    {"slot_key": "platform-3f980000.usb-usb-0:1.2:1.0", "label": "ESP32-A", "tcp_port": 4001},
    {"slot_key": "platform-3f980000.usb-usb-0:1.3:1.0", "label": "ESP32-B", "tcp_port": 4002}
  ]
}
```

Use `rfc2217-learn-slots` to discover the `slot_key` values — plug each device in one at a time and run the tool.

### 🖥️ Web Portal

Open **http://192.168.0.87:8080** in your browser to see:

- Connected devices and their serial slot status
- WiFi AP/STA state and connected stations
- Test progress panel (current test, step, pass/fail counts)
- Copy-paste connection URLs

### 🔌 Serial: Flashing & Monitoring

**Python with pyserial:**
```python
import serial
ser = serial.serial_for_url("rfc2217://192.168.0.87:4001?ign_set_control", baudrate=115200)
print(ser.readline())
```

**esptool:**
```bash
esptool --port "rfc2217://192.168.0.87:4001?ign_set_control" write_flash 0x0 firmware.bin
```

**PlatformIO:**
```ini
[env:esp32]
upload_port = rfc2217://192.168.0.87:4001?ign_set_control
monitor_port = rfc2217://192.168.0.87:4001?ign_set_control
```

**ESP-IDF:**
```bash
export ESPPORT="rfc2217://192.168.0.87:4001?ign_set_control"
idf.py flash monitor
```

### 🧪 pytest Integration

Install the driver from this repo:

```bash
pip install -e Universal-ESP32-Tester/pytest
```

Use it in your tests:

```python
from wifi_tester_driver import WiFiTesterDriver as ESP32TesterDriver

ut = ESP32TesterDriver("http://192.168.0.87:8080")

# Serial
ut.serial_reset("SLOT2")                              # Reset DUT via DTR/RTS
result = ut.serial_monitor("SLOT2", pattern="WiFi connected", timeout=30)

# WiFi AP
ut.ap_start("TestAP", "password123")                  # Start test AP
station = ut.wait_for_station(timeout=30)              # Wait for DUT to connect
ut.ap_stop()                                           # Stop AP

# HTTP relay (talk to DUT through Pi's radio)
resp = ut.http_get(f"http://{station['ip']}/api/status")
assert resp.json()["wifi_connected"] is True

# GPIO (simulate button press during boot)
try:
    ut.gpio_set(17, 0)                                 # Hold DUT pin LOW
    ut.serial_reset("SLOT2")                           # Reset — DUT boots with pin held
finally:
    ut.gpio_set(17, "z")                               # Release to input

# Captive portal
ut.sta_join("MODBUS-Proxy-Setup", timeout=15)          # Join DUT's portal AP
resp = ut.http_get("http://192.168.4.1/")              # Access portal page
ut.sta_leave()                                         # Disconnect

# WiFi scan
networks = ut.scan()                                   # See nearby APs

# Test progress panel
ut.test_start(spec="my-test-spec v1.0", phase="Phase 1", total=10)
ut.test_step("TC-100", "Startup", "Step 1: Check DUT reachable")
ut.test_result("TC-100", "Startup", "PASS")
ut.test_end()
```

### 🔀 GPIO Wiring

Wire Pi GPIO pins to DUT pins for automated hardware control:

| Pi GPIO (BCM) | DUT GPIO | Function | Active Level |
|---------------|----------|----------|-------------|
| 17 | 2 | Captive portal trigger | LOW |

**Pin allowlist:** `{5, 6, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26}`

Always release pins after use: `ut.gpio_set(17, "z")`

### ❓ Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Connection refused on serial port | Proxy not running | Check portal at :8080; verify device is plugged in |
| Timeout during flash | Network latency | Use `esptool --no-stub` for reliability |
| Port busy | Another client connected | Close the other connection first |
| ESP32-C3 stuck in download mode | DTR asserted on port open | Use `--after=watchdog-reset` with esptool, never `hard-reset` |
| DUT not seen on AP | DUT has wrong credentials | Check DUT WiFi config, verify AP is running with `ut.ap_status()` |
| Hotplug events not reaching portal | udev sandbox | Verify `systemd-run --no-block` in udev rules |
| Device not detected | USB issue | Run `ls /dev/ttyUSB* /dev/ttyACM*` and `dmesg | tail` on the Pi |

---

## 🔧 Under the Hood

### 📡 API Endpoints

**Serial:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/devices` | List all slots with status |
| GET | `/api/info` | Pi IP, hostname, slot counts |
| POST | `/api/hotplug` | Receive udev hotplug event |
| POST | `/api/start` | Manually start proxy for a slot |
| POST | `/api/stop` | Manually stop proxy for a slot |
| POST | `/api/serial/reset` | Reset device via DTR/RTS |
| POST | `/api/serial/monitor` | Read serial output with pattern match |
| POST | `/api/enter-portal` | Trigger DUT captive portal via serial reset sequence |

**WiFi:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/wifi/ping` | Version and uptime |
| GET | `/api/wifi/mode` | Current operating mode |
| POST | `/api/wifi/mode` | Switch mode (wifi-testing / serial-interface) |
| POST | `/api/wifi/ap_start` | Start SoftAP |
| POST | `/api/wifi/ap_stop` | Stop SoftAP |
| GET | `/api/wifi/ap_status` | AP status, SSID, stations |
| POST | `/api/wifi/sta_join` | Join a WiFi network as station |
| POST | `/api/wifi/sta_leave` | Disconnect from WiFi network |
| GET | `/api/wifi/scan` | Scan for WiFi networks |
| POST | `/api/wifi/http` | HTTP relay through Pi's radio |
| GET | `/api/wifi/events` | Event queue (long-poll) |

**GPIO / Test / Other:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/gpio/set` | Drive GPIO pin low/high or release to input |
| GET | `/api/gpio/status` | Read actively driven pin states |
| POST | `/api/test/update` | Push test session start/step/result/end |
| GET | `/api/test/progress` | Poll current test session state |
| POST | `/api/human-interaction` | Block until operator confirms physical action |
| GET | `/api/log` | Activity log (filterable with `?since=`) |

### 📂 Files

```
pi/
├── portal.py                     # Web portal + API + proxy supervisor
├── wifi_controller.py            # WiFi instrument (AP, STA, scan, relay)
├── plain_rfc2217_server.py       # RFC2217 server with DTR/RTS passthrough
├── install.sh                    # Installer
├── rfc2217-learn-slots           # Slot discovery tool
├── c3_reset_test.py              # ESP32-C3 reset validation script
├── config/
│   └── slots.json                # Slot-to-port mapping
├── scripts/
│   ├── rfc2217-udev-notify.sh    # udev event forwarder
│   └── wifi-lease-notify.sh      # dnsmasq DHCP lease callback
├── udev/
│   └── 99-rfc2217-hotplug.rules  # udev rules
└── systemd/
    └── rfc2217-portal.service    # systemd unit

pytest/
├── wifi_tester_driver.py         # Python test driver (WiFiTesterDriver class)
├── conftest.py                   # pytest fixtures
└── test_instrument.py            # Self-tests for the instrument

docs/
├── Serial-Portal-FSD.md          # Full functional specification
└── WiFi-Tester-HTTP-Manual.md    # HTTP API manual

skills/
└── esp32-test-harness/SKILL.md   # Claude Code skill for test automation
```

### 🌐 Network Ports

| Port | Direction | Purpose |
|------|-----------|---------|
| 8080 | Browser/API → Pi | Web portal and REST API |
| 4001+ | Container/VM → Pi | RFC2217 serial connections (one per slot) |

### 📻 WiFi Addressing

| Mode | Pi Address | DUT Address | Subnet |
|------|-----------|-------------|--------|
| AP (Pi hosts network) | 192.168.4.1 | 192.168.4.2–20 (DHCP) | 192.168.4.0/24 |
| STA (Pi joins DUT AP) | 192.168.4.x (DHCP) | 192.168.4.1 | DUT's subnet |

---

## 📚 Attributions & References

- [RFC 2217](https://datatracker.ietf.org/doc/html/rfc2217) — Telnet Com Port Control Option
- [pyserial](https://pyserial.readthedocs.io/) — Python serial port library with RFC2217 support
- [esptool](https://github.com/espressif/esptool) — ESP32 flashing tool
- [PlatformIO](https://platformio.org/) — Embedded development platform
- [ESP-IDF](https://docs.espressif.com/projects/esp-idf/) — Espressif IoT Development Framework
- [hostapd](https://w1.fi/hostapd/) — WiFi AP daemon
- [dnsmasq](https://thekelleys.org.uk/dnsmasq/doc.html) — DHCP and DNS for the test AP
- [gpiod](https://git.kernel.org/pub/scm/libs/libgpiod/libgpiod.git/) — Linux GPIO character device library

## 📄 License

MIT License — feel free to use and modify.
