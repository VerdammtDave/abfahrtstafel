# Abfahrtstafel: Hinweise für Claude und andere Agenten

## Aufbau

- `index.html`: die Website (eine Datei, kein Build). Spricht Transitous direkt an und optional die Bot-API unter `/api`.
- `bot/abfahrtsbot.py`: Telegram-Bot mit kleiner Web-API (nur Standardbibliothek). `bot/test_rechte.py` prüft die Rollen.
- `deploy/`: `abfahrtsbot.service` (systemd), `Caddyfile.example`, `web/` (nginx-Konfiguration und Start-Skript der Website).
- `install.sh`: Installer für Debian/Ubuntu (`--web-only`, `--bot-only`).
- `Dockerfile` (Website), `bot/Dockerfile` (Bot), `docker-compose.yml` (beides zusammen, z. B. in Coolify).
- `docs/` und `bot/README.md`: Anleitungen.

## Rollen im Bot

- **Verwalter:** betreibt den Bot technisch und besitzt den Server. Genau eine Person, nur über `VERWALTER_ID` (oder `verwalter_id` in `config.json`).
- **Besitzer:** eine andere Person, Chef im Bot. Verwaltet Inhalte und Mitglieder.
- **Mitglied:** eingeladen per `/code`. Voller Funktionsumfang ausser der Verwaltung.
- **Admin:** Mitglied mit der Rolle Admin (`data["admins"]`, nur Besitzer oder Verwalter vergeben sie). Darf Einladungscodes erzeugen und widerrufen und Mitglieder rauswerfen, aber nie den Besitzer oder den Verwalter, und keine Rollen vergeben. Es gibt genau einen Besitzer.
- **Gast:** hat den Code einer geteilten Verbindung eingelöst und sieht nur diese.

## Dauerhafte Regel zum Schutz des Servers (gilt für ALLE künftigen Änderungen)

1. Der Verwalter wird ausschließlich über `VERWALTER_ID` (oder `verwalter_id`) bestimmt. Kein Befehl, Knopf oder API-Aufruf vergibt, ändert oder entzieht die Rolle, auch nicht der Besitzer. Nichts davon steht in `data.json`.
2. Der Verwalter hat immer mindestens alle Rechte des Besitzers, auch bei künftigen Befehlen, Knöpfen und Web-API-Funktionen. Jede neue Rechteprüfung muss ihn einschließen (zentral über `is_verwalter`, `is_admin`, `is_full` und `may_use`), nie nur den Besitzer.
3. Der Verwalter kann nicht entfernt, gesperrt, pausiert, stummgeschaltet oder eingeschränkt werden, weder durch den Besitzer noch durch Mitglieder. Entfernen- und Sperr-Funktionen lehnen seine ID serverseitig ab. Seine Website-Sitzungen werden nie beendet, wenn andere entfernt werden.
4. Besitzer, Mitglieder und Gäste bekommen keine Funktion, die Server, Konfiguration oder Betrieb betrifft: kein Ändern von `config.json`, Umgebungsvariablen, `VERWALTER_ID`, Token, Ports, Dateien oder Diensten, kein Neustarten, Updaten oder Ausführen von Befehlen auf dem Server, kein Auslesen von Konfiguration, Token oder Logs, kein Ändern der Rolle des Verwalters. Neue Befehle dieser Art kommen nur in `VERWALTER_COMMANDS`. Der Besitzer darf nur Bot-Inhalte und Mitglieder verwalten.
5. Der Bot gibt Bot-Token, Verwalter-ID und Dateipfade nie in Nachrichten, Fehlermeldungen oder API-Antworten aus.
6. Der Betreiber behält per Server immer die Kontrolle (`data.json` und `config.json` bearbeiten, Dienst neu starten). Der Bot nimmt ihm nie Daten oder Rechte weg.
7. Alle Zugriffsprüfungen laufen serverseitig im Bot, nie allein auf Basis von Callback-Daten.

Der Kommentarblock "REGELN FUER SPAETERE AENDERUNGEN" steht in `bot/abfahrtsbot.py` direkt über `is_verwalter`, die Regeln auch in `bot/README.md`.

## Allgemeine Regeln

- **Vor jedem Commit** `python3 bot/test_rechte.py` und `python3 bot/test_einstellungen.py` ausführen. Beide müssen bestehen. Zusätzlich `python3 -m py_compile bot/abfahrtsbot.py`.
- Persönliche Einstellungen (Name, Löschzeit, Erinnerungen) liegen pro Chat in `data["chats"]` und werden nur über `apply_settings` geändert (Website und Telegram gemeinsam). Neue Einstellungen dort mit Standardwert und Prüfung ergänzen.
- Bei jeder sichtbaren Änderung an der Website einen Eintrag oben in `UPDATES` in `index.html` ergänzen (neue Versionsnummer, Datum, kurze Stichpunkte in einfacher Sprache). Die Liste sehen nur angemeldete Nutzer.
- Bei Sicherheitsfixes immer einen leicht verständlichen Bericht für das Team ohne Fachjargon anlegen.
- `bot/config.json` und `bot/data.json` nie einchecken. Der Bot-Token gehört nie in das Repo, in Logs oder in Antworten.
- Neue Befehle für Nutzer bekommen auch eine Taste (`MENU_BUTTONS`, `menu_keyboard`), aber nur für Rollen, die den Befehl nutzen dürfen. Der Test prüft, dass keine Taste mehr zeigt, als erlaubt ist.
- Nur Standardbibliothek im Bot. Stil und Sprache wie der bestehende Code: deutsche Texte, kurze Funktionen, Kommentare nur wo nötig.
- Keinen langen Gedankenstrich verwenden, weder in Texten noch in Doku, Kommentaren oder Commit-Nachrichten.
- Autor-Adresse der Commits ist die GitHub-No-Reply-Adresse, nie eine private E-Mail-Adresse.
