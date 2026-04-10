#!/usr/bin/env python3
"""
SDR Scanner Recorder — VOX-triggered recording daemon.
Listens on a configured frequency, records each transmission to a separate
WAV file when signal exceeds squelch threshold.
Config: /etc/sdr_recorder.cfg
"""

import json
import os
import signal
import subprocess
import threading
import time
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
    "silence_dur":     1.2,    # seconds of silence before cutting
    "silence_thresh":  "2%",   # sox silence threshold
}

_stop_event   = threading.Event()
_reload_event = threading.Event()
_rtl_proc     = None
_sox_proc     = None
_state        = "idle"   # idle | listening | recording
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


def prune_recordings(max_keep):
    """Delete oldest recordings if over limit."""
    files = sorted(REC_DIR.glob("*.wav"), key=lambda f: f.stat().st_mtime)
    while len(files) > max_keep:
        try:
            files.pop(0).unlink()
        except Exception:
            pass


def record_loop():
    global _rtl_proc, _sox_proc

    while not _stop_event.is_set():
        _reload_event.clear()
        cfg = load_cfg()
        REC_DIR.mkdir(parents=True, exist_ok=True)

        freq   = cfg["frequency"]
        sq     = int(cfg["squelch"])
        mode   = cfg["mode"]
        rate   = int(cfg["sample_rate"])
        gain   = int(cfg["gain"])
        maxrec = int(cfg["max_recordings"])
        mindur = float(cfg["min_duration"])
        sildur = float(cfg["silence_dur"])
        silth  = cfg["silence_thresh"]

        log.info(f"Starting: freq={freq} squelch={sq} mode={mode} gain={gain}")
        set_state("listening")

        # rtl_fm → stdout raw audio
        rtl_cmd = [
            "rtl_fm",
            "-f", freq,
            "-M", mode,
            "-s", str(rate),
            "-g", str(gain),
            "-l", str(sq),
            "-",
        ]

        try:
            _rtl_proc = subprocess.Popen(
                rtl_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            log.error(f"rtl_fm failed to start: {e}")
            set_state("idle")
            time.sleep(5)
            continue

        # sox reads raw pipe, splits on silence, writes individual WAV files
        # We use a temp pipe file and handle splitting ourselves for better control
        # sox in pipe mode with silence + newfile for clean splitting
        ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = REC_DIR / f"{freq.replace('.','_')}_{ts}.wav"

        sox_cmd = [
            "sox",
            "-t", "raw",
            "-r", str(rate),
            "-e", "signed-integer",
            "-b", "16",
            "-c", "1",
            "-",                          # stdin
            str(fname),                   # output
            "silence",
            "1", "0.1", silth,            # start trigger: 0.1s above threshold
            "1", str(sildur), silth,      # stop trigger: silence_dur below threshold
        ]

        try:
            _sox_proc = subprocess.Popen(
                sox_cmd,
                stdin=_rtl_proc.stdout,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            log.error(f"sox failed to start: {e}")
            _rtl_proc.kill()
            set_state("idle")
            time.sleep(5)
            continue

        set_state("recording" if fname.exists() else "listening", str(fname))

        # Monitor: wait for sox to finish one clip, then loop for next
        while not _stop_event.is_set() and not _reload_event.is_set():
            ret = _sox_proc.poll()
            if ret is not None:
                # sox finished one clip — check if file is worth keeping
                if fname.exists():
                    dur = _get_duration(fname)
                    if dur < mindur:
                        fname.unlink(missing_ok=True)
                        log.debug(f"Discarded short clip ({dur:.1f}s)")
                    else:
                        log.info(f"Saved: {fname.name} ({dur:.1f}s)")
                        prune_recordings(maxrec)

                # Start next clip
                ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
                fname = REC_DIR / f"{freq.replace('.','_')}_{ts}.wav"
                set_state("listening")

                try:
                    _sox_proc = subprocess.Popen(
                        [sox_cmd[0]] + sox_cmd[1:-8] + [str(fname)] + sox_cmd[-7:],
                        stdin=_rtl_proc.stdout,
                        stderr=subprocess.DEVNULL,
                    )
                except Exception as e:
                    log.error(f"sox restart failed: {e}")
                    break

            time.sleep(0.2)
            # Update status with current file
            if fname.exists() and fname.stat().st_size > 44:
                set_state("recording", str(fname))
            else:
                set_state("listening")

        # Clean up
        try:
            _sox_proc.kill()
        except Exception:
            pass
        try:
            _rtl_proc.kill()
            _rtl_proc.wait(timeout=3)
        except Exception:
            pass
        _rtl_proc = None
        _sox_proc = None
        set_state("idle")

        if _stop_event.is_set():
            break
        if _reload_event.is_set():
            log.info("Config reload — restarting...")
            time.sleep(0.5)


def _get_duration(path):
    """Return duration of WAV file in seconds."""
    try:
        r = subprocess.run(
            ["soxi", "-D", str(path)],
            capture_output=True, text=True
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def signal_handler(sig, frame):
    log.info("Stopping...")
    _stop_event.set()
    try:
        if _rtl_proc:
            _rtl_proc.kill()
        if _sox_proc:
            _sox_proc.kill()
    except Exception:
        pass


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT,  signal_handler)
    REC_DIR.mkdir(parents=True, exist_ok=True)
    log.info("SDR Recorder starting")
    record_loop()
    log.info("SDR Recorder stopped")
