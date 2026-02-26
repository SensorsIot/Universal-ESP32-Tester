---
name: esp32-tester-fsd-writer
description: Reads a project's FSD and adds a testing chapter — how to verify each feature using the Universal ESP32 Tester, with hardware connections, test procedures, and troubleshooting. Triggers on "FSD", "write FSD", "enhance FSD", "add tester to FSD", "add testing", "new project", "set up project".
---

# FSD Writer — Add Testing Chapter to Any ESP32 Project FSD

This is a procedure. When triggered, read the project's existing FSD and add a testing chapter that explains how to verify each feature using the Universal ESP32 Tester.

The tester provides the **test infrastructure**. The FSD writer adds the **test plan** — not build commands, not dev workflow, just how to test that each feature works.

## Procedure

### Step 1: Read the existing FSD

Find and read the project's FSD. Extract:
- Every feature that needs testing
- What connectivity the firmware uses (WiFi, BLE, USB, none)
- What implementation phases exist and what each delivers
- Project-specific values (chip type, SSIDs, BLE names, portal IPs, OTA endpoints)

### Step 2: Query the tester for hardware details

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

### Step 3: Determine which tester services apply

The tester provides these services. Only include what the project actually needs:

**Serial** (`esp32-tester-serial`)
- Device discovery — auto-detect slots, hotplug, dual-USB hub boards (JTAG + UART)
- Remote flashing — `esptool` via RFC2217 over the network
- Serial reset — DTR/RTS hardware reset, or GPIO reset for boards with wired EN/BOOT
- Serial monitor — pattern matching on boot output, crash capture
- Crash-loop recovery — `esptool erase_flash` works even during panic loops on native USB

**WiFi** (`esp32-tester-wifi`)
- Tester AP — start a SoftAP for the DUT to connect to
- Captive portal provisioning — `enter-portal` auto-detects if provisioning is needed, joins device's portal, fills in tester AP credentials, submits
- WiFi scan — verify device's AP is broadcasting
- HTTP relay — make HTTP requests to devices on the tester's WiFi network (bridges LAN ↔ WiFi)
- Event monitoring — long-poll for STA_CONNECT / STA_DISCONNECT events

**GPIO** (`esp32-tester-gpio`)
- Boot mode control — hold BOOT LOW during EN reset to enter download mode
- Hardware reset — pulse EN LOW/HIGH
- Button simulation — drive any allowed pin LOW/HIGH

**UDP Logging** (`esp32-tester-udplog`)
- Receive ESP32 debug logs over WiFi (port 5555)
- Buffer and filter by source IP, timestamp
- Essential when USB is occupied (e.g. HID keyboard mode)

**OTA Firmware** (`esp32-tester-ota`)
- Firmware repository — upload, list, delete .bin files
- Serve binaries over HTTP for ESP32 OTA clients
- Trigger OTA on device via HTTP relay

**BLE** (`esp32-tester-ble`)
- Scan for peripherals, filter by name
- Connect and write raw bytes to GATT characteristics
- Test BLE interfaces remotely (one connection at a time)

**Test Automation**
- Test progress tracking — push live session updates to web portal
- Human interaction — block test until operator confirms a physical action
- Activity log — timestamped log of all tester operations

### Step 4: Write the testing chapter

Add a `## Testing with the ESP32 Tester` chapter to the FSD containing:

#### 4a. Hardware connections table

Document actual wiring from Step 2:

For single-USB boards (one slot):
```markdown
### Test Hardware

| What | Where |
|------|-------|
| ESP32 USB | Tester slot <N>, serial at `rfc2217://192.168.0.87:<PORT>` |
| Tester GPIO 17 | ESP32 EN/RST (hardware reset) |
| Tester GPIO 18 | ESP32 boot-select |
| ... | (project-specific connections) |
```

For dual-USB hub boards (two slots):
```markdown
### Test Hardware

| What | Where |
|------|-------|
| ESP32 JTAG | Tester slot <N> (Espressif USB JTAG), `rfc2217://192.168.0.87:<PORT>` — flash here |
| ESP32 UART | Tester slot <M> (CH340/UART bridge), `rfc2217://192.168.0.87:<PORT>` — serial console here |
| Reset/Boot | Via DTR/RTS on JTAG slot (onboard auto-download circuit) |
| ... | (project-specific connections) |
```

Include project-specific constants. **For WiFi provisioning, always document all three values:**
- Device's captive portal SoftAP name (`portal_ssid`)
- Tester AP SSID (`ssid`) — the tester fills this into the device's portal form
- Tester AP password (`password`) — the tester fills this into the device's portal form

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
1. Ensure device is on tester AP (provisions via captive portal if needed):
   ```bash
   curl -X POST http://192.168.0.87:8080/api/enter-portal \
     -H 'Content-Type: application/json' \
     -d '{"portal_ssid": "<device-portal-AP>", "ssid": "<tester-AP>", "password": "<tester-pass>"}'
   ```
   The tester starts its AP, waits for the device to connect. If the device
   has no credentials, the tester joins the device's captive portal SoftAP,
   follows the redirect, fills in its own AP SSID/password, and submits.
2. Verify device connected:
   ```bash
   curl http://192.168.0.87:8080/api/wifi/ap_status
   ```

**Success:** `ap_status` shows device as connected client.

**All three values must come from the project FSD** — never guess them:
- `portal_ssid` = device's captive portal SoftAP name
- `ssid` = tester's AP SSID (what the tester fills into the portal)
- `password` = tester's AP password (what the tester fills into the portal)
```

#### 4c. Phase verification tables

For each implementation phase, add a table mapping every deliverable to a test:

```markdown
### Phase N Verification

| Step | Feature | Test procedure | Success criteria |
|------|---------|---------------|-----------------|
| 1 | <feature> | <which tester API + what to send> | <what response/log to expect> |
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

Add a table of test failures mapped to tester-based diagnostics:

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
- [ ] Only tester features the project actually uses are included

## Tester Capabilities Reference

| Skill | Key endpoints | What it enables |
|-------|-------------|-----------------|
| `esp32-tester-serial` | `GET /api/devices`, `POST /api/serial/reset`, `POST /api/serial/monitor` | Device discovery, remote flashing (esptool via RFC2217), boot verification, crash capture, crash-loop recovery |
| `esp32-tester-wifi` | `POST /api/enter-portal`, `GET /api/wifi/ap_status`, `GET /api/wifi/scan`, `POST /api/wifi/http`, `GET /api/wifi/events` | Captive portal provisioning, AP connectivity, WiFi scan, HTTP relay to device, event monitoring |
| `esp32-tester-ota` | `POST /api/firmware/upload`, `GET /api/firmware/list`, `POST /api/wifi/http` | Firmware upload/serve, OTA trigger via HTTP relay, update verification |
| `esp32-tester-ble` | `POST /api/ble/scan`, `POST /api/ble/connect`, `POST /api/ble/write`, `POST /api/ble/disconnect` | BLE scan, connect, GATT write, remote BLE interface testing |
| `esp32-tester-gpio` | `POST /api/gpio/set`, `GET /api/gpio/status` | Boot mode control, hardware reset, button simulation |
| `esp32-tester-udplog` | `GET /api/udplog`, `DELETE /api/udplog` | Runtime log capture over WiFi, essential when USB is occupied |

## Example

See `ios-keyboard-esp32/IOS-Keyboard-fsd.md` — the "Development Environment" and "Implementation Phases" sections are an example of a testing chapter produced by this procedure.
