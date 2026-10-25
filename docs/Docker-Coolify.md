# Website + Bot zusammen in Coolify (empfohlen)

Beide Teile laufen als zwei Container (`docker-compose.yml`) hinter **einer** Adresse, z. B. `https://abfahrtstafel.deine-domain.de`.
Die Website leitet `/api` intern an den Bot weiter. Dadurch gibt es **keine Portfreigabe, kein CORS, keine zweite Domain** und
der Bot ist von außen nicht direkt erreichbar. Der Bot holt Telegram-Nachrichten selbst ab (ausgehende Verbindung genügt).

```
Browser ──► https://abfahrtstafel.deine-domain.de ──► web (nginx) ──/api──► bot (Python, nur intern)
                                                                              └──► Telegram
```

## Was du brauchst

1. **Bot-Token** von `@BotFather` (`/newbot`).
2. **Deine Telegram-Chat-ID:** Schreibe `@userinfobot`, er antwortet mit deiner ID (eine Zahl). Sie macht dich fest zum Besitzer.
   Ohne sie würde der Erste, der dem Bot `/start` schreibt, Besitzer werden.
3. Eine Domain, deren DNS auf den Coolify-Server zeigt (A-Record, bei Cloudflare zunächst „DNS only“).

## Einrichtung in Coolify

> **Wichtig:** Eine schon vorhandene Coolify-Anwendung vom Typ *Dockerfile* lässt sich nicht in *Docker Compose* umwandeln.
> Lege eine **neue Ressource** an. Variablen und Domain der alten Anwendung gelten dort nicht, setze sie in der neuen neu.
> Eine Domain darf nur an einer Ressource hängen: Die alte Anwendung vorher stoppen oder die Domain dort entfernen.

1. **Projekt → New Resource → Public Repository**, URL `https://github.com/VerdammtDave/abfahrtstafel`, Branch `main`.
2. **Build Pack: Docker Compose**, Compose-Datei `/docker-compose.yml`.
3. Unter **Environment Variables** eintragen (beide als Runtime-Variable):
   - `BOT_TOKEN` = der Token von BotFather
   - `BOT_OWNER_ID` = deine Chat-ID
   - optional `VERWALTER_ID` = Chat-ID der Person, die den Server betreibt (Rolle mit immer mindestens den Rechten des Besitzers, siehe `bot/README.md`)
4. Beim Dienst **web** die Domain eintragen: `https://abfahrtstafel.deine-domain.de` (Port 80). Der Dienst **bot** bekommt **keine** Domain.
5. **Deploy**. Im Log des Dienstes `bot` muss „Web-API auf 0.0.0.0:8787“ und „Bot läuft.“ stehen.
6. Öffne die Seite, klicke „Mit Telegram anmelden“ und bestätige im Bot mit „Ja, anmelden“.

Der Token steht nur in Coolify (verschlüsselt) und nie im Repo. Die Daten des Bots liegen im Volume `botdata` und überleben Updates.

## Vom alten Bot (Privatserver) umziehen

Telegram erlaubt **nur einen** laufenden Bot pro Token. Reihenfolge:

1. Auf dem alten Server: `systemctl stop abfahrtsbot` und `cp /opt/abfahrtsbot/data.json ~/data.json`. Die Datei auf deinen PC holen.
2. In Coolify deployen, dann einmal den Dienst `bot` stoppen, die Datei in das Volume kopieren und wieder starten:
   `docker cp data.json <bot-container>:/data/data.json` (Container-Name steht in Coolify), danach `docker restart <bot-container>`.
   Alle Favoriten, Tracking und Mitglieder sind dann wieder da. Website-Anmeldungen laufen ab, man meldet sich einmal neu an.
3. Alten Bot nicht wieder starten. Auf dem Privatserver `systemctl disable abfahrtsbot`.

Ohne Umzug der Daten startet der Bot leer, du bist trotzdem Besitzer (wegen `BOT_OWNER_ID`).

## Updates

Neuen Stand pushen und in Coolify **Redeploy** klicken (oder Auto-Deploy per Webhook einschalten).

## Sicherheit in diesem Aufbau

- Der Bot läuft ohne Root, mit schreibgeschütztem Dateisystem, ohne zusätzliche Rechte (`cap_drop: ALL`) und ohne offenen Port nach außen.
- Sitzungs-Tokens der Website liegen nur als Hash im Bot, die Datendatei ist nur für den Bot lesbar.
- Anmeldeversuche sind pro IP gedrosselt (10 pro 10 Minuten).
- Die Website sendet strenge Header (CSP, `X-Frame-Options`, …). Sie darf nur Transitous und die eigene Adresse kontaktieren.
- Besitzer ist fest `BOT_OWNER_ID`. Weitere Personen kommen nur per Einladungscode (`/code`).

## Preview-Deployments (Pull Requests)

Coolify kann zu jedem Pull Request eine eigene Vorschau starten (`{{pr_id}}.preview.deine-domain.de`). Dafür braucht es einen Wildcard-DNS-Eintrag
`*.preview` auf den Server. **Achtung:** Coolify kopiert die Produktions-Variablen in den Bereich „Preview deployments“, also auch den
`BOT_TOKEN`. Ein zweiter Bot mit demselben Token stört den echten Bot (Telegram erlaubt nur einen pro Token).

- **Ohne Bot im Preview (üblich):** Im Bereich „Preview deployments“ der Environment Variables `BOT_TOKEN` und `BOT_OWNER_ID` **löschen**.
  Der Bot bleibt dann aus (`BOT_IDLE_WITHOUT_TOKEN`), die Website der Vorschau läuft trotzdem, nur ohne Telegram-Anmeldung.
- **Mit Test-Bot:** Dort stattdessen den Token eines zweiten Bots von `@BotFather` eintragen.

