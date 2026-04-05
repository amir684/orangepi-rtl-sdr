#!/bin/bash
# Stop any existing connections
nmcli dev disconnect wlan0 2>/dev/null
pkill hostapd 2>/dev/null
pkill dnsmasq 2>/dev/null
sleep 1

# Set static IP on wlan0
ip addr flush dev wlan0
ip addr add 192.168.100.1/24 dev wlan0
ip link set wlan0 up

# Start hostapd
hostapd /etc/hostapd/hostapd_5g.conf > /tmp/hostapd_run.log 2>&1 &
sleep 2

# Start DHCP server
dnsmasq --interface=wlan0 \
        --dhcp-range=192.168.100.10,192.168.100.50,12h \
        --keep-in-foreground > /tmp/dnsmasq_run.log 2>&1 &
