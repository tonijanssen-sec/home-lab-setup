#!/bin/bash
# =============================================================================
# backup-mentat.sh — mentat-ai-node -> MentatVault NAS
# =============================================================================
# Sichert MemPalace, Skripte, Knowledge Base, N8N Workflows und Identity
# per SSH auf den NAS.
#
# Voraussetzungen:
#   - SSH-Key auf mentat-ai-node hinterlegt: ssh-copy-id pi@<NODE_IP>
#   - NAS unter /mnt/mentatvault gemountet (siehe fstab)
#
# Konfiguration: Passe die Variablen unten an dein Setup an.
# Cronjob: 0 3 * * * /home/<DEIN_USER>/backup-mentat.sh
# =============================================================================

NODE_USER="pi"
NODE_IP="<NODE_IP>"               # IP des mentat-ai-node, z.B. 192.168.0.x
ZIEL="/mnt/mentatvault/mentat-ai-node"
LOG="$HOME/backup-mentat.log"
DATUM=$(date +%Y-%m-%d)

echo "[$DATUM] Backup gestartet" >> $LOG

# MemPalace (ChromaDB)
rsync -avz --delete $NODE_USER@$NODE_IP:/home/pi/mentat-palace/ $ZIEL/mentat-palace/ >> $LOG 2>&1

# Chat-Historie
rsync -avz --delete $NODE_USER@$NODE_IP:/home/pi/mentat-chats/ $ZIEL/mentat-chats/ >> $LOG 2>&1

# Hauptskript
rsync -avz $NODE_USER@$NODE_IP:/home/pi/mentat.py $ZIEL/ >> $LOG 2>&1

# Knowledge Base
rsync -avz --delete $NODE_USER@$NODE_IP:/home/pi/mentat-knowledge/ $ZIEL/mentat-knowledge/ >> $LOG 2>&1

# N8N Workflows (Docker-Volume nicht direkt lesbar -> export über docker exec)
ssh $NODE_USER@$NODE_IP "mkdir -p /home/pi/n8n-backup && docker exec n8n sh -c 'n8n export:workflow --backup --output=/tmp/n8n-backup/' && docker cp n8n:/tmp/n8n-backup/. /home/pi/n8n-backup/" >> $LOG 2>&1
rsync -avz --delete $NODE_USER@$NODE_IP:/home/pi/n8n-backup/ $ZIEL/n8n/ >> $LOG 2>&1

# Mentat Identity/Soul
rsync -avz $NODE_USER@$NODE_IP:/home/pi/.mempalace/identity.txt $ZIEL/ >> $LOG 2>&1

echo "[$DATUM] Backup abgeschlossen (Exit: $?)" >> $LOG
