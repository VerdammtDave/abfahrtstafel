# Abfahrtstafel + Telegram-Bot: Einrichtung mit Router (Portfreigabe)

Ziel: Die Abfahrtstafel ist unter `https://bahnhofstafel.DEINE-DOMAIN.de` erreichbar und mit dem Telegram-Bot verbunden.
Alles läuft in **einem** Proxmox-Container zu Hause. Der Container enthält den Bot, die Webseite und den Webserver Caddy
(holt das HTTPS-Zertifikat automatisch).

```
Internet → Router (Port 80/443) → Container: Caddy → Seite (index.html)
                                                   → /api → Bot (Port 8787, nur lokal)
```

## Vorab prüfen

- [ ] Der Anschluss hat eine **öffentliche IPv4-Adresse**. Test: Die WAN-/Internet-IP im Router mit der Adresse vergleichen, die
      `curl ifconfig.me` im Container ausgibt. Sind sie verschieden oder beginnt die Router-IP mit `100.64.` bis `100.127.`, liegt CGNAT vor.
      Dann geht diese Anleitung nicht. Alternative: Cloudflare Tunnel (Domain muss bei Cloudflare liegen) oder Tailscale Funnel.
- [ ] Zugriff auf die DNS-Verwaltung der Domain.
- [ ] Zugriff auf die Router-Einstellungen (Portfreigaben).
- [ ] Bot-Token von `@BotFather` (Telegram) liegt bereit.

## 1. Container vorbereiten

Ubuntu- oder Debian-LXC in Proxmox (512 MB RAM reichen), Konsole öffnen:

```
apt update && apt install -y python3 nano curl git tzdata
timedatectl set-timezone Europe/Berlin
```

**Feste interne Adresse:** Der Container braucht immer dieselbe IP im Heimnetz. Entweder im Router eine DHCP-Reservierung für den Container
anlegen, oder in Proxmox unter Container → Netzwerk eine statische IP eintragen. Diese Adresse in Schritt 4 verwenden.

## 2. Bot und Webseite installieren

1. Repository holen und Installer starten (`apt install -y git` falls nötig):
   `git clone https://github.com/VerdammtDave/abfahrtstafel.git && cd abfahrtstafel && sudo ./install.sh`. Das Skript legt an:
   Bot (`/opt/abfahrtsbot`, läuft als eigener Benutzer), Webseite (`/var/www/bahnhofstafel`), Caddy und die Dienste.
2. Token eintragen: `nano /opt/abfahrtsbot/config.json`, bei `bot_token` den Token von BotFather einsetzen.
3. Bot starten: `systemctl enable --now abfahrtsbot`
4. Log prüfen: `journalctl -u abfahrtsbot -f`. Dort muss **„Web-API auf 127.0.0.1:8787“** und **„Bot läuft.“** stehen.
5. In Telegram dem Bot `/start` schreiben. **Wer als Erstes `/start` sendet, wird Besitzer** (Admin). Das sollte der Bot-Betreiber sein.

Lokaler Test im Heimnetz: `http://<Container-IP>:8080` zeigt die Abfahrtstafel.

## 3. DNS-Eintrag

Bei der Domain-Verwaltung einen Eintrag anlegen:

| Typ | Name | Wert |
|---|---|---|
| A | `bahnhofstafel` | öffentliche IP des Anschlusses |

**Wechselt die IP** (bei Privatanschlüssen üblich), stattdessen DynDNS verwenden:
- Fritz!Box: Internet → Freigaben → DynDNS, Anbieter z. B. DuckDNS (kostenlos), man bekommt eine Adresse wie `xyz.duckdns.org`.
  Bei der Domain dann statt des A-Records einen **CNAME** `bahnhofstafel` → `xyz.duckdns.org` anlegen.
- Andere Router oder Domain-Anbieter mit eigener DynDNS-Funktion: deren Anleitung nutzen.

Prüfen (nach einigen Minuten): `nslookup bahnhofstafel.DEINE-DOMAIN.de` muss die richtige IP liefern.

## 4. Portfreigabe im Router

Zwei Weiterleitungen (TCP) auf die interne IP des Containers:

| Externer Port | Interner Port | Ziel |
|---|---|---|
| 80 | 80 | Container |
| 443 | 443 | Container |

Port 80 braucht Caddy, um das HTTPS-Zertifikat zu holen und zu erneuern. Nicht weglassen.

Fritz!Box: Internet → Freigaben → Portfreigaben → „Gerät für Freigaben hinzufügen“ → Container wählen → „Neue Freigabe“
→ Portfreigabe, Protokoll TCP, Port 80 an Port 80 → danach dasselbe für 443. Dort bei IPv4 „Zugriff aus dem Internet erlauben“ aktivieren.
Ist die Proxmox-Firewall eingeschaltet, müssen 80 und 443 für den Container erlaubt sein.

## 5. Adresse in Caddy eintragen

```
nano /etc/caddy/Caddyfile
```

Die erste Zeile `:8080 {` ersetzen durch die Adresse:

```
bahnhofstafel.DEINE-DOMAIN.de {
```

Der Rest bleibt unverändert. Dann: `systemctl restart caddy`.
Das Zertifikat wird beim ersten Aufruf automatisch geholt (Log: `journalctl -u caddy -f`).
Schlägt es fehl, sind meist DNS-Eintrag (Schritt 3) oder Port 80 (Schritt 4) falsch.

## 6. Testen

1. Von **außerhalb des WLANs** (z. B. Handy über mobile Daten) `https://bahnhofstafel.DEINE-DOMAIN.de` öffnen.
   Aus dem Heimnetz klappt das mit manchen Routern nicht (kein NAT-Loopback), das ist dann kein Fehler.
2. Oben auf „Mit Telegram anmelden“ klicken, in Telegram „Ja, anmelden“ bestätigen. Es erscheint der Name und der Tab „🔔 Tracking“.
3. Eine Haltestelle suchen und in der Tafel auf eine Fahrt klicken (🔔). Der Bot meldet sich in Telegram mit dem Stand.

## Weitere Personen zulassen

- Admin schickt dem Bot `/code` und gibt die erhaltene Zeile (`/code ABCD2345`) weiter. Die Person schickt sie dem Bot und ist dann
  Mitglied (Code: 24 h gültig, einmal nutzbar). Danach kann sie sich auch auf der Website anmelden.
- `/mitglieder` (nur Admin): Eingeladene ansehen und entfernen. Entfernte verlieren auch die Website-Anmeldung.
- Nicht eingeladene Personen können auf der Seite nichts verfolgen und sehen keine Daten anderer.

## Wartung

| Aufgabe | Befehl |
|---|---|
| Bot-Log | `journalctl -u abfahrtsbot -f` |
| Bot neu starten | `systemctl restart abfahrtsbot` |
| Web-Log | `journalctl -u caddy -f` |
| Update (Bot und Seite) | `git pull && sudo ./install.sh`. `config.json`, `data.json` und die angepasste `Caddyfile` bleiben erhalten |
| Daten und Einstellungen | `/opt/abfahrtsbot/data.json`, `/opt/abfahrtsbot/config.json` |

## Fehlersuche

| Problem | Ursache / Lösung |
|---|---|
| Seite lädt nicht | DNS (`nslookup`), Portfreigabe 80/443, Container-IP noch dieselbe? `systemctl status caddy` |
| Zertifikatsfehler | Port 80 nicht erreichbar oder DNS zeigt auf falsche IP. `journalctl -u caddy` |
| „Mit Telegram anmelden“ zeigt „Bot nicht erreichbar“ | Bot läuft nicht oder `api_port` in `config.json` ist 0. `systemctl status abfahrtsbot` |
| Anmeldelink „abgelaufen“ | Der Link gilt 10 Minuten. Auf der Website neu auf „Mit Telegram anmelden“ klicken |
| Nach Router-Neustart weg | IP hat gewechselt und DynDNS aktualisiert nicht, oder Container hat eine andere interne IP bekommen |

## Sicherheit

- `config.json` enthält den Bot-Token und darf nicht weitergegeben oder in Chats gepostet werden. Ist er bekannt geworden: bei `@BotFather`
  `/revoke` ausführen und den neuen Token eintragen.
- Im Router **nur** 80 und 443 an den Container weiterleiten, nicht 8787 (die Bot-API ist absichtlich nur lokal erreichbar).
- Ohne Telegram-Anmeldung eines Besitzers oder eingeladenen Mitglieds liefert die API keine Daten.
