---
name: esp32-test-harness
description: Manipulate ESP32 DUT during automated tests using the Serial Portal and WiFi Tester infrastructure. Covers serial reset/monitor, NVS erase, captive portal triggering, and WiFi AP provisioning. Use when running tests, resetting the DUT, entering captive portal, provisioning WiFi, or monitoring serial output. Triggers on "test harness", "reset DUT", "captive portal test", "provision WiFi", "NVS erase", "clean state", "test setup".
---

# ESP32 Test Harness

How to manipulate the ESP32-C3 DUT during automated tests using the Serial Portal (192.168.0.87) and WiFi Tester infrastructure. The DUT is connected via USB to the Pi's SLOT2.

**Golden rule:** The Serial Portal and MQTT broker are always-on infrastructure. Tests NEVER start, stop, or restart them.

---

## Infrastructure

| Component | Address | Role |
|-----------|---------|------|
| Serial Portal | 192.168.0.87:8080 | RFC2217 serial proxy, WiFi/Serial API |
| DUT serial | `rfc2217://192.168.0.87:4002` | SLOT2 (ESP32-C3, ttyACM, plain RFC2217) |
| DUT WiFi (production) | 192.168.0.177 | DUT on production network |
| DUT WiFi (portal) | 192.168.4.1 | DUT in captive portal AP mode |
| MQTT broker | 192.168.0.203:1883 | Mosquitto |
| Serial Service API | 192.168.0.87:8080/api/serial/* | Reset and monitor via HTTP |
| WiFi Service API | 192.168.0.87:8080/api/wifi/* | Control test AP via HTTP |

---

## 0. State Detection (ALWAYS check serial first)

**Serial is the lifeline.** Never rely on WiFi/HTTP to check if the C3 is running — WiFi may not be up. Always check serial first.

### 0.1 Read serial output

**Preferred — Serial Monitor API (FR-009):**

```bash
curl -s -X POST http://192.168.0.87:8080/api/serial/monitor \
    -H "Content-Type: application/json" \
    -d '{"slot": "SLOT2", "timeout": 5}'
# Returns: {"ok": true, "matched": false, "line": null, "output": ["line1", ...]}

# With pattern matching:
curl -s -X POST http://192.168.0.87:8080/api/serial/monitor \
    -H "Content-Type: application/json" \
    -d '{"slot": "SLOT2", "pattern": "Boot count", "timeout": 10}'
# Returns: {"ok": true, "matched": true, "line": "Boot count: 1", "output": [...]}
```

**Fallback — direct pyserial via RFC2217:**

**IMPORTANT:** Always use `do_not_open=True` and set `dtr=False, rts=False` before opening:

```python
import serial, time
ser = serial.serial_for_url('rfc2217://192.168.0.87:4002', do_not_open=True)
ser.baudrate = 115200
ser.timeout = 2
ser.dtr = False   # Prevents download mode on C3
ser.rts = False   # Prevents reset
ser.open()
deadline = time.time() + 5
while time.time() < deadline:
    data = ser.read(1024)
    if data:
        print(data.decode('utf-8', errors='replace'), end='', flush=True)
ser.close()
```

### 0.2 Detect state from boot output

| Serial output | State | Action needed |
|--------------|-------|---------------|
| `boot:0x7 (DOWNLOAD...)` + `waiting for download` | **Download mode** | Run esptool with `--after=watchdog-reset` to recover |
| `boot:0xc (SPI_FAST_FLASH_BOOT)` + app messages | **Running** | Normal — check WiFi/HTTP if needed |
| No output at all | **Unknown** | Firmware may lack serial debug, or proxy has stale fd. Reflash. |

---

## 1. Serial Operations

### 1.1 Reset DUT (normal boot)

**Preferred — Serial Reset API (FR-008):**

Stops the proxy, opens direct serial, sends DTR/RTS reset pulse, captures boot
output, then restarts the proxy automatically.

```bash
curl -s -X POST http://192.168.0.87:8080/api/serial/reset \
    -H "Content-Type: application/json" \
    -d '{"slot": "SLOT2"}'
# Returns: {"ok": true, "output": ["ESP-ROM:esp32c3-api1-20210207", "Boot count: 1", ...]}
```

**Fallback — direct pyserial via RFC2217:**

Reset via DTR/RTS through RFC2217. The portal uses `plain_rfc2217_server` which
passes DTR/RTS directly to the serial device.

```python
import serial, time

ser = serial.serial_for_url('rfc2217://192.168.0.87:4002', do_not_open=True)
ser.baudrate = 115200
ser.timeout = 2
ser.dtr = False
ser.rts = False
ser.open()
time.sleep(0.1)

# USBJTAGSerialReset sequence (same as esptool --before=usb-reset)
ser.dtr = False; ser.rts = False
ser.dtr = True;  ser.rts = False
time.sleep(0.1)
ser.dtr = False; ser.rts = True
time.sleep(0.1)
ser.rts = False

# Read boot output on the SAME connection
time.sleep(0.5)
deadline = time.time() + 10
while time.time() < deadline:
    data = ser.read(1024)
    if data:
        print(data.decode('utf-8', errors='replace'), end='', flush=True)
ser.close()
```

### 1.2 Flash via RFC2217

Flashing works via RFC2217 for both chip types through `plain_rfc2217_server`.
No SSH to Pi needed.

**ESP32-C3 (native USB):**
```bash
python3 -m esptool --chip esp32c3 \
    --port "rfc2217://192.168.0.87:4002" \
    --before=usb-reset --after=watchdog-reset \
    write_flash 0x10000 firmware.bin
```

**Classic ESP32 (UART bridge):**
```bash
python3 -m esptool --chip esp32 \
    --port "rfc2217://192.168.0.87:4002" \
    --before=default-reset --after=hard-reset \
    write_flash 0x10000 firmware.bin
```

**Note:** A harmless RFC2217 parameter negotiation error may appear at the end —
the flash and reset still complete successfully.

### 1.3 Monitor serial output

**Preferred — Serial Monitor API (FR-009):**

```bash
# Read for 5s, no pattern matching
curl -s -X POST http://192.168.0.87:8080/api/serial/monitor \
    -H "Content-Type: application/json" \
    -d '{"slot": "SLOT2", "timeout": 5}'

# Wait for specific pattern (returns immediately on match)
curl -s -X POST http://192.168.0.87:8080/api/serial/monitor \
    -H "Content-Type: application/json" \
    -d '{"slot": "SLOT2", "pattern": "WiFi connected", "timeout": 30}'
```

**Fallback — direct pyserial via RFC2217:**

```python
import serial, time

ser = serial.serial_for_url('rfc2217://192.168.0.87:4002', do_not_open=True)
ser.baudrate = 115200
ser.timeout = 2
ser.dtr = False
ser.rts = False
ser.open()
deadline = time.time() + 30
while time.time() < deadline:
    data = ser.read(1024)
    if data:
        print(data.decode('utf-8', errors='replace'), end='', flush=True)
ser.close()
```

**Tip:** The serial reset API (1.1) captures boot output in one call. For
separate reset + monitor, use the API endpoints sequentially.

### 1.4 Known issue: C3 stuck in download mode

If the C3 gets stuck in download mode (serial shows `waiting for download`):

```bash
python3 -m esptool --chip esp32c3 \
    --port "rfc2217://192.168.0.87:4002" \
    --before=usb-reset --after=watchdog-reset chip_id
```

The `--after=watchdog-reset` triggers a system reset that re-samples GPIO9,
returning the chip to SPI boot mode. Do NOT use `--after=hard-reset` — that
only does a core reset which stays in download mode.

---

## 2. NVS Erase (Clean State)

Erase the NVS partition to reset all configuration to compiled defaults:

```bash
python3 -m esptool --chip esp32c3 \
    --port "rfc2217://192.168.0.87:4002" \
    --before=usb-reset --after=watchdog-reset \
    erase_region 0x9000 0x5000
```

After erase, the DUT resets and boots with:
- WiFi: `private-2G` (from credentials.h)
- MQTT: 192.168.0.203:1883 (compiled default)
- Boot count: 0
- Debug mode: off
- Wallbox topic: `wallbox`
- Log level: 1 (INFO)

### Full clean slate (flash + NVS erase)

```bash
PORT="rfc2217://192.168.0.87:4002"
BUILD="Modbus_Proxy/.pio/build/esp32-c3-debug"

# Flash firmware
python3 -m esptool --chip esp32c3 --port $PORT \
    --before=usb-reset --after=watchdog-reset \
    write_flash 0x10000 $BUILD/firmware.bin

# Erase NVS
python3 -m esptool --chip esp32c3 --port $PORT \
    --before=usb-reset --after=watchdog-reset \
    erase_region 0x9000 0x5000
```

### Verify clean state

```bash
# Wait for boot, then check
sleep 15
curl -s http://192.168.0.177/api/status | python3 -m json.tool
# Expect: wifi_ssid=private-2G, mqtt_connected=true, debug_mode=false
```

---

## 3. Captive Portal

The DUT enters captive portal mode after 3 consecutive failed WiFi boot attempts. Portal broadcasts AP SSID `MODBUS-Proxy-Setup` on 192.168.4.1.

### 3.1 Trigger captive portal (via enter-portal API)

**Preferred — use the enter-portal composite API:**

```bash
curl -s -X POST http://192.168.0.87:8080/api/enter-portal \
    -H "Content-Type: application/json" \
    -d '{"slot": "SLOT2"}'
# Runs in background — monitor via activity log:
curl -s http://192.168.0.87:8080/api/log
```

This performs 3 rapid serial resets to trigger the boot counter threshold,
then verifies "PORTAL mode" appears in serial output. Progress is logged to
the activity log.

### 3.1b Trigger captive portal (manual, from clean state)

Provision the DUT with credentials for a non-existent AP, then wait for 3 failed boot cycles:

```python
import requests, time

DUT = "http://192.168.0.177"
WIFI_CONNECT_TIMEOUT = 30  # DUT's WiFi timeout per boot
BOOT_OVERHEAD = 5
PORTAL_THRESHOLD = 3

# Set WiFi to a non-existent SSID (DUT will reboot)
requests.post(f"{DUT}/api/wifi", json={"ssid": "NONEXISTENT", "password": "x"}, timeout=5)

# Wait for 3 failed boot cycles
wait = (WIFI_CONNECT_TIMEOUT + BOOT_OVERHEAD) * PORTAL_THRESHOLD + 10
print(f"Waiting {wait}s for portal activation...")
time.sleep(wait)

# DUT should now be in portal mode on 192.168.4.1
```

### 3.2 Trigger captive portal (from clean state, via WiFi Tester)

If the DUT is not reachable on the production network, use the WiFi Tester to set up an AP, provision the DUT onto it, then take the AP down:

```bash
# Start a temporary test AP
curl -s -X POST http://192.168.0.87:8080/api/wifi/ap_start \
    -H "Content-Type: application/json" \
    -d '{"ssid": "TEMP-AP", "pass": "temppass123"}'

# Provision DUT to use this AP (via production network)
curl -s -X POST http://192.168.0.177/api/wifi \
    -H "Content-Type: application/json" \
    -d '{"ssid": "TEMP-AP", "password": "temppass123"}'

# Wait for DUT to reboot and connect to TEMP-AP
sleep 20

# Stop the AP — DUT will fail on next boot
curl -s -X POST http://192.168.0.87:8080/api/wifi/ap_stop

# Wait for 3 failed boot cycles (~105s)
sleep 110

# Verify portal is active
curl -s http://192.168.0.87:8080/api/wifi/scan
# Look for "MODBUS-Proxy-Setup" in the network list
```

### 3.3 Interact with captive portal (via WiFi Tester)

The WiFi Tester can join the DUT's portal AP and relay HTTP requests:

```bash
# Join the portal AP
curl -s -X POST http://192.168.0.87:8080/api/wifi/sta_join \
    -H "Content-Type: application/json" \
    -d '{"ssid": "MODBUS-Proxy-Setup", "pass": ""}'

# Access portal page (via HTTP relay)
curl -s -X POST http://192.168.0.87:8080/api/wifi/http \
    -H "Content-Type: application/json" \
    -d '{"method": "GET", "url": "http://192.168.4.1/"}'

# Scan for networks from portal
curl -s -X POST http://192.168.0.87:8080/api/wifi/http \
    -H "Content-Type: application/json" \
    -d '{"method": "GET", "url": "http://192.168.4.1/api/scan"}'

# Submit WiFi credentials through portal
curl -s -X POST http://192.168.0.87:8080/api/wifi/http \
    -H "Content-Type: application/json" \
    -d '{"method": "POST", "url": "http://192.168.4.1/api/wifi", "body": "eyJzc2lkIjoicHJpdmF0ZS0yRyIsInBhc3N3b3JkIjoiRGlzX2E0NDE1In0=", "headers": {"Content-Type": "application/json"}}'
# Note: body is base64-encoded JSON: {"ssid":"private-2G","password":"Dis_a4415"}

# Leave portal AP
curl -s -X POST http://192.168.0.87:8080/api/wifi/sta_leave
```

### 3.4 Restore DUT from portal mode

Option A — Submit production WiFi credentials via portal (see 3.3 above).

Option B — Erase NVS via serial (portal mode doesn't block serial access):
```bash
python3 esptool.py --port "rfc2217://192.168.0.87:4002" --chip esp32c3 \
    --baud 921600 erase_region 0x9000 0x5000
# DUT resets, boots with credentials.h fallback, connects to private-2G
```

Option C — Wait for portal timeout (5 minutes), DUT reboots automatically.

---

## 4. WiFi AP Management (WiFi Tester)

The WiFi Tester is a Pi wlan0 interface controlled via the Serial Portal's HTTP API. It can act as an AP or join other APs.

### 4.1 Check mode

```bash
curl -s http://192.168.0.87:8080/api/wifi/mode
# Returns: {"ok": true, "mode": "wifi-testing"} or "serial-interface"
```

Mode must be `wifi-testing` to use the AP/STA functions.

### 4.2 Start a test AP

```bash
curl -s -X POST http://192.168.0.87:8080/api/wifi/ap_start \
    -H "Content-Type: application/json" \
    -d '{"ssid": "TEST-NET", "pass": "testpass123", "channel": 6}'
```

### 4.3 Stop test AP

```bash
curl -s -X POST http://192.168.0.87:8080/api/wifi/ap_stop
```

### 4.4 Check AP status

```bash
curl -s http://192.168.0.87:8080/api/wifi/ap_status
# Returns: {"ok": true, "active": true, "ssid": "TEST-NET", "channel": 6, "stations": [...]}
```

### 4.5 Scan for networks

```bash
curl -s http://192.168.0.87:8080/api/wifi/scan
# Returns: {"ok": true, "networks": [{"ssid": "...", "rssi": -45, "auth": "WPA2"}, ...]}
```

### 4.6 Join an AP as station (for portal access)

```bash
curl -s -X POST http://192.168.0.87:8080/api/wifi/sta_join \
    -H "Content-Type: application/json" \
    -d '{"ssid": "MODBUS-Proxy-Setup", "pass": "", "timeout": 15}'
```

### 4.7 Leave AP

```bash
curl -s -X POST http://192.168.0.87:8080/api/wifi/sta_leave
```

### 4.8 HTTP relay (reach DUT on isolated network)

When the WiFi Tester is joined to the DUT's AP (portal or test AP), use the relay to reach the DUT:

```bash
# GET request
curl -s -X POST http://192.168.0.87:8080/api/wifi/http \
    -H "Content-Type: application/json" \
    -d '{"method": "GET", "url": "http://192.168.4.1/api/status", "timeout": 10}'

# POST request (body must be base64-encoded)
curl -s -X POST http://192.168.0.87:8080/api/wifi/http \
    -H "Content-Type: application/json" \
    -d '{"method": "POST", "url": "http://192.168.4.1/api/wifi", "body": "<base64>", "headers": {"Content-Type": "application/json"}}'
```

### 4.9 Wait for DUT to connect to test AP

```bash
# Poll station events (long-poll, timeout in seconds)
curl -s "http://192.168.0.87:8080/api/wifi/events?timeout=30"
# Returns: {"ok": true, "events": [{"type": "join", "mac": "...", "ip": "192.168.4.2"}]}
```

---

## 5. Common Test Workflows

### 5.1 Clean slate then run integration tests

```bash
# 1. Flash and erase NVS
$ESPTOOL --port $PORT --chip esp32c3 --baud 921600 \
    write_flash 0x0 $BUILD/bootloader.bin 0x8000 $BUILD/partitions.bin 0x10000 $BUILD/firmware.bin
$ESPTOOL --port $PORT --chip esp32c3 --baud 921600 erase_region 0x9000 0x5000

# 2. Wait for DUT to boot and connect
sleep 15
curl -s http://192.168.0.177/api/status  # verify reachable

# 3. Run tests
pytest test/integration/ -v
```

### 5.2 Captive portal test cycle

```bash
# 1. Start from clean state (DUT on production WiFi)
# 2. Provision DUT with non-existent SSID
curl -s -X POST http://192.168.0.177/api/wifi -H "Content-Type: application/json" \
    -d '{"ssid": "NONEXISTENT", "password": "x"}'
# 3. Wait for portal activation (~110s)
sleep 110
# 4. Verify portal AP visible
curl -s http://192.168.0.87:8080/api/wifi/scan | grep MODBUS-Proxy-Setup
# 5. Run portal tests via WiFi Tester relay
# 6. Restore: erase NVS via serial
$ESPTOOL --port $PORT --chip esp32c3 --baud 921600 erase_region 0x9000 0x5000
# 7. DUT boots back to production WiFi
```

### 5.3 WiFi disconnect test cycle

```bash
# 1. Start test AP, provision DUT onto it
curl -s -X POST http://192.168.0.87:8080/api/wifi/ap_start \
    -H "Content-Type: application/json" -d '{"ssid": "TEST-DROP", "pass": "test123"}'
curl -s -X POST http://192.168.0.177/api/wifi \
    -H "Content-Type: application/json" -d '{"ssid": "TEST-DROP", "password": "test123"}'
sleep 20

# 2. Drop the AP
curl -s -X POST http://192.168.0.87:8080/api/wifi/ap_stop
sleep 5  # DUT loses WiFi

# 3. Bring AP back
curl -s -X POST http://192.168.0.87:8080/api/wifi/ap_start \
    -H "Content-Type: application/json" -d '{"ssid": "TEST-DROP", "pass": "test123"}'

# 4. Wait for reconnection, verify via events
curl -s "http://192.168.0.87:8080/api/wifi/events?timeout=30"

# 5. Restore: erase NVS
$ESPTOOL --port $PORT --chip esp32c3 --baud 921600 erase_region 0x9000 0x5000
```
