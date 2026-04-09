# OrangePi Zero 2W — RTL-SDR Server with OLED UI

A fully standalone RTL-SDR server running on an **Orange Pi Zero 2W**, controlled via 4 physical buttons and a 128×32 OLED display.  
Supports WiFi client mode, 5GHz AP mode, and real-time frequency display — no screen or keyboard required after setup.

---

## Features

- **RTL-TCP server** — start/stop `rtl_tcp` with a button press, real-time frequency display as clients tune
- **128×32 OLED UI** — shows IP address, RTL status, current frequency, CPU temperature
- **4-button menu system** — navigate with Up/Down, select with SEL, back with BACK
- **WiFi management** — connect to last saved network or scan and join new networks with on-screen password entry
- **5GHz AP mode** — turns the Pi into an open hotspot (`OrangePi-SDR`) using hostapd + dnsmasq
- **Systemd service** — auto-starts on boot, restarts automatically on crash

---

## Hardware

| Component | Details |
|-----------|---------|
| Board | Orange Pi Zero 2W (Allwinner H618) |
| OS | Orange Pi OS 1.0.2 (Bookworm) |
| SDR Dongle | RTL-SDR (any RTL2832U-based) via USB |
| Display | SSD1306 128×32 OLED — I2C bus 2, address `0x3C` |
| Buttons | 4× tactile push buttons (active LOW, internal pull-up) |

### Button Wiring

| GPIO Pin | Role | Function |
|----------|------|----------|
| PI0 | BTN_BACK | Back / Cancel |
| PI1 | BTN_UP | Scroll up / Toggle RTL |
| PI3 | BTN_DOWN | Scroll down |
| PI4 | BTN_SEL | Short press: Select — Long press (1s): Open menu |

> All buttons: one side to **GND**, other side to the GPIO pin (internal pull-up enabled).

### OLED Wiring (I2C)

| OLED Pin | Orange Pi Pin |
|----------|---------------|
| VCC | 3.3V |
| GND | GND |
| SDA | PI7 (I2C-3 SDA) |
| SCL | PI8 (I2C-3 SCL) |

---

## Quick Install (Fresh SD Card)

### Step 1 — Enable I2C bus 3

```bash
sudo orangepi-config
# System → Hardware → enable i2c1 → Save → Back
```

### Step 2 — Clone and run install script

```bash
git clone https://github.com/amir684/orangepi-rtl-sdr.git
cd orangepi-rtl-sdr
bash install.sh
```

### Step 3 — Reboot

```bash
sudo reboot
```

That's it. The OLED will light up and the service starts automatically on every boot.

---

## What `install.sh` Does

1. Updates the system (`apt update && upgrade`)
2. Installs system packages: `rtl-sdr`, `hostapd`, `dnsmasq`, `python3-pip`
3. Installs Python libraries: `OPi.GPIO`, `smbus2`, `Pillow`
4. Copies scripts to `/usr/local/bin/`
5. Copies `hostapd_5g.conf` to `/etc/hostapd/`
6. Installs and enables the `button_rtl` systemd service

---

## File Structure

```
Repository:
├── button_rtl.py        Main controller script
├── start_ap.sh          Start hostapd + dnsmasq AP
├── stop_ap.sh           Stop AP and reconnect to WiFi
├── hostapd_5g.conf      5GHz open AP configuration
├── button_rtl.service   Systemd unit for auto-start
└── install.sh           One-shot install script

Installed on device:
/usr/local/bin/
├── button_rtl.py
├── start_ap.sh
└── stop_ap.sh
/etc/hostapd/hostapd_5g.conf
/etc/systemd/system/button_rtl.service
```

---

## Usage

### Idle Screen

```
192.168.1.XXX          32.5C
RTL: OFF
```

| Button | Action |
|--------|--------|
| BTN_UP (short) | Toggle rtl_tcp ON / OFF |
| BTN_SEL (hold 1s) | Open menu |

---

### Menu Navigation

```
> AP Mode
  WiFi Mode
  Brightness
  < Back
```

| Button | Action |
|--------|--------|
| BTN_UP / BTN_DOWN | Scroll items |
| BTN_SEL | Confirm selection |
| BTN_BACK | Go back / cancel |

---

### Brightness Control

Accessible from the main menu → **Brightness**.

```
Brightness
███░░  3/5
```

| Button | Action |
|--------|--------|
| RIGHT | Increase brightness |
| LEFT | Decrease brightness |
| CENTER | Back to menu |

- 5 levels (1 = very dim, 5 = max)
- Default: level 3
- Setting is saved to `/etc/button_rtl_brightness` and restored on reboot

---

### AP Mode

Starts a 5GHz open hotspot. Idle screen changes to:

```
192.168.100.1          34.1C
AP:ON  RTL: ON
```

- SSID: `OrangePi-SDR` (no password)
- DHCP range: `192.168.100.10 – 192.168.100.50`
- RTL-TCP: `192.168.100.1:1234`

To stop: open menu → **Stop AP** (returns to WiFi client mode).

---

### WiFi Password Entry

On-screen character picker:

| Button | Action |
|--------|--------|
| BTN_UP / BTN_DOWN | Cycle through characters |
| BTN_SEL (short) | Confirm character |
| BTN_BACK | Delete last character |
| BTN_SEL (hold 1s) | Submit password |

---

## Connecting SDR Software

| Setting | Value |
|---------|-------|
| Host | Pi IP (shown on OLED) |
| Port | `1234` |

Works with SDR#, GQRX, SDR++, and any rtl_tcp-compatible client.

---

## Service Management

```bash
# Check status
sudo systemctl status button_rtl

# Restart
sudo systemctl restart button_rtl

# View logs
journalctl -u button_rtl -f
```

---

## Notes

- Verify OLED detected: `i2cdetect -y 2` — should show `0x3C`
- `usb_claim_interface error -6` means another rtl_tcp is running — handled automatically by the script
- AP uses `country_code=IL` (Israel) — change in `hostapd_5g.conf` if needed
- SSH: `ssh orangepi@<ip>` (key auth recommended: `~/.ssh/orangepi_key`)
