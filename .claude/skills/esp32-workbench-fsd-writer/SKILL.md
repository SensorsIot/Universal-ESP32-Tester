---
name: esp32-tester-fsd-writer
description: Reads a project's FSD and integrates workbench infrastructure, then writes the Workbench, Testing, and Appendix chapters. Triggers on "FSD", "write FSD", "enhance FSD", "add tester to FSD", "add testing", "new project", "set up project".
---

# FSD Writer — Integrate Workbench + Write FSD Chapters

This is a procedure. When triggered, read the project's existing FSD, integrate the firmware with the workbench infrastructure (UDP logging, OTA, BLE command handling, strategic log messages), then write the operational, testing, and appendix chapters.

The workbench provides the **test infrastructure**. This skill adds both the **firmware integration** (modules the workbench needs to interact with the device) and the **FSD documentation** (operational guide, test plan, troubleshooting).

## FSD Document Structure

Every FSD produced by this skill must follow this structure:

```
# Project FSD
## Goal                             ← what & why (pre-existing)
## Functionality                    ← features, phases, constants (pre-existing)
## Working with the ESP32 Workbench ← operational: how to flash, provision, use BLE/OTA
## Testing                          ← test cases by phase, procedures, pass/fail criteria
## Appendix                         ← logging strategy, troubleshooting
```

Steps 1–7 handle firmware integration. Steps 8–12 write the FSD chapters.

## Template Reference

All template code lives in `/workspaces/YouTube/workbench-test/`. When adding modules, copy from these templates and customize project-specific values:

| Module | Template source | Customization |
|--------|----------------|---------------|
| `udp_log.c/.h` | `workbench-test/main/udp_log.c` | None (universal) |
| `wifi_prov.c/.h` | `workbench-test/main/wifi_prov.c` | Change `AP_SSID`, `NVS_NAMESPACE`. Has retry backoff (1s→2s→4s→8s→16s cap) and 15-minute timeout. |
| `portal.html` | `workbench-test/main/portal.html` | Change `<title>` and `<h1>` |
| `ota.c/.h` | `workbench-test/main/ota.c` | Change `OTA_DEFAULT_URL` |
| `ble_nus.c/.h` | `workbench-test/main/ble_nus.c` | Change BLE device name |
| `cmd_handler.c/.h` | `workbench-test/main/cmd_handler.c` | Remove/add project-specific opcodes |
| `dns_server/` | `workbench-test/components/dns_server/` | None (copy entire dir) |
| `partitions.csv` | `workbench-test/partitions.csv` | None (dual OTA layout) |
| `sdkconfig.defaults` | `workbench-test/sdkconfig.defaults` | Reference for required options |
| `app_main.c` | `workbench-test/main/app_main.c` | Reference for init order only |

## Required Log Patterns

These log messages are required for the workbench skills to work. Step 5 ensures they exist.

| Pattern | Workbench skill that needs it | Where |
|---------|-------------------------------|-------|
| `"Init complete"` | serial monitor boot verification | End of app_main() |
| `"alive %lu"` | liveness checking | Heartbeat task |
| `"OTA succeeded"` / `"OTA failed"` | OTA verification | OTA task |
| `"OTA update requested"` | BLE command verification | cmd_handler |
| `"WiFi reset requested"` | WiFi reset verification | cmd_handler |
| `"WiFi credentials erased"` | confirm NVS wipe before reboot | wifi_prov_reset() |
| `"INSERT: %.*s"`, `"ENTER"`, `"BACKSPACE x%d"` | BLE command verification via UDP logs | cmd_handler |
| `"UDP logging -> %s:%d"` | UDP log confirmation | udp_log_init() |
| `"No WiFi credentials"` | confirm device boots into AP mode | wifi_prov_init() |
| `"AP mode: SSID='%s'"` | captive portal detection | WiFi AP start |
| `"Portal page requested"` | confirm workbench reached the portal | portal_get_handler() |
| `"Credentials saved"` | confirm portal form was submitted | connect_post_handler() |
| `"STA mode, connecting to '%s'"` | confirm device is trying to connect | start_sta() |
| `"STA got IP"` | confirm device connected to WiFi | wifi_event_handler() |
| `"STA disconnect, retry"` | diagnose WiFi connection failures | wifi_event_handler() |
| `"BLE NUS initialized"` | BLE readiness | BLE init |

## Procedure

### Step 1: Identify project

Find the project's FSD path and firmware root directory. Confirm:
- What chip is being used (ESP32, ESP32-S3, etc.)
- Where the firmware source lives (e.g. `main/` directory)
- The project name

### Step 2: Parse FSD — extract features and build checklist

Read the entire FSD. Extract every feature, phase, and constant. Then build a feature checklist:

```
NEEDS_WIFI        → if project uses WiFi
NEEDS_BLE         → if project uses BLE
NEEDS_BLE_NUS     → if project uses Nordic UART Service
NEEDS_OTA         → if project supports firmware updates
NEEDS_MQTT        → if project uses MQTT
NEEDS_UDP_LOG     → always yes when NEEDS_WIFI=yes
NEEDS_CMD_HANDLER → if NEEDS_BLE_NUS=yes
OTA_TRIGGER       → ble / http / both
```

Record project-specific values:
- WiFi AP SSID for captive portal (e.g. `"KB-Setup"`)
- BLE device name (e.g. `"iOS-KB"`)
- OTA URL (e.g. `"http://192.168.0.87:8080/firmware/ios-keyboard/ios-keyboard.bin"`)
- NVS namespace
- Any project-specific command opcodes

### Step 3: Audit firmware code

Inventory the project's source files. For each module in the template reference table, check:
- Does the file exist?
- Does it contain the required log patterns?
- Does it match the template's API signatures?

Also check:
- `CMakeLists.txt` — are all sources listed in SRCS? Are all PRIV_REQUIRES present?
- `sdkconfig.defaults` — are required options set?
- `partitions.csv` — does it have OTA slots (if NEEDS_OTA)?
- `app_main.c` — what's the init order? Is "Init complete" the last log?
- `components/dns_server/` — does it exist (if NEEDS_WIFI)?

### Step 4: Add missing modules

Follow this decision tree. For each missing module, copy from `workbench-test/main/` and customize:

```
Does the project use WiFi? --NO--> Skip WiFi, UDP, OTA
  |YES
  v
Has udp_log.c? --YES--> Check log message exists
  |NO --> Copy from workbench-test
  v
Has wifi_prov.c? --YES--> Check AP_SSID, check wifi_prov_reset()
  |NO --> Copy from workbench-test, customize AP_SSID
  v
Needs OTA? --NO--> Skip
  |YES
  v
Has ota.c? --YES--> Check OTA_DEFAULT_URL, check log messages
  |NO --> Copy from workbench-test, customize URL
         Ensure partitions.csv has OTA slots
  v
Uses BLE? --NO--> Skip BLE modules
  |YES
  v
Has ble_nus.c? --YES--> Check device name
  |NO --> Copy from workbench-test, customize name
  v
Has cmd_handler.c? --YES--> Check CMD_OTA + CMD_WIFI_RESET exist
  |NO --> Copy from workbench-test, add project-specific opcodes
  v
Has heartbeat task? --YES--> Check "alive" pattern
  |NO --> Add to app_main.c
  v
Has "Init complete"? --YES--> Done
  |NO --> Add to end of app_main()
```

When copying files:
- Read the template source from workbench-test
- Customize project-specific values (AP_SSID, BLE name, OTA URL, NVS namespace)
- Add or remove project-specific opcodes in cmd_handler
- Write the customized file to the project

### Step 5: Add strategic logging

Check every required log pattern from the table above. For each missing pattern:
- Add the exact log statement at the correct location
- Use the exact format string — the workbench skills grep for these patterns

### Step 6: Update build config

Update the project's build configuration:

**CMakeLists.txt** — add new source files to SRCS, add any missing PRIV_REQUIRES:
- `nvs_flash`, `esp_wifi`, `esp_netif`, `esp_event` (WiFi)
- `esp_http_server`, `esp_http_client`, `esp_https_ota` (OTA)
- `bt` (BLE)
- `dns_server`, `lwip` (captive portal)
- `esp_app_format`, `app_update` (OTA + status endpoint)
- `json` (OTA HTTP endpoint)
- Add `EMBED_FILES "portal.html"` if wifi_prov uses captive portal

**partitions.csv** — copy from workbench-test if project needs OTA but doesn't have dual OTA layout

**sdkconfig.defaults** — verify required options are set (NimBLE, partition table, flash size, etc.)

**dns_server component** — copy `workbench-test/components/dns_server/` if project needs captive portal but doesn't have it

### Step 7: Update app_main.c

Ensure the canonical init order:
1. NVS init (with erase-on-corrupt fallback)
2. Boot count increment
3. `esp_netif_init()` + `esp_event_loop_create_default()`
4. `udp_log_init("192.168.0.87", 5555)`
5. Register IP event handler for HTTP server
6. `wifi_prov_init()`
7. `ble_nus_init(cmd_handler_on_rx)`
8. Heartbeat task (`alive_task`)
9. `ESP_LOGI(TAG, "Init complete, running event-driven")`

The exact implementation can vary, but the order must be: NVS → netif → UDP → WiFi → BLE → cmd handler → heartbeat → "Init complete".

### Step 8: Write "Working with the ESP32 Workbench" chapter

Add a `## Working with the ESP32 Workbench` chapter to the FSD. This is a standalone **operations guide** — how to interact with the device through the workbench. It contains no test cases.

#### 8a. Hardware setup

Query the workbench for hardware details:
```bash
curl -s http://192.168.0.87:8080/api/devices | jq .
curl -s http://192.168.0.87:8080/api/info | jq .
```

Record: slot label, TCP port, RFC2217 URL, device state.

**Check for dual-USB hub boards:** If the board occupies two slots (onboard USB hub exposing both JTAG and UART), identify which slot is which:
- Espressif USB-Serial/JTAG (`303a:1001`) → **JTAG slot** (flash here)
- CH340/CP2102 UART bridge (`1a86:55d3` / `10c4:ea60`) → **UART slot** (console output here)

Write a hardware table and a project-specific values table:

```markdown
### Hardware Setup

| What | Where |
|------|-------|
| ESP32 USB | Workbench slot <N>, serial at `rfc2217://192.168.0.87:<PORT>` |
| Workbench host | `192.168.0.87:8080` |
| UDP log sink | `192.168.0.87:5555` |
| OTA firmware URL | `http://192.168.0.87:8080/firmware/<project>/<project>.bin` |

#### Project-Specific Values

| Value | Setting |
|-------|---------|
| WiFi portal SSID | `<SSID>` (device SoftAP name when no credentials stored) |
| Workbench AP SSID | `WB-TestAP` |
| Workbench AP password | `wbtestpass` |
| BLE device name | `<NAME>` |
| NVS namespace | `<NS>` |
| NUS RX characteristic | `6e400002-b5a3-f393-e0a9-e50e24dcca9e` |
```

**Important:** Fill in all actual values from the firmware source — never leave `<placeholder>` in the final FSD.

#### 8b. Flashing

Document the project-specific esptool command for serial flashing via RFC2217. Reference the `esp32-workbench-serial-flashing` skill for download mode, crash-loop recovery, and dual-USB hub details.

#### 8c. WiFi provisioning

WiFi provisioning is a prerequisite for most operations (OTA, UDP logs, HTTP endpoints). Document it as a complete two-phase procedure with filled-in project values.

**Three values are involved — document all three clearly:**

| Value | What it is | Where it's defined |
|-------|-----------|-------------------|
| Device portal SSID | The SoftAP name the device broadcasts when it has no WiFi credentials | `wifi_prov.c` → `AP_SSID` |
| Workbench AP SSID | The WiFi network the workbench creates for the device to join | Passed in `enter-portal` request |
| Workbench AP password | Password for the workbench's AP | Passed in `enter-portal` request |

**Always document both phases:**
1. **Ensure device is in AP mode** — BLE WiFi reset if previously provisioned, skip if freshly flashed
2. **Provision via captive portal** — `enter-portal` with all three values filled in, serial monitor for confirmation

Include the enter-portal failure diagnostic steps (check AP mode, check WiFi scan, check activity log).

#### 8d. BLE commands

Document how to scan, connect, and send each opcode. Write a command reference table:

```markdown
| Opcode | Hex example | Description | Expected log |
|--------|-------------|-------------|--------------|
| `0x01 <count>` | `0103` | Backspace | `"BACKSPACE x3"` |
| ... | ... | ... | ... |
```

Include one example `curl` write command. This is reference material — test cases go in the Testing chapter.

#### 8e. OTA updates

Document the complete OTA workflow:
1. Upload firmware to the workbench (`/api/firmware/upload`)
2. Trigger OTA via BLE (`CMD_OTA` opcode) or via HTTP (`POST /ota` through relay)
3. Monitor result via serial

#### 8f. HTTP endpoints

Document the device's HTTP endpoints and how to reach them via the workbench HTTP relay (`/api/wifi/http`). Typical endpoints: `/status`, `/ota`.

#### 8g. Log monitoring

Document the two log methods (serial monitor and UDP logs) with example commands. This is the "how" — when to use which method goes in the Appendix.

### Step 9: Write "Testing" chapter

Add a `## Testing` chapter to the FSD. This chapter contains **only test cases** — verification tables with pass/fail criteria. It does not repeat operational procedures from the Workbench chapter.

#### 9a. Phase verification tables

For each implementation phase, write a table:

```markdown
### Phase N Verification

| Step | Feature | Test procedure | Success criteria |
|------|---------|---------------|-----------------|
| 1 | <feature> | <brief description, reference workbench chapter> | <expected output> |
```

**Rules:**
- Every FSD feature must appear in exactly one phase verification table
- Test procedures **reference** operations from the Workbench chapter (e.g., "Provision WiFi (see WiFi Provisioning)") — they don't duplicate curl commands
- Every step must have concrete, observable success criteria — no vague "verify it works"
- Include the hex data for BLE commands inline (e.g., "BLE write `024869`") since that's test-specific

### Step 10: Write "Appendix"

Add a `## Appendix` chapter to the FSD.

#### 10a. Logging strategy

Document when to use each log method:

```markdown
### Logging Strategy

| Situation | Method | Why |
|-----------|--------|-----|
| Verify boot output | Serial monitor | Captures UART before WiFi is up |
| Monitor BLE commands | UDP logs | Non-blocking, works while device runs |
| Capture crash output | Serial monitor | Only UART captures panic handler output |
```

#### 10b. Troubleshooting

Add a failure-to-diagnostic-to-fix mapping table covering likely failure modes:

```markdown
### Troubleshooting

| Test failure | Diagnostic | Fix |
|-------------|-----------|-----|
| Serial monitor shows no output | Check `/api/devices` | Device absent or flapping |
| enter-portal times out | Check serial for AP mode | BLE `CMD_WIFI_RESET` first |
| ... | ... | ... |
```

### Step 11: Build verification

```bash
cd <project-root> && idf.py build
```

Fix any compilation errors. Common issues:
- Missing PRIV_REQUIRES in CMakeLists.txt
- Missing `#include` directives
- Function signature mismatches between header and implementation

### Step 12: Summary report

List what was added/changed:
- New files copied from workbench-test (with customizations noted)
- Modified files (what changed)
- Build result
- Any issues found and fixed

## Completeness Checklist

After completing all steps, verify:

**Firmware integration (Steps 1–7):**
- [ ] Every module needed by the feature checklist exists
- [ ] Every required log pattern is present
- [ ] CMakeLists.txt has all sources and dependencies
- [ ] app_main.c follows the canonical init order
- [ ] "Init complete" is the last log message in app_main()

**Working with the Workbench chapter (Step 8):**
- [ ] Hardware table documents all slots (including dual-USB if applicable)
- [ ] All project-specific values are filled in (no `<placeholder>` the AI must guess)
- [ ] WiFi provisioning includes all three values: `portal_ssid`, `ssid`, `password`
- [ ] WiFi provisioning documents both phases (ensure AP mode + provision via portal)
- [ ] BLE command reference table covers every opcode
- [ ] OTA workflow covers upload + both trigger methods (BLE and HTTP)
- [ ] HTTP endpoints documented with relay examples
- [ ] Chapter works as a standalone operations guide

**Testing chapter (Step 9):**
- [ ] Every FSD feature appears in a phase verification table
- [ ] Every implementation phase has a verification table
- [ ] Test procedures reference (not duplicate) the Workbench chapter
- [ ] Every test step has concrete success criteria

**Appendix (Step 10):**
- [ ] Logging strategy explains when to use serial monitor vs UDP logs
- [ ] Troubleshooting covers likely failure modes

**Build (Step 11):**
- [ ] Project builds cleanly with `idf.py build`

## Workbench Skills Reference

| Skill | Key endpoints | What it enables |
|-------|-------------|-----------------|
| `esp32-tester-serial` | `GET /api/devices`, `POST /api/serial/reset` | Device discovery, remote flashing (esptool via RFC2217), GPIO download mode, crash-loop recovery |
| `esp32-tester-udplog` | `POST /api/serial/monitor`, `GET /api/udplog` | Serial monitor with pattern matching, UDP log collection, boot/crash capture |
| `esp32-tester-wifi` | `POST /api/enter-portal`, `GET /api/wifi/ap_status`, `GET /api/wifi/scan`, `POST /api/wifi/http`, `GET /api/wifi/events` | Captive portal provisioning, AP control, WiFi on/off testing, HTTP relay, event monitoring |
| `esp32-tester-gpio` | `POST /api/gpio/set`, `GET /api/gpio/status` | Boot mode control, hardware reset, button simulation, GPIO probe |
| `esp32-tester-ota` | `POST /api/firmware/upload`, `GET /api/firmware/list`, `POST /api/wifi/http` | Firmware upload/serve, OTA trigger via HTTP relay |
| `esp32-tester-ble` | `POST /api/ble/scan`, `POST /api/ble/connect`, `POST /api/ble/write`, `POST /api/ble/disconnect` | BLE scan, connect, GATT write, remote BLE testing |
