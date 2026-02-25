---
name: esp32-tester-ota
description: OTA firmware upload, listing, deletion, and over-the-air update for the Universal ESP32 Tester. Triggers on "OTA", "firmware", "update", "upload", "binary", "over-the-air".
---

# ESP32 OTA & Firmware Repository

Base URL: `http://192.168.0.87:8080`

## When to Use OTA (vs Serial Flashing)

### Use OTA when:
- Device **already runs firmware** with an OTA HTTP endpoint
- Device is **on the WiFi network** (connected to tester's AP or same LAN)
- You want to update firmware **without blocking the serial port**
- You're doing **iterative development** (build → upload → trigger → monitor cycle)

### Prerequisites:
1. Device firmware must expose an **OTA trigger endpoint** (e.g., `POST /ota` accepting a URL)
2. Device must be **on the network** — either connected to tester's AP (see esp32-tester-wifi) or on the same LAN
3. Firmware binary must be **uploaded to the tester** first (it serves the file for the ESP32 to download)

### Do NOT use OTA when:
- Device is **blank/bricked** — use serial flashing instead (see esp32-tester-serial)
- Device firmware **has no OTA support** — use serial flashing
- Device has **no WiFi connectivity** — use serial flashing
- You need to flash a **bootloader or partition table** — only esptool can do this

### Monitoring OTA progress:
- Use **UDP logs** (see esp32-tester-udplog) if the firmware sends UDP log packets during OTA
- Use **serial monitor** (see esp32-tester-serial) if the firmware prints OTA progress to UART
- UDP logs are preferred (non-blocking); serial monitor blocks the slot
- **Dual-USB hub boards:** serial monitor must use the **UART slot** (not the JTAG slot) — see esp32-tester-serial for details

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/firmware/upload` | Upload firmware binary (multipart/form-data) |
| GET | `/api/firmware/list` | List all uploaded firmware files |
| DELETE | `/api/firmware/delete` | Delete a firmware file |
| GET | `/firmware/<project>/<file>` | Download URL (ESP32 fetches from here during OTA) |

## End-to-End OTA Workflow

### Step 1: Upload firmware to tester

```bash
curl -X POST http://192.168.0.87:8080/api/firmware/upload \
  -F "project=my-project" \
  -F "file=@build/firmware.bin"
```

Response: `{"ok": true, "project": "my-project", "filename": "firmware.bin", "size": 456789}`

### Step 2: Verify upload

```bash
curl -s http://192.168.0.87:8080/api/firmware/list | jq .
```

### Step 3: Ensure device is on the network

The device must be able to reach `http://192.168.0.87:8080`. Either:
- Tester runs AP and device connects to it (see esp32-tester-wifi `ap_start`)
- Both are on the same LAN

### Step 4: Clear UDP log buffer (for clean monitoring)

```bash
curl -X DELETE http://192.168.0.87:8080/api/udplog
```

### Step 5: Trigger OTA on the ESP32 via HTTP relay

```bash
# Build the JSON body for the device's OTA endpoint
OTA_BODY=$(echo -n '{"url":"http://192.168.0.87:8080/firmware/my-project/firmware.bin"}' | base64)

# Send to device via tester's HTTP relay
curl -X POST http://192.168.0.87:8080/api/wifi/http \
  -H 'Content-Type: application/json' \
  -d "{\"method\": \"POST\", \"url\": \"http://192.168.4.1/ota\", \"headers\": {\"Content-Type\": \"application/json\"}, \"body\": \"$OTA_BODY\", \"timeout\": 30}"
```

### Step 6: Monitor OTA progress

```bash
# Via UDP logs (preferred — non-blocking)
curl "http://192.168.0.87:8080/api/udplog?limit=50"

# Or via serial monitor (if firmware logs OTA to UART)
curl -X POST http://192.168.0.87:8080/api/serial/monitor \
  -H 'Content-Type: application/json' \
  -d '{"slot": "slot-1", "pattern": "OTA.*complete", "timeout": 60}'
```

## Managing Firmware Files

```bash
# List all uploaded firmware
curl http://192.168.0.87:8080/api/firmware/list

# Delete a firmware file
curl -X DELETE http://192.168.0.87:8080/api/firmware/delete \
  -H 'Content-Type: application/json' \
  -d '{"project": "my-project", "filename": "firmware.bin"}'

# The download URL for ESP32 to fetch:
# http://192.168.0.87:8080/firmware/<project>/<filename>
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Upload returns "expected multipart/form-data" | Use `-F` flags (not `-d`) for multipart upload |
| File not in list after upload | Check project/filename; `..` and `/` are rejected |
| ESP32 can't download firmware | Device must reach tester at 192.168.0.87:8080; check WiFi |
| OTA trigger times out | Check device's OTA endpoint URL; increase HTTP relay timeout |
| No progress in UDP logs | Device may not send UDP logs — check serial monitor instead |
| OTA trigger returns error | Verify device firmware has OTA endpoint; check relay response body |
