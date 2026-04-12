#!/usr/bin/env python3
"""
SDR Recorder Web UI — configure frequency, squelch, browse and play recordings.
Serves on port 8082 — browse to http://DEVICE_IP:8082
"""

import json
import os
import subprocess
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
from datetime import datetime

CFG_FILE    = Path("/etc/sdr_recorder.cfg")
REC_DIR     = Path("/var/lib/sdr_recorder")
STATUS_FILE = Path("/tmp/sdr_recorder_status.json")
PORT        = 8082

DEFAULT_CFG = {
    "frequency":      "145.775M",
    "squelch":        30,
    "mode":           "fm",
    "gain":           40,
    "max_recordings": 50,
    "min_duration":   1.0,
    "silence_dur":    1.5,
    "vox_threshold":  500,
}

MODES = ["fm", "am", "wbfm", "lsb", "usb"]


def load_cfg():
    try:
        cfg = dict(DEFAULT_CFG)
        cfg.update(json.loads(CFG_FILE.read_text()))
        return cfg
    except Exception:
        return dict(DEFAULT_CFG)


def save_cfg(cfg):
    CFG_FILE.write_text(json.dumps(cfg, indent=2))


def load_status():
    try:
        d = json.loads(STATUS_FILE.read_text())
        # Inject live RMS from shared file if available
        try:
            rms = int(Path("/tmp/sdr_recorder_rms").read_text().strip())
            d["rms"] = rms
        except Exception:
            d["rms"] = 0
        return d
    except Exception:
        return {"state": "idle", "frequency": "—", "squelch": "—", "rms": 0}


def reload_recorder():
    subprocess.call(["sudo", "systemctl", "restart", "sdr_recorder"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def stop_recorder():
    subprocess.call(["sudo", "systemctl", "stop", "sdr_recorder"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def start_recorder():
    subprocess.call(["sudo", "systemctl", "start", "sdr_recorder"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def get_duration(path):
    try:
        r = subprocess.run(["soxi", "-D", str(path)],
                           capture_output=True, text=True)
        secs = float(r.stdout.strip())
        m, s = divmod(int(secs), 60)
        return f"{m}:{s:02d}"
    except Exception:
        return "—"


def get_recordings():
    """Return list of recordings, newest first."""
    files = sorted(REC_DIR.glob("*.wav"),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    result = []
    for f in files:
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        result.append({
            "name":     f.name,
            "size_kb":  round(f.stat().st_size / 1024),
            "date":     mtime.strftime("%d/%m/%Y"),
            "time":     mtime.strftime("%H:%M:%S"),
            "duration": get_duration(f),
        })
    return result


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SDR Recorder — OrangePi SDR</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:monospace;background:#0d0d0d;color:#ddd;padding:12px 16px}
h1{color:#0af;font-size:1.2em;margin-bottom:12px}
h2{color:#888;font-size:.9em;margin:16px 0 8px;text-transform:uppercase;letter-spacing:.08em}

/* Status bar */
.status-bar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;
  background:#111;border:1px solid #222;border-radius:6px;padding:10px 14px;margin-bottom:14px}
.dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.dot.idle{background:#444}
.dot.listening{background:#fa0;animation:pulse 1.2s infinite}
.dot.recording{background:#f00;animation:pulse .6s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.status-label{font-size:.9em}
.status-label b{color:#0af}
.status-label.recording b{color:#f44}

/* Config form */
.cfg-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;margin-bottom:10px}
.field label{display:block;font-size:.75em;color:#888;margin-bottom:3px}
.field input,.field select{
  width:100%;background:#151515;border:1px solid #333;color:#eee;
  padding:6px 8px;border-radius:4px;font-family:monospace;font-size:.9em}
.field input:focus,.field select:focus{outline:none;border-color:#0af}
.btn{padding:7px 18px;border-radius:4px;font-family:monospace;font-size:.9em;
     cursor:pointer;border:1px solid}
.btn-primary{background:#0a2040;border-color:#0af;color:#0af}
.btn-primary:hover{background:#0af;color:#000}
.btn-danger{background:#200;border-color:#f44;color:#f44}
.btn-danger:hover{background:#f44;color:#000}
.btn-sm{padding:3px 10px;font-size:.78em}
.btn-dl{background:#1a2a1a;border-color:#0a4;color:#0d0}
.btn-dl:hover{background:#0a4;color:#000}

/* Recordings table */
.rec-table{width:100%;border-collapse:collapse;font-size:.82em;margin-top:4px}
.rec-table th{background:#111;color:#666;text-align:left;padding:5px 8px;
  border-bottom:1px solid #222;white-space:nowrap}
.rec-table td{padding:5px 8px;border-bottom:1px solid #181818;vertical-align:middle}
.rec-table tr:hover td{background:#141414}
.rec-name{color:#aaa;font-size:.8em}
.rec-time{color:#0af}
.rec-dur{color:#fa0}
.rec-size{color:#555}
.actions{display:flex;gap:6px;align-items:center}

/* Audio player */
audio{height:28px;width:100%;max-width:320px;filter:invert(1) hue-rotate(180deg)}

.empty{color:#444;padding:20px 0;font-style:italic}
.saved-msg{color:#0f0;font-size:.85em;margin-top:6px;display:none}
</style>
</head>
<body>
<h1>SDR Scanner Recorder</h1>

<!-- Status -->
<div class="status-bar" id="status-bar">
  <div class="dot idle" id="dot"></div>
  <div class="status-label" id="status-lbl">Loading…</div>
</div>

<!-- Config -->
<h2>Configuration</h2>
<form id="cfg-form" onsubmit="saveCfg(event)">
<div class="cfg-grid">
  <div class="field">
    <label>Frequency</label>
    <input id="cfg-freq" name="frequency" placeholder="145.775M" required>
  </div>
  <div class="field">
    <label>Mode</label>
    <select id="cfg-mode" name="mode">
      <option value="fm">FM (narrow)</option>
      <option value="wbfm">FM (wide broadcast)</option>
      <option value="am">AM</option>
      <option value="usb">USB</option>
      <option value="lsb">LSB</option>
    </select>
  </div>
  <div class="field">
    <label>Squelch (0=off, higher=stricter)</label>
    <input id="cfg-sq" name="squelch" type="number" min="0" max="100">
  </div>
  <div class="field">
    <label>Gain (dB, 0=auto)</label>
    <input id="cfg-gain" name="gain" type="number" min="0" max="50">
  </div>
  <div class="field">
    <label>Max recordings to keep</label>
    <input id="cfg-max" name="max_recordings" type="number" min="1" max="500">
  </div>
  <div class="field">
    <label>Min clip duration (sec)</label>
    <input id="cfg-mindur" name="min_duration" type="number" min="0.5" max="30" step="0.5">
  </div>
  <div class="field">
    <label>Silence cutoff (sec)</label>
    <input id="cfg-sildur" name="silence_dur" type="number" min="0.3" max="10" step="0.1">
  </div>
  <div class="field">
    <label>VOX threshold (RMS 0-32768) &nbsp;
      <span style="color:#888;font-size:.85em">current noise: </span>
      <span id="rms-val" style="color:#fa0;font-weight:bold">—</span>
    </label>
    <input id="cfg-vox" name="vox_threshold" type="number" min="50" max="32768">
    <div style="margin-top:6px;height:8px;background:#1a1a1a;border-radius:4px;overflow:hidden">
      <div id="rms-bar" style="height:100%;background:#fa0;width:0%;transition:width .2s;border-radius:4px"></div>
    </div>
    <div style="font-size:.72em;color:#555;margin-top:3px">Set threshold above noise floor to avoid false recordings</div>
  </div>
</div>
<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
  <button type="submit" class="btn btn-primary">Save &amp; Apply</button>
  <button type="button" class="btn btn-danger" onclick="ctrlRecorder('stop')">⬛ Stop</button>
  <button type="button" class="btn btn-primary" style="border-color:#0f0;color:#0f0" onclick="ctrlRecorder('start')">▶ Start</button>
  <span class="saved-msg" id="saved-msg">✔ Saved — recorder restarting…</span>
</div>
</form>

<!-- Recordings -->
<h2>Recordings (<span id="rec-count">0</span>)</h2>
<table class="rec-table">
<thead>
<tr><th>Date</th><th>Time (IL)</th><th>Duration</th><th>Size</th><th>Play / Download</th><th></th></tr>
</thead>
<tbody id="rec-tbody">
<tr><td colspan="6" class="empty">No recordings yet</td></tr>
</tbody>
</table>

<script>
let cfg = {};

async function loadCfg() {
  const r = await fetch('/api/config');
  cfg = await r.json();
  document.getElementById('cfg-freq').value   = cfg.frequency       || '';
  document.getElementById('cfg-mode').value   = cfg.mode            || 'fm';
  document.getElementById('cfg-sq').value     = cfg.squelch         ?? 30;
  document.getElementById('cfg-gain').value   = cfg.gain            ?? 40;
  document.getElementById('cfg-max').value    = cfg.max_recordings  ?? 50;
  document.getElementById('cfg-mindur').value = cfg.min_duration    ?? 1.0;
  document.getElementById('cfg-sildur').value = cfg.silence_dur     ?? 1.5;
  document.getElementById('cfg-vox').value    = cfg.vox_threshold   ?? 500;
}

async function saveCfg(e) {
  e.preventDefault();
  const data = {
    frequency:      document.getElementById('cfg-freq').value.trim(),
    mode:           document.getElementById('cfg-mode').value,
    squelch:        parseFloat(document.getElementById('cfg-sq').value),
    gain:           parseFloat(document.getElementById('cfg-gain').value),
    max_recordings: parseInt(document.getElementById('cfg-max').value),
    min_duration:   parseFloat(document.getElementById('cfg-mindur').value),
    silence_dur:    parseFloat(document.getElementById('cfg-sildur').value),
    vox_threshold:  parseInt(document.getElementById('cfg-vox').value),
  };
  await fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  const msg = document.getElementById('saved-msg');
  msg.style.display = 'inline';
  setTimeout(() => msg.style.display = 'none', 4000);
  loadRecordings();
}

async function loadStatus() {
  try {
    const r  = await fetch('/api/status');
    const st = await r.json();
    const dot = document.getElementById('dot');
    const lbl = document.getElementById('status-lbl');
    dot.className = 'dot ' + st.state;
    const stateText = {idle:'Idle', listening:'Listening…', recording:'● REC'};
    lbl.innerHTML =
      `<b>${stateText[st.state] || st.state}</b> &nbsp;·&nbsp; ` +
      `Freq: <b>${st.frequency}</b> &nbsp;·&nbsp; ` +
      `Squelch: <b>${st.squelch}</b> &nbsp;·&nbsp; ` +
      `Mode: <b>${st.mode||'—'}</b>`;
    lbl.className = 'status-label ' + st.state;
    // RMS meter
    const rms = st.rms || 0;
    const rmsEl = document.getElementById('rms-val');
    const rmsBar = document.getElementById('rms-bar');
    if (rmsEl) {
      rmsEl.textContent = rms;
      rmsEl.style.color = rms > parseInt(document.getElementById('cfg-vox').value||500) ? '#f44' : '#fa0';
    }
    if (rmsBar) {
      const pct = Math.min(rms / 32768 * 100, 100);
      rmsBar.style.width = pct + '%';
      rmsBar.style.background = rms > parseInt(document.getElementById('cfg-vox').value||500) ? '#f44' : '#fa0';
    }
  } catch(e) {}
  setTimeout(loadStatus, 1000);
}

async function loadRecordings() {
  const r    = await fetch('/api/recordings');
  const recs = await r.json();
  const tbody = document.getElementById('rec-tbody');
  document.getElementById('rec-count').textContent = recs.length;
  if (!recs.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">No recordings yet</td></tr>';
    return;
  }
  tbody.innerHTML = recs.map(rec => `
    <tr>
      <td>${rec.date}</td>
      <td class="rec-time">${rec.time}</td>
      <td class="rec-dur">${rec.duration}</td>
      <td class="rec-size">${rec.size_kb} KB</td>
      <td>
        <div class="actions">
          <audio src="/recordings/${encodeURIComponent(rec.name)}" controls preload="none"></audio>
          <a href="/download/${encodeURIComponent(rec.name)}" download="${rec.name}">
            <button class="btn btn-sm btn-dl">⬇ Download</button>
          </a>
        </div>
      </td>
      <td>
        <button class="btn btn-sm btn-danger" onclick="deleteRec('${rec.name}')">✕</button>
      </td>
    </tr>`).join('');
}

async function deleteRec(name) {
  if (!confirm('Delete ' + name + '?')) return;
  await fetch('/api/delete?name=' + encodeURIComponent(name), {method:'POST'});
  loadRecordings();
}

async function ctrlRecorder(action) {
  await fetch('/api/control', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action})});
  setTimeout(loadStatus, 1000);
}

loadCfg();
loadStatus();
loadRecordings();
setInterval(loadRecordings, 10000);
</script>
</body>
</html>
"""


CHUNK = 65536  # 64 KB read chunks for streaming


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_HEAD(self):
        """Support HEAD requests (needed by browsers before audio playback)."""
        p = urlparse(self.path)
        if p.path.startswith("/recordings/"):
            filename = Path(unquote(p.path[12:])).name
            f = REC_DIR / filename
            if f.exists() and f.suffix == ".wav":
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", f.stat().st_size)
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
            else:
                self.send_error(404)
        else:
            self.send_error(404)

    def do_GET(self):
        p = urlparse(self.path)
        path = p.path

        if path in ("/", "/index.html"):
            self.send_html(HTML.encode())
        elif path == "/api/config":
            self.send_json(load_cfg())
        elif path == "/api/status":
            self.send_json(load_status())
        elif path == "/api/recordings":
            self.send_json(get_recordings())
        elif path.startswith("/recordings/"):
            self.serve_audio(unquote(path[12:]), download=False)
        elif path.startswith("/download/"):
            self.serve_audio(unquote(path[10:]), download=True)
        else:
            self.send_error(404)

    def do_POST(self):
        p = urlparse(self.path)
        path = p.path

        if path == "/api/config":
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length)
            try:
                new_cfg = dict(DEFAULT_CFG)
                new_cfg.update(json.loads(body))
                save_cfg(new_cfg)
                reload_recorder()
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
        elif path == "/api/control":
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length)
            action = json.loads(body).get("action", "")
            if action == "stop":
                stop_recorder()
            elif action == "start":
                start_recorder()
            self.send_json({"ok": True})
        elif path == "/api/delete":
            qs   = parse_qs(p.query)
            name = qs.get("name", [""])[0]
            name = Path(name).name  # sanitize
            f    = REC_DIR / name
            if f.exists() and f.suffix == ".wav":
                f.unlink()
                self.send_json({"ok": True})
            else:
                self.send_json({"ok": False})
        else:
            self.send_error(404)

    def send_html(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(data))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def serve_audio(self, filename, download=False):
        """Serve WAV with HTTP Range support for in-browser playback."""
        filename = Path(filename).name  # sanitize path traversal
        f = REC_DIR / filename
        if not f.exists() or f.suffix != ".wav":
            self.send_error(404)
            return

        file_size = f.stat().st_size
        range_header = self.headers.get("Range")

        # Parse Range header
        start, end = 0, file_size - 1
        if range_header:
            try:
                rng = range_header.strip().replace("bytes=", "")
                s, e = rng.split("-")
                start = int(s) if s else 0
                end   = int(e) if e else file_size - 1
            except Exception:
                pass

        end   = min(end, file_size - 1)
        length = end - start + 1

        status = 206 if range_header else 200
        self.send_response(status)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", length)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        if download:
            self.send_header("Content-Disposition",
                             f'attachment; filename="{filename}"')
        else:
            self.send_header("Content-Disposition",
                             f'inline; filename="{filename}"')
        self.end_headers()

        try:
            with open(f, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == "__main__":
    REC_DIR.mkdir(parents=True, exist_ok=True)
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"SDR Recorder web UI on port {PORT}")
    server.serve_forever()
