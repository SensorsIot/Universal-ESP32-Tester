# WiFi Tester HTTP Backend — Operator Manual

The Pi-based WiFi Tester uses the Pi Zero W's own wlan0 radio as a WiFi test instrument. The portal exposes the test-instrument API over HTTP alongside the serial interface.

## Architecture

```
Your machine                         Pi Zero W
───────────                          ─────────
pytest                               portal.py :8080
  └─ WiFiTesterDriver ──HTTP──►        └─ wifi_controller.py
                                          ├─ hostapd    (AP mode)
                                          ├─ dnsmasq    (DHCP)
                                          ├─ wpa_supplicant (STA mode)
                                          ├─ iw scan
                                          └─ urllib     (HTTP relay)
```

The Pi connects to your network via **eth0** (USB Ethernet adapter). The **wlan0** radio is freed for testing.

## Quick Start

### 1. Install on Pi

```bash
ssh pi@<pi-ip>
cd Serial-via-Ethernet/pi
sudo bash install.sh
```

This installs hostapd, dnsmasq-base, deploys all scripts, and restarts the portal.

### 2. Verify

```bash
curl http://<pi-ip>:8080/api/wifi/ping
# {"ok": true, "fw_version": "1.0.0-pi", "uptime": 42}
```

### 3. Run Tests

```bash
cd Serial-via-Ethernet/pytest
pytest test_instrument.py --wt-url http://<pi-ip>:8080
```

## API Reference

All endpoints return `{"ok": true, ...}` or `{"ok": false, "error": "message"}`.

| Endpoint | Method | Body | Returns |
|----------|--------|------|---------|
| `/api/wifi/ping` | GET | — | `fw_version`, `uptime` |
| `/api/wifi/mode` | GET | — | `{mode, ssid?, ip?}` |
| `/api/wifi/mode` | POST | `{mode, ssid?, pass?}` | `{mode, ssid?, ip?}` |
| `/api/wifi/ap_start` | POST | `{ssid, pass?, channel?}` | `{ip}` |
| `/api/wifi/ap_stop` | POST | — | — |
| `/api/wifi/ap_status` | GET | — | `{active, ssid, channel, stations}` |
| `/api/wifi/sta_join` | POST | `{ssid, pass?, timeout?}` | `{ip, gateway}` |
| `/api/wifi/sta_leave` | POST | — | — |
| `/api/wifi/http` | POST | `{method, url, headers?, body?, timeout?}` | `{status, headers, body}` |
| `/api/wifi/scan` | GET | — | `{networks: [{ssid, rssi, auth}]}` |
| `/api/wifi/events` | GET | `?timeout=N` | `{events: [...]}` |

## Driver Usage (Python)

```python
from wifi_tester_driver import WiFiTesterDriver

wt = WiFiTesterDriver("http://192.168.1.50:8080")
wt.open()

# Ping
info = wt.ping()

# Start AP
wt.ap_start("MyTestAP", "password123", channel=6)
status = wt.ap_status()

# Wait for a device to connect
station = wt.wait_for_station(timeout=30)
print(f"Station joined: {station['mac']} at {station['ip']}")

# HTTP relay (request goes out via Pi's wlan0)
resp = wt.http_get(f"http://{station['ip']}/")
print(resp.status_code, resp.text)

# Scan
result = wt.scan()
for net in result["networks"]:
    print(f"  {net['ssid']}  {net['rssi']} dBm  {net['auth']}")

# STA mode (joins an external network)
wt.sta_join("HomeWiFi", "secret", timeout=15)
resp = wt.http_get("http://example.com")
wt.sta_leave()

# Cleanup
wt.ap_stop()
wt.close()
```

## Test Coverage

| Suite | Status |
|-------|--------|
| WT-100 (ping) | pass |
| WT-104 (rapid commands) | pass |
| WT-2xx (AP management) | pass |
| WT-3xx (station events) | pass (requires DUT) |
| WT-4xx (STA mode) | pass (requires DUT) |
| WT-5xx (HTTP relay) | pass (requires DUT) |
| WT-6xx (scan) | pass |

## Networking Notes

- **AP IP** is always `192.168.4.1/24`, DHCP range `.2`-`.20` (matches ESP32)
- AP and STA modes are mutually exclusive (starting one stops the other)
- Station connect/disconnect events arrive via dnsmasq lease callbacks
- The `body` field in HTTP relay requests/responses is **base64-encoded**
- Long-poll events with `GET /api/wifi/events?timeout=5` (seconds)

## Troubleshooting

```bash
# Check portal is running
curl http://<pi-ip>:8080/api/wifi/ping

# Check wlan0 exists
ssh pi@<pi-ip> iw dev

# Check hostapd is available
ssh pi@<pi-ip> which hostapd

# View portal logs
ssh pi@<pi-ip> journalctl -u rfc2217-portal -f

# Manual AP test
curl -X POST http://<pi-ip>:8080/api/wifi/ap_start \
  -H "Content-Type: application/json" \
  -d '{"ssid":"TEST","pass":"12345678"}'

curl http://<pi-ip>:8080/api/wifi/ap_status

curl -X POST http://<pi-ip>:8080/api/wifi/ap_stop
```
