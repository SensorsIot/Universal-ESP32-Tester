#!/usr/bin/env python3
"""USB/IP Setup Portal - Web interface for configuring Pi-VM pairing"""

import http.server
import json
import subprocess
import socket
import os
import re
import socketserver
from urllib.parse import urlparse

PORT = 8080
CONFIG_FILE = "/etc/usbip/vm.conf"

HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>USB/IP Setup Portal</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               max-width: 800px; margin: 0 auto; padding: 20px; background: #f5f5f5; }
        h1 { color: #333; border-bottom: 2px solid #007bff; padding-bottom: 10px; }
        .card { background: white; border-radius: 8px; padding: 20px; margin: 15px 0;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .status { padding: 10px 15px; border-radius: 5px; margin: 10px 0; }
        .status.connected { background: #d4edda; color: #155724; }
        .status.disconnected { background: #f8d7da; color: #721c24; }
        input[type="text"] { width: 100%; padding: 10px; border: 1px solid #ddd;
                            border-radius: 4px; font-size: 16px; }
        button { background: #007bff; color: white; border: none; padding: 10px 20px;
                border-radius: 4px; cursor: pointer; font-size: 16px; margin: 5px 5px 5px 0; }
        button:hover { background: #0056b3; }
        button.danger { background: #dc3545; }
        button.success { background: #28a745; }
        .log { background: #1e1e1e; color: #0f0; padding: 15px; border-radius: 4px;
               font-family: monospace; font-size: 13px; max-height: 200px; overflow-y: auto; }
        .device { display: flex; justify-content: space-between; align-items: center;
                 padding: 10px; background: #f8f9fa; margin: 5px 0; border-radius: 4px; }
        .device.attached { border-left: 4px solid #28a745; }
        .device.bound { border-left: 4px solid #ffc107; }
        .vm-option { padding: 10px; margin: 5px 0; background: #f8f9fa; border-radius: 4px;
                    cursor: pointer; border: 2px solid transparent; }
        .vm-option:hover { border-color: #007bff; }
        .vm-option.configured { border-left: 4px solid #28a745; }
        #log { white-space: pre-wrap; }
        .refresh-btn { font-size: 12px; padding: 5px 10px; }
    </style>
</head>
<body>
    <h1>USB/IP Setup Portal</h1>
    <div class="card">
        <h3>Status</h3>
        <div id="status" class="status disconnected">Loading...</div>
    </div>
    <div class="card">
        <h3>USB Devices <button class="refresh-btn" onclick="loadDevices()">Refresh</button></h3>
        <div id="devices">Loading...</div>
    </div>
    <div class="card">
        <h3>VM Configuration</h3>
        <div id="vm-list"><p>Scanning...</p></div>
        <p style="margin-top:15px"><strong>Or enter manually:</strong></p>
        <input type="text" id="vm-input" placeholder="hostname or IP"><p style="margin-top:10px"><strong>Username:</strong></p><input type="text" id="vm-user" value="dev" placeholder="dev">
        <div style="margin-top:15px">
            <button onclick="testConnection()">Test Connection</button>
            <button onclick="setupPairing()" class="success">Setup Pairing</button>
            <button onclick="attachAll()" class="success">Attach All</button>
            <button onclick="disconnect()" class="danger">Disconnect</button>
        </div>
    </div>
    <div class="card">
        <h3>Log</h3>
        <div class="log"><div id="log">Ready.</div></div>
    </div>
    <script>
        let selectedVm = '';
        function log(msg) {
            const el = document.getElementById('log');
            el.textContent += '\\n> ' + msg;
            el.parentElement.scrollTop = el.parentElement.scrollHeight;
        }
        async function api(endpoint, method='GET', body=null) {
            const opts = { method };
            if (body) { opts.headers = {'Content-Type':'application/json'}; opts.body = JSON.stringify(body); }
            const res = await fetch('/api/' + endpoint, opts);
            return res.json();
        }
        async function loadStatus() {
            const data = await api('status');
            const el = document.getElementById('status');
            if (data.vm_host) {
                el.className = 'status connected';
                el.innerHTML = '&#9679; Connected to <strong>' + data.vm_user + '@' + data.vm_host + '</strong>';
                document.getElementById('vm-input').value = data.vm_host;
                document.getElementById('vm-user').value = data.vm_user || 'dev';
                selectedVm = data.vm_host;
            } else {
                el.className = 'status disconnected';
                el.textContent = 'Not configured';
            }
        }
        async function loadDevices() {
            const data = await api('devices');
            const el = document.getElementById('devices');
            const devices = (data.devices || []).filter(d => !d.skipped);
            if (devices.length > 0) {
                el.innerHTML = devices.map(d => {
                    let info = d.product || d.name;
                    if (d.serial) info += ' <small style="color:#666">[' + d.serial + ']</small>';
                    return '<div class="device ' + (d.attached ? 'attached' : 'bound') + '">' +
                        '<div><strong>' + d.busid + '</strong>: ' + info + '</div>' +
                        '<div>' + (d.attached ? '&#10003; attached' : '&#9679; bound') + '</div></div>';
                }).join('');
            } else { el.innerHTML = '<p>No serial devices</p>'; }
        }
        async function loadVms() {
            const data = await api('scan');
            const el = document.getElementById('vm-list');
            if (data.vms && data.vms.length > 0) {
                el.innerHTML = '<p><strong>Discovered:</strong></p>' +
                    data.vms.map(vm => 
                        '<div class="vm-option' + (vm.configured ? ' configured' : '') + '" onclick="selectVm(this, \\'' + vm.ip + '\\')">' +
                        (vm.host || vm.ip) + ' (' + vm.ip + ')' + (vm.configured ? ' &#10003;' : '') + '</div>'
                    ).join('');
            } else { el.innerHTML = '<p>No VMs discovered. Enter IP below.</p>'; }
        }
        function selectVm(el, host) {
            document.querySelectorAll('.vm-option').forEach(e => e.classList.remove('selected'));
            el.classList.add('selected');
            document.getElementById('vm-input').value = host;
            selectedVm = host;
        }
        function getVmHost() { return document.getElementById('vm-input').value.trim() || selectedVm; }
        function getVmUser() { return document.getElementById('vm-user').value.trim() || 'dev'; }
        async function testConnection() {
            const host = getVmHost();
            const user = getVmUser();
            if (!host) { log('Enter VM address'); return; }
            log('Testing ' + user + '@' + host + '...');
            const data = await api('test', 'POST', { host, user });
            log(data.message);
        }
        async function setupPairing() {
            const host = getVmHost();
            const user = getVmUser();
            if (!host) { log('Enter VM address'); return; }
            log('Setting up ' + user + '@' + host + '...');
            const data = await api('setup', 'POST', { host, user });
            for (const msg of data.log || []) { log(msg); }
            log(data.success ? 'Setup complete!' : 'Failed: ' + data.error);
            loadStatus(); loadDevices();
        }
        async function attachAll() {
            log('Attaching all devices...');
            const data = await api('attach-all', 'POST');
            for (const msg of data.log || []) { log(msg); }
            loadDevices();
        }
        async function disconnect() {
            if (!confirm('Remove pairing?')) return;
            log('Disconnecting...');
            const data = await api('disconnect', 'POST');
            log(data.message);
            loadStatus(); loadDevices();
        }
        loadStatus(); loadDevices(); loadVms();
        setInterval(loadDevices, 10000);
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
    
    def read_config(self):
        config = {'vm_host': '', 'vm_user': 'dev'}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE) as f:
                for line in f:
                    if '=' in line and not line.strip().startswith('#'):
                        k, v = line.strip().split('=', 1)
                        if k == 'VM_HOST': config['vm_host'] = v
                        if k == 'VM_USER': config['vm_user'] = v
        return config
    
    def write_config(self, host, user='dev'):
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            f.write(f"VM_HOST={host}\nVM_USER={user}\n")
    
    def get_vm_attached(self):
        """Get busids attached on VM"""
        attached = set()
        try:
            config = self.read_config()
            if config.get('vm_host'):
                result = subprocess.run(
                    ['ssh', '-o', 'ConnectTimeout=3', '-o', 'BatchMode=yes',
                     f"{config['vm_user']}@{config['vm_host']}",
                     'sudo /usr/sbin/usbip port 2>/dev/null'],
                    capture_output=True, text=True, timeout=10)
                for line in result.stdout.split('\n'):
                    m = re.search(r'usbip://[^/]+/([0-9]+-[0-9.]+)', line)
                    if m:
                        attached.add(m.group(1))
        except: pass
        return attached

    def get_device_info(self, busid):
        """Read device info from sysfs"""
        info = {}
        sysfs = f"/sys/bus/usb/devices/{busid}"
        for attr in ['product', 'serial', 'manufacturer', 'idVendor', 'idProduct']:
            try:
                with open(f"{sysfs}/{attr}") as f:
                    info[attr] = f.read().strip()
            except: pass
        return info

    def get_devices(self):
        devices = []
        attached = self.get_vm_attached()
        try:
            result = subprocess.run(['/usr/sbin/usbip', 'list', '-l'], capture_output=True, text=True)
            current_busid = None
            for line in result.stdout.split('\n'):
                m = re.match(r'\s+-\s+busid\s+(\S+)\s+\(([0-9a-f:]+)\)', line)
                if m:
                    current_busid = m.group(1)
                elif current_busid and line.strip() and not line.startswith(' -'):
                    name = line.strip()
                    skipped = 'ethernet' in name.lower()
                    info = self.get_device_info(current_busid)
                    devices.append({
                        'busid': current_busid,
                        'name': name,
                        'product': info.get('product', ''),
                        'serial': info.get('serial', ''),
                        'skipped': skipped,
                        'attached': current_busid in attached
                    })
                    current_busid = None
        except: pass
        return devices
    
    def scan_vms(self):
        vms = []
        seen = set()
        config = self.read_config()
        if config.get('vm_host'):
            vms.append({'host': config['vm_host'], 'ip': config['vm_host'], 'configured': True})
            seen.add(config['vm_host'])
        for name in ['dev-1.local', 'dev-2.local']:
            try:
                ip = socket.gethostbyname(name)
                if ip not in seen:
                    vms.append({'host': name.replace('.local',''), 'ip': ip})
                    seen.add(ip)
            except: pass
        return vms
    
    def test_connection(self, host, user='dev'):
        try:
            result = subprocess.run(
                ['ssh', '-o', 'ConnectTimeout=5', '-o', 'BatchMode=yes', f'{user}@{host}', 'echo OK'],
                capture_output=True, text=True, timeout=10)
            return result.returncode == 0, result.stdout.strip() or result.stderr.strip()
        except Exception as e:
            return False, str(e)
    
    def setup_pairing(self, host, user='dev'):
        log = []
        try:
            log.append(f"Testing SSH to {user}@{host}...")
            ok, msg = self.test_connection(host, user)
            if not ok:
                return False, f"SSH failed: {msg}", log
            log.append("SSH OK")
            
            log.append("Saving config...")
            self.write_config(host, user)
            
            log.append("Attaching all devices...")
            for d in self.get_devices():
                if not d['skipped'] and not d['attached']:
                    result = subprocess.run(['/usr/local/bin/notify-vm.sh', 'boot', d['busid']],
                        capture_output=True, text=True, timeout=60)
                    if 'Success' in result.stderr or result.returncode == 0:
                        log.append(f"  {d['busid']}: attached")
                    else:
                        log.append(f"  {d['busid']}: failed")
            
            return True, "Setup complete", log
        except Exception as e:
            log.append(f"Error: {e}")
            return False, str(e), log
    
    def attach_all(self):
        log = []
        config = self.read_config()
        if not config.get('vm_host'):
            return False, "Not configured", log
        
        for d in self.get_devices():
            if not d['skipped'] and not d['attached']:
                result = subprocess.run(['/usr/local/bin/notify-vm.sh', 'boot', d['busid']],
                    capture_output=True, text=True, timeout=60)
                log.append(f"{d['busid']}: {'attached' if result.returncode == 0 else 'failed'}")
        
        if not log:
            log.append("All devices already attached")
        return True, "Done", log
    
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/': self.send_html(HTML_TEMPLATE)
        elif path == '/api/status': self.send_json(self.read_config())
        elif path == '/api/devices': self.send_json({'devices': self.get_devices()})
        elif path == '/api/scan': self.send_json({'vms': self.scan_vms()})
        else: self.send_json({'error': 'Not found'}, 404)
    
    def do_POST(self):
        path = urlparse(self.path).path
        content_len = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(content_len)) if content_len else {}
        
        if path == '/api/test':
            ok, msg = self.test_connection(body.get('host', ''), body.get('user', 'dev'))
            self.send_json({'success': ok, 'message': f"{'OK' if ok else 'Failed'}: {msg}"})
        elif path == '/api/setup':
            ok, msg, log = self.setup_pairing(body.get('host', ''), body.get('user', 'dev'))
            self.send_json({'success': ok, 'error': msg if not ok else '', 'log': log})
        elif path == '/api/attach-all':
            ok, msg, log = self.attach_all()
            self.send_json({'success': ok, 'log': log})
        elif path == '/api/disconnect':
            if os.path.exists(CONFIG_FILE): os.remove(CONFIG_FILE)
            self.send_json({'success': True, 'message': 'Disconnected'})
        else: self.send_json({'error': 'Not found'}, 404)

if __name__ == '__main__':
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(('', PORT), Handler) as httpd:
        print(f"Portal running on http://0.0.0.0:{PORT}")
        httpd.serve_forever()
