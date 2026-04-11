#!/usr/bin/env python3
"""
SDR Scanner Recorder — VOX-triggered recording daemon.
Uses Python-based RMS level detection instead of sox silence piping,
for reliable start/stop of recordings.
Config: /etc/sdr_recorder.cfg
"""

import json
import math
import os
import signal
import struct
import subprocess
import threading
import time
import wave
import logging
from pathlib import Path
from datetime import datetime

CFG_FILE    = Path("/etc/sdr_recorder.cfg")
REC_DIR     = Path("/var/lib/sdr_recorder")
STATUS_FILE = Path("/tmp/sdr_recorder_status.json")
LOG_FILE    = Path("/var/log/sdr_recorder.log")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

DEFAULT_CFG = {
    "frequency":       "145.775M",
    "squelch":         30,
    "mode":            "fm",
    "sample_rate":     24000,
    "gain":            40,
    "max_recordings":  50,
    "min_duration":    1.0,    # seconds — discard shorter clips
    "silence_dur":     1.5,    # seconds of silence before closing file
    "vox_threshold":   500,    # RMS level 0-32768 to trigger recording
}

_stop_event   = threading.Event()
_reload_event = threading.Event()
_rtl_proc     = None
_state        = "idle"
_state_lock   = threading.Lock()
_cur_file     = ""


def load_cfg():
    try:
        cfg = dict(DEFAULT_CFG)
        cfg.update(json.loads(CFG_FILE.read_text()))
        return cfg
    except Exception:
        return dict(DEFAULT_CFG)


def set_state(s, fname=""):
    global _state, _cur_file
    with _state_lock:
        _state    = s
        _cur_file = fname
    write_status()


def write_status():
    try:
        with _state_lock:
            s = _state
            f = _cur_file
        cfg = load_cfg()
        STATUS_FILE.write_text(json.dumps({
            "state":     s,
            "cur_file":  f,
            "frequency": cfg["frequency"],
            "squelch":   cfg["squelch"],
            "mode":      cfg["mode"],
            "gain":      cfg["gain"],
        }))
    except Exception:
        pass


def calc_rms(data):
    """Calculate RMS level of 16-bit signed PCM chunk."""
    if len(data) < 2:
        return 0
    count  = len(data) // 2
    shorts = struct.unpack(f"<{count}h", data[:count * 2])
    sq_sum = sum(s * s for s in shorts)
    return int(math.sqrt(sq_sum / count)) if count else 0


def prune_recordings(max_keep):
    files = sorted(REC_DIR.glob("*.wav"), key=lambda f: f.stat().st_mtime)
    while len(files) > max_keep:
        try:
            files.pop(0).unlink()
            log.debug("Pruned old recording")
        except Exception:
            pass


def make_filename(freq):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = freq.replace(".", "_").replace(" ", "")
    return REC_DIR / f"{safe}_{ts}.wav"


def open_wav(path, rate):
    wf = wave.open(str(path), "wb")
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(rate)
    return wf


def get_duration_secs(path):
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        return 0.0


def record_loop():
    global _rtl_proc

    while not _stop_event.is_set():
        _reload_event.clear()
        cfg = load_cfg()
        REC_DIR.mkdir(parents=True, exist_ok=True)

        freq      = cfg["frequency"]
        mode      = cfg["mode"]
        rate      = int(cfg["sample_rate"])
        gain      = int(cfg["gain"])
        squelch   = int(cfg["squelch"])
        maxrec    = int(cfg["max_recordings"])
        mindur    = float(cfg["min_duration"])
        sildur    = float(cfg["silence_dur"])
        vox_thr   = int(cfg["vox_threshold"])

        # Chunk size: 50ms of 16-bit mono audio
        chunk_bytes = int(rate * 0.05) * 2

        log.info(f"Starting: freq={freq} mode={mode} squelch={squelch} "
                 f"gain={gain} vox={vox_thr}")
        set_state("listening")

        rtl_cmd = [
            "rtl_fm",
            "-f", freq,
            "-M", mode,
            "-s", str(rate),
            "-g", str(gain),
            "-l", str(squelch),
            "-",
        ]

        try:
            _rtl_proc = subprocess.Popen(
                rtl_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=chunk_bytes * 4,
            )
        except Exception as e:
            log.error(f"rtl_fm failed: {e}")
            set_state("idle")
            time.sleep(5)
            continue

        wf              = None
        cur_path        = None
        rec_start       = 0.0
        silence_since   = None   # timestamp when signal dropped

        try:
            while not _stop_event.is_set() and not _reload_event.is_set():
                if _rtl_proc.poll() is not None:
                    log.warning("rtl_fm exited unexpectedly")
                    break

                chunk = _rtl_proc.stdout.read(chunk_bytes)
                if not chunk:
                    time.sleep(0.01)
                    continue

                rms = calc_rms(chunk)
                now = time.time()

                if rms >= vox_thr:
                    # ── Signal present ────────────────────
                    silence_since = None

                    if wf is None:
                        # Open new recording file
                        cur_path  = make_filename(freq)
                        wf        = open_wav(cur_path, rate)
                        rec_start = now
                        set_state("recording", str(cur_path))
                        log.info(f"Recording started: {cur_path.name}")

                    wf.writeframes(chunk)

                else:
                    # ── Silence / below threshold ─────────
                    if wf is not None:
                        wf.writeframes(chunk)   # write tail to avoid cutoff

                        if silence_since is None:
                            silence_since = now
                        elif now - silence_since >= sildur:
                            # Close file
                            wf.close()
                            wf = None
                            dur = get_duration_secs(cur_path)
                            if dur < mindur:
                                cur_path.unlink(missing_ok=True)
                                log.debug(f"Discarded short clip ({dur:.1f}s)")
                            else:
                                log.info(f"Saved: {cur_path.name} ({dur:.1f}s)")
                                prune_recordings(maxrec)
                            cur_path      = None
                            silence_since = None
                            set_state("listening")
                    else:
                        set_state("listening")

        except Exception as e:
            log.error(f"Record loop error: {e}")
        finally:
            if wf is not None:
                try:
                    wf.close()
                    dur = get_duration_secs(cur_path)
                    if cur_path and dur < mindur:
                        cur_path.unlink(missing_ok=True)
                    elif cur_path:
                        log.info(f"Saved on exit: {cur_path.name} ({dur:.1f}s)")
                        prune_recordings(maxrec)
                except Exception:
                    pass
            try:
                _rtl_proc.kill()
                _rtl_proc.wait(timeout=3)
            except Exception:
                pass
            _rtl_proc = None
            set_state("idle")

        if _stop_event.is_set():
            break
        if _reload_event.is_set():
            log.info("Config reload — restarting...")
            time.sleep(0.5)


def signal_handler(sig, frame):
    log.info("Stopping...")
    _stop_event.set()
    try:
        if _rtl_proc:
            _rtl_proc.kill()
    except Exception:
        pass


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT,  signal_handler)
    signal.signal(signal.SIGHUP,  lambda s, f: _reload_event.set())
    REC_DIR.mkdir(parents=True, exist_ok=True)
    log.info("SDR Recorder starting")
    record_loop()
    log.info("SDR Recorder stopped")
