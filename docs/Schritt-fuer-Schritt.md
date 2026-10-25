# Schritt für Schritt: Website (Coolify) + Bot (Privatserver) + Domain (All-Inkl)

So sieht es am Ende aus:

```
Browser ──► https://abfahrten.deine-domain.de   (Website, läuft in Coolify auf dem Hetzner-Server)
   │
   └──────► https://bot-api.deine-domain.de      (Bot-API, läuft beim Privatserver, Router leitet Port 80/443 weiter)
                      │
                      └──► Telegram
```

Die Website und der Bot sind getrennt. Die Website kennt nie den Bot-Token. Es gibt zwei Domains (Subdomains).
Ersetze `deine-domain.de` überall durch deine echte Domain.

Wer **nur die Website ohne Telegram** will, macht nur Teil A und C und lässt `BOT_API_URL` leer.

---

## Teil A: Website in Coolify (Hetzner-Server)

1. In Coolify: **Projects → New project**, Name z. B. `Abfahrtstafel`.
2. Im Projekt (Umgebung `production`): **New Resource → Public Repository**.
3. Repository-URL: `https://github.com/VerdammtDave/abfahrtstafel` (oder dein Fork), Branch `main`.
4. **Build Pack: Dockerfile** wählen. Port: **80**.
5. Bei **Domains** eintragen: `https://abfahrten.deine-domain.de`. Coolify holt das HTTPS-Zertifikat automatisch.
6. Unter **Environment Variables** anlegen: `BOT_API_URL` = `https://bot-api.deine-domain.de` (ohne `/` am Ende).
   Ohne Bot: Variable weglassen.
7. **Deploy** klicken.

## Teil B: DNS-Einträge setzen

**Erst prüfen, wo die DNS-Einträge liegen:** `nslookup -type=NS deine-domain.de`. Stehen dort Nameserver von All-Inkl (`ns5.kasserver.com` o. ä.), geht es im KAS.
Stehen dort z. B. `*.ns.cloudflare.com`, werden die Einträge **in Cloudflare** gesetzt, auch wenn die Domain bei All-Inkl gekauft wurde.

**All-Inkl (KAS):** Domain → deine Domain → DNS-Einstellungen → neuen DNS-Eintrag hinzufügen.
**Cloudflare:** Dashboard → deine Domain → DNS → Records → Add record. Bei einem vorhandenen Wildcard-Eintrag `*` gilt der neue, genauere Eintrag zuerst.
Die Wolke zunächst auf **„DNS only“** (grau) stellen, damit Coolify das HTTPS-Zertifikat holen kann. Mit orange Wolke (Proxy) muss in Cloudflare unter SSL/TLS der Modus **Full** stehen, sonst gibt es Weiterleitungsschleifen.

Einträge:

| Name | Typ | Wert |
|---|---|---|
| `abfahrten` | A | öffentliche IP des Hetzner-Servers (steht in Hetzner oder Coolify unter Servers) |
| `bot-api` | A | öffentliche IP des Anschlusses, an dem der Privatserver hängt |

Prüfen (nach ein paar Minuten): `nslookup abfahrten.deine-domain.de` und `nslookup bot-api.deine-domain.de`.

**Wechselt die IP am Privatanschluss** (üblich), brauchst du DynDNS: Im KAS unter dem DNS-Eintrag `bot-api` die DynDNS-Option aktivieren
(der Router oder ein Skript meldet dann die neue IP an All-Inkl), oder DuckDNS nutzen und bei All-Inkl einen CNAME `bot-api` → `name.duckdns.org` setzen.

## Teil C: Bot auf dem Privatserver (Proxmox-Container)

Voraussetzung: öffentliche IPv4 am Anschluss (kein CGNAT, siehe `docs/Einrichtung-Router.md`, „Vorab prüfen“).

1. **Bot anlegen:** In Telegram `@BotFather` → `/newbot`, den Token kopieren. Den Token niemals weitergeben oder hochladen.
2. **Container** (Debian/Ubuntu, 512 MB RAM reichen) in der Konsole:
   ```
   apt update && apt install -y git python3 nano curl tzdata
   timedatectl set-timezone Europe/Berlin
   git clone https://github.com/VerdammtDave/abfahrtstafel.git
   cd abfahrtstafel
   sudo API_DOMAIN=bot-api.deine-domain.de WEB_ORIGIN=https://abfahrten.deine-domain.de ./install.sh --bot-only
   ```
3. **Token eintragen:**
   ```
   nano /opt/abfahrtsbot/config.json      # bei bot_token den Token von BotFather einsetzen
   systemctl restart abfahrtsbot
   journalctl -u abfahrtsbot -f           # muss "Web-API auf 127.0.0.1:8787" und "Bot läuft." zeigen
   ```
4. **Feste IP für den Container:** Im Router eine DHCP-Reservierung anlegen (oder in Proxmox eine statische IP), sonst bricht die Weiterleitung nach einem Neustart.
5. **Portfreigabe im Router** (Fritz!Box: Internet → Freigaben → Portfreigaben): TCP **80 → 80** und **443 → 443** auf den Container.
   Nur diese zwei. **Nie** Port 8787 freigeben. Port 80 braucht Caddy, um das Zertifikat zu holen.
6. **In Telegram dem Bot sofort `/start` schreiben.** Wer zuerst schreibt, wird Besitzer.
7. **Test:** `https://bot-api.deine-domain.de/me` im Browser öffnen. Antwort `{"error": "nicht angemeldet"}` heißt: läuft.
   Aus dem eigenen WLAN klappt das mit manchen Routern nicht (kein NAT-Loopback). Dann mit Handy über mobile Daten testen.

## Teil D: Alles zusammen testen

1. `https://abfahrten.deine-domain.de` öffnen. Die Tafel erscheint, oben „Mit Telegram anmelden“.
2. Anmelden, in Telegram „Ja, anmelden“ bestätigen. Dein Name und der Tab „🔔 Tracking“ erscheinen.
3. Haltestelle suchen, in der Tafel eine Fahrt antippen (🔔). Der Bot meldet sich in Telegram.

**Fehlersuche:** Browser-Konsole (F12). `CORS` = `WEB_ORIGIN` stimmt nicht genau (mit `https://`, ohne `/` am Ende). `Mixed Content` = eine
der beiden Adressen ist nicht HTTPS. „Bot nicht erreichbar“ = DNS, Portfreigabe oder Caddy (`journalctl -u caddy -f`) prüfen.

## Weitere Personen zulassen

Der Besitzer schickt dem Bot `/code` und gibt die erhaltene Zeile weiter. Wer sie dem Bot schickt, ist Mitglied und kann sich auch auf der Website anmelden.

## Für Freunde, die es bei sich aufsetzen

Jeder braucht **seinen eigenen** Bot (eigener Token von BotFather), **eigene** Domains und **eigenen** Server. Das Repo klonen oder forken und
Teil A bis D mit den eigenen Daten durchgehen. Es gibt keinen gemeinsamen Bot.

## Updates

- Website: In Coolify auf **Redeploy** klicken (oder Auto-Deploy über Webhook einrichten).
- Bot: `cd abfahrtstafel && git pull && sudo ./install.sh --bot-only` (mit denselben Variablen wie oben). `config.json` und `data.json` bleiben erhalten.
