---
name: esp32-tester-serial
description: Serial device discovery, reset, monitor, and flashing for the Universal ESP32 Tester. Triggers on "serial", "reset", "monitor", "device", "slot", "NVS", "erase", "flash", "esptool".
---

# ESP32 Serial & Device Discovery

Base URL: `http://192.168.0.87:8080`

## When to Use Serial (vs OTA / UDP logs)

### Serial Flashing (esptool) — use when:
- Device has **no firmware** (blank/bricked/first flash)
- Firmware **lacks OTA support**
- You need to **erase NVS** or flash a **bootloader/partition table**
- Device has **no WiFi connectivity**
- **Prerequisite:** slot state must be `idle` (device present, USB connected)
- **Blocks:** stops the RFC2217 proxy during flash; no serial monitor while flashing
- **Alternative:** if device already runs OTA-capable firmware and is on WiFi, use OTA instead (see esp32-tester-ota) — it's faster and doesn't block serial

### Serial Monitor — use when:
- You need **boot messages** (before WiFi is up)
- You need to **wait for a specific log line** (pattern matching with timeout)
- Device has **no WiFi** or UDP logging is not compiled in
- You want **crash/panic output** from the UART
- **Prerequisite:** slot must be `idle` and proxy must be `running`
- **Blocks:** sets slot state to `monitoring` — only one monitor session per slot at a time
- **Alternative:** if device is on WiFi and sends UDP logs, use esp32-tester-udplog instead — it's non-blocking, supports multiple devices, and doesn't tie up the serial port

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/devices` | List all slots with state, device node, RFC2217 URL |
| GET | `/api/info` | System info (host IP, hostname, slot counts) |
| POST | `/api/serial/reset` | Hardware reset via DTR/RTS pulse, returns boot output |
| POST | `/api/serial/monitor` | Read serial output with optional pattern matching |

## Step 1: Discover Devices and Determine Board Type

Always start here. This determines whether you have a single-USB or dual-USB board.

```bash
curl -s http://192.168.0.87:8080/api/devices | jq .
```

Response fields per slot: `label`, `state`, `url` (RFC2217), `present`, `running`.

### Board type detection

Count how many slots show `present: true`. Then determine the type:

| Present slots | Board type | GPIO reset needed? | How to identify |
|---------------|------------|-------------------|-----------------|
| 1 slot | **Single-USB** | Yes — wire Pi GPIO 17→EN, GPIO 18→BOOT | One `ttyACM`/`ttyUSB` device; same slot for flash + monitor |
| 2 slots (same hub parent) | **Dual-USB hub board** | No — onboard auto-download circuit | Two `ttyACM` devices under a common USB hub path |

**For dual-USB boards**, you must identify which slot is which:

```bash
# SSH to tester — check the USB vendor for each present slot's devnode:
ssh pi@192.168.0.87 "udevadm info -q property /dev/ttyACM0 | grep ID_SERIAL"
# Contains "Espressif" → JTAG slot (flash + reset here)
# Contains "1a86", "CH340", "CP210x" → UART slot (serial console here)
```

**Summary of slot roles:**

| Operation | Single-USB board | Dual-USB board |
|-----------|-----------------|----------------|
| **Flash (esptool)** | The one slot | JTAG slot |
| **Serial monitor** | The one slot | UART slot |
| **Reset (DTR/RTS)** | The one slot (or Pi GPIO) | JTAG slot (auto-download circuit) |
| **Boot output after reset** | The one slot | UART slot (NOT the JTAG slot!) |
| **GPIO control needed?** | Yes (EN + BOOT pins) | No (handled by JTAG DTR/RTS) |

## Serial Reset

Sends DTR/RTS pulse, captures boot output (up to 5s), restarts proxy automatically.

```bash
curl -X POST http://192.168.0.87:8080/api/serial/reset \
  -H 'Content-Type: application/json' \
  -d '{"slot": "slot-1"}'
```

Response: `{"ok": true, "output": ["line1", "line2", ...]}`

## Serial Monitor

Reads serial output via RFC2217 proxy (non-exclusive read). Optionally waits for a regex pattern.

```bash
# Wait up to 10s for a pattern match
curl -X POST http://192.168.0.87:8080/api/serial/monitor \
  -H 'Content-Type: application/json' \
  -d '{"slot": "slot-1", "pattern": "WiFi connected", "timeout": 10}'

# Just capture output for 5s (no pattern)
curl -X POST http://192.168.0.87:8080/api/serial/monitor \
  -H 'Content-Type: application/json' \
  -d '{"slot": "slot-1", "timeout": 5}'
```

Response: `{"ok": true, "matched": true, "line": "WiFi connected to MyAP", "output": [...]}`

## Serial Flashing (esptool over RFC2217)

Each slot exposes an RFC2217 URL from `/api/devices`. Use it with esptool directly:

```bash
# 1. Get the RFC2217 URL
SLOT_URL=$(curl -s http://192.168.0.87:8080/api/devices | jq -r '.slots[0].url')

# 2. Flash firmware
esptool.py --port "$SLOT_URL" --chip esp32c3 write_flash 0x0 firmware.bin

# 3. Erase NVS partition
esptool.py --port "$SLOT_URL" --chip esp32c3 erase_region 0x9000 0x6000
```

## Slot States

| State | Meaning | Can flash? | Can monitor? |
|-------|---------|------------|--------------|
| `absent` | No USB device | No | No |
| `idle` | Ready | Yes | Yes |
| `resetting` | Reset in progress | No | No |
| `monitoring` | Monitor active | No | No (wait for current to finish) |
| `flapping` | USB storm | No | No (wait 30s) |

## Dual-USB Hub Board Reference

These boards contain an onboard USB hub exposing two interfaces:

| Interface | USB ID | Tester role |
|-----------|--------|-------------|
| Espressif USB-Serial/JTAG | `303a:1001` | **JTAG slot** — flash + reset |
| CH340/CP2102 UART bridge | `1a86:55d3` / `10c4:ea60` | **UART slot** — serial console |

### Workflow: reset + capture boot log on dual-USB board

```bash
# 1. Reset via JTAG slot (triggers auto-download circuit DTR/RTS reset)
curl -X POST http://192.168.0.87:8080/api/serial/reset \
  -H 'Content-Type: application/json' \
  -d '{"slot": "<JTAG-slot>"}'

# 2. Capture boot output from UART slot (where ESP_LOGI goes)
curl -X POST http://192.168.0.87:8080/api/serial/monitor \
  -H 'Content-Type: application/json' \
  -d '{"slot": "<UART-slot>", "timeout": 10}'
```

**Key:** reset output on the JTAG slot will be empty or minimal — the actual boot log appears on the UART slot.

## Common Workflows

1. **Flash a blank device:** `GET /api/devices` to find slot URL, then `esptool.py --port <url> write_flash ...`
2. **Reset and read boot log:** `POST /api/serial/reset` — returns boot output lines. For dual-USB boards: reset via JTAG slot, monitor via UART slot
3. **Wait for a specific message after reset:** reset first, then `POST /api/serial/monitor` with `pattern`. For dual-USB boards: monitor the UART slot
4. **Flash then verify boot:** flash via esptool (JTAG slot), then reset + monitor (UART slot for dual-USB boards, same slot for single-USB boards)

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Slot shows `absent` | Check USB cable, re-seat device |
| "proxy not running" | Device may be flapping — check `state` field |
| Monitor timeout, no output | Baud rate is fixed at 115200; ensure device matches. **For dual-USB boards:** console output goes to the UART slot, not the JTAG slot — make sure you're monitoring the right slot |
| `flapping` state | USB connection cycling — wait 30s for cooldown |
| esptool can't connect | Ensure slot is `idle`; may need to enter download mode via GPIO (see esp32-tester-gpio) |
| Reset works but no boot output | On dual-USB boards, reset via JTAG slot but boot output appears on UART slot |
| Board occupies two slots | Onboard USB hub — identify JTAG vs UART via `udevadm info` (see above) |
