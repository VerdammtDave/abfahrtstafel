# Website als Container. Ohne Bot: BOT_API_URL leer lassen. Mit Bot siehe docker-compose.yml.
FROM nginx:1.27-alpine
COPY index.html /usr/share/nginx/html/index.html
COPY favicon.svg favicon-32.png icon-512.png apple-touch-icon.png /usr/share/nginx/html/
COPY deploy/web/default.conf /etc/nginx/conf.d/default.conf
COPY deploy/web/40-bot-api.sh /docker-entrypoint.d/40-bot-api.sh
RUN chmod +x /docker-entrypoint.d/40-bot-api.sh
EXPOSE 80
