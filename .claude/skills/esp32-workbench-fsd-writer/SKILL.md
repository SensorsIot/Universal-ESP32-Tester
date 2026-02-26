---
name: esp32-workbench-fsd-writer
description: Reads a project's FSD and adds a testing chapter — how to verify each feature using the Universal ESP32 Workbench, with hardware connections, test procedures, and troubleshooting. Triggers on "FSD", "write FSD", "enhance FSD", "add workbench to FSD", "add testing", "new project", "set up project".
---

# FSD Writer — Add Testing Chapter to Any ESP32 Project FSD

This is a procedure. When triggered, read the project's existing FSD and add a testing chapter that explains how to verify each feature using the Universal ESP32 Workbench.

The workbench provides the **test infrastructure**. The FSD writer adds the **test plan** — not build commands, not dev workflow, just how to test that each feature works.

## Procedure

### Step 1: Read the existing FSD

Find and read the project's FSD. Extract:
- Every feature that needs testing
- What connectivity the firmware uses (WiFi, BLE, USB, none)
- What implementation phases exist and what each delivers
- Project-specific values (chip type, SSIDs, BLE names, portal IPs, OTA endpoints)

### Step 2: Query the workbench for hardware details

```bash
curl -s http://192.168.0.87:8080/api/devices | jq .
curl -s http://192.168.0.87:8080/api/info | jq .
```

Record: slot label, TCP port, RFC2217 URL, device state.

**Check for dual-USB hub boards:** If the board occupies two slots (onboard USB hub
exposing both JTAG and UART), identify which slot is which:
- Espressif USB-Serial/JTAG (`303a:1001`) → **JTAG slot** (flash here)
- CH340/CP2102 UART bridge (`1a86:55d3` / `10c4:ea60`) → **UART slot** (console output here)

Document both slots in the hardware connections table and note which is used for
flashing vs serial monitoring.

### Step 3: Determine which workbench skills apply

The workbench offers these capabilities through 8 skills. Only include what the project actually needs:

**Serial Flashing** (`esp32-workbench-serial-flashing`)
- Device discovery — auto-detect slots, hotplug, dual-USB hub boards (JTAG + UART)
- Remote flashing — `esptool` via RFC2217 over the network
- GPIO download mode — enter download mode via Pi GPIO when DTR/RTS is unavailable
- Crash-loop recovery — `esptool erase_flash` works even during panic loops on native USB
- Flapping recovery — handling USB connection storms from empty/corrupt flash

**Serial Logging** (`esp32-workbench-serial-logging`)
- Serial monitor — pattern matching on boot output, crash capture, regex with timeout
- Serial reset — DTR/RTS hardware reset with boot output capture
- UDP log receiver — non-blocking debug log collection over WiFi (port 5555)
- Activity log — timestamped log of all workbench operations

**WiFi** (`esp32-workbench-wifi`)
- Workbench AP — start/stop a SoftAP for the DUT to connect to
- Captive portal provisioning — `enter-portal` auto-detects if provisioning is needed, joins device's portal, fills in workbench AP credentials, submits
- WiFi on/off testing — stop/start AP to test device WiFi disconnect/reconnect behavior
- WiFi scan — verify device's AP is broadcasting
- HTTP relay — make HTTP requests to devices on the workbench's WiFi network
- Event monitoring — long-poll for STA_CONNECT / STA_DISCONNECT events
- Mode switching — toggle between wifi-testing and serial-interface modes

**GPIO** (`esp32-workbench-gpio`)
- Boot mode control — hold BOOT LOW during EN reset to enter download mode
- Hardware reset — pulse EN LOW/HIGH
- Button simulation — drive any allowed pin LOW/HIGH
- GPIO probe — auto-detect if board has EN/BOOT wired to Pi GPIOs

**OTA Firmware** (`esp32-workbench-ota`)
- Firmware repository — upload, list, delete .bin files
- Serve binaries over HTTP for ESP32 OTA clients
- Trigger OTA on device via HTTP relay
- Monitor OTA progress via UDP logs or serial monitor

**MQTT Broker** (`esp32-workbench-mqtt`)
- Start/stop an MQTT broker (mosquitto) on the workbench
- Test MQTT client connect/disconnect/reconnect behavior
- Test combined WiFi + MQTT failure scenarios

**BLE** (`esp32-workbench-ble`)
- Scan for peripherals, filter by name
- Connect and write raw bytes to GATT characteristics
- Test BLE interfaces remotely (one connection at a time)
- Nordic UART Service (NUS) support

**Test Automation** (`esp32-workbench-test`)
- Test progress tracking — push live session updates to web portal
- Human interaction — block test until operator confirms a physical action
- Activity log — timestamped log of all workbench operations

### Step 4: Write the testing chapter

Add a `## Testing with the ESP32 Workbench` chapter to the FSD containing:

#### 4a. Hardware connections table

Document actual wiring from Step 2:

For single-USB boards (one slot):
```markdown
### Test Hardware

| What | Where |
|------|-------|
| ESP32 USB | Workbench slot <N>, serial at `rfc2217://192.168.0.87:<PORT>` |
| Workbench GPIO 17 | ESP32 EN/RST (hardware reset) |
| Workbench GPIO 18 | ESP32 boot-select |
| ... | (project-specific connections) |
```

For dual-USB hub boards (two slots):
```markdown
### Test Hardware

| What | Where |
|------|-------|
| ESP32 JTAG | Workbench slot <N> (Espressif USB JTAG), `rfc2217://192.168.0.87:<PORT>` — flash here |
| ESP32 UART | Workbench slot <M> (CH340/UART bridge), `rfc2217://192.168.0.87:<PORT>` — serial console here |
| Reset/Boot | Via DTR/RTS on JTAG slot (onboard auto-download circuit) |
| ... | (project-specific connections) |
```

Include project-specific constants. **For WiFi provisioning, always document all three values:**
- Device's captive portal SoftAP name (`portal_ssid`)
- Workbench AP SSID (`ssid`) — the workbench fills this into the device's portal form
- Workbench AP password (`password`) — the workbench fills this into the device's portal form

#### 4b. Test procedures for each feature

For every testable feature in the FSD, write a concrete test procedure with exact curl commands using project-specific values. Each procedure must answer:
- **What prerequisite state** the device must be in
- **What to do** (exact curl commands)
- **What success looks like** (expected response or log output)

Example structure:
```markdown
### Test: WiFi Provisioning

**Prerequisite:** device freshly flashed, no WiFi credentials stored.

**Steps:**
1. Ensure device is on workbench AP (provisions via captive portal if needed):
   ```bash
   curl -X POST http://192.168.0.87:8080/api/enter-portal \
     -H 'Content-Type: application/json' \
     -d '{"portal_ssid": "<device-portal-AP>", "ssid": "<workbench-AP>", "password": "<workbench-pass>"}'
   ```
   The workbench starts its AP, waits for the device to connect. If the device
   has no credentials, the workbench joins the device's captive portal SoftAP,
   follows the redirect, fills in its own AP SSID/password, and submits.
2. Verify device connected:
   ```bash
   curl http://192.168.0.87:8080/api/wifi/ap_status
   ```

**Success:** `ap_status` shows device as connected client.

**All three values must come from the project FSD** — never guess them:
- `portal_ssid` = device's captive portal SoftAP name
- `ssid` = workbench's AP SSID (what the workbench fills into the portal)
- `password` = workbench's AP password (what the workbench fills into the portal)
```

#### 4c. Phase verification tables

For each implementation phase, add a table mapping every deliverable to a test:

```markdown
### Phase N Verification

| Step | Feature | Test procedure | Success criteria |
|------|---------|---------------|-----------------|
| 1 | <feature> | <which workbench API + what to send> | <what response/log to expect> |
```

Every step must have a concrete, executable test — no vague "verify it works."

#### 4d. Logging strategy

Document which log method to use for testing each feature:

```markdown
### Logging for Tests

| Situation | Method | Why |
|-----------|--------|-----|
| Verify boot output | Serial monitor (`/api/serial/monitor`) | Captures UART before WiFi is up |
| Monitor runtime behavior | UDP logs (`/api/udplog`) | Non-blocking, works while device runs |
| Capture crash output | Serial monitor | Only UART captures panic handler output |
```

#### 4e. Troubleshooting

Add a table of test failures mapped to workbench-based diagnostics:

```markdown
### Test Troubleshooting

| Test failure | Diagnostic | Fix |
|-------------|-----------|-----|
| Serial monitor shows no output | Check `/api/devices` for slot state | Device may be absent or flapping. For dual-USB boards: ensure you're monitoring the UART slot, not the JTAG slot |
| OTA test fails | Check `/api/wifi/ap_status` | Device not on WiFi — provision first |
| BLE test finds no device | Serial monitor for boot errors | Firmware may have crashed before BLE init |
```

### Step 5: Verify completeness

Check that the testing chapter covers:

- [ ] Every feature in the FSD has a test procedure with exact curl commands
- [ ] Every implementation phase has a verification table
- [ ] All project-specific values are filled in (no `<placeholder>` the AI must guess)
- [ ] WiFi provisioning tests include all three values: `portal_ssid`, `ssid`, `password`
- [ ] Logging strategy explains when to use serial monitor vs UDP logs for this project
- [ ] Troubleshooting covers the most likely test failure modes
- [ ] Only workbench features the project actually uses are included

## Workbench Skills Reference

| Skill | Key endpoints | What it enables |
|-------|-------------|-----------------|
| `esp32-workbench-serial-flashing` | `GET /api/devices`, `POST /api/serial/reset` | Device discovery, remote flashing (esptool via RFC2217), GPIO download mode, crash-loop recovery |
| `esp32-workbench-serial-logging` | `POST /api/serial/monitor`, `GET /api/udplog` | Serial monitor with pattern matching, UDP log collection, boot/crash capture |
| `esp32-workbench-wifi` | `POST /api/enter-portal`, `GET /api/wifi/ap_status`, `GET /api/wifi/scan`, `POST /api/wifi/http`, `GET /api/wifi/events` | Captive portal provisioning, AP control, WiFi on/off testing, HTTP relay, event monitoring |
| `esp32-workbench-gpio` | `POST /api/gpio/set`, `GET /api/gpio/status` | Boot mode control, hardware reset, button simulation, GPIO probe |
| `esp32-workbench-ota` | `POST /api/firmware/upload`, `GET /api/firmware/list`, `POST /api/wifi/http` | Firmware upload/serve, OTA trigger via HTTP relay |
| `esp32-workbench-mqtt` | `POST /api/mqtt/start`, `POST /api/mqtt/stop`, `GET /api/mqtt/status` | MQTT broker on/off, client connect/disconnect testing |
| `esp32-workbench-ble` | `POST /api/ble/scan`, `POST /api/ble/connect`, `POST /api/ble/write`, `POST /api/ble/disconnect` | BLE scan, connect, GATT write, remote BLE testing |
| `esp32-workbench-test` | `POST /api/test/update`, `GET /api/test/progress`, `POST /api/human-interaction` | Test progress tracking, human interaction, activity log |

## Example

See `ios-keyboard-esp32/IOS-Keyboard-fsd.md` — the "Development Environment" and "Implementation Phases" sections are an example of a testing chapter produced by this procedure.
