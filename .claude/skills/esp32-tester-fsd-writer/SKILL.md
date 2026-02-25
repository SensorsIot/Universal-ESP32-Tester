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

### Step 3: Determine which tester capabilities apply

Only include what the project actually needs to test:

| Feature to test | Tester capability | Skill |
|----------------|-------------------|-------|
| Firmware boots correctly | Serial reset + monitor for expected output | `esp32-tester-serial` |
| WiFi provisioning works | Captive portal automation + AP status check | `esp32-tester-wifi` |
| OTA updates work | Upload binary + trigger via HTTP relay + monitor logs | `esp32-tester-ota` |
| BLE interface responds | Scan + connect + write + verify via logs | `esp32-tester-ble` |
| Boot mode selection | GPIO pin drive during reset | `esp32-tester-gpio` |
| Runtime behavior | UDP log monitoring | `esp32-tester-udplog` |
| Crash/panic recovery | Serial monitor for crash output | `esp32-tester-serial` |

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

Include project-specific constants (captive portal SSID, tester AP credentials, BLE device name, OTA endpoint URL).

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
1. Trigger captive portal provisioning:
   ```bash
   curl -X POST http://192.168.0.87:8080/api/enter-portal \
     -H 'Content-Type: application/json' \
     -d '{"portal_ssid": "<device-AP>", "ssid": "<tester-AP>", "password": "<password>"}'
   ```
2. Verify device connected:
   ```bash
   curl http://192.168.0.87:8080/api/wifi/ap_status
   ```

**Success:** `ap_status` shows device IP in `192.168.4.x` range.
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
- [ ] Logging strategy explains when to use serial monitor vs UDP logs for this project
- [ ] Troubleshooting covers the most likely test failure modes
- [ ] Only tester features the project actually uses are included

## Tester Capabilities Reference

| Skill | Key endpoints | Tests it enables |
|-------|-------------|-----------------|
| `esp32-tester-serial` | `POST /api/serial/reset`, `/api/serial/monitor` | Boot verification, crash capture, pattern matching |
| `esp32-tester-wifi` | `POST /api/enter-portal`, `/api/wifi/ap_start`, `GET /api/wifi/ap_status` | Provisioning test, AP connectivity, HTTP relay to device |
| `esp32-tester-ota` | `POST /api/firmware/upload`, `GET /api/firmware/list` | OTA update test (upload → trigger → verify) |
| `esp32-tester-ble` | `POST /api/ble/scan`, `/api/ble/connect`, `/api/ble/write` | BLE interface test (scan → connect → send data → check logs) |
| `esp32-tester-gpio` | `POST /api/gpio/set` | Boot mode test, button simulation |
| `esp32-tester-udplog` | `GET /api/udplog`, `DELETE /api/udplog` | Runtime log verification, test monitoring |

## Example

See `ios-keyboard-esp32/IOS-Keyboard-fsd.md` — the "Development Environment" and "Implementation Phases" sections are an example of a testing chapter produced by this procedure.
