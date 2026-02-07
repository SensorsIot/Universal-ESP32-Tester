#!/bin/sh
# dnsmasq DHCP lease callback — posts lease events to the portal.
#
# dnsmasq calls this script with:
#   $1 = action (add, old, del)
#   $2 = MAC address
#   $3 = IP address
#   $4 = hostname (may be empty)
#
# Installed as dhcp-script in dnsmasq.conf.

ACTION="$1"
MAC="$2"
IP="$3"
HOSTNAME="${4:-}"

PORTAL_URL="http://127.0.0.1:8080/api/wifi/lease_event"

PAYLOAD="{\"action\":\"${ACTION}\",\"mac\":\"${MAC}\",\"ip\":\"${IP}\",\"hostname\":\"${HOSTNAME}\"}"

# Fire-and-forget POST to portal (timeout 2s, ignore errors)
curl -s -X POST -H "Content-Type: application/json" \
     -d "$PAYLOAD" --max-time 2 "$PORTAL_URL" >/dev/null 2>&1 || true
