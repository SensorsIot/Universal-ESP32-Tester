---
name: esp32-tester-udplog
description: UDP debug log retrieval from ESP32 devices and tester activity log. Triggers on "UDP log", "debug log", "ESP32 log", "remote log", "activity log".
---

# ESP32 UDP Debug Logging

Base URL: `http://192.168.0.87:8080`

## When to Use UDP Logs (vs Serial Monitor)

### Use UDP logs when:
- Device is **on WiFi** and firmware sends UDP log packets
- You want **non-blocking** log collection (doesn't tie up the serial port)
- You're monitoring **multiple devices** simultaneously (filter by source IP)
- You need logs during **OTA updates** (serial may be unavailable)
- You want to **poll repeatedly** without blocking any slot

### Prerequisites:
1. Device firmware must **send UDP datagrams** to tester IP on port **5555**
2. Device must have **WiFi connectivity** to the tester (AP or same LAN)

### Do NOT use UDP logs when:
- Device has **no WiFi** yet (pre-provisioning, boot phase) — use serial monitor instead
- Firmware **doesn't include UDP logging** — use serial monitor instead
- You need **boot/crash output** — only serial monitor captures UART output from early boot
- You need to **wait for a specific pattern** with a timeout — serial monitor has built-in pattern matching; UDP logs require manual polling

### Summary: Serial Monitor vs UDP Logs

| | Serial Monitor | UDP Logs |
|---|---|---|
| **Works without WiFi** | Yes | No |
| **Boot/crash output** | Yes | No |
| **Pattern matching** | Built-in (regex + timeout) | Manual (poll + grep) |
| **Blocks serial port** | Yes (one session per slot) | No |
| **Multiple devices** | One slot at a time | All devices simultaneously |
| **Long-running** | Limited by timeout | Continuous (buffer persists) |

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/udplog` | Retrieve UDP log lines (filter by source, since, limit) |
| DELETE | `/api/udplog` | Clear the UDP log buffer |
| GET | `/api/log` | Tester activity log (portal actions, not device logs) |

## UDP Log Examples

```bash
# Get recent UDP logs (default limit: 200)
curl -s http://192.168.0.87:8080/api/udplog | jq .

# Filter by source device IP
curl -s "http://192.168.0.87:8080/api/udplog?source=192.168.4.2" | jq .

# Get logs since a timestamp, limited to 50 lines
curl -s "http://192.168.0.87:8080/api/udplog?since=1700000000.0&limit=50" | jq .

# Clear the buffer before starting a test
curl -X DELETE http://192.168.0.87:8080/api/udplog
```

Response format: `{"ok": true, "lines": [{"ts": 1700000001.23, "source": "192.168.4.2", "line": "OTA progress: 45%"}, ...]}`

## Activity Log Examples

The activity log tracks tester actions (resets, WiFi changes, firmware uploads) — not device output.

```bash
# Get all activity entries
curl -s http://192.168.0.87:8080/api/log | jq .

# Get entries since a timestamp
curl -s "http://192.168.0.87:8080/api/log?since=2025-01-01T00:00:00Z" | jq .
```

## How ESP32 Sends UDP Logs

The tester listens on UDP port **5555**. ESP32 firmware sends plain text lines:

```c
// ESP-IDF: send log line to tester
struct sockaddr_in tester = { .sin_family = AF_INET, .sin_port = htons(5555) };
inet_aton("192.168.0.87", &tester.sin_addr);
sendto(sock, msg, strlen(msg), 0, (struct sockaddr *)&tester, sizeof(tester));
```

Each line is stored with timestamp + source IP. Buffer holds ~10000 lines.

## Common Workflows

1. **Monitor OTA progress:**
   - `DELETE /api/udplog` — clear buffer
   - Trigger OTA (see esp32-tester-ota)
   - Poll: `GET /api/udplog?since=<last_ts>&limit=50`
   - Repeat polling until you see completion message

2. **Debug a running device:**
   - `GET /api/udplog?source=<device_ip>` — see what it's logging
   - If empty, device may not have UDP logging — fall back to serial monitor

3. **Multi-device monitoring:**
   - `GET /api/udplog` — all devices
   - Filter per device with `source=<ip>`

## Troubleshooting

| Problem | Fix |
|---------|-----|
| No UDP logs appearing | Ensure firmware sends UDP to tester IP:5555; check WiFi connectivity |
| Logs from wrong device | Use `source` query param to filter by IP |
| Old/stale logs | Clear with `DELETE /api/udplog` before starting a test |
| Need boot output | UDP logs don't capture boot — use serial monitor (esp32-tester-serial). For dual-USB hub boards, monitor the UART slot (not the JTAG slot) |
| Need pattern matching | Poll UDP logs manually; or use serial monitor which has built-in regex matching |
