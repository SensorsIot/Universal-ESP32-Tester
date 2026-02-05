# ESP32 Serial Sharing via RFC2217

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform](https://img.shields.io/badge/Platform-Raspberry%20Pi-red.svg)](https://www.raspberrypi.org/)
[![Proxmox](https://img.shields.io/badge/Proxmox-Containers-orange.svg)](https://www.proxmox.com/)
[![RFC2217](https://img.shields.io/badge/Protocol-RFC2217-green.svg)](https://datatracker.ietf.org/doc/html/rfc2217)

> Share USB serial devices (ESP32, Arduino) from a Raspberry Pi to containers over the network using RFC2217, with automatic logging of all serial traffic

---

## Scenario

```
                                    Proxmox Host
                                   ┌─────────────────────────────────┐
┌──────────────┐                   │  VM                             │
│ Raspberry Pi │                   │ ┌─────────────────────────────┐ │
│    Zero      │    Network        │ │ Container A                 │ │
│              │◄──────────────────┼─┤   rfc2217://pi:4001         │ │
│ ESP32 #1 ────┼── Port 4001       │ │   (monitors ESP32 #1)       │ │
│ ESP32 #2 ────┼── Port 4002       │ └─────────────────────────────┘ │
│              │                   │ ┌─────────────────────────────┐ │
│ Web Portal ──┼── Port 8080       │ │ Container B                 │ │
│              │◄──────────────────┼─┤   rfc2217://pi:4002         │ │
└──────────────┘                   │ │   (monitors ESP32 #2)       │ │
                                   │ └─────────────────────────────┘ │
                                   └─────────────────────────────────┘
```

**Your setup:**
- 2 ESP32 devices connected via USB to a Raspberry Pi Zero
- Pi Zero connected to Proxmox network
- VM running 2 containers
- Each container uses one ESP32 via RFC2217

---

## Quick Start

### 1. Setup Raspberry Pi Zero

```bash
# Install dependencies
sudo apt update && sudo apt install -y python3-pip curl
sudo pip3 install esptool --break-system-packages

# Clone repo
git clone https://github.com/SensorsIot/Serial-via-Ethernet.git
cd Serial-via-Ethernet/pi

# Install portal and scripts
sudo cp portal.py /usr/local/bin/rfc2217-portal
sudo cp scripts/rfc2217-hotplug.sh /usr/local/bin/rfc2217-hotplug.sh
sudo chmod +x /usr/local/bin/rfc2217-portal /usr/local/bin/rfc2217-hotplug.sh

# Install udev rules (auto-start on device plug)
sudo cp udev/99-rfc2217.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules

# Install and enable service
sudo cp systemd/rfc2217-portal.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rfc2217-portal
```

### 2. Access Web Portal

Open **http://\<pi-ip\>:8080** in your browser.

- See connected devices
- Start/Stop RFC2217 servers
- Copy connection URLs

### 3. Connect from Containers

**Option A: Auto-discovery (recommended)**

```python
# Use the included discover.py helper
from discover import get_serial_connection

# Container A gets first device
ser = get_serial_connection("PI_IP", index=0)

# Container B gets second device
ser = get_serial_connection("PI_IP", index=1)

while True:
    line = ser.readline()
    if line:
        print(line.decode().strip())
```

**Option B: Query discovery API**

```bash
# Find available devices
curl http://PI_IP:8080/api/discover
# Returns: {"devices": [{"url": "rfc2217://192.168.1.100:4001", ...}, ...]}
```

**Option C: Direct URL (if you know the port)**

```python
import serial

# Container A (ESP32 #1 on port 4001)
ser = serial.serial_for_url("rfc2217://PI_IP:4001", baudrate=115200)

# Container B (ESP32 #2 on port 4002)
ser = serial.serial_for_url("rfc2217://PI_IP:4002", baudrate=115200)
```

---

## Container Setup

### Docker

```yaml
# docker-compose.yml
services:
  esp32-monitor-a:
    image: python:3.11-slim
    command: python /app/monitor.py
    volumes:
      - ./monitor.py:/app/monitor.py
    environment:
      - ESP32_PORT=rfc2217://PI_IP:4001
    network_mode: bridge

  esp32-monitor-b:
    image: python:3.11-slim
    command: python /app/monitor.py
    volumes:
      - ./monitor.py:/app/monitor.py
    environment:
      - ESP32_PORT=rfc2217://PI_IP:4002
    network_mode: bridge
```

### LXC Container

No special configuration needed. Just install pyserial:

```bash
apt update && apt install -y python3-pip
pip3 install pyserial
```

### DevContainer (VS Code)

```json
{
  "name": "ESP32 Dev",
  "image": "python:3.11",
  "features": {
    "ghcr.io/devcontainers/features/python:1": {}
  },
  "postCreateCommand": "pip install pyserial esptool"
}
```

---

## Usage Examples

### Python with pyserial

```python
import serial
import os

PORT = os.environ.get('ESP32_PORT', 'rfc2217://192.168.1.100:4001')
ser = serial.serial_for_url(PORT, baudrate=115200, timeout=1)

# Read serial data
while True:
    line = ser.readline()
    if line:
        text = line.decode('utf-8', errors='replace').strip()
        print(text)

        # Simple AI-like monitoring
        if 'Guru Meditation' in text or 'Backtrace:' in text:
            print("ALERT: Crash detected!")
        if 'heap' in text.lower() and 'free' in text.lower():
            # Parse heap info
            pass
```

### esptool (Flashing)

```bash
# Flash firmware
esptool --port 'rfc2217://PI_IP:4001?ign_set_control' \
    write_flash 0x0 firmware.bin

# Read chip info
esptool --port 'rfc2217://PI_IP:4001?ign_set_control' chip_id
```

### PlatformIO

```ini
; platformio.ini
[env:esp32]
platform = espressif32
board = esp32dev
upload_port = rfc2217://PI_IP:4001?ign_set_control
monitor_port = rfc2217://PI_IP:4001?ign_set_control
```

### ESP-IDF

```bash
export ESPPORT='rfc2217://PI_IP:4001?ign_set_control'
idf.py flash monitor
```

### Local /dev/tty via socat

If your tool requires a local device path:

```bash
# In the container
apt install -y socat

# Create virtual serial port
socat pty,link=/dev/ttyESP32,raw,echo=0 tcp:PI_IP:4001 &

# Now use /dev/ttyESP32 as normal
cat /dev/ttyESP32
```

---

## AI Monitoring Example

```python
#!/usr/bin/env python3
"""ESP32 AI Monitor - Detect patterns and alert on issues"""

import serial
import json
import re
import os
from datetime import datetime

PORT = os.environ.get('ESP32_PORT', 'rfc2217://192.168.1.100:4001')

# Alert patterns
PATTERNS = {
    'crash': [r'Guru Meditation', r'Backtrace:', r'assert failed'],
    'memory': [r'heap.*free.*(\d+)', r'MALLOC_CAP'],
    'wifi': [r'WIFI.*DISCONNECT', r'wifi:.*reason'],
    'boot': [r'rst:.*boot:', r'configsip:'],
}

def analyze_line(line):
    """Analyze a line for patterns"""
    alerts = []
    for category, patterns in PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, line, re.IGNORECASE):
                alerts.append({
                    'category': category,
                    'pattern': pattern,
                    'line': line,
                    'timestamp': datetime.now().isoformat()
                })
    return alerts

def main():
    print(f"Connecting to {PORT}...")
    ser = serial.serial_for_url(PORT, baudrate=115200, timeout=1)
    print("Connected. Monitoring...")

    while True:
        try:
            line = ser.readline()
            if not line:
                continue

            text = line.decode('utf-8', errors='replace').strip()
            if not text:
                continue

            # Print the line
            print(text)

            # Analyze for alerts
            alerts = analyze_line(text)
            for alert in alerts:
                print(f"\n*** ALERT [{alert['category']}] ***")
                print(json.dumps(alert, indent=2))
                print()

        except Exception as e:
            print(f"Error: {e}")
            break

if __name__ == '__main__':
    main()
```

---

## Port Assignment

Ports are assigned based on **device serial number** for persistence:

| Serial Number | Port |
|---------------|------|
| 94_A9_90_47_5B_48 | 4001 |
| A5069RR4 | 4002 |
| (new device) | 4003+ |

**Key feature:** When an ESP32 resets or reconnects, it keeps the same port even if the tty name changes (e.g., ttyACM0 -> ttyACM1). This ensures containers always connect to the same device.

Config stored in: `/etc/rfc2217/devices.conf`

Check the web portal for current assignments.

---

## Serial Logging

All serial traffic is automatically logged with timestamps to `/var/log/serial/` on the Pi.

**Log file naming:**
```
FT232R_USB_UART_A5069RR4_2026-02-03.log
CP2102_USB_to_UART_0001_2026-02-03.log
```

**Log format:**
```
[2026-02-03 19:32:00.154] [RX] Boot message from ESP32...
[2026-02-03 19:32:00.258] [INFO] Baudrate changed to 115200
[2026-02-03 19:32:00.711] [TX] Data sent to ESP32...
```

**View logs via API:**
```bash
# List all logs
curl http://PI_IP:8080/api/logs

# Get last 100 lines of a log
curl "http://PI_IP:8080/api/logs/FT232R_USB_UART_A5069RR4_2026-02-03.log?lines=100"
```

**View logs on Pi:**
```bash
tail -f /var/log/serial/*.log
```

This allows AI monitoring, debugging, and post-mortem analysis of ESP32 behavior.

---

## Troubleshooting

### Connection Refused

```bash
# Check if server is running on Pi
ss -tlnp | grep 400

# Start via portal or manually
esp_rfc2217_server.py -p 4001 /dev/ttyUSB0
```

### Device Not Detected

```bash
# On Pi, check USB devices
ls -la /dev/ttyUSB* /dev/ttyACM*

# Check dmesg for USB events
dmesg | tail -20
```

### Timeout During Flash

Use `--no-stub` flag:
```bash
esptool --no-stub --port 'rfc2217://PI_IP:4001?ign_set_control' flash_id
```

### Port Busy

Only one client can connect at a time. Close other connections first.

---

## Files

```
.
├── pi/
│   ├── portal.py              # Web portal (auto-starts servers)
│   ├── serial_proxy.py        # RFC2217 proxy with logging
│   ├── scripts/
│   │   └── rfc2217-hotplug.sh # Hotplug handler for udev
│   ├── udev/
│   │   └── 99-rfc2217.rules   # Auto-start on device plug
│   └── systemd/
│       └── rfc2217-portal.service
├── container/
│   ├── README.md              # Container setup guide
│   ├── devcontainer.json      # VS Code devcontainer
│   └── scripts/
│       ├── discover.py        # Device discovery helper
│       └── monitor.py         # Example serial monitor
├── docs/
│   └── Setup Guide.md         # Detailed setup documentation
└── README.md                  # This file
```

---

## Network Requirements

| Port | Direction | Purpose |
|------|-----------|---------|
| 8080 | Browser -> Pi | Web portal |
| 4001+ | Container -> Pi | RFC2217 serial |

---

## License

MIT License - feel free to use and modify!
