#!/usr/bin/env python3
"""
ESP32 Discovery Helper

Discovers available ESP32 devices from the RFC2217 portal.

Usage:
    # As a module
    from discover import discover_devices, get_device_url

    devices = discover_devices("<PI_HOST>")
    url = get_device_url("<PI_HOST>", index=0)  # First device

    # Command line
    python discover.py <PI_HOST>
    python discover.py <PI_HOST> --index 1
    python discover.py <PI_HOST> --serial 58DD029450
"""

import json
import os
import sys
try:
    from urllib.request import urlopen
    from urllib.error import URLError
except ImportError:
    from urllib2 import urlopen, URLError


def discover_devices(pi_host, port=8080, timeout=5):
    """
    Discover available ESP32 devices from the portal.

    Args:
        pi_host: IP or hostname of the Raspberry Pi
        port: Portal port (default 8080)
        timeout: Request timeout in seconds

    Returns:
        List of device dicts with 'url', 'port', 'product', 'serial', 'tty'
    """
    url = f"http://{pi_host}:{port}/api/discover"
    try:
        response = urlopen(url, timeout=timeout)
        data = json.loads(response.read().decode())
        return data.get('devices', [])
    except (URLError, Exception) as e:
        print(f"Discovery failed: {e}", file=sys.stderr)
        return []


def get_device_url(pi_host, index=0, serial=None, port=8080):
    """
    Get RFC2217 URL for a specific device.

    Args:
        pi_host: IP or hostname of the Raspberry Pi
        index: Device index (0 = first device)
        serial: Device serial number (overrides index if provided)
        port: Portal port (default 8080)

    Returns:
        RFC2217 URL string or None if not found
    """
    devices = discover_devices(pi_host, port)

    if not devices:
        return None

    # Find by serial if provided
    if serial:
        for d in devices:
            if d.get('serial') == serial:
                return d['url']
        return None

    # Find by index
    if 0 <= index < len(devices):
        return devices[index]['url']

    return None


def get_serial_connection(pi_host, index=0, serial=None, baudrate=115200, timeout=1):
    """
    Get a pyserial connection to the device.

    Args:
        pi_host: IP or hostname of the Raspberry Pi
        index: Device index (0 = first device)
        serial: Device serial number (overrides index if provided)
        baudrate: Serial baud rate
        timeout: Read timeout

    Returns:
        Serial connection object or None
    """
    import serial as pyserial

    url = get_device_url(pi_host, index=index, serial=serial)
    if not url:
        return None

    return pyserial.serial_for_url(url, baudrate=baudrate, timeout=timeout)


# Environment-based auto-discovery
def auto_discover():
    """
    Auto-discover using environment variables.

    Environment variables:
        PI_HOST: Raspberry Pi IP/hostname (required)
        ESP32_INDEX: Device index (default: 0)
        ESP32_SERIAL: Device serial number (optional, overrides index)

    Returns:
        RFC2217 URL string or None
    """
    pi_host = os.environ.get('PI_HOST')
    if not pi_host:
        return None

    index = int(os.environ.get('ESP32_INDEX', '0'))
    serial = os.environ.get('ESP32_SERIAL')

    return get_device_url(pi_host, index=index, serial=serial)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Discover ESP32 devices')
    parser.add_argument('pi_host', nargs='?', help='Pi IP or hostname')
    parser.add_argument('--index', '-i', type=int, default=0, help='Device index')
    parser.add_argument('--serial', '-s', help='Device serial number')
    parser.add_argument('--list', '-l', action='store_true', help='List all devices')
    parser.add_argument('--json', '-j', action='store_true', help='Output as JSON')
    args = parser.parse_args()

    # Use environment variable if no host provided
    pi_host = args.pi_host or os.environ.get('PI_HOST')

    if not pi_host:
        print("Usage: discover.py <pi-host> [--list] [--index N] [--serial S]")
        print("Or set PI_HOST environment variable")
        sys.exit(1)

    if args.list:
        devices = discover_devices(pi_host)
        if args.json:
            print(json.dumps(devices, indent=2))
        else:
            if not devices:
                print("No devices found")
            else:
                for i, d in enumerate(devices):
                    print(f"[{i}] {d['url']}")
                    if d.get('product'):
                        print(f"    Product: {d['product']}")
                    if d.get('serial'):
                        print(f"    Serial:  {d['serial']}")
    else:
        url = get_device_url(pi_host, index=args.index, serial=args.serial)
        if url:
            print(url)
        else:
            print("Device not found", file=sys.stderr)
            sys.exit(1)
