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

Unter dem Eingabefeld erscheint ein Menü mit Tasten (nach `/start` oder `/hilfe`). Man muss die Befehle nicht tippen.
Die Tasten hängen von der Rolle ab, es erscheinen nur Tasten, die man auch nutzen darf. Bei "Suche" und "Code eingeben" fragt der Bot nach dem Namen oder
dem Code, die nächste Nachricht ist die Antwort (wartet 5 Minuten). Ein Befehl mit `/` bricht die Frage ab. Die Befehle unten funktionieren weiterhin.

| Befehl | Wirkung |
|---|---|
| `/suche Bocholt Bahnhof` | Haltestelle suchen, per Tippen als Favorit speichern |
| `/favoriten` | Liste deiner Haltestellen, antippen zeigt die nächsten 10 Abfahrten |
| `/abfahrten [Name]` | nächste 10 Abfahrten einer Favoriten- oder beliebigen Haltestelle |
| `/tracking` | verfolgte Fahrten, antippen zeigt den Stand und erlaubt „Tracking beenden“ |
| `/wiederkehrend` | wiederkehrende Fahrten (z. B. Mo–Fr 7:00), antippen zum Löschen |
| `/jetzt` | aktuelle Verspätungen an deinen Favoriten |
| `/pause`, `/weiter` | Benachrichtigungen aus/an |
| `/menue` | Tasten-Menü wieder anzeigen. Die Taste "Ausblenden" blendet es aus, mit dem Tastatur-Symbol im Eingabefeld klappt man es ein und aus |
| `/clear` | Chatverlauf löschen (Telegram erlaubt nur Nachrichten der letzten 48 Stunden). Favoriten und Tracking bleiben |
| `/autoclear` | Nachrichten automatisch nach 47 Stunden löschen, an oder aus (`/autoclear an`, `/autoclear aus`, oder über die Taste). Gilt ab dem Einschalten |
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

## Rollen und der Verwalter

| Rolle | Wer | Wie wird man es |
|---|---|---|
| **Verwalter** | die Person, die den Bot technisch betreibt und den Server besitzt | nur über `VERWALTER_ID` (oder `verwalter_id` in `config.json`) |
| **Besitzer** | Chef im Bot, verwaltet Inhalte und Mitglieder | `BOT_OWNER_ID` bzw. `owner_id` (sonst der Erste mit `/start`) |
| **Mitglied** | eingeladene Person | Einladungscode (`/code`) |
| **Einlader** | Mitglied, das selbst Einladungscodes erzeugen darf | Besitzer oder Verwalter schaltet es in `/mitglieder` frei |
| **Gast** | sieht nur eine geteilte Verbindung | Code einer geteilten Verbindung |

Der Besitzer ist eine andere Person als der Verwalter und behält seine normalen Rechte im Bot.

| Aktion | Verwalter | Besitzer | Mitglied | Gast |
|---|---|---|---|---|
| Suchen, Favoriten, Tracking, wiederkehrende Fahrten | ja | ja | ja | nein |
| Geteilte Verbindung ansehen und verfolgen | ja | ja | ja | ja |
| Einladungscodes erzeugen | ja | ja | nur als Einlader (höchstens 5 offene Codes) | nein |
| `/mitglieder`, Mitglieder entfernen, Einladen erlauben oder entziehen | ja | ja | nein | nein |
| Braucht eine Einladung | nein | nein | ja | ja |
| Kann entfernt, gesperrt oder eingeschränkt werden | **nie** | nur durch den Betreiber am Server | durch Besitzer und Verwalter | durch Ablauf des Codes |
| Server, Konfiguration, Token, Dienste, Logs | nur am Server selbst | nein | nein | nein |

Eine Person, die ein Einlader einlädt, wird ein normales Mitglied, kein Einlader. Entzieht man einem Einlader die Erlaubnis oder entfernt ihn, werden seine offenen Codes ungültig.
Besitzer und Verwalter bekommen eine Meldung, wenn jemand über einen Einlader dazukommt.

### Eigene Telegram-Nummer herausfinden

Schreibe in Telegram dem Bot `@userinfobot`. Er antwortet mit deiner ID, einer Zahl wie `123456789`.

### VERWALTER_ID setzen

- **systemd:** In `/etc/systemd/system/abfahrtsbot.service` unter `[Service]` die Zeile `Environment=VERWALTER_ID=123456789` ergänzen, dann `systemctl daemon-reload && systemctl restart abfahrtsbot`.
  Alternativ eine Datei mit `EnvironmentFile=` nutzen, die nur für root lesbar ist.
- **Coolify / Docker Compose:** Unter Environment Variables `VERWALTER_ID` mit der Zahl anlegen und neu deployen.
- **config.json:** `"verwalter_id": 123456789`. Die Umgebungsvariable hat Vorrang. Ist beides leer oder keine gültige Zahl, gibt es keinen Verwalter.

Beim Start schreibt der Bot nur "Verwalter gesetzt: ja" oder "nein" ins Log, nie die ID.

### Regeln für spätere Änderungen

1. Der Verwalter wird ausschließlich über `VERWALTER_ID` (oder `verwalter_id`) bestimmt. Kein Befehl, Knopf oder API-Aufruf vergibt, ändert oder entzieht die Rolle, auch nicht der Besitzer. Nichts davon steht in `data.json`.
2. Der Verwalter hat immer mindestens alle Rechte des Besitzers, auch bei künftigen Befehlen, Knöpfen und Web-API-Funktionen. Jede neue Rechteprüfung muss ihn einschließen (über `is_admin`, `is_full` und `may_use`, nie nur `is_owner`).
3. Er kann nicht entfernt, gesperrt, pausiert, stummgeschaltet oder eingeschränkt werden. Entfernen- und Sperr-Funktionen lehnen seine ID serverseitig ab. Seine Website-Sitzungen werden nie beendet, wenn andere entfernt werden.
4. Besitzer, Mitglieder und Gäste bekommen keine Funktion, die Server, Konfiguration oder Betrieb betrifft: kein Ändern von `config.json`, Umgebungsvariablen, `VERWALTER_ID`, Token, Ports, Dateien oder Diensten, kein Neustarten, Updaten oder Ausführen von Befehlen, kein Auslesen von Konfiguration, Token oder Logs. Neue Befehle dieser Art kommen nur in `VERWALTER_COMMANDS`.
5. Der Bot gibt Token, Verwalter-ID und Dateipfade nie in Nachrichten, Fehlermeldungen oder API-Antworten aus.
6. Der Betreiber behält per Server immer die Kontrolle (`data.json` und `config.json` bearbeiten, Dienst neu starten). Der Bot nimmt ihm nie Daten oder Rechte weg.
7. Alle Zugriffsprüfungen laufen serverseitig im Bot, nie allein auf Basis von Callback-Daten.

### Test

`python3 bot/test_rechte.py` prüft diese Regeln. Er leitet Befehle und Knopf-Aktionen automatisch aus dem Code ab und schlägt fehl, wenn eine neue Funktion den Verwalter ausschließt.
**Der Test muss vor jedem Commit laufen.**

### Auto-Löschen

Pro Chat einschaltbar (Taste "Auto-Löschen" oder `/autoclear an`). Telegram erlaubt Bots nur, Nachrichten der letzten 48 Stunden zu löschen, deshalb löscht der Bot nach 47 Stunden.
Dafür merkt er sich die Nachrichten-IDs und Zeitpunkte der Chats, die es eingeschaltet haben (nicht den Inhalt), höchstens 5000 pro Chat, in `data.json` unter `msglog`.
Ausgeschaltet werden keine IDs gespeichert. Ein Hintergrunddienst prüft alle 5 Minuten, was fällig ist.

