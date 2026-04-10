#!/usr/bin/env python3
"""
ACARS Web UI — real-time aircraft datalink message viewer.
Serves on port 8081 — browse to http://DEVICE_IP:8081
"""

import json
import os
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta

LOG_FILE  = Path("/var/log/acarsdec/messages.json")
MAX_MSG   = 200   # keep last N messages in memory
PORT      = 8081

# ACARS label descriptions (most common)
LABEL_DESC = {
    "H1": "ATIS/Weather",   "H2": "ATIS/Weather",
    "Q0": "Ping/Empty",     "QD": "Ping",
    "5Z": "Position",       "4A": "ETA/Arrival",
    "4N": "ETA",            "44": "OUT/OFF/ON/IN",
    "10": "Engine Data",    "12": "Engine Data",
    "13": "Engine Data",    "14": "Engine Data",
    "15": "Engine Data",    "16": "Engine Data",
    "SA": "Free Text",      "SQ": "Free Text",
    "80": "Weather Obs",    "81": "Weather",
    "20": "Position",       "21": "Position",
    "22": "Position",
    "7B": "Out of Service", "7W": "Turbulence",
    "B6": "FMS",            "F3": "Oceanic",
    "AA": "ACARS Report",
}

LABEL_COLOR = {
    "H1": "#0af", "H2": "#0af",   # weather — blue
    "Q0": "#444", "QD": "#444",   # ping    — grey
    "5Z": "#0d0", "20": "#0d0",   # position — green
    "21": "#0d0", "22": "#0d0",
    "4A": "#fa0", "4N": "#fa0",   # ETA     — amber
    "44": "#f80",                  # events  — orange
    "10": "#a0f", "12": "#a0f",   # engine  — purple
    "13": "#a0f", "14": "#a0f",
    "SA": "#eee", "SQ": "#eee",   # text    — white
    "80": "#0cf", "81": "#0cf",   # wx obs  — cyan
}

IL_TZ = timezone(timedelta(hours=3))


def load_messages():
    """Read last MAX_MSG lines from the JSON log."""
    msgs = []
    if not LOG_FILE.exists():
        return msgs
    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return msgs
            chunk = min(size, 65536)
            f.seek(max(0, size - chunk))
            raw = f.read().decode(errors="ignore")
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                msgs.append(json.loads(line))
            except Exception:
                pass
        # newest first
        msgs = msgs[-MAX_MSG:]
        msgs.reverse()
    except Exception:
        pass
    return msgs


def fmt_time(ts):
    try:
        dt = datetime.fromtimestamp(int(ts), tz=IL_TZ)
        return dt.strftime("%H:%M:%S")
    except Exception:
        return ""


def fmt_date(ts):
    try:
        dt = datetime.fromtimestamp(int(ts), tz=IL_TZ)
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return ""


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ACARS — OrangePi SDR</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:monospace;background:#0d0d0d;color:#ddd;padding:12px 16px}
h1{color:#0af;font-size:1.2em;margin-bottom:10px}
h1 span{color:#666;font-size:.8em;font-weight:normal;margin-left:8px}

/* Stats bar */
.stats{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px}
.stat{background:#151515;border:1px solid #222;border-radius:4px;
      padding:6px 12px;font-size:.8em}
.stat b{color:#0af}

/* Filter bar */
.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.filters input,.filters select{
  background:#151515;border:1px solid #333;color:#eee;
  padding:5px 8px;border-radius:4px;font-family:monospace;font-size:.85em}
.filters input:focus,.filters select:focus{outline:none;border-color:#0af}
.filters button{background:#1a2a3a;border:1px solid #0af;color:#0af;
  padding:5px 12px;border-radius:4px;cursor:pointer;font-family:monospace;font-size:.85em}
.filters button:hover{background:#0af;color:#000}

/* Table */
.wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:.82em}
th{background:#111;color:#888;text-align:left;padding:5px 8px;
   border-bottom:1px solid #222;position:sticky;top:0;white-space:nowrap}
td{padding:4px 8px;border-bottom:1px solid #1a1a1a;vertical-align:top;white-space:nowrap}
td.msg{white-space:pre-wrap;word-break:break-all;max-width:380px;color:#ccc}
tr:hover td{background:#161616}
tr.new td{animation:flash .6s ease-out}
@keyframes flash{from{background:#0a2a0a}to{background:transparent}}

.label-badge{
  display:inline-block;padding:1px 5px;border-radius:3px;
  font-size:.78em;font-weight:bold;color:#000}
.flight{color:#0af;font-weight:bold}
.tail{color:#888}
.freq{color:#fa0}
.time{color:#666}
.level{color:#555}
.empty{color:#444;font-style:italic}

/* Refresh indicator */
.refresh{position:fixed;top:10px;right:14px;font-size:.75em;color:#333}
.refresh.active{color:#0a4}

/* Scrolltop */
.totop{position:fixed;bottom:14px;right:14px;background:#1a2a3a;
       border:1px solid #0af;color:#0af;padding:4px 10px;
       border-radius:4px;cursor:pointer;font-size:.8em;display:none}
</style>
</head>
<body>
<h1>ACARS <span>Aircraft Datalink Messages</span></h1>

<div class="stats">
  <div class="stat">Messages: <b id="s-total">0</b></div>
  <div class="stat">Flights: <b id="s-flights">0</b></div>
  <div class="stat">Last: <b id="s-last">—</b></div>
  <div class="stat">Updated: <b id="s-updated">—</b></div>
</div>

<div class="filters">
  <input id="f-flight" placeholder="Flight filter…" oninput="applyFilter()">
  <input id="f-text"   placeholder="Text search…"   oninput="applyFilter()">
  <select id="f-label" onchange="applyFilter()">
    <option value="">All labels</option>
  </select>
  <select id="f-freq" onchange="applyFilter()">
    <option value="">All frequencies</option>
  </select>
  <button onclick="clearFilters()">Clear</button>
</div>

<div class="wrap">
<table id="tbl">
<thead>
<tr>
  <th>Time (IL)</th>
  <th>Flight</th>
  <th>Tail</th>
  <th>Freq</th>
  <th>Label</th>
  <th>Msg#</th>
  <th>Message</th>
  <th>dBm</th>
</tr>
</thead>
<tbody id="tbody"></tbody>
</table>
</div>

<div class="refresh" id="ind">⬤ live</div>
<div class="totop" id="totop" onclick="window.scrollTo(0,0)">▲ top</div>

<script>
const LABEL_DESC = """ + json.dumps(LABEL_DESC) + r""";
const LABEL_COLOR = """ + json.dumps(LABEL_COLOR) + r""";

let allRows = [];
let knownIds = new Set();
let firstLoad = true;

function labelBadge(lbl) {
  const col = LABEL_COLOR[lbl] || '#555';
  const desc = LABEL_DESC[lbl] || lbl;
  return `<span class="label-badge" style="background:${col}" title="${desc}">${lbl}</span>`;
}

function makeRow(m, isNew) {
  const time   = m.time_il  || '';
  const flight = m.flight   || '';
  const tail   = m.tail     || '';
  const freq   = m.freq     || '';
  const label  = m.label    || '';
  const msgno  = m.msgno    || '';
  const text   = m.text     || '';
  const level  = m.level != null ? Math.round(m.level) + 'dB' : '';
  const newCls = isNew ? ' class="new"' : '';
  const textCell = text
    ? `<td class="msg">${escHtml(text)}</td>`
    : `<td class="empty">—</td>`;
  return `<tr${newCls} data-flight="${escAttr(flight)}" data-label="${escAttr(label)}" data-freq="${escAttr(freq)}" data-text="${escAttr(text)}">
    <td class="time">${time}</td>
    <td class="flight">${escHtml(flight)}</td>
    <td class="tail">${escHtml(tail)}</td>
    <td class="freq">${escHtml(freq)}</td>
    <td>${labelBadge(label)}</td>
    <td class="tail">${escHtml(msgno)}</td>
    ${textCell}
    <td class="level">${level}</td>
  </tr>`;
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function escAttr(s) {
  return String(s).replace(/"/g,'&quot;');
}

function populateFilters(msgs) {
  const labels = [...new Set(msgs.map(m => m.label).filter(Boolean))].sort();
  const freqs  = [...new Set(msgs.map(m => m.freq).filter(Boolean))].sort();
  const lSel = document.getElementById('f-label');
  const fSel = document.getElementById('f-freq');
  const curL = lSel.value, curF = fSel.value;
  lSel.innerHTML = '<option value="">All labels</option>' +
    labels.map(l => `<option value="${l}">${l} — ${LABEL_DESC[l]||l}</option>`).join('');
  fSel.innerHTML = '<option value="">All frequencies</option>' +
    freqs.map(f => `<option value="${f}">${f} MHz</option>`).join('');
  lSel.value = curL;
  fSel.value = curF;
}

function updateStats(msgs) {
  document.getElementById('s-total').textContent   = msgs.length;
  const flights = new Set(msgs.map(m=>m.flight).filter(Boolean));
  document.getElementById('s-flights').textContent = flights.size;
  if (msgs.length) document.getElementById('s-last').textContent = msgs[0].time_il || '—';
  const now = new Date();
  document.getElementById('s-updated').textContent =
    now.toLocaleTimeString('he-IL',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
}

function applyFilter() {
  const fFlight = document.getElementById('f-flight').value.toUpperCase();
  const fText   = document.getElementById('f-text').value.toUpperCase();
  const fLabel  = document.getElementById('f-label').value;
  const fFreq   = document.getElementById('f-freq').value;
  const tbody   = document.getElementById('tbody');
  const rows    = tbody.querySelectorAll('tr');
  rows.forEach(tr => {
    const ok =
      (!fFlight || tr.dataset.flight.includes(fFlight)) &&
      (!fText   || tr.dataset.text.toUpperCase().includes(fText)) &&
      (!fLabel  || tr.dataset.label === fLabel) &&
      (!fFreq   || tr.dataset.freq  === fFreq);
    tr.style.display = ok ? '' : 'none';
  });
}

function clearFilters() {
  ['f-flight','f-text'].forEach(id => document.getElementById(id).value = '');
  ['f-label','f-freq'].forEach(id => document.getElementById(id).value = '');
  applyFilter();
}

async function refresh() {
  const ind = document.getElementById('ind');
  try {
    const res = await fetch('/api/messages');
    if (!res.ok) throw new Error('bad response');
    const msgs = await res.json();

    const tbody = document.getElementById('tbody');

    if (firstLoad) {
      allRows = msgs;
      msgs.forEach(m => knownIds.add(m._id));
      tbody.innerHTML = msgs.map(m => makeRow(m, false)).join('');
      populateFilters(msgs);
      firstLoad = false;
    } else {
      const newMsgs = msgs.filter(m => !knownIds.has(m._id));
      if (newMsgs.length) {
        newMsgs.forEach(m => knownIds.add(m._id));
        const html = newMsgs.map(m => makeRow(m, true)).join('');
        tbody.insertAdjacentHTML('afterbegin', html);
        allRows = msgs;
        populateFilters(msgs);
      }
    }

    updateStats(msgs);
    applyFilter();
    ind.textContent = '⬤ live';
    ind.className = 'refresh active';
  } catch(e) {
    ind.textContent = '⬤ offline';
    ind.className = 'refresh';
  }
  setTimeout(refresh, 5000);
}

window.addEventListener('scroll', () => {
  document.getElementById('totop').style.display = window.scrollY > 300 ? 'block' : 'none';
});

refresh();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress access log

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_html(HTML.encode())
        elif self.path == "/api/messages":
            self.serve_api()
        else:
            self.send_error(404)

    def send_html(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def serve_api(self):
        msgs = load_messages()
        # Enrich each message
        enriched = []
        for i, m in enumerate(msgs):
            em = dict(m)
            em["time_il"] = fmt_time(m.get("timestamp", 0))
            em["date_il"] = fmt_date(m.get("timestamp", 0))
            em["_id"]     = f"{m.get('timestamp',0)}_{m.get('tail','')}_{m.get('msgno','')}"
            enriched.append(em)
        data = json.dumps(enriched).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(data))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"ACARS web UI on port {PORT}")
    server.serve_forever()
