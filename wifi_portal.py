#!/usr/bin/env python3
"""
WiFi Captive Portal — scan networks and connect via browser.
Runs on port 80 while AP is active.
Result written to /tmp/wifi_portal_result.json
"""

import json
import subprocess
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse, unquote
from pathlib import Path

RESULT_FILE  = Path("/tmp/wifi_portal_result.json")
STATUS_FILE  = Path("/tmp/wifi_portal_status.json")
PORT         = 80

_server      = None
_status      = "waiting"   # waiting | connecting | success | failed
_status_lock = threading.Lock()


def set_status(s, msg=""):
    with _status_lock:
        STATUS_FILE.write_text(json.dumps({"status": s, "msg": msg}))


def scan_networks():
    try:
        subprocess.run(["nmcli", "dev", "wifi", "rescan"],
                       capture_output=True, timeout=10)
        r = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"],
            capture_output=True, text=True)
        seen, nets = set(), []
        for line in r.stdout.splitlines():
            parts = line.split(":")
            ssid = parts[0].strip()
            if ssid and ssid not in seen:
                seen.add(ssid)
                signal   = parts[1].strip() if len(parts) > 1 else "0"
                security = parts[2].strip() if len(parts) > 2 else ""
                nets.append({"ssid": ssid,
                             "signal": int(signal) if signal.isdigit() else 0,
                             "secure": bool(security and security != "--")})
        nets.sort(key=lambda n: -n["signal"])
        return nets[:15]
    except Exception:
        return []


def connect(ssid, password):
    set_status("connecting", ssid)
    try:
        subprocess.run(["nmcli", "con", "delete", ssid], capture_output=True)
    except Exception:
        pass
    try:
        if password:
            r = subprocess.run(
                ["nmcli", "dev", "wifi", "connect", ssid, "password", password],
                capture_output=True, text=True, timeout=30)
        else:
            r = subprocess.run(
                ["nmcli", "dev", "wifi", "connect", ssid],
                capture_output=True, text=True, timeout=30)
        ok = r.returncode == 0
        set_status("success" if ok else "failed", ssid)
        RESULT_FILE.write_text(json.dumps({
            "ok": ok, "ssid": ssid,
            "msg": r.stdout.strip() or r.stderr.strip()
        }))
        return ok
    except Exception as e:
        set_status("failed", str(e))
        RESULT_FILE.write_text(json.dumps({"ok": False, "ssid": ssid, "msg": str(e)}))
        return False


HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0d0d0d">
<title>WiFi Setup — OrangePi SDR</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:#0d0d0d;color:#ddd;padding:16px;max-width:480px;margin:0 auto}
h1{color:#0af;font-size:1.3em;margin-bottom:4px}
p.sub{color:#666;font-size:.85em;margin-bottom:20px}

.net-list{list-style:none;margin-bottom:20px}
.net-item{background:#151515;border:1px solid #222;border-radius:8px;
  padding:12px 14px;margin-bottom:8px;cursor:pointer;
  display:flex;align-items:center;gap:12px;transition:border-color .15s}
.net-item:hover,.net-item.selected{border-color:#0af;background:#0a1a2a}
.net-name{font-weight:600;font-size:1em;flex:1}
.net-signal{font-size:.75em;color:#888}
.lock{font-size:.85em;color:#fa0}
.signal-bar{display:flex;gap:2px;align-items:flex-end;height:14px}
.signal-bar span{display:block;background:#333;border-radius:1px;width:4px}
.signal-bar span.on{background:#0af}

.pwd-box{background:#111;border:1px solid #333;border-radius:8px;padding:14px;
  margin-bottom:16px;display:none}
.pwd-box label{display:block;font-size:.8em;color:#888;margin-bottom:6px}
.pwd-box input{width:100%;background:#0d0d0d;border:1px solid #444;color:#eee;
  padding:10px 12px;border-radius:6px;font-size:1em;outline:none}
.pwd-box input:focus{border-color:#0af}
.show-pwd{font-size:.75em;color:#0af;cursor:pointer;margin-top:6px;display:inline-block}

.btn{display:block;width:100%;padding:12px;border-radius:8px;font-size:1em;
  font-weight:600;cursor:pointer;border:none;margin-bottom:10px;transition:.15s}
.btn-connect{background:#0af;color:#000}
.btn-connect:hover{background:#08d}
.btn-connect:disabled{background:#1a3a4a;color:#456;cursor:not-allowed}
.btn-scan{background:#1a1a1a;border:1px solid #333;color:#888;font-size:.85em}
.btn-scan:hover{border-color:#0af;color:#0af}

.status{border-radius:8px;padding:12px 14px;margin-bottom:16px;font-size:.9em;display:none}
.status.connecting{background:#1a2a1a;border:1px solid #0a4;color:#0f0}
.status.success{background:#0a2a1a;border:1px solid #0f0;color:#0f0}
.status.failed{background:#2a0a0a;border:1px solid #f44;color:#f66}

.spinner{display:inline-block;width:14px;height:14px;border:2px solid #0f0;
  border-top-color:transparent;border-radius:50%;animation:spin .7s linear infinite;
  vertical-align:middle;margin-right:6px}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<h1>WiFi Setup</h1>
<p class="sub">OrangePi SDR — select a network to connect</p>

<div class="status" id="status-box"></div>

<ul class="net-list" id="net-list">
  <li style="color:#555;padding:12px">Scanning…</li>
</ul>

<div class="pwd-box" id="pwd-box">
  <label id="pwd-label">Password for <b id="pwd-ssid"></b></label>
  <input type="password" id="pwd-input" placeholder="Enter password…" autocomplete="off">
  <span class="show-pwd" onclick="togglePwd()">Show password</span>
</div>

<button class="btn btn-connect" id="btn-connect" disabled onclick="doConnect()">Connect</button>
<button class="btn btn-scan" onclick="loadNetworks()">↺ Scan again</button>

<script>
let selected = null;

function signalBars(sig) {
  const lvl = sig >= 80 ? 4 : sig >= 60 ? 3 : sig >= 40 ? 2 : sig >= 20 ? 1 : 0;
  let h = [4,8,12,16];
  return '<div class="signal-bar">' +
    h.map((hh,i) =>
      `<span style="height:${hh}px" class="${i < lvl ? 'on' : ''}"></span>`
    ).join('') + '</div>';
}

async function loadNetworks() {
  document.getElementById('net-list').innerHTML =
    '<li style="color:#555;padding:12px">Scanning…</li>';
  const r    = await fetch('/api/scan');
  const nets = await r.json();
  const ul   = document.getElementById('net-list');
  if (!nets.length) {
    ul.innerHTML = '<li style="color:#666;padding:12px">No networks found</li>';
    return;
  }
  ul.innerHTML = nets.map(n => `
    <li class="net-item" onclick="selectNet('${esc(n.ssid)}',${n.secure})">
      ${signalBars(n.signal)}
      <span class="net-name">${esc(n.ssid)}</span>
      ${n.secure ? '<span class="lock">🔒</span>' : ''}
      <span class="net-signal">${n.signal}%</span>
    </li>`).join('');
}

function esc(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
          .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function selectNet(ssid, secure) {
  selected = ssid;
  document.querySelectorAll('.net-item').forEach(el => el.classList.remove('selected'));
  event.currentTarget.classList.add('selected');
  document.getElementById('pwd-ssid').textContent = ssid;
  document.getElementById('pwd-box').style.display = secure ? 'block' : 'none';
  document.getElementById('pwd-input').value = '';
  document.getElementById('btn-connect').disabled = false;
}

function togglePwd() {
  const inp = document.getElementById('pwd-input');
  inp.type = inp.type === 'password' ? 'text' : 'password';
}

async function doConnect() {
  if (!selected) return;
  const pwd = document.getElementById('pwd-input').value;
  const box = document.getElementById('status-box');
  box.className = 'status connecting';
  box.innerHTML = '<span class="spinner"></span> Connecting to <b>' + esc(selected) + '</b>…';
  box.style.display = 'block';
  document.getElementById('btn-connect').disabled = true;

  const r    = await fetch('/api/connect', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ssid: selected, password: pwd})
  });
  const data = await r.json();

  if (data.ok) {
    box.className = 'status success';
    box.innerHTML = '✔ Connected to <b>' + esc(selected) + '</b><br>' +
      '<small style="color:#888">The hotspot will close. Reconnect to your WiFi.</small>';
  } else {
    box.className = 'status failed';
    box.innerHTML = '✗ Failed to connect to <b>' + esc(selected) + '</b><br>' +
      '<small>' + esc(data.msg || '') + '</small>';
    document.getElementById('btn-connect').disabled = false;
  }
}

loadNetworks();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/index.html") or p == "/generate_204" or p == "/hotspot-detect.html":
            # Also handle captive portal detection redirects
            self._html(HTML.encode())
        elif p == "/api/scan":
            self._json(scan_networks())
        elif p == "/api/status":
            try:
                self._json(json.loads(STATUS_FILE.read_text()))
            except Exception:
                self._json({"status": "waiting"})
        else:
            # Redirect everything to portal (captive portal behaviour)
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()

    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/api/connect":
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length)
            data   = json.loads(body)
            ssid   = data.get("ssid", "").strip()
            pwd    = data.get("password", "").strip()
            if not ssid:
                self._json({"ok": False, "msg": "No SSID"})
                return
            # Connect in background thread so HTTP response returns immediately
            def _do():
                connect(ssid, pwd)
            threading.Thread(target=_do, daemon=True).start()
            # Poll for result (max 30s)
            for _ in range(60):
                time.sleep(0.5)
                try:
                    result = json.loads(RESULT_FILE.read_text())
                    if result.get("ssid") == ssid:
                        self._json(result)
                        if result.get("ok"):
                            # Signal button_rtl.py to stop AP
                            Path("/tmp/wifi_portal_done").write_text("ok")
                        return
                except Exception:
                    pass
            self._json({"ok": False, "msg": "Timeout"})
        else:
            self.send_error(404)

    def _html(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(data))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def run(stop_event):
    global _server
    set_status("waiting")
    RESULT_FILE.unlink(missing_ok=True)
    Path("/tmp/wifi_portal_done").unlink(missing_ok=True)
    _server = HTTPServer(("0.0.0.0", PORT), Handler)
    _server.timeout = 1
    while not stop_event.is_set():
        _server.handle_request()
    _server.server_close()


if __name__ == "__main__":
    import signal as _signal
    stop = threading.Event()
    _signal.signal(_signal.SIGTERM, lambda s, f: stop.set())
    _signal.signal(_signal.SIGINT,  lambda s, f: stop.set())
    run(stop)
