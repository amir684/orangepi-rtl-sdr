# OrangePi Zero 2W — RTL-SDR Server with OLED UI

A fully standalone RTL-SDR server running on an **Orange Pi Zero 2W**, controlled via 4 physical buttons and a 128×32 OLED display. Supports WiFi client mode, 5GHz AP mode, and real-time frequency display.

---

## Hardware

| Component | Details |
|-----------|---------|
| Board | Orange Pi Zero 2W (Allwinner H618) |
| OS | Orange Pi OS 1.0.2 (Bookworm) |
| SDR Dongle | RTL-SDR (any RTL2832U-based) via USB |
| Display | SSD1306 128×32 OLED — I2C bus 3, address `0x3C` |
| Buttons | 4× tactile push buttons (active LOW, internal pull-up) |

### Button Wiring

| GPIO Pin | Constant | Function |
|----------|----------|----------|
| PI0 | BTN_BACK | Back / Cancel |
| PI1 | BTN_UP | Scroll up / Increase RTL gain |
| PI3 | BTN_DOWN | Scroll down |
| PI4 | BTN_SEL | Short press: Select — Long press (1s): Open menu |

All buttons wired: one side to **GND**, other side to the GPIO pin (internal pull-up enabled).

### OLED Wiring (I2C)

| OLED Pin | Orange Pi Pin |
|----------|---------------|
| VCC | 3.3V |
| GND | GND |
| SDA | PI7 (I2C-3 SDA) |
| SCL | PI8 (I2C-3 SCL) |

---

## Features

- **RTL-TCP server** — start/stop `rtl_tcp` via button, real-time frequency display as clients tune in
- **128×32 OLED UI** — shows IP address, RTL status, current frequency, CPU temperature
- **4-button menu system** — navigate with Up/Down, select with SEL, back with BACK
- **WiFi management** — connect to last saved network or scan and join new networks with on-screen password entry
- **5GHz AP mode** — turns the Pi into an open hotspot (`OrangePi-SDR`) using hostapd + dnsmasq, DHCP on `192.168.100.x`
- **Auto temperature refresh** — CPU temp updated every 10 seconds on idle screen
- **Systemd service** — auto-starts on boot, restarts on crash

---

## Software Dependencies

```bash
sudo apt install rtl-sdr python3-pip hostapd dnsmasq
pip3 install OPi.GPIO smbus2 Pillow
```

---

## File Structure

```
/usr/local/bin/
├── button_rtl.py       # Main controller script
├── start_ap.sh         # Start hostapd + dnsmasq AP
└── stop_ap.sh          # Stop AP and reconnect to WiFi

/etc/systemd/system/
└── button_rtl.service  # Systemd unit for auto-start

/etc/hostapd/
└── hostapd_5g.conf     # 5GHz open AP configuration
```

---

## Installation

### 1. Copy scripts

```bash
sudo cp button_rtl.py /usr/local/bin/
sudo cp start_ap.sh stop_ap.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/start_ap.sh /usr/local/bin/stop_ap.sh
```

### 2. Create hostapd config

```bash
sudo nano /etc/hostapd/hostapd_5g.conf
```

```ini
interface=wlan0
driver=nl80211
ssid=OrangePi-SDR
hw_mode=a
channel=36
ieee80211n=1
ieee80211ac=1
wmm_enabled=1
country_code=IL
ignore_broadcast_ssid=0
auth_algs=1
wpa=0
```

### 3. Install and enable systemd service

```bash
sudo cp button_rtl.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable button_rtl
sudo systemctl start button_rtl
```

### 4. Check status

```bash
sudo systemctl status button_rtl
```

---

## Usage

### Idle Screen

```
192.168.1.XXX        32.5C
RTL: OFF
```

- **BTN_UP** (short press): Toggle rtl_tcp ON/OFF
- **BTN_SEL** (long press 1s): Open menu

### Menu

```
> AP Mode
  WiFi Mode
  < Back
```

Navigate with UP/DOWN, confirm with SEL, cancel with BACK.

### AP Mode

When AP is active, idle screen shows:
```
192.168.100.1        34.1C
AP:ON RTL: ON
```

Connect to WiFi `OrangePi-SDR` (no password) and use `192.168.100.1:1234` as RTL-TCP server.

To stop AP: open menu → **Stop AP**.

### WiFi Password Entry

On-screen character picker — UP/DOWN to cycle characters, SEL to confirm each character, BACK to delete, long-press SEL to submit password.

---

## RTL-TCP

Default port: **1234**

Connect from SDR software (SDR#, GQRX, etc.):

```
Host: <Pi IP>
Port: 1234
```

---

## Notes

- I2C discovery: run `i2cdetect -y 3` to confirm OLED at `0x3C`
- If `rtl_tcp` fails with `usb_claim_interface error -6`, another instance is running — the script handles this automatically with pkill before each start
- AP uses country code `IL` (Israel) — change `country_code` in `hostapd_5g.conf` if needed
- SSH access: `ssh orangepi@<ip>` (key auth recommended)
