# mentat-ai-node – Raspberry Pi 5 + Hailo-10H AI Node

Lokaler, privater AI-Inference-Node auf Basis des Raspberry Pi 5 mit AI HAT+ 2 (Hailo-10H). Komplett offline-fähig, kein Cloud-Zwang, volle Kontrolle. Potenzielles IHK-Abschlussprojekt.

---

## Hardware

| Komponente | Details |
|---|---|
| Raspberry Pi 5 | 8GB RAM |
| AI HAT+ 2 | Hailo-10H, 40 TOPS, 8GB LPDDR4X onboard |
| Netzteil | Raspberry Pi 27W USB-C |
| Kühler | Berry Base Aktiver Kühler + Heatsink |
| SD-Karte | 64GB A2 microSD |
| Externe HDD | WD Elements 1TB USB 3.0, 2.5", 5400 RPM |

---

## OS & Grundsetup

- **OS:** Raspberry Pi OS Lite 64-bit (Trixie)
- **SSH:** aktiviert
- **Hostname:** mentat-ai-node

### System aktualisieren

```bash
sudo apt update && sudo apt full-upgrade -y
```

---

## Netzwerk & Remote Access

### Tailscale installieren

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo systemctl enable tailscaled
```

Zugriff von überall per Tailscale-IP oder via aShellFish (iOS).

---

## Hailo-10H Treiber & SDK

### hailo-h10-all installieren

```bash
sudo apt install hailo-h10-all -y
sudo reboot
```

> Wichtig: `hailo-all` allein reicht nicht für den Hailo-10H — `hailo-h10-all` ist das richtige Paket für diesen Chip.

### Installation verifizieren

```bash
lspci | grep Hailo
sudo hailortcli fw-control identify
```

**Erwarteter Output:**
```
Executing on device: 0001:01:00.0
Firmware Version: 5.1.1 (release,app)
Device Architecture: HAILO10H
```

---

## hailo-ollama installieren

```bash
wget https://dev-public.hailo.ai/2025_12/Hailo10/hailo_gen_ai_model_zoo_5.1.1_arm64.deb
sudo dpkg -i hailo_gen_ai_model_zoo_5.1.1_arm64.deb
```

### hailo-ollama als systemd Service

```bash
sudo nano /etc/systemd/system/hailo-ollama.service
```

```ini
[Unit]
Description=Hailo Ollama Runtime
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStartPre=/bin/sleep 5
ExecStart=/usr/bin/hailo-ollama
Restart=always
RestartSec=3
Environment=HAILO_LOG_LEVEL=info

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable hailo-ollama
sudo systemctl start hailo-ollama
```

### Modell herunterladen

```bash
curl http://localhost:8000/api/pull \
  -H 'Content-Type: application/json' \
  -d '{"model": "qwen2.5-instruct:1.5b", "stream": true}'
```

### Verfügbare Modelle anzeigen

```bash
curl --silent http://localhost:8000/hailo/v1/list
```

### Modell testen

```bash
curl http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"model": "qwen2.5-instruct:1.5b", "messages": [{"role": "user", "content": "Hallo, wer bist du?"}]}'
```

---

## Docker installieren

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker pi
```

Nach dem Neueinloggen testen:

```bash
docker run hello-world
```

---

## N8N via Docker

```bash
docker run -d \
  --name n8n \
  --restart always \
  -p 5678:5678 \
  -e N8N_SECURE_COOKIE=false \
  -e GENERIC_TIMEZONE=Europe/Berlin \
  -e TZ=Europe/Berlin \
  -v n8n_data:/home/node/.n8n \
  --network host \
  n8nio/n8n
```

Erreichbar unter: `http://<NODE-IP>:5678`

---

## MemPalace & ChromaDB

MemPalace ist das Gedächtnissystem von Mentat — alle Gespräche, Suchergebnisse und Wissensdateien landen hier als durchsuchbare Vektordatenbank.

### Installation

```bash
pip install mempalace chromadb --break-system-packages
```

### Versionen (Stand April 2026)

| Paket | Version |
|---|---|
| MemPalace | 3.3.2 |
| ChromaDB | 1.5.8 |

> Hinweis: MemPalace 3.3.x erfordert ChromaDB >= 1.5.4. Bei einem Upgrade von ChromaDB 0.6.x auf 1.5.x immer `mempalace migrate` ausführen — die Migration ist irreversibel, vorher Backup!

### Upgrade-Ablauf

```bash
# Backup sichern
cp -r ~/mentat-palace ~/mentat-palace-backup-$(date +%Y%m%d)

# Upgrade
pip install --upgrade mempalace chromadb --break-system-packages

# Datenbank migrieren
mempalace --palace ~/mentat-palace migrate

# Status prüfen
mempalace --palace ~/mentat-palace status
```

### Palace-Struktur

Das Palace ist in zwei Wings aufgeteilt:

```
mentat_chats/        — Gesprächsprotokolle (technical, architecture, general, planning)
mentat_knowledge/    — Wissensbasis
  ├── pentesting/    — OWASP, Tools, Pentesting-Guides
  ├── homelab/       — Mentat-System, N8N, NAS, Skripte
  ├── school/        — SQL, PowerShell, Java, Windows Server
  ├── networking/    — OSI, Netzwerktechnik
  └── general/       — Linux-Rechte, Docker
```

### Palace initialisieren und befüllen

```bash
# Rooms aus Ordnerstruktur erkennen
mempalace --palace ~/mentat-palace init ~/mentat-knowledge

# Wissensdateien minen
mempalace --palace ~/mentat-palace mine ~/mentat-knowledge

# Gesprächslogs minen
mempalace --palace ~/mentat-palace mine ~/mentat-chats --mode convos

# Status prüfen
mempalace --palace ~/mentat-palace status
```

### Active Learning

Mentat analysiert nach jeder Session eigenständig das Gespräch und extrahiert erinnerungswürdige Fakten direkt ins Palace — ohne manuellen Mine-Lauf.

Damit das gut funktioniert werden die gelernten Fakten als zusammenhängende Paragraphen gespeichert, nicht als isolierte Einzelsätze. Das verbessert die ChromaDB-Similarity-Scores deutlich.

Beispiel eines generierten `learned_*.md` Eintrags:

```markdown
# Session Memory — 2026-04-24 20:14

## What Toni and Mentat discussed

Toni tested SQL injection (SQLi) on DVWA in a controlled environment and confirmed
the attack worked as expected. This indicates practical experience with SQLi techniques
applied to a vulnerable web application.
```

> Wichtig: `mentat-chats` und `mentat-knowledge` niemals löschen — sie sind Trainingsdaten für ein geplantes Fine-Tuning 2027.

---

## Externe HDD als primärer Storage

Da Palace, Chats und Knowledge kontinuierlich wachsen, lohnt sich eine externe HDD statt der SD-Karte als primärer Storage. SD-Karten haben begrenzte Schreibzyklen und können bei Dauerbetrieb irgendwann ausfallen.

### HDD formatieren

```bash
# Sicherstellen dass die HDD erkannt wird
lsblk

# Aushängen falls gemountet
sudo umount /dev/sdX2

# Mit ext4 formatieren
sudo mkfs.ext4 /dev/sdX2
```

### Mountpunkt einrichten

```bash
sudo mkdir -p /mnt/mentat-hdd
sudo mount /dev/sdX2 /mnt/mentat-hdd
```

### Daten auf HDD verschieben

```bash
sudo chown -R pi:pi /mnt/mentat-hdd

# Daten kopieren
rsync -av ~/mentat-palace ~/mentat-chats ~/mentat-knowledge /mnt/mentat-hdd/

# Originale sichern (nach Überprüfung löschen)
mv ~/mentat-palace ~/mentat-palace-backup
mv ~/mentat-chats ~/mentat-chats-backup
mv ~/mentat-knowledge ~/mentat-knowledge-backup

# Symlinks setzen
ln -s /mnt/mentat-hdd/mentat-palace ~/mentat-palace
ln -s /mnt/mentat-hdd/mentat-chats ~/mentat-chats
ln -s /mnt/mentat-hdd/mentat-knowledge ~/mentat-knowledge
```

### Auto-Mount beim Booten (fstab)

UUID der HDD auslesen:

```bash
sudo blkid /dev/sdX2
```

In `/etc/fstab` eintragen:

```
UUID=<DEINE-UUID> /mnt/mentat-hdd ext4 defaults,nofail 0 2
```

> `nofail` stellt sicher dass der Pi auch ohne HDD normal bootet.

### HDD-Gesundheit prüfen

```bash
sudo apt install smartmontools -y
sudo smartctl -H /dev/sda
```

Erwarteter Output: `SMART overall-health self-assessment test result: PASSED`

---

## Backup

### Automatisches NAS-Backup

Ein tägliches Backup-Script (`backup-mentat.sh`) sichert den Node vollständig auf das NAS. Da Palace, Chats und Knowledge über Symlinks auf die HDD zeigen, folgt rsync automatisch den Links — die HDD-Daten werden mit gesichert.

### Manuelles SD-Karten Image

Vor größeren Änderungen vollständiges SD-Karten Image erstellen:

- Tool: **Win32DiskImager** (Windows) oder **dd** (Linux)
- SD-Karte in den PC stecken
- Image lesen nach z.B. `~/Backups/mentat-ai-node-backup-DATUM.img`

> Lektion aus dem Pwnagotchi-Fiasko: **Immer vor größeren Änderungen ein Backup ziehen.**

---

## Aktueller Status

- ✅ Raspberry Pi OS Lite 64-bit (Trixie)
- ✅ SSH aktiv
- ✅ Tailscale aktiv
- ✅ Aktiver Kühler verbaut
- ✅ hailo-h10-all installiert
- ✅ Hailo-10H erkannt (FW 5.1.1)
- ✅ hailo-ollama als systemd Service
- ✅ qwen2.5-instruct:1.5b läuft (~8 Token/s)
- ✅ Docker 29.3.1 installiert
- ✅ N8N läuft auf Port 5678
- ✅ MemPalace 3.3.2 + ChromaDB 1.5.8
- ✅ Palace-Struktur: pentesting / homelab / school / networking / general
- ✅ Active Learning aktiv
- ✅ WD Elements 1TB HDD als primärer Storage (ext4, auto-mount, SMART: PASSED)
- ✅ Symlinks: mentat-palace / mentat-chats / mentat-knowledge → HDD
- ✅ NAS-Backup sichert HDD-Daten täglich mit

---

## Warum Hailo-10H?

Der Hailo-10H ist ein dedizierter Neural Processing Unit (NPU) mit 8GB eigenem RAM:

- LLM-Inferenz läuft auf dem HAT, Pi CPU bleibt frei
- Stromsparend — single-digit Watt
- Komplett offline, keine Cloud, keine Datenweitergabe
- Perfekt als always-on lokaler AI-Node im Heimnetz

---

> ⚠️ Dieses Repository enthält keine echten IPs, Tokens, Passwörter oder sensiblen Netzwerkdaten.
