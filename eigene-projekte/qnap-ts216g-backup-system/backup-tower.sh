#!/bin/bash
# =============================================================================
# backup-tower.sh — Tower -> MentatVault NAS
# =============================================================================
# Sichert das Home-Verzeichnis des Towers auf den NAS per rsync über CIFS-Mount.
#
# Voraussetzungen:
#   - NAS unter /mnt/mentatvault gemountet (siehe fstab)
#   - Skript ausführbar: chmod +x backup-tower.sh
#
# Konfiguration: Passe die Variablen unten an dein Setup an.
# Cronjob: 0 2 * * * /home/<DEIN_USER>/backup-tower.sh
# =============================================================================

USER_HOME="/home/<DEIN_USER>"     # z.B. /home/tonij
ZIEL="/mnt/mentatvault/tower"
LOG="$HOME/backup-tower.log"
DATUM=$(date +%Y-%m-%d)

echo "[$DATUM] Backup gestartet" >> $LOG

rsync -avz --delete \
  --exclude='.local/share/Steam/' \
  --exclude='.cache/' \
  --exclude='Downloads/' \
  $USER_HOME/ \
  $ZIEL/home/ >> $LOG 2>&1

echo "[$DATUM] Backup abgeschlossen (Exit: $?)" >> $LOG
