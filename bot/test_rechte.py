#!/usr/bin/env python3
"""Rechtetest fuer den Bot. Nur Standardbibliothek. Ausfuehren: python3 bot/test_rechte.py

Muss vor jedem Commit laufen. Der Test leitet Befehle und Knopf-Aktionen automatisch aus dem Code ab
(HELP, Befehlsverteiler, Callback-Aktionen). Kommt eine neue Funktion dazu, die den Verwalter ausschliesst,
schlaegt er fehl.
"""
import ast
import importlib.util
import os
import re
import sys
import tempfile

HIER = os.path.dirname(os.path.abspath(__file__))
QUELLE = os.path.join(HIER, "abfahrtsbot.py")

spec = importlib.util.spec_from_file_location("abfahrtsbot", QUELLE)
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)
bot.HERE = tempfile.mkdtemp()          # keine echte config.json oder data.json lesen oder schreiben
for _name in ("BOT_TOKEN", "BOT_OWNER_ID", "VERWALTER_ID", "BOT_DATA_FILE"):
    os.environ.pop(_name, None)
bot.load_config()                      # nur die Standardwerte
bot.cfg["ipv4_only"] = False
bot.cfg["data_file"] = "test_data.json"

ORIG_SEND = bot.send                   # echtes send(), andere Tests ersetzen es durch Attrappen
BESITZER, VERWALTER, MITGLIED, GAST, FREMD = 1001, 2002, 3003, 4004, 5005
FEHLER = []


def pruefe(bedingung, text):
    if not bedingung:
        FEHLER.append(text)
        print("FEHLER:", text)


def neu(verwalter=VERWALTER):
    """Frischer Zustand: Besitzer, ein Mitglied, ein Gast. Der Verwalter steht nirgends in den Daten."""
    bot.data.clear()
    bot.data.update({"owner": BESITZER, "chats": {}, "state": {}, "pending": {}, "menus": {}, "deps": {},
                     "guests": [GAST], "shares": {}, "members": [MITGLIED], "invites": {}, "names": {},
                     "sessions": {}, "inviters": [], "msglog": {}})
    bot.cfg["verwalter_id"] = verwalter


def befehle_und_aktionen():
    """Alle Befehle und Aktionen aus dem Code ableiten."""
    baum = ast.parse(open(QUELLE, encoding="utf-8").read())
    befehle = set(re.findall(r"^(/[a-z]+)", bot.HELP + "\n" + bot.GUEST_HELP, re.M))
    aktionen = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Dict):              # Befehlsverteiler: Woerterbuch mit Schluesseln wie "/suche"
            for k in knoten.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str) and re.fullmatch(r"/[a-z]+", k.value):
                    befehle.add(k.value)
        if isinstance(knoten, ast.Compare) and isinstance(knoten.left, ast.Name) and knoten.left.id == "action":
            for teil in knoten.comparators:           # action == "x" oder action in ("x", "y")
                for c in ast.walk(teil):
                    if isinstance(c, ast.Constant) and isinstance(c.value, str):
                        aktionen.add(c.value)
    for name in dir(bot):                             # Konstanten wie GUEST_ACTIONS, ROUTINE_ACTIONS, SHARE_ACTIONS
        if name.endswith("_ACTIONS"):
            aktionen |= set(getattr(bot, name))
    befehle |= set(bot.OWNER_COMMANDS) | set(bot.VERWALTER_COMMANDS) | set(bot.GUEST_COMMANDS)
    aktionen |= {"mrm", "wl"}
    return sorted(befehle), sorted(aktionen)


def test_ohne_verwalter():
    for wert in (None, "", "0", "abc", "1.5", "  ", True):
        os.environ.pop("VERWALTER_ID", None)
        if wert is not None:
            os.environ["VERWALTER_ID"] = str(wert)
        bot.cfg["verwalter_id"] = 0
        bot.cfg["verwalter_id"] = bot.parse_id(os.environ.get("VERWALTER_ID") or 0)
        neu(verwalter=bot.cfg["verwalter_id"])
        pruefe(not bot.is_verwalter(VERWALTER), f"ohne gueltige VERWALTER_ID ({wert!r}) darf es keinen Verwalter geben")
        pruefe(not bot.is_full(VERWALTER), "ohne Verwalter hat die ID keinen Zugriff")
    os.environ.pop("VERWALTER_ID", None)
    pruefe(bot.parse_id("123") == 123 and bot.parse_id(" -5 ") == -5, "gueltige Zahlen werden gelesen")
    # Umgebungsvariable hat Vorrang vor config.json
    os.environ["VERWALTER_ID"] = "777"
    bot.HERE = tempfile.mkdtemp()
    with open(os.path.join(bot.HERE, "config.json"), "w", encoding="utf-8") as f:
        f.write('{"verwalter_id": 555}')
    bot.load_config()
    pruefe(bot.cfg["verwalter_id"] == 777, "VERWALTER_ID aus der Umgebung hat Vorrang vor config.json")
    os.environ.pop("VERWALTER_ID", None)
    bot.load_config()
    pruefe(bot.cfg["verwalter_id"] == 555, "ohne Umgebungsvariable gilt verwalter_id aus config.json")
    bot.HERE = tempfile.mkdtemp()


def test_zugriff_ohne_einladung():
    neu()
    pruefe(bot.is_verwalter(VERWALTER), "Verwalter wird erkannt")
    pruefe(bot.is_full(VERWALTER) and bot.is_admin(VERWALTER), "Verwalter hat vollen Zugriff ohne Einladung")
    pruefe(VERWALTER not in bot.data["members"], "Verwalter steht nicht in den Mitgliedern (nichts in data.json)")
    pruefe(not bot.is_full(FREMD), "Fremde haben keinen Zugriff")


def test_mindestens_besitzerrechte():
    neu()
    befehle, aktionen = befehle_und_aktionen()
    pruefe(len(befehle) >= 10 and len(aktionen) >= 10, f"Ableitung aus dem Code liefert zu wenig ({len(befehle)} Befehle, {len(aktionen)} Aktionen)")
    for b in befehle:
        if bot.may_use(BESITZER, cmd=b):
            pruefe(bot.may_use(VERWALTER, cmd=b), f"Verwalter darf Befehl {b} nicht, obwohl der Besitzer ihn darf")
        pruefe(bot.may_use(VERWALTER, cmd=b), f"Verwalter darf Befehl {b} nicht")
    for a in aktionen:
        if bot.may_use(BESITZER, action=a):
            pruefe(bot.may_use(VERWALTER, action=a), f"Verwalter darf Aktion {a} nicht, obwohl der Besitzer sie darf")
        pruefe(bot.may_use(VERWALTER, action=a), f"Verwalter darf Aktion {a} nicht")
    for b in bot.VERWALTER_COMMANDS:
        pruefe(not bot.may_use(BESITZER, cmd=b), f"Besitzer darf den Verwalter-Befehl {b} nicht nutzen")
        pruefe(not bot.may_use(MITGLIED, cmd=b), f"Mitglied darf den Verwalter-Befehl {b} nicht nutzen")


def test_mitglieder_duerfen_nichts_verwalten():
    neu()
    befehle, aktionen = befehle_und_aktionen()
    for b in bot.OWNER_COMMANDS | bot.VERWALTER_COMMANDS:
        pruefe(not bot.may_use(MITGLIED, cmd=b), f"Mitglied darf {b} nicht")
        pruefe(not bot.may_use(GAST, cmd=b), f"Gast darf {b} nicht")
        pruefe(not bot.may_use(FREMD, cmd=b), f"Fremde duerfen {b} nicht")
    for a in bot.ADMIN_ACTIONS:
        pruefe(not bot.may_use(MITGLIED, action=a), f"Mitglied darf die Verwaltungs-Aktion {a} nicht")
        pruefe(not bot.may_use(GAST, action=a), f"Gast darf die Verwaltungs-Aktion {a} nicht")
        pruefe(bot.may_use(BESITZER, action=a) and bot.may_use(VERWALTER, action=a), f"Besitzer oder Verwalter darf {a} nicht")
    pruefe(not bot.is_admin(MITGLIED), "Mitglied ist kein Admin")
    pruefe(bot.may_use(BESITZER, action="mrm"), "Besitzer darf Mitglieder entfernen")
    for a in aktionen:
        pruefe(not bot.may_use(FREMD, action=a), f"Fremde duerfen Aktion {a} nicht")


def test_nicht_entfernbar():
    neu()
    bot.data["members"].append(VERWALTER)          # selbst wenn er zusaetzlich als Mitglied eingetragen waere
    bot.data["names"][str(VERWALTER)] = "Betreiber"
    bot.data["chats"][str(VERWALTER)] = {"favs": [], "trips": [], "routines": [], "paused": False}
    gesendet = []
    bot.tg = lambda methode, **p: gesendet.append((methode, p))
    bot.save = lambda: None
    karte = {"id": "x", "message": {"chat": {"id": BESITZER}, "message_id": 1}}
    for wer in (BESITZER, VERWALTER):
        bot.handle_member_admin_callback(karte, wer, 1, "mrm", str(VERWALTER))
        pruefe(VERWALTER in bot.data["members"], f"Verwalter wurde von {wer} entfernt")
        pruefe(str(VERWALTER) in bot.data["chats"], "Daten des Verwalters wurden geloescht")
    bot.handle_member_admin_callback(karte, MITGLIED, 1, "mrm", str(VERWALTER))
    pruefe(VERWALTER in bot.data["members"], "Mitglied konnte den Verwalter entfernen")
    pruefe(bot.is_full(VERWALTER), "Verwalter hat nach Entfernversuchen keinen Zugriff mehr")
    tasten = [k["callback_data"] for z in bot.members_keyboard()["inline_keyboard"] for k in z]
    pruefe(f"mrm:{VERWALTER}" not in tasten, "in /mitglieder gibt es einen Entfernen-Knopf fuer den Verwalter")
    # Mitglieder koennen nicht von Mitgliedern entfernt werden, aber vom Besitzer schon
    bot.handle_member_admin_callback(karte, MITGLIED, 1, "mrm", str(MITGLIED))
    pruefe(MITGLIED in bot.data["members"], "Mitglied konnte sich oder andere ohne Rechte entfernen")
    bot.handle_member_admin_callback(karte, BESITZER, 1, "mrm", str(MITGLIED))
    pruefe(MITGLIED not in bot.data["members"], "Besitzer kann Mitglieder nicht mehr entfernen")
    # Sitzungen des Verwalters bleiben, wenn andere entfernt werden
    neu()
    bot.data["sessions"] = {"a" * 64: {"cid": VERWALTER, "exp": 9e12}, "b" * 64: {"cid": MITGLIED, "exp": 9e12}}
    bot.handle_member_admin_callback(karte, BESITZER, 1, "mrm", str(MITGLIED))
    pruefe(("a" * 64) in bot.data["sessions"], "Sitzung des Verwalters wurde beendet")
    pruefe(("b" * 64) not in bot.data["sessions"], "Sitzung des entfernten Mitglieds blieb bestehen")


def test_mitglieder_anzeige():
    neu()
    gesendet = []
    bot.send = lambda cid, text, **k: gesendet.append(text)
    bot.cmd_mitglieder(BESITZER)
    pruefe("Verwalter" in gesendet[-1], "/mitglieder markiert den Verwalter nicht")
    pruefe(str(VERWALTER) not in gesendet[-1], "/mitglieder gibt die ID des Verwalters aus")
    pruefe(bot.who(VERWALTER) == "Verwalter", "who() gibt die ID des Verwalters aus")
    anzahl = len(gesendet)
    bot.cmd_mitglieder(MITGLIED)
    pruefe(len(gesendet) == anzahl, "Mitglied konnte /mitglieder aufrufen")


def test_rolle_nicht_aenderbar():
    """Keine Funktion setzt die Rolle. Es gibt auch keinen Eintrag dazu in den Daten."""
    baum = ast.parse(open(QUELLE, encoding="utf-8").read())
    for fn in [n for n in ast.walk(baum) if isinstance(n, ast.FunctionDef)]:
        for knoten in ast.walk(fn):
            ziele = []
            if isinstance(knoten, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                ziele = knoten.targets if isinstance(knoten, ast.Assign) else [knoten.target]
            if isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Attribute) and knoten.func.attr in ("update", "setdefault", "pop"):
                ziele = [knoten.func.value]
            for z in ziele:
                text = ast.unparse(z)
                if "verwalter" in text.lower() and fn.name != "load_config":
                    pruefe(False, f"{fn.name} veraendert die Verwalter-Rolle ({text})")
    quelle = open(QUELLE, encoding="utf-8").read()
    pruefe('data["verwalter' not in quelle and "data['verwalter" not in quelle, "Verwalter-Rolle wird in data gespeichert")
    neu()
    bot.cfg["verwalter_id"] = VERWALTER
    for zustand in (bot.data,):
        pruefe(not any("verwalter" in str(k).lower() for k in zustand), "data enthaelt einen Verwalter-Eintrag")
        pruefe(str(VERWALTER) not in str(zustand), "die Verwalter-ID steht in data")


def test_keine_umgehung_der_pruefung():
    """Neue Rechtepruefungen duerfen nicht nur den Besitzer meinen."""
    baum = ast.parse(open(QUELLE, encoding="utf-8").read())
    erlaubt = {"is_admin", "may_use"}
    for fn in [n for n in ast.walk(baum) if isinstance(n, ast.FunctionDef)]:
        for knoten in ast.walk(fn):
            if isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Name) and knoten.func.id == "is_owner" and fn.name not in erlaubt:
                pruefe(False, f"{fn.name} prueft nur is_owner. Bitte is_admin nutzen, damit der Verwalter eingeschlossen ist")
            if isinstance(knoten, ast.Compare) and "data[\"owner\"]" in ast.unparse(knoten.left) \
                    and not all(isinstance(o, (ast.Is, ast.IsNot)) for o in knoten.ops) and fn.name != "is_owner":
                pruefe(False, f"{fn.name} vergleicht data[\"owner\"] direkt. Bitte is_admin nutzen")


def test_keine_geheimnisse_in_texten():
    neu()
    bot.cfg["bot_token"] = "123456:GEHEIMER-TOKEN"
    gesendet = []
    bot.send = lambda cid, text, **k: gesendet.append(str(text))
    bot.tg = lambda *a, **k: None
    bot.get_chat(BESITZER)
    for cmd in ("/status", "/hilfe", "/mitglieder"):
        bot.handle_message({"chat": {"id": BESITZER}, "text": cmd, "from": {"first_name": "Test"}})
    alles = "\n".join(gesendet)
    pruefe("GEHEIMER-TOKEN" not in alles, "der Token erscheint in einer Nachricht")
    pruefe(str(VERWALTER) not in alles, "die Verwalter-ID erscheint in einer Nachricht")
    pruefe(HIER not in alles, "ein Dateipfad erscheint in einer Nachricht")


def test_einlader():
    neu()
    nachrichten, aufrufe = [], []
    bot.send = lambda cid, text, **k: nachrichten.append((cid, str(text)))
    bot.tg = lambda methode, **p: aufrufe.append((methode, p))
    bot.save = lambda: None
    karte = {"id": "x", "message": {"chat": {"id": BESITZER}, "message_id": 1}}
    pruefe(not bot.can_invite(MITGLIED), "Mitglied darf ohne Erlaubnis einladen")
    bot.cmd_code(MITGLIED, "")
    pruefe(not bot.data["invites"], "Mitglied ohne Erlaubnis hat einen Einladungscode erzeugt")
    # Erlauben: nur Besitzer und Verwalter
    for wer in (MITGLIED, GAST, FREMD):
        bot.handle_member_admin_callback(karte, wer, 1, "mi", str(MITGLIED))
    pruefe(MITGLIED not in bot.data["inviters"], "ohne Berechtigung konnte jemand das Einladen erlauben")
    bot.handle_member_admin_callback(karte, BESITZER, 1, "mi", str(MITGLIED))
    pruefe(MITGLIED in bot.data["inviters"] and bot.can_invite(MITGLIED), "Besitzer konnte das Einladen nicht erlauben")
    bot.cmd_code(MITGLIED, "")
    pruefe(len(bot.data["invites"]) == 1 and all(i["by"] == MITGLIED for i in bot.data["invites"].values()), "Einlader erzeugt keinen Code")
    pruefe(not bot.may_use(MITGLIED, cmd="/mitglieder") and not bot.may_use(MITGLIED, action="mrm"), "Einlader darf Mitglieder verwalten")
    pruefe(not bot.may_use(MITGLIED, action="mi"), "Einlader darf das Einladen weitergeben")
    # Obergrenze offener Codes
    for _ in range(10):
        bot.cmd_code(MITGLIED, "")
    pruefe(len(bot.data["invites"]) == bot.MAX_OPEN_INVITES, f"Einlader hat {len(bot.data['invites'])} offene Codes statt {bot.MAX_OPEN_INVITES}")
    # Einloesen: neue Person ist normales Mitglied, Besitzer erfaehrt es
    code = next(iter(bot.data["invites"]))
    nachrichten.clear()
    bot.cmd_code(FREMD, code, {"first_name": "Neu"})
    pruefe(FREMD in bot.data["members"] and FREMD not in bot.data["inviters"], "eingeladene Person ist kein normales Mitglied")
    pruefe(not bot.can_invite(FREMD), "eingeladene Person darf sofort weiter einladen")
    pruefe(any(c == BESITZER and "eingeladen" in t for c, t in nachrichten), "Besitzer erfaehrt nicht, wer ueber einen Einlader kam")
    pruefe(not any(str(VERWALTER) in t for _, t in nachrichten), "die Verwalter-ID steht in einer Nachricht")
    # Verwalter und Nicht-Mitglieder koennen nicht zu Einladern gemacht werden
    bot.handle_member_admin_callback(karte, BESITZER, 1, "mi", str(VERWALTER))
    bot.handle_member_admin_callback(karte, BESITZER, 1, "mi", str(GAST))
    pruefe(VERWALTER not in bot.data["inviters"] and GAST not in bot.data["inviters"], "Verwalter oder Nicht-Mitglied wurde Einlader")
    # Entziehen loescht die offenen Codes
    bot.handle_member_admin_callback(karte, VERWALTER, 1, "mi", str(MITGLIED))
    pruefe(MITGLIED not in bot.data["inviters"] and not bot.can_invite(MITGLIED), "Entziehen hat nicht funktioniert")
    pruefe(not any(i["by"] == MITGLIED for i in bot.data["invites"].values()), "offene Codes blieben nach dem Entziehen gueltig")
    # Entfernen loescht die Berechtigung
    bot.handle_member_admin_callback(karte, BESITZER, 1, "mi", str(MITGLIED))
    bot.cmd_code(MITGLIED, "")
    bot.handle_member_admin_callback(karte, BESITZER, 1, "mrm", str(MITGLIED))
    pruefe(MITGLIED not in bot.data["inviters"] and not any(i["by"] == MITGLIED for i in bot.data["invites"].values()), "Entfernen laesst Einlader-Rechte oder Codes zurueck")
    pruefe(not bot.can_invite(MITGLIED), "entferntes Mitglied darf noch einladen")
    pruefe("inviters" in bot.data and "verwalter" not in str(bot.data).lower(), "Rolle des Verwalters steht in data")


def tasten(cid):
    return [k["text"] for z in bot.menu_keyboard(cid)["keyboard"] for k in z]


def test_menue_tasten_je_rolle():
    neu()
    befehle, _ = befehle_und_aktionen()
    for text, cmd in bot.MENU_BUTTONS.items():
        pruefe(cmd in befehle, f"Taste {text} gehoert zu einem unbekannten Befehl {cmd}")
    einlader = MITGLIED + 1
    bot.data["members"].append(einlader)
    bot.data["inviters"].append(einlader)
    rollen = {"Besitzer": BESITZER, "Verwalter": VERWALTER, "Mitglied": MITGLIED, "Einlader": einlader, "Gast": GAST, "Fremder": FREMD}
    for name, cid in rollen.items():
        for t in tasten(cid):
            pruefe(t in bot.MENU_BUTTONS, f"{name}: unbekannte Taste {t}")
            cmd = bot.MENU_BUTTONS.get(t)
            erlaubt = t == "🎟 Code eingeben" or (t == "🔑 Einladen" and bot.can_invite(cid)) or                 (t != "🔑 Einladen" and bot.may_use(cid, cmd=cmd))
            pruefe(erlaubt, f"{name} sieht die Taste {t}, darf den Befehl {cmd} aber nicht")
    pruefe("👥 Mitglieder" in tasten(BESITZER) and "👥 Mitglieder" in tasten(VERWALTER), "Besitzer oder Verwalter sieht /mitglieder nicht")
    pruefe("🔑 Einladen" in tasten(BESITZER) and "🔑 Einladen" in tasten(VERWALTER), "Besitzer oder Verwalter sieht Einladen nicht")
    pruefe("👥 Mitglieder" not in tasten(MITGLIED) and "🔑 Einladen" not in tasten(MITGLIED), "normales Mitglied sieht Verwaltungstasten")
    pruefe("🔑 Einladen" in tasten(einlader) and "👥 Mitglieder" not in tasten(einlader), "Einlader sieht falsche Tasten")
    pruefe("🔍 Suche" not in tasten(GAST) and "⭐ Favoriten" not in tasten(GAST), "Gast sieht Tasten fuer Suche oder Favoriten")
    pruefe(tasten(FREMD) == ["🎟 Code eingeben"], "Fremde sehen mehr als die Code-Taste")
    pruefe(all(len(z) <= 3 for z in bot.menu_keyboard(BESITZER)["keyboard"]), "zu viele Tasten in einer Reihe")


def test_menue_ablauf():
    neu()
    aufrufe, nachrichten = [], []
    bot.send = lambda cid, text, **k: nachrichten.append((cid, str(text)))
    bot.save = lambda: None
    namen = ("cmd_favoriten", "cmd_suche", "cmd_code", "cmd_status", "cmd_mitglieder")
    originale = {n: getattr(bot, n) for n in namen}
    for name in namen:
        setattr(bot, name, (lambda n: lambda cid, *a: aufrufe.append((n, cid) + a))(name))
    try:
        menue_ablauf(aufrufe, nachrichten)
    finally:
        for n, f in originale.items():          # Attrappen wieder entfernen, damit andere Tests die echten Funktionen nutzen
            setattr(bot, n, f)


def menue_ablauf(aufrufe, nachrichten):

    def druecke(cid, text):
        bot.handle_message({"chat": {"id": cid}, "text": text, "from": {"first_name": "T"}})

    bot.awaiting.clear()
    druecke(MITGLIED, "⭐ Favoriten")
    pruefe(("cmd_favoriten", MITGLIED) in aufrufe, "Taste Favoriten startet den Befehl nicht")
    druecke(MITGLIED, "🔍 Suche")
    pruefe(MITGLIED in bot.awaiting and "Haltestelle" in nachrichten[-1][1], "Taste Suche fragt nicht nach dem Namen")
    druecke(MITGLIED, "Bocholt Bahnhof")
    pruefe(("cmd_suche", MITGLIED, "Bocholt Bahnhof") in aufrufe, "Antwort nach der Taste Suche startet die Suche nicht")
    pruefe(MITGLIED not in bot.awaiting, "die offene Frage blieb bestehen")
    druecke(MITGLIED, "einfach Text")
    pruefe(not any(a[0] == "cmd_suche" and a[2] == "einfach Text" for a in aufrufe), "freier Text loest ohne Taste eine Suche aus")
    # Befehl bricht eine offene Frage ab
    druecke(MITGLIED, "🔍 Suche")
    druecke(MITGLIED, "/status")
    pruefe(MITGLIED not in bot.awaiting and ("cmd_status", MITGLIED) in aufrufe, "ein Befehl brach die offene Frage nicht ab")
    # Frage laeuft ab
    bot.awaiting[MITGLIED] = ("/suche", __import__("time").time() - 1000)
    n = len([a for a in aufrufe if a[0] == "cmd_suche"])
    druecke(MITGLIED, "zu spaet")
    pruefe(len([a for a in aufrufe if a[0] == "cmd_suche"]) == n, "abgelaufene Frage wurde noch beantwortet")
    # Rechte gelten auch fuer Tasten
    aufrufe.clear()
    druecke(GAST, "⭐ Favoriten")
    druecke(GAST, "🔍 Suche")
    druecke(FREMD, "⭐ Favoriten")
    druecke(FREMD, "🔍 Suche")
    druecke(MITGLIED, "👥 Mitglieder")
    druecke(FREMD, "👥 Mitglieder")
    pruefe(not aufrufe, f"Tasten umgehen die Rechtepruefung: {aufrufe}")
    pruefe(GAST not in bot.awaiting and FREMD not in bot.awaiting, "Gast oder Fremder bekam eine offene Frage fuer Suche")
    druecke(FREMD, "🎟 Code eingeben")
    druecke(FREMD, "ABC234")
    pruefe(("cmd_code", FREMD, "ABC234", {"first_name": "T"}) in aufrufe, "Code-Taste funktioniert nicht fuer Fremde")
    druecke(BESITZER, "👥 Mitglieder")
    druecke(VERWALTER, "👥 Mitglieder")
    pruefe(("cmd_mitglieder", BESITZER) in aufrufe and ("cmd_mitglieder", VERWALTER) in aufrufe, "Besitzer oder Verwalter erreicht Mitglieder nicht")


def test_auto_loeschen():
    import time as zeit
    neu()
    bot.save = lambda: None
    bot.time.sleep = lambda s: None
    bot.msg_log.clear()
    zaehler = [100]

    def fake_tg(methode, **p):
        aufrufe.append((methode, p))
        if methode == "sendMessage":
            zaehler[0] += 1
            return {"message_id": zaehler[0]}

    aufrufe = []
    bot.tg = fake_tg
    bot.send = ORIG_SEND
    pruefe(bot.AUTOCLEAR_HOURS < 48, "Auto-Loeschen liegt nicht unter der 48-Stunden-Grenze von Telegram")
    for wer in (BESITZER, VERWALTER, MITGLIED, GAST):
        pruefe(bot.may_use(wer, cmd="/autoclear") and bot.may_use(wer, action="ac"), f"/autoclear ist fuer {wer} nicht erlaubt")
    pruefe(not bot.may_use(FREMD, cmd="/autoclear") and not bot.may_use(FREMD, action="ac"), "Fremde duerfen /autoclear nutzen")
    # Standard: aus, es wird nichts gemerkt
    bot.send(BESITZER, "x")
    pruefe(not bot.msg_log, "ohne Auto-Loeschen werden Nachrichten-IDs gespeichert")
    # Einschalten per Befehl: ab jetzt wird gemerkt (gesendet und empfangen)
    bot.cmd_autoclear(BESITZER, "an")
    pruefe(bot.autoclear_on(BESITZER), "Auto-Loeschen liess sich nicht einschalten")
    bot.send(BESITZER, "y")
    bot.log_message(BESITZER, 7)
    ids = [e[0] for e in bot.msg_log.get(BESITZER, [])]
    pruefe(len(ids) >= 2 and 7 in ids, "gesendete oder empfangene Nachrichten werden nicht gemerkt")
    pruefe(MITGLIED not in bot.msg_log, "ein anderer Chat wird mitprotokolliert")
    # Ablauf: nur Nachrichten ab 47 Stunden werden geloescht
    jetzt = zeit.time()
    bot.msg_log[BESITZER] = [[1, jetzt - 47 * 3600 - 10], [2, jetzt - 170000], [3, jetzt - 1000], [4, jetzt - 46 * 3600]]
    aufrufe.clear()
    bot.autoclear_step()
    geloescht = sorted(i for m, p in aufrufe if m == "deleteMessages" for i in p["message_ids"])
    pruefe(geloescht == [1, 2], f"falsche Nachrichten geloescht: {geloescht}")
    pruefe(sorted(e[0] for e in bot.msg_log[BESITZER]) == [3, 4], "neuere Nachrichten wurden aus dem Protokoll genommen")
    pruefe(sorted(e[0] for e in bot.data["msglog"][str(BESITZER)]) == [3, 4], "das Protokoll wird nicht in data gesichert")
    # Batches
    bot.msg_log[BESITZER] = [[i, jetzt - 200000] for i in range(1, 251)]
    aufrufe.clear()
    bot.autoclear_step()
    pruefe(len(aufrufe) == 3 and all(len(p["message_ids"]) <= 100 for _, p in aufrufe), "Batches sind falsch geschnitten")
    pruefe(all(p["chat_id"] == BESITZER for _, p in aufrufe), "Auto-Loeschen loescht in einem fremden Chat")
    # Obergrenze
    bot.msg_log.clear()
    for i in range(bot.AUTOCLEAR_MAX + 500):
        bot.log_message(BESITZER, i + 1)
    pruefe(len(bot.msg_log[BESITZER]) == bot.AUTOCLEAR_MAX, "das Protokoll waechst ohne Grenze")
    # Ausschalten raeumt auf
    bot.cmd_autoclear(BESITZER, "aus")
    pruefe(not bot.autoclear_on(BESITZER) and BESITZER not in bot.msg_log, "Ausschalten raeumt das Protokoll nicht auf")
    bot.msg_log[BESITZER] = [[1, jetzt - 200000]]
    aufrufe.clear()
    bot.autoclear_step()
    pruefe(not any(m == "deleteMessages" for m, _ in aufrufe), "bei ausgeschaltetem Auto-Loeschen wird geloescht")
    # Knopf: jeder fuer sich, Fremde nicht
    karte = {"id": "x", "message": {"chat": {"id": MITGLIED}, "message_id": 1}}
    bot.handle_autoclear_callback(karte, MITGLIED, 1, "1")
    pruefe(bot.autoclear_on(MITGLIED), "Knopf schaltet Auto-Loeschen nicht ein")
    bot.handle_autoclear_callback(karte, FREMD, 1, "1")
    pruefe(not bot.autoclear_on(FREMD) and str(FREMD) not in bot.data["chats"], "Fremde konnten Auto-Loeschen einschalten")
    bot.handle_autoclear_callback(karte, MITGLIED, 1, "0")
    pruefe(not bot.autoclear_on(MITGLIED), "Knopf schaltet Auto-Loeschen nicht aus")
    # /clear leert das Protokoll
    bot.cmd_autoclear(BESITZER, "an")
    bot.log_message(BESITZER, 5)
    bot.rate_hits.clear()
    bot.cmd_clear(BESITZER, 10)
    pruefe(not bot.msg_log.get(BESITZER) or all(e[0] > 10 for e in bot.msg_log[BESITZER]), "/clear laesst alte IDs im Protokoll")
    bot.send = lambda cid, text, **k: None


def test_abfahrten_fragt_nach_haltestelle():
    neu()
    suchen, texte = [], []
    bot.save = lambda: None
    bot.send = lambda cid, text, **k: texte.append(str(text))
    bot.search_stops = lambda t: suchen.append(t) or []
    bot.awaiting.clear()

    def sende(cid, text):
        bot.handle_message({"chat": {"id": cid}, "text": text, "from": {"first_name": "T"}})

    sende(MITGLIED, "🚏 Abfahrten")
    pruefe(MITGLIED in bot.awaiting and "Haltestelle" in texte[-1], "Taste Abfahrten fragt nicht nach der Haltestelle")
    sende(MITGLIED, "Bocholt")
    pruefe(suchen == ["Bocholt"], f"Antwort wurde nicht als Haltestelle gesucht: {suchen}")
    pruefe(MITGLIED not in bot.awaiting, "offene Frage blieb bestehen")
    sende(MITGLIED, "noch ein Text")
    pruefe(suchen == ["Bocholt"], "freier Text loest ohne Taste eine Suche aus")
    sende(GAST, "🚏 Abfahrten")
    pruefe(GAST not in bot.awaiting, "Gast bekam eine Frage fuer Abfahrten")


def test_menue_ausblenden():
    neu()
    bot.save = lambda: None
    gesendet = []
    bot.send = lambda cid, text, **k: gesendet.append((cid, str(text), k.get("reply_markup")))
    bot.awaiting.clear()
    tastatur = bot.menu_keyboard(BESITZER)
    pruefe("is_persistent" not in tastatur, "das Menue ist festgeheftet und laesst sich nicht einklappen")
    pruefe(tastatur.get("resize_keyboard") is True, "das Menue ist nicht kompakt")
    for name, cid in {"Besitzer": BESITZER, "Verwalter": VERWALTER, "Mitglied": MITGLIED, "Gast": GAST}.items():
        pruefe("🙈 Ausblenden" in tasten(cid), f"{name} hat keine Taste zum Ausblenden")
    pruefe("🙈 Ausblenden" not in tasten(FREMD), "Fremde sehen die Ausblenden-Taste")
    for wer in (BESITZER, VERWALTER, MITGLIED, GAST):
        pruefe(bot.may_use(wer, cmd="/menue") and bot.may_use(wer, cmd="/menueaus"), f"/menue ist fuer {wer} nicht erlaubt")
    pruefe(not bot.may_use(FREMD, cmd="/menue") and not bot.may_use(FREMD, cmd="/menueaus"), "Fremde duerfen /menue nutzen")
    bot.handle_message({"chat": {"id": MITGLIED}, "text": "🙈 Ausblenden", "from": {"first_name": "T"}})
    pruefe(gesendet and gesendet[-1][2] == {"remove_keyboard": True}, "die Taste Ausblenden entfernt das Menue nicht")
    pruefe("/menue" in gesendet[-1][1], "die Meldung sagt nicht, wie man das Menue zurueckholt")
    bot.handle_message({"chat": {"id": MITGLIED}, "text": "/menue", "from": {"first_name": "T"}})
    pruefe(gesendet[-1][2] and "keyboard" in gesendet[-1][2], "/menue zeigt das Menue nicht wieder")
    bot.handle_message({"chat": {"id": FREMD}, "text": "/menueaus", "from": {"first_name": "T"}})
    pruefe(gesendet[-1][2] != {"remove_keyboard": True}, "Fremde konnten /menueaus nutzen")


def test_clear_befehl():
    neu()
    for wer in (BESITZER, VERWALTER, MITGLIED, GAST):
        pruefe(bot.may_use(wer, cmd="/clear"), f"/clear ist fuer {wer} nicht erlaubt")
    pruefe(not bot.may_use(FREMD, cmd="/clear"), "Fremde duerfen /clear nutzen")
    aufrufe, nachrichten = [], []
    bot.tg = lambda methode, **p: aufrufe.append((methode, p))
    bot.send = lambda cid, text, **k: nachrichten.append(text)
    bot.time.sleep = lambda s: None
    bot.rate_hits.clear()
    bot.data["chats"][str(BESITZER)] = {"favs": [{"sid": "a"}], "trips": [{"key": "k"}], "routines": [], "paused": False}
    bot.cmd_clear(BESITZER, 250)
    ids = [i for _, p in aufrufe for i in p["message_ids"]]
    pruefe(all(m == "deleteMessages" for m, _ in aufrufe), "/clear nutzt etwas anderes als deleteMessages")
    pruefe(all(len(p["message_ids"]) <= 100 for _, p in aufrufe), "ein Batch hat mehr als 100 Nachrichten")
    pruefe(all(p["chat_id"] == BESITZER for _, p in aufrufe), "/clear loescht in einem fremden Chat")
    pruefe(sorted(ids) == list(range(1, 251)), "die Nachrichten 1 bis 250 werden nicht alle erfasst")
    pruefe(bot.data["chats"][str(BESITZER)]["favs"] and bot.data["chats"][str(BESITZER)]["trips"], "/clear loescht Favoriten oder Tracking")
    pruefe(nachrichten and "48" in nachrichten[-1], "/clear weist nicht auf die 48-Stunden-Grenze hin")
    bot.cmd_clear(BESITZER, None)
    bot.cmd_clear(BESITZER, 0)
    bot.cmd_clear(BESITZER, "5")
    pruefe(len(aufrufe) == 3, "/clear loescht bei ungueltiger Nachrichten-ID")
    for _ in range(4):
        bot.cmd_clear(MITGLIED, 10)
    pruefe(sum(1 for _, p in aufrufe if p["chat_id"] == MITGLIED) == 3, "/clear ist nicht auf 3 Aufrufe pro Minute begrenzt")


def test_tracking_zeigt_gleis_und_ueber():
    neu()
    bot.save = lambda: None
    bot.send_first_status = lambda cid, w: None
    token = "t" * 20
    bot.data["sessions"][bot.token_hash(token)] = {"cid": MITGLIED, "exp": 9e12}
    auth = "Bearer " + token
    basis = {"key": "k1", "stop": "s1", "stop_name": "Bocholt Bahnhof", "line": "RE19", "dest": "Duesseldorf",
             "kind": "bahn", "planned": 9e11}
    voll = dict(basis, platform="3", platform_label="Gleis", via="Wesel, Oberhausen", mode_name="Zug")
    bot.web_route("POST", "/trips", {}, voll, auth)
    bot.web_route("POST", "/trips", {}, dict(basis, key="k2", platform_label="Steig<b>"), auth)
    _, antwort = bot.web_route("GET", "/trips", {}, {}, auth)
    nach_key = {t["key"]: t for t in antwort["trips"]}
    pruefe(nach_key["k1"]["platform"] == "3" and nach_key["k1"]["via"] == "Wesel, Oberhausen", "Gleis oder Ueber fehlt in der Tracking-Antwort")
    pruefe(nach_key["k1"]["platform_label"] == "Gleis" and nach_key["k1"]["mode_name"] == "Zug", "Gleis/Steig-Beschriftung oder Art fehlt")
    pruefe(nach_key["k2"]["platform"] == "" and nach_key["k2"]["via"] == "", "Fahrt ohne Gleis und Ueber wird nicht leer geliefert")
    pruefe(nach_key["k2"]["platform_label"] == "Gleis", "unbekannte Beschriftung wurde uebernommen")
    try:
        bot.web_route("POST", "/trips", {}, dict(basis, key="k3", via="x" * 401), auth)
        pruefe(False, "zu langer Ueber-Text wurde akzeptiert")
    except ValueError:
        pass


if __name__ == "__main__":
    for name, fn in sorted((n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)):
        fn()
        print("ok  ", name)
    if FEHLER:
        print(f"\n{len(FEHLER)} Fehler")
        sys.exit(1)
    print("\nAlle Rechtepruefungen bestanden.")
