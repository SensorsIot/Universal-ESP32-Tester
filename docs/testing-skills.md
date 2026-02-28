# Testing Workbench Skills

The `test-firmware/` directory contains a generic ESP-IDF firmware that exercises
all workbench infrastructure without any project-specific logic. Use it to
validate that workbench skills work correctly after making changes to the
workbench software or skills.

## Building

Requires ESP-IDF v5.x (tested with 5.1+).

```bash
cd test-firmware
idf.py set-target esp32s3    # or esp32, esp32c3
idf.py build
```

The binary lands at `build/wb-test-firmware.bin`.

## Flashing

Upload to the workbench and flash via RFC2217:

```bash
# Upload binary for OTA (optional, needed for OTA test)
curl -F "file=@build/wb-test-firmware.bin" \
     "http://192.168.0.87:8080/api/firmware/upload?project=test-firmware&filename=wb-test-firmware.bin"

# Flash via serial
esptool.py --port rfc2217://192.168.0.87:4001?ign_set_control \
           --chip esp32s3 --baud 460800 \
           write_flash @flash_args
```

Or use the `esp32-workbench-serial-flashing` skill.

## What the Firmware Does

| Module | What it exercises |
|--------|-------------------|
| `udp_log.c` | UDP log forwarding to `192.168.0.87:5555` |
| `wifi_prov.c` | SoftAP captive portal (`WB-Test-Setup`), STA mode with stored creds |
| `ble_nus.c` | BLE advertisement as `WB-Test`, NUS service |
| `ota_update.c` | HTTP OTA from workbench firmware server |
| `http_server.c` | `/status`, `/ota`, `/wifi-reset` endpoints |
| `nvs_store.c` | WiFi credential persistence in NVS (`wb_test` namespace) |
| Heartbeat task | Periodic log line confirming firmware is alive |

## Skill Validation Matrix

Each workbench skill maps to specific test steps using the firmware:

| Skill | Test steps | What confirms it works |
|-------|-----------|----------------------|
| `esp32-workbench-serial-flashing` | Flash the firmware via RFC2217 | Serial monitor shows `"=== Workbench Test Firmware"` after reboot |
| `esp32-workbench-logging` | Start serial monitor; check UDP logs | Serial shows boot output; `GET /api/udplog` returns heartbeat lines |
| `esp32-workbench-wifi` | Run `enter-portal` with device in AP mode | Serial shows `"STA got IP"`, device joins workbench network |
| `esp32-workbench-ble` | Scan for `WB-Test`, connect, discover services | BLE scan finds device; NUS service UUID appears in characteristics |
| `esp32-workbench-ota` | Upload binary, trigger OTA via HTTP `/ota` | Serial shows `"OTA succeeded"`, device reboots with new firmware |
| `esp32-workbench-gpio` | Toggle EN pin to reset device | Serial monitor shows fresh boot output |
| `esp32-workbench-serial-flashing` | Trigger flapping, verify detection and recovery | Flapping detected, auto-recovery succeeds, device returns to idle |
| `esp32-workbench-mqtt` | Start broker, verify device can reach `192.168.4.1:1883` | (Firmware doesn't use MQTT; test broker start/stop independently) |
| `esp32-workbench-test` | Run full validation walkthrough below | All steps pass |

## Validation Walkthrough

Run through these steps in order after flashing. Each step builds on the
previous one.

### 1. Serial flashing and boot

1. Flash `wb-test-firmware.bin` via the serial flashing skill
2. Start serial monitor
3. Confirm output contains:
   - `"=== Workbench Test Firmware v0.1.0 ==="`
   - `"NVS initialized"`
   - `"UDP logging -> 192.168.0.87:5555"`
   - `"No WiFi credentials, starting AP provisioning"`
   - `"AP mode: SSID='WB-Test-Setup'"`
   - `"BLE NUS initialized"`
   - `"Init complete, running event-driven"`

### 2. WiFi provisioning

1. Confirm device is in AP mode (serial shows `"AP mode"`)
2. Run `enter-portal` with:
   - `portal_ssid`: `WB-Test-Setup`
   - `ssid`: workbench AP SSID
   - `password`: workbench AP password
3. Confirm serial shows:
   - `"Credentials saved, rebooting"`
   - `"STA mode, connecting to '<ssid>'"`
   - `"STA got IP"`

### 3. UDP logging

1. After WiFi is connected, check UDP logs:
   ```bash
   curl -s http://192.168.0.87:8080/api/udplog | head -20
   ```
2. Confirm heartbeat lines appear: `"heartbeat N | wifi=1 ble=0"`

### 4. HTTP endpoints

1. Get device IP from serial output or workbench scan
2. Via HTTP relay:
   ```bash
   curl -s -X POST http://192.168.0.87:8080/api/wifi/http \
        -H "Content-Type: application/json" \
        -d '{"method":"GET","url":"http://<device-ip>/status"}'
   ```
3. Confirm JSON response contains `project`, `version`, `wifi_connected: true`

### 5. BLE

1. Scan for BLE devices:
   ```bash
   curl -s -X POST http://192.168.0.87:8080/api/ble/scan \
        -H "Content-Type: application/json" \
        -d '{"duration": 5}'
   ```
2. Confirm `WB-Test` appears in scan results
3. Connect and discover services — NUS UUID `6e400001-b5a3-f393-e0a9-e50e24dcca9e` should be present

### 6. OTA update

1. Ensure firmware binary is uploaded to workbench (see Flashing section)
2. Trigger OTA via HTTP:
   ```bash
   curl -s -X POST http://192.168.0.87:8080/api/wifi/http \
        -H "Content-Type: application/json" \
        -d '{"method":"POST","url":"http://<device-ip>/ota"}'
   ```
3. Monitor serial for `"OTA succeeded, rebooting..."`
4. Confirm device reboots and shows boot banner again

### 7. WiFi reset

1. Via HTTP:
   ```bash
   curl -s -X POST http://192.168.0.87:8080/api/wifi/http \
        -H "Content-Type: application/json" \
        -d '{"method":"POST","url":"http://<device-ip>/wifi-reset"}'
   ```
2. Confirm serial shows `"WiFi credentials erased"` then reboot into AP mode

### 8. GPIO reset

1. Toggle EN pin LOW then HIGH via GPIO skill
2. Confirm serial shows fresh boot output

### 9. Flapping detection and recovery (requires GPIO slot)

This test verifies that the portal detects USB flapping (rapid connect/disconnect
cycling) and automatically recovers the device. **Use a slot with GPIO pins
configured** (e.g. SLOT1 with `gpio_boot` and `gpio_en`).

**Prerequisites:** Device has valid firmware and is in a stable state (idle, not
flapping). Note the slot's current state via `GET /api/devices`.

#### 9a. Trigger flapping

Erase the device's flash to cause a boot loop (corrupt flash → USB reconnect
cycle):

```bash
# Put device in download mode via GPIO
curl -s -X POST http://192.168.0.87:8080/api/serial/recover \
     -H "Content-Type: application/json" \
     -d '{"slot": "SLOT1"}'

# Wait for download mode, then erase flash
esptool.py --port rfc2217://192.168.0.87:4001?ign_set_control \
           --chip esp32s3 erase_flash

# Release GPIO — device reboots into erased flash → boot loop
curl -s -X POST http://192.168.0.87:8080/api/serial/release \
     -H "Content-Type: application/json" \
     -d '{"slot": "SLOT1"}'
```

#### 9b. Verify flap detection

Within ~30 seconds, the portal should detect the flapping:

```bash
curl -s http://192.168.0.87:8080/api/devices | python3 -m json.tool
```

Confirm for the target slot:
- `"flapping": true`
- `"recovering": true`
- `"state": "flapping"` or `"recovering"`
- Activity log shows: `"flapping detected (N events in 30s)"`

#### 9c. Verify GPIO recovery

For a GPIO-equipped slot, the portal automatically:
1. Unbinds the USB device at the kernel level (stops the event storm)
2. Waits `FLAP_COOLDOWN_S` (10s) for hardware to settle
3. Holds BOOT/GPIO0 LOW (forces download mode on next boot)
4. Pulses EN to reset the device
5. Rebinds USB — device enumerates in download mode (stable)

After recovery completes (~15s), check:

```bash
curl -s http://192.168.0.87:8080/api/devices | python3 -m json.tool
```

Confirm:
- `"state": "download_mode"` — device is in download mode, waiting for flash
- `"flapping": false`
- `"recovering": false`

#### 9d. Re-flash and release

Flash firmware back onto the device, then release GPIO:

```bash
# Flash firmware
esptool.py --port rfc2217://192.168.0.87:4001?ign_set_control \
           --chip esp32s3 --baud 460800 \
           write_flash --flash_size 8MB @flash_args

# Release BOOT pin and reboot into firmware
curl -s -X POST http://192.168.0.87:8080/api/serial/release \
     -H "Content-Type: application/json" \
     -d '{"slot": "SLOT1"}'
```

Confirm via serial or `/api/devices`:
- Device boots normally (`"state": "idle"`)
- Serial shows `"=== Workbench Test Firmware"` banner

### 10. Flapping recovery — no-GPIO slot

This tests the no-GPIO recovery path on a slot **without** `gpio_boot`/`gpio_en`
(e.g. SLOT2 or SLOT3). The portal cannot force download mode, so it uses a
flat cooldown + USB rebind strategy with up to `FLAP_MAX_RETRIES` (2) attempts.

**Warning:** If the device has erased/corrupt flash, no-GPIO recovery will
exhaust retries and leave the slot in `flapping` state requiring manual
intervention (flash via direct USB cable on the Pi). Only run this test if you
can physically access the Pi or the device has a recoverable boot issue (e.g.
WiFi misconfiguration, not erased flash).

#### 10a. Trigger and detect

Same as 9a/9b, but on a no-GPIO slot. Since there's no GPIO to enter download
mode, you'll need to erase flash by connecting directly on the Pi:

```bash
# On the Pi — erase flash directly
ssh pi@192.168.0.87 "esptool.py --port /dev/ttyACM1 \
    --before=usb_reset --chip esp32s3 erase_flash"
```

After erase, the device boot-loops and the portal detects flapping.

#### 10b. Verify no-GPIO recovery attempts

The portal will:
1. Unbind USB, wait `FLAP_COOLDOWN_S` (10s)
2. Rebind USB and hope the device stabilises
3. If flapping resumes, repeat up to `FLAP_MAX_RETRIES` (2) times
4. After retries exhausted: slot stays `flapping`, activity log shows
   `"needs manual intervention"`

Check:
```bash
curl -s http://192.168.0.87:8080/api/devices | python3 -m json.tool
```

Confirm:
- `"flapping": true`, `"recovering": false`
- `"recover_retries": 2` (max reached)

#### 10c. Manual recovery

Flash firmware directly on the Pi to stop the boot loop:

```bash
ssh pi@192.168.0.87 "esptool.py --port /dev/ttyACM1 \
    --before=usb_reset --chip esp32s3 --baud 460800 \
    write_flash --flash_size 8MB 0x0 /path/to/merged-binary.bin"
```

After flashing, the device stabilises and the stale-flapping auto-clear kicks
in: on the next `/api/devices` poll, aged-out events are pruned from
`_event_times` and the `flapping` flag clears automatically.

Confirm:
- `"flapping": false`, `"state": "idle"`

### 11. Manual recovery trigger

The `POST /api/serial/recover` endpoint can trigger recovery manually, even
when the slot is not currently flapping. This resets the retry counter and
starts a fresh recovery cycle.

```bash
curl -s -X POST http://192.168.0.87:8080/api/serial/recover \
     -H "Content-Type: application/json" \
     -d '{"slot": "SLOT1"}'
```

Confirm response: `{"ok": true, ...}`

### 12. Stale flapping auto-clear

After a device stabilises (stops cycling), the `flapping` flag should clear
automatically without any manual action.

1. Trigger flapping on a GPIO slot (step 9a–9c)
2. After GPIO recovery puts device in download mode, flash firmware (step 9d)
3. Wait at least `FLAP_WINDOW_S` (30s) without touching the device
4. Poll `GET /api/devices`
5. Confirm `"flapping": false` — the portal pruned aged-out events from the
   event window and cleared the flag

## Adding Test Coverage

When modifying a workbench skill:

1. Add a row to the **Skill Validation Matrix** if the skill isn't already covered
2. Add a step to the **Validation Walkthrough** if it requires a new test sequence
3. Flash the test firmware and run through the affected steps to confirm the
   skill still works
