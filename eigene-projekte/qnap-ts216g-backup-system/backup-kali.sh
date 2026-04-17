#!/bin/bash
# =============================================================================
# backup-kali.sh — Kali-Pi -> MentatVault NAS
# =============================================================================
# Sichert Home-Verzeichnis und Apache/DVWA Konfiguration per SSH auf den NAS.
#
# Voraussetzungen:
#   - SSH-Key auf Kali-Pi hinterlegt: ssh-copy-id kali@<KALI_IP>
#   - NAS unter /mnt/mentatvault gemountet (siehe fstab)
#
# Konfiguration: Passe die Variablen unten an dein Setup an.
# Cronjob: 5 4 * * 0 /home/<DEIN_USER>/backup-kali.sh  (wöchentlich Sonntag)
# =============================================================================

KALI_USER="kali"
KALI_IP="<KALI_IP>"              # IP des Kali-Pi, z.B. 192.168.0.x
ZIEL="/mnt/mentatvault/kali-pi"
LOG="$HOME/backup-kali.log"
DATUM=$(date +%Y-%m-%d)

echo "[$DATUM] Backup gestartet" >> $LOG

# Home-Verzeichnis (ohne Cache)
rsync -avz --delete \
  --exclude='.cache/' \
  $KALI_USER@$KALI_IP:/home/kali/ $ZIEL/home/ >> $LOG 2>&1

# Apache/DVWA Konfiguration
# --no-links: CIFS unterstützt keine Linux-Symlinks
rsync -avz --delete --no-links \
  $KALI_USER@$KALI_IP:/etc/apache2/ $ZIEL/apache2/ >> $LOG 2>&1

echo "[$DATUM] Backup abgeschlossen (Exit: $?)" >> $LOG
