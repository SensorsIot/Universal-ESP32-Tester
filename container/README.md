# Container Configuration

## Overview

With RFC2217, containers connect to ESP32 devices over the network. No USB passthrough or special privileges required.

## Device Discovery

### Option 1: Discovery API (Recommended)

Query the portal to find available devices:

```bash
curl http://PI_IP:8080/api/discover
```

Response:
```json
{
  "devices": [
    {"url": "rfc2217://PI_IP:4001", "port": 4001, "product": "CP2102", "serial": "0001"},
    {"url": "rfc2217://PI_IP:4002", "port": 4002, "product": "CH340", "serial": "5678"}
  ]
}
```

### Option 2: Python Discovery Helper

Use the included `discover.py`:

```python
from discover import discover_devices, get_device_url, get_serial_connection

# List all devices
devices = discover_devices(PI_HOST)
for d in devices:
    print(f"{d['url']} - {d['product']}")

# Get URL for first device
url = get_device_url(PI_HOST, index=0)

# Get URL by serial number
url = get_device_url(PI_HOST, serial="58DD029450")

# Get ready-to-use serial connection
ser = get_serial_connection(PI_HOST, index=0)
print(ser.readline())
```

### Option 3: Environment Variables

Set `PI_HOST` and use auto-discovery:

```bash
export PI_HOST=<your-pi-ip>
export ESP32_INDEX=0  # First device (default)
# or
export ESP32_SERIAL=58DD029450  # By serial number

python monitor.py
```

### Option 4: Direct URL

If you know the port, just use it directly:

```python
import serial
ser = serial.serial_for_url("rfc2217://PI_IP:4001", baudrate=115200)
```

## Connection Methods

### 1. Direct RFC2217 URL (Recommended)

Use pyserial's RFC2217 support directly:

```python
import serial

# Connect to ESP32 #1 on port 4001
ser = serial.serial_for_url("rfc2217://PI_IP:4001", baudrate=115200, timeout=1)

# Read/write like normal serial
while True:
    line = ser.readline()
    if line:
        print(line.decode().strip())
```

**Advantages:**
- No special container configuration
- Works in any container (Docker, LXC, DevContainer)
- Simple and reliable

### 2. Virtual TTY via socat

If your tool requires a local `/dev/tty*` path:

```bash
# Install socat
apt update && apt install -y socat

# Create virtual serial port
socat pty,link=/dev/ttyESP32,raw,echo=0 tcp:PI_IP:4001 &

# Now use /dev/ttyESP32 as a normal serial port
cat /dev/ttyESP32
```

**Note:** Run socat in background or as a service.

## Container Examples

### Docker

```dockerfile
# Dockerfile
FROM python:3.11-slim
RUN pip install pyserial
COPY monitor.py /app/
CMD ["python", "/app/monitor.py"]
```

```yaml
# docker-compose.yml
services:
  esp32-a:
    build: .
    environment:
      - ESP32_PORT=rfc2217://PI_IP:4001
    restart: unless-stopped

  esp32-b:
    build: .
    environment:
      - ESP32_PORT=rfc2217://PI_IP:4002
    restart: unless-stopped
```

### LXC (Proxmox)

No special configuration needed:

```bash
# In LXC container
apt update && apt install -y python3-pip
pip3 install pyserial

# Connect
python3 -c "
import serial
ser = serial.serial_for_url('rfc2217://PI_IP:4001', baudrate=115200)
print(ser.readline())
"
```

### DevContainer (VS Code)

See `devcontainer.json` for example configuration.

```bash
# After container starts
pip install pyserial esptool

# Test connection
python3 -c "
import serial
ser = serial.serial_for_url('rfc2217://PI_IP:4001', baudrate=115200)
print(ser.readline())
"
```

## Environment Variable Pattern

Use environment variables for flexible configuration:

```python
import serial
import os

PORT = os.environ.get('ESP32_PORT', 'rfc2217://localhost:4001')
ser = serial.serial_for_url(PORT, baudrate=115200, timeout=1)
```

This allows different containers to connect to different devices without code changes.

## PlatformIO in Container

```ini
; platformio.ini
[env:esp32]
platform = espressif32
board = esp32dev

; Use RFC2217 for upload and monitor
upload_port = rfc2217://PI_IP:4001?ign_set_control
monitor_port = rfc2217://PI_IP:4001?ign_set_control
```

## ESP-IDF in Container

```bash
# Set port via environment
export ESPPORT='rfc2217://PI_IP:4001?ign_set_control'

# Flash and monitor
idf.py flash monitor
```

## esptool Commands

```bash
# Flash firmware
esptool --port 'rfc2217://PI_IP:4001?ign_set_control' \
    write_flash 0x0 firmware.bin

# Read chip info
esptool --port 'rfc2217://PI_IP:4001?ign_set_control' chip_id

# Erase flash
esptool --port 'rfc2217://PI_IP:4001?ign_set_control' erase_flash
```

**Note:** Use `?ign_set_control` to ignore control line errors over network.

## Troubleshooting

### Connection Refused

1. Check if RFC2217 server is running on Pi (via web portal)
2. Verify network connectivity: `ping PI_IP`
3. Check firewall allows port 4001+

### Timeout Errors

- Use `--no-stub` flag with esptool for flashing
- Increase timeout in pyserial: `timeout=5`

### Port Busy

Only one client can connect at a time. Close other connections first.

### socat Connection Drops

Run socat with auto-restart:

```bash
while true; do
    socat pty,link=/dev/ttyESP32,raw,echo=0 tcp:PI_IP:4001
    sleep 1
done
```

Or use a systemd service in the container.
