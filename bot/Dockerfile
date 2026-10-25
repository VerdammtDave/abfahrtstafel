# Telegram-Bot als Container. Läuft ohne Root, Daten liegen im Volume /data.
FROM python:3.12-slim
ENV TZ=Europe/Berlin
RUN pip install --no-cache-dir tzdata==2025.2 \
 && useradd --system --uid 10001 --no-create-home bot \
 && mkdir /data && chown bot:bot /data
WORKDIR /app
COPY abfahrtsbot.py .
USER bot
VOLUME /data
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8787/login/poll?token=hc', timeout=3)" || exit 1
CMD ["python", "-I", "abfahrtsbot.py"]
