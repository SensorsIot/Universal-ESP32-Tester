---
name: esp32-tester-fsd-writer
description: Reads a project's FSD and adds a testing chapter — how to verify each feature using the Universal ESP32 Tester, with hardware connections, test procedures, and troubleshooting. Triggers on "FSD", "write FSD", "enhance FSD", "add tester to FSD", "add testing", "new project", "set up project".
---

# FSD Writer — Integrate Workbench + Add Testing Chapter

This is a procedure. When triggered, read the project's existing FSD, integrate the firmware with the workbench infrastructure (UDP logging, OTA, BLE command handling, strategic log messages), then add a testing chapter.

The workbench provides the **test infrastructure**. This skill adds both the **firmware integration** (modules the workbench needs to interact with the device) and the **test plan** (how to verify each feature).

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

### Step 8: Write FSD testing chapter

Add a `## Testing with the ESP32 Workbench` chapter to the FSD containing:

#### 8a. Hardware connections table

Query the workbench for hardware details:
```bash
curl -s http://192.168.0.87:8080/api/devices | jq .
curl -s http://192.168.0.87:8080/api/info | jq .
```

Record: slot label, TCP port, RFC2217 URL, device state.

**Check for dual-USB hub boards:** If the board occupies two slots (onboard USB hub exposing both JTAG and UART), identify which slot is which:
- Espressif USB-Serial/JTAG (`303a:1001`) → **JTAG slot** (flash here)
- CH340/CP2102 UART bridge (`1a86:55d3` / `10c4:ea60`) → **UART slot** (console output here)

Document both slots in the hardware connections table.

For single-USB boards:
```markdown
### Test Hardware

| What | Where |
|------|-------|
| ESP32 USB | Workbench slot <N>, serial at `rfc2217://192.168.0.87:<PORT>` |
| Workbench GPIO 17 | ESP32 EN/RST (hardware reset) |
| Workbench GPIO 18 | ESP32 boot-select |
| ... | (project-specific connections) |
```

For dual-USB hub boards:
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
- Workbench AP SSID (`ssid`) — what the workbench fills into the device's portal form
- Workbench AP password (`password`) — what the workbench fills into the device's portal form

#### 8b. WiFi provisioning procedure

WiFi provisioning is a prerequisite for most tests (OTA, UDP logs, HTTP status). Document it as the **first test** and reference it from all tests that need WiFi.

**Three values are involved — document all three clearly:**

| Value | What it is | Where it's defined | Example |
|-------|-----------|-------------------|---------|
| Device portal SSID | The SoftAP name the device broadcasts when it has no WiFi credentials | `wifi_prov.c` → `AP_SSID` | `"KB-Setup"` |
| Workbench AP SSID | The WiFi network the workbench creates for the device to join | Passed in `enter-portal` request | `"WB-TestAP"` |
| Workbench AP password | Password for the workbench's AP | Passed in `enter-portal` request | `"wbtestpass"` |

**The provisioning flow has two phases — always document both:**

```markdown
### WiFi Provisioning

The device starts in one of two states:
- **AP mode** (no stored credentials) — device broadcasts its portal SSID
- **STA mode** (has stored credentials) — device tries to connect to stored WiFi

#### Phase 1: Ensure device is in AP mode

If the device was previously provisioned, erase its stored credentials first:

```bash
# Connect BLE
curl -s -X POST http://192.168.0.87:8080/api/ble/connect \
  -H 'Content-Type: application/json' \
  -d '{"address":"<DEVICE_BLE_MAC>"}'

# Send CMD_WIFI_RESET (0x11) — erases NVS credentials, device reboots into AP mode
curl -s -X POST http://192.168.0.87:8080/api/ble/write \
  -H 'Content-Type: application/json' \
  -d '{"characteristic":"6e400002-b5a3-f393-e0a9-e50e24dcca9e","data":"11"}'

# Wait for reboot, then verify AP mode via serial
curl -s -X POST http://192.168.0.87:8080/api/serial/monitor \
  -H 'Content-Type: application/json' \
  -d '{"slot":"<SLOT>","pattern":"AP mode","timeout":10}'
```

Skip this phase if the device was just freshly flashed (NVS is empty → AP mode).

#### Phase 2: Provision via captive portal

```bash
# enter-portal is async — returns immediately
curl -s -X POST http://192.168.0.87:8080/api/enter-portal \
  -H 'Content-Type: application/json' \
  -d '{"portal_ssid":"<DEVICE_PORTAL_SSID>","ssid":"WB-TestAP","password":"wbtestpass"}'

# Wait for device to reboot and connect (the portal submit triggers a reboot)
# Use serial monitor to confirm the device rebooted and connected
curl -s -X POST http://192.168.0.87:8080/api/serial/monitor \
  -H 'Content-Type: application/json' \
  -d '{"slot":"<SLOT>","pattern":"STA got IP","timeout":30}'

# Verify device appears on workbench AP
curl -s http://192.168.0.87:8080/api/wifi/ap_status
```

**Success:** Serial shows `"STA got IP: <ip>"` and `ap_status` shows the device as a connected station.

**If enter-portal fails** (device doesn't connect within 30s):
1. Check serial: is the device in AP mode? (`"AP mode: SSID='<DEVICE_PORTAL_SSID>'"`)
2. Check WiFi scan: does the workbench see the device's portal AP? (`GET /api/wifi/scan`)
3. Check activity log for errors: `GET /api/log`
```

**Important:** The FSD writer must fill in the actual values — never leave `<DEVICE_PORTAL_SSID>` as a placeholder. Extract from `wifi_prov.c` → `AP_SSID`.

#### 8c. Test procedures for each feature

For every testable feature in the FSD, write a concrete test procedure with exact curl commands using project-specific values. Each procedure must answer:
- **What prerequisite state** the device must be in (most tests require "WiFi provisioned" — reference the provisioning procedure above)
- **What to do** (exact curl commands)
- **What success looks like** (expected response or log output)

#### 8d. Phase verification tables

For each implementation phase, add a table mapping every deliverable to a test:

```markdown
### Phase N Verification

| Step | Feature | Test procedure | Success criteria |
|------|---------|---------------|-----------------|
| 1 | <feature> | <which workbench API + what to send> | <what response/log to expect> |
```

Every step must have a concrete, executable test — no vague "verify it works."

#### 8e. Logging strategy

Document which log method to use for each feature:

```markdown
### Logging for Tests

| Situation | Method | Why |
|-----------|--------|-----|
| Verify boot output | Serial monitor (`/api/serial/monitor`) | Captures UART before WiFi is up |
| Monitor runtime behavior | UDP logs (`/api/udplog`) | Non-blocking, works while device runs |
| Capture crash output | Serial monitor | Only UART captures panic handler output |
```

#### 8f. Troubleshooting

Add failure-to-diagnostic mapping:

```markdown
### Test Troubleshooting

| Test failure | Diagnostic | Fix |
|-------------|-----------|-----|
| Serial monitor shows no output | Check `/api/devices` for slot state | Device may be absent or flapping |
| enter-portal times out | Serial monitor — is device in AP mode? | Device has stored credentials → BLE `CMD_WIFI_RESET (0x11)` first |
| enter-portal succeeds but ap_status empty | Serial for `"STA got IP"` | Device connected then disconnected — check workbench AP is stable |
| Device keeps retrying STA | Serial shows `"STA disconnect, retry"` | Wrong credentials stored → BLE `CMD_WIFI_RESET (0x11)` to erase and re-provision |
| OTA test fails | Check `/api/wifi/ap_status` | Device not on WiFi — provision first |
| BLE test finds no device | Serial monitor for boot errors | Firmware may have crashed before BLE init |
```

### Step 9: Build verification

```bash
cd <project-root> && idf.py build
```

Fix any compilation errors. Common issues:
- Missing PRIV_REQUIRES in CMakeLists.txt
- Missing `#include` directives
- Function signature mismatches between header and implementation

### Step 10: Summary report

List what was added/changed:
- New files copied from workbench-test (with customizations noted)
- Modified files (what changed)
- Build result
- Any issues found and fixed

## Completeness Checklist

After completing all steps, verify:

- [ ] Every module needed by the feature checklist exists
- [ ] Every required log pattern is present
- [ ] CMakeLists.txt has all sources and dependencies
- [ ] app_main.c follows the canonical init order
- [ ] "Init complete" is the last log message in app_main()
- [ ] The testing chapter covers every FSD feature
- [ ] Every implementation phase has a verification table
- [ ] All project-specific values are filled in (no `<placeholder>` the AI must guess)
- [ ] WiFi provisioning tests include all three values: `portal_ssid`, `ssid`, `password`
- [ ] Logging strategy explains when to use serial monitor vs UDP logs
- [ ] Troubleshooting covers likely failure modes
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
