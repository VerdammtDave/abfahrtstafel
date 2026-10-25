# Abfahrtstafel

Eine Abfahrtstafel im Look einer Bahnhofsanzeige für Bus, Tram, U-Bahn und Zug. Läuft komplett im Browser:
eine einzelne Datei (`index.html`), keine Build-Schritte, kein Server-Code nötig. Die Daten kommen von
[Transitous](https://transitous.org) (MOTIS, europaweit, ohne API-Schlüssel).

- Haltestellen suchen, Favoriten merken, nach Bus/Bahn filtern
- Verspätungen, Ausfälle und Störungsmeldungen, „Über“-Spalte als Laufschrift
- Gleis oder Steig je nach Verkehrsmittel
- Drei Darstellungen: klassisch, dunkel, hell

**Optional:** Mit dem mitgelieferten Telegram-Bot (`bot/`) lassen sich einzelne Fahrten verfolgen (Meldung bei Verspätung/Ausfall)
und Favoriten zwischen Geräten synchronisieren. Das ist nur nötig, wenn du „Mit Telegram anmelden“ nutzen willst.

## Empfohlen: alles zusammen in Coolify / Docker Compose

Website und Bot laufen als zwei Container hinter einer Adresse, ohne Portfreigabe: [docs/Docker-Coolify.md](docs/Docker-Coolify.md).
Pflicht-Variablen: `BOT_TOKEN` und `BOT_OWNER_ID`.

## Nur die Website

`index.html` auf irgendeinen Webspace legen, fertig. Damit die Anmelde-Schaltfläche nicht erscheint, in `index.html`
`const BOT_API = '/api';` auf `const BOT_API = '';` ändern.

## Website auf eigenem Server (Debian/Ubuntu)

```
git clone https://github.com/VerdammtDave/abfahrtstafel.git
cd abfahrtstafel
sudo ./install.sh --web-only
```

Installiert Caddy und die Seite (`http://<Server-IP>:8080`). Für HTTPS unter eigener Domain die erste Zeile in
`/etc/caddy/Caddyfile` durch die Domain ersetzen und `systemctl restart caddy`.

## Mit Telegram-Bot

1. Bei `@BotFather` in Telegram `/newbot`, Token kopieren.
2. `sudo ./install.sh`
3. `sudo nano /opt/abfahrtsbot/config.json`, `bot_token` eintragen, dann `sudo systemctl restart abfahrtsbot`.
4. **Dem Bot sofort selbst `/start` schreiben:** Wer zuerst schreibt, wird Besitzer.

Schritt für Schritt (Coolify, Privatserver, All-Inkl): [docs/Schritt-fuer-Schritt.md](docs/Schritt-fuer-Schritt.md).
Details: [bot/README.md](bot/README.md), Betrieb hinter dem Heimrouter mit eigener Domain: [docs/Einrichtung-Router.md](docs/Einrichtung-Router.md).

## Sicherheit

- `bot/config.json` (Token) und `bot/data.json` (Sitzungen, Favoriten) stehen in `.gitignore`. Niemals einchecken. Im Container kommt der Token aus der Umgebungsvariable `BOT_TOKEN`.
- Der Bot läuft ohne Root (Container) bzw. als eigener Systembenutzer; die API ist nur über den Proxy erreichbar.
- `BOT_OWNER_ID` legt den Besitzer fest, sonst würde der Erste mit `/start` Besitzer.
- Sitzungs-Tokens werden nur als Hash gespeichert, Anmeldeversuche sind pro IP gedrosselt, die Datendatei ist nur für den Bot lesbar.
- Sicherheitslücken bitte nicht öffentlich als Issue melden, sondern per privater Nachricht an den Autor.
- Im Router (nur bei der Variante ohne Docker) nur Port 80 und 443 freigeben, nie 8787.

## Lizenz

[MIT](LICENSE). Du darfst den Code frei nutzen, verändern und weitergeben.
Die Abfahrtsdaten kommen von [Transitous](https://transitous.org); für sie gelten deren eigene Nutzungsbedingungen.
