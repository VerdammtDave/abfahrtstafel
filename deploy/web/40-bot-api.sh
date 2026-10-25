#!/bin/sh
# Trägt beim Containerstart BOT_API_URL in die Seite ein. Leer oder nicht gesetzt = Seite ohne Telegram-Anmeldung.
set -e
url="${BOT_API_URL%/}"
case "$url" in
  ""|https://*|http://*) ;;
  *) echo "BOT_API_URL muss mit https:// beginnen" >&2; exit 1 ;;
esac
esc=$(printf '%s' "$url" | sed 's/[|&\]/\&/g')
sed -i "s|^const BOT_API = .*|const BOT_API = '${esc}';|" /usr/share/nginx/html/index.html
