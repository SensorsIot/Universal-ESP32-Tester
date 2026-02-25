---
name: esp32-test-harness
description: Manipulate ESP32 DUT during automated tests using the Serial Portal and WiFi Tester infrastructure. Covers serial reset/monitor, NVS erase, captive portal triggering, and WiFi AP provisioning. Use when running tests, resetting the DUT, entering captive portal, provisioning WiFi, or monitoring serial output. Triggers on "test harness", "reset DUT", "captive portal test", "provision WiFi", "NVS erase", "clean state", "test setup".
---

# ESP32 Test Harness

How to manipulate the ESP32-C3 DUT during automated tests using the Serial Portal (192.168.0.87) and WiFi Tester infrastructure.

**Golden rule:** The Serial Portal and MQTT broker are always-on infrastructure. Tests NEVER start, stop, or restart them.

**Driver rule:** Always use `WiFiTesterDriver` from Python — never raw curl. This gives typed responses, proper error handling, and access to the slot `state` field.

---

## Infrastructure

| Component | Address | Role |
|-----------|---------|------|
| Serial Portal | 192.168.0.87:8080 | RFC2217 serial proxy, WiFi/Serial API |
| DUT WiFi (test AP) | DHCP-assigned on AP subnet | DUT on WiFi Tester AP |
| DUT WiFi (portal) | DUT's SoftAP gateway IP | DUT in captive portal AP mode |
| MQTT broker | site-specific | Mosquitto (on home network only) |

Slots are tied to physical USB connectors on the Pi, not to devices. **Always discover the DUT slot at runtime** using `wt.get_devices()` — never hardcode a slot label or port number.

---

## 0. WiFi Tester Driver Setup

All test operations use `WiFiTesterDriver`. Set `PYTHONPATH` to import it:

```python
import sys
sys.path.insert(0, "/tmp/Universal-ESP32-Tester/pytest")
from wifi_tester_driver import WiFiTesterDriver

wt = WiFiTesterDriver("http://192.168.0.87:8080")
```

Or from bash one-liners:

```bash
PYTHONPATH=/tmp/Universal-ESP32-Tester/pytest python3 -c "
from wifi_tester_driver import WiFiTesterDriver
wt = WiFiTesterDriver('http://192.168.0.87:8080')
# ... operations ...
"
```

### Discover DUT Slot

```python
# Find which slot has a device present
devices = wt.get_devices()
dut = next(s for s in devices if s["present"])
SLOT = dut["label"]       # e.g. "SLOT1", "SLOT2", "SLOT3"
PORT = dut["url"]         # e.g. "$PORT"
```

### Driver Methods Reference

**Slot state & devices:**
```python
wt.get_devices()                          # list[dict] — all slots
wt.get_slot(SLOT)                         # dict — single slot by label
wt.wait_for_state(SLOT, "idle", timeout=30)  # poll until state matches
```

**Serial operations:**
```python
wt.serial_reset(SLOT)                     # dict — reset DUT, returns boot output
wt.serial_monitor(SLOT, pattern="WiFi connected", timeout=15)  # dict — wait for pattern
wt.enter_portal(SLOT, resets=3)            # dict — trigger captive portal
```

**WiFi management:**
```python
wt.get_mode()                              # dict — {"mode": "wifi-testing"}
wt.ap_start("TestAP-Modbus", "test12345")  # dict — start test AP
wt.ap_stop()                               # None
wt.ap_status()                             # dict — active, ssid, stations
wt.sta_join("MODBUS-Proxy-Setup", "modbus-setup", timeout=15)  # dict — join AP
wt.sta_leave()                             # None
wt.scan()                                  # dict — nearby networks
```

**HTTP relay (reach DUT on isolated network):**
```python
wt.http_get(f"http://{dut_ip}/api/status")             # Response
wt.http_post(f"http://{dut_ip}/api/wifi",
             json_data={"ssid": "TestAP-Modbus", "password": "test12345"})  # Response
```

**Human interaction (for physical actions — button presses, cable changes, power cycles):**
```python
wt.human_interaction("Connect the USB cable and click Done", timeout=60)  # bool — blocks until Done/Cancel
```

**Activity log:**
```python
wt.get_log()                               # list[dict] — all entries
wt.get_log(since="2026-02-08T12:00:00")    # list[dict] — entries since timestamp
```

---

## 1. Slot States

Each slot has an explicit `state` field visible in `get_slot()` and `get_devices()`:

| State | Meaning |
|-------|---------|
| `absent` | No device plugged into this USB slot |
| `idle` | Device present, proxy not running (available for operations) |
| `resetting` | Serial reset or enter-portal in progress |
| `monitoring` | Serial monitor capturing output |
| `flapping` | Device hotplug flapping detected |

### Check state

```python
slot = wt.get_slot(SLOT)
print(f"State: {slot['state']}, Present: {slot['present']}")
```

### Wait for state transition

```python
# Wait for reset to complete
wt.wait_for_state(SLOT, "idle", timeout=30)
```

---

## 2. Serial Operations

### 2.1 Reset DUT (normal boot)

```python
result = wt.serial_reset(SLOT)
print(result["output"])  # list of boot output lines
```

The reset API stops the proxy, opens direct serial, sends DTR/RTS reset pulse, captures boot output, then restarts the proxy. Slot state goes `idle` → `resetting` → `idle`.

### 2.2 Monitor serial output

```python
# Read for 5s, no pattern matching
result = wt.serial_monitor(SLOT, timeout=5)
print(result["output"])

# Wait for specific pattern (returns immediately on match)
result = wt.serial_monitor(SLOT, pattern="WiFi connected", timeout=30)
if result["matched"]:
    print(f"Found: {result['line']}")
```

### 2.3 Flash via RFC2217

Flashing uses esptool directly (not through the driver). Get `PORT` from `wt.get_slot()["url"]`:

```bash
# PORT from driver discovery, e.g. wt.get_slot()["url"]

# ESP32-C3 (native USB)
python3 -m esptool --chip esp32c3 \
    --port "$PORT" \
    --before=usb-reset --after=watchdog-reset \
    write_flash 0x10000 firmware.bin

# Full flash (bootloader + partitions + firmware)
python3 -m esptool --chip esp32c3 \
    --port "$PORT" \
    --baud 921600 --before=usb-reset --after=watchdog-reset \
    write_flash --flash_mode dio --flash_size 4MB \
    0x0000 bootloader.bin 0x8000 partitions.bin 0x10000 firmware.bin
```

### 2.4 Known issue: C3 stuck in download mode

```bash
python3 -m esptool --chip esp32c3 \
    --port "$PORT" \
    --before=usb-reset --after=watchdog-reset chip_id
```

Use `--after=watchdog-reset` (NOT `hard-reset`) — system reset re-samples GPIO9.

---

## 3. NVS Erase (Clean State)

```bash
python3 -m esptool --chip esp32c3 \
    --port "$PORT" \
    --before=usb-reset --after=watchdog-reset \
    erase_region 0x9000 0x5000
```

After erase, the DUT resets and boots with:
- WiFi: `private-2G` (from credentials.h) — **only for initial setup, not for tests**
- MQTT: site-specific broker (compiled default)
- Boot count: 0
- Debug mode: off

---

## 4. Captive Portal

### 4.1 Trigger captive portal (GPIO 2 button)

Firmware v1.2.0+ uses a physical GPIO 2 button held during boot to enter captive portal. This requires a human operator. The `human_interaction()` method displays a popup on the Pi's web UI and blocks (event-driven, no polling) until the operator clicks Done.

```python
import threading

# Ask human to hold GPIO 2 button (blocks until Done clicked on Pi UI)
# Run in a thread so we can monitor serial in parallel
human = threading.Thread(target=wt.human_interaction,
    args=("Hold GPIO 2 button on DUT, then click Done",),
    kwargs={"timeout": 60})
human.start()

# Reset DUT while human holds button
wt.serial_reset(SLOT)

# Monitor serial for portal activation
result = wt.serial_monitor(SLOT, pattern="CAPTIVE PORTAL MODE TRIGGERED", timeout=15)
assert result["matched"], "Portal mode not triggered"

# Wait for human to click Done
human.join()
```

**Legacy method** (firmware < v1.2.0, boot counter):
```python
# Triggers 3 rapid serial resets (runs in background on Pi)
result = wt.enter_portal(SLOT, resets=3)
wt.wait_for_state(SLOT, "idle", timeout=30)
```

### 4.2 Interact with captive portal (via WiFi Tester)

```python
# Join the portal AP
wt.sta_join("MODBUS-Proxy-Setup", "modbus-setup", timeout=15)

# Access portal page
resp = wt.http_get(f"http://{dut_portal_ip}/")
print(f"Status: {resp.status_code}, Body: {resp.text[:200]}")

# Scan for networks from portal
resp = wt.http_get(f"http://{dut_portal_ip}/api/scan")
print(resp.json())

# Submit WiFi credentials through portal
resp = wt.http_post(f"http://{dut_portal_ip}/api/wifi",
                     json_data={"ssid": "TestAP-Modbus", "password": "test12345"})
print(resp.json())

# Leave portal AP
wt.sta_leave()
```

### 4.3 Restore DUT from portal mode

**Option A** — Submit WiFi credentials via portal (see 4.2).

**Option B** — Erase NVS via serial (portal doesn't block serial):
```bash
python3 -m esptool --chip esp32c3 \
    --port "$PORT" \
    --before=usb-reset --after=watchdog-reset \
    erase_region 0x9000 0x5000
```

**Option C** — Wait for portal timeout (5 minutes), DUT reboots automatically.

---

## 5. WiFi AP Management

### 5.1 Start test AP

```python
result = wt.ap_start("TestAP-Modbus", "test12345")
print(f"AP IP: {result['ip']}")
```

### 5.2 Check AP status and connected stations

```python
status = wt.ap_status()
print(f"Active: {status['active']}, SSID: {status['ssid']}")
for sta in status.get("stations", []):
    print(f"  Station: {sta['mac']} @ {sta['ip']}")
```

### 5.3 Stop test AP

```python
wt.ap_stop()
```

### 5.4 Wait for DUT to connect to test AP

```python
# Start AP then wait for DUT station event
wt.ap_start("TestAP-Modbus", "test12345")
evt = wt.wait_for_station(timeout=30)
print(f"DUT connected: {evt}")
```

### 5.5 HTTP relay to DUT on test AP

When DUT is on the WiFi Tester's AP, use relay (DUT IP from `wt.wait_for_station()`):

```python
# GET
resp = wt.http_get(f"http://{dut_ip}/api/status")
status = resp.json()
print(f"FW: {status['fw_version']}, Heap: {status['free_heap']}")

# POST
resp = wt.http_post(f"http://{dut_ip}/api/debug",
                     json_data={"enabled": True})
```

---

## 6. Common Test Workflows

### 6.1 Clean slate then verify

```python
wt = WiFiTesterDriver("http://192.168.0.87:8080")

# Flash + erase NVS (via bash/esptool)
# Then verify via driver:
slot = wt.get_slot(SLOT)
assert slot["state"] == "idle"
assert slot["present"] is True
```

### 6.2 Captive portal test cycle (GPIO 2 button)

```python
import threading
wt = WiFiTesterDriver("http://192.168.0.87:8080")

# 1. Trigger portal via GPIO 2 button (human holds button)
human = threading.Thread(target=wt.human_interaction,
    args=("Hold GPIO 2 button on DUT, then click Done",),
    kwargs={"timeout": 60})
human.start()
wt.serial_reset(SLOT)
result = wt.serial_monitor(SLOT, pattern="CAPTIVE PORTAL MODE TRIGGERED", timeout=15)
assert result["matched"]
human.join()

# 2. Join portal AP
wt.sta_join("MODBUS-Proxy-Setup", "modbus-setup", timeout=15)

# 3. Test portal page
resp = wt.http_get(f"http://{dut_portal_ip}/")
assert resp.status_code == 200

# 4. Submit credentials
resp = wt.http_post(f"http://{dut_portal_ip}/api/wifi",
                     json_data={"ssid": "TestAP-Modbus", "password": "test12345"})
wt.sta_leave()

# 5. Start test AP and wait for DUT
wt.ap_start("TestAP-Modbus", "test12345")
evt = wt.wait_for_station(timeout=30)
```

### 6.3 WiFi disconnect test cycle

```python
wt = WiFiTesterDriver("http://192.168.0.87:8080")

# 1. DUT on test AP
wt.ap_start("TestAP-Modbus", "test12345")
# (DUT connects via NVS creds)

# 2. Drop the AP
wt.ap_stop()
import time; time.sleep(5)

# 3. Bring AP back
wt.ap_start("TestAP-Modbus", "test12345")

# 4. Wait for reconnection
evt = wt.wait_for_station(timeout=30)
print(f"Reconnected: {evt}")
```

### 6.4 Reset DUT and verify normal boot

```python
wt = WiFiTesterDriver("http://192.168.0.87:8080")

# Single reset (GPIO 2 not pressed → normal boot)
wt.serial_reset(SLOT)

# Verify normal boot via serial
result = wt.serial_monitor(SLOT, pattern="WiFi connected", timeout=30)
assert result["matched"]
```

---

## 7. State Detection (Serial Lifeline)

**Serial is the lifeline.** Never rely on WiFi/HTTP to check if the C3 is running — WiFi may not be up.

### 7.1 Detect state from serial monitor

```python
result = wt.serial_monitor(SLOT, timeout=5)
output = "\n".join(result.get("output", []))

if "waiting for download" in output:
    print("DOWNLOAD MODE — recover with esptool --after=watchdog-reset")
elif "SPI_FAST_FLASH_BOOT" in output:
    print("RUNNING — normal boot")
else:
    print("UNKNOWN — may need reflash")
```

### 7.2 State table

| Serial output | State | Action needed |
|--------------|-------|---------------|
| `boot:0x7 (DOWNLOAD...)` + `waiting for download` | **Download mode** | Run esptool with `--after=watchdog-reset` |
| `boot:0xc (SPI_FAST_FLASH_BOOT)` + app messages | **Running** | Normal |
| No output at all | **Unknown** | Reflash firmware |

### 7.3 Direct pyserial via RFC2217 (fallback only)

Only use when the driver API is insufficient:

```python
import serial, time
ser = serial.serial_for_url(PORT, do_not_open=True)  # PORT from wt.get_slot()
ser.baudrate = 115200
ser.timeout = 2
ser.dtr = False   # CRITICAL: prevents download mode on C3
ser.rts = False   # CRITICAL: prevents reset
ser.open()
deadline = time.time() + 5
while time.time() < deadline:
    data = ser.read(1024)
    if data:
        print(data.decode('utf-8', errors='replace'), end='', flush=True)
ser.close()
```
