#!/usr/bin/env python3
"""
ESP32 Serial Monitor

Connects to ESP32 via RFC2217 and monitors serial output.

Environment variables:
    PI_HOST: Raspberry Pi IP/hostname (required for auto-discovery)
    ESP32_PORT: Full RFC2217 URL (optional, overrides auto-discovery)
    ESP32_INDEX: Device index for auto-discovery (default: 0)
    ESP32_SERIAL: Device serial for auto-discovery (optional)

Usage:
    # With explicit URL
    ESP32_PORT=rfc2217://<PI_HOST>:4001 python monitor.py

    # With auto-discovery (first device)
    PI_HOST=<PI_HOST> python monitor.py

    # With auto-discovery (second device)
    PI_HOST=<PI_HOST> ESP32_INDEX=1 python monitor.py

    # With auto-discovery (by serial number)
    PI_HOST=<PI_HOST> ESP32_SERIAL=58DD029450 python monitor.py
"""

import os
import sys
import serial


def get_port():
    """Get serial port from environment or auto-discovery."""

    # Check for explicit port first
    port = os.environ.get('ESP32_PORT')
    if port:
        return port

    # Try auto-discovery
    pi_host = os.environ.get('PI_HOST')
    if pi_host:
        try:
            from discover import get_device_url
            index = int(os.environ.get('ESP32_INDEX', '0'))
            serial_num = os.environ.get('ESP32_SERIAL')
            port = get_device_url(pi_host, index=index, serial=serial_num)
            if port:
                return port
        except ImportError:
            # discover.py not available, try direct API call
            import json
            try:
                from urllib.request import urlopen
            except ImportError:
                from urllib2 import urlopen

            try:
                url = f"http://{pi_host}:8080/api/discover"
                response = urlopen(url, timeout=5)
                data = json.loads(response.read().decode())
                devices = data.get('devices', [])

                serial_num = os.environ.get('ESP32_SERIAL')
                if serial_num:
                    for d in devices:
                        if d.get('serial') == serial_num:
                            return d['url']

                index = int(os.environ.get('ESP32_INDEX', '0'))
                if 0 <= index < len(devices):
                    return devices[index]['url']
            except Exception as e:
                print(f"Auto-discovery failed: {e}", file=sys.stderr)

    return None


def main():
    port = get_port()

    if not port:
        print("Error: No ESP32 port configured", file=sys.stderr)
        print("Set ESP32_PORT=rfc2217://pi:4001 or PI_HOST=pi-ip", file=sys.stderr)
        sys.exit(1)

    print(f"Connecting to {port}...")

    try:
        ser = serial.serial_for_url(port, baudrate=115200, timeout=1)
        print("Connected. Monitoring... (Ctrl+C to exit)")
        print("-" * 50)

        while True:
            line = ser.readline()
            if line:
                try:
                    text = line.decode('utf-8', errors='replace').rstrip()
                    print(text)
                except Exception:
                    print(line)

    except KeyboardInterrupt:
        print("\nDisconnected.")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
