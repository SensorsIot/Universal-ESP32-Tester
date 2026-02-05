#!/usr/bin/env python3
"""RFC2217 Serial Portal - Web interface for managing serial-over-TCP servers"""

import http.server
import json
import subprocess
import os
import re
import socketserver
import signal
import time
import glob
from urllib.parse import urlparse

PORT = 8080
CONFIG_FILE = "/etc/rfc2217/devices.conf"
LOG_DIR = "/var/log/serial"
RFC2217_BASE_PORT = 4001  # Start from 4001, one port per device

HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>RFC2217 Serial Portal</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               max-width: 800px; margin: 0 auto; padding: 20px; background: #f5f5f5; }
        h1 { color: #333; border-bottom: 2px solid #17a2b8; padding-bottom: 10px; }
        .card { background: white; border-radius: 8px; padding: 20px; margin: 15px 0;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .device { padding: 15px; background: #f8f9fa; margin: 10px 0; border-radius: 4px;
                  border-left: 4px solid #17a2b8; }
        .device.stopped { border-left-color: #dc3545; opacity: 0.7; }
        .device-header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
        .device-name { font-weight: bold; color: #333; }
        .device-serial { font-size: 12px; color: #666; }
        .device-url { font-family: monospace; background: #e9ecef; padding: 8px 12px;
                      border-radius: 4px; margin-top: 10px; font-size: 14px; word-break: break-all; }
        .device-url.stopped { color: #999; }
        .copy-btn { font-size: 11px; padding: 4px 8px; cursor: pointer; background: #6c757d;
                    color: white; border: none; border-radius: 3px; margin-left: 10px; }
        .copy-btn:hover { background: #5a6268; }
        button { background: #17a2b8; color: white; border: none; padding: 10px 20px;
                border-radius: 4px; cursor: pointer; font-size: 14px; margin: 5px 5px 5px 0; }
        button:hover { background: #138496; }
        button.danger { background: #dc3545; }
        button.danger:hover { background: #c82333; }
        button.success { background: #28a745; }
        button.success:hover { background: #218838; }
        .status-badge { font-size: 11px; padding: 3px 8px; border-radius: 3px; font-weight: bold; }
        .status-badge.running { background: #28a745; color: white; }
        .status-badge.stopped { background: #dc3545; color: white; }
        .info-box { background: #d1ecf1; border: 1px solid #bee5eb; border-radius: 4px;
                    padding: 15px; margin: 15px 0; color: #0c5460; }
        .info-box code { background: #fff; padding: 2px 6px; border-radius: 3px; }
        .refresh-btn { font-size: 12px; padding: 5px 10px; }
        .actions { margin-top: 10px; }
        pre { background: #1e1e1e; color: #0f0; padding: 15px; border-radius: 4px;
              font-size: 13px; overflow-x: auto; }
    </style>
</head>
<body>
    <h1>RFC2217 Serial Portal</h1>

    <div class="card">
        <h3>Network Info</h3>
        <div class="info-box">
            <strong>Pi Address:</strong> <code id="pi-addr">Loading...</code><br>
            <small>Use this IP in your container's RFC2217 URL</small>
        </div>
    </div>

    <div class="card">
        <h3>Serial Devices <button class="refresh-btn" onclick="loadDevices()">Refresh</button></h3>
        <div id="devices">Loading...</div>
        <div style="margin-top: 15px;">
            <button onclick="startAll()" class="success">Start All</button>
            <button onclick="stopAll()" class="danger">Stop All</button>
        </div>
    </div>

    <div class="card">
        <h3>Usage Examples</h3>
        <p><strong>Python (pyserial):</strong></p>
        <pre>import serial
ser = serial.serial_for_url("rfc2217://PI_IP:4001", baudrate=115200)</pre>

        <p><strong>esptool:</strong></p>
        <pre>esptool --port rfc2217://PI_IP:4001?ign_set_control flash_id</pre>

        <p><strong>PlatformIO (platformio.ini):</strong></p>
        <pre>upload_port = rfc2217://PI_IP:4001?ign_set_control
monitor_port = rfc2217://PI_IP:4001?ign_set_control</pre>

        <p><strong>Create local /dev/tty (socat):</strong></p>
        <pre>socat pty,link=/dev/ttyESP32,raw tcp:PI_IP:4001</pre>
    </div>

    <script>
        let piAddr = '';

        function log(msg) { console.log(msg); }

        async function api(endpoint, method='GET', body=null) {
            const opts = { method };
            if (body) { opts.headers = {'Content-Type':'application/json'}; opts.body = JSON.stringify(body); }
            const res = await fetch('/api/' + endpoint, opts);
            return res.json();
        }

        async function loadInfo() {
            const data = await api('info');
            piAddr = data.ip || 'PI_IP';
            document.getElementById('pi-addr').textContent = piAddr;
        }

        async function loadDevices() {
            const data = await api('devices');
            const el = document.getElementById('devices');
            const devices = data.devices || [];

            if (devices.length === 0) {
                el.innerHTML = '<p style="color:#666">No serial devices detected. Connect an ESP32 or Arduino.</p>';
                return;
            }

            el.innerHTML = devices.map(d => {
                const statusClass = d.running ? 'running' : 'stopped';
                const deviceClass = d.running ? '' : 'stopped';
                const url = 'rfc2217://' + piAddr + ':' + d.port;
                return '<div class="device ' + deviceClass + '">' +
                    '<div class="device-header">' +
                    '<div>' +
                    '<span class="device-name">' + (d.product || d.tty) + '</span>' +
                    (d.serial ? ' <span class="device-serial">[' + d.serial + ']</span>' : '') +
                    '</div>' +
                    '<span class="status-badge ' + statusClass + '">' + (d.running ? 'Running' : 'Stopped') + '</span>' +
                    '</div>' +
                    '<div class="device-url ' + (d.running ? '' : 'stopped') + '">' +
                    '<strong>Port ' + d.port + ':</strong> ' + url +
                    '<button class="copy-btn" onclick="copyUrl(\\'' + url + '\\')">Copy</button>' +
                    '</div>' +
                    '<div class="actions">' +
                    (d.running ?
                        '<button class="danger" onclick="stopDevice(\\'' + d.tty + '\\')">Stop</button>' :
                        '<button class="success" onclick="startDevice(\\'' + d.tty + '\\')">Start</button>') +
                    '</div>' +
                    '</div>';
            }).join('');
        }

        function copyUrl(url) {
            navigator.clipboard.writeText(url);
        }

        async function startDevice(tty) {
            await api('start', 'POST', { tty });
            loadDevices();
        }

        async function stopDevice(tty) {
            await api('stop', 'POST', { tty });
            loadDevices();
        }

        async function startAll() {
            await api('start-all', 'POST');
            loadDevices();
        }

        async function stopAll() {
            await api('stop-all', 'POST');
            loadDevices();
        }

        loadInfo();
        loadDevices();
        setInterval(loadDevices, 5000);
    </script>
</body>
</html>
"""

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def send_html(self, html):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def get_ip(self):
        """Get Pi's IP address"""
        try:
            result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=5)
            ips = result.stdout.strip().split()
            return ips[0] if ips else '127.0.0.1'
        except:
            return '127.0.0.1'

    def read_config(self):
        """Read device-port assignments: {key: port} where key is serial or tty"""
        config = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE) as f:
                    for line in f:
                        if '=' in line and not line.strip().startswith('#'):
                            key, port = line.strip().split('=', 1)
                            config[key] = int(port)
            except: pass
        return config

    def write_config(self, config):
        """Write device-port assignments (keyed by serial number or tty)"""
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            f.write("# RFC2217 device-port assignments (by serial number or tty)\n")
            for key, port in sorted(config.items(), key=lambda x: x[1]):
                f.write(f"{key}={port}\n")

    def normalize_serial(self, serial):
        """Normalize serial number for use as config key (replace colons with underscores)"""
        if not serial:
            return ''
        return serial.replace(':', '_').replace(' ', '_')

    def get_serial_devices(self):
        """Find all serial devices"""
        devices = []

        # Check /dev/ttyUSB* and /dev/ttyACM*
        for pattern in ['/dev/ttyUSB*', '/dev/ttyACM*']:
            for tty in glob.glob(pattern):
                info = self.get_device_info(tty)
                if info:
                    devices.append(info)

        return devices

    def get_device_info(self, tty):
        """Get device info from sysfs"""
        info = {'tty': tty, 'product': '', 'serial': '', 'vendor': ''}

        # Find sysfs path
        tty_name = os.path.basename(tty)
        sysfs_path = f"/sys/class/tty/{tty_name}/device"

        if not os.path.exists(sysfs_path):
            return info

        # Walk up to find USB device attributes
        try:
            device_path = os.path.realpath(sysfs_path)
            # Go up a few levels to find USB device
            for _ in range(5):
                device_path = os.path.dirname(device_path)
                product_file = os.path.join(device_path, 'product')
                if os.path.exists(product_file):
                    break

            for attr in ['product', 'serial', 'manufacturer']:
                attr_file = os.path.join(device_path, attr)
                if os.path.exists(attr_file):
                    try:
                        with open(attr_file) as f:
                            info[attr] = f.read().strip()
                    except: pass
        except: pass

        return info

    def get_running_servers(self):
        """Get running serial proxy or esp_rfc2217_server processes: {tty: {port, pid}}"""
        servers = {}
        # Check for both serial_proxy and esp_rfc2217_server
        for pattern in ['serial.proxy', 'serial_proxy', 'esp_rfc2217_server']:
            try:
                result = subprocess.run(['pgrep', '-a', '-f', pattern],
                    capture_output=True, text=True)
                for line in result.stdout.strip().split('\n'):
                    if not line:
                        continue
                    parts = line.split()
                    pid = int(parts[0])
                    # Parse args: -p PORT ... TTY
                    port = None
                    tty = None
                    for i, arg in enumerate(parts):
                        if arg == '-p' and i+1 < len(parts):
                            try:
                                port = int(parts[i+1])
                            except: pass
                        if arg.startswith('/dev/tty'):
                            tty = arg
                    if tty and port:
                        servers[tty] = {'port': port, 'pid': pid}
            except: pass
        return servers

    def assign_port(self, device_info, config):
        """Assign a port to a device based on serial number (persistent) or tty (fallback)"""
        tty = device_info['tty'] if isinstance(device_info, dict) else device_info
        serial = self.normalize_serial(device_info.get('serial', '')) if isinstance(device_info, dict) else ''

        # 1. Try serial number first (persistent across reconnects)
        if serial and serial in config:
            return config[serial]

        # 2. Fallback to tty (legacy config entries)
        if tty in config:
            # Migrate: if we have serial, re-save with serial as key
            if serial:
                port = config[tty]
                del config[tty]
                config[serial] = port
                self.write_config(config)
            return config.get(serial, config.get(tty))

        # 3. Assign next available port
        used_ports = set(config.values())
        port = RFC2217_BASE_PORT
        while port in used_ports:
            port += 1

        # 4. Save with serial as key (preferred) or tty as fallback
        key = serial if serial else tty
        config[key] = port
        self.write_config(config)
        return port

    def start_server(self, tty):
        """Start serial proxy with logging for device"""
        # Get device info for serial-based port assignment
        device_info = self.get_device_info(tty)
        config = self.read_config()
        port = self.assign_port(device_info, config)

        # Check if already running
        running = self.get_running_servers()
        if tty in running:
            return True, f"Already running on port {running[tty]['port']}"

        # Find serial_proxy or fall back to esp_rfc2217_server
        server_paths = [
            '/usr/local/bin/serial-proxy',
            '/usr/local/bin/serial_proxy.py',
            '/usr/local/bin/esp_rfc2217_server',
            '/usr/local/bin/esp_rfc2217_server.py',
            os.path.expanduser('~/.local/bin/esp_rfc2217_server.py'),
        ]

        server_cmd = None
        use_proxy = False
        for path in server_paths:
            if os.path.exists(path):
                server_cmd = path
                use_proxy = 'proxy' in path
                break

        if not server_cmd:
            return False, "serial_proxy not found"

        try:
            # Create log directory
            os.makedirs(LOG_DIR, exist_ok=True)

            if use_proxy:
                proc = subprocess.Popen(
                    [server_cmd, '-p', str(port), '-l', LOG_DIR, tty],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True)
            else:
                proc = subprocess.Popen(
                    [server_cmd, '-p', str(port), tty],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True)

            time.sleep(0.5)  # Give it time to start

            # Verify it's running
            if proc.poll() is None:
                msg = f"Started on port {port}"
                if use_proxy:
                    msg += f" (logging to {LOG_DIR})"
                return True, msg
            else:
                return False, "Server exited immediately"
        except Exception as e:
            return False, str(e)

    def stop_server(self, tty):
        """Stop RFC2217 server for device"""
        running = self.get_running_servers()
        if tty not in running:
            return True, "Not running"

        try:
            os.kill(running[tty]['pid'], signal.SIGTERM)
            time.sleep(0.3)
            return True, "Stopped"
        except Exception as e:
            return False, str(e)

    def switch_to_flash_mode(self, tty):
        """Switch to esp_rfc2217_server for fast flashing (no logging)"""
        # Stop current server
        self.stop_server(tty)
        time.sleep(0.5)

        # Get port assignment (using device info for serial-based lookup)
        device_info = self.get_device_info(tty)
        config = self.read_config()
        port = self.assign_port(device_info, config)

        # Find esp_rfc2217_server
        server_paths = [
            '/usr/local/bin/esp_rfc2217_server.py',
            '/usr/local/bin/esp_rfc2217_server',
            os.path.expanduser('~/.local/bin/esp_rfc2217_server.py'),
        ]
        server_cmd = None
        for path in server_paths:
            if os.path.exists(path):
                server_cmd = path
                break

        if not server_cmd:
            return False, "esp_rfc2217_server not found"

        try:
            subprocess.Popen(
                [server_cmd, '-p', str(port), tty],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            time.sleep(0.5)
            return True, f"Flash mode on port {port} (no logging, faster)"
        except Exception as e:
            return False, str(e)

    def switch_to_log_mode(self, tty):
        """Switch back to serial-proxy for logging"""
        # Stop current server
        self.stop_server(tty)
        time.sleep(0.5)

        # Start serial-proxy
        return self.start_server(tty)

    def get_devices(self):
        """Get all devices with their status"""
        devices = self.get_serial_devices()
        config = self.read_config()
        running = self.get_running_servers()

        result = []
        for d in devices:
            tty = d['tty']
            port = self.assign_port(d, config)  # Pass full device info for serial-based lookup
            result.append({
                'tty': tty,
                'product': d.get('product', ''),
                'serial': d.get('serial', ''),
                'port': port,
                'running': tty in running
            })

        return result

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/':
            self.send_html(HTML_TEMPLATE)
        elif path == '/api/info':
            self.send_json({'ip': self.get_ip()})
        elif path == '/api/devices':
            self.send_json({'devices': self.get_devices()})
        elif path == '/api/discover':
            # Simple discovery endpoint - returns list of RFC2217 URLs
            ip = self.get_ip()
            devices = self.get_devices()
            urls = []
            for d in devices:
                if d['running']:
                    urls.append({
                        'url': f"rfc2217://{ip}:{d['port']}",
                        'port': d['port'],
                        'product': d.get('product', ''),
                        'serial': d.get('serial', ''),
                        'tty': d['tty']
                    })
            self.send_json({'devices': urls})
        elif path == '/api/logs':
            # List available log files
            logs = []
            if os.path.exists(LOG_DIR):
                for f in sorted(os.listdir(LOG_DIR), reverse=True):
                    if f.endswith('.log'):
                        fpath = os.path.join(LOG_DIR, f)
                        logs.append({
                            'name': f,
                            'size': os.path.getsize(fpath),
                            'modified': os.path.getmtime(fpath)
                        })
            self.send_json({'logs': logs})
        elif path.startswith('/api/logs/'):
            # Get log content
            log_name = path[len('/api/logs/'):]
            log_path = os.path.join(LOG_DIR, log_name)
            if os.path.exists(log_path) and log_path.startswith(LOG_DIR):
                # Get optional tail parameter from query string
                query = urlparse(self.path).query
                lines = 100  # Default
                for param in query.split('&'):
                    if param.startswith('lines='):
                        try:
                            lines = int(param.split('=')[1])
                        except: pass
                try:
                    with open(log_path, 'r') as f:
                        all_lines = f.readlines()
                        content = ''.join(all_lines[-lines:])
                    self.send_json({'content': content, 'total_lines': len(all_lines)})
                except Exception as e:
                    self.send_json({'error': str(e)}, 500)
            else:
                self.send_json({'error': 'Log not found'}, 404)
        else:
            self.send_json({'error': 'Not found'}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        content_len = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(content_len)) if content_len else {}

        if path == '/api/start':
            tty = body.get('tty', '')
            if not tty:
                self.send_json({'success': False, 'error': 'Missing tty'})
                return
            ok, msg = self.start_server(tty)
            self.send_json({'success': ok, 'message': msg})

        elif path == '/api/stop':
            tty = body.get('tty', '')
            if not tty:
                self.send_json({'success': False, 'error': 'Missing tty'})
                return
            ok, msg = self.stop_server(tty)
            self.send_json({'success': ok, 'message': msg})

        elif path == '/api/start-all':
            results = []
            for d in self.get_serial_devices():
                ok, msg = self.start_server(d['tty'])
                results.append(f"{d['tty']}: {msg}")
            self.send_json({'success': True, 'messages': results})

        elif path == '/api/stop-all':
            running = self.get_running_servers()
            results = []
            for tty in running:
                ok, msg = self.stop_server(tty)
                results.append(f"{tty}: {msg}")
            self.send_json({'success': True, 'messages': results})

        elif path == '/api/flash-mode':
            # Switch to esp_rfc2217_server for fast flashing (no logging)
            tty = body.get('tty', '')
            if not tty:
                self.send_json({'success': False, 'error': 'Missing tty'})
                return
            ok, msg = self.switch_to_flash_mode(tty)
            self.send_json({'success': ok, 'message': msg})

        elif path == '/api/log-mode':
            # Switch back to serial-proxy for logging
            tty = body.get('tty', '')
            if not tty:
                self.send_json({'success': False, 'error': 'Missing tty'})
                return
            ok, msg = self.switch_to_log_mode(tty)
            self.send_json({'success': ok, 'message': msg})

        else:
            self.send_json({'error': 'Not found'}, 404)

def get_serial_devices_standalone():
    """Find all serial devices (standalone function)"""
    devices = []
    for pattern in ['/dev/ttyUSB*', '/dev/ttyACM*']:
        for tty in glob.glob(pattern):
            devices.append(tty)
    return devices

def get_device_info_standalone(tty):
    """Get device info from sysfs (standalone function)"""
    info = {'tty': tty, 'product': '', 'serial': '', 'vendor': ''}
    tty_name = os.path.basename(tty)
    sysfs_path = f"/sys/class/tty/{tty_name}/device"

    if not os.path.exists(sysfs_path):
        return info

    try:
        device_path = os.path.realpath(sysfs_path)
        for _ in range(5):
            device_path = os.path.dirname(device_path)
            product_file = os.path.join(device_path, 'product')
            if os.path.exists(product_file):
                break

        for attr in ['product', 'serial', 'manufacturer']:
            attr_file = os.path.join(device_path, attr)
            if os.path.exists(attr_file):
                try:
                    with open(attr_file) as f:
                        info[attr] = f.read().strip()
                except: pass
    except: pass
    return info

def normalize_serial_standalone(serial):
    """Normalize serial number for use as config key"""
    if not serial:
        return ''
    return serial.replace(':', '_').replace(' ', '_')

def start_server_standalone(tty, config_file=CONFIG_FILE):
    """Start serial proxy with logging for device (standalone function)"""
    # Get device info for serial-based port assignment
    device_info = get_device_info_standalone(tty)
    serial = normalize_serial_standalone(device_info.get('serial', ''))

    # Read config
    config = {}
    if os.path.exists(config_file):
        try:
            with open(config_file) as f:
                for line in f:
                    if '=' in line and not line.strip().startswith('#'):
                        key, p = line.strip().split('=', 1)
                        config[key] = int(p)
        except: pass

    # Assign port (prefer serial number for persistence)
    port = None
    key = None

    # 1. Try serial number first
    if serial and serial in config:
        port = config[serial]
        key = serial
    # 2. Fallback to tty (legacy)
    elif tty in config:
        port = config[tty]
        # Migrate to serial-based key
        if serial:
            del config[tty]
            config[serial] = port
            key = serial
        else:
            key = tty
    # 3. Assign new port
    else:
        used_ports = set(config.values())
        port = RFC2217_BASE_PORT
        while port in used_ports:
            port += 1
        key = serial if serial else tty
        config[key] = port

    # Save config
    os.makedirs(os.path.dirname(config_file), exist_ok=True)
    with open(config_file, 'w') as f:
        f.write("# RFC2217 device-port assignments (by serial number or tty)\n")
        for k, p in sorted(config.items(), key=lambda x: x[1]):
            f.write(f"{k}={p}\n")

    # Check if already running
    try:
        for pattern in ['serial.proxy', 'serial_proxy', 'esp_rfc2217_server']:
            result = subprocess.run(['pgrep', '-a', '-f', pattern],
                capture_output=True, text=True)
            if tty in result.stdout:
                return True, f"Already running on port {port}"
    except: pass

    # Find server command (prefer serial_proxy for logging)
    server_paths = [
        '/usr/local/bin/serial-proxy',
        '/usr/local/bin/serial_proxy.py',
        '/usr/local/bin/esp_rfc2217_server',
        '/usr/local/bin/esp_rfc2217_server.py',
        os.path.expanduser('~/.local/bin/esp_rfc2217_server.py'),
    ]
    server_cmd = None
    use_proxy = False
    for path in server_paths:
        if os.path.exists(path):
            server_cmd = path
            use_proxy = 'proxy' in path
            break

    if not server_cmd:
        return False, "serial_proxy not found"

    # Create log directory
    os.makedirs(LOG_DIR, exist_ok=True)

    # Start server
    try:
        if use_proxy:
            subprocess.Popen(
                [server_cmd, '-p', str(port), '-l', LOG_DIR, tty],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            return True, f"Started on port {port} (logging)"
        else:
            subprocess.Popen(
                [server_cmd, '-p', str(port), tty],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            return True, f"Started on port {port}"
    except Exception as e:
        return False, str(e)

def auto_start_all():
    """Start RFC2217 servers for all connected devices"""
    devices = get_serial_devices_standalone()
    for tty in devices:
        ok, msg = start_server_standalone(tty)
        print(f"  {tty}: {msg}")

if __name__ == '__main__':
    print("Starting RFC2217 Portal...")

    # Auto-start servers for all connected devices
    print("Auto-starting RFC2217 servers:")
    time.sleep(2)  # Wait for USB devices to settle
    auto_start_all()

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(('', PORT), Handler) as httpd:
        print(f"Portal running on http://0.0.0.0:{PORT}")
        httpd.serve_forever()
