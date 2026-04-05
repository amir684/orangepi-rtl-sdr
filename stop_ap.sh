#!/bin/bash
pkill hostapd 2>/dev/null
pkill dnsmasq 2>/dev/null
sleep 1
nmcli con up "amir&liel" 2>/dev/null
