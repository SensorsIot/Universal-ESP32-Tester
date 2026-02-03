# ESP32 Serial Sharing Setup Guide

Complete guide for sharing ESP32 serial devices from a Raspberry Pi to containers via RFC2217.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            Proxmox Host                                 │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                              VM                                   │  │
│  │  ┌─────────────────────┐       ┌─────────────────────┐           │  │
│  │  │    Container A      │       │    Container B      │           │  │
│  │  │                     │       │                     │           │  │
│  │  │  discover.py        │       │  discover.py        │           │  │
│  │  │  rfc2217://pi:4001  │       │  rfc2217://pi:4002  │           │  │
│  │  └──────────┬──────────┘       └──────────┬──────────┘           │  │
│  │             │                             │                       │  │
│  └─────────────┼─────────────────────────────┼───────────────────────┘  │
│                │           TCP               │                          │
└────────────────┼─────────────────────────────┼──────────────────────────┘
                 │                             │
                 ▼                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Raspberry Pi Zero                               │
│                                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────────────┐  │
│  │   Portal    │  │    udev     │  │      esp_rfc2217_server        │  │
│  │   :8080     │  │   rules     │  │  :4001 ◄── /dev/ttyUSB0 ◄── ESP32 #1
│  │             │  │             │  │  :4002 ◄── /dev/ttyUSB1 ◄── ESP32 #2
│  │ /api/discover│ │  hotplug    │  │                                 │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────────────┘  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## How RFC2217 Works

RFC2217 is a Telnet protocol extension that allows serial port control over TCP/IP. The `esp_rfc2217_server` (from esptool) creates a TCP socket that bridges to a local serial device.

**Benefits over USB/IP:**
- No kernel modules required
- No VM configuration needed
- Works through firewalls (just TCP)
- Simpler and more reliable
- Native support in esptool, pyserial, PlatformIO

**Limitations:**
- Serial only (no USB HID, JTAG, etc.)
- One client per device at a time
- Slightly higher latency than local serial

---

## Part 1: Raspberry Pi Setup

### Prerequisites

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python and curl
sudo apt install -y python3-pip curl

# Install esptool (includes esp_rfc2217_server)
sudo pip3 install esptool --break-system-packages
```

### Verify esptool Installation

```bash
# Check esp_rfc2217_server is available
which esp_rfc2217_server.py

# Should output something like:
# /usr/local/bin/esp_rfc2217_server.py
```

### Install Portal and Scripts

```bash
# Clone repository
git clone https://github.com/SensorsIot/Serial-via-Ethernet.git
cd Serial-via-Ethernet/pi

# Install portal
sudo cp portal.py /usr/local/bin/rfc2217-portal
sudo chmod +x /usr/local/bin/rfc2217-portal

# Install hotplug script
sudo cp scripts/rfc2217-hotplug.sh /usr/local/bin/rfc2217-hotplug
sudo chmod +x /usr/local/bin/rfc2217-hotplug

# Install udev rules
sudo cp udev/99-rfc2217.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules

# Install systemd service
sudo cp systemd/rfc2217-portal.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable --now rfc2217-portal
```

### Verify Installation

```bash
# Check portal is running
sudo systemctl status rfc2217-portal

# Check web portal
curl http://localhost:8080/api/info

# List connected devices
curl http://localhost:8080/api/devices

# Check RFC2217 servers
ss -tlnp | grep 400
```

### Connect USB Devices

1. Plug in your ESP32 devices
2. They should appear automatically:

```bash
# Check devices detected
ls -la /dev/ttyUSB* /dev/ttyACM*

# Check RFC2217 servers started
curl http://localhost:8080/api/discover
```

---

## Part 2: Container Setup

### No VM Configuration Required

RFC2217 uses standard TCP connections. Containers connect directly to the Pi - no special VM setup needed.

**Requirements:**
- Container can reach Pi's IP address
- Ports 4001+ and 8080 are not blocked

### Install Dependencies in Container

```bash
# Python with pyserial
apt update && apt install -y python3-pip
pip3 install pyserial

# Optional: esptool for flashing
pip3 install esptool
```

### Copy Discovery Scripts (Optional)

```bash
# Copy from repo or download
curl -O https://raw.githubusercontent.com/SensorsIot/Serial-via-Ethernet/main/container/scripts/discover.py
curl -O https://raw.githubusercontent.com/SensorsIot/Serial-via-Ethernet/main/container/scripts/monitor.py
```

---

## Part 3: Connecting to Devices

### Method 1: Discovery API

Query available devices:

```bash
curl http://PI_IP:8080/api/discover
```

Response:
```json
{
  "devices": [
    {
      "url": "rfc2217://192.168.1.100:4001",
      "port": 4001,
      "product": "CP2102 USB to UART Bridge",
      "serial": "0001",
      "tty": "/dev/ttyUSB0"
    },
    {
      "url": "rfc2217://192.168.1.100:4002",
      "port": 4002,
      "product": "USB Single Serial",
      "serial": "58DD029450",
      "tty": "/dev/ttyUSB1"
    }
  ]
}
```

### Method 2: Python Discovery Helper

```python
from discover import discover_devices, get_device_url, get_serial_connection

# List all available devices
devices = discover_devices("192.168.1.100")
for d in devices:
    print(f"{d['url']} - {d['product']} [{d['serial']}]")

# Get URL by index (0 = first device)
url = get_device_url("192.168.1.100", index=0)
print(url)  # rfc2217://192.168.1.100:4001

# Get URL by serial number (stable across reboots)
url = get_device_url("192.168.1.100", serial="58DD029450")

# Get ready-to-use serial connection
ser = get_serial_connection("192.168.1.100", index=0)
while True:
    line = ser.readline()
    if line:
        print(line.decode().strip())
```

### Method 3: Environment Variables

```bash
# Set Pi host
export PI_HOST=192.168.1.100

# Select device by index
export ESP32_INDEX=0

# Or select by serial number
export ESP32_SERIAL=58DD029450
```

Then in Python:
```python
from discover import auto_discover

url = auto_discover()  # Uses PI_HOST and ESP32_INDEX/ESP32_SERIAL
```

### Method 4: Direct URL

If you know the port assignment:

```python
import serial

ser = serial.serial_for_url("rfc2217://192.168.1.100:4001", baudrate=115200, timeout=1)
```

---

## Part 4: Usage Examples

### Serial Monitor

```python
import serial
import os

PORT = os.environ.get('ESP32_PORT', 'rfc2217://192.168.1.100:4001')

ser = serial.serial_for_url(PORT, baudrate=115200, timeout=1)
print(f"Connected to {PORT}")

while True:
    line = ser.readline()
    if line:
        print(line.decode('utf-8', errors='replace').strip())
```

### Flash with esptool

```bash
# Read chip info
esptool --port 'rfc2217://192.168.1.100:4001?ign_set_control' chip_id

# Flash firmware
esptool --port 'rfc2217://192.168.1.100:4001?ign_set_control' \
    write_flash 0x0 firmware.bin

# If timeout errors, use --no-stub
esptool --no-stub --port 'rfc2217://192.168.1.100:4001?ign_set_control' \
    write_flash 0x0 firmware.bin
```

### PlatformIO

```ini
; platformio.ini
[env:esp32]
platform = espressif32
board = esp32dev
framework = arduino

upload_port = rfc2217://192.168.1.100:4001?ign_set_control
monitor_port = rfc2217://192.168.1.100:4001?ign_set_control
monitor_speed = 115200
```

### ESP-IDF

```bash
export ESPPORT='rfc2217://192.168.1.100:4001?ign_set_control'
idf.py flash monitor
```

### Create Local /dev/tty with socat

If your tool requires a local device path:

```bash
# Install socat
apt install -y socat

# Create virtual serial port
socat pty,link=/dev/ttyESP32,raw,echo=0 tcp:192.168.1.100:4001 &

# Now use /dev/ttyESP32
cat /dev/ttyESP32
```

---

## Part 5: Docker / Docker Compose

### Dockerfile

```dockerfile
FROM python:3.11-slim

RUN pip install pyserial esptool

COPY discover.py monitor.py /app/
WORKDIR /app

CMD ["python", "monitor.py"]
```

### docker-compose.yml

```yaml
version: '3.8'

services:
  esp32-monitor-a:
    build: .
    environment:
      - PI_HOST=192.168.1.100
      - ESP32_INDEX=0
    restart: unless-stopped

  esp32-monitor-b:
    build: .
    environment:
      - PI_HOST=192.168.1.100
      - ESP32_INDEX=1
    restart: unless-stopped
```

### Run

```bash
docker-compose up -d
docker-compose logs -f esp32-monitor-a
```

---

## Part 6: Troubleshooting

### Pi Side

**Portal not starting:**
```bash
sudo systemctl status rfc2217-portal
sudo journalctl -u rfc2217-portal -f
```

**esp_rfc2217_server not found:**
```bash
sudo pip3 install --force-reinstall esptool --break-system-packages
```

**Device not detected:**
```bash
ls -la /dev/ttyUSB* /dev/ttyACM*
dmesg | grep -i usb | tail -20
```

**Server not auto-starting:**
```bash
sudo udevadm control --reload-rules
sudo /usr/local/bin/rfc2217-hotplug add /dev/ttyUSB0
journalctl -t rfc2217-hotplug
```

**Check listening ports:**
```bash
ss -tlnp | grep 400
```

### Container Side

**Connection refused:**
```bash
# Check network connectivity
ping 192.168.1.100
curl http://192.168.1.100:8080/api/discover
```

**Timeout during flash:**
- Use `--no-stub` flag with esptool
- Check network latency: `ping 192.168.1.100`

**Port busy:**
- Only one client can connect at a time
- Close other connections first

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Connection refused | Server not running | Start via portal or check udev |
| Timeout | Network latency | Use `--no-stub`, check network |
| Permission denied | Device permissions | Check user is in `dialout` group |
| Device not found | USB not detected | Check `dmesg`, try different port |
| Port busy | Another client connected | Close other connection |

---

## Part 7: Port Assignments

Ports are assigned automatically starting from 4001:

| Port | Assignment |
|------|------------|
| 4001 | First device detected |
| 4002 | Second device detected |
| 4003 | Third device detected |
| ... | ... |

Port assignments are saved in `/etc/rfc2217/devices.conf`:

```
# RFC2217 device-port assignments
/dev/ttyUSB0=4001
/dev/ttyUSB1=4002
```

### Stable Assignments

For consistent port assignments across reboots, use device serial numbers:

```bash
# Find serial numbers
curl http://PI_IP:8080/api/discover | jq '.devices[] | {serial, port}'
```

Then in containers, reference by serial:
```python
url = get_device_url("192.168.1.100", serial="58DD029450")
```

---

## Part 8: Network Requirements

| Port | Direction | Purpose |
|------|-----------|---------|
| 8080 | Browser/Container → Pi | Web portal, Discovery API |
| 4001+ | Container → Pi | RFC2217 serial data |

### Firewall Rules (if needed)

**On Pi:**
```bash
sudo ufw allow 8080/tcp
sudo ufw allow 4001:4010/tcp
```

**On VM (usually not needed):**
```bash
sudo ufw allow out to PI_IP port 4001:4010 proto tcp
sudo ufw allow out to PI_IP port 8080 proto tcp
```

---

## Part 9: Security Considerations

- RFC2217 has **no authentication** - anyone who can reach the port can connect
- Keep on trusted network or use VPN/firewall
- Portal runs as root for device access
- Consider SSH tunnel for remote access:

```bash
# On client, create tunnel
ssh -L 4001:localhost:4001 -L 8080:localhost:8080 pi@PI_IP

# Then connect to localhost
curl http://localhost:8080/api/discover
```

---

## Part 10: Files Reference

### Pi Files

| Path | Purpose |
|------|---------|
| `/usr/local/bin/rfc2217-portal` | Web portal + discovery API |
| `/usr/local/bin/rfc2217-hotplug` | udev hotplug handler |
| `/etc/systemd/system/rfc2217-portal.service` | Systemd service |
| `/etc/udev/rules.d/99-rfc2217.rules` | Auto-start on device plug |
| `/etc/rfc2217/devices.conf` | Port assignments |

### Container Files

| Path | Purpose |
|------|---------|
| `discover.py` | Device discovery helper |
| `monitor.py` | Example serial monitor |

---

## Quick Reference

**Start/Stop servers:**
```bash
# Via portal
curl -X POST http://PI_IP:8080/api/start-all
curl -X POST http://PI_IP:8080/api/stop-all

# Manual
esp_rfc2217_server.py -p 4001 /dev/ttyUSB0
pkill -f esp_rfc2217_server
```

**Discover devices:**
```bash
curl http://PI_IP:8080/api/discover
```

**Connect from Python:**
```python
import serial
ser = serial.serial_for_url("rfc2217://PI_IP:4001", baudrate=115200)
```

**Flash with esptool:**
```bash
esptool --port 'rfc2217://PI_IP:4001?ign_set_control' write_flash 0x0 fw.bin
```
