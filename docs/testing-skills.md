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

The default build uses 4MB flash with `partitions-4mb.csv`. For boards with
larger flash (detected via `esptool.py flash_id`), rebuild with
`-DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.defaults.esp32s3"` and
set `CONFIG_ESPTOOLPY_FLASHSIZE_8MB=y` (or appropriate size) plus
`CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions.csv"`.

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
| `esp32-workbench-serial-flashing` | Erase flash, trigger flapping, recover, re-flash | Flapping detected, recovery runs, firmware boots after re-flash |
| `esp32-workbench-logging` | Start serial monitor; check UDP logs | Serial shows boot output; `GET /api/udplog` returns heartbeat lines |
| `esp32-workbench-wifi` | Run `enter-portal` with device in AP mode | Serial shows `"STA got IP"`, device joins workbench network |
| `esp32-workbench-ble` | Scan for `WB-Test`, connect, discover services | BLE scan finds device; NUS service UUID appears in characteristics |
| `esp32-workbench-ota` | Upload binary, trigger OTA via HTTP `/ota` | Serial shows `"OTA succeeded"`, device reboots with new firmware |
| `esp32-workbench-gpio` | Toggle EN pin to reset device (GPIO slots only) | Serial monitor shows fresh boot output |
| `esp32-workbench-mqtt` | Start broker, verify device can reach `192.168.4.1:1883` | (Firmware doesn't use MQTT; test broker start/stop independently) |
| `esp32-workbench-test` | Run full validation walkthrough below | All steps pass |

## Validation Walkthrough

Run through these steps in order on each slot under test. Each step builds on
the previous one. Steps marked **(GPIO only)** require a slot with `gpio_boot`
and `gpio_en` configured (e.g. SLOT1).

Replace `<SLOT>`, `<PORT>`, and `<DEVNODE>` with the slot's values from
`GET /api/devices` (e.g. `SLOT1`/`4001`/`/dev/ttyACM3`).

### 1. Flapping detection and recovery

Erase flash to trigger a boot loop, verify the portal detects flapping and
attempts recovery. The recovery path depends on whether the slot has GPIO.

#### 1a. Erase flash

**GPIO slot** — use the recover API to enter download mode, erase, then release:

```bash
curl -s -X POST http://192.168.0.87:8080/api/serial/recover \
     -H "Content-Type: application/json" -d '{"slot": "<SLOT>"}'
# Wait for download mode (~15s)
esptool.py --port rfc2217://192.168.0.87:<PORT>?ign_set_control \
           --chip esp32s3 erase_flash
curl -s -X POST http://192.168.0.87:8080/api/serial/release \
     -H "Content-Type: application/json" -d '{"slot": "<SLOT>"}'
```

**No-GPIO slot** — erase directly on the Pi (portal must release the serial
port first, or use `--before=usb_reset`):

```bash
ssh pi@192.168.0.87 "python3 -m esptool --port <DEVNODE> \
    --before=usb_reset --chip esp32s3 erase_flash"
```

The device reboots into erased flash and starts boot-looping.

#### 1b. Verify flap detection

Within ~30 seconds the portal detects the flapping:

```bash
curl -s http://192.168.0.87:8080/api/devices | python3 -m json.tool
```

Confirm for the target slot:
- `"flapping": true`
- `"recovering": true`
- Activity log shows: `"flapping detected (N events in 30s)"`

#### 1c. Verify recovery

**GPIO slot:** The portal automatically unbinds USB, waits `FLAP_COOLDOWN_S`
(10s), holds BOOT LOW, pulses EN, rebinds USB. After ~15s:
- `"state": "download_mode"` — device stable in download mode
- `"flapping": false`, `"recovering": false`

**No-GPIO slot:** The portal unbinds USB, waits `FLAP_COOLDOWN_S` (10s),
rebinds. With erased flash, flapping resumes. After `FLAP_MAX_RETRIES` (2)
exhausted:
- `"flapping": true`, `"recovering": false`
- `"recover_retries": 2`
- Activity log shows: `"needs manual intervention"`

### 2. Serial flashing and boot

Flash test firmware. This also serves as the recovery step after flapping.

**GPIO slot** (device is in download mode from step 1c):

```bash
esptool.py --port rfc2217://192.168.0.87:<PORT>?ign_set_control \
           --chip esp32s3 --baud 460800 \
           write_flash @flash_args
curl -s -X POST http://192.168.0.87:8080/api/serial/release \
     -H "Content-Type: application/json" -d '{"slot": "<SLOT>"}'
```

**No-GPIO slot** (flash directly on the Pi):

```bash
# Upload build artifacts to Pi first
scp build/bootloader/bootloader.bin build/wb-test-firmware.bin \
    build/partition_table/partition-table.bin build/ota_data_initial.bin \
    pi@192.168.0.87:/tmp/

ssh pi@192.168.0.87 "python3 -m esptool --port <DEVNODE> \
    --before=usb_reset --chip esp32s3 --baud 460800 \
    write_flash --flash_mode dio --flash_freq 80m --flash_size <FLASH_SIZE> \
    0x0 /tmp/bootloader.bin \
    0x8000 /tmp/partition-table.bin \
    0xf000 /tmp/ota_data_initial.bin \
    0x20000 /tmp/wb-test-firmware.bin"
```

Use `--flash_size` matching the board (check with `flash_id` or `GET /api/devices`).

After flashing, verify boot via serial reset:

```bash
curl -s -X POST http://192.168.0.87:8080/api/serial/reset \
     -H "Content-Type: application/json" \
     -d '{"slot": "<SLOT>", "lines": 80}'
```

Confirm output contains:
- `"=== Workbench Test Firmware v0.1.0 ==="`
- `"NVS initialized"`
- `"UDP logging -> 192.168.0.87:5555"`
- `"No WiFi credentials, starting AP provisioning"`
- `"AP mode: SSID='WB-Test-Setup'"`
- `"BLE NUS initialized"`
- `"Init complete, running event-driven"`

#### Stale flapping auto-clear

For no-GPIO slots that were left in `flapping` state after step 1c, the flag
clears automatically once the device stabilises: on the next `GET /api/devices`
poll, aged-out events are pruned from `_event_times` within `FLAP_WINDOW_S`
(30s). Confirm `"flapping": false`, `"state": "idle"` after waiting 30s.

### 3. WiFi provisioning

1. Confirm device is in AP mode (serial shows `"AP mode"`)
2. Run `enter-portal` with:
   - `portal_ssid`: `WB-Test-Setup`
   - `ssid`: workbench AP SSID
   - `password`: workbench AP password
3. Confirm serial shows:
   - `"Credentials saved, rebooting"`
   - `"STA mode, connecting to '<ssid>'"`
   - `"STA got IP"`

### 4. UDP logging

1. After WiFi is connected, check UDP logs:
   ```bash
   curl -s http://192.168.0.87:8080/api/udplog | head -20
   ```
2. Confirm heartbeat lines appear: `"heartbeat N | wifi=1 ble=0"`

### 5. HTTP endpoints

1. Get device IP from serial output or workbench scan
2. Via HTTP relay:
   ```bash
   curl -s -X POST http://192.168.0.87:8080/api/wifi/http \
        -H "Content-Type: application/json" \
        -d '{"method":"GET","url":"http://<device-ip>/status"}'
   ```
3. Confirm JSON response contains `project`, `version`, `wifi_connected: true`

### 6. BLE

1. Scan for BLE devices:
   ```bash
   curl -s -X POST http://192.168.0.87:8080/api/ble/scan \
        -H "Content-Type: application/json" \
        -d '{"duration": 5}'
   ```
2. Confirm `WB-Test` appears in scan results
3. Connect and discover services — NUS UUID `6e400001-b5a3-f393-e0a9-e50e24dcca9e` should be present

### 7. OTA update

1. Ensure firmware binary is uploaded to workbench (see Flashing section)
2. Trigger OTA via HTTP:
   ```bash
   curl -s -X POST http://192.168.0.87:8080/api/wifi/http \
        -H "Content-Type: application/json" \
        -d '{"method":"POST","url":"http://<device-ip>/ota"}'
   ```
3. Monitor serial for `"OTA succeeded, rebooting..."`
4. Confirm device reboots and shows boot banner again

### 8. WiFi reset

1. Via HTTP:
   ```bash
   curl -s -X POST http://192.168.0.87:8080/api/wifi/http \
        -H "Content-Type: application/json" \
        -d '{"method":"POST","url":"http://<device-ip>/wifi-reset"}'
   ```
2. Confirm serial shows `"WiFi credentials erased"` then reboot into AP mode

### 9. GPIO reset (GPIO only)

1. Toggle EN pin LOW then HIGH via GPIO skill
2. Confirm serial shows fresh boot output

### 10. Manual recovery trigger

The `POST /api/serial/recover` endpoint triggers recovery manually, even when
the slot is not currently flapping. Resets the retry counter and starts a fresh
recovery cycle.

```bash
curl -s -X POST http://192.168.0.87:8080/api/serial/recover \
     -H "Content-Type: application/json" -d '{"slot": "<SLOT>"}'
```

Confirm response: `{"ok": true, ...}`

## Adding Test Coverage

When modifying a workbench skill:

1. Add a row to the **Skill Validation Matrix** if the skill isn't already covered
2. Add a step to the **Validation Walkthrough** if it requires a new test sequence
3. Flash the test firmware and run through the affected steps to confirm the
   skill still works
