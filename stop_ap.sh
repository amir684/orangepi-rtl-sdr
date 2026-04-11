#!/bin/bash
pkill hostapd 2>/dev/null
pkill dnsmasq 2>/dev/null
sleep 1

# Find and reconnect to last known WiFi (not Hotspot)
LAST=$(nmcli -t -f NAME,TYPE con show | grep ':wifi$' | grep -v 'Hotspot' | head -1 | cut -d: -f1)
if [ -n "$LAST" ]; then
    nmcli con up "$LAST" 2>/dev/null
else
    nmcli dev connect wlan0 2>/dev/null
fi
