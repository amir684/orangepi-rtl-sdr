#!/usr/bin/env python3
"""
SDR Multi-Tool Config Portal
Unified web interface to configure all SDR services.
Port 80, accessible at 192.168.100.1 when hotspot is active.
Connect to OrangePi-SDR AP → open browser → 192.168.100.1
"""

import json
import re
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

PORT = 80

# ── Config file paths ──────────────────────────────────────────────────────────
AUTORX_CFG   = Path("/home/orangepi/radiosonde_auto_rx/auto_rx/station.cfg")
NOAA_CFG     = Path("/etc/noaa_apt.cfg")
REC_CFG      = Path("/etc/sdr_recorder.cfg")
READSB_CFG   = Path("/etc/default/readsb")
ACARSDEC_SVC = Path("/etc/systemd/system/acarsdec.service")

# ── Services list ──────────────────────────────────────────────────────────────
SERVICES = [
    ("auto-rx",          "AutoRX (Radiosonde)"),
    ("readsb",           "readsb (ADSB decoder)"),
    ("tar1090",          "tar1090 (ADSB map)"),
    ("noaa_capture",     "NOAA Capture"),
    ("noaa_web",         "NOAA Web UI"),
    ("acarsdec",         "ACARS Decoder"),
    ("acars_web",        "ACARS Web UI"),
    ("sdr_recorder",     "SDR Recorder"),
    ("sdr_recorder_web", "Recorder Web UI"),
    ("rtl_433",          "RTL-433"),
    ("ais_catcher",      "AIS Catcher"),
    ("multimon_ng",      "Pager Decoder"),
    ("lighttpd",         "Lighttpd"),
    ("button_rtl",       "Button Service"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Config readers / writers
# ─────────────────────────────────────────────────────────────────────────────

def read_autorx():
    data = {
        "station_callsign":        "STATION",
        "station_lat":             "0.0",
        "station_lon":             "0.0",
        "station_alt":             "0",
        "gain":                    "40.0",
        "ppm":                     "0",
        "sondehub_enabled":        "True",
        "aprs_enabled":            "False",
        "upload_listener_position":"True",
        "station_beacon_enabled":  "False",
        "gpsd_enabled":            "False",
    }
    try:
        for line in AUTORX_CFG.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, val = line.partition('=')
            key = key.strip()
            if key not in data:
                continue
            val = val.strip().split('#')[0].strip()
            if len(val) >= 2 and val[0] in ('"', "'") and val[-1] == val[0]:
                val = val[1:-1]
            data[key] = val
    except Exception:
        pass
    return data


def write_autorx(raw):
    errors = []
    for key, value in raw.items():
        try:
            text = AUTORX_CFG.read_text()
            if value in ("True", "False"):
                fmt = value
            else:
                try:
                    float(value)
                    fmt = value
                except ValueError:
                    fmt = f'"{value}"'
            new_text = re.sub(
                rf'(?m)^{re.escape(key)}\s*=.*',
                f'{key} = {fmt}',
                text
            )
            AUTORX_CFG.write_text(new_text)
        except Exception as e:
            errors.append(f"{key}: {e}")
    return not errors, errors


def read_noaa():
    try:
        return json.loads(NOAA_CFG.read_text())
    except Exception:
        return {"auto_capture": False, "min_elev": 15}


def write_noaa(raw):
    try:
        cfg = read_noaa()
        if "auto_capture" in raw:
            cfg["auto_capture"] = raw["auto_capture"] in (True, "True", "true", "1", 1)
        if "min_elev" in raw:
            cfg["min_elev"] = int(raw["min_elev"])
        NOAA_CFG.write_text(json.dumps(cfg, indent=2))
        return True, []
    except Exception as e:
        return False, [str(e)]


_REC_DEFAULTS = {
    "frequency":    "145.775M",
    "squelch":      30,
    "mode":         "fm",
    "sample_rate":  24000,
    "gain":         40,
    "max_recordings": 50,
    "min_duration": 1.0,
    "silence_dur":  1.5,
    "vox_threshold":500,
}


def read_recorder():
    try:
        cfg = dict(_REC_DEFAULTS)
        cfg.update(json.loads(REC_CFG.read_text()))
        return cfg
    except Exception:
        return dict(_REC_DEFAULTS)


def write_recorder(raw):
    try:
        cfg = read_recorder()
        for k, v in raw.items():
            if k not in cfg:
                continue
            ref = _REC_DEFAULTS.get(k, "")
            if isinstance(ref, int):
                cfg[k] = int(v)
            elif isinstance(ref, float):
                cfg[k] = float(v)
            else:
                cfg[k] = v
        REC_CFG.write_text(json.dumps(cfg, indent=2))
        return True, []
    except Exception as e:
        return False, [str(e)]


def read_readsb():
    data = {"gain": "auto", "ppm": "0"}
    try:
        text = READSB_CFG.read_text()
        m = re.search(r'RECEIVER_OPTIONS="([^"]*)"', text)
        if m:
            opts = m.group(1)
            gm = re.search(r'--gain\s+(\S+)', opts)
            pm = re.search(r'--ppm\s+(\S+)', opts)
            if gm: data["gain"] = gm.group(1)
            if pm: data["ppm"]  = pm.group(1)
    except Exception:
        pass
    return data


def write_readsb(raw):
    try:
        text  = READSB_CFG.read_text()
        gain  = str(raw.get("gain", "auto"))
        ppm   = str(raw.get("ppm",  "0"))
        if re.search(r'--gain\s+\S+', text):
            text = re.sub(r'(--gain\s+)\S+', rf'\g<1>{gain}', text)
        else:
            text = re.sub(r'(RECEIVER_OPTIONS="[^"]*)"', rf'\1 --gain {gain}"', text)
        if re.search(r'--ppm\s+\S+', text):
            text = re.sub(r'(--ppm\s+)\S+', rf'\g<1>{ppm}', text)
        else:
            text = re.sub(r'(RECEIVER_OPTIONS="[^"]*)"', rf'\1 --ppm {ppm}"', text)
        READSB_CFG.write_text(text)
        return True, []
    except Exception as e:
        return False, [str(e)]


def read_acars():
    data = {"gain": "40", "frequencies": "129.125 130.025 130.425 130.450"}
    try:
        text  = ACARSDEC_SVC.read_text()
        lines = text.splitlines()
        exec_lines, in_exec = [], False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("ExecStart="):
                in_exec = True
            if in_exec:
                exec_lines.append(stripped.rstrip("\\").strip())
                if not stripped.endswith("\\"):
                    break
        exec_str = " ".join(exec_lines)
        gm = re.search(r'\s-g\s+(\S+)', exec_str)
        if gm: data["gain"] = gm.group(1)
        rm = re.search(r'-r\s+\d+\s+([\d.\s]+)', exec_str)
        if rm:
            freqs = rm.group(1).strip().split()
            data["frequencies"] = " ".join(freqs)
    except Exception:
        pass
    return data


def write_acars(raw):
    try:
        gain  = str(raw.get("gain", "40"))
        freqs = " ".join(str(raw.get("frequencies", "129.125 130.025")).split())
        new_exec = (
            f"ExecStart=/usr/local/bin/acarsdec \\\n"
            f"    -g {gain} \\\n"
            f"    -e \\\n"
            f"    -A \\\n"
            f"    -o 4 \\\n"
            f"    -l /var/log/acarsdec/messages.json \\\n"
            f"    -r 0 \\\n"
            f"    {freqs}"
        )
        text = ACARSDEC_SVC.read_text()
        # Replace ExecStart block (may span multiple lines ending with \)
        text = re.sub(
            r'ExecStart=(?:[^\n]*\\\n)*[^\n]*',
            new_exec,
            text
        )
        ACARSDEC_SVC.write_text(text)
        subprocess.run(["systemctl", "daemon-reload"],
                       capture_output=True, timeout=10)
        return True, []
    except Exception as e:
        return False, [str(e)]


def service_status(name):
    try:
        r = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True, text=True, timeout=3)
        return r.stdout.strip()
    except Exception:
        return "unknown"


def service_control(name, action):
    try:
        r = subprocess.run(
            ["systemctl", action, name],
            capture_output=True, text=True, timeout=15)
        return r.returncode == 0, r.stderr.strip()
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────────────────────
# HTML
# ─────────────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0d0d0d">
<title>SDR Config Portal</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:#0d0d0d;color:#ddd;min-height:100vh}
header{padding:16px 16px 0;max-width:520px;margin:0 auto}
h1{color:#0af;font-size:1.3em}
p.sub{color:#555;font-size:.8em;margin-top:2px}

/* ── Tab bar ──────────────────────────────────────────── */
.tab-bar{display:flex;gap:4px;padding:12px 16px 0;max-width:520px;margin:0 auto;
         overflow-x:auto;-webkit-overflow-scrolling:touch;
         scrollbar-width:none}
.tab-bar::-webkit-scrollbar{display:none}
.tab{flex-shrink:0;background:#111;border:1px solid #1e1e1e;color:#666;
     padding:7px 14px;border-radius:20px;font-size:.82em;cursor:pointer;
     white-space:nowrap;transition:.15s}
.tab.active{background:#0a1a2a;border-color:#0af;color:#0af}
.tab:hover:not(.active){border-color:#333;color:#aaa}

/* ── Panels ───────────────────────────────────────────── */
.panel{display:none;padding:16px;max-width:520px;margin:0 auto;
       padding-bottom:40px}
.panel.active{display:block}

/* ── Card ─────────────────────────────────────────────── */
.card{background:#111;border:1px solid #1e1e1e;border-radius:12px;
      padding:16px;margin-bottom:12px}
.card-title{font-size:.75em;font-weight:700;color:#0af;letter-spacing:.08em;
            text-transform:uppercase;margin-bottom:14px}

/* ── Fields ───────────────────────────────────────────── */
.field{margin-bottom:14px}
.field:last-child{margin-bottom:0}
.field label{display:block;font-size:.78em;color:#888;margin-bottom:5px}
.field input[type=text],.field input[type=number],.field select,.field textarea{
  width:100%;background:#0d0d0d;border:1px solid #2a2a2a;color:#eee;
  padding:9px 11px;border-radius:7px;font-size:.95em;outline:none;
  transition:border-color .15s}
.field input:focus,.field select:focus,.field textarea:focus{border-color:#0af}
.field textarea{resize:vertical;min-height:70px;font-family:monospace;font-size:.85em}
.field .hint{font-size:.72em;color:#555;margin-top:4px}

.row2{display:grid;grid-template-columns:1fr 1fr;gap:10px}

/* ── Toggle switch ────────────────────────────────────── */
.toggle-row{display:flex;align-items:center;justify-content:space-between;
            padding:10px 0;border-bottom:1px solid #1a1a1a}
.toggle-row:last-child{border-bottom:none}
.toggle-row span{font-size:.9em}
.sw{position:relative;display:inline-block;width:44px;height:24px;flex-shrink:0}
.sw input{opacity:0;width:0;height:0}
.sw .sl{position:absolute;inset:0;background:#222;border-radius:12px;
         cursor:pointer;transition:.2s}
.sw .sl:before{content:"";position:absolute;height:18px;width:18px;
               left:3px;bottom:3px;background:#555;border-radius:50%;transition:.2s}
.sw input:checked+.sl{background:#0a3050}
.sw input:checked+.sl:before{transform:translateX(20px);background:#0af}

/* ── Buttons ──────────────────────────────────────────── */
.btn{display:block;width:100%;padding:12px;border-radius:8px;font-size:.95em;
     font-weight:600;cursor:pointer;border:none;transition:.15s;margin-top:4px}
.btn-save{background:#0af;color:#000}
.btn-save:hover{background:#08d}
.btn-save:disabled{background:#1a3a4a;color:#456;cursor:not-allowed}

/* ── System table ─────────────────────────────────────── */
.svc-table{width:100%;border-collapse:collapse;font-size:.85em}
.svc-table th{text-align:left;color:#555;font-weight:600;
              padding:6px 8px;border-bottom:1px solid #1e1e1e}
.svc-table td{padding:7px 8px;border-bottom:1px solid #111;vertical-align:middle}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;
       font-size:.75em;font-weight:600;letter-spacing:.04em}
.badge.active{background:#0a2a1a;color:#0f0;border:1px solid #0a4}
.badge.inactive{background:#1a1a1a;color:#555;border:1px solid #222}
.badge.failed{background:#2a0a0a;color:#f44;border:1px solid #600}
.badge.unknown{background:#1a1a0a;color:#880;border:1px solid #440}
.svc-btn{background:#1a1a1a;border:1px solid #2a2a2a;color:#888;
         padding:3px 9px;border-radius:5px;cursor:pointer;font-size:.8em;
         transition:.12s;margin-left:2px}
.svc-btn:hover{border-color:#0af;color:#0af}

/* ── Toast ────────────────────────────────────────────── */
#toast{display:none;position:fixed;bottom:24px;left:50%;
       transform:translateX(-50%);
       padding:10px 20px;border-radius:8px;font-size:.9em;
       font-weight:600;z-index:999;white-space:nowrap}
#toast.ok{background:#0a2a1a;border:1px solid #0f0;color:#0f0}
#toast.err{background:#2a0a0a;border:1px solid #f44;color:#f66}

/* ── Loading ──────────────────────────────────────────── */
.loading{color:#555;padding:20px;text-align:center;font-size:.9em}
</style>
</head>
<body>

<header>
  <h1>SDR Config Portal</h1>
  <p class="sub">OrangePi Multi-Tool — configure all services</p>
</header>

<div class="tab-bar">
  <button class="tab active" onclick="showTab('autorx')">AutoRX</button>
  <button class="tab" onclick="showTab('adsb')">ADSB</button>
  <button class="tab" onclick="showTab('noaa')">NOAA</button>
  <button class="tab" onclick="showTab('recorder')">Recorder</button>
  <button class="tab" onclick="showTab('acars')">ACARS</button>
  <button class="tab" onclick="showTab('system')">System</button>
</div>

<!-- ── AutoRX ───────────────────────────────────────────── -->
<div class="panel active" id="tab-autorx">
  <div class="card">
    <div class="card-title">Station Info</div>
    <div class="field">
      <label>Callsign</label>
      <input type="text" id="autorx_station_callsign" data-key="station_callsign"
             placeholder="e.g. VK2XAB">
    </div>
    <div class="row2">
      <div class="field">
        <label>Latitude</label>
        <input type="number" step="0.0001" id="autorx_station_lat" data-key="station_lat">
      </div>
      <div class="field">
        <label>Longitude</label>
        <input type="number" step="0.0001" id="autorx_station_lon" data-key="station_lon">
      </div>
    </div>
    <div class="row2">
      <div class="field">
        <label>Altitude (m)</label>
        <input type="number" id="autorx_station_alt" data-key="station_alt">
      </div>
      <div class="field">
        <label>SDR Gain (dB)</label>
        <input type="number" step="0.1" id="autorx_gain" data-key="gain"
               placeholder="-1 = auto">
        <div class="hint">-1 for auto gain</div>
      </div>
    </div>
    <div class="field">
      <label>PPM Frequency Correction</label>
      <input type="number" id="autorx_ppm" data-key="ppm" placeholder="0">
    </div>
  </div>
  <div class="card">
    <div class="card-title">Upload Settings</div>
    <div class="toggle-row">
      <span>SondeHub Upload</span>
      <label class="sw"><input type="checkbox" id="autorx_sondehub_enabled"
             data-key="sondehub_enabled"><span class="sl"></span></label>
    </div>
    <div class="toggle-row">
      <span>Upload Station Position</span>
      <label class="sw"><input type="checkbox" id="autorx_upload_listener_position"
             data-key="upload_listener_position"><span class="sl"></span></label>
    </div>
    <div class="toggle-row">
      <span>APRS Upload</span>
      <label class="sw"><input type="checkbox" id="autorx_aprs_enabled"
             data-key="aprs_enabled"><span class="sl"></span></label>
    </div>
    <div class="toggle-row">
      <span>APRS Beacon</span>
      <label class="sw"><input type="checkbox" id="autorx_station_beacon_enabled"
             data-key="station_beacon_enabled"><span class="sl"></span></label>
    </div>
    <div class="toggle-row">
      <span>GPSD (GPS daemon)</span>
      <label class="sw"><input type="checkbox" id="autorx_gpsd_enabled"
             data-key="gpsd_enabled"><span class="sl"></span></label>
    </div>
  </div>
  <button class="btn btn-save" id="save-autorx"
          onclick="saveConfig('autorx','auto-rx')">Save &amp; Restart AutoRX</button>
</div>

<!-- ── ADSB ─────────────────────────────────────────────── -->
<div class="panel" id="tab-adsb">
  <div class="card">
    <div class="card-title">SDR Settings</div>
    <div class="row2">
      <div class="field">
        <label>Gain</label>
        <input type="text" id="adsb_gain" data-key="gain" placeholder="auto">
        <div class="hint">"auto" or dB value (e.g. 40)</div>
      </div>
      <div class="field">
        <label>PPM Correction</label>
        <input type="number" id="adsb_ppm" data-key="ppm" placeholder="0">
      </div>
    </div>
    <div class="hint" style="padding-top:4px">
      Changes saved to /etc/default/readsb — readsb + tar1090 will restart.
    </div>
  </div>
  <button class="btn btn-save" id="save-adsb"
          onclick="saveConfig('adsb','readsb')">Save &amp; Restart ADSB</button>
</div>

<!-- ── NOAA ─────────────────────────────────────────────── -->
<div class="panel" id="tab-noaa">
  <div class="card">
    <div class="card-title">NOAA APT Settings</div>
    <div class="toggle-row">
      <span>Auto Capture (schedule-based)</span>
      <label class="sw"><input type="checkbox" id="noaa_auto_capture"
             data-key="auto_capture"><span class="sl"></span></label>
    </div>
    <div class="field" style="margin-top:14px">
      <label>Minimum Elevation (°)</label>
      <input type="number" id="noaa_min_elev" data-key="min_elev"
             min="0" max="90" step="1">
      <div class="hint">Ignore passes below this elevation (15° recommended)</div>
    </div>
  </div>
  <button class="btn btn-save" id="save-noaa"
          onclick="saveConfig('noaa','noaa_capture')">Save &amp; Restart NOAA</button>
</div>

<!-- ── Recorder ──────────────────────────────────────────── -->
<div class="panel" id="tab-recorder">
  <div class="card">
    <div class="card-title">Frequency &amp; Radio</div>
    <div class="row2">
      <div class="field">
        <label>Frequency</label>
        <input type="text" id="rec_frequency" data-key="frequency"
               placeholder="145.775M">
        <div class="hint">Use M for MHz (e.g. 145.500M)</div>
      </div>
      <div class="field">
        <label>Mode</label>
        <select id="rec_mode" data-key="mode">
          <option value="fm">FM (narrow)</option>
          <option value="wbfm">FM (wide)</option>
          <option value="am">AM</option>
          <option value="usb">USB</option>
          <option value="lsb">LSB</option>
        </select>
      </div>
    </div>
    <div class="row2">
      <div class="field">
        <label>SDR Gain (dB)</label>
        <input type="number" id="rec_gain" data-key="gain">
      </div>
      <div class="field">
        <label>Squelch</label>
        <input type="number" id="rec_squelch" data-key="squelch">
      </div>
    </div>
    <div class="field">
      <label>Sample Rate (Hz)</label>
      <input type="number" id="rec_sample_rate" data-key="sample_rate" step="1000">
      <div class="hint">24000 = good quality, 16000 = lighter</div>
    </div>
  </div>
  <div class="card">
    <div class="card-title">VOX Recording</div>
    <div class="field">
      <label>VOX Threshold (RMS 0–32768)</label>
      <input type="number" id="rec_vox_threshold" data-key="vox_threshold"
             min="0" max="32768">
      <div class="hint">Signal must exceed this RMS level to start recording</div>
    </div>
    <div class="row2">
      <div class="field">
        <label>Min Duration (s)</label>
        <input type="number" step="0.1" id="rec_min_duration" data-key="min_duration">
        <div class="hint">Discard shorter clips</div>
      </div>
      <div class="field">
        <label>Silence Duration (s)</label>
        <input type="number" step="0.1" id="rec_silence_dur" data-key="silence_dur">
        <div class="hint">Silence before closing file</div>
      </div>
    </div>
    <div class="field">
      <label>Max Recordings (oldest deleted)</label>
      <input type="number" id="rec_max_recordings" data-key="max_recordings">
    </div>
  </div>
  <button class="btn btn-save" id="save-recorder"
          onclick="saveConfig('recorder','sdr_recorder')">Save &amp; Restart Recorder</button>
</div>

<!-- ── ACARS ─────────────────────────────────────────────── -->
<div class="panel" id="tab-acars">
  <div class="card">
    <div class="card-title">ACARS Decoder Settings</div>
    <div class="field">
      <label>SDR Gain (dB)</label>
      <input type="number" id="acars_gain" data-key="gain">
    </div>
    <div class="field">
      <label>Frequencies (space-separated, MHz)</label>
      <textarea id="acars_frequencies" data-key="frequencies"
                rows="3">129.125 130.025 130.425 130.450</textarea>
      <div class="hint">
        All frequencies must fit within 2 MHz RTL-SDR bandwidth.<br>
        Common: 129.125 130.025 130.425 130.450
      </div>
    </div>
  </div>
  <button class="btn btn-save" id="save-acars"
          onclick="saveConfig('acars','acarsdec')">Save &amp; Restart ACARS</button>
</div>

<!-- ── System ─────────────────────────────────────────────── -->
<div class="panel" id="tab-system">
  <div class="card">
    <div class="card-title">Services</div>
    <div id="svc-loading" class="loading">Loading…</div>
    <table class="svc-table" id="svc-table" style="display:none">
      <thead>
        <tr>
          <th>Service</th>
          <th>Status</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody id="svc-tbody"></tbody>
    </table>
  </div>
  <div class="card" style="margin-top:12px">
    <div class="card-title">Quick Links</div>
    <div style="display:flex;flex-direction:column;gap:8px;font-size:.88em">
      <a href="http://192.168.100.1:8080" target="_blank"
         style="color:#0af">NOAA Web UI → :8080</a>
      <a href="http://192.168.100.1:8081" target="_blank"
         style="color:#0af">ACARS Web UI → :8081</a>
      <a href="http://192.168.100.1:8082" target="_blank"
         style="color:#0af">Recorder Web UI → :8082</a>
      <a href="http://192.168.100.1:8080/tar1090" target="_blank"
         style="color:#0af">ADSB Map (tar1090) → :8080/tar1090</a>
    </div>
  </div>
</div>

<div id="toast"></div>

<script>
const TABS = ['autorx','adsb','noaa','recorder','acars','system'];
let activeTab = 'autorx';

function showTab(name) {
  TABS.forEach(t => {
    document.getElementById('tab-' + t).classList.toggle('active', t === name);
  });
  document.querySelectorAll('.tab').forEach((el, i) => {
    el.classList.toggle('active', TABS[i] === name);
  });
  activeTab = name;
  if (name === 'system') loadServices();
  else loadConfig(name);
}

async function loadConfig(section) {
  try {
    const r    = await fetch('/api/config/' + section);
    const data = await r.json();
    fillForm(section, data);
  } catch(e) {
    toast('Failed to load config', 'err');
  }
}

function fillForm(section, data) {
  for (const [key, val] of Object.entries(data)) {
    const el = document.getElementById(section + '_' + key);
    if (!el) continue;
    if (el.type === 'checkbox') {
      el.checked = (val === true || val === 'True' || val === '1' || val === 1);
    } else {
      el.value = val;
    }
  }
}

async function saveConfig(section, restartSvc) {
  const form = document.getElementById('tab-' + section);
  const data = {};
  form.querySelectorAll('[data-key]').forEach(el => {
    const key = el.dataset.key;
    if (el.type === 'checkbox') {
      data[key] = el.checked ? 'True' : 'False';
    } else {
      data[key] = el.value;
    }
  });

  const btn = document.getElementById('save-' + section);
  btn.disabled = true;
  btn.textContent = 'Saving…';

  try {
    const r = await fetch('/api/save/' + section, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data)
    });
    const result = await r.json();
    if (result.ok) {
      toast('Saved & service restarted ✓', 'ok');
    } else {
      toast('Error: ' + (result.error || 'unknown'), 'err');
    }
  } catch(e) {
    toast('Network error', 'err');
  }
  btn.disabled = false;
  btn.textContent = btn.dataset.orig || 'Save & Restart';
}

async function loadServices() {
  document.getElementById('svc-loading').style.display = 'block';
  document.getElementById('svc-table').style.display  = 'none';
  try {
    const r    = await fetch('/api/services');
    const data = await r.json();
    const tb   = document.getElementById('svc-tbody');
    tb.innerHTML = data.map(s => `
      <tr>
        <td>${s.label}</td>
        <td><span class="badge ${s.status}">${s.status}</span></td>
        <td>
          <button class="svc-btn" title="Start"   onclick="ctrlSvc('${s.name}','start')">▶</button>
          <button class="svc-btn" title="Stop"    onclick="ctrlSvc('${s.name}','stop')">■</button>
          <button class="svc-btn" title="Restart" onclick="ctrlSvc('${s.name}','restart')">↺</button>
        </td>
      </tr>`).join('');
    document.getElementById('svc-loading').style.display = 'none';
    document.getElementById('svc-table').style.display   = 'table';
  } catch(e) {
    document.getElementById('svc-loading').textContent = 'Failed to load services';
  }
}

async function ctrlSvc(name, action) {
  const r    = await fetch('/api/service/' + action + '/' + name, {method:'POST'});
  const data = await r.json();
  toast(data.ok ? `${action} OK` : (data.error || 'error'), data.ok ? 'ok' : 'err');
  setTimeout(loadServices, 1800);
}

let _toastTimer = null;
function toast(msg, type) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className   = 'toast ' + type;
  el.style.display = 'block';
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.style.display = 'none', 3200);
}

// Store original button text
document.querySelectorAll('.btn-save').forEach(btn => {
  btn.dataset.orig = btn.textContent;
});

// Load initial tab
loadConfig('autorx');
</script>
</body>
</html>
"""


# ─────────────────────────────────────────────────────────────────────────────
# HTTP Handler
# ─────────────────────────────────────────────────────────────────────────────

_READERS = {
    "autorx":   read_autorx,
    "adsb":     read_readsb,
    "noaa":     read_noaa,
    "recorder": read_recorder,
    "acars":    read_acars,
}

_WRITERS = {
    "autorx":   (write_autorx,   ["auto-rx"]),
    "adsb":     (write_readsb,   ["readsb", "tar1090"]),
    "noaa":     (write_noaa,     ["noaa_capture"]),
    "recorder": (write_recorder, ["sdr_recorder"]),
    "acars":    (write_acars,    ["acarsdec"]),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence access logs

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ('/', '/index.html', '/generate_204', '/hotspot-detect.html'):
            self._html(HTML.encode())
        elif p.startswith('/api/config/'):
            section = p.split('/')[-1]
            if section in _READERS:
                self._json(_READERS[section]())
            else:
                self.send_error(404)
        elif p == '/api/services':
            statuses = [
                {"name": n, "label": l, "status": service_status(n)}
                for n, l in SERVICES
            ]
            self._json(statuses)
        else:
            # Captive portal: redirect all unknown URLs to home
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()

    def do_POST(self):
        p      = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}

        if p.startswith('/api/save/'):
            section = p.split('/')[-1]
            if section in _WRITERS:
                writer, svcs = _WRITERS[section]
                ok, errors = writer(body)
                if ok:
                    for svc in svcs:
                        service_control(svc, "restart")
                    self._json({"ok": True})
                else:
                    self._json({"ok": False, "error": "; ".join(errors)})
            else:
                self.send_error(404)

        elif p.startswith('/api/service/'):
            parts = p.strip('/').split('/')
            # parts: api, service, <action>, <name>
            if len(parts) == 4 and parts[2] in ("start", "stop", "restart"):
                action = parts[2]
                name   = parts[3]
                ok, err = service_control(name, action)
                self._json({"ok": ok, "error": err})
            else:
                self.send_error(400)

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


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def run(stop_event):
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.timeout = 1
    while not stop_event.is_set():
        server.handle_request()
    server.server_close()


if __name__ == "__main__":
    import signal
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda s, f: stop.set())
    signal.signal(signal.SIGINT,  lambda s, f: stop.set())
    run(stop)
