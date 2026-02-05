#!/bin/bash
#
# Push firmware to ESP32 via HTTP OTA
#
# Usage:
#   ota-push.sh <esp32-ip> <firmware.bin>
#   ota-push.sh <esp32-ip>                   # Uses default PlatformIO path
#
# Examples:
#   ota-push.sh 192.168.0.123 firmware.bin
#   ota-push.sh 192.168.0.123                # Auto-finds .pio/build/*/firmware.bin
#
# Environment variables:
#   OTA_PORT - HTTP port on ESP32 (default: 8080)

set -e

OTA_PORT="${OTA_PORT:-8080}"

usage() {
    echo "Usage: $0 <esp32-ip> [firmware.bin]"
    echo ""
    echo "Examples:"
    echo "  $0 192.168.0.123 firmware.bin"
    echo "  $0 192.168.0.123                  # Auto-find in .pio/build/"
    echo ""
    echo "Environment:"
    echo "  OTA_PORT - ESP32 HTTP port (default: 8080)"
    exit 1
}

if [ $# -lt 1 ]; then
    usage
fi

ESP32_IP="$1"
FIRMWARE="${2:-}"

# Auto-find firmware if not specified
if [ -z "$FIRMWARE" ]; then
    # Look for PlatformIO build output
    if [ -d ".pio/build" ]; then
        FIRMWARE=$(find .pio/build -name "firmware.bin" -type f 2>/dev/null | head -1)
    fi

    # Look for ESP-IDF build output
    if [ -z "$FIRMWARE" ] && [ -d "build" ]; then
        FIRMWARE=$(find build -name "*.bin" -path "*build*" ! -name "bootloader.bin" ! -name "partition*.bin" -type f 2>/dev/null | head -1)
    fi

    if [ -z "$FIRMWARE" ]; then
        echo "Error: No firmware.bin found. Specify path or run from project directory."
        exit 1
    fi
    echo "Auto-detected firmware: $FIRMWARE"
fi

if [ ! -f "$FIRMWARE" ]; then
    echo "Error: Firmware file not found: $FIRMWARE"
    exit 1
fi

SIZE=$(stat -c%s "$FIRMWARE" 2>/dev/null || stat -f%z "$FIRMWARE" 2>/dev/null)
echo "Pushing firmware to ESP32..."
echo "  Target: http://${ESP32_IP}:${OTA_PORT}/ota"
echo "  File:   $FIRMWARE"
echo "  Size:   $SIZE bytes"
echo ""

# Check device is reachable
echo "Checking device..."
if ! curl -s --connect-timeout 5 "http://${ESP32_IP}:${OTA_PORT}/health" > /dev/null 2>&1; then
    echo "Warning: Device not responding to health check. Trying anyway..."
fi

# Get current version
echo "Current version:"
curl -s "http://${ESP32_IP}:${OTA_PORT}/version" 2>/dev/null || echo "  (unavailable)"
echo ""

# Push firmware
echo "Uploading firmware..."
RESPONSE=$(curl -s -w "\n%{http_code}" \
    --connect-timeout 10 \
    --max-time 300 \
    -X POST "http://${ESP32_IP}:${OTA_PORT}/ota" \
    -H "Content-Type: application/octet-stream" \
    --data-binary "@${FIRMWARE}" 2>&1)

HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | sed '$d')

echo ""
if [ "$HTTP_CODE" = "200" ]; then
    echo "Success! ESP32 is rebooting with new firmware."
    echo "$BODY"

    # Wait and check new version
    echo ""
    echo "Waiting for reboot..."
    sleep 10

    echo "Checking new version:"
    for i in 1 2 3; do
        if curl -s --connect-timeout 3 "http://${ESP32_IP}:${OTA_PORT}/version" 2>/dev/null; then
            echo ""
            exit 0
        fi
        sleep 2
    done
    echo "  Device not responding yet (may still be booting)"
else
    echo "Error (HTTP $HTTP_CODE):"
    echo "$BODY"
    exit 1
fi
