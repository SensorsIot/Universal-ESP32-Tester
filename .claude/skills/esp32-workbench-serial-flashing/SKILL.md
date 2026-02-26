---
name: esp32-workbench-serial-flashing
description: Device discovery, slot management, dual-USB hub boards, remote flashing via RFC2217, GPIO download mode, crash-loop recovery, and flapping. Triggers on "flash", "esptool", "device", "slot", "erase", "download mode", "crash loop", "flapping", "bricked".
---

# ESP32 Serial Flashing

Base URL: `http://192.168.0.87:8080`

## When to Use Serial Flashing

- Device has **no firmware** (blank/bricked/first flash)
- Firmware **lacks OTA support**
- You need to **erase NVS** or flash a **bootloader/partition table**
- Device has **no WiFi connectivity**
- **Alternative:** if device already runs OTA-capable firmware and is on WiFi, use OTA instead (see esp32-workbench-ota) — it's faster and doesn't block serial

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/devices` | List all slots with state, device node, RFC2217 URL |
| GET | `/api/info` | System info (host IP, hostname, slot counts) |
| POST | `/api/serial/reset` | Hardware reset via DTR/RTS pulse, returns boot output |

## Step 1: Discover Devices and Determine Board Type

Always start here.

```bash
curl -s http://192.168.0.87:8080/api/devices | jq .
```

Response fields per slot: `label`, `state`, `url` (RFC2217), `present`, `running`.

### Board type detection

| Present slots | Board type | How to identify |
|---------------|------------|-----------------|
| 1 slot | **Single-USB** | One `ttyACM`/`ttyUSB` device; same slot for flash + monitor |
| 2 slots (same hub parent) | **Dual-USB hub board** | Two `ttyACM` devices under a common USB hub path |

**For dual-USB boards**, identify which slot is which:

```bash
ssh pi@192.168.0.87 "udevadm info -q property /dev/ttyACM0 | grep ID_SERIAL"
# Contains "Espressif" → JTAG slot (flash + reset here)
# Contains "1a86", "CH340", "CP210x" → UART slot (serial console here)
```

### Slot roles

| Operation | Single-USB board | Dual-USB board |
|-----------|-----------------|----------------|
| **Flash (esptool)** | The one slot | JTAG slot |
| **Reset (DTR/RTS)** | The one slot (or Pi GPIO) | JTAG slot (auto-download circuit) |
| **GPIO control needed?** | Run GPIO probe (see esp32-workbench-gpio) | No (handled by JTAG DTR/RTS) |

## Step 2: Flash via RFC2217

Each slot exposes an RFC2217 URL from `/api/devices`. Use it with esptool.

**Baud rate:** Native USB devices (ESP32-S3/C3 `ttyACM`) ignore the baud rate — data transfers at USB speed regardless. The effective throughput is limited by the RFC2217 TCP proxy (~300 kbit/s). UART-bridge devices (`ttyUSB`) respect the baud rate. Use `-b 921600` as a sensible default for both cases.

```bash
# Get the RFC2217 URL
SLOT_URL=$(curl -s http://192.168.0.87:8080/api/devices | jq -r '.slots[0].url')

# Flash firmware (use ?ign_set_control for RFC2217 proxy compatibility)
esptool.py --port "${SLOT_URL}?ign_set_control" --chip esp32s3 -b 921600 \
  --before=default_reset --after=hard_reset write_flash \
  --flash_mode dio --flash_size 4MB --flash_freq 80m \
  0x0 build/bootloader/bootloader.bin \
  0x8000 build/partition_table/partition-table.bin \
  0xf000 build/ota_data_initial.bin \
  0x20000 build/firmware.bin

# Erase NVS partition
esptool.py --port "${SLOT_URL}?ign_set_control" --chip esp32s3 erase_region 0x9000 0x6000
```

### esptool flags by device type

| Device | `--before` | `--after` |
|--------|-----------|----------|
| ESP32-S3 (ttyACM, native USB) | `usb_reset` | `hard_reset` |
| ESP32-C3 (ttyACM, native USB) | `usb_reset` | `watchdog_reset` |
| ESP32 (ttyUSB, UART bridge) | `default_reset` | `hard_reset` |

**For dual-USB boards:** always flash via the **JTAG slot** (not the UART slot).

## GPIO Download Mode

When DTR/RTS reset doesn't work (no auto-download circuit), use GPIO to enter download mode. See esp32-workbench-gpio for the full sequence.

After entering download mode via GPIO, flash with `--before=no_reset` (device is already in download mode):

```bash
# Wait 5s for USB re-enumeration after GPIO reset
sleep 5

esptool.py --port "rfc2217://192.168.0.87:<PORT>?ign_set_control" \
  --chip esp32s3 --before=no_reset write_flash 0x0 firmware.bin
```

## Crash-Loop Recovery

When firmware crashes on boot, the ESP32 enters a rapid panic→reboot cycle. Serial monitor shows repeated `rst:0xc (RTC_SW_CPU_RST)` with crash backtraces.

**For native USB devices (ESP32-S3/C3):** `esptool --before=usb_reset` can connect even during a crash loop — it catches the device during the brief USB re-enumeration between reboots.

```bash
esptool.py --port "rfc2217://192.168.0.87:<PORT>?ign_set_control" \
  --chip esp32s3 --before=usb_reset erase_flash
```

After erasing, the device boots to empty flash and stops looping. Verify with serial reset — should show `rst:0x15 (USB_UART_CHIP_RESET)` and `boot:0x28 (SPI_FAST_FLASH_BOOT)`.

## Flapping

Empty or corrupt flash can cause USB connection cycling (`flapping` state). The workbench suppresses the RFC2217 proxy during flapping.

**Recovery:** Wait for flapping to clear (up to 30s), then flash firmware immediately. If the slot stays in `flapping`, the device may need a physical power cycle.

## Slot States

| State | Meaning | Can flash? |
|-------|---------|------------|
| `absent` | No USB device | No |
| `idle` | Ready | Yes |
| `resetting` | Reset in progress | No |
| `monitoring` | Monitor active | No |
| `flapping` | USB storm | No (wait 30s) |

## Serial Reset

Sends DTR/RTS pulse, captures boot output (up to 5s), restarts proxy automatically.

```bash
curl -X POST http://192.168.0.87:8080/api/serial/reset \
  -H 'Content-Type: application/json' \
  -d '{"slot": "slot-1"}'
```

Response: `{"ok": true, "output": ["line1", "line2", ...]}`

## Common Workflows

1. **Flash a blank device:** `GET /api/devices` to find slot URL → `esptool.py --port <url> write_flash ...`
2. **Flash via GPIO download mode:** enter download mode (see esp32-workbench-gpio) → wait 5s → `esptool.py --before=no_reset write_flash ...`
3. **Recover crash-looping device:** `esptool.py --before=usb_reset erase_flash` → then flash working firmware
4. **Recover flapping device:** wait for flapping to clear → flash firmware immediately

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Slot shows `absent` | Check USB cable, re-seat device |
| "proxy not running" | Device may be flapping — check `state` field |
| `flapping` state | USB connection cycling — wait 30s for cooldown |
| esptool can't connect | Ensure slot is `idle`; for native USB use `--before=usb_reset` |
| esptool fails after GPIO download mode | Wait 5s for USB re-enumeration before connecting; use `--before=no_reset` |
| Device crash-looping (`rst:0xc` repeated) | Erase flash with `esptool.py --before=usb_reset erase_flash` |
| Board occupies two slots | Onboard USB hub — identify JTAG vs UART via `udevadm info` (see above) |
