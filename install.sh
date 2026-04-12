#!/bin/bash
set -e

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  OrangePi RTL-SDR MultiTool — Installer  ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── Optional components (ask upfront) ─────────────────────
echo "Default components (always installed):"
echo "  [✔] RTL-TCP server"
echo "  [✔] OLED UI + joystick"
echo "  [✔] WiFi / AP management"
echo ""
echo "Optional components:"

read -p "  Install ADS-B tracking (readsb + tar1090)?  [Y/n] " ANS_ADSB
ANS_ADSB=${ANS_ADSB:-Y}

read -p "  Install AutoRX (radiosonde tracking)?       [Y/n] " ANS_AUTORX
ANS_AUTORX=${ANS_AUTORX:-Y}

read -p "  Install rtl_433 (433MHz sensors/IoT)?       [y/N] " ANS_433
ANS_433=${ANS_433:-N}

read -p "  Install AIS (ship tracking)?                [y/N] " ANS_AIS
ANS_AIS=${ANS_AIS:-N}

read -p "  Install NOAA APT (satellite images)?        [y/N] " ANS_NOAA
ANS_NOAA=${ANS_NOAA:-N}

read -p "  Install multimon-ng (POCSAG pagers)?        [y/N] " ANS_PAGER
ANS_PAGER=${ANS_PAGER:-N}

echo ""

# ── 1. System update ──────────────────────────────────────
echo "[1] Updating package lists..."
sudo apt update -y

# ── 2. Core packages ──────────────────────────────────────
echo "[2] Installing core packages..."
sudo apt install -y \
    rtl-sdr \
    hostapd \
    dnsmasq \
    network-manager \
    python3-pip \
    fonts-dejavu-core \
    i2c-tools \
    wget

# ── 3. Python libraries ───────────────────────────────────
echo "[3] Installing Python libraries..."
pip3 install --break-system-packages OPi.GPIO smbus2 Pillow

# ── 4. ADS-B: readsb + tar1090 ────────────────────────────
if [[ "$ANS_ADSB" =~ ^[Yy] ]]; then
    echo "[4] Installing readsb..."
    sudo apt install -y readsb

    echo "[4] Configuring readsb for RTL-SDR..."
    sudo tee /etc/default/readsb > /dev/null <<'EOF'
# RTL-SDR dongle
RECEIVER_OPTIONS="--device-type rtlsdr --device 0"
# Decoder — limit range to reduce CPU/RAM load
DECODER_OPTIONS="--max-range 450"
# Network — minimal ports
NET_OPTIONS="--net --net-heartbeat 60 --net-ro-size 1000 --net-ro-interval 1 \
--net-ri-port 0 --net-ro-port 30002 --net-sbs-port 30003 \
--net-bi-port 30004,30104 --net-bo-port 30005"
# JSON output
JSON_OPTIONS="--json-location-accuracy 1"
EXTRA_OPTIONS=""
EOF

    echo "[4] Installing tar1090 web UI..."
    sudo apt install -y lighttpd
    bash -c "$(wget -nv -O - https://github.com/wiedehopf/tar1090/raw/master/install.sh)"

    # Disable autostart — button_rtl.py manages start/stop
    sudo systemctl disable readsb tar1090 2>/dev/null || true
    sudo systemctl stop readsb tar1090 2>/dev/null || true
    echo "[4] ADS-B installed. Web UI will be at http://IP/tar1090"
else
    echo "[4] Skipping ADS-B."
fi

# ── 5. AutoRX ─────────────────────────────────────────────
if [[ "$ANS_AUTORX" =~ ^[Yy] ]]; then
    echo "[5] Installing AutoRX dependencies..."
    sudo apt install -y \
        python3-numpy python3-scipy python3-requests \
        python3-dateutil tini git

    pip3 install --break-system-packages \
        crcmod construct bitarray

    echo "[5] Cloning radiosonde_auto_rx..."
    if [ ! -d /home/orangepi/radiosonde_auto_rx ]; then
        git clone --depth=1 \
            https://github.com/projecthorus/radiosonde_auto_rx.git \
            /home/orangepi/radiosonde_auto_rx
    else
        echo "[5] Already cloned, skipping."
    fi

    # Minimal config — 1 scanner, 1 decoder max to save RAM
    if [ ! -f /home/orangepi/radiosonde_auto_rx/auto_rx/station.cfg ]; then
        cp /home/orangepi/radiosonde_auto_rx/auto_rx/station.cfg.example \
           /home/orangepi/radiosonde_auto_rx/auto_rx/station.cfg
    fi

    echo "[5] Building AutoRX decoders..."
    cd /home/orangepi/radiosonde_auto_rx/auto_rx
    sudo -u orangepi bash build.sh 2>&1 | tail -3
    cd -

    echo "[5] AutoRX installed at /home/orangepi/radiosonde_auto_rx"
    echo "[5] Edit station.cfg before first use (optional)."
else
    echo "[5] Skipping AutoRX."
fi

# ── 6. rtl_433 ────────────────────────────────────────────
if [[ "$ANS_433" =~ ^[Yy] ]]; then
    echo "[6] Installing rtl_433..."
    sudo apt install -y rtl-433

    echo "[6] Installing rtl_433 systemd service..."
    sudo cp rtl_433.service /etc/systemd/system/rtl_433.service
    sudo mkdir -p /var/log/rtl_433
    sudo systemctl daemon-reload
    sudo systemctl disable rtl_433 2>/dev/null || true
    sudo systemctl stop rtl_433 2>/dev/null || true
    echo "[6] rtl_433 installed. HTTP feed at http://IP:8433"
else
    echo "[6] Skipping rtl_433."
fi

# ── 7. AIS — AIS-catcher ──────────────────────────────────
# Note: rtl-ais is NOT in apt for arm64 Bookworm — build AIS-catcher instead
if [[ "$ANS_AIS" =~ ^[Yy] ]]; then
    echo "[7] Building AIS-catcher from source (~3 min)..."
    sudo apt install -y cmake build-essential librtlsdr-dev libusb-1.0-0-dev \
        pkg-config libssl-dev libz-dev
    rm -rf /tmp/AIS-catcher
    git clone --depth=1 https://github.com/jvde-github/AIS-catcher.git /tmp/AIS-catcher
    cd /tmp/AIS-catcher && mkdir build && cd build
    cmake .. -DCMAKE_BUILD_TYPE=Release && make -j2
    sudo cp AIS-catcher /usr/local/bin/
    cd /
    rm -rf /tmp/AIS-catcher
    echo "[7] AIS-catcher installed. Web UI will be at http://IP:8424"
else
    echo "[7] Skipping AIS."
fi

# ── 8. NOAA APT ───────────────────────────────────────────
if [[ "$ANS_NOAA" =~ ^[Yy] ]]; then
    echo "[8] Installing NOAA APT..."
    sudo apt install -y sox python3-ephem wget unzip

    echo "[8] Downloading noaa-apt arm64 binary..."
    NOAA_APT_VER="1.4.1"
    wget -q "https://github.com/martinber/noaa-apt/releases/download/v${NOAA_APT_VER}/noaa-apt-${NOAA_APT_VER}-aarch64-linux-gnu-nogui.zip" \
        -O /tmp/noaa-apt.zip
    unzip -o /tmp/noaa-apt.zip -d /tmp/noaa-apt-bin
    sudo cp /tmp/noaa-apt-bin/noaa-apt-${NOAA_APT_VER}-aarch64-linux-gnu-nogui/noaa-apt \
        /usr/local/bin/noaa-apt
    sudo chmod +x /usr/local/bin/noaa-apt
    rm -rf /tmp/noaa-apt.zip /tmp/noaa-apt-bin

    sudo mkdir -p /var/lib/noaa-apt/images
    sudo chown orangepi:orangepi /var/lib/noaa-apt/images

    if [ ! -f /etc/noaa_apt.cfg ]; then
        sudo tee /etc/noaa_apt.cfg > /dev/null <<'EOF'
{
  "auto_capture": false,
  "min_elev": 15
}
EOF
    fi
    echo "[8] NOAA APT installed. Web gallery will be at http://IP:8080"
else
    echo "[8] Skipping NOAA APT."
fi

# ── 9. multimon-ng ────────────────────────────────────────
if [[ "$ANS_PAGER" =~ ^[Yy] ]]; then
    echo "[9] Installing multimon-ng..."
    sudo apt install -y multimon-ng
    echo "[9] multimon-ng installed."
else
    echo "[9] Skipping multimon-ng."
fi

# ── 10. Copy scripts and service files ────────────────────
echo "[10] Copying scripts..."
sudo cp button_rtl.py      /usr/local/bin/button_rtl.py
sudo cp wifi_portal.py     /usr/local/bin/wifi_portal.py
sudo cp config_portal.py   /usr/local/bin/config_portal.py
sudo cp start_ap.sh        /usr/local/bin/start_ap.sh
sudo cp stop_ap.sh         /usr/local/bin/stop_ap.sh
sudo chmod +x /usr/local/bin/start_ap.sh /usr/local/bin/stop_ap.sh

echo "[10] Configuring hostapd..."
sudo mkdir -p /etc/hostapd
sudo cp hostapd_5g.conf /etc/hostapd/hostapd_5g.conf

echo "[10] Installing systemd service files..."
sudo cp button_rtl.service /lib/systemd/system/button_rtl.service

if [[ "$ANS_ADSB" =~ ^[Yy] ]]; then
    sudo cp auto-rx.service /lib/systemd/system/auto-rx.service 2>/dev/null || true
fi
if [[ "$ANS_433" =~ ^[Yy] ]]; then
    sudo cp rtl_433.service /lib/systemd/system/rtl_433.service
    sudo mkdir -p /var/log/rtl_433
fi
if [[ "$ANS_AIS" =~ ^[Yy] ]]; then
    sudo cp ais_catcher.service /lib/systemd/system/ais_catcher.service
fi
if [[ "$ANS_NOAA" =~ ^[Yy] ]]; then
    sudo cp noaa_capture.py   /usr/local/bin/noaa_capture.py
    sudo cp noaa_web.py       /usr/local/bin/noaa_web.py
    sudo cp noaa_capture.service /lib/systemd/system/noaa_capture.service
    sudo cp noaa_web.service     /lib/systemd/system/noaa_web.service
fi
if [[ "$ANS_PAGER" =~ ^[Yy] ]]; then
    sudo cp multimon_ng.service /lib/systemd/system/multimon_ng.service
    sudo mkdir -p /var/log/multimon_ng
fi

# ── 11. Systemd — enable / start ──────────────────────────
echo "[11] Enabling systemd services..."
sudo systemctl daemon-reload
sudo systemctl enable button_rtl
sudo systemctl start button_rtl

if [[ "$ANS_NOAA" =~ ^[Yy] ]]; then
    sudo systemctl enable noaa_web
    sudo systemctl start noaa_web
    sudo systemctl disable noaa_capture 2>/dev/null || true
    sudo systemctl stop noaa_capture 2>/dev/null || true
fi

# ── Done ──────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║              Install complete!           ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "IMPORTANT — Enable I2C before rebooting:"
echo "  sudo orangepi-config → System → Hardware → i2c1"
echo ""
echo "Then reboot:"
echo "  sudo reboot"
echo ""
