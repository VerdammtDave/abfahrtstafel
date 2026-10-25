# Website als Container (z. B. für Coolify). Die Adresse der Bot-API kommt aus der Umgebungsvariable BOT_API_URL.
FROM nginx:alpine
COPY index.html /usr/share/nginx/html/index.html
COPY deploy/web/40-bot-api.sh /docker-entrypoint.d/40-bot-api.sh
RUN chmod +x /docker-entrypoint.d/40-bot-api.sh
EXPOSE 80
