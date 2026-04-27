# Nextcloud auf Ubuntu 24.04 mit Let's Encrypt

Nextcloud ist eine selbst gehostete private Cloud — eigene Dateien, Kalender, Kontakte, Notizen. Kein Google Drive, kein OneDrive, keine Drittanbieter. Du hostest es selbst, du hast die Kontrolle.

---

## Voraussetzungen

- Ubuntu 24.04 LTS Server (VPS oder lokal)
- Domain oder Subdomain (z.B. `meinserver.example.com`)
- Root-Zugriff

---

## 1. System vorbereiten

Erstmal alles aktuell halten — Pflicht vor jeder Installation.

```bash
sudo apt update && sudo apt upgrade -y
```

---

## 2. Webserver, Datenbank und PHP installieren

Nextcloud braucht einen sogenannten **LAMP-Stack** — das ist die Kombination aus:
- **L**inux (läuft schon)
- **A**pache — der Webserver, der die Seite ausliefert
- **M**ariaDB — die Datenbank, in der Nextcloud alle Daten speichert (Benutzer, Dateien, Einstellungen)
- **P**HP — die Programmiersprache in der Nextcloud geschrieben ist

Die ganzen `php-*` Module sind Erweiterungen die Nextcloud für bestimmte Funktionen braucht (Bilder, ZIP, XML usw.).

```bash
sudo apt install apache2 mariadb-server php php-mysql php-gd php-curl php-zip \
php-xml php-mbstring php-intl php-bcmath php-imagick libapache2-mod-php unzip -y
```

---

## 3. Dienste starten & beim Boot aktivieren

Apache (Webserver) und MariaDB (Datenbank) müssen laufen — und beim nächsten Neustart automatisch wieder starten.

```bash
sudo systemctl start apache2 mariadb
sudo systemctl enable apache2 mariadb
```

---

## 4. Datenbank einrichten

Nextcloud braucht eine eigene Datenbank mit einem eigenen Benutzer — nicht einfach root verwenden.

```bash
sudo mysql -u root
```

```sql
-- Datenbank für Nextcloud anlegen
CREATE DATABASE nextcloud;

-- Eigenen Datenbankbenutzer anlegen
CREATE USER 'nextcloud'@'localhost' IDENTIFIED BY 'StarkesPasswort!';

-- Dem Benutzer alle Rechte auf die Nextcloud-Datenbank geben
GRANT ALL ON nextcloud.* TO 'nextcloud'@'localhost';

-- Rechte neu laden
FLUSH PRIVILEGES;
EXIT;
```

> ⚠️ Starkes Passwort verwenden — der Server ist öffentlich erreichbar!

---

## 5. Nextcloud herunterladen

Nextcloud wird direkt vom offiziellen Server gezogen und in den Apache-Webroot entpackt (`/var/www/html/` ist der Ordner den Apache standardmäßig nach außen gibt).

`chown` übergibt den Ordner an `www-data` — das ist der Benutzer unter dem Apache läuft. Ohne das kann Apache die Dateien nicht lesen.

```bash
cd /var/www/html
sudo wget https://download.nextcloud.com/server/releases/latest.zip
sudo unzip latest.zip
sudo chown -R www-data:www-data nextcloud/
```

---

## 6. Apache Virtual Host konfigurieren

Ein **Virtual Host** sagt Apache: "Wenn jemand auf diese Domain kommt, zeig ihm diesen Ordner." Ohne das weiß Apache nicht wo Nextcloud liegt.

```bash
sudo nano /etc/apache2/sites-available/nextcloud.conf
```

Inhalt:

```apache
<VirtualHost *:80>
    DocumentRoot /var/www/html/nextcloud
    ServerName DEINE_DOMAIN

    <Directory /var/www/html/nextcloud>
        Require all granted
        AllowOverride All
        Options FollowSymLinks MultiViews
    </Directory>
</VirtualHost>
```

Config aktivieren und benötigte Apache-Module einschalten:

```bash
sudo a2ensite nextcloud.conf
sudo a2enmod rewrite headers env dir mime
sudo systemctl restart apache2
```

---

## 7. SSL mit Let's Encrypt (HTTPS)

HTTP ist unverschlüsselt — Passwörter würden im Klartext übertragen. **Certbot** holt automatisch ein kostenloses SSL-Zertifikat von Let's Encrypt und konfiguriert Apache für HTTPS. Das Zertifikat wird alle 90 Tage automatisch erneuert.

```bash
sudo apt install certbot python3-certbot-apache -y
sudo certbot --apache -d DEINE_DOMAIN
```

---

## 8. Nextcloud Web-Setup

Browser: `https://DEINE_DOMAIN`

Ausfüllen:
- **Admin-Benutzername:** kein `admin` — zu offensichtlich, erster Angriffspunkt bei Brute Force
- **Admin-Passwort:** stark
- **Database user:** `nextcloud`
- **Database password:** dein gewähltes Passwort
- **Database name:** `nextcloud`
- **Database host:** `localhost`

→ **Install** klicken.

---

## 9. Handy verbinden (iPhone / Android)

Nextcloud App aus dem App Store installieren → Server-URL eingeben → Login. Dateien synchronisieren sich automatisch.

---

## Hinweise

- Datenbankpasswort geändert? Dann auch in `/var/www/html/nextcloud/config/config.php` anpassen (`dbpassword`)
- Neue Benutzer anlegen: oben rechts → Administration → Benutzer
- Selbstregistrierung ist standardmäßig deaktiviert — für private Server so lassen

---

## Getestet mit

- Ubuntu 24.04 LTS
- Nextcloud 33.0.2
- Apache 2.4
- MariaDB 10.11
- Certbot / Let's Encrypt
