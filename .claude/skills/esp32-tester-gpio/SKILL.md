---
name: esp32-tester-gpio
description: GPIO pin control on the Raspberry Pi tester for driving ESP32 boot modes and buttons. Triggers on "GPIO", "pin", "boot mode", "button", "hardware reset".
---

# ESP32 GPIO Control

Base URL: `http://192.168.0.87:8080`

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/gpio/set` | Drive a pin: 0 (low), 1 (high), or "z" (hi-Z/release) |
| GET | `/api/gpio/status` | Read state of all driven pins |

## Allowed BCM Pins

`5, 6, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27`

## Examples

```bash
# Drive GPIO18 LOW (e.g., hold BOOT button)
curl -X POST http://192.168.0.87:8080/api/gpio/set \
  -H 'Content-Type: application/json' \
  -d '{"pin": 18, "value": 0}'

# Drive GPIO18 HIGH
curl -X POST http://192.168.0.87:8080/api/gpio/set \
  -H 'Content-Type: application/json' \
  -d '{"pin": 18, "value": 1}'

# Release GPIO18 (hi-Z)
curl -X POST http://192.168.0.87:8080/api/gpio/set \
  -H 'Content-Type: application/json' \
  -d '{"pin": 18, "value": "z"}'

# Read all driven pin states
curl http://192.168.0.87:8080/api/gpio/status
```

## Common Workflows

1. **Enter ESP32 download mode** (hold BOOT during reset):
   ```bash
   # Hold GPIO18 LOW (connected to ESP32 BOOT/GPIO0)
   curl -X POST http://192.168.0.87:8080/api/gpio/set \
     -H 'Content-Type: application/json' -d '{"pin": 18, "value": 0}'
   # Reset the device
   curl -X POST http://192.168.0.87:8080/api/serial/reset \
     -H 'Content-Type: application/json' -d '{"slot": "slot-1"}'
   # Release BOOT pin
   curl -X POST http://192.168.0.87:8080/api/gpio/set \
     -H 'Content-Type: application/json' -d '{"pin": 18, "value": "z"}'
   ```

2. **Simulate button press:**
   - Set pin LOW, wait, set pin to `"z"` to release

## Note: Dual-USB Hub Boards

Some ESP32-S3 dev boards have an onboard USB hub with a built-in auto-download circuit that connects GPIO0/EN to DTR/RTS on the USB-Serial/JTAG interface. For these boards, **external Pi GPIO wiring for reset and boot mode is not needed** — DTR/RTS on the JTAG slot handles it via `POST /api/serial/reset` on the JTAG slot. See esp32-tester-serial for identifying dual-USB boards.

## GPIO Control Probe — Auto-Detecting Board Capabilities

Not all boards have EN/BOOT pins wired to Pi GPIOs. Run this probe once per board to determine if GPIO control is available.

### Probe Procedure

```bash
# Step 1: Try GPIO-based download mode entry
# Hold BOOT low
curl -X POST http://192.168.0.87:8080/api/gpio/set \
  -H 'Content-Type: application/json' -d '{"pin": 18, "value": 0}'
# Pulse EN (reset)
curl -X POST http://192.168.0.87:8080/api/gpio/set \
  -H 'Content-Type: application/json' -d '{"pin": 17, "value": 0}'
sleep 0.1
curl -X POST http://192.168.0.87:8080/api/gpio/set \
  -H 'Content-Type: application/json' -d '{"pin": 17, "value": 1}'
# Release BOOT
curl -X POST http://192.168.0.87:8080/api/gpio/set \
  -H 'Content-Type: application/json' -d '{"pin": 18, "value": "z"}'
# Release EN
curl -X POST http://192.168.0.87:8080/api/gpio/set \
  -H 'Content-Type: application/json' -d '{"pin": 17, "value": "z"}'

# Monitor for boot output
curl -X POST http://192.168.0.87:8080/api/serial/monitor \
  -H 'Content-Type: application/json' \
  -d '{"slot": "<slot>", "pattern": "boot:", "timeout": 3}'

# Step 2: If GPIO had no effect, try USB DTR/RTS reset
curl -X POST http://192.168.0.87:8080/api/serial/reset \
  -H 'Content-Type: application/json' -d '{"slot": "<slot>"}'
```

### Interpreting Results

| GPIO probe output | USB reset output | Board type |
|-------------------|-----------------|------------|
| `boot:0x23` (DOWNLOAD) | — | **GPIO-controlled** — Pi GPIOs wired to EN/BOOT |
| No output / normal boot | Hardware reset output (`rst:0x15`) | **USB-controlled** — no GPIO wiring, use DTR/RTS |
| No output | No output | No control — check wiring or wrong slot |

### Caveats
- **Firmware crash loops** (`rst:0xc`) mask GPIO resets — continuous panic reboots make it impossible to distinguish a GPIO-triggered reset from a crash-triggered one. For reliable probing, first break the crash loop with `esptool.py --before=usb_reset erase_flash` (works even during crash loops on native USB devices — see esp32-tester-serial), then re-run the probe on the clean device.
- **Dual-USB hub boards** always respond to USB DTR/RTS on the JTAG slot; GPIO probe will show no effect.
- Probe only needs to run once per physical board.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "pin not in allowed set" | Use only the BCM pins listed above |
| "value must be 0, 1, or 'z'" | Pin must be integer; value must be `0`, `1`, or `"z"` |
| Pin stays driven after test | Always release pins with `"z"` when done |
| GPIO reset not needed | Board may have onboard auto-download circuit (dual-USB hub board) — use DTR/RTS via JTAG slot instead |
| Probe shows crash loop output | Board is rebooting from firmware panic, not from GPIO. Erase flash first for clean probe. |
