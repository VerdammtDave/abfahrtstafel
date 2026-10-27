#!/usr/bin/env python3
"""Test fuer Einstellungen, automatisches Loeschen, Erinnerungen und Mitgliederverwaltung.
Nur Standardbibliothek. Ausfuehren: python3 bot/test_einstellungen.py

Wie test_rechte.py: kein Netz, keine echten Dateien. Telegram-Aufrufe werden mitgeschrieben statt gesendet.
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

HIER = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("abfahrtsbot_einst", os.path.join(HIER, "abfahrtsbot.py"))
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)
bot.HERE = tempfile.mkdtemp()
for _name in ("BOT_TOKEN", "BOT_OWNER_ID", "VERWALTER_ID", "BOT_DATA_FILE"):
    os.environ.pop(_name, None)
bot.load_config()
bot.cfg["ipv4_only"] = False
bot.cfg["data_file"] = "test_einst.json"
bot.cfg["bot_token"] = "123456:GEHEIM"

BESITZER, ADMIN, MITGLIED, GAST, FREMD = 1001, 2002, 3003, 4004, 5005
FEHLER = []
TG, GESENDET = [], []


def pruefe(bedingung, text):
    if not bedingung:
        FEHLER.append(text)
        print("FEHLER:", text)


def neu():
    bot.data.clear()
    bot.data.update({"owner": BESITZER, "chats": {}, "state": {}, "pending": {}, "menus": {}, "deps": {}, "guests": [GAST],
                     "shares": {}, "members": [ADMIN, MITGLIED], "inviters": [], "invites": {}, "names": {}, "sessions": {}, "admins": [ADMIN],
                     "invited_by": {}, "msglog": {}})
    bot.msg_log.clear()
    bot.awaiting.clear()
    TG.clear()
    GESENDET.clear()
    bot.save = lambda: None
    bot.tg = lambda methode, **p: TG.append((methode, p))
    bot.send = lambda cid, text, **k: GESENDET.append((cid, str(text), k))


def fehler(fn, *a):
    try:
        fn(*a)
    except ValueError as e:
        return str(e)
    return None


def chat(cid):
    return bot.data["chats"].get(str(cid), {})


def test_name():
    neu()
    s = bot.apply_settings(BESITZER, {"nick": "  Helmut  "})
    pruefe(s["nick"] == "Helmut" and chat(BESITZER)["nick"] == "Helmut", "Name wird nicht gespeichert")
    s = bot.apply_settings(BESITZER, {"nick": "Davi"})
    pruefe(s["nick"] == "Davi", "Name laesst sich nicht aendern")
    s = bot.apply_settings(BESITZER, {"nick": None})
    pruefe(s["nick"] == "" and not chat(BESITZER).get("nick"), "Name laesst sich nicht entfernen")
    pruefe(fehler(bot.apply_settings, BESITZER, {"nick": "x" * 31}), "zu langer Name wird akzeptiert")
    pruefe(fehler(bot.apply_settings, BESITZER, {"nick": "a\x00b"}), "Steuerzeichen im Namen werden akzeptiert")
    pruefe(fehler(bot.apply_settings, BESITZER, {"nick": 5}), "Zahl als Name wird akzeptiert")
    pruefe(not chat(BESITZER).get("nick"), "ungueltiger Name wurde trotzdem gespeichert")
    # der Name hat mit Rechten und Telegram-Namen nichts zu tun
    bot.apply_settings(MITGLIED, {"nick": "Chef"})
    pruefe(not bot.is_manager(MITGLIED) and bot.who(MITGLIED) != "Chef", "der Name beeinflusst Rechte oder Anzeige im Verwaltungsbereich")


def test_loeschintervall():
    neu()
    pruefe(bot.clear_hours(BESITZER) == 47, "Standard ist nicht 47 Stunden")
    for h in (3, 5, 12, 24, 47, "5", " 8 h ", "10 Stunden"):
        bot.apply_settings(BESITZER, {"autoclear_hours": h})
        pruefe(bot.clear_hours(BESITZER) == bot.check_hours(h), f"{h!r} wurde nicht gespeichert")
    bot.apply_settings(BESITZER, {"autoclear_hours": 5})
    for schlecht in (2, 0, -1, "2", "abc", "", None, True, 48, 72, 3.5, "1e3", [5], {"a": 1}):
        meldung = fehler(bot.apply_settings, BESITZER, {"autoclear_hours": schlecht})
        pruefe(meldung, f"ungueltiger Wert {schlecht!r} wird akzeptiert")
        pruefe(bot.clear_hours(BESITZER) == 5, f"ungueltiger Wert {schlecht!r} hat den gespeicherten Wert veraendert")
    pruefe("3" in fehler(bot.apply_settings, BESITZER, {"autoclear_hours": 2}), "Fehlermeldung nennt das Minimum nicht")
    pruefe("48" in fehler(bot.apply_settings, BESITZER, {"autoclear_hours": 72}), "Fehlermeldung erklaert die Telegram-Grenze nicht")
    pruefe(fehler(bot.apply_settings, BESITZER, {"autoclear_on": "ja"}), "Text als Schalter wird akzeptiert")


def test_loeschen_nutzt_einstellung():
    neu()
    jetzt = int(time.time())
    bot.data["chats"][str(BESITZER)] = {"autoclear": True, "autoclear_h": 5, "trips": [], "favs": []}
    bot.data["chats"][str(MITGLIED)] = {"autoclear": True, "trips": [], "favs": []}          # Standard 47 Stunden
    bot.msg_log[BESITZER] = [[1, jetzt - 6 * 3600], [2, jetzt - 2 * 3600]]
    bot.msg_log[MITGLIED] = [[10, jetzt - 6 * 3600], [11, jetzt - 46 * 3600], [12, jetzt - 48 * 3600]]
    bot.autoclear_step()
    geloescht = {p["chat_id"]: p["message_ids"] for m, p in TG if m == "deleteMessages"}
    pruefe(geloescht.get(BESITZER) == [1], f"bei 5 Stunden wurde {geloescht.get(BESITZER)} geloescht, erwartet [1]")
    pruefe(geloescht.get(MITGLIED) == [12], f"bei 47 Stunden wurde {geloescht.get(MITGLIED)} geloescht, erwartet [12]")
    TG.clear()
    bot.autoclear_step()                                            # kein zweites Mal, nichts doppelt
    pruefe(not [1 for m, _ in TG if m == "deleteMessages"], "dieselben Nachrichten werden mehrfach geloescht")
    # Intervall verkuerzen: bereits gemerkte Nachrichten gelten sofort nach der neuen Grenze
    bot.apply_settings(MITGLIED, {"autoclear_hours": 3})
    TG.clear()
    bot.autoclear_step()
    geloescht = {p["chat_id"]: p["message_ids"] for m, p in TG if m == "deleteMessages"}
    pruefe(geloescht.get(MITGLIED) == [10, 11], f"nach Verkuerzen auf 3 Stunden wurde {geloescht.get(MITGLIED)} geloescht")
    # Ausschalten vergisst die Nachrichten
    bot.apply_settings(BESITZER, {"autoclear_on": False})
    pruefe(BESITZER not in bot.msg_log, "nach dem Ausschalten merkt sich der Bot noch Nachrichten")


def punkt(minuten, verspaetung=0, ausfall=False, jetzt=None):
    jetzt = jetzt or datetime.now(timezone.utc)
    plan = jetzt + timedelta(minutes=minuten - verspaetung)
    return {"planned": plan, "actual": jetzt + timedelta(minutes=minuten), "delay": verspaetung, "cancelled": ausfall,
            "kind": "bahn", "line": "S1", "dest": "Ziel", "stop": "Halt", "track": ""}


def test_erinnerungszeiten():
    neu()
    s = bot.settings_view(BESITZER)
    pruefe((s["remind"]["first"], s["remind"]["second"]) == (15, 5), "Standard ist nicht 15 und 5 Minuten")
    bot.apply_settings(BESITZER, {"remind_first": 30, "remind_second": 10})
    pruefe(bot.active_remind(chat(BESITZER)) == [30, 10], "30 und 10 wurden nicht gespeichert")
    bot.apply_settings(BESITZER, {"remind_second": None})
    pruefe(bot.active_remind(chat(BESITZER)) == [30], "zweite Erinnerung laesst sich nicht ausschalten")
    bot.apply_settings(BESITZER, {"remind_first": None})
    pruefe(bot.active_remind(chat(BESITZER)) == [], "beide Erinnerungen lassen sich nicht ausschalten")
    bot.apply_settings(BESITZER, {"remind_first": 60, "remind_second": 1})
    for a, b in ((10, 10), (5, 15), (0, 5), (121, 5), (15, 0), (-5, 3), ("x", 5), (True, 5), (15.5, 5)):
        vorher = list(chat(BESITZER)["remind"])
        pruefe(fehler(bot.apply_settings, BESITZER, {"remind_first": a, "remind_second": b}), f"({a!r}, {b!r}) wird akzeptiert")
        pruefe(chat(BESITZER)["remind"] == vorher, f"({a!r}, {b!r}) hat den gespeicherten Wert veraendert")
    # Einzelaenderung, die zur anderen Zeit nicht passt
    bot.apply_settings(BESITZER, {"remind_first": 15, "remind_second": 5})
    pruefe(fehler(bot.apply_settings, BESITZER, {"remind_first": 3}), "erste Erinnerung nach der zweiten wird akzeptiert")
    pruefe(chat(BESITZER)["remind"] == [15, 5], "abgelehnte Einzelaenderung wurde trotzdem gespeichert")
    pruefe(bot.active_remind({}) == [15, 5] and bot.active_remind({"remind": "kaputt"}) == [15, 5], "ohne gueltige Werte gilt nicht der Standard")


def test_erinnerungen_ausloesen():
    neu()
    jetzt = datetime.now(timezone.utc)
    zu = (30, 10)
    ev, _, st = bot.decide_trip(None, punkt(31, jetzt=jetzt), jetzt, zu)
    pruefe(ev is None, "Erinnerung kommt zu frueh")
    ev, _, st = bot.decide_trip(st, punkt(30, jetzt=jetzt), jetzt, zu)
    pruefe(ev == "remind" and st["greet"], "erste Erinnerung kommt nicht bei 30 Minuten")
    ev, _, st = bot.decide_trip(st, punkt(29, jetzt=jetzt), jetzt, zu)
    pruefe(ev is None, "dieselbe Erinnerung kommt doppelt")
    ev, _, st = bot.decide_trip(st, punkt(10, jetzt=jetzt), jetzt, zu)
    pruefe(ev == "remind" and not st["greet"], "zweite Erinnerung kommt nicht bei 10 Minuten oder wird als erste gezaehlt")
    ev, _, st = bot.decide_trip(st, punkt(5, jetzt=jetzt), jetzt, zu)
    pruefe(ev is None, "bei 5 Minuten kommt eine Erinnerung, obwohl nur 30 und 10 gelten")
    # nach Abfahrtsbeginn nichts mehr
    ev, _, _ = bot.decide_trip(None, punkt(-1, jetzt=jetzt), jetzt, zu)
    pruefe(ev is None, "nach Fahrtbeginn kommt noch eine Erinnerung")
    ev, _, _ = bot.decide_trip(None, punkt(0, jetzt=jetzt), jetzt, zu)
    pruefe(ev is None, "bei Abfahrt kommt noch eine Erinnerung")
    # ausgeschaltet
    ev, _, _ = bot.decide_trip(None, punkt(10, jetzt=jetzt), jetzt, [])
    pruefe(ev is None, "bei ausgeschalteten Erinnerungen kommt trotzdem eine")
    # Standard bleibt ohne Angabe
    ev, _, st = bot.decide_trip(None, punkt(15, jetzt=jetzt), jetzt)
    pruefe(ev == "remind" and st["rem"] == [15], "Standard 15 Minuten funktioniert nicht mehr")
    # Verspaetung verschiebt die Erinnerung: gerechnet wird mit der voraussichtlichen Abfahrt
    ev, _, _ = bot.decide_trip(None, punkt(20, verspaetung=10, jetzt=jetzt), jetzt, (30, 10))
    pruefe(ev == "remind", "bei Verspaetung wird mit der geplanten statt der voraussichtlichen Zeit gerechnet")


def test_aenderung_bei_laufender_fahrt():
    neu()
    jetzt = time.time()
    bot.data["chats"][str(BESITZER)] = {"trips": [{"wid": "w1", "key": "k", "stop": "s"}], "favs": []}
    bot.data["state"][str(BESITZER)] = {"w_w1": {"eta": jetzt + 20 * 60, "rem": [], "nd": 0}}
    bot.apply_settings(BESITZER, {"remind_first": 30})
    st = bot.data["state"][str(BESITZER)]["w_w1"]
    pruefe(st["rem"] == [30], f"bereits erreichte 30 Minuten gelten nicht als erledigt ({st['rem']})")
    d = punkt(20)
    ev, _, _ = bot.decide_trip(st, d, datetime.now(timezone.utc), bot.active_remind(chat(BESITZER)))
    pruefe(ev is None, "nach dem Aendern kommt sofort eine nachtraegliche Erinnerung")
    # Abgefahrene Fahrt: nichts mehr
    bot.data["state"][str(BESITZER)]["w_w1"] = {"eta": jetzt - 120, "rem": [], "nd": 0}
    bot.apply_settings(BESITZER, {"remind_first": 45})
    pruefe(bot.data["state"][str(BESITZER)]["w_w1"]["rem"] == [45, 5] or bot.data["state"][str(BESITZER)]["w_w1"]["rem"] == [45], "abgefahrene Fahrt wird nicht als erledigt markiert")
    # Der naechste Weckzeitpunkt richtet sich nach den neuen Werten
    bot.data["state"][str(BESITZER)]["w_w1"] = {"eta": jetzt + 40 * 60, "rem": [], "nd": 0}
    bot.data["chats"][str(BESITZER)]["trips"][0]["stop"] = "s"
    bot.apply_settings(BESITZER, {"remind_first": 30, "remind_second": 10})
    faellig = bot.next_remind_due()
    pruefe(faellig is not None and abs(faellig[0] - (jetzt + 40 * 60 - 30 * 60 - bot.REMIND_LEAD)) < 3, "naechster Weckzeitpunkt nutzt nicht die neuen Werte")


def test_personalisierte_nachricht():
    neu()
    d = punkt(15)
    pruefe(bot.fmt_trip("remind", d).startswith("⏰ Abfahrt in"), "ohne Namen hat sich der Text geaendert")
    text = bot.fmt_trip("remind", d, nick="Helmut")
    pruefe("Hallo Helmut" in text and "beginnt in" in text, f"Name fehlt in der Erinnerung: {text.splitlines()[0]}")
    pruefe("Helmut" not in bot.fmt_trip("change", d, 0, "Helmut") and "Helmut" not in bot.fmt_trip("cancel", d, nick="Helmut"), "Name taucht in jeder Nachricht auf")
    # ueber check_trip: nur die erste Erinnerung nennt den Namen
    bot.apply_settings(BESITZER, {"nick": "Helmut", "remind_first": 30, "remind_second": 10})
    c = bot.get_chat(BESITZER)
    w = {"wid": "w1", "key": "k", "stop": "s", "line": "S1", "stop_name": "Halt", "planned": time.time() + 3600}
    c["trips"].append(w)
    jetzt = datetime.now(timezone.utc)
    ausgang = []
    for minuten in (30, 10):
        d = punkt(minuten, jetzt=jetzt)
        d["key"] = "k"
        bot.check_trip(str(BESITZER), c, w, {"s": [d]}, jetzt, ausgang)
    pruefe(len(ausgang) == 2 and "Helmut" in ausgang[0][1] and "Helmut" not in ausgang[1][1], "Anrede bei Erinnerung 1 und nicht bei 2 erwartet")
    bot.apply_settings(BESITZER, {"nick": None})
    c["trips"].append(w)
    bot.data["state"][str(BESITZER)].clear()
    ausgang.clear()
    d = punkt(30, jetzt=jetzt)
    d["key"] = "k"
    bot.check_trip(str(BESITZER), c, w, {"s": [d]}, jetzt, ausgang)
    pruefe(len(ausgang) == 1 and "Hallo" not in ausgang[0][1], "ohne Namen wird trotzdem angesprochen")


def knopf(cid, daten):
    TG.clear()
    GESENDET.clear()
    bot.handle_callback({"id": "x", "data": daten, "message": {"chat": {"id": cid}, "message_id": 7}})


def antworten():
    return [p.get("text", "") for m, p in TG if m == "answerCallbackQuery"]


def test_telegram_menue():
    neu()
    for cid in (BESITZER, ADMIN, MITGLIED):
        GESENDET.clear()
        bot.handle_message({"chat": {"id": cid}, "text": "/einstellungen", "from": {"first_name": "T"}})
        pruefe(GESENDET and "Einstellungen" in GESENDET[-1][1], f"/einstellungen antwortet {cid} nicht")
    GESENDET.clear()
    bot.handle_message({"chat": {"id": GAST}, "text": "/einstellungen", "from": {"first_name": "T"}})
    pruefe(not any("Name" in t for _, t, _ in GESENDET), "Gast sieht die Einstellungen")
    GESENDET.clear()
    bot.handle_message({"chat": {"id": FREMD}, "text": "/einstellungen", "from": {"first_name": "T"}})
    pruefe(not any("Einstellungen" in t and "Name" in t for _, t, _ in GESENDET), "Fremde sehen das Einstellungsmenue")
    pruefe(str(FREMD) not in bot.data["chats"], "Fremde bekommen ein Profil angelegt")
    # alle Ansichten haben einen Zurueck-Knopf und jede Callback-Antwort kommt
    for ansicht in ("m", "p", "c", "r", "r1", "r2"):
        text, kb = bot.st_view(BESITZER, ansicht)
        daten = [k["callback_data"] for z in kb["inline_keyboard"] for k in z]
        pruefe(ansicht == "m" and "st:h" in daten or ansicht != "m" and any(d in ("st:m", "st:r") for d in daten), f"Ansicht {ansicht} hat keinen Zurueck-Knopf")
        pruefe(all(len(d.encode()) <= 64 for d in daten), f"Callback-Daten in {ansicht} zu lang")
    knopf(BESITZER, "st:ch:12")
    pruefe(bot.clear_hours(BESITZER) == 12 and antworten() == ["Gespeichert"], "Knopf fuer 12 Stunden speichert nicht")
    knopf(BESITZER, "st:ch:12")                                       # Doppelklick: derselbe Wert, kein Fehler
    pruefe(bot.clear_hours(BESITZER) == 12 and len(antworten()) == 1, "wiederholter Klick wird nicht sauber beantwortet")
    for schlecht in ("st:ch:2", "st:ch:72", "st:ch:abc", "st:ch:", "st:s1:0", "st:s1:999", "st:s2:20", "st:s1:<b>"):
        vorher = (bot.clear_hours(BESITZER), list(bot.get_chat(BESITZER).get("remind", [15, 5])))
        knopf(BESITZER, schlecht)
        nachher = (bot.clear_hours(BESITZER), list(bot.get_chat(BESITZER).get("remind", [15, 5])))
        pruefe(vorher == nachher, f"manipulierter Knopf {schlecht} hat etwas veraendert")
        pruefe(len(antworten()) == 1, f"Knopf {schlecht} wurde nicht genau einmal beantwortet")
    knopf(BESITZER, "st:s1:30")
    knopf(BESITZER, "st:s2:10")
    pruefe(bot.active_remind(chat(BESITZER)) == [30, 10], "Knoepfe fuer Erinnerungen speichern nicht")
    knopf(BESITZER, "st:s2:off")
    pruefe(bot.active_remind(chat(BESITZER)) == [30], "Ausschalten per Knopf funktioniert nicht")
    knopf(BESITZER, "st:co:1")
    pruefe(chat(BESITZER)["autoclear"] is True, "Einschalten per Knopf funktioniert nicht")
    # nur die eigenen Einstellungen: ein Knopf wirkt immer auf den Chat, aus dem er kommt
    pruefe(not chat(MITGLIED).get("autoclear_h") and not chat(MITGLIED).get("remind"), "Einstellungen einer anderen Person wurden veraendert")
    # Fremde und Unberechtigte
    knopf(FREMD, "st:ch:5")
    pruefe(str(FREMD) not in bot.data["chats"] and not antworten(), "Fremde koennen Einstellungen ueber Knoepfe aendern")
    # Gast darf keine Einstellungen und keine Verwaltung
    knopf(GAST, "st:ch:5")
    pruefe(bot.clear_hours(GAST) == 47 and not antworten(), "Gast kann Einstellungen aendern")
    knopf(GAST, "mo:0")
    pruefe(not antworten(), "Gast kann Einladungen ueber Knoepfe sehen")


def test_telegram_eingabe():
    neu()

    def schreibe(cid, text):
        GESENDET.clear()
        bot.handle_message({"chat": {"id": cid}, "text": text, "from": {"first_name": "T"}})
    knopf(BESITZER, "st:pn")
    pruefe(bot.awaiting.get(BESITZER, ("",))[0] == "/_name", "Name festlegen fragt nicht nach")
    schreibe(BESITZER, "Helmut")
    pruefe(chat(BESITZER).get("nick") == "Helmut" and BESITZER not in bot.awaiting, "Name per Text wird nicht gespeichert")
    knopf(BESITZER, "st:px")
    pruefe(not chat(BESITZER).get("nick"), "Name entfernen funktioniert nicht")
    # falsche Eingabe: erklaert, Frage bleibt offen, Abbrechen beendet sauber
    knopf(BESITZER, "st:cc")
    schreibe(BESITZER, "2")
    pruefe("3" in GESENDET[-1][1] and bot.awaiting.get(BESITZER, ("",))[0] == "/_clearh", "falsche Stundenzahl wird nicht erklaert oder Frage endet")
    schreibe(BESITZER, "viel")
    pruefe(bot.clear_hours(BESITZER) == 47, "ungueltige Eingabe wurde gespeichert")
    knopf(BESITZER, "st:x")
    pruefe(BESITZER not in bot.awaiting, "Abbrechen beendet die Eingabe nicht")
    schreibe(BESITZER, "5")
    pruefe(bot.clear_hours(BESITZER) == 47 and "Nutze die Tasten" in GESENDET[-1][1], "Zahl nach Abbrechen wurde als Eingabe behandelt")
    # Befehl bricht die Frage ab, Taste auch
    knopf(BESITZER, "st:s1c")
    schreibe(BESITZER, "/status")
    pruefe(BESITZER not in bot.awaiting, "ein Befehl bricht die Eingabe nicht ab")
    knopf(BESITZER, "st:s1c")
    schreibe(BESITZER, "❓ Hilfe")
    pruefe(BESITZER not in bot.awaiting, "eine Taste bricht die Eingabe nicht ab")
    # Abgelaufene Frage
    knopf(BESITZER, "st:s1c")
    bot.awaiting[BESITZER] = ("/_r1", time.time() - bot.ASK_TTL - 5)
    schreibe(BESITZER, "40")
    pruefe(bot.active_remind(chat(BESITZER)) == [15, 5] and "abgelaufen" in GESENDET[-1][1], "abgelaufene Eingabe wurde noch gespeichert")
    # Erinnerung per Text
    knopf(BESITZER, "st:s1c")
    schreibe(BESITZER, "40 min")
    knopf(BESITZER, "st:s2c")
    schreibe(BESITZER, "aus")
    pruefe(bot.active_remind(chat(BESITZER)) == [40], "Eingabe '40 min' und 'aus' funktionieren nicht")
    knopf(BESITZER, "st:s2c")
    schreibe(BESITZER, "50")
    pruefe(bot.active_remind(chat(BESITZER)) == [40] and bot.awaiting.get(BESITZER), "zweite Erinnerung ueber der ersten wurde akzeptiert oder Frage endete")
    # Fremde koennen keine Frage ausloesen
    bot.awaiting[FREMD] = ("/_name", time.time())
    schreibe(FREMD, "Hacker")
    pruefe(str(FREMD) not in bot.data["chats"], "Fremde konnten einen Namen speichern")
    # Eingaben von Menue-Tasten
    pruefe("🛠 Einstellungen" in [k["text"] for z in bot.menu_keyboard(BESITZER)["keyboard"] for k in z], "Taste Einstellungen fehlt")
    pruefe("🛠 Einstellungen" not in [k["text"] for z in bot.menu_keyboard(GAST)["keyboard"] for k in z], "Gast sieht die Taste Einstellungen")
    pruefe("🛠 Einstellungen" not in [k["text"] for z in bot.menu_keyboard(FREMD)["keyboard"] for k in z], "Fremde sehen die Taste Einstellungen")


def session(cid, token="tok"):
    bot.data["sessions"][bot.token_hash(token)] = {"cid": cid, "exp": 9e12}
    return "Bearer " + token


def web(methode, pfad, auth, body=None, q=None):
    return bot.web_route(methode, pfad, q or {}, body or {}, auth)


def test_web_einstellungen():
    neu()
    code, _ = web("GET", "/settings", "")
    pruefe(code == 401, "Einstellungen ohne Anmeldung abrufbar")
    code, _ = web("POST", "/settings", "Bearer falsch", {"nick": "x"})
    pruefe(code == 401, "Einstellungen mit falschem Token aenderbar")
    a = session(BESITZER, "a")
    m = session(MITGLIED, "m")
    code, out = web("GET", "/settings", a)
    pruefe(code == 200 and out["autoclear"]["hours"] == 47 and out["remind"]["first"] == 15 and out["remind"]["second"] == 5 and out["nick"] == "", "Standardwerte der Website stimmen nicht")
    pruefe(out["admin"] is True and web("GET", "/settings", m)[1]["admin"] is False, "Admin-Kennzeichen falsch")
    code, out = web("POST", "/settings", a, {"nick": "Helmut", "autoclear_hours": 5, "remind_first": 30, "remind_second": 10, "autoclear_on": True})
    pruefe(code == 200 and out["nick"] == "Helmut" and out["autoclear"]["hours"] == 5 and out["autoclear"]["on"], "Speichern ueber die Website funktioniert nicht")
    # dieselben Werte in Telegram
    text, _ = bot.st_view(BESITZER, "m")
    pruefe("Helmut" in text and "5 Stunden" in text and "30 min und 10 min" in text, "Telegram zeigt die Werte der Website nicht")
    pruefe(bot.clear_hours(BESITZER) == 5 and bot.active_remind(chat(BESITZER)) == [30, 10], "Telegram nutzt die Werte der Website nicht")
    # und umgekehrt
    knopf(BESITZER, "st:ch:12")
    knopf(BESITZER, "st:s1:60")
    out = web("GET", "/settings", a)[1]
    pruefe(out["autoclear"]["hours"] == 12 and out["remind"]["first"] == 60, "Website zeigt die Werte aus Telegram nicht")
    # Fehler: verstaendliche Meldung, nichts gespeichert, ein Fehler stoppt das ganze Paket
    code, out = web("POST", "/settings", a, {"nick": "Neu", "autoclear_hours": 2})
    pruefe(code == 400 and "3" in out["error"], "ungueltige Stunden werden serverseitig nicht abgelehnt")
    pruefe(chat(BESITZER)["nick"] == "Helmut", "bei einem Fehler wurde ein Teil trotzdem gespeichert")
    code, out = web("POST", "/settings", a, {"remind_first": 5, "remind_second": 5})
    pruefe(code == 400 and chat(BESITZER)["remind"] == [60, 10], "gleiche Erinnerungszeiten werden akzeptiert")
    # nur eigene Einstellungen: cid, Rolle und Co. aus der Anfrage werden ignoriert
    code, _ = web("POST", "/settings", m, {"cid": BESITZER, "chat": BESITZER, "admin": True, "owner": MITGLIED, "nick": "Mitglied"})
    pruefe(chat(BESITZER)["nick"] == "Helmut" and chat(MITGLIED)["nick"] == "Mitglied", "Anfrage konnte fremde Einstellungen aendern")
    pruefe(not bot.is_manager(MITGLIED) and bot.data["owner"] == BESITZER, "Anfrage konnte die Rolle aendern")
    pruefe(web("GET", "/settings", m)[1]["admin"] is False, "Mitglied wurde Admin")


def test_web_wiederkehrend():
    neu()
    bot.save = lambda: None
    sent = []
    bot.send = lambda cid, text, **k: sent.append((cid, str(text)))
    m = session(MITGLIED, "m")
    plan = __import__("time").time() + 3600
    bot.get_chat(MITGLIED)["trips"].append({"wid": "w1", "key": "k1", "stop": "s", "stop_name": "Halt", "line": "S1", "dest": "Ziel", "kind": "bahn", "planned": plan})
    pruefe(web("POST", "/routines", "", {"key": "k1", "days": [0], "weeks": 2})[0] == 401, "Wiederkehrend ohne Anmeldung")
    code, out = web("POST", "/routines", m, {"key": "k1", "days": [4, 0, 0], "weeks": 2})
    r = out["routines"][0] if code == 200 and out["routines"] else {}
    pruefe(code == 200 and r.get("days") == "Mo, Fr" and r.get("until", "").startswith("bis "), f"Wiederkehrende Fahrt nicht gespeichert: {code} {out}")
    code, out = web("POST", "/routines", m, {"key": "k1", "days": [0, 1, 2, 3, 4], "weeks": None})
    pruefe(code == 200 and len(out["routines"]) == 1 and out["routines"][0]["until"] == "", "unbegrenzt ersetzt die Fahrt nicht")
    for schlecht in ({"key": "k1", "days": [], "weeks": 1}, {"key": "k1", "days": [7], "weeks": 1}, {"key": "k1", "days": [True], "weeks": 1},
                     {"key": "k1", "days": [0], "weeks": 0}, {"key": "k1", "days": [0], "weeks": "2"}, {"key": "k1", "days": "0", "weeks": 1}):
        try:
            web("POST", "/routines", m, schlecht)
            pruefe(False, f"ungueltige Eingabe angenommen: {schlecht}")
        except ValueError:
            pass
    pruefe(web("POST", "/routines", m, {"key": "fremd", "days": [0], "weeks": 1})[0] == 404, "Fahrt, die nicht verfolgt wird, wurde Routine")
    fremd = session(ADMIN, "a")
    pruefe(web("POST", "/routines", fremd, {"key": "k1", "days": [0], "weeks": 1})[0] == 404, "fremde verfolgte Fahrt benutzt")
    rid = out["routines"][0]["rid"]
    web("DELETE", "/routines", fremd, None, {"rid": [rid]})
    pruefe(len(bot.get_chat(MITGLIED)["routines"]) == 1, "fremde Routine geloescht")
    # Ablauf: abgelaufene Routinen verschwinden mit Meldung
    bot.get_chat(MITGLIED)["routines"][0]["until"] = "2000-01-01"
    bot.check_routines()
    pruefe(not bot.get_chat(MITGLIED)["routines"] and any("abgelaufen" in t for _, t in sent), "abgelaufene Routine bleibt")
    web("POST", "/routines", m, {"key": "k1", "days": [0], "weeks": 1})
    web("DELETE", "/routines", m, None, {"rid": [bot.get_chat(MITGLIED)["routines"][0]["rid"]]})
    pruefe(not bot.get_chat(MITGLIED)["routines"], "Routine nicht loeschbar")


def test_erst_nach_zehn_sekunden_melden():
    """Wer eine Fahrt gleich wieder abschaltet (Tippfehler), bekommt keine Nachricht."""
    import threading, time
    neu()
    bot.save = lambda: None
    ersten = []
    alt = bot.send_first_status, bot.TRACK_GRACE
    bot.send_first_status = lambda cid, w, event="status": ersten.append(w["key"])
    bot.TRACK_GRACE = 0.3
    try:
        for bleibt in (False, True):
            ersten.clear()
            w = {"wid": "w1", "key": "k1", "stop": "s", "stop_name": "H", "line": "S1", "dest": "Z", "kind": "bahn", "planned": time.time() + 600, "since": time.time()}
            bot.get_chat(MITGLIED)["trips"][:] = [w]
            bot.send_first_status_later(MITGLIED, w)
            if not bleibt:
                bot.get_chat(MITGLIED)["trips"][:] = []
            time.sleep(0.2)
            pruefe(not ersten, "Nachricht kam schon vor Ablauf der Wartezeit")
            time.sleep(0.4)
            pruefe(bool(ersten) == bleibt, f"Nachricht nach Wartezeit falsch (bleibt={bleibt}, gesendet={ersten})")
        # die Ueberwachung schweigt waehrend der Wartezeit
        w = {"wid": "w2", "key": "k2", "stop": "s", "stop_name": "H", "line": "S1", "dest": "Z", "kind": "bahn", "planned": time.time() + 600, "since": time.time()}
        bot.TRACK_GRACE = 5
        box = []
        bot.check_trip(str(MITGLIED), bot.get_chat(MITGLIED), w, {"s": [{"key": "k2"}]}, bot.datetime.now(bot.timezone.utc), box)
        pruefe(not box and str(MITGLIED) not in bot.data["state"], "Ueberwachung arbeitet in der Wartezeit")
    finally:
        bot.send_first_status, bot.TRACK_GRACE = alt


def test_web_mitglieder():
    neu()
    bot.data["guests"] = [GAST]
    a, v, m, g = session(BESITZER, "a"), session(ADMIN, "v"), session(MITGLIED, "m"), session(GAST, "g")
    for methode, pfad in (("GET", "/members"), ("POST", "/members/invite"), ("DELETE", "/members/invite"),
                          ("DELETE", "/members"), ("POST", "/members/role")):
        code, _ = web(methode, pfad, m, {"ref": "x", "role": "gast"}, {"code": ["ABCD"], "ref": ["x"]})
        pruefe(code == 403, f"Mitglied darf {methode} {pfad}")
        code, _ = web(methode, pfad, g, {}, {})
        pruefe(code == 401, f"Gast hat Zugang zu {methode} {pfad}")
        code, _ = web(methode, pfad, "", {}, {})
        pruefe(code == 401, f"ohne Anmeldung ist {methode} {pfad} erreichbar")
    code, out = web("GET", "/members", a)
    rollen = {r["role"] for r in out["members"]}
    pruefe(code == 200 and rollen == {"Besitzer", "Admin", "Mitglied", "Gast"}, f"Liste der Personen unvollstaendig: {rollen}")
    pruefe(str(ADMIN) not in json.dumps(out) and str(MITGLIED) not in json.dumps(out) and str(GAST) not in json.dumps(out), "Chat-IDs stehen in der Antwort")
    zeile = {r["role"]: r for r in out["members"]}
    pruefe(zeile["Besitzer"]["roles"] == [] and not zeile["Besitzer"]["removable"], "der Besitzer ist aenderbar")
    pruefe(zeile["Admin"]["roles"] == ["admin", "verwalter", "mitglied", "gast"] and zeile["Gast"]["removable"], "Besitzer darf nicht alles unter sich")
    out_v = web("GET", "/members", v)[1]
    zv = {r["role"]: r for r in out_v["members"]}
    pruefe(zv["Mitglied"]["roles"] == ["verwalter", "mitglied", "gast"] and zv["Mitglied"]["removable"], "Admin sieht falsche Rollenauswahl")
    pruefe(zv["Admin"]["roles"] == [] and not zv["Admin"]["removable"] and zv["Besitzer"]["roles"] == [], "Admin darf sich, Admin oder Besitzer aendern")
    # Einladung: erstellen, in Liste, widerrufen, nicht mehr einloesbar
    code, out = web("POST", "/members/invite", a)
    pruefe(code == 200 and len(out["invites"]) == 1, "Einladung erstellen funktioniert nicht")
    einladung = out["invites"][0]["code"]
    pruefe(not bot.is_manager(MITGLIED), "Einladung vergibt Rechte")
    code, out = web("DELETE", "/members/invite", a, None, {"code": [einladung]})
    pruefe(code == 200 and not out["invites"] and out["history"][0]["status"] == "widerrufen", "Widerrufen funktioniert nicht oder fehlt im Verlauf")
    bot.cmd_code(FREMD, einladung, {"first_name": "Neu"})
    pruefe(FREMD not in bot.data["members"], "widerrufene Einladung wurde eingeloest")
    # einloesen: nur einmal, danach Verlauf, Einlader wird angezeigt
    einladung = web("POST", "/members/invite", v)[1]["invites"][0]["code"]
    bot.cmd_code(FREMD, einladung, {"first_name": "Neu"})
    pruefe(FREMD in bot.data["members"] and bot.rang(FREMD) == 2 and not bot.can_invite(FREMD), "eingeloeste Einladung gibt falsche Rechte")
    bot.cmd_code(MITGLIED + 1, einladung, {"first_name": "Zweiter"})
    pruefe(MITGLIED + 1 not in bot.data["members"], "Einladung ist mehrfach einloesbar")
    pruefe(web("GET", "/members", a)[1]["history"][0]["status"] == "eingelöst", "eingeloeste Einladung fehlt im Verlauf")
    neu_zeile = next(r for r in web("GET", "/members", a)[1]["members"] if r["name"] == "Neu")
    pruefe(neu_zeile["invited_by"] != "" and neu_zeile["role"] == "Mitglied", "es wird nicht angezeigt, wer eingeladen hat")
    # Ablauf
    einladung = web("POST", "/members/invite", a)[1]["invites"][0]["code"]
    bot.data["invites"][einladung]["exp"] = time.time() - 1
    bot.cmd_code(MITGLIED + 2, einladung, {"first_name": "Spaet"})
    pruefe(MITGLIED + 2 not in bot.data["members"], "abgelaufene Einladung wurde eingeloest")
    pruefe(not web("GET", "/members", a)[1]["invites"], "abgelaufene Einladung steht noch als offen in der Liste")
    bot.cleanup_shares()
    pruefe(any(h["status"] == "abgelaufen" for h in web("GET", "/members", a)[1]["history"]), "abgelaufene Einladung fehlt im Verlauf")
    # Rollen ueber das Dropdown (POST /members/role)
    ref_m, ref_g = bot.member_ref(MITGLIED), bot.member_ref(GAST)
    code, out = web("POST", "/members/role", a, {"ref": ref_m, "role": "admin"})
    pruefe(code == 200 and bot.is_admin(MITGLIED) and any(r["role"] == "Admin" and r["name"] == bot.web_name(MITGLIED) for r in out["members"]), "Besitzer konnte keinen Admin ernennen")
    code, _ = web("POST", "/members/role", m, {"ref": ref_g, "role": "mitglied"})
    pruefe(code == 200 and GAST in bot.data["members"], "ein neuer Admin kann nicht verwalten")
    # Admin: keine Ernennung, keine Aenderung an Adminn und Besitzer
    for ziel in (bot.member_ref(BESITZER), bot.member_ref(ADMIN), bot.member_ref(MITGLIED)):
        for rolle in ("admin", "mitglied", "gast", "besitzer", "x", None):
            code, _ = web("POST", "/members/role", v, {"ref": ziel, "role": rolle})
            pruefe(code == 400, f"Admin konnte {ziel} die Rolle {rolle!r} geben")
        pruefe(web("DELETE", "/members", v, None, {"ref": [ziel]})[0] == 400, f"Admin konnte {ziel} entfernen")
    pruefe(bot.data["owner"] == BESITZER and bot.is_admin(ADMIN) and bot.is_admin(MITGLIED), "Admin hat Besitzer oder Admin veraendert")
    code, _ = web("POST", "/members/role", v, {"ref": ref_g, "role": "admin"})
    pruefe(code == 400 and not bot.is_admin(GAST), "Admin konnte jemanden zum Admin machen")
    # Besitzer kann niemals entfernt oder umbesetzt werden, auch nicht vom Besitzer
    for rolle in ("admin", "mitglied", "gast", "besitzer"):
        pruefe(web("POST", "/members/role", a, {"ref": bot.member_ref(BESITZER), "role": rolle})[0] == 400, f"Besitzer bekam die Rolle {rolle}")
    pruefe(web("DELETE", "/members", a, None, {"ref": [bot.member_ref(BESITZER)]})[0] == 400, "Besitzer konnte entfernt werden")
    # Admin wirft Mitglieder und Gaeste raus
    fremd_ref = bot.member_ref(FREMD)
    pruefe(web("DELETE", "/members", v, None, {"ref": [fremd_ref]})[0] == 200 and FREMD not in bot.data["members"], "Admin konnte kein Mitglied rauswerfen")
    code, _ = web("POST", "/members/role", v, {"ref": ref_g, "role": "gast"})
    pruefe(code == 200 and GAST in bot.data["guests"] and GAST not in bot.data["members"], "Admin konnte kein Mitglied zum Gast machen")
    # Einladungen: Admin widerrufen nur eigene und die von Personen unter sich
    vcode = web("POST", "/members/invite", v)[1]["invites"][-1]["code"]
    bcode = web("POST", "/members/invite", a)[1]["invites"][-1]["code"]
    pruefe(web("DELETE", "/members/invite", v, None, {"code": [bcode]})[0] == 400 and bcode in bot.data["invites"], "Admin konnte eine Einladung des Besitzers widerrufen")
    pruefe(web("DELETE", "/members/invite", v, None, {"code": [vcode]})[0] == 200, "Admin konnte die eigene Einladung nicht widerrufen")
    pruefe(web("DELETE", "/members", a, None, {"ref": [bot.member_ref(MITGLIED)]})[0] == 200 and not bot.is_full(MITGLIED), "Besitzer konnte einen Admin nicht entfernen")
    code, _ = web("DELETE", "/members", a, None, {"ref": ["gibtesnicht"]})
    pruefe(code == 400, "unbekannte Kennung wird nicht abgelehnt")
    pruefe(web("GET", "/members", v)[0] == 200, "Admin kann die Mitgliederverwaltung nicht oeffnen")


def test_telegram_mitglieder():
    neu()
    bot.cmd_mitglieder(BESITZER)
    daten = [k["callback_data"] for z in GESENDET[-1][2]["reply_markup"]["inline_keyboard"] for k in z]
    pruefe("mn:0" in daten and "mo:0" in daten and f"md:{MITGLIED}" in daten, "Verwaltungsknoepfe fehlen")
    for wer in (MITGLIED, GAST, FREMD):
        for aktion in ("mn:0", "mo:0", f"md:{MITGLIED}", "mx:ABCD", f"mrm:{MITGLIED}", f"mr:{MITGLIED}:a", "mm:0"):
            knopf(wer, aktion)
            pruefe(not TG and not GESENDET, f"{wer} konnte mit {aktion} etwas ausloesen")
    pruefe(MITGLIED in bot.data["members"] and not bot.data["invites"], "unberechtigte Knoepfe haben Daten veraendert")
    knopf(BESITZER, "mn:0")
    pruefe(len(bot.data["invites"]) == 1 and "/code" in GESENDET[-1][1], "neue Einladung per Knopf funktioniert nicht")
    code = next(iter(bot.data["invites"]))
    knopf(BESITZER, "mo:0")
    pruefe(code in [p for m, p in TG if m == "editMessageText"][-1]["text"], "offene Einladung wird nicht angezeigt")
    knopf(BESITZER, f"mx:{code}")
    pruefe(code not in bot.data["invites"], "Widerrufen per Knopf funktioniert nicht")
    knopf(BESITZER, f"md:{MITGLIED}")
    pruefe("Mitglied" in [p for m, p in TG if m == "editMessageText"][-1]["text"], "Details werden nicht angezeigt")
    knopf(BESITZER, f"md:{ADMIN}")
    pruefe("Admin" in [p for m, p in TG if m == "editMessageText"][-1]["text"], "Details des Admins fehlen")


def test_neustart_und_migration():
    neu()
    bot.cfg["data_file"] = "test_neustart.json"
    bot.save = ORIG_SAVE
    bot.apply_settings(BESITZER, {"nick": "Helmut", "autoclear_on": True, "autoclear_hours": 5, "remind_first": 30, "remind_second": 10})
    bot.note_invite(BESITZER, "widerrufen")
    bot.save()
    bot.data.clear()
    bot.data.update({"owner": None, "chats": {}, "state": {}, "invites": {}})
    bot.load()
    bot.migrate()
    c = chat(BESITZER)
    pruefe(c["nick"] == "Helmut" and bot.clear_hours(BESITZER) == 5 and bot.active_remind(c) == [30, 10] and c["autoclear"], "Einstellungen ueberleben den Neustart nicht")
    pruefe(bot.data["invite_hist"][-1]["status"] == "widerrufen", "Einladungsverlauf ueberlebt den Neustart nicht")
    # Alte Daten ohne neue Felder: Standardwerte, nichts geht verloren
    alt = {"favs": [{"sid": "a", "id": "b", "name": "c", "mode": "all", "seeded": False}], "threshold": 5, "paused": False, "autoclear": True,
           "trips": [], "routines": []}
    bot.data["chats"]["77"] = json.loads(json.dumps(alt))
    bot.data.pop("invite_hist", None)
    bot.migrate()
    bot.migrate()                                                 # wiederholbar
    pruefe(bot.data["chats"]["77"] == alt, "Migration hat bestehende Daten veraendert")
    pruefe(bot.clear_hours(77) == 47 and bot.active_remind(bot.data["chats"]["77"]) == [15, 5] and not nick_von(77), "bestehende Nutzer behalten die Standardwerte nicht")
    # Verwalter heissen jetzt Admins, Rollen von Nicht-Mitgliedern verfallen
    bot.data["members"] = [MITGLIED, ADMIN]
    bot.data["verwalter"] = [MITGLIED, FREMD]
    bot.data["admins"] = []
    bot.data["inviters"] = [ADMIN, FREMD]
    bot.migrate()
    pruefe("verwalter" not in bot.data and bot.data["admins"] == [MITGLIED] and bot.data["inviters"] == [ADMIN] and bot.data["invited_by"] == {}, "Migration der Rollen stimmt nicht")
    bot.migrate()
    pruefe(bot.data["admins"] == [MITGLIED] and bot.data["inviters"] == [ADMIN], "Migration ist nicht wiederholbar")
    # kaputte gespeicherte Werte fallen auf den Standard
    bot.data["chats"]["88"] = {"autoclear_h": 1, "remind": [3, 9], "nick": "x" * 99, "favs": [], "trips": []}
    bot.migrate()
    c = bot.data["chats"]["88"]
    pruefe("autoclear_h" not in c and "remind" not in c and "nick" not in c, "ungueltige gespeicherte Werte bleiben stehen")
    pruefe(bot.clear_hours(88) == 47 and bot.active_remind(c) == [15, 5], "ungueltige gespeicherte Werte wirken sich aus")


def nick_von(cid):
    return bot.nick_of(cid)


def test_keine_geheimnisse():
    neu()
    bot.apply_settings(BESITZER, {"nick": "Helmut"})
    ausgaben = []
    for ansicht in ("m", "p", "c", "r", "r1", "r2"):
        ausgaben.append(str(bot.st_view(BESITZER, ansicht)))
    a = session(BESITZER, "a")
    ausgaben.append(json.dumps(web("GET", "/settings", a)[1]))
    ausgaben.append(json.dumps(web("GET", "/members", a)[1]))
    alles = "\n".join(ausgaben)
    pruefe("GEHEIM" not in alles, "der Token erscheint in einer Ausgabe")
    pruefe(HIER not in alles, "ein Dateipfad erscheint in einer Ausgabe")


ORIG_SAVE = bot.save


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            n0 = len(FEHLER)
            try:
                fn()
            except Exception as e:                  # ein Programmfehler gilt als Testfehler
                import traceback
                traceback.print_exc()
                FEHLER.append(f"{name}: {e!r}")
            print(("ok  " if len(FEHLER) == n0 else "FAIL"), name)
    if FEHLER:
        print(f"\n{len(FEHLER)} Fehler.")
        sys.exit(1)
    print("\nAlle Einstellungstests bestanden.")
