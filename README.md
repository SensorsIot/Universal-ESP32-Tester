# Universal ESP32 Tester

A Raspberry Pi that turns into a complete remote test instrument for ESP32 devices. Plug your boards into its USB hub, and control everything — serial, WiFi, BLE, GPIO, firmware updates — over the network through a single HTTP API.

---

## Services

### 1. Remote Serial (RFC2217)

Each USB port on the Pi's hub gets a **fixed TCP port**. Plug an ESP32 into port 1 and it's always reachable at `rfc2217://pi:4001`, regardless of what `/dev/ttyUSB*` name Linux assigns. Swap boards freely — the port follows the physical connector, not the device.

Works with esptool, PlatformIO, ESP-IDF, and any pyserial-based tool. One client at a time per device.

**What happens on plug/unplug:** udev detects the event, notifies the portal, and the RFC2217 proxy starts or stops automatically. No manual intervention needed.

**ESP32 reset behavior:** The Pi can reset devices via DTR/RTS signals over the serial connection. This works differently depending on the chip:

| Chip | USB Interface | Device Node | Reset Method | Caveat |
|------|--------------|-------------|--------------|--------|
| ESP32, ESP32-S2 | External UART bridge (CP2102, CH340) | `/dev/ttyUSB*` | DTR/RTS toggle | Reliable, no issues |
| ESP32-C3, ESP32-S3 | Native USB-Serial/JTAG | `/dev/ttyACM*` | DTR/RTS toggle | Linux asserts DTR+RTS on port open, which puts the chip into **download mode** during early boot. The Pi adds a 2-second delay before opening the port to avoid this. |

**Download mode vs normal boot:** ESP32 chips use GPIO0 (active LOW) to select boot mode. If GPIO0 is held LOW during reset, the chip enters download mode (for flashing). In normal operation GPIO0 has an internal pull-up, so the chip boots normally. The UART bridge chips (CP2102) use a capacitor-based circuit to pulse GPIO0 only during the esptool handshake — this is transparent to the user.

### 2. WiFi Test Instrument

The Pi's **wlan0** radio acts as a programmable WiFi access point or station, isolated from the wired LAN on eth0.

- **AP mode** — start a SoftAP with any SSID/password. DUTs connect via DHCP, Pi is at the configured AP IP (default `192.168.4.1`). DHCP and DNS included.
- **STA mode** — join a DUT's captive portal AP as a station to test provisioning flows.
- **HTTP relay** — proxy HTTP requests through the Pi's radio to devices on its WiFi network.
- **Scan** — list nearby WiFi networks to verify a DUT's AP is broadcasting.

AP and STA are mutually exclusive — starting one stops the other.

### 3. GPIO Control

Drive Pi GPIO pins from test scripts to simulate button presses on the DUT. The most common use: **hold a pin LOW during reset** to force the DUT into a specific boot mode (captive portal, factory reset, etc.).

**Allowed pins (BCM numbering):** 5, 6, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27

**Important:** Always release pins when done by setting them to `"z"` (high-impedance input). A pin left driven LOW will prevent the DUT from booting normally.

**Standard wiring:**

| Pi GPIO (BCM) | Pin # | DUT Pin | Function |
|---------------|-------|---------|----------|
| 17 | 11 | EN/RST | Hardware reset (active LOW) |
| 18 | 12 | GPIO0 (ESP32) / GPIO9 (ESP32-C3) | Boot mode select (active LOW → download mode) |
| 27 | 13 | — | Spare 1 |
| 22 | 15 | — | Spare 2 |

**GPIO0 vs GPIO9:** Classic ESP32 uses GPIO0 for boot mode selection. ESP32-C3/S3 with native USB use GPIO9 instead. Both are active LOW — hold LOW during reset to enter download/portal mode.

Example — trigger captive portal mode without touching the board:
```
1. GPIO 18 → LOW          (hold DUT boot-select pin low)
2. GPIO 17 → LOW, wait, → "z"   (pulse EN/RST to reset DUT)
3. DUT boots with boot pin held low → enters captive portal
4. GPIO 18 → "z"          (release immediately)
```

### 4. UDP Log Receiver

Listens on **UDP port 5555** for debug log output from ESP32 devices. This is essential when the USB port is occupied (e.g., ESP32-S3 running as USB HID keyboard) and you can't use a serial monitor.

The ESP32 firmware sends `ESP_LOG` output to the Pi's IP over UDP. Logs are buffered (last 2000 lines) and available via the HTTP API, filterable by source IP and timestamp.

**ESP32 side** — point your UDP logging to `192.168.0.87:5555` (or whatever the Pi's IP is).

### 5. OTA Firmware Repository

Serves firmware binaries over HTTP so ESP32 devices can perform OTA updates from the local network. No internet or GitHub access required during development.

Upload a `.bin` file to the Pi, then point the ESP32's OTA URL to:
```
http://192.168.0.87:8080/firmware/<project-name>/<filename>.bin
```

Firmware is stored in `/var/lib/rfc2217/firmware/` organized by project subdirectory.

### 6. BLE Proxy

Uses the Pi's **onboard Bluetooth radio** to scan for, connect to, and send raw bytes to BLE peripherals. The Pi acts as a dumb BLE-to-HTTP bridge — you send hex-encoded bytes via the API, and the Pi writes them to the specified GATT characteristic.

This enables remote control of BLE devices from test scripts or AI agents. For example, sending keystrokes to an ESP32 running as a BLE-USB keyboard, or triggering OTA updates via BLE command.

**Limitation:** One BLE connection at a time (single radio).

**Prerequisite:** Bluetooth must be powered on:
```bash
sudo rfkill unblock bluetooth
sudo hciconfig hci0 up
sudo bluetoothctl power on
```

### 7. Test Automation

Two additional services support automated test workflows:

- **Test progress tracking** — push live test session updates (start, step, result, end) to the web portal. Operators see a real-time progress panel without needing a terminal.
- **Human interaction requests** — block a test script until an operator confirms a physical action (cable swap, power cycle, antenna repositioning). The web portal shows a modal with the instruction and Done/Cancel buttons.

### 8. Web Portal

A browser-based dashboard at **http://pi-ip:8080** showing:
- Serial slot status (running/empty/flapping)
- WiFi AP/STA state and connected stations
- Activity log with color-coded entries
- Test progress panel
- Human interaction modal

---

## Hardware Setup

### What You Need

| Component | Purpose |
|-----------|---------|
| **Raspberry Pi** (Zero W, 3, 4, or 5) | Runs the portal. Needs onboard WiFi + Bluetooth. |
| **USB Ethernet adapter** | Wired LAN on eth0 (wlan0 is reserved for WiFi testing) |
| **USB hub** | Connect multiple ESP32 boards (if needed) |
| **Jumper wires** (optional) | Pi GPIO → DUT GPIO for automated boot mode control |

### Network Topology

```
 LAN (192.168.0.x)
       |
       | eth0 (wired)
       v
  Raspberry Pi ---- wlan0 (WiFi test AP: 192.168.4.x)
  192.168.0.87      hci0  (Bluetooth LE)
       |             UDP :5555 (log receiver)
       | USB hub
       |
  +----+----+----+
  |    |    |    |
 :4001 :4002 :4003
 SLOT1 SLOT2 SLOT3
```

eth0 carries all management traffic (HTTP API, RFC2217 serial). wlan0 is dedicated to WiFi testing. They never overlap.

### Network Ports

| Port | Protocol | Direction | Purpose |
|------|----------|-----------|---------|
| 8080 | TCP/HTTP | Clients → Pi | Web portal, REST API, firmware downloads |
| 4001+ | TCP/RFC2217 | Clients → Pi | Serial connections (one per USB slot) |
| 5555 | UDP | ESP32 → Pi | Debug log receiver |

---

## Quick Start

### Installation

```bash
git clone https://github.com/SensorsIot/Universal-ESP32-Tester.git
cd Universal-ESP32-Tester/pi
bash install.sh
```

This installs all dependencies (pyserial, hostapd, dnsmasq, bleak, esptool), copies scripts to `/usr/local/bin/`, creates the firmware directory, and starts the portal as a systemd service.

### Slot Configuration

Discover which USB connector maps to which slot key:

```bash
rfc2217-learn-slots     # Plug in one device at a time
```

Edit the configuration:

```bash
sudo nano /etc/rfc2217/slots.json
```

```json
{
  "slots": [
    {"slot_key": "platform-3f980000.usb-usb-0:1.2:1.0", "label": "ESP32-A", "tcp_port": 4001},
    {"slot_key": "platform-3f980000.usb-usb-0:1.3:1.0", "label": "ESP32-B", "tcp_port": 4002}
  ]
}
```

Restart after editing: `sudo systemctl restart rfc2217-portal`

---

## Usage

### Serial: Flash & Monitor

```bash
# esptool
esptool --port "rfc2217://192.168.0.87:4001?ign_set_control" write_flash 0x0 firmware.bin

# ESP-IDF
export ESPPORT="rfc2217://192.168.0.87:4001?ign_set_control"
idf.py flash monitor

# Python
import serial
ser = serial.serial_for_url("rfc2217://192.168.0.87:4001?ign_set_control", baudrate=115200)
```

```ini
# PlatformIO (platformio.ini)
[env:esp32]
upload_port = rfc2217://192.168.0.87:4001?ign_set_control
monitor_port = rfc2217://192.168.0.87:4001?ign_set_control
```

### pytest Driver

```bash
pip install -e Universal-ESP32-Tester/pytest
```

```python
from wifi_tester_driver import WiFiTesterDriver as ESP32TesterDriver

ut = ESP32TesterDriver("http://192.168.0.87:8080")

# Serial
ut.serial_reset("SLOT2")
result = ut.serial_monitor("SLOT2", pattern="WiFi connected", timeout=30)

# WiFi
ut.ap_start("TestAP", "password123")
station = ut.wait_for_station(timeout=30)
resp = ut.http_get(f"http://{station['ip']}/api/status")
ut.ap_stop()

# GPIO — trigger captive portal mode
try:
    ut.gpio_set(18, 0)                   # Hold DUT boot pin LOW
    ut.gpio_set(17, 0)                   # Pull EN/RST LOW (reset)
    time.sleep(0.1)
    ut.gpio_set(17, "z")                 # Release reset — DUT boots into portal
finally:
    ut.gpio_set(18, "z")                 # Always release boot pin

# Join DUT's captive portal AP
ut.sta_join("MyDevice-Setup", timeout=15)
resp = ut.http_get(f"http://{ut.ap_ip}/")
ut.sta_leave()

# UDP logs
logs = ut.udplog(source="<DUT_IP>")
ut.udplog_clear()

# OTA firmware
ut.firmware_upload("my-project", "build/firmware.bin")
files = ut.firmware_list()
# ESP32 OTA URL: http://192.168.0.87:8080/firmware/my-project/firmware.bin

# BLE
devices = ut.ble_scan(name_filter="iOS-Keyboard")
ut.ble_connect(devices[0]["address"])
ut.ble_write("6e400002-b5a3-f393-e0a9-e50e24dcca9e", b"\x02Hello")
ut.ble_disconnect()

# Test progress
ut.test_start(spec="Firmware v2.1", phase="Integration", total=10)
ut.test_step("TC-001", "WiFi Connect", "Joining AP...")
ut.test_result("TC-001", "WiFi Connect", "PASS")
ut.test_end()
```

### OTA Firmware Update Workflow

The tester provides a complete end-to-end OTA workflow for ESP32 devices connected via its WiFi AP:

```bash
# 1. Upload firmware to the tester's OTA repository
curl -X POST http://192.168.0.87:8080/api/firmware/upload \
  -F "project=ios-keyboard" -F "file=@build/ios-keyboard.bin"

# 2. Verify the firmware is downloadable
#    (ESP32 will fetch from this URL during OTA)
curl -o /dev/null -w "%{http_code}" \
  http://192.168.0.87:8080/firmware/ios-keyboard/ios-keyboard.bin

# 3. Trigger OTA on the ESP32 via HTTP relay
#    (the ESP32 must expose a /ota endpoint and be connected to the tester's AP)
curl -X POST http://192.168.0.87:8080/api/wifi/http \
  -H "Content-Type: application/json" \
  -d '{"method":"POST","url":"http://<DUT_IP>/ota"}'

# 4. Monitor progress via UDP logs
curl http://192.168.0.87:8080/api/udplog?source=<DUT_IP>
```

The ESP32 device must:
- Be connected to the tester's WiFi AP (e.g. via `POST /api/enter-portal`)
- Have an HTTP server with a `POST /ota` endpoint that triggers `esp_ota_ops`
- Configure its OTA URL to `http://192.168.0.87:8080/firmware/<project>/<file>.bin`

The tester's HTTP relay (`POST /api/wifi/http`) bridges the gap between the LAN network and the WiFi AP network, allowing remote triggering of OTA from any client on the LAN.

### curl Examples

```bash
# Serial reset
curl -X POST http://192.168.0.87:8080/api/serial/reset \
  -H "Content-Type: application/json" -d '{"slot":"SLOT1"}'

# Start WiFi AP
curl -X POST http://192.168.0.87:8080/api/wifi/ap_start \
  -H "Content-Type: application/json" -d '{"ssid":"TestAP","password":"secret"}'

# GPIO: hold boot pin LOW, pulse reset, release
curl -X POST http://192.168.0.87:8080/api/gpio/set \
  -H "Content-Type: application/json" -d '{"pin":18,"value":0}'
curl -X POST http://192.168.0.87:8080/api/gpio/set \
  -H "Content-Type: application/json" -d '{"pin":17,"value":0}'
sleep 0.1
curl -X POST http://192.168.0.87:8080/api/gpio/set \
  -H "Content-Type: application/json" -d '{"pin":17,"value":"z"}'
curl -X POST http://192.168.0.87:8080/api/gpio/set \
  -H "Content-Type: application/json" -d '{"pin":18,"value":"z"}'

# Get UDP logs
curl http://192.168.0.87:8080/api/udplog?source=<DUT_IP>&limit=50

# Upload firmware
curl -X POST http://192.168.0.87:8080/api/firmware/upload \
  -F "project=ios-keyboard" -F "file=@build/ios-keyboard.bin"

# BLE: scan, connect, write, disconnect
curl -X POST http://192.168.0.87:8080/api/ble/scan \
  -H "Content-Type: application/json" -d '{"timeout":5,"name_filter":"iOS-Keyboard"}'
curl -X POST http://192.168.0.87:8080/api/ble/connect \
  -H "Content-Type: application/json" -d '{"address":"1C:DB:D4:84:58:CE"}'
curl -X POST http://192.168.0.87:8080/api/ble/write \
  -H "Content-Type: application/json" \
  -d '{"characteristic":"6e400002-b5a3-f393-e0a9-e50e24dcca9e","data":"0248656c6c6f"}'
curl -X POST http://192.168.0.87:8080/api/ble/disconnect
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Connection refused on serial port | Proxy not running | Check portal at :8080; verify device is plugged in |
| Timeout during flash | Network latency over RFC2217 | Use `esptool --no-stub` for reliability |
| Port busy | Another client connected | Close the other connection first (RFC2217 = 1 client) |
| ESP32-C3 stuck in download mode | DTR asserted on port open | Use `--after=watchdog-reset` with esptool, never `hard-reset` |
| DUT not connecting to AP | Wrong WiFi credentials in DUT | Verify AP is running: `curl .../api/wifi/ap_status` |
| BLE scan finds nothing | Bluetooth powered off | `sudo rfkill unblock bluetooth && sudo hciconfig hci0 up && sudo bluetoothctl power on` |
| No UDP logs appearing | ESP32 not sending to correct IP/port | Verify firmware log host is `192.168.0.87:5555` |
| Firmware download returns 404 | Wrong path or not uploaded | Check `curl .../api/firmware/list` |
| GPIO pin has no effect | Wrong BCM pin number or not wired | Verify wiring; only BCM pins in the allowlist work |

---

## API Reference

### Serial

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/devices` | List all slots with status |
| GET | `/api/info` | Pi IP, hostname, slot counts |
| POST | `/api/hotplug` | Receive udev hotplug event (internal) |
| POST | `/api/start` | Manually start proxy for a slot |
| POST | `/api/stop` | Manually stop proxy for a slot |
| POST | `/api/serial/reset` | Reset device via DTR/RTS |
| POST | `/api/serial/monitor` | Read serial output with pattern match |
| POST | `/api/enter-portal` | Connect to DUT's captive portal SoftAP, submit WiFi creds, start local AP `{"portal_ssid?", "ssid", "password?"}` |

### WiFi

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/wifi/ap_start` | Start SoftAP `{"ssid", "password?", "channel?"}` |
| POST | `/api/wifi/ap_stop` | Stop SoftAP |
| GET | `/api/wifi/ap_status` | AP status, SSID, connected stations |
| POST | `/api/wifi/sta_join` | Join a WiFi network as station `{"ssid", "password?"}` |
| POST | `/api/wifi/sta_leave` | Disconnect from WiFi network |
| GET | `/api/wifi/scan` | Scan for nearby WiFi networks |
| POST | `/api/wifi/http` | HTTP relay through Pi's radio `{"method", "url", "headers?", "body?"}` |
| GET | `/api/wifi/events` | Event queue with long-poll `?timeout=` |
| GET | `/api/wifi/mode` | Current operating mode |
| POST | `/api/wifi/mode` | Switch mode `{"mode": "wifi-testing"|"serial-interface"}` |

### GPIO

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/gpio/set` | Drive pin `{"pin": 17, "value": 0|1|"z"}` |
| GET | `/api/gpio/status` | Read state of all actively driven pins |

### UDP Log

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/udplog` | Get buffered log lines `?since=&source=&limit=` |
| DELETE | `/api/udplog` | Clear the log buffer |

### Firmware

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/firmware/<project>/<file>` | Download binary (used by ESP32 OTA client) |
| GET | `/api/firmware/list` | List all available firmware files |
| POST | `/api/firmware/upload` | Upload binary (multipart: `project` + `file`) |
| DELETE | `/api/firmware/delete` | Delete a file `{"project", "filename"}` |

### BLE

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/ble/scan` | Scan for peripherals `{"timeout?", "name_filter?"}` |
| POST | `/api/ble/connect` | Connect by address `{"address"}` |
| POST | `/api/ble/disconnect` | Disconnect current connection |
| GET | `/api/ble/status` | Connection state (`idle` / `scanning` / `connected`) |
| POST | `/api/ble/write` | Write hex bytes `{"characteristic", "data", "response?"}` |

### Test / Other

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/test/update` | Push test session start/step/result/end |
| GET | `/api/test/progress` | Poll current test session state |
| POST | `/api/human-interaction` | Block until operator confirms `{"message", "timeout?"}` |
| GET | `/api/human/status` | Check if a human interaction is pending |
| POST | `/api/human/done` | Confirm the pending interaction |
| POST | `/api/human/cancel` | Cancel the pending interaction |
| GET | `/api/log` | Activity log `?since=` |

---

## Project Structure

```
pi/
  portal.py                  Main HTTP server, proxy supervisor, all API endpoints
  wifi_controller.py         WiFi AP/STA/scan/relay backend
  ble_controller.py          BLE scan/connect/write backend (bleak)
  plain_rfc2217_server.py    RFC2217 serial proxy with DTR/RTS passthrough
  install.sh                 One-command installer
  rfc2217-learn-slots        Slot discovery helper
  config/slots.json          USB slot → TCP port mapping
  scripts/                   udev and dnsmasq callback scripts
  udev/                      Hotplug rules
  systemd/                   Service unit file

pytest/
  wifi_tester_driver.py      Python test driver (WiFiTesterDriver class)
  conftest.py                Fixtures and CLI options
  test_instrument.py         Self-tests for the instrument

docs/
  Universal-ESP32-Tester-FSD.md  Full functional specification
```

---

## License

MIT
