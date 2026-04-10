# Installation Notes — Lessons Learned

Hard-won knowledge from building this system. Read this before installing on a fresh SD card.

---

## System

- **Board**: Orange Pi Zero 2W (Allwinner H618, arm64, 1GB RAM)
- **OS**: Orange Pi OS 1.0.2 Bookworm (Debian 12)
- **Kernel**: 6.1.31-sun50iw9

---

## GPIO

- Only `PI`-prefix pins work reliably with OPi.GPIO in SUNXI mode
- `PH`-prefix pins fail with `OSError: [Errno 22] Invalid argument` — do not use
- All joystick pins use `GPIO.PUD_UP` (active LOW)

---

## I2C / OLED

- OLED is on I2C bus **2** (not 1, not 3) at address `0x3C`
- Must enable I2C in `orangepi-config → System → Hardware → i2c1` before use
- Verify with: `i2cdetect -y 2` — should show `3c`
- The `i2c1` label in orangepi-config corresponds to `/dev/i2c-2` at runtime

---

## RTL-SDR

- Only one process can hold the dongle at a time
- `usb_claim_interface error -6` = another process has the device → `pkill -f rtl_tcp` or stop the service
- Always call `stop_all_sdr()` before starting any SDR mode
- `stdbuf -oL rtl_tcp ...` is needed to get line-buffered output for frequency parsing
- rtl_fm gain `40` works well; `0` = auto (sometimes unstable)

---

## ADS-B — readsb + tar1090

- **readsb is NOT in apt for arm64 Debian Bookworm** — must build from source
- Use wiedehopf's install script: `https://raw.githubusercontent.com/wiedehopf/adsb-scripts/master/readsb-install.sh`
- tar1090 is installed automatically by the readsb script
- Web UI is at `http://IP/tar1090` — port **80**, path `/tar1090` (NOT port 8080 or 8504)
- JSON output: `/run/readsb/aircraft.json` — field `seen` < 60 = recently active aircraft
- Config file: `/etc/default/readsb`
- After install: `sudo systemctl disable readsb tar1090` — managed by button_rtl.py

---

## AutoRX — Radiosonde Auto-RX v1.8.2

- Clone with: `git clone --depth=1 --branch v1.8.2 https://github.com/projecthorus/radiosonde_auto_rx.git`
- **Must run `bash build.sh`** inside `auto_rx/` — running `make` alone does NOT copy binaries into place
  - Without `bash build.sh` you get: `dft_detect does not exist` error at startup
- Service name is `auto-rx` (with dash, not underscore)
- Web UI at port **5000**
- Default station config is lat=0 lon=0 (middle of ocean) — must edit `station.cfg`
- Use `MemoryMax=256M` in the service to protect 1GB RAM
- Config file: `/home/orangepi/radiosonde_auto_rx/auto_rx/station.cfg`

---

## ACARS — acarsdec

- `acarsdec` is NOT in apt — must build from source
- Dependencies: `cmake build-essential librtlsdr-dev libusb-1.0-0-dev libjansson-dev libxml2-dev`
- Build: `cmake .. -DCMAKE_BUILD_TYPE=Release -Drtl=ON && make -j2`
- Monitors multiple frequencies simultaneously within 2 MHz RTL-SDR bandwidth — all must fit within 2 MHz window
- Israel/Europe frequencies: 129.125 / 130.025 / 130.425 / 130.450 MHz
- Output format `-o 4` = JSON per message (one object per line)
- `-e` flag skips empty messages, `-A` flag keeps aircraft messages only
- JSON log at `/var/log/acarsdec/messages.json`
- Service: `acarsdec.service` — managed by button_rtl.py

---

## RTL-433

- Available in apt: `sudo apt install -y rtl-433`
- Web UI / HTTP feed on port **8433** via `-F http:0.0.0.0:8433`
- JSON log at `/var/log/rtl_433/events.json` via `-F json:/var/log/rtl_433/events.json`
- Default frequency 433.92MHz covers most European sensors
- Service: `rtl_433.service` — managed by button_rtl.py

---

## AIS — AIS-catcher

- `rtl-ais` package does NOT exist in apt for arm64 Debian Bookworm
- Use **AIS-catcher** instead: `https://github.com/jvde-github/AIS-catcher`
- Build from source (takes ~3 minutes on OrangePi Zero 2W):
  ```bash
  sudo apt install -y cmake build-essential librtlsdr-dev libusb-1.0-0-dev pkg-config libssl-dev libz-dev
  git clone --depth=1 https://github.com/jvde-github/AIS-catcher.git /tmp/AIS-catcher
  cd /tmp/AIS-catcher && mkdir build && cd build
  cmake .. -DCMAKE_BUILD_TYPE=Release && make -j2
  sudo cp AIS-catcher /usr/local/bin/
  ```
- **Do NOT use `-d 0`** — it treats `0` as a Serial Number, not device index → "cannot find device with SN 0"
- Run without `-d` flag: `AIS-catcher -N 8424` — picks first device automatically
- Web UI at port **8424**
- Service: `ais_catcher.service`

---

## NOAA APT — Satellite Images

- Install `noaa-apt` from GitHub releases (arm64 binary available):
  ```bash
  wget https://github.com/martinber/noaa-apt/releases/download/v1.4.1/noaa-apt-1.4.1-aarch64-linux-gnu-nogui.zip
  unzip noaa-apt*.zip && sudo cp noaa-apt /usr/local/bin/ && sudo chmod +x /usr/local/bin/noaa-apt
  ```
- Install `python3-ephem` for pass prediction: `sudo apt install -y python3-ephem`
- TLE satellite names from Celestrak are `NOAA 15` / `NOAA 18` / `NOAA 19` (space, not dash)
- `noaa-apt --sat` flag expects `noaa_15` / `noaa_18` / `noaa_19` (lowercase underscore)
- TLE source: `https://celestrak.org/NORAD/elements/gp.php?GROUP=noaa&FORMAT=tle`
- WAV files (~150MB per pass) must be deleted after decode — only keep PNG
- Minimum elevation 15° is usable; 30°+ gives much better images
- Web UI at port **8080** — `noaa_web.py`
- Config: `/etc/noaa_apt.cfg` — `auto_capture` (bool) + `min_elev` (degrees)
- Station coordinates default: Tel Aviv (32.0853, 34.7818, 30m)
- Frequencies: NOAA-15=137.620M, NOAA-18=137.9125M, NOAA-19=137.100M

---

## Systemd

- All SDR services are **disabled** from autostart — `button_rtl.py` manages start/stop
- `button_rtl.service` is the only service that autostart on boot
- After adding a new `.service` file: always run `sudo systemctl daemon-reload`
- `noaa_web.service` is the exception — it autostarts (web UI only, no SDR access)
- Service restart after editing: `sudo systemctl restart button_rtl`

---

## SD Card Space Budget (16GB)

| Item | Size |
|------|------|
| OS base | ~3GB |
| radiosonde_auto_rx clone | ~200MB |
| AIS-catcher build artifacts (can delete /tmp/AIS-catcher after install) | ~150MB |
| NOAA PNG images (per pass) | ~500KB |
| readsb build (cached in /tmp) | ~100MB temp |
| **Free after full install** | ~11GB |

Clean up after install:
```bash
rm -rf /tmp/AIS-catcher
rm -rf /tmp/noaa-apt*
```

---

## Reinstall Checklist (Fresh SD Card)

1. Flash Orange Pi OS 1.0.2 Bookworm
2. First boot — set hostname, password, SSH key
3. `sudo orangepi-config` → System → Hardware → enable `i2c1`
4. `sudo reboot`
5. Verify OLED: `i2cdetect -y 2` → should show `3c`
6. `git clone https://github.com/amir684/orangepi-rtl-sdr.git && cd orangepi-rtl-sdr`
7. `bash install.sh` — answer Y/N for each component
8. `sudo reboot`
9. `bash install.sh` handles AIS-catcher, NOAA APT, multimon-ng, rtl_433 — answer Y/N
10. For ACARS: build acarsdec manually (see above) — not in install.sh yet
11. Edit `/home/orangepi/radiosonde_auto_rx/auto_rx/station.cfg` or use OLED menu
12. Edit `/etc/noaa_apt.cfg` for NOAA preferences

---

## Ports Reference

| Service | Port | Notes |
|---------|------|-------|
| RTL-TCP | 1234 | Raw IQ for SDR clients |
| AutoRX | 5000 | Radiosonde web UI |
| ADS-B | 80/tar1090 | tar1090 web map |
| RTL-433 | 8433 | HTTP feed + web UI |
| AIS | 8424 | AIS-catcher web map |
| NOAA APT | 8080 | Image gallery |

---

## SSH / Backup

```bash
# Backup scripts from Pi to repo
scp -i ~/.ssh/orangepi_key orangepi@IP:/usr/local/bin/button_rtl.py .
scp -i ~/.ssh/orangepi_key orangepi@IP:/usr/local/bin/noaa_capture.py .
scp -i ~/.ssh/orangepi_key orangepi@IP:/usr/local/bin/noaa_web.py .

# Copy NOAA images from Pi to PC
scp -i ~/.ssh/orangepi_key -r orangepi@IP:/var/lib/noaa-apt/images ./noaa_images/
```
