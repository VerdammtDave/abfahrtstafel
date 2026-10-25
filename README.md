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

- `bot/config.json` (Token) und `bot/data.json` (Sitzungen, Favoriten) stehen in `.gitignore`. Niemals einchecken.
- Der Bot läuft als eigener Systembenutzer mit eingeschränkten Rechten; die API lauscht nur auf `127.0.0.1` und ist nur über Caddy erreichbar.
- Im Router nur Port 80 und 443 freigeben, nie 8787.

## Lizenz

[MIT](LICENSE). Du darfst den Code frei nutzen, verändern und weitergeben.
Die Abfahrtsdaten kommen von [Transitous](https://transitous.org); für sie gelten deren eigene Nutzungsbedingungen.
