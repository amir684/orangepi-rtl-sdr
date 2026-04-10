#!/usr/bin/env python3
"""
Simple web UI for NOAA APT captured images.
Serves on port 8080 — browse to http://DEVICE_IP:8080
"""

import json
import os
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone

IMAGE_DIR = Path("/var/lib/noaa-apt/images")

HTML_HEADER = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NOAA APT — OrangePi SDR</title>
<style>
  body{font-family:monospace;background:#111;color:#eee;margin:0;padding:16px}
  h1{color:#0af;margin:0 0 8px}
  .next{background:#1a2a1a;border:1px solid #0a4;padding:10px;margin:8px 0;border-radius:4px}
  .next h2{margin:0 0 4px;color:#0f0;font-size:1em}
  .gallery{display:flex;flex-wrap:wrap;gap:12px;margin-top:16px}
  .card{background:#1a1a1a;border:1px solid #333;border-radius:6px;padding:8px;max-width:320px}
  .card img{width:100%;border-radius:4px;display:block}
  .card .meta{font-size:0.75em;color:#aaa;margin-top:6px}
  .card .sat{color:#0af;font-weight:bold}
  .none{color:#666;margin-top:32px}
  a{color:#0af}
</style>
</head>
<body>
<h1>NOAA APT Satellite Images</h1>
"""

HTML_FOOTER = "</body></html>"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress access log

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.serve_index()
        elif self.path.startswith("/images/") and self.path.endswith(".png"):
            self.serve_image(self.path[8:])
        elif self.path == "/next_pass.json":
            self.serve_json()
        else:
            self.send_error(404)

    def serve_index(self):
        html = HTML_HEADER

        # Next pass info
        next_file = IMAGE_DIR / "next_pass.json"
        if next_file.exists():
            try:
                data = json.loads(next_file.read_text())
                wait = data.get("wait_sec", 0)
                if wait > 0:
                    h, m = divmod(wait // 60, 60)
                    s = wait % 60
                    wait_str = f"{h:02d}:{m:02d}:{s:02d}"
                else:
                    wait_str = "NOW"
                rise_utc = data.get("rise_utc", "")[:19].replace("T", " ")
                # Convert to Israel time (UTC+3)
                try:
                    from datetime import timedelta
                    rise_il_dt = datetime.fromisoformat(
                        data["rise_utc"]).astimezone(
                        timezone(timedelta(hours=3)))
                    rise_il = rise_il_dt.strftime("%H:%M:%S")
                except Exception:
                    rise_il = "?"
                html += f"""<div class="next">
<h2>Next pass: {data.get('satellite','')} — {data.get('max_elev',0)}° max elevation</h2>
<div>Rise: {rise_utc} UTC &nbsp;|&nbsp; 🇮🇱 {rise_il} IL &nbsp;|&nbsp; In: {wait_str}</div>
</div>"""
            except Exception:
                pass

        # Image gallery — newest first
        images = sorted(IMAGE_DIR.glob("*.png"), reverse=True)
        if not images:
            html += '<p class="none">No images yet — waiting for a satellite pass.</p>'
        else:
            html += '<div class="gallery">'
            for img in images[:20]:
                stem = img.stem
                meta_file = IMAGE_DIR / f"{stem}.json"
                sat_name = ""
                ts_str = ""
                if meta_file.exists():
                    try:
                        meta = json.loads(meta_file.read_text())
                        sat_name = meta.get("satellite", "")
                        ts = meta.get("timestamp", "")
                        if ts:
                            ts_str = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]} UTC"
                    except Exception:
                        pass
                html += f"""<div class="card">
<img src="/images/{img.name}" alt="{stem}">
<div class="meta">
  <span class="sat">{sat_name}</span><br>
  {ts_str}<br>
  <a href="/images/{img.name}" target="_blank">Full size</a>
</div>
</div>"""
            html += '</div>'

        html += HTML_FOOTER
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(html.encode()))
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_image(self, filename):
        # Sanitize filename
        filename = Path(filename).name
        img_path = IMAGE_DIR / filename
        if not img_path.exists() or not filename.endswith(".png"):
            self.send_error(404)
            return
        data = img_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def serve_json(self):
        next_file = IMAGE_DIR / "next_pass.json"
        if not next_file.exists():
            self.send_error(404)
            return
        data = next_file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    server = HTTPServer(("0.0.0.0", 8080), Handler)
    print("NOAA web UI on port 8080")
    server.serve_forever()
