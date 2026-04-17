#!/bin/bash
# =============================================================================
# network-monitor.sh — Heimnetz Scanner für N8N Network Monitor
# =============================================================================
# Scannt das lokale Netzwerk per arp-scan, vergleicht Geräte mit der Whitelist
# und sendet das Ergebnis als JSON an den N8N Webhook.
#
# Voraussetzungen:
#   - arp-scan installiert: sudo apt install arp-scan
#   - nmap installiert: sudo apt install nmap
#   - Whitelist: /etc/network-monitor/whitelist.json
#   - N8N läuft auf localhost:5678
#
# Konfiguration: Passe die Variablen unten an dein Setup an.
# Cronjob: */15 * * * * /etc/network-monitor/network-monitor.sh
# =============================================================================

WHITELIST="/etc/network-monitor/whitelist.json"
WEBHOOK="http://localhost:5678/webhook/network-scan"
SUBNET="<DEIN_SUBNETZ>"          # z.B. 192.168.0.0/24

NMAP_OUT=$(sudo /usr/bin/nmap -sn $SUBNET 2>/dev/null)
ARP_OUT=$(sudo /usr/sbin/arp-scan --localnet --retry=3 2>/dev/null)
DEVICES=$(echo "$ARP_OUT" | grep -E "^192\." | awk '{print $1, $2}' | sort -u)

JSON="["
FIRST=true

while IFS= read -r line; do
  IP=$(echo "$line" | awk '{print $1}')
  MAC=$(echo "$line" | awk '{print $2}')

  KNOWN=$(python3 -c "
import json, sys
wl = json.load(open('$WHITELIST'))
for d in wl:
    if d['mac'].lower() == '$MAC'.lower():
        print(d['name'])
        sys.exit()
print('UNKNOWN')
")

  if [ "$FIRST" = true ]; then
    FIRST=false
  else
    JSON="$JSON,"
  fi

  JSON="$JSON{\"ip\":\"$IP\",\"mac\":\"$MAC\",\"name\":\"$KNOWN\",\"known\":$([ \"$KNOWN\" = \"UNKNOWN\" ] && echo false || echo true)}"
done <<< "$DEVICES"

JSON="$JSON]"

curl -s -X POST "$WEBHOOK" \
  -H "Content-Type: application/json" \
  -d "{\"devices\":$JSON,\"timestamp\":\"$(date -Iseconds)\"}"
