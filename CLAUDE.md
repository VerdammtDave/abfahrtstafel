# Abfahrtstafel: Hinweise für Claude und andere Agenten

## Aufbau

- `index.html`: die Website (eine Datei, kein Build). Spricht Transitous direkt an und optional die Bot-API unter `/api`.
- `bot/abfahrtsbot.py`: Telegram-Bot mit kleiner Web-API (nur Standardbibliothek). `bot/test_rechte.py` prüft die Rollen.
- `deploy/`: `abfahrtsbot.service` (systemd), `Caddyfile.example`, `web/` (nginx-Konfiguration und Start-Skript der Website).
- `install.sh`: Installer für Debian/Ubuntu (`--web-only`, `--bot-only`).
- `Dockerfile` (Website), `bot/Dockerfile` (Bot), `docker-compose.yml` (beides zusammen, z. B. in Coolify).
- `docs/` und `bot/README.md`: Anleitungen.

## Rollen im Bot

- **Besitzer:** gehört der Bot. Genau eine Person, nur über `BOT_OWNER_ID` (oder `owner_id` in `config.json`), sonst die erste Person mit `/start`. Darf alles, auch `/restart`, und ernennt und entlässt Verwalter.
- **Verwalter:** vom Besitzer ernannt (`data["verwalter"]`). Lädt ein und verwaltet Rollen und Zugänge von Mitgliedern und Gästen, aber nie den Besitzer oder andere Verwalter, und ernennt keine Verwalter. Kein `/restart`.
- **Mitglied:** eingeladen per `/code`. Nutzt den Bot, sonst nichts.
- **Gast:** hat den Code einer geteilten Verbindung eingelöst. Darf diese Verbindungen ansehen und verfolgen, sonst nichts, und hat keinen Zugang zur Website-Anmeldung.

Jede Rolle ändert nur Rollen unter sich (Rang: Besitzer 4, Verwalter 3, Mitglied 2, Gast 1).

## Dauerhafte Regeln zu Rollen und Server (gilt für ALLE künftigen Änderungen)

1. Es gibt genau einen Besitzer. Er wird nur über `BOT_OWNER_ID` (oder `owner_id`) bestimmt, sonst ist es die erste Person mit `/start`. Kein Befehl, Knopf oder API-Aufruf macht jemanden zum Besitzer, ersetzt oder entfernt ihn.
2. Verwalter ernennt und entlässt nur der Besitzer. Ein Verwalter kann den Besitzer und andere Verwalter nicht entfernen, nicht ändern und deren Einladungen nicht widerrufen. Jede Rolle ändert nur Rollen unter sich (zentral `rang`, `can_touch`, `allowed_roles`).
3. Betrieb (Server, `config.json`, Umgebung, Token, Ports, Dateien, Dienste, Neustart, Logs) gehört dem Besitzer. Die einzige Betriebsfunktion im Bot ist `/restart` (`BETRIEB_COMMANDS`, mit Rückfrage), und die darf nur der Besitzer. Verwalter, Mitglieder und Gäste nie.
4. Mitglieder nutzen den Bot, sonst nichts. Gäste können nur per `/code` erhaltene Verbindungen ansehen und verfolgen.
5. Der Bot gibt Bot-Token, Besitzer-ID und Dateipfade nie in Nachrichten, Fehlermeldungen oder API-Antworten aus.
6. Der Betreiber behält per Server immer die Kontrolle (`data.json` und `config.json` bearbeiten, Dienst neu starten). Der Bot nimmt ihm nie Daten oder Rechte weg.
7. Alle Zugriffsprüfungen laufen serverseitig im Bot, nie allein auf Basis von Callback-Daten. Rechte immer über `may_use`, `is_manager` oder `rang`, nicht über `is_owner`.

Der Kommentarblock "REGELN FUER SPAETERE AENDERUNGEN" steht in `bot/abfahrtsbot.py` bei den Rollen (`GUEST_COMMANDS`), die Regeln auch in `bot/README.md`.

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
