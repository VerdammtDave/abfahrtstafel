#!/bin/bash
# Installiert die Abfahrtstafel und/oder den Telegram-Bot auf Debian/Ubuntu.
#   sudo ./install.sh                 Website + Bot + Caddy (alles auf einem Server)
#   sudo ./install.sh --web-only      nur Website + Caddy (ohne Telegram)
#   sudo API_DOMAIN=bot-api.example.de WEB_ORIGIN=https://abfahrten.example.de ./install.sh --bot-only
#                                     nur Bot + Caddy mit HTTPS-Adresse für die API (Website läuft woanders, z. B. Coolify)
# Erneutes Ausführen aktualisiert Dateien; config.json, data.json und eine angepasste Caddyfile bleiben erhalten.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Bitte als root ausführen (sudo ./install.sh)."; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"
MODE=all
case "${1:-}" in
  --web-only) MODE=web ;;
  --bot-only) MODE=bot ;;
  "") ;;
  *) echo "Unbekannte Option: $1"; exit 1 ;;
esac
WEBROOT=/var/www/bahnhofstafel
BOTDIR=/opt/abfahrtsbot

if [ "$MODE" = bot ]; then
  [ -n "${API_DOMAIN:-}" ] || { echo "Bitte API_DOMAIN setzen, z. B. API_DOMAIN=bot-api.example.de"; exit 1; }
  [[ "$API_DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || { echo "API_DOMAIN enthält ungültige Zeichen."; exit 1; }
  [ -z "${WEB_ORIGIN:-}" ] || [[ "$WEB_ORIGIN" =~ ^https://[A-Za-z0-9.-]+$ ]] || { echo "WEB_ORIGIN muss so aussehen: https://abfahrten.example.de (ohne Pfad, ohne / am Ende)."; exit 1; }
fi

apt-get update -qq
command -v caddy >/dev/null || apt-get install -y -qq caddy
[ "$MODE" = web ] || apt-get install -y -qq python3 tzdata

# Website
if [ "$MODE" != bot ]; then
  mkdir -p "$WEBROOT"
  install -m 644 "$SRC/index.html" "$WEBROOT/index.html"
  for f in favicon.svg favicon-32.png icon-512.png apple-touch-icon.png; do install -m 644 "$SRC/$f" "$WEBROOT/$f"; done
  install -m 644 "$SRC/deploy/web/503.html" "$WEBROOT/503.html"
  if [ "$MODE" = web ]; then
    sed -i "s|^const BOT_API = '/api';|const BOT_API = '';|" "$WEBROOT/index.html"   # ohne Bot keine Anmeldung anzeigen
  fi
fi

# Bot
if [ "$MODE" != web ]; then
  id abfahrtsbot >/dev/null 2>&1 || useradd --system --home "$BOTDIR" --shell /usr/sbin/nologin abfahrtsbot
  mkdir -p "$BOTDIR"
  install -m 644 "$SRC/bot/abfahrtsbot.py" "$BOTDIR/abfahrtsbot.py"
  [ -f "$BOTDIR/config.json" ] || install -m 600 "$SRC/bot/config.example.json" "$BOTDIR/config.json"
  if [ -n "${WEB_ORIGIN:-}" ]; then   # CORS auf die Website beschränken
    WEB_ORIGIN="$WEB_ORIGIN" python3 - <<'PY'
import json, os
p = "/opt/abfahrtsbot/config.json"
c = json.load(open(p, encoding="utf-8"))
c["web_origin"] = os.environ["WEB_ORIGIN"]
json.dump(c, open(p, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
PY
  fi
  chown -R abfahrtsbot:abfahrtsbot "$BOTDIR"
  chmod 700 "$BOTDIR"; chmod 600 "$BOTDIR/config.json"
  [ ! -f "$BOTDIR/data.json" ] || chmod 600 "$BOTDIR/data.json"
  install -m 644 "$SRC/deploy/abfahrtsbot.service" /etc/systemd/system/abfahrtsbot.service
  systemctl daemon-reload
  systemctl enable abfahrtsbot >/dev/null 2>&1
  grep -q "DEIN_TOKEN" "$BOTDIR/config.json" || systemctl restart abfahrtsbot
fi

# Webserver (nur schreiben, wenn noch keine eigene Caddyfile von uns existiert)
if [ ! -f /etc/caddy/Caddyfile ] || ! grep -qE "abfahrtstafel-install|bahnhofstafel" /etc/caddy/Caddyfile; then
  if [ "$MODE" = bot ]; then
    cat > /etc/caddy/Caddyfile <<CADDY
# abfahrtstafel-install: nur die Bot-API, mit automatischem HTTPS (Ports 80 und 443 müssen erreichbar sein)
$API_DOMAIN {
	reverse_proxy 127.0.0.1:8787
}
CADDY
  else
    { echo "# abfahrtstafel-install"; cat "$SRC/deploy/Caddyfile.example"; } > /etc/caddy/Caddyfile
    if [ "$MODE" = web ]; then   # API-Block entfernen
      python3 - <<'PY' 2>/dev/null || sed -i '/handle \/api\/\*/,/^\t}/d' /etc/caddy/Caddyfile
import re
p = "/etc/caddy/Caddyfile"
s = open(p).read()
s = re.sub(r"\t# Nur mit Bot.*?\n\thandle /api/\* \{.*?\n\t\}\n", "", s, flags=re.S)
open(p, "w").write(s)
PY
    fi
  fi
fi
systemctl enable caddy >/dev/null 2>&1
systemctl restart caddy

echo
if [ "$MODE" != web ] && grep -q "DEIN_TOKEN" "$BOTDIR/config.json"; then
  echo "Noch zu tun: Token eintragen (nano $BOTDIR/config.json), dann: systemctl restart abfahrtsbot"
fi
[ "$MODE" = bot ] && echo "Bot-API: https://$API_DOMAIN (Test: im Browser /me aufrufen, Antwort: nicht angemeldet)" \
                  || echo "Seite lokal: http://$(hostname -I | cut -d' ' -f1):8080"
