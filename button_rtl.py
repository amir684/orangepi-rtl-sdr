import os
import re
import shutil
import json
import socket
import time
import subprocess
import threading
import OPi.GPIO as GPIO
from smbus2 import SMBus, i2c_msg
from PIL import Image, ImageDraw, ImageFont

# ── Hardware ──────────────────────────────────────────────
ADDR = 0x3C
BUS  = 2
BTN_UP    = "PI1"
BTN_DOWN  = "PI3"
BTN_BACK  = "PI14"
BTN_RIGHT = "PI2"
BTN_SEL   = "PI4"

AP_SSID = "OrangePi-SDR"
AP_PASS = "12345678"

CHARS = (list("abcdefghijklmnopqrstuvwxyz") +
         list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") +
         list("0123456789") +
         list("!@#$%&*-_=+.,; ") +
         ["<DEL>", "<OK>"])

# ── State ─────────────────────────────────────────────────
state       = "idle"
menu_idx    = 0
wifi_list   = []
wifi_idx    = 0
password    = ""
char_idx    = 0
rtl_process = None
rtl_active  = False          # flag to stop display thread after kill
ap_running  = False          # flag for AP mode active
display_lock = threading.Lock()
current_sdr_mode = "rtltcp"  # active SDR mode
sdr_data    = []             # [(label, mode_id), ...]
sdr_idx     = 0

# AutoRX config edit state
cfg_menu_idx      = 0
cfg_edit_field    = ""
cfg_edit_chars    = []
cfg_edit_cursor   = 0
cfg_edit_editable = []
cfg_call_chars    = []
cfg_call_char_idx = 0

# Scroll / idle-display state
current_freq    = ""          # last frequency seen from rtl_tcp
_scroll_offset  = 0
_scroll_state   = "pause_start"   # pause_start | scrolling | pause_end
_scroll_pause   = 30
_status_cache   = {"val": "", "mode": "", "ts": 0.0}

AUTORX_CFG_ITEMS = ["Latitude", "Longitude", "Altitude", "Callsign", "< Back"]
CALL_CHARS = (list("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/") +
              ["<DEL>", "<OK>"])

BRIGHTNESS_FILE = "/etc/button_rtl_brightness"
BRIGHTNESS_LEVELS = [0x10, 0x40, 0x8F, 0xCF, 0xFF]

def _load_brightness():
    try:
        with open(BRIGHTNESS_FILE) as f:
            v = int(f.read().strip())
            return max(0, min(4, v))
    except:
        return 2

def _save_brightness(level):
    try:
        with open(BRIGHTNESS_FILE, "w") as f:
            f.write(str(level))
    except:
        pass

oled_brightness = _load_brightness()

MENU_ITEMS_IDLE = ["SDR Mode", "AutoRX Cfg", "AP Mode", "WiFi Mode", "Brightness", "Power Off", "< Back"]
MENU_ITEMS_AP   = ["SDR Mode", "AutoRX Cfg", "Stop AP", "WiFi Mode", "Brightness", "Power Off", "< Back"]
MENU_ITEMS = MENU_ITEMS_IDLE
WIFI_ITEMS  = ["Last Network", "Scan Networks", "< Back"]

# ── Display ───────────────────────────────────────────────
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)

def cmd(bus, *commands):
    msg = i2c_msg.write(ADDR, [0x00] + list(commands))
    bus.i2c_rdwr(msg)

def init_display(bus):
    for c in [0xAE,0xD5,0x80,0xA8,0x1F,0xD3,0x00,0x40,
              0x8D,0x14,0x20,0x00,0xA1,0xC8,0xDA,0x02,
              0x81,0xCF,0xD9,0xF1,0xDB,0x40,0xA4,0xA6,0xAF]:
        cmd(bus, c)

def set_contrast(bus, level):
    cmd(bus, 0x81, BRIGHTNESS_LEVELS[level])

def display_image(bus, image):
    img = image.convert('1')
    pixels = list(img.getdata())
    cmd(bus, 0x21, 0, 127)
    cmd(bus, 0x22, 0, 3)
    buf = []
    for page in range(4):
        for col in range(128):
            byte = 0
            for bit in range(8):
                row = page * 8 + bit
                if row < 32 and pixels[row * 128 + col] != 0:
                    byte |= (1 << bit)
            buf.append(byte)
    for i in range(0, len(buf), 16):
        msg = i2c_msg.write(ADDR, [0x40] + buf[i:i+16])
        bus.i2c_rdwr(msg)

def get_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return f"{int(f.read())//1000}C"
    except:
        return ""

def show(bus, line1, line2="", temp_right=False, line2_right=""):
    with display_lock:
        img = Image.new('1', (128, 32), 0)
        d = ImageDraw.Draw(img)
        d.text((0, 1),  line1, font=font, fill=1)
        d.text((0, 17), line2, font=font, fill=1)
        if temp_right:
            temp = get_cpu_temp()
            tw = int(d.textlength(temp, font=font))
            d.text((127 - tw, 1), temp, font=font, fill=1)
        if line2_right:
            rw = int(d.textlength(line2_right, font=font))
            d.text((127 - rw, 17), line2_right, font=font, fill=1)
        display_image(bus, img)

def show_menu(bus, title, items, idx):
    with display_lock:
        img = Image.new('1', (128, 32), 0)
        d = ImageDraw.Draw(img)
        d.text((0, 1), title, font=font, fill=1)
        d.text((0, 17), "> " + items[idx], font=font, fill=1)
        display_image(bus, img)

# ── Network ───────────────────────────────────────────────
def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "No IP"

def current_ip():
    return "192.168.100.1" if ap_running else get_ip()

def get_rssi():
    try:
        r = subprocess.run(["iw", "dev", "wlan0", "link"],
                           capture_output=True, text=True)
        for line in r.stdout.splitlines():
            if "signal:" in line:
                return line.strip().split()[1] + "dBm"
    except:
        pass
    return ""

def get_ap_clients():
    try:
        r = subprocess.run(["iw", "dev", "wlan0", "station", "dump"],
                           capture_output=True, text=True)
        count = r.stdout.count("Station ")
        return f"{count}cli"
    except:
        return ""

def get_sdr_menu():
    """Returns [(label, mode_id)] for installed SDR modes."""
    modes = []
    def add(label, mode_id):
        marker = "*" if mode_id == current_sdr_mode else " "
        modes.append((marker + label, mode_id))
    add("RTL-TCP", "rtltcp")
    if os.path.exists("/home/orangepi/radiosonde_auto_rx/auto_rx/auto_rx.py") \
            or os.path.exists("/lib/systemd/system/auto-rx.service"):
        add("AutoRX", "autorx")
    if shutil.which("readsb"):
        add("ADS-B", "adsb")
    if shutil.which("rtl_433"):
        add("RTL-433", "rtl433")
    if shutil.which("AIS-catcher"):
        add("AIS", "ais")
    add("SDR Off", "off")
    modes.append(("< Back", "back"))
    return modes

def stop_all_sdr():
    global rtl_process, rtl_active
    rtl_active = False
    subprocess.call(["pkill", "-f", "rtl_tcp"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rtl_process = None
    for svc in ["readsb", "tar1090", "auto-rx", "rtl_433", "ais_catcher"]:
        subprocess.call(["systemctl", "stop", svc],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)

def start_sdr(mode):
    global rtl_process, rtl_active, current_sdr_mode
    stop_all_sdr()
    current_sdr_mode = mode
    if mode == "rtltcp":
        rtl_active = True
        rtl_process = subprocess.Popen(
            ["stdbuf", "-oL", "rtl_tcp", "-a", "0.0.0.0", "-p", "1234"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        threading.Thread(target=read_rtl, args=(rtl_process, bus), daemon=True).start()
    elif mode == "adsb":
        subprocess.call(["systemctl", "start", "readsb"])
        subprocess.call(["systemctl", "start", "tar1090"])
    elif mode == "autorx":
        subprocess.call(["systemctl", "start", "auto-rx"])
    elif mode == "rtl433":
        subprocess.call(["systemctl", "start", "rtl_433"])
    elif mode == "ais":
        subprocess.call(["systemctl", "start", "ais_catcher"])
    # "off" → stop only (already done above)

def get_adsb_count():
    try:
        with open("/run/readsb/aircraft.json") as f:
            data = json.load(f)
        count = sum(1 for a in data.get("aircraft", [])
                    if a.get("seen", 999) < 60)
        return f"AC:{count}"
    except:
        return "AC:?"

def get_autorx_status():
    try:
        r = subprocess.run(
            ["curl", "-s", "--max-time", "1",
             "http://localhost:5000/get_telemetry"],
            capture_output=True, text=True)
        data = json.loads(r.stdout)
        count = len(data) if isinstance(data, list) else len(data.keys())
        return f"Sonde:{count}" if count else "Scanning..."
    except:
        return "Scanning..."

def get_ais_status():
    """Return number of vessels seen from AIS-catcher HTTP API."""
    try:
        r = subprocess.run(
            ["curl", "-s", "--max-time", "1",
             "http://localhost:8424/ships.json"],
            capture_output=True, text=True)
        data = json.loads(r.stdout)
        count = len(data.get("vessels", data if isinstance(data, list) else []))
        return f"Ships:{count}"
    except:
        return "Listening..."

RTL433_LOG = "/var/log/rtl_433/events.json"

def get_rtl433_status():
    """Return last decoded device model from JSON log."""
    try:
        with open(RTL433_LOG, "rb") as f:
            # Read last non-empty line efficiently
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return "Listening..."
            buf = b""
            pos = size - 1
            while pos >= 0:
                f.seek(pos)
                ch = f.read(1)
                if ch == b"\n" and buf.strip():
                    break
                buf = ch + buf
                pos -= 1
            line = buf.strip().decode(errors="ignore")
            if not line:
                return "Listening..."
            data = json.loads(line)
            model = data.get("model", "")
            return model[:12] if model else "Listening..."
    except:
        return "Listening..."

AUTORX_CFG_FILE = "/home/orangepi/radiosonde_auto_rx/auto_rx/station.cfg"

def read_autorx_config():
    lat, lon, alt, cs = 32.0853, 34.7818, 30, "STATION"
    try:
        with open(AUTORX_CFG_FILE) as f:
            for line in f:
                line = line.strip()
                if line.startswith('station_lat') and '=' in line:
                    lat = float(line.split('=')[1].strip())
                elif line.startswith('station_lon') and '=' in line:
                    lon = float(line.split('=')[1].strip())
                elif line.startswith('station_alt') and '=' in line:
                    alt = int(float(line.split('=')[1].strip()))
                elif line.startswith('station_callsign') and '=' in line:
                    cs = line.split('=')[1].strip().split('#')[0].strip()
    except:
        pass
    return lat, lon, alt, cs

def save_autorx_field(field, value):
    try:
        with open(AUTORX_CFG_FILE, 'r') as f:
            content = f.read()
        content = re.sub(rf'(?m)^{field}\s*=.*', f'{field} = {value}', content)
        with open(AUTORX_CFG_FILE, 'w') as f:
            f.write(content)
        return True
    except:
        return False

def float_to_lat_chars(lat):
    sign = '+' if lat >= 0 else '-'
    lat = min(abs(lat), 90.9999)
    i = int(lat); d = round((lat - i) * 10000)
    if d >= 10000: i += 1; d = 0
    return ([sign, str(i//10%10), str(i%10), '.',
             str(d//1000), str(d//100%10), str(d//10%10), str(d%10)],
            [0, 1, 2, 4, 5, 6, 7])

def lat_chars_to_float(c):
    s = 1 if c[0]=='+' else -1
    return round(s*(int(c[1])*10+int(c[2])+int(c[4])*.1+int(c[5])*.01+int(c[6])*.001+int(c[7])*.0001), 4)

def float_to_lon_chars(lon):
    sign = '+' if lon >= 0 else '-'
    lon = min(abs(lon), 180.9999)
    i = int(lon); d = round((lon - i) * 10000)
    if d >= 10000: i += 1; d = 0
    return ([sign, str(i//100), str(i//10%10), str(i%10), '.',
             str(d//1000), str(d//100%10), str(d//10%10), str(d%10)],
            [0, 1, 2, 3, 5, 6, 7, 8])

def lon_chars_to_float(c):
    s = 1 if c[0]=='+' else -1
    return round(s*(int(c[1])*100+int(c[2])*10+int(c[3])+int(c[5])*.1+int(c[6])*.01+int(c[7])*.001+int(c[8])*.0001), 4)

def int_to_alt_chars(alt):
    alt = max(0, min(9999, int(alt)))
    return ([str(alt//1000), str(alt//100%10), str(alt//10%10), str(alt%10)],
            [0, 1, 2, 3])

def alt_chars_to_int(c):
    return int(c[0])*1000 + int(c[1])*100 + int(c[2])*10 + int(c[3])

def _show_coord_edit():
    labels = {"lat": "Lat", "lon": "Lon", "alt": "Alt"}
    lbl = labels.get(cfg_edit_field, "")
    val = "".join(cfg_edit_chars)
    char_idx = cfg_edit_editable[cfg_edit_cursor]
    with display_lock:
        img = Image.new('1', (128, 32), 0)
        d = ImageDraw.Draw(img)
        full_lbl = lbl + ":"
        d.text((0, 1), full_lbl + val, font=font, fill=1)
        lw = int(d.textlength(full_lbl, font=font))
        bw = int(d.textlength("".join(cfg_edit_chars[:char_idx]), font=font))
        cw = max(int(d.textlength(cfg_edit_chars[char_idx], font=font)), 5)
        x = lw + bw
        d.rectangle([x, 14, x + cw, 15], fill=1)
        d.text((0, 17), "^v:val  <>:pos  OK", font=font, fill=1)
        display_image(bus, img)

def _show_call_edit():
    disp = "".join(cfg_call_chars[-10:]) if len(cfg_call_chars) > 10 else "".join(cfg_call_chars)
    show(bus, "Callsign:", disp + "[" + CALL_CHARS[cfg_call_char_idx] + "]")

def _coord_up():
    global cfg_edit_chars
    ci = cfg_edit_editable[cfg_edit_cursor]
    if ci == 0 and cfg_edit_field in ("lat", "lon"):
        cfg_edit_chars[0] = '-' if cfg_edit_chars[0] == '+' else '+'
    else:
        cfg_edit_chars[ci] = str((int(cfg_edit_chars[ci]) + 1) % 10)

def _coord_down():
    global cfg_edit_chars
    ci = cfg_edit_editable[cfg_edit_cursor]
    if ci == 0 and cfg_edit_field in ("lat", "lon"):
        cfg_edit_chars[0] = '-' if cfg_edit_chars[0] == '+' else '+'
    else:
        cfg_edit_chars[ci] = str((int(cfg_edit_chars[ci]) - 1) % 10)

def _coord_save():
    if cfg_edit_field == "lat":
        save_autorx_field("station_lat", f"{lat_chars_to_float(cfg_edit_chars):.4f}")
    elif cfg_edit_field == "lon":
        save_autorx_field("station_lon", f"{lon_chars_to_float(cfg_edit_chars):.4f}")
    elif cfg_edit_field == "alt":
        save_autorx_field("station_alt", str(alt_chars_to_int(cfg_edit_chars)))

def get_last_wifi():
    r = subprocess.run(["nmcli","-t","-f","NAME,TYPE","con","show"],
                       capture_output=True, text=True)
    for line in r.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 2 and parts[1] == "wifi" and parts[0] != "Hotspot":
            return parts[0]
    return None

def scan_wifi():
    subprocess.run(["nmcli","dev","wifi","rescan"], capture_output=True, timeout=10)
    r = subprocess.run(["nmcli","-t","-f","SSID,SIGNAL","dev","wifi","list"],
                       capture_output=True, text=True)
    seen, nets = set(), []
    for line in r.stdout.splitlines():
        parts = line.split(":")
        ssid = parts[0].strip()
        if ssid and ssid not in seen:
            seen.add(ssid)
            nets.append(ssid)
    return nets[:8]

def connect_known(ssid):
    r = subprocess.run(["nmcli","con","up", ssid],
                       capture_output=True, text=True, timeout=20)
    return r.returncode == 0

def connect_new(ssid, pwd):
    # remove old profile if exists
    subprocess.run(["nmcli","con","delete", ssid], capture_output=True)
    r = subprocess.run(["nmcli","dev","wifi","connect", ssid,"password", pwd],
                       capture_output=True, text=True, timeout=30)
    return r.returncode == 0

def start_ap():
    r = subprocess.run(["bash", "/usr/local/bin/start_ap.sh"],
                       capture_output=True, timeout=15)
    return r.returncode == 0

def stop_ap():
    subprocess.run(["bash", "/usr/local/bin/stop_ap.sh"],
                   capture_output=True, timeout=20)

# ── RTL helpers ───────────────────────────────────────────
def format_freq(hz):
    if hz >= 1_000_000_000: return f"{hz/1e9:.3f}GHz"
    if hz >= 1_000_000:     return f"{hz/1e6:.3f}MHz"
    if hz >= 1_000:         return f"{hz/1e3:.1f}kHz"
    return f"{hz}Hz"

def read_rtl(proc, bus_ref):
    global rtl_active, current_freq
    for line in proc.stdout:
        if not rtl_active:
            break
        line = line.decode(errors='ignore').strip()
        if "set freq" in line:
            try:
                current_freq = format_freq(int(line.split()[-1]))
            except:
                pass

# ── Button helpers ────────────────────────────────────────
def wait_release(pin):
    while GPIO.input(pin) == GPIO.LOW:
        time.sleep(0.02)

def is_long_press(pin, threshold=1.0):
    t = time.time()
    while GPIO.input(pin) == GPIO.LOW:
        if time.time() - t > threshold:
            wait_release(pin)
            return True
        time.sleep(0.02)
    return False

# ── GPIO setup ────────────────────────────────────────────
GPIO.setwarnings(False)
GPIO.setmode(GPIO.SUNXI)
for pin in [BTN_UP, BTN_DOWN, BTN_SEL, BTN_BACK, BTN_RIGHT]:
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

bus = SMBus(BUS)
init_display(bus)
set_contrast(bus, oled_brightness)

def _make_scroll_text():
    """Build the address string for line 1 (IP + port/path)."""
    ip = current_ip()
    if current_sdr_mode == "rtltcp":
        return f"{ip}:1234"
    elif current_sdr_mode == "autorx":
        return f"{ip}:5000"
    elif current_sdr_mode == "adsb":
        return f"{ip}/tar1090"
    elif current_sdr_mode == "rtl433":
        return f"{ip}:8433"
    elif current_sdr_mode == "ais":
        return f"{ip}:8424"
    else:
        return ip

def _get_line2_status():
    """Return right-side annotation for line 2, cached 5s."""
    now = time.time()
    if now - _status_cache["ts"] > 5 or _status_cache["mode"] != current_sdr_mode:
        if current_sdr_mode == "rtltcp":
            _status_cache["val"] = "" if ap_running else get_rssi()
        elif current_sdr_mode == "adsb":
            _status_cache["val"] = get_adsb_count()
        elif current_sdr_mode == "autorx":
            _status_cache["val"] = get_autorx_status()
        elif current_sdr_mode == "rtl433":
            _status_cache["val"] = get_rtl433_status()
        elif current_sdr_mode == "ais":
            _status_cache["val"] = get_ais_status()
        else:
            _status_cache["val"] = ""
        _status_cache["mode"] = current_sdr_mode
        _status_cache["ts"]   = now
    return _status_cache["val"]

def _draw_idle_frame(bus_ref, scroll_px=0):
    """Draw one idle screen frame with scroll_px offset on line 1."""
    with display_lock:
        img = Image.new('1', (128, 32), 0)
        d   = ImageDraw.Draw(img)

        # ── Line 1: scrolling address ──────────────────────
        addr = _make_scroll_text()
        d.text((-scroll_px, 1), addr, font=font, fill=1)

        # Temp fixed on the right — black backdrop clears scroll text behind it
        temp = get_cpu_temp()
        tw   = int(d.textlength(temp, font=font))
        d.rectangle([128 - tw - 2, 0, 127, 15], fill=0)
        d.text((128 - tw, 1), temp, font=font, fill=1)

        # ── Line 2: mode status ────────────────────────────
        pfx = "AP " if ap_running else ""
        if current_sdr_mode == "rtltcp":
            rtl_on = rtl_process is not None and rtl_process.poll() is None
            if rtl_on and current_freq:
                line2 = pfx + current_freq
            else:
                line2 = pfx + ("RTL:ON" if rtl_on else "RTL:OFF")
        elif current_sdr_mode == "adsb":
            line2 = pfx + "ADS-B ON"
        elif current_sdr_mode == "autorx":
            line2 = pfx + "AutoRX ON"
        elif current_sdr_mode == "rtl433":
            line2 = pfx + "RTL-433 ON"
        elif current_sdr_mode == "ais":
            line2 = pfx + "AIS ON  "
        else:
            line2 = pfx + "SDR: OFF"

        r2 = _get_line2_status()
        d.text((0, 17), line2, font=font, fill=1)
        if r2:
            rw = int(d.textlength(r2, font=font))
            d.text((127 - rw, 17), r2, font=font, fill=1)

        display_image(bus_ref, img)

def refresh_idle():
    """Reset scroll to start and draw immediately."""
    global _scroll_offset, _scroll_state, _scroll_pause
    _scroll_offset = 0
    _scroll_state  = "pause_start"
    _scroll_pause  = 30
    _draw_idle_frame(bus, 0)

def _idle_scroll_thread(bus_ref):
    """Continuously update idle screen with marquee scroll on line 1."""
    global _scroll_offset, _scroll_state, _scroll_pause
    PAUSE = 30  # frames at each end (~2.4s at 12fps)

    while True:
        if state != "idle":
            time.sleep(0.2)
            _scroll_offset = 0
            _scroll_state  = "pause_start"
            _scroll_pause  = PAUSE
            continue

        # Measure how far to scroll
        addr   = _make_scroll_text()
        dummy  = Image.new('1', (1, 1), 0)
        dd     = ImageDraw.Draw(dummy)
        full_w = int(dd.textlength(addr, font=font))
        avail  = 90          # pixels before temp
        max_off = max(0, full_w - avail)

        _draw_idle_frame(bus_ref, _scroll_offset)

        if max_off == 0:
            time.sleep(0.5)   # no scroll needed — just keep refreshing temp
            continue

        # State machine
        if _scroll_state == "pause_start":
            _scroll_pause -= 1
            if _scroll_pause <= 0:
                _scroll_state = "scrolling"
        elif _scroll_state == "scrolling":
            _scroll_offset += 1
            if _scroll_offset >= max_off:
                _scroll_offset = max_off
                _scroll_state  = "pause_end"
                _scroll_pause  = PAUSE
        elif _scroll_state == "pause_end":
            _scroll_pause -= 1
            if _scroll_pause <= 0:
                _scroll_offset = 0
                _scroll_state  = "pause_start"
                _scroll_pause  = PAUSE

        time.sleep(0.08)

refresh_idle()

# ── Auto-start RTL-TCP on boot ────────────────────────────
start_sdr("rtltcp")
refresh_idle()
threading.Thread(target=_idle_scroll_thread, args=(bus,), daemon=True).start()


def _show_brightness():
    bar = "█" * (oled_brightness + 1) + "░" * (4 - oled_brightness)
    show(bus, "Brightness", f"{bar}  {oled_brightness+1}/5")

print("Ready.")

last_up_press = 0

while True:
    # ── IDLE ──────────────────────────────────────────────
    if state == "idle":
        if GPIO.input(BTN_UP) == GPIO.LOW:
            now = time.time()
            if now - last_up_press > 0.5:
                last_up_press = now
                time.sleep(0.05)
                if GPIO.input(BTN_UP) == GPIO.LOW:
                    if rtl_process is None or rtl_process.poll() is not None:
                        subprocess.call(["pkill","-f","rtl_tcp"])
                        time.sleep(0.3)
                        rtl_active = True
                        rtl_process = subprocess.Popen(
                            ["stdbuf","-oL","rtl_tcp","-a","0.0.0.0","-p","1234"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                        threading.Thread(target=read_rtl,
                                         args=(rtl_process, bus), daemon=True).start()
                        show(bus, current_ip(), "RTL: ON", temp_right=True)
                    else:
                        rtl_active = False
                        subprocess.call(["pkill","-f","rtl_tcp"])
                        rtl_process = None
                        time.sleep(0.3)
                        show(bus, current_ip(), "RTL: OFF", temp_right=True)
                    wait_release(BTN_UP)

        elif GPIO.input(BTN_RIGHT) == GPIO.LOW and current_sdr_mode == "rtltcp":
            now = time.time()
            if now - last_up_press > 0.5:
                last_up_press = now
                time.sleep(0.05)
                if GPIO.input(BTN_RIGHT) == GPIO.LOW:
                    if rtl_process is None or rtl_process.poll() is not None:
                        start_sdr("rtltcp")
                        show(bus, current_ip(), "RTL: ON", temp_right=True)
                    else:
                        stop_all_sdr()
                        show(bus, current_ip(), "RTL: OFF", temp_right=True)
                    wait_release(BTN_RIGHT)

        if GPIO.input(BTN_SEL) == GPIO.LOW:
            if is_long_press(BTN_SEL, 1.0):
                state = "menu"
                menu_idx = 0
                show_menu(bus, "-- MENU --", MENU_ITEMS, menu_idx)

    # ── MAIN MENU ─────────────────────────────────────────
    elif state == "menu":
        if GPIO.input(BTN_UP) == GPIO.LOW:
            time.sleep(0.05)
            menu_idx = (menu_idx - 1) % len(MENU_ITEMS)
            show_menu(bus, "-- MENU --", MENU_ITEMS, menu_idx)
            wait_release(BTN_UP)

        elif GPIO.input(BTN_DOWN) == GPIO.LOW:
            time.sleep(0.05)
            menu_idx = (menu_idx + 1) % len(MENU_ITEMS)
            show_menu(bus, "-- MENU --", MENU_ITEMS, menu_idx)
            wait_release(BTN_DOWN)

        elif GPIO.input(BTN_BACK) == GPIO.LOW:
            time.sleep(0.05)
            state = "idle"
            refresh_idle()
            wait_release(BTN_BACK)

        elif GPIO.input(BTN_SEL) == GPIO.LOW:
            wait_release(BTN_SEL)
            choice = MENU_ITEMS[menu_idx]
            if choice == "AutoRX Cfg":
                if not os.path.exists(AUTORX_CFG_FILE):
                    show(bus, "AutoRX", "not installed")
                    time.sleep(2)
                    show_menu(bus, "-- MENU --", MENU_ITEMS, menu_idx)
                else:
                    cfg_menu_idx = 0
                    show_menu(bus, "AutoRX Cfg", AUTORX_CFG_ITEMS, cfg_menu_idx)
                    state = "autorx_config"
            elif choice == "SDR Mode":
                sdr_data = get_sdr_menu()
                sdr_idx  = 0
                show_menu(bus, "-- SDR Mode --",
                          [l for l, _ in sdr_data], sdr_idx)
                state = "sdr_menu"
            elif choice == "AP Mode":
                show(bus, "Starting AP...", "5GHz open")
                if start_ap():
                    ap_running = True
                    MENU_ITEMS[:] = MENU_ITEMS_AP
                    state = "idle"
                    refresh_idle()
                else:
                    show(bus, "AP Failed!", "")
                    time.sleep(2)
                    state = "idle"
                    refresh_idle()
            elif choice == "Stop AP":
                show(bus, "Stopping AP...", "")
                stop_ap()
                ap_running = False
                MENU_ITEMS[:] = MENU_ITEMS_IDLE
                state = "idle"
                time.sleep(2)
                refresh_idle()
            elif choice == "WiFi Mode":
                state = "wifi_menu"
                menu_idx = 0
                show_menu(bus, "-- WiFi --", WIFI_ITEMS, menu_idx)
            elif choice == "Brightness":
                state = "brightness"
                _show_brightness()
            elif choice == "Power Off":
                show(bus, "Shutting down...", "")
                time.sleep(2)
                subprocess.call(["sudo", "poweroff"])
            elif choice == "< Back":
                state = "idle"
                refresh_idle()

    # ── AUTORX CONFIG MENU ────────────────────────────────
    elif state == "autorx_config":
        if GPIO.input(BTN_UP) == GPIO.LOW:
            time.sleep(0.05)
            cfg_menu_idx = (cfg_menu_idx - 1) % len(AUTORX_CFG_ITEMS)
            show_menu(bus, "AutoRX Cfg", AUTORX_CFG_ITEMS, cfg_menu_idx)
            wait_release(BTN_UP)
        elif GPIO.input(BTN_DOWN) == GPIO.LOW:
            time.sleep(0.05)
            cfg_menu_idx = (cfg_menu_idx + 1) % len(AUTORX_CFG_ITEMS)
            show_menu(bus, "AutoRX Cfg", AUTORX_CFG_ITEMS, cfg_menu_idx)
            wait_release(BTN_DOWN)
        elif GPIO.input(BTN_BACK) == GPIO.LOW:
            time.sleep(0.05)
            state = "menu"; menu_idx = 0
            show_menu(bus, "-- MENU --", MENU_ITEMS, menu_idx)
            wait_release(BTN_BACK)
        elif GPIO.input(BTN_SEL) == GPIO.LOW:
            wait_release(BTN_SEL)
            choice = AUTORX_CFG_ITEMS[cfg_menu_idx]
            lat, lon, alt, cs = read_autorx_config()
            if choice == "Latitude":
                cfg_edit_field = "lat"
                cfg_edit_chars[:], cfg_edit_editable[:] = float_to_lat_chars(lat)
                cfg_edit_cursor = 0; state = "autorx_edit_coord"
                _show_coord_edit()
            elif choice == "Longitude":
                cfg_edit_field = "lon"
                cfg_edit_chars[:], cfg_edit_editable[:] = float_to_lon_chars(lon)
                cfg_edit_cursor = 0; state = "autorx_edit_coord"
                _show_coord_edit()
            elif choice == "Altitude":
                cfg_edit_field = "alt"
                cfg_edit_chars[:], cfg_edit_editable[:] = int_to_alt_chars(alt)
                cfg_edit_cursor = 0; state = "autorx_edit_coord"
                _show_coord_edit()
            elif choice == "Callsign":
                cfg_call_chars[:] = list(cs.upper()[:8])
                cfg_call_char_idx = 0; state = "autorx_edit_call"
                _show_call_edit()
            elif choice == "< Back":
                state = "menu"; menu_idx = 0
                show_menu(bus, "-- MENU --", MENU_ITEMS, menu_idx)

    # ── AUTORX COORD EDIT ─────────────────────────────────
    elif state == "autorx_edit_coord":
        if GPIO.input(BTN_UP) == GPIO.LOW:
            time.sleep(0.04)
            _coord_up(); _show_coord_edit()
            wait_release(BTN_UP)
        elif GPIO.input(BTN_DOWN) == GPIO.LOW:
            time.sleep(0.04)
            _coord_down(); _show_coord_edit()
            wait_release(BTN_DOWN)
        elif GPIO.input(BTN_RIGHT) == GPIO.LOW:
            time.sleep(0.04)
            cfg_edit_cursor = (cfg_edit_cursor + 1) % len(cfg_edit_editable)
            _show_coord_edit(); wait_release(BTN_RIGHT)
        elif GPIO.input(BTN_BACK) == GPIO.LOW:
            time.sleep(0.04)
            if cfg_edit_cursor > 0:
                cfg_edit_cursor -= 1
                _show_coord_edit(); wait_release(BTN_BACK)
            else:
                wait_release(BTN_BACK)
                state = "autorx_config"
                show_menu(bus, "AutoRX Cfg", AUTORX_CFG_ITEMS, cfg_menu_idx)
        elif GPIO.input(BTN_SEL) == GPIO.LOW:
            wait_release(BTN_SEL)
            _coord_save()
            show(bus, "Saved!", "".join(cfg_edit_chars))
            time.sleep(1)
            state = "autorx_config"
            show_menu(bus, "AutoRX Cfg", AUTORX_CFG_ITEMS, cfg_menu_idx)

    # ── AUTORX CALLSIGN EDIT ──────────────────────────────
    elif state == "autorx_edit_call":
        if GPIO.input(BTN_UP) == GPIO.LOW:
            time.sleep(0.04)
            cfg_call_char_idx = (cfg_call_char_idx - 1) % len(CALL_CHARS)
            _show_call_edit(); wait_release(BTN_UP)
        elif GPIO.input(BTN_DOWN) == GPIO.LOW:
            time.sleep(0.04)
            cfg_call_char_idx = (cfg_call_char_idx + 1) % len(CALL_CHARS)
            _show_call_edit(); wait_release(BTN_DOWN)
        elif GPIO.input(BTN_BACK) == GPIO.LOW:
            time.sleep(0.05)
            if cfg_call_chars:
                cfg_call_chars.pop()
                _show_call_edit()
            else:
                state = "autorx_config"
                show_menu(bus, "AutoRX Cfg", AUTORX_CFG_ITEMS, cfg_menu_idx)
            wait_release(BTN_BACK)
        elif GPIO.input(BTN_SEL) == GPIO.LOW:
            wait_release(BTN_SEL)
            ch = CALL_CHARS[cfg_call_char_idx]
            if ch == "<DEL>":
                if cfg_call_chars: cfg_call_chars.pop()
                _show_call_edit()
            elif ch == "<OK>":
                if cfg_call_chars:
                    save_autorx_field("station_callsign",
                                      "".join(cfg_call_chars))
                    show(bus, "Saved!", "".join(cfg_call_chars))
                    time.sleep(1)
                    state = "autorx_config"
                    show_menu(bus, "AutoRX Cfg", AUTORX_CFG_ITEMS, cfg_menu_idx)
            else:
                if len(cfg_call_chars) < 8:
                    cfg_call_chars.append(ch)
                _show_call_edit()

    # ── SDR MENU ──────────────────────────────────────────
    elif state == "sdr_menu":
        labels = [l for l, _ in sdr_data]
        if GPIO.input(BTN_UP) == GPIO.LOW:
            time.sleep(0.05)
            sdr_idx = (sdr_idx - 1) % len(labels)
            show_menu(bus, "-- SDR Mode --", labels, sdr_idx)
            wait_release(BTN_UP)

        elif GPIO.input(BTN_DOWN) == GPIO.LOW:
            time.sleep(0.05)
            sdr_idx = (sdr_idx + 1) % len(labels)
            show_menu(bus, "-- SDR Mode --", labels, sdr_idx)
            wait_release(BTN_DOWN)

        elif GPIO.input(BTN_BACK) == GPIO.LOW:
            time.sleep(0.05)
            state = "menu"
            menu_idx = 0
            show_menu(bus, "-- MENU --", MENU_ITEMS, menu_idx)
            wait_release(BTN_BACK)

        elif GPIO.input(BTN_SEL) == GPIO.LOW:
            wait_release(BTN_SEL)
            _, mode_id = sdr_data[sdr_idx]
            if mode_id == "back":
                state = "menu"
                menu_idx = 0
                show_menu(bus, "-- MENU --", MENU_ITEMS, menu_idx)
            else:
                show(bus, "Switching...", labels[sdr_idx].strip())
                start_sdr(mode_id)
                state = "idle"
                refresh_idle()

    # ── WIFI MENU ─────────────────────────────────────────
    elif state == "wifi_menu":
        if GPIO.input(BTN_UP) == GPIO.LOW:
            time.sleep(0.05)
            menu_idx = (menu_idx - 1) % len(WIFI_ITEMS)
            show_menu(bus, "-- WiFi --", WIFI_ITEMS, menu_idx)
            wait_release(BTN_UP)

        elif GPIO.input(BTN_DOWN) == GPIO.LOW:
            time.sleep(0.05)
            menu_idx = (menu_idx + 1) % len(WIFI_ITEMS)
            show_menu(bus, "-- WiFi --", WIFI_ITEMS, menu_idx)
            wait_release(BTN_DOWN)

        elif GPIO.input(BTN_BACK) == GPIO.LOW:
            time.sleep(0.05)
            state = "menu"
            menu_idx = 0
            show_menu(bus, "-- MENU --", MENU_ITEMS, menu_idx)
            wait_release(BTN_BACK)

        elif GPIO.input(BTN_SEL) == GPIO.LOW:
            wait_release(BTN_SEL)
            choice = WIFI_ITEMS[menu_idx]
            if choice == "Last Network":
                last = get_last_wifi()
                if last:
                    show(bus, "Connecting...", last[:18])
                    if connect_known(last):
                        time.sleep(1)
                        state = "idle"
                        refresh_idle()
                    else:
                        show(bus, "Failed!", "")
                        time.sleep(2)
                        state = "idle"
                        refresh_idle()
                else:
                    show(bus, "No saved net", "")
                    time.sleep(2)
            elif choice == "Scan Networks":
                show(bus, "Scanning...", "")
                wifi_list = scan_wifi()
                if wifi_list:
                    state = "wifi_list"
                    wifi_idx = 0
                    show_menu(bus, "-- Networks --", wifi_list, wifi_idx)
                else:
                    show(bus, "None found", "")
                    time.sleep(2)
            elif choice == "< Back":
                state = "menu"
                menu_idx = 0
                show_menu(bus, "-- MENU --", MENU_ITEMS, menu_idx)

    # ── BRIGHTNESS ────────────────────────────────────────
    elif state == "brightness":
        if GPIO.input(BTN_UP) == GPIO.LOW:
            time.sleep(0.05)
            if oled_brightness < 4:
                oled_brightness += 1
                set_contrast(bus, oled_brightness)
                _save_brightness(oled_brightness)
            _show_brightness()
            wait_release(BTN_UP)

        elif GPIO.input(BTN_DOWN) == GPIO.LOW:
            time.sleep(0.05)
            if oled_brightness > 0:
                oled_brightness -= 1
                set_contrast(bus, oled_brightness)
                _save_brightness(oled_brightness)
            _show_brightness()
            wait_release(BTN_DOWN)

        elif GPIO.input(BTN_SEL) == GPIO.LOW:
            time.sleep(0.05)
            state = "menu"
            menu_idx = 0
            show_menu(bus, "-- MENU --", MENU_ITEMS, menu_idx)
            wait_release(BTN_SEL)

    # ── WIFI LIST ─────────────────────────────────────────
    elif state == "wifi_list":
        if GPIO.input(BTN_UP) == GPIO.LOW:
            time.sleep(0.05)
            wifi_idx = (wifi_idx - 1) % len(wifi_list)
            show_menu(bus, "-- Networks --", wifi_list, wifi_idx)
            wait_release(BTN_UP)

        elif GPIO.input(BTN_DOWN) == GPIO.LOW:
            time.sleep(0.05)
            wifi_idx = (wifi_idx + 1) % len(wifi_list)
            show_menu(bus, "-- Networks --", wifi_list, wifi_idx)
            wait_release(BTN_DOWN)

        elif GPIO.input(BTN_BACK) == GPIO.LOW:
            time.sleep(0.05)
            state = "wifi_menu"
            menu_idx = 0
            show_menu(bus, "-- WiFi --", WIFI_ITEMS, menu_idx)
            wait_release(BTN_BACK)

        elif GPIO.input(BTN_SEL) == GPIO.LOW:
            wait_release(BTN_SEL)
            state = "wifi_password"
            password = ""
            char_idx = 0
            show(bus, wifi_list[wifi_idx][:18], "Pwd:[" + CHARS[char_idx] + "]")

    # ── PASSWORD ENTRY ────────────────────────────────────
    elif state == "wifi_password":
        def _pwd_display():
            pwd_disp = password[-12:] if len(password) > 12 else password
            show(bus, wifi_list[wifi_idx][:18],
                 pwd_disp + "[" + CHARS[char_idx] + "]")

        if GPIO.input(BTN_UP) == GPIO.LOW:
            time.sleep(0.03)
            char_idx = (char_idx - 1) % len(CHARS)
            _pwd_display()
            wait_release(BTN_UP)

        elif GPIO.input(BTN_DOWN) == GPIO.LOW:
            time.sleep(0.03)
            char_idx = (char_idx + 1) % len(CHARS)
            _pwd_display()
            wait_release(BTN_DOWN)

        elif GPIO.input(BTN_BACK) == GPIO.LOW:
            time.sleep(0.05)
            if password:
                password = password[:-1]   # backspace
                _pwd_display()
            else:
                state = "wifi_list"        # exit to network list
                show_menu(bus, "-- Networks --", wifi_list, wifi_idx)
            wait_release(BTN_BACK)

        elif GPIO.input(BTN_SEL) == GPIO.LOW:
            if is_long_press(BTN_SEL, 1.0):
                # Long press = connect
                if len(password) >= 8:
                    show(bus, "Connecting...", wifi_list[wifi_idx][:18])
                    ok = connect_new(wifi_list[wifi_idx], password)
                    time.sleep(2)
                    state = "idle"
                    if ok:
                        refresh_idle()
                    else:
                        show(bus, "Failed!", "Check password")
                        time.sleep(2)
                        refresh_idle()
                else:
                    show(bus, f"Min 8 chars!", f"Got: {len(password)}")
                    time.sleep(2)
                    _pwd_display()
            else:
                c = CHARS[char_idx]
                if c == "<DEL>":
                    password = password[:-1]
                elif c == "<OK>":
                    if len(password) >= 8:
                        show(bus, "Connecting...", wifi_list[wifi_idx][:18])
                        ok = connect_new(wifi_list[wifi_idx], password)
                        time.sleep(2)
                        state = "idle"
                        if ok:
                            refresh_idle()
                        else:
                            show(bus, "Failed!", "Check password")
                            time.sleep(2)
                            refresh_idle()
                    else:
                        show(bus, f"Min 8 chars!", f"Got: {len(password)}")
                        time.sleep(2)
                        _pwd_display()
                else:
                    password += c
                    _pwd_display()
                wait_release(BTN_SEL)

    time.sleep(0.05)
