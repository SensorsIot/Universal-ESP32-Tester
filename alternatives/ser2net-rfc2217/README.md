# RFC2217 Serial over TCP (Alternative to USB/IP)

A simpler, more robust alternative to USB/IP for ESP32 development.

## Comparison

| Feature | USB/IP | RFC2217 (ser2net) |
|---------|--------|-------------------|
| Complexity | High (kernel modules) | Low (userspace only) |
| Blocking risk | Yes (kernel hangs) | No |
| RTS/DTR support | Full USB signals | Via RFC2217 protocol |
| esptool support | Native (as /dev/ttyUSBx) | Native (`rfc2217://host:port`) |
| idf.py support | Native | Via ESPPORT env var |
| Device appears as | /dev/ttyUSB0 | Network socket |
| Other USB devices | Yes (any USB) | No (serial only) |

## When to use RFC2217

- ESP32/ESP8266 development only
- You only need serial (flashing + monitoring)
- You want reliability over complexity

## When to use USB/IP

- Need non-serial USB devices (JTAG, HID, etc.)
- Software requires a real /dev/ttyUSB* path
- Multiple device types on same Pi

## Setup

### Pi Side (Server)

#### Option 1: esp_rfc2217_server (Recommended)

```bash
# Install esptool (includes esp_rfc2217_server)
pip3 install esptool

# Run server (replace /dev/ttyUSB0 with your device)
esp_rfc2217_server.py -p 4000 /dev/ttyUSB0
```

#### Option 2: ser2net

```bash
# Install
sudo apt install ser2net

# Configure /etc/ser2net.yaml
connection: &esp32
  accepter: tcp,4000
  connector: serialdev,/dev/ttyUSB0,115200n81,local
  options:
    kickolduser: true
    telnet-brk-on-sync: true

# Start
sudo systemctl enable --now ser2net
```

### VM/Container Side (Client)

#### Flashing with esptool

```bash
# Direct esptool
esptool.py --port rfc2217://192.168.0.87:4000 flash_firmware.bin

# Or set environment variable
export ESPTOOL_PORT=rfc2217://192.168.0.87:4000
esptool.py flash_firmware.bin
```

#### Flashing with ESP-IDF

```bash
# Set port and flash
export ESPPORT=rfc2217://192.168.0.87:4000
idf.py flash

# Or inline
idf.py -p rfc2217://192.168.0.87:4000 flash
```

#### Monitoring

```bash
# ESP-IDF monitor
idf.py -p rfc2217://192.168.0.87:4000 monitor

# Or with esptool
esptool.py --port rfc2217://192.168.0.87:4000 monitor
```

#### PlatformIO

```ini
; platformio.ini
[env:esp32]
upload_port = rfc2217://192.168.0.87:4000
monitor_port = rfc2217://192.168.0.87:4000
```

## Known Issues

1. **Auto-reset timing**: Network latency can affect RTS/DTR timing. `esp_rfc2217_server` handles this better than generic ser2net.

2. **Baud rate changes**: Some operations change baud rate mid-stream. RFC2217 supports this but adds latency.

3. **No local device**: Software that requires `/dev/ttyUSB*` won't work. Use socat to create a PTY if needed:
   ```bash
   socat pty,link=/dev/ttyVUSB0,raw tcp:192.168.0.87:4000
   ```

## References

- [esptool Remote Serial Ports](https://docs.espressif.com/projects/esptool/en/latest/esp32/remote-serial-ports.html)
- [RFC2217 Protocol](https://tools.ietf.org/html/rfc2217)
- [esp_rfc2217_server source](https://github.com/espressif/esptool/blob/master/esptool/reset.py)
