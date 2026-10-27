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
| `/suche Berlin Hauptbahnhof` | Haltestelle suchen, per Tippen als Favorit speichern |
| `/favoriten` | Liste deiner Haltestellen, antippen zeigt die nächsten 10 Abfahrten |
| `/abfahrten [Name]` | nächste 10 Abfahrten einer Favoriten- oder beliebigen Haltestelle |
| `/tracking` | verfolgte Fahrten, antippen zeigt den Stand und erlaubt „Tracking beenden“ |
| `/wiederkehrend` | wiederkehrende Fahrten (z. B. Mo–Fr 7:00), antippen zum Löschen |
| `/jetzt` | aktuelle Verspätungen an deinen Favoriten |
| `/pause`, `/weiter` | Benachrichtigungen aus/an |
| `/menue` | Tasten-Menü wieder anzeigen. Die Taste "Ausblenden" blendet es aus, mit dem Tastatur-Symbol im Eingabefeld klappt man es ein und aus |
| `/clear` | Chatverlauf löschen (Telegram erlaubt nur Nachrichten der letzten 48 Stunden). Favoriten und Tracking bleiben |
| `/autoclear` | Nachrichten automatisch löschen, an oder aus (`/autoclear an`, `/autoclear aus`, oder über die Taste). Gilt ab dem Einschalten, die Zeit stellt man in `/einstellungen` ein (Standard 47 Stunden) |
| `/einstellungen` | Menü mit Tasten: Anzeigename, automatisches Löschen (3 bis 47 Stunden) und die zwei Erinnerungen vor einer Fahrt. Gilt für alle, die den Bot nutzen dürfen, auch Gäste |
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
`/mitglieder` zeigt alle Eingeladenen und entfernt Zugänge (inklusive deren Favoriten, Tracking und Einstellungen). Dort gibt es auch die Knöpfe
„Neue Einladung“, „Offene Einladungen“ (mit Widerrufen und den zuletzt erledigten) und pro Person „Details“. Dasselbe gibt es auf der Website unter
Einstellungen, Mitgliederverwaltung (Besitzer, Admins und Verwalter).
Eine Einladung gibt nur den Mitglieder-Zugang, nie Verwaltungsrechte. Sie ist 24 Stunden gültig, einmal einlösbar und lässt sich widerrufen.
Widerrufene, abgelaufene und eingelöste Codes funktionieren nicht mehr. Der Verlauf (ohne Codes) steht in `data.json` unter `invite_hist`.
Admins und Verwalter widerrufen nur eigene Einladungen und die von Personen unter sich.

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

## Rollen

| Rolle | Wer | Wie wird man es |
|---|---|---|
| **Besitzer** | gehört der Bot, genau eine Person | `BOT_OWNER_ID` bzw. `owner_id` (sonst der Erste mit `/start`) |
| **Admin** | vom Besitzer ernannt, verwaltet Rollen unter sich | der Besitzer wählt die Rolle in `/mitglieder` oder auf der Website |
| **Verwalter** | lädt ein und entzieht Mitgliedern und Gästen den Zugang | Besitzer oder Admin wählt die Rolle |
| **Mitglied** | eingeladene Person | Einladungscode (`/code`) |
| **Gast** | sieht nur per Code geteilte Verbindungen | Code einer geteilten Verbindung |

Jede Rolle ändert nur Rollen unter sich. Es gibt keine zweite Besitzer-Rolle, und die Rolle Besitzer lässt sich nicht vergeben.

| Aktion | Besitzer | Admin | Verwalter | Mitglied | Gast |
|---|---|---|---|---|---|
| Suchen, Favoriten, Tracking, wiederkehrende Fahrten, Einstellungen | ja | ja | ja | ja | nein |
| Geteilte Verbindung per `/code` ansehen und verfolgen | ja | ja | ja | ja | ja |
| Einladungscodes erzeugen | ja | ja (höchstens 5 offene Codes) | ja (höchstens 5 offene Codes) | nein | nein |
| Einladungen widerrufen | alle | eigene und die von Personen unter sich | eigene und die von Personen unter sich | nein | nein |
| `/mitglieder`, Mitglieder und Gäste rauswerfen | alle | alle unter sich | Mitglieder und Gäste | nein | nein |
| Rollen ändern (Dropdown, `/mitglieder`) | alle | Verwalter, Mitglied und Gast | nein | nein | nein |
| Admins ernennen oder entlassen | ja | nein | nein | nein | nein |
| Website-Anmeldung | ja | ja | ja | ja | nein |
| Server, Konfiguration, Token, Dienste, Logs | nur am Server selbst | nein | nein | nein | nein |

Eine Person, die ein Admin oder Verwalter einlädt, wird Mitglied. Es wird festgehalten, wer wen eingeladen hat (`invited_by`, sichtbar in `/mitglieder`, bei den Details und auf der Website).
Entzieht man einem Admin oder Verwalter die Rolle oder wirft ihn raus, werden seine offenen Codes ungültig. Der Besitzer bekommt eine Meldung, wenn jemand über einen Admin oder Verwalter dazukommt.

**Umstellung auf diese Rollen:** Die frühere Rolle Verwalter heißt jetzt Admin (`migrate()` übernimmt sie aus `data["verwalter"]` nach `data["admins"]`). Den früheren Server-Verwalter über `VERWALTER_ID` gibt es nicht mehr, die Einstellung wird ignoriert (der Bot schreibt beim Start einen Hinweis ins Log).
Wer nur darüber Zugang hatte, hat keinen mehr und muss neu eingeladen werden. Das frühere „Einladen erlauben“ für Mitglieder ist ebenfalls entfallen.

### Eigene Telegram-Nummer herausfinden

Schreibe in Telegram dem Bot `@userinfobot`. Er antwortet mit deiner ID, einer Zahl wie `123456789`.

### Regeln für spätere Änderungen

1. Es gibt genau einen Besitzer. Er wird nur über `BOT_OWNER_ID` (oder `owner_id`) bestimmt, sonst ist es die erste Person mit `/start`. Kein Befehl, Knopf oder API-Aufruf macht jemanden zum Besitzer, ersetzt oder entfernt ihn.
2. Admins ernennt und entlässt nur der Besitzer. Ein Admin kann den Besitzer und andere Admins nicht entfernen, nicht ändern und deren Einladungen nicht widerrufen. Jede Rolle ändert nur Rollen unter sich (zentral `rang`, `can_touch`, `allowed_roles`).
3. Betrieb (Server, `config.json`, Umgebung, Token, Ports, Dateien, Dienste, Neustart, Logs) gehört dem Besitzer. Der Bot hat keine Betriebsfunktion (es gibt kein `/restart` mehr). Käme je eine neue, gehört sie in `BETRIEB_COMMANDS` oder `BETRIEB_ACTIONS` und darf nur der Besitzer.
4. Mitglieder nutzen den Bot, sonst nichts. Gäste können nur per `/code` erhaltene Verbindungen ansehen und verfolgen.
5. Der Bot gibt Bot-Token, Besitzer-ID und Dateipfade nie in Nachrichten, Fehlermeldungen oder API-Antworten aus.
6. Der Betreiber behält per Server immer die Kontrolle (`data.json` und `config.json` bearbeiten, Dienst neu starten). Der Bot nimmt ihm nie Daten oder Rechte weg.
7. Alle Zugriffsprüfungen laufen serverseitig im Bot, nie allein auf Basis von Callback-Daten. Rechte immer über `may_use`, `is_manager` oder `rang`, nicht über `is_owner`.

### Test

`python3 bot/test_rechte.py` prüft diese Regeln. Er leitet Befehle und Knopf-Aktionen automatisch aus dem Code ab und schlägt fehl, wenn eine neue Funktion die Rollen verletzt (z. B. ein Gast bekommt mehr als das Verfolgen, ein Verwalter den Neustart oder ein Rang wird übersprungen).
**Der Test muss vor jedem Commit laufen.**

### Auto-Löschen

Pro Chat einschaltbar (Taste "Auto-Löschen" oder `/autoclear an`). Telegram erlaubt Bots nur, Nachrichten der letzten 48 Stunden zu löschen, deshalb ist 47 Stunden der Standard und zugleich das Höchste, was einstellbar ist.
Die Zeit stellt jede Person für sich ein (3 bis 47 Stunden, `autoclear_h` im Chat-Eintrag). Der Hintergrunddienst liest sie bei jedem Durchlauf neu,
eine Änderung gilt deshalb ohne Neustart und ohne Timer für alle bereits gemerkten Nachrichten (kürzer: ältere werden beim nächsten Durchlauf gelöscht).
Dafür merkt er sich die Nachrichten-IDs und Zeitpunkte der Chats, die es eingeschaltet haben (nicht den Inhalt), höchstens 5000 pro Chat, in `data.json` unter `msglog`.
Ausgeschaltet werden keine IDs gespeichert. Ein Hintergrunddienst prüft alle 5 Minuten, was fällig ist.

## Persönliche Einstellungen

Gespeichert pro Person in `data.json` unter `chats.<Chat-ID>`. Website (Einstellungen) und Telegram (`/einstellungen`) lesen und schreiben dieselben Werte,
eine Änderung gilt also überall sofort. Jede Person ändert nur ihre eigenen Werte, die Chat-ID kommt immer aus der Sitzung oder dem Telegram-Chat, nie aus der Anfrage.

| Einstellung | Feld | Standard | Gültig |
|---|---|---|---|
| Anzeigename | `nick` | keiner | bis 30 Zeichen, ohne Steuerzeichen. Nur für die Anrede, nie für Rechte oder Anmeldung |
| Auto-Löschen an/aus | `autoclear` | aus | |
| Löschen nach | `autoclear_h` | 47 Stunden | 3 bis 47 (Telegram erlaubt dem Bot nur 48) |
| Erste und zweite Erinnerung | `remind` = `[erste, zweite]` | 15 und 5 Minuten | je 1 bis 120 oder aus, die erste muss mehr Minuten haben als die zweite |

- Fehlt ein Feld, gilt der Standard. Bestehende Nutzer behalten dadurch ihr bisheriges Verhalten. `migrate()` läuft bei jedem Start, ist wiederholbar,
  ändert keine gültigen Daten und entfernt nur ungültige gespeicherte Werte.
- Erinnerungen: Mit Namen lautet die erste „Hallo Helmut, deine Fahrt beginnt in 15 min“, die zweite und alle anderen Meldungen bleiben neutral. Ohne Namen ändert sich nichts.
  Nach Abfahrtsbeginn kommt keine Erinnerung mehr. Ändert man die Zeiten bei einer laufenden Fahrt, gelten schon erreichte Zeitpunkte als erledigt (nichts kommt nachträglich oder doppelt).
- Alles wird serverseitig geprüft (`apply_settings`, `check_hours`, `check_remind`, `check_nick`). Bei einem Fehler wird nichts gespeichert.

### Web-API

Alle Aufrufe mit `Authorization: Bearer <Sitzung>` (kein Cookie, daher kein CSRF-Risiko). Fehler kommen als `{"error": "verständlicher Text"}` mit Status 400.

| Aufruf | Wer | Was |
|---|---|---|
| `GET /settings` | angemeldet | eigene Einstellungen |
| `POST /settings` | angemeldet | `nick`, `autoclear_on`, `autoclear_hours`, `remind_first`, `remind_second` (nur die gesendeten Felder, `null` = aus oder entfernen) |
| `GET /members` | Besitzer, Admin, Verwalter | Mitglieder (mit nicht umkehrbarer `ref` statt Chat-ID), offene Einladungen, Verlauf |
| `POST /members/invite` | Besitzer, Admin, Verwalter | neue Einladung |
| `DELETE /members/invite?code=` | Besitzer, Admin, Verwalter | Einladung widerrufen |
| `POST /members/role` | Besitzer, Admin, Verwalter | `{"ref", "role": "admin", "verwalter", "mitglied" oder "gast"}`, nur für Personen unter sich, nur Besitzer und Admin, Admins ernennt nur der Besitzer |
| `POST /routines` | angemeldet | `{"key", "days": [0-6], "weeks": 1-52 oder null}`: macht aus einer verfolgten Fahrt eine wiederkehrende, `weeks` null = unbegrenzt |
| `DELETE /routines?rid=` | angemeldet | wiederkehrende Fahrt beenden |
| `DELETE /members?ref=` | Besitzer, Admin, Verwalter | Person rauswerfen, nur unter sich. Der Besitzer geht nie |

Mitglieder deaktivieren (ohne zu entfernen) gibt es nicht, es bleibt beim Entfernen.

### Tests

`python3 bot/test_einstellungen.py` prüft Name, Löschzeit, Erinnerungen, die Menüs in Telegram, die Web-API, Einladungen (erstellen, einlösen, widerrufen, ablaufen),
manipulierte Anfragen und Knöpfe sowie das Speichern über einen Neustart. Er läuft zusätzlich zu `test_rechte.py`.
