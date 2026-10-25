# Abfahrtsbot (Telegram)

Verfolgt einzelne Fahrten und meldet Verspätungen, Änderungen und Ausfälle. Keine Abhängigkeiten, nur Python 3.9+.
Der Bot ruft Telegram selbst ab, du brauchst also weder offene Ports noch eine öffentliche Adresse.

## Einrichten

1. **Bot anlegen:** In Telegram `@BotFather` → `/newbot` → Token kopieren.
2. **Server:** Ubuntu- oder Debian-LXC-Container in Proxmox (512 MB RAM reichen). Darin:
   ```
   apt update && apt install -y python3 nano tzdata
   timedatectl set-timezone Europe/Berlin
   ```
3. **Installieren:** Im Repository `sudo ./install.sh` ausführen (legt Bot, Konfiguration und Dienst an).
4. **Token eintragen:** `nano /opt/abfahrtsbot/config.json`, danach `systemctl enable --now abfahrtsbot`.
5. Log ansehen: `journalctl -u abfahrtsbot -f`. Dort muss „Bot läuft.“ stehen.
6. In Telegram dem Bot `/start` schreiben. Wer als Erstes `/start` sendet, wird Besitzer, alle anderen werden abgewiesen.

Updates: `git pull && sudo ./install.sh`. `config.json` und `data.json` (Favoriten, Tracking) bleiben erhalten.

## Bedienung

| Befehl | Wirkung |
|---|---|
| `/suche Bocholt Bahnhof` | Haltestelle suchen, per Tippen als Favorit speichern |
| `/favoriten` | Liste deiner Haltestellen, antippen zeigt die nächsten 10 Abfahrten |
| `/abfahrten [Name]` | nächste 10 Abfahrten einer Favoriten- oder beliebigen Haltestelle |
| `/tracking` | verfolgte Fahrten, antippen zeigt den Stand und erlaubt „Tracking beenden“ |
| `/wiederkehrend` | wiederkehrende Fahrten (z. B. Mo–Fr 7:00), antippen zum Löschen |
| `/jetzt` | aktuelle Verspätungen an deinen Favoriten |
| `/pause`, `/weiter` | Benachrichtigungen aus/an |
| `/status` | Einstellungen und Zeit der letzten Prüfung |

Favoriten sind nur eine Liste für den schnellen Zugriff und melden nichts von selbst.
In einer Abfahrtsliste kann man eine Haltestelle unten als Favorit speichern oder entfernen.

## Tracking

Eine Fahrt in einer Abfahrtsliste antippen (🔔). Du bekommst sofort den Stand, danach:

- **Änderung:** wenn sich die Verspätung um mindestens 2 min gegenüber der letzten Meldung ändert
- **Erinnerung** genau 15 und 5 min vor der voraussichtlichen Abfahrt (der Bot wacht dafür zum exakten Zeitpunkt auf)
- **Ausfall**

Abgefahrene Fahrten verschwinden automatisch aus der Liste.

## Wiederkehrende Fahrten

Eine Fahrt antippen (Tracking), unter dem Stand auf **🔁 Wiederholen** tippen und die Wochentage wählen (Voreinstellung Mo–Fr), dann **Speichern**.
An den gewählten Tagen sucht der Bot die Fahrt (gleiche Linie, gleiches Ziel, gleiche Uhrzeit) und verfolgt sie ab 60 min vor Abfahrt automatisch
(`routine_lead_min` in `config.json`): Stand sofort, danach Änderungen um 2 min und Erinnerungen 15 und 5 min vor Abfahrt.
Findet er die Fahrt kurz vor Abfahrt nicht mehr in der Abfahrtsliste, warnt er einmal (z. B. bei Ausfall).

## Verbindungen teilen

Unter dem Stand einer verfolgten Fahrt auf **🔗 Teilen** tippen. Der Bot schickt eine Zeile `/code ABC234` (Antippen kopiert sie ganz).
Wer sie dem Bot schickt, sieht die Verbindung mit aktuellem Stand und kann sie aktualisieren oder selbst verfolgen (🔔).
Der Code gilt bis 10 min nach der (verspäteten) Abfahrt und wird dann gelöscht. Wer einen Code eingelöst hat, darf nur geteilte
Verbindungen ansehen und verfolgen (`/code`, `/tracking`, `/pause`, `/weiter`, `/status`), nicht suchen, Favoriten oder wiederkehrende Fahrten anlegen.
Nach 10 falschen Codes pro Stunde sperrt der Bot weitere Versuche dieses Nutzers.

## Weitere Personen einladen

Der Besitzer schickt `/code` ohne Code und bekommt eine Zeile `/code ABCD2345`. Wer sie dem Bot schickt, wird Mitglied
und darf alles außer `/mitglieder` (eigene Favoriten, Tracking, wiederkehrende Fahrten, eigener Chat-Zustand).
Der Einladungscode gilt 24 Stunden und nur für eine Person; der Besitzer bekommt eine Meldung, wenn er eingelöst wird.
`/mitglieder` zeigt alle Eingeladenen und entfernt Zugänge (inklusive deren Favoriten und Tracking).

## Website (Abfahrtstafel unter eigener Adresse)

`install.sh` richtet auf dem Container auch **Caddy** ein. Er liefert die Abfahrtstafel (`/var/www/bahnhofstafel/index.html`) aus,
leitet `/api` an die kleine HTTP-API des Bots weiter (`api_port`, Standard 8787, nur lokal) und holt das HTTPS-Zertifikat automatisch (Let's Encrypt).
Seite und API haben dieselbe Adresse, eine CORS-Einstellung ist nicht nötig.

Standardmäßig lauscht Caddy nur lokal auf Port 8080 (`http://<Container-IP>:8080`). Von außen erreichbar machen:

**Ohne Router-Zugriff: Tailscale Funnel** (kostenlos, feste Adresse `https://<name>.<tailnet>.ts.net`, keine Portfreigabe, keine eigene Domain)

1. `curl -fsSL https://tailscale.com/install.sh | sh` und `tailscale up`, den angezeigten Link im Browser bestätigen.
   Meldet Tailscale, dass `/dev/net/tun` fehlt: in `/etc/default/tailscaled` `FLAGS="--tun=userspace-networking"` setzen und `systemctl restart tailscaled`.
2. Unter login.tailscale.com/admin/dns MagicDNS und HTTPS Certificates einschalten.
3. `tailscale funnel --bg 8080` gibt die Adresse aus (`tailscale funnel status` zeigt sie wieder). Das bleibt nach einem Neustart erhalten.

**Mit eigener Domain und Router-Zugriff**

1. **DNS:** Eine Subdomain, z. B. `bahnhofstafel.deine-domain.de`, als A-Record auf die öffentliche IP des Heimanschlusses
   (bei wechselnder IP per DynDNS). Ohne eigene öffentliche IPv4 (CGNAT) geht das nicht.
2. **Router:** Ports 80 und 443 auf den Container weiterleiten (80 braucht Caddy für das Zertifikat).
3. **Adresse eintragen:** `nano /etc/caddy/Caddyfile`, die Zeile `:8080 {` durch die Subdomain ersetzen (`bahnhofstafel.deine-domain.de {`),
   dann `systemctl restart caddy`. Caddy holt das Zertifikat automatisch. Spätere Updates mit `install.sh` lassen eine angepasste Caddyfile unverändert.

Updates der Seite kommen mit `sudo ./install.sh`.
Was die Seite mit dem Bot kann: Favoriten synchronisieren, Fahrten verfolgen (Zeile antippen, 🔔) und verfolgte Fahrten anzeigen.
Anmelden dürfen sich nur Besitzer und eingeladene Mitglieder: „Mit Telegram anmelden“ öffnet den Bot, dort mit „Ja, anmelden“ bestätigen.
Die Anmeldung gilt 30 Tage; wer aus `/mitglieder` entfernt wird, verliert sie sofort.
Beim ersten Anmelden werden die Favoriten aus dem Browser mit denen des Bots zusammengeführt. Wiederkehrende Fahrten legt man weiter im Bot an,
die Website zeigt sie nur an. Bleibt `BOT_API` in `index.html` leer, arbeitet die Seite ohne Anmeldung wie bisher.

Läuft die Seite woanders (z. B. bei einem Webhoster), braucht die API eine eigene HTTPS-Adresse, `BOT_API` wird auf
diese Adresse gesetzt und `web_origin` in `config.json` auf die Adresse der Seite. Ohne `web_origin` sendet die API keine CORS-Header (nur gleiche Adresse).

Weitere Einstellungen in `config.json` bzw. Umgebungsvariablen: `owner_id`/`BOT_OWNER_ID` (Besitzer festlegen), `trust_proxy`/`BOT_TRUST_PROXY`
(Client-IP aus `X-Real-IP` lesen, nur hinter eigenem Proxy), `BOT_API_HOST`, `BOT_DATA_FILE`, `BOT_TOKEN`.

## Hinweise

- Verspätungen gibt es nur für Linien, für die Transitous Echtzeitdaten hat.
- Die Favoriten des Bots sind getrennt von denen im Browser (diese liegen nur im Browser).
- Einstellungen und Zustand liegen in `data.json`, die Konfiguration in `config.json`.
