import os
import shutil
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

MENU_ITEMS_IDLE = ["SDR Mode", "AP Mode", "WiFi Mode", "Brightness", "Power Off", "< Back"]
MENU_ITEMS_AP   = ["SDR Mode", "Stop AP", "WiFi Mode", "Brightness", "Power Off", "< Back"]
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
    if shutil.which("readsb"):
        add("ADS-B", "adsb")
    if os.path.exists("/home/orangepi/radiosonde_auto_rx/auto_rx/auto_rx.py"):
        add("AutoRX", "autorx")
    if shutil.which("rtl_433"):
        add("RTL-433", "rtl433")
    if shutil.which("rtl_ais"):
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
    for svc in ["readsb", "tar1090", "auto-rx", "rtl_433", "rtl-ais"]:
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
        subprocess.call(["systemctl", "start", "rtl-ais"])
    # "off" → stop only (already done above)

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
    global rtl_active
    for line in proc.stdout:
        if not rtl_active:
            break
        line = line.decode(errors='ignore').strip()
        if "set freq" in line:
            try:
                hz = int(line.split()[-1])
                if rtl_active and state == "idle":
                    r2 = "" if ap_running else get_rssi()
                    show(bus_ref, current_ip(), format_freq(hz),
                         temp_right=True, line2_right=r2)
            except: pass

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

def refresh_idle():
    line1 = "192.168.100.1" if ap_running else get_ip()
    pfx   = "AP " if ap_running else ""
    if current_sdr_mode == "rtltcp":
        rtl_on    = rtl_process is not None and rtl_process.poll() is None
        line2     = pfx + ("RTL:ON" if rtl_on else "RTL:OFF")
        line2_right = "" if ap_running else get_rssi()
    elif current_sdr_mode == "adsb":
        line2 = pfx + "ADS-B ON";  line2_right = ""
    elif current_sdr_mode == "autorx":
        line2 = pfx + "AutoRX ON"; line2_right = ""
    elif current_sdr_mode == "rtl433":
        line2 = pfx + "RTL-433 ON"; line2_right = ""
    elif current_sdr_mode == "ais":
        line2 = pfx + "AIS ON";    line2_right = ""
    else:
        line2 = pfx + "SDR: OFF";  line2_right = ""
    show(bus, line1, line2, temp_right=True, line2_right=line2_right)

refresh_idle()

# ── Auto-start RTL-TCP on boot ────────────────────────────
start_sdr("rtltcp")
refresh_idle()


def _show_brightness():
    bar = "█" * (oled_brightness + 1) + "░" * (4 - oled_brightness)
    show(bus, "Brightness", f"{bar}  {oled_brightness+1}/5")

print("Ready.")

last_up_press = 0
last_temp_refresh = 0

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
            if choice == "SDR Mode":
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

    if state == "idle":
        now = time.time()
        if now - last_temp_refresh > 10:
            last_temp_refresh = now
            refresh_idle()

    time.sleep(0.05)
