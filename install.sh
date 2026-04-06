#!/bin/bash
set -e

echo "=== OrangePi RTL-SDR Server — Install Script ==="

# ── 1. System update ──────────────────────────────────────
echo "[1/6] Updating system..."
sudo apt update -y && sudo apt upgrade -y

# ── 2. Install packages ───────────────────────────────────
echo "[2/6] Installing packages..."
sudo apt install -y rtl-sdr hostapd dnsmasq python3-pip

# ── 3. Install Python libraries ───────────────────────────
echo "[3/6] Installing Python libraries..."
pip3 install OPi.GPIO smbus2 Pillow

# ── 4. Copy scripts ───────────────────────────────────────
echo "[4/6] Copying scripts..."
sudo cp button_rtl.py /usr/local/bin/button_rtl.py
sudo cp start_ap.sh /usr/local/bin/start_ap.sh
sudo cp stop_ap.sh /usr/local/bin/stop_ap.sh
sudo chmod +x /usr/local/bin/start_ap.sh /usr/local/bin/stop_ap.sh

# ── 5. Copy hostapd config ────────────────────────────────
echo "[5/6] Configuring hostapd..."
sudo mkdir -p /etc/hostapd
sudo cp hostapd_5g.conf /etc/hostapd/hostapd_5g.conf

# ── 6. Install and enable systemd service ─────────────────
echo "[6/6] Enabling systemd service..."
sudo cp button_rtl.service /etc/systemd/system/button_rtl.service
sudo systemctl daemon-reload
sudo systemctl enable button_rtl
sudo systemctl start button_rtl

echo ""
echo "=== Done! ==="
echo ""
echo "IMPORTANT: Enable I2C bus 3 if not already done:"
echo "  sudo orangepi-config → System → Hardware → i2c3"
echo ""
echo "Then reboot:"
echo "  sudo reboot"
