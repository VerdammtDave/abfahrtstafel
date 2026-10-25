#!/bin/sh
# Trägt beim Containerstart BOT_API_URL in die Seite ein und setzt die passende Content-Security-Policy.
#   leer / nicht gesetzt   Seite ohne Telegram-Anmeldung
#   /api                   Bot im selben Compose-Projekt (gleiche Adresse)
#   https://api.beispiel.de  Bot auf einem anderen Server
set -e
url="${BOT_API_URL%/}"
case "$url" in
  ""|/api|https://*|http://*) ;;
  *) echo "BOT_API_URL muss leer, /api oder eine URL mit https:// sein" >&2; exit 1 ;;
esac
case "$url" in *[!A-Za-z0-9:/._-]*) echo "BOT_API_URL enthält ungültige Zeichen" >&2; exit 1 ;; esac
sed -i "s|^const BOT_API = .*|const BOT_API = '${url}';|" /usr/share/nginx/html/index.html
extra=""
case "$url" in http*) extra=" $url" ;; esac
printf 'add_header Content-Security-Policy "default-src '"'"'none'"'"'; script-src '"'"'self'"'"' '"'"'unsafe-inline'"'"'; style-src '"'"'self'"'"' '"'"'unsafe-inline'"'"'; img-src '"'"'self'"'"' data:; connect-src '"'"'self'"'"' https://api.transitous.org%s; base-uri '"'"'none'"'"'; form-action '"'"'none'"'"'; frame-ancestors '"'"'none'"'"'" always;\n' "$extra" > /etc/nginx/csp.inc
