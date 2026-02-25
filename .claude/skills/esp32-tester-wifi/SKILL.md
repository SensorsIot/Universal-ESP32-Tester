---
name: esp32-tester-wifi
description: WiFi AP/STA control, scanning, HTTP relay, and captive portal provisioning for the Universal ESP32 Tester. Triggers on "wifi", "AP", "station", "scan", "provision", "captive portal", "enter-portal", "HTTP relay".
---

# ESP32 WiFi & Provisioning

Base URL: `http://192.168.0.87:8080`

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/wifi/ap_start` | Start WiFi AP (for DUT to connect to) |
| POST | `/api/wifi/ap_stop` | Stop WiFi AP |
| GET | `/api/wifi/ap_status` | Current AP state and connected clients |
| POST | `/api/wifi/sta_join` | Join an existing WiFi network as station |
| POST | `/api/wifi/sta_leave` | Disconnect from WiFi network |
| GET | `/api/wifi/scan` | Scan for nearby WiFi networks |
| POST | `/api/wifi/http` | HTTP relay — make HTTP requests via tester's network |
| GET | `/api/wifi/events` | Long-poll for STA_CONNECT / STA_DISCONNECT events |
| POST | `/api/enter-portal` | Ensure device is on tester AP — provision via captive portal if needed |
| GET | `/api/wifi/ping` | Quick connectivity check |
| POST | `/api/wifi/mode` | Set mode: `wifi-testing` or `serial-interface` |
| GET | `/api/wifi/mode` | Get current mode |

## Examples

```bash
# Start AP for device testing
curl -X POST http://192.168.0.87:8080/api/wifi/ap_start \
  -H 'Content-Type: application/json' \
  -d '{"ssid": "TestAP", "pass": "testpass123", "channel": 6}'

# Check AP status
curl http://192.168.0.87:8080/api/wifi/ap_status

# Stop AP
curl -X POST http://192.168.0.87:8080/api/wifi/ap_stop

# Join a WiFi network as station
curl -X POST http://192.168.0.87:8080/api/wifi/sta_join \
  -H 'Content-Type: application/json' \
  -d '{"ssid": "MyNetwork", "pass": "password", "timeout": 15}'

# Disconnect station
curl -X POST http://192.168.0.87:8080/api/wifi/sta_leave

# Scan for networks
curl http://192.168.0.87:8080/api/wifi/scan

# Long-poll for WiFi events (30s timeout)
curl "http://192.168.0.87:8080/api/wifi/events?timeout=30"

# HTTP relay — make a GET request through the tester
curl -X POST http://192.168.0.87:8080/api/wifi/http \
  -H 'Content-Type: application/json' \
  -d '{"method": "GET", "url": "http://192.168.4.1/status", "timeout": 10}'

# HTTP relay — POST with base64 body
curl -X POST http://192.168.0.87:8080/api/wifi/http \
  -H 'Content-Type: application/json' \
  -d '{"method": "POST", "url": "http://192.168.4.1/config", "headers": {"Content-Type": "application/json"}, "body": "eyJzc2lkIjoiTXlOZXQifQ==", "timeout": 10}'

# Ensure device is on tester AP (provisions via captive portal if needed)
curl -X POST http://192.168.0.87:8080/api/enter-portal \
  -H 'Content-Type: application/json' \
  -d '{"portal_ssid": "iOS-Keyboard-Setup", "ssid": "TestAP", "password": "testpass123"}'
```

## Common Workflows

1. **Ensure device is connected to tester AP:**
   ```bash
   curl -X POST http://192.168.0.87:8080/api/enter-portal \
     -H 'Content-Type: application/json' \
     -d '{"portal_ssid": "<device-AP>", "ssid": "<tester-AP>", "password": "<tester-pass>"}'
   ```
   - Starts tester AP if not running
   - If device already has credentials → connects directly
   - If not → tester joins device's captive portal, fills in its own AP credentials, submits
   - Monitor progress via `GET /api/log`

2. **Test device WiFi connectivity:**
   - `POST /api/enter-portal` — ensure device is on tester AP
   - `GET /api/wifi/ap_status` — verify device is connected
   - `POST /api/wifi/http` — relay HTTP to DUT's IP to verify it responds

3. **Scan and join network:**
   - `GET /api/wifi/scan` — find available networks
   - `POST /api/wifi/sta_join` with chosen SSID

## Troubleshooting

| Problem | Fix |
|---------|-----|
| AP won't start | Check that mode is `wifi-testing` via `GET /api/wifi/mode` |
| STA join timeout | Verify SSID/password; increase timeout |
| HTTP relay fails | Ensure tester is on same network as target (AP or STA) |
| enter-portal "already running" | Previous run still active; wait for it to finish |
| No events from long-poll | DUT may not have connected yet; increase timeout |
