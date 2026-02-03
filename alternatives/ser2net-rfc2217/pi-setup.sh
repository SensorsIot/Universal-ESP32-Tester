#!/bin/bash
# Setup esp_rfc2217_server on Raspberry Pi
# This is the recommended alternative to USB/IP for ESP32 development

set -e

PORT=${1:-4000}
DEVICE=${2:-/dev/ttyUSB0}

echo "=== ESP32 RFC2217 Server Setup ==="
echo "Port: $PORT"
echo "Device: $DEVICE"
echo ""

# Install esptool
echo "Installing esptool..."
pip3 install --user esptool

# Add to PATH if needed
export PATH="$HOME/.local/bin:$PATH"
grep -q '.local/bin' ~/.bashrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc

# Create systemd service
echo "Creating systemd service..."
sudo tee /etc/systemd/system/esp-rfc2217.service > /dev/null << EOF
[Unit]
Description=ESP32 RFC2217 Serial Server
After=network.target

[Service]
Type=simple
User=$USER
ExecStart=$HOME/.local/bin/esp_rfc2217_server.py -p $PORT $DEVICE
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable esp-rfc2217
sudo systemctl start esp-rfc2217

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Server running on port $PORT"
echo "Test from VM/container:"
echo "  esptool.py --port rfc2217://$(hostname -I | awk '{print $1}'):$PORT chip_id"
echo ""
echo "For ESP-IDF:"
echo "  idf.py -p rfc2217://$(hostname -I | awk '{print $1}'):$PORT flash monitor"
