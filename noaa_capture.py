#!/usr/bin/env python3
"""
NOAA APT Satellite Capture Script
Schedules and captures NOAA-15, NOAA-18, NOAA-19 passes over the station.
Runs as a systemd service. When a satellite is overhead, stops the current SDR
mode, records the pass, decodes the APT image, then restores the previous mode.

Usage: python3 noaa_capture.py
"""

import os
import sys
import time
import math
import subprocess
import threading
import logging
import json
from datetime import datetime, timezone
from pathlib import Path

import ephem

# ── Config ────────────────────────────────────────────────────────────────────
LAT        = 32.0853    # Station latitude  (Tel Aviv default)
LON        = 34.7818    # Station longitude
ALT        = 30         # Station altitude (meters)
MIN_ELEV   = 15         # Minimum elevation to record (degrees)
FREQ = {
    "NOAA-15": "137.620M",
    "NOAA-18": "137.9125M",
    "NOAA-19": "137.100M",
}
IMAGE_DIR  = Path("/var/lib/noaa-apt/images")
TLE_FILE   = Path("/var/lib/noaa-apt/tle.txt")
TLE_URL    = "https://celestrak.org/NORAD/elements/gp.php?GROUP=noaa&FORMAT=tle"
RTL_GAIN   = "40"       # RTL-SDR gain (dB), "0" for auto
SAMPLE_RATE = "60000"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [NOAA] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("noaa")

# ── TLE management ────────────────────────────────────────────────────────────
def update_tle():
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    TLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            ["curl", "-s", "--max-time", "15", TLE_URL],
            capture_output=True, text=True)
        if r.returncode == 0 and "NOAA" in r.stdout:
            TLE_FILE.write_text(r.stdout)
            log.info("TLE updated")
        else:
            log.warning("TLE update failed — using cached")
    except Exception as e:
        log.warning(f"TLE update error: {e}")

def load_satellites():
    """Parse TLE file and return dict of ephem.EarthSatellite objects."""
    sats = {}
    if not TLE_FILE.exists():
        update_tle()
    lines = TLE_FILE.read_text().splitlines()
    i = 0
    while i < len(lines) - 2:
        name = lines[i].strip()
        if name in FREQ:
            try:
                sat = ephem.readtle(name, lines[i+1], lines[i+2])
                sats[name] = sat
            except Exception:
                pass
        i += 1
    return sats

# ── Pass prediction ───────────────────────────────────────────────────────────
def next_pass(sat, observer):
    """Return (rise_time_utc, set_time_utc, max_elevation_deg) or None."""
    try:
        info = observer.next_pass(sat)
        # info: (rise_time, rise_az, max_time, max_elev, set_time, set_az)
        max_elev = math.degrees(info[3])
        if max_elev < MIN_ELEV:
            return None
        rise_dt = ephem.Date(info[0]).datetime().replace(tzinfo=timezone.utc)
        set_dt  = ephem.Date(info[4]).datetime().replace(tzinfo=timezone.utc)
        return rise_dt, set_dt, round(max_elev, 1)
    except Exception:
        return None

def make_observer():
    obs = ephem.Observer()
    obs.lat  = str(LAT)
    obs.lon  = str(LON)
    obs.elev = ALT
    obs.pressure = 0   # disable atmospheric refraction
    return obs

# ── Capture ───────────────────────────────────────────────────────────────────
_capturing = False

def capture_pass(sat_name, freq, duration_sec):
    """Record and decode one satellite pass."""
    global _capturing
    _capturing = True

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    wav_path = IMAGE_DIR / f"{sat_name.replace(' ','')}_{ts}.wav"
    img_path = IMAGE_DIR / f"{sat_name.replace(' ','')}_{ts}.png"

    log.info(f"Recording {sat_name} — {duration_sec}s → {wav_path.name}")

    # Stop any running SDR service via systemctl signal file
    Path("/tmp/noaa_capturing").write_text(sat_name)

    try:
        # Record raw FM audio with rtl_fm
        rtl = subprocess.Popen([
            "rtl_fm",
            "-f", freq,
            "-M", "fm",
            "-s", SAMPLE_RATE,
            "-g", RTL_GAIN,
            "-E", "deemp",
            "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL)

        # Convert raw PCM → WAV with sox
        sox = subprocess.Popen([
            "sox",
            "-t", "raw", "-r", SAMPLE_RATE, "-e", "signed", "-b", "16", "-",
            "-t", "wav", str(wav_path),
            "rate", "11025"],
            stdin=rtl.stdout,
            stderr=subprocess.DEVNULL)

        time.sleep(duration_sec)

        rtl.terminate()
        sox.wait(timeout=15)

    except Exception as e:
        log.error(f"Capture error: {e}")
        _capturing = False
        Path("/tmp/noaa_capturing").unlink(missing_ok=True)
        return

    log.info(f"Decoding {wav_path.name} → {img_path.name}")
    try:
        subprocess.run([
            "noaa-apt",
            "-o", str(img_path),
            "--satellite", sat_name,
            "--color", "no",
            str(wav_path)],
            timeout=120, capture_output=True)
        log.info(f"Image saved: {img_path.name}")
    except Exception as e:
        log.error(f"Decode error: {e}")

    # Save metadata
    meta = {
        "satellite": sat_name,
        "timestamp": ts,
        "image": img_path.name,
        "wav": wav_path.name,
    }
    (IMAGE_DIR / f"{sat_name.replace(' ','')}_{ts}.json").write_text(
        json.dumps(meta, indent=2))

    _capturing = False
    Path("/tmp/noaa_capturing").unlink(missing_ok=True)

# ── Scheduler loop ────────────────────────────────────────────────────────────
def scheduler():
    log.info("NOAA APT scheduler started")
    update_tle()
    last_tle_update = time.time()

    while True:
        # Refresh TLE every 12 hours
        if time.time() - last_tle_update > 43200:
            update_tle()
            last_tle_update = time.time()

        sats = load_satellites()
        if not sats:
            log.warning("No satellites loaded — retrying in 60s")
            time.sleep(60)
            continue

        obs = make_observer()
        obs.date = ephem.now()

        # Find next pass across all satellites
        passes = []
        for name, sat in sats.items():
            p = next_pass(sat, obs)
            if p:
                passes.append((p[0], name, FREQ[name], p[1], p[2]))

        if not passes:
            log.info("No passes above threshold — checking again in 5min")
            time.sleep(300)
            continue

        passes.sort()
        rise, sat_name, freq, set_time, max_elev = passes[0]

        now_utc = datetime.now(timezone.utc)
        wait_sec = (rise - now_utc).total_seconds()

        log.info(f"Next pass: {sat_name} in {int(wait_sec)}s "
                 f"(max elev {max_elev}°) at {rise.strftime('%H:%M:%S')} UTC")

        # Write next pass info for web UI and OLED
        next_info = {
            "satellite": sat_name,
            "rise_utc": rise.isoformat(),
            "set_utc": set_time.isoformat(),
            "max_elev": max_elev,
            "wait_sec": max(0, int(wait_sec)),
        }
        (IMAGE_DIR / "next_pass.json").write_text(json.dumps(next_info, indent=2))

        if wait_sec > 30:
            sleep = min(wait_sec - 20, 60)
            time.sleep(sleep)
            continue

        # Wait until rise
        if wait_sec > 0:
            time.sleep(wait_sec)

        if _capturing:
            time.sleep(10)
            continue

        duration = max(60, int((set_time - rise).total_seconds()) + 10)
        threading.Thread(
            target=capture_pass,
            args=(sat_name, freq, duration),
            daemon=True).start()

        # Don't schedule another pass during this one
        time.sleep(duration + 30)

if __name__ == "__main__":
    scheduler()
