#!/usr/bin/env python3
"""Rechtetest fuer den Bot. Nur Standardbibliothek. Ausfuehren: python3 bot/test_rechte.py

Muss vor jedem Commit laufen. Der Test leitet Befehle und Knopf-Aktionen automatisch aus dem Code ab
(HELP, Befehlsverteiler, Callback-Aktionen) und prueft die Rollen Besitzer, Admin, Verwalter, Mitglied und Gast.
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
BESITZER, ADMIN, MITGLIED, GAST, FREMD = 1001, 2002, 3003, 4004, 5005
INVITER = 6006
FEHLER = []


def pruefe(bedingung, text):
    if not bedingung:
        FEHLER.append(text)
        print("FEHLER:", text)


def neu():
    """Frischer Zustand: ein Besitzer, ein Admin, ein Mitglied, ein Gast."""
    bot.data.clear()
    bot.data.update({"owner": BESITZER, "chats": {}, "state": {}, "pending": {}, "menus": {}, "deps": {},
                     "guests": [GAST], "shares": {}, "members": [ADMIN, MITGLIED, INVITER], "invites": {}, "names": {},
                     "sessions": {}, "admins": [ADMIN], "inviters": [INVITER], "invited_by": {}, "msglog": {}})


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
    befehle |= set(bot.OWNER_COMMANDS) | set(bot.BETRIEB_COMMANDS) | set(bot.GUEST_COMMANDS)
    aktionen |= {"mrm", "wl"}
    return sorted(befehle), sorted(aktionen)


GAST_BEFEHLE = {"/start", "/hilfe", "/help", "/code", "/tracking", "/menue", "/menueaus"}
GAST_AKTIONEN = {"tw", "tr", "ud", "sr", "sf"}


def rang_alle():
    return {"Besitzer": BESITZER, "Admin": ADMIN, "Verwalter": INVITER, "Mitglied": MITGLIED, "Gast": GAST, "Fremder": FREMD}


def test_rollen_und_rang():
    neu()
    pruefe([bot.rang(c) for c in (BESITZER, ADMIN, INVITER, MITGLIED, GAST, FREMD)] == [5, 4, 3, 2, 1, 0], "Rang der Rollen stimmt nicht")
    pruefe(bot.is_manager(BESITZER) and bot.is_manager(ADMIN) and not bot.is_manager(INVITER) and not bot.is_manager(MITGLIED) and not bot.is_manager(GAST), "is_manager stimmt nicht")
    pruefe(bot.can_invite(INVITER) and not bot.can_invite(MITGLIED) and not bot.can_invite(GAST), "can_invite stimmt nicht")
    pruefe(bot.is_full(BESITZER) and bot.is_full(ADMIN) and bot.is_full(MITGLIED) and bot.is_full(INVITER) and not bot.is_full(GAST) and not bot.is_full(FREMD), "is_full stimmt nicht")
    # Admin-Eintrag ohne Mitgliedschaft zaehlt nicht
    bot.data["admins"].append(FREMD)
    pruefe(not bot.is_admin(FREMD) and bot.rang(FREMD) == 0, "ein Admin-Eintrag ohne Mitgliedschaft gibt Rechte")
    # die alte Server-Rolle gibt es nicht mehr: eine Einstellung dafuer wird ignoriert
    bot.cfg["verwalter_id"] = FREMD
    pruefe(bot.rang(FREMD) == 0 and not bot.may_use(FREMD, cmd="/mitglieder"), "VERWALTER_ID gibt noch Rechte")
    bot.cfg.pop("verwalter_id", None)


def test_rechte_matrix():
    """Befehle und Knoepfe werden aus dem Code abgeleitet, damit eine neue Funktion nicht unbemerkt zu viele Rechte bekommt."""
    neu()
    befehle, aktionen = befehle_und_aktionen()
    pruefe(len(befehle) >= 10 and len(aktionen) >= 10, f"Ableitung aus dem Code liefert zu wenig ({len(befehle)} Befehle, {len(aktionen)} Aktionen)")
    for b in befehle:
        pruefe(bot.may_use(BESITZER, cmd=b), f"Besitzer darf {b} nicht")
        pruefe(bot.may_use(ADMIN, cmd=b) == (b not in bot.BETRIEB_COMMANDS), f"Admin-Recht fuer {b} falsch")
        pruefe(bot.may_use(INVITER, cmd=b) == (b not in bot.BETRIEB_COMMANDS), f"Verwalter-Recht fuer {b} falsch")
        pruefe(bot.may_use(MITGLIED, cmd=b) == (b not in bot.OWNER_COMMANDS | bot.BETRIEB_COMMANDS), f"Mitglied-Recht fuer {b} falsch")
        pruefe(bot.may_use(GAST, cmd=b) == (b in GAST_BEFEHLE), f"Gast-Recht fuer {b} falsch")
        pruefe(not bot.may_use(FREMD, cmd=b), f"Fremde duerfen {b}")
    for a in aktionen:
        pruefe(bot.may_use(BESITZER, action=a), f"Besitzer darf Aktion {a} nicht")
        pruefe(bot.may_use(ADMIN, action=a) == (a not in bot.BETRIEB_ACTIONS), f"Admin-Recht fuer Aktion {a} falsch")
        pruefe(bot.may_use(INVITER, action=a) == (a not in bot.ROLE_ACTIONS | bot.BETRIEB_ACTIONS), f"Verwalter-Recht fuer Aktion {a} falsch")
        pruefe(bot.may_use(MITGLIED, action=a) == (a not in bot.ADMIN_ACTIONS | bot.BETRIEB_ACTIONS), f"Mitglied-Recht fuer Aktion {a} falsch")
        pruefe(bot.may_use(GAST, action=a) == (a in GAST_AKTIONEN), f"Gast-Recht fuer Aktion {a} falsch")
        pruefe(not bot.may_use(FREMD, action=a), f"Fremde duerfen Aktion {a}")
    pruefe(set(bot.GUEST_COMMANDS) == GAST_BEFEHLE and set(bot.GUEST_ACTIONS) == GAST_AKTIONEN, "Gaeste duerfen mehr oder weniger als das Verfolgen")
    pruefe(not bot.BETRIEB_COMMANDS and not bot.BETRIEB_ACTIONS and "/restart" not in befehle and "rst" not in aktionen, "es gibt wieder eine Neustart-Funktion")
    pruefe(bot.OWNER_COMMANDS == {"/mitglieder"}, "unerwarteter Verwaltungsbefehl")


def test_nur_ein_besitzer():
    neu()
    neu_gesendet = []
    bot.send = lambda cid, text, **k: neu_gesendet.append(text)
    bot.tg = lambda *a, **k: None
    bot.save = lambda: None
    karte = {"id": "x", "message": {"chat": {"id": BESITZER}, "message_id": 1}}
    for wer in (BESITZER, ADMIN, MITGLIED):
        for rolle in ("besitzer", "owner", "Besitzer", "chef", ""):
            ok, _ = bot.set_role(wer, BESITZER, rolle)
            pruefe(not ok, f"{wer} konnte die Rolle des Besitzers setzen ({rolle!r})")
        for ziel in (ADMIN, MITGLIED, GAST):
            for rolle in ("besitzer", "owner", "chef", "x", None, 5):
                ok, _ = bot.set_role(wer, ziel, rolle)
                pruefe(not ok, f"Rolle {rolle!r} wurde vergeben")
    pruefe(bot.data["owner"] == BESITZER and not any(bot.ist_besitzer(c) for c in (ADMIN, MITGLIED, GAST, FREMD)), "es gibt mehr als einen Besitzer")
    for wer in (BESITZER, ADMIN, MITGLIED):
        ok, _ = bot.remove_member(wer, BESITZER)
        pruefe(not ok and bot.is_full(BESITZER) and bot.data["owner"] == BESITZER, f"{wer} konnte den Besitzer entfernen")
    # niemand uebernimmt den Besitz ueber /start, solange es einen gibt
    bot.cfg["owner_id"] = 0
    bot.handle_message({"chat": {"id": FREMD}, "text": "/start", "from": {"first_name": "T"}})
    pruefe(bot.data["owner"] == BESITZER and not bot.is_full(FREMD), "/start hat den Besitzer ersetzt")
    # nur main() und /start setzen den Besitzer
    baum = ast.parse(open(QUELLE, encoding="utf-8").read())
    for fn in [n for n in ast.walk(baum) if isinstance(n, ast.FunctionDef)]:
        for knoten in ast.walk(fn):
            if isinstance(knoten, ast.Assign):
                for z in knoten.targets:
                    if ast.unparse(z) == "data['owner']" and fn.name not in ("handle_message", "main"):
                        pruefe(False, f"{fn.name} setzt den Besitzer")


def test_admin_grenzen():
    neu()
    bot.data["members"].append(ADMIN + 1)
    bot.data["admins"].append(ADMIN + 1)                   # zweiter Admin
    zweiter = ADMIN + 1
    bot.save = lambda: None
    # Admin darf einladen, Mitglieder und Gaeste verwalten
    code, _ = bot.make_invite(ADMIN)
    pruefe(code in bot.data["invites"], "Admin kann nicht einladen")
    ok, _ = bot.set_role(ADMIN, GAST, "mitglied")
    pruefe(ok and GAST in bot.data["members"] and GAST not in bot.data["guests"], "Admin kann Gast nicht zum Mitglied machen")
    ok, _ = bot.set_role(ADMIN, GAST, "gast")
    pruefe(ok and GAST in bot.data["guests"] and GAST not in bot.data["members"], "Admin kann Mitglied nicht zum Gast machen")
    # aber nicht ernennen, nicht gleichrangige oder hoehere antasten
    ok, _ = bot.set_role(ADMIN, MITGLIED, "admin")
    pruefe(not ok and MITGLIED not in bot.data["admins"], "Admin konnte jemanden zum Admin machen")
    for ziel in (BESITZER, zweiter):
        ok1, _ = bot.remove_member(ADMIN, ziel)
        ok2, _ = bot.set_role(ADMIN, ziel, "mitglied")
        ok3, _ = bot.set_role(ADMIN, ziel, "gast")
        pruefe(not (ok1 or ok2 or ok3) and bot.is_full(ziel), f"Admin konnte {ziel} entfernen oder aendern")
    ok, _ = bot.remove_member(ADMIN, ADMIN)
    pruefe(not ok, "Admin konnte sich selbst entfernen")
    # Einladungen: eigene und die von Personen unter sich ja, die von Besitzer und Mitadminn nein
    eigene, _ = bot.make_invite(ADMIN)
    vom_besitzer, _ = bot.make_invite(BESITZER)
    vom_zweiten, _ = bot.make_invite(zweiter)
    ok, _ = bot.revoke_invite(ADMIN, vom_besitzer)
    pruefe(not ok and vom_besitzer in bot.data["invites"], "Admin konnte eine Einladung des Besitzers widerrufen")
    ok, _ = bot.revoke_invite(ADMIN, vom_zweiten)
    pruefe(not ok and vom_zweiten in bot.data["invites"], "Admin konnte eine Einladung eines anderen Admins widerrufen")
    ok, _ = bot.revoke_invite(ADMIN, eigene)
    pruefe(ok and eigene not in bot.data["invites"], "Admin konnte die eigene Einladung nicht widerrufen")
    # Mitglied entfernen: Admin ja
    ok, _ = bot.remove_member(ADMIN, MITGLIED)
    pruefe(ok and MITGLIED not in bot.data["members"], "Admin konnte kein Mitglied entfernen")
    # Besitzer darf alles darunter
    ok, _ = bot.set_role(BESITZER, zweiter, "mitglied")
    pruefe(ok and zweiter not in bot.data["admins"] and zweiter in bot.data["members"], "Besitzer konnte keinen Admin absetzen")
    ok, _ = bot.set_role(BESITZER, zweiter, "admin")
    pruefe(ok and bot.is_admin(zweiter), "Besitzer konnte keinen Admin ernennen")
    neuer, _ = bot.make_invite(zweiter)
    ok, _ = bot.revoke_invite(BESITZER, neuer)
    pruefe(ok and neuer not in bot.data["invites"], "Besitzer konnte eine Einladung eines Admins nicht widerrufen")
    ok, _ = bot.remove_member(BESITZER, zweiter)
    pruefe(ok and bot.rang(zweiter) == 0, "Besitzer konnte keinen Admin entfernen")
    pruefe(not any(i["by"] == zweiter for i in bot.data["invites"].values()), "Einladungen eines entfernten Admins blieben gueltig")
    # Mitglieder und Gaeste koennen nichts davon
    bot.data["members"].append(MITGLIED + 5)
    for wer in (MITGLIED + 5, FREMD):
        pruefe(not bot.can_invite(wer), f"{wer} darf einladen")
        pruefe(not bot.set_role(wer, GAST, "mitglied")[0] and not bot.remove_member(wer, GAST)[0], f"{wer} konnte Rollen aendern")
    # Admin-Rolle wird zurueckgenommen: Codes weg
    bot.data["members"].append(zweiter)
    bot.data["admins"].append(zweiter)
    bot.make_invite(zweiter)
    bot.set_role(BESITZER, zweiter, "mitglied")
    pruefe(not any(i["by"] == zweiter for i in bot.data["invites"].values()), "Codes bleiben nach dem Absetzen gueltig")
    # Limit offener Codes
    bot.data["invites"].clear()
    for _ in range(bot.MAX_OPEN_INVITES):
        bot.make_invite(ADMIN)
    try:
        bot.make_invite(ADMIN)
        pruefe(False, "Admin hat mehr als die erlaubten offenen Codes")
    except ValueError:
        pass


def test_gast_nur_verfolgen():
    neu()
    nachrichten = []
    bot.send = lambda cid, text, **k: nachrichten.append((cid, str(text)))
    bot.tg = lambda *a, **k: None
    bot.save = lambda: None
    bot.fetch_departures = lambda *a, **k: []
    for text in ("/suche Berlin", "/favoriten", "/abfahrten", "/jetzt", "/pause", "/weiter", "/status", "/clear", "/autoclear",
                 "/einstellungen", "/wiederkehrend", "/mitglieder"):
        nachrichten.clear()
        bot.handle_message({"chat": {"id": GAST}, "text": text, "from": {"first_name": "G"}})
        pruefe(len(nachrichten) == 1 and "privater Bot" in nachrichten[0][1], f"Gast konnte {text} nutzen")
    pruefe(not bot.data["chats"].get(str(GAST), {}).get("favs"), "Gast hat Favoriten bekommen")
    for aktion in ("ac", "st", "sh", "add", "w", "fav", "mrm", "mn", "rep"):
        pruefe(not bot.may_use(GAST, action=aktion), f"Gast darf Aktion {aktion}")
    pruefe(bot.help_text(GAST) == bot.GUEST_HELP and "/suche" not in bot.GUEST_HELP, "Gast bekommt die falsche Hilfe")
    pruefe([t for t in tasten(GAST)] == ["🎟 Code eingeben", "📌 Verfolgte Fahrten", "❓ Hilfe", "🙈 Ausblenden"], f"Tasten des Gastes: {tasten(GAST)}")
    # Verbindung per /code ansehen und verfolgen: das darf er, mehr nicht
    bot.data["shares"]["ABC234"] = {"w": {"wid": "w1", "key": "k", "stop": "s", "stop_name": "Halt", "line": "S1", "dest": "Ziel",
                                           "kind": "bahn", "planned": 9e12}, "by": MITGLIED, "eta": 9e12}
    nachrichten.clear()
    bot.handle_message({"chat": {"id": FREMD}, "text": "/code ABC234", "from": {"first_name": "N"}})
    pruefe(FREMD in bot.data["guests"] and FREMD not in bot.data["members"] and bot.rang(FREMD) == 1, "Code einer Verbindung macht nicht zum Gast")
    bot.handle_callback({"id": "x", "data": "sf:ABC234", "message": {"chat": {"id": FREMD}, "message_id": 1}})
    pruefe(any(t["key"] == "k" for t in bot.data["chats"][str(FREMD)]["trips"]), "Gast kann die Verbindung nicht verfolgen")
    # kein Zugang zur Website
    bot.data["sessions"][bot.token_hash("g")] = {"cid": GAST, "exp": 9e12}
    pruefe(bot.web_route("GET", "/settings", {}, {}, "Bearer g")[0] == 401, "Gast hat Zugang zur Website-Anmeldung")
    # ein Gast kann sich nicht selbst befoerdern
    bot.handle_callback({"id": "x", "data": f"mr:{GAST}:a", "message": {"chat": {"id": GAST}, "message_id": 1}})
    pruefe(bot.rang(GAST) == 1, "Gast hat sich befoerdert")
    # Einladungscode einloesen macht ihn zum Mitglied
    code, _ = bot.make_invite(ADMIN)
    bot.cmd_code(GAST, code, {"first_name": "G"})
    pruefe(bot.rang(GAST) == 2 and GAST not in bot.data["guests"], "Gast wird per Einladung kein Mitglied")


def test_hilfe_je_rolle():
    neu()
    h = {n: bot.help_text(c) for n, c in rang_alle().items()}
    pruefe("/mitglieder" in h["Besitzer"] and "/restart" not in h["Besitzer"], "Besitzer sieht nicht alle Befehle")
    pruefe("/mitglieder" in h["Admin"] and "/mitglieder" in h["Verwalter"], "Admin oder Verwalter sieht /mitglieder nicht")
    pruefe("/mitglieder" not in h["Mitglied"] and "/suche" in h["Mitglied"], "Mitglied sieht Verwaltungsbefehle oder nicht die Bedienung")
    pruefe(h["Gast"] == bot.GUEST_HELP, "Gast sieht nicht die Gast-Hilfe")


def test_mitglieder_anzeige():
    neu()
    gesendet = []
    bot.send = lambda cid, text, **k: gesendet.append((cid, text, k))
    bot.data["names"] = {str(BESITZER): "Helmut", str(ADMIN): "Vera", str(MITGLIED): "Mia", str(GAST): "Gero"}
    bot.data["invited_by"] = {str(MITGLIED): ADMIN}
    bot.cmd_mitglieder(BESITZER)
    text = gesendet[-1][1]
    pruefe("Helmut (Besitzer)" in text and "Vera (Admin)" in text and "Mia · eingeladen von Vera" in text and "Gero (Gast)" in text, f"/mitglieder zeigt Rollen oder Einlader nicht: {text}")
    daten = [k["callback_data"] for z in gesendet[-1][2]["reply_markup"]["inline_keyboard"] for k in z]
    pruefe(f"mrm:{ADMIN}" in daten and f"mrm:{MITGLIED}" in daten and f"mr:{GAST}" in daten, "Besitzer sieht nicht alle Knoepfe")
    pruefe(f"mrm:{BESITZER}" not in daten, "es gibt einen Knopf zum Entfernen des Besitzers")
    gesendet.clear()
    bot.cmd_mitglieder(ADMIN)
    daten = [k["callback_data"] for z in gesendet[-1][2]["reply_markup"]["inline_keyboard"] for k in z]
    pruefe(f"mrm:{MITGLIED}" in daten and f"mrm:{ADMIN}" not in daten and f"mrm:{BESITZER}" not in daten, "Admin sieht Knoepfe fuer Besitzer oder sich selbst")
    n = len(gesendet)
    bot.cmd_mitglieder(MITGLIED)
    bot.cmd_mitglieder(GAST)
    pruefe(len(gesendet) == n, "Mitglied oder Gast konnte /mitglieder aufrufen")


def test_rollen_per_knopf():
    neu()
    aufrufe = []
    bot.tg = lambda methode, **p: aufrufe.append((methode, p))
    bot.send = lambda *a, **k: None
    bot.save = lambda: None

    def knopf(wer, aktion, rest):
        aufrufe.clear()
        bot.handle_member_admin_callback({"id": "x"}, wer, 1, aktion, rest)
    knopf(BESITZER, "mr", str(MITGLIED))
    pruefe(any("Rolle" in p.get("text", "") for m, p in aufrufe if m == "editMessageText"), "Rollenauswahl wird nicht gezeigt")
    kb = [k["callback_data"] for m, p in aufrufe if m == "editMessageText" for z in p["reply_markup"]["inline_keyboard"] for k in z]
    pruefe({f"mr:{MITGLIED}:a", f"mr:{MITGLIED}:m", f"mr:{MITGLIED}:g"} <= set(kb), f"Besitzer sieht nicht alle Rollen: {kb}")
    knopf(ADMIN, "mr", str(MITGLIED))
    kb = [k["callback_data"] for m, p in aufrufe if m == "editMessageText" for z in p["reply_markup"]["inline_keyboard"] for k in z]
    pruefe(f"mr:{MITGLIED}:a" not in kb and f"mr:{MITGLIED}:g" in kb, "Admin sieht die Rolle Admin zur Auswahl")
    knopf(BESITZER, "mr", f"{MITGLIED}:a")
    pruefe(bot.is_admin(MITGLIED), "Besitzer konnte per Knopf keinen Admin ernennen")
    knopf(ADMIN, "mr", f"{GAST}:m")
    pruefe(GAST in bot.data["members"], "Admin konnte Gast nicht zum Mitglied machen")
    knopf(ADMIN, "mr", f"{MITGLIED}:g")
    pruefe(bot.is_admin(MITGLIED), "Admin konnte einen anderen Admin zum Gast machen")
    knopf(ADMIN, "mrm", str(MITGLIED))
    pruefe(MITGLIED in bot.data["members"], "Admin konnte einen anderen Admin entfernen")
    for wer in (MITGLIED + 100, FREMD, GAST):
        for aktion, rest in (("mr", f"{GAST}:m"), ("mrm", str(GAST)), ("mn", "0"), ("mo", "0")):
            aufrufe.clear()
            bot.handle_member_admin_callback({"id": "x"}, wer, 1, aktion, rest)
            pruefe(not aufrufe, f"{wer} konnte {aktion} ausloesen")


def test_rolle_nicht_ueber_daten():
    """Die Rolle steht nur in data.json und wird nur ueber set_role, den Einladungscode und die Verbindungscodes geaendert."""
    baum = ast.parse(open(QUELLE, encoding="utf-8").read())
    erlaubt = {"set_role", "remove_member", "cmd_code", "migrate", "load", "main"}
    for fn in [n for n in ast.walk(baum) if isinstance(n, ast.FunctionDef)]:
        for knoten in ast.walk(fn):
            if isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Attribute) and knoten.func.attr in ("append", "remove", "extend", "insert"):
                if ast.unparse(knoten.func.value) in ("data['admins']", "data['inviters']", "data['members']", "data['guests']") and fn.name not in erlaubt:
                    pruefe(False, f"{fn.name} aendert eine Rollenliste ({ast.unparse(knoten.func.value)})")


def test_keine_umgehung_der_pruefung():
    """Rechte nie nur mit is_owner pruefen, sondern ueber may_use, is_manager oder rang."""
    baum = ast.parse(open(QUELLE, encoding="utf-8").read())
    erlaubt = {"ist_besitzer", "may_use"}
    for fn in [n for n in ast.walk(baum) if isinstance(n, ast.FunctionDef)]:
        for knoten in ast.walk(fn):
            if isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Name) and knoten.func.id == "is_owner" and fn.name not in erlaubt:
                pruefe(False, f"{fn.name} ruft is_owner auf. Bitte ist_besitzer, is_manager oder may_use nutzen")
            if isinstance(knoten, ast.Compare) and "data[\"owner\"]" in ast.unparse(knoten.left) \
                    and not all(isinstance(o, (ast.Is, ast.IsNot)) for o in knoten.ops) and fn.name != "is_owner":
                pruefe(False, f"{fn.name} vergleicht data[\"owner\"] direkt. Bitte ist_besitzer nutzen")


def test_keine_geheimnisse_in_texten():
    neu()
    bot.cfg["bot_token"] = "123456:GEHEIMER-TOKEN"
    gesendet = []
    bot.send = lambda cid, text, **k: gesendet.append(str(text))
    bot.tg = lambda *a, **k: None
    bot.get_chat(BESITZER)
    for cmd in ("/status", "/hilfe", "/mitglieder", "/einstellungen"):
        bot.handle_message({"chat": {"id": BESITZER}, "text": cmd, "from": {"first_name": "Test"}})
    alles = "\n".join(gesendet)
    pruefe("GEHEIMER-TOKEN" not in alles, "der Token erscheint in einer Nachricht")
    pruefe(HIER not in alles, "ein Dateipfad erscheint in einer Nachricht")
    bot.cfg["bot_token"] = ""


def test_einladung_vermerk():
    neu()
    nachrichten = []
    bot.send = lambda cid, text, **k: nachrichten.append((cid, str(text)))
    bot.tg = lambda *a, **k: None
    bot.save = lambda: None
    bot.cmd_code(MITGLIED, "")
    pruefe(not bot.data["invites"], "ein Mitglied hat einen Einladungscode erzeugt")
    bot.cmd_code(ADMIN, "")
    code = next(iter(bot.data["invites"]))
    pruefe(bot.data["invites"][code]["by"] == ADMIN, "Admin erzeugt keinen Code")
    nachrichten.clear()
    bot.cmd_code(FREMD, code, {"first_name": "Neu"})
    pruefe(bot.rang(FREMD) == 2 and not bot.is_manager(FREMD), "eingeladene Person ist kein normales Mitglied")
    pruefe(bot.data["invited_by"].get(str(FREMD)) == ADMIN and bot.eingeladen_von(FREMD) == bot.who(ADMIN), "es wird nicht festgehalten, wer eingeladen hat")
    pruefe(any(c == ADMIN and "eingeloest" in t.replace("ö", "oe") for c, t in nachrichten), "der Einlader erfaehrt es nicht")
    pruefe(any(c == BESITZER and "eingeladen" in t for c, t in nachrichten), "der Besitzer erfaehrt nicht, wer ueber einen Admin kam")
    bot.cmd_code(MITGLIED + 7, code, {"first_name": "Zweiter"})
    pruefe(bot.rang(MITGLIED + 7) == 0, "Einladung ist mehrfach einloesbar")
    bot.remove_member(BESITZER, FREMD)
    pruefe(str(FREMD) not in bot.data["invited_by"], "der Vermerk bleibt nach dem Entfernen stehen")


def tasten(cid):
    return [k["text"] for z in bot.menu_keyboard(cid)["keyboard"] for k in z]


def test_menue_tasten_je_rolle():
    neu()
    befehle, _ = befehle_und_aktionen()
    for text, cmd in bot.MENU_BUTTONS.items():
        pruefe(cmd in befehle, f"Taste {text} gehoert zu einem unbekannten Befehl {cmd}")
    rollen = {"Besitzer": BESITZER, "Admin": ADMIN, "Mitglied": MITGLIED, "Gast": GAST, "Fremder": FREMD}
    for name, cid in rollen.items():
        for t in tasten(cid):
            pruefe(t in bot.MENU_BUTTONS, f"{name}: unbekannte Taste {t}")
            cmd = bot.MENU_BUTTONS.get(t)
            erlaubt = t == "🎟 Code eingeben" or (t == "🔑 Einladen" and bot.can_invite(cid)) or                 (t != "🔑 Einladen" and bot.may_use(cid, cmd=cmd))
            pruefe(erlaubt, f"{name} sieht die Taste {t}, darf den Befehl {cmd} aber nicht")
    pruefe("👥 Mitglieder" in tasten(BESITZER) and "👥 Mitglieder" in tasten(ADMIN), "Besitzer oder Admin sieht /mitglieder nicht")
    pruefe("🔑 Einladen" in tasten(BESITZER) and "🔑 Einladen" in tasten(ADMIN), "Besitzer oder Admin sieht Einladen nicht")
    pruefe("👥 Mitglieder" not in tasten(MITGLIED) and "🔑 Einladen" not in tasten(MITGLIED), "normales Mitglied sieht Verwaltungstasten")
    pruefe("👥 Mitglieder" in tasten(INVITER) and "🔑 Einladen" in tasten(INVITER), "Verwalter sieht Mitglieder oder Einladen nicht")
    pruefe(not any("Neustart" in t for r in rollen.values() for t in tasten(r)), "es gibt wieder eine Neustart-Taste")
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
    druecke(MITGLIED, "Berlin Hauptbahnhof")
    pruefe(("cmd_suche", MITGLIED, "Berlin Hauptbahnhof") in aufrufe, "Antwort nach der Taste Suche startet die Suche nicht")
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
    druecke(ADMIN, "👥 Mitglieder")
    pruefe(("cmd_mitglieder", BESITZER) in aufrufe and ("cmd_mitglieder", ADMIN) in aufrufe, "Besitzer oder Admin erreicht Mitglieder nicht")


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
    for wer in (BESITZER, ADMIN, MITGLIED):
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
    sende(MITGLIED, "Berlin Hauptbahnhof")
    pruefe(suchen == ["Berlin Hauptbahnhof"], f"Antwort wurde nicht als Haltestelle gesucht: {suchen}")
    pruefe(MITGLIED not in bot.awaiting, "offene Frage blieb bestehen")
    sende(MITGLIED, "noch ein Text")
    pruefe(suchen == ["Berlin Hauptbahnhof"], "freier Text loest ohne Taste eine Suche aus")
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
    for name, cid in {"Besitzer": BESITZER, "Admin": ADMIN, "Mitglied": MITGLIED}.items():
        pruefe("🙈 Ausblenden" in tasten(cid), f"{name} hat keine Taste zum Ausblenden")
    pruefe("🙈 Ausblenden" not in tasten(FREMD), "Fremde sehen die Ausblenden-Taste")
    for wer in (BESITZER, ADMIN, MITGLIED):
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
    for wer in (BESITZER, ADMIN, MITGLIED):
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
    basis = {"key": "k1", "stop": "s1", "stop_name": "Berlin Hauptbahnhof", "line": "RE19", "dest": "Duesseldorf",
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


def test_inviter_grenzen():
    """Verwalter laden ein und entziehen Mitgliedern und Gaesten den Zugang, aendern aber keine Rollen und fassen nichts Hoeheres an."""
    neu()
    bot.save = lambda: None
    code, _ = bot.make_invite(INVITER)
    pruefe(code in bot.data["invites"], "Verwalter kann nicht einladen")
    ok, _ = bot.revoke_invite(INVITER, code)
    pruefe(ok and code not in bot.data["invites"], "Verwalter konnte die eigene Einladung nicht widerrufen")
    admin_code, _ = bot.make_invite(ADMIN)
    pruefe(not bot.revoke_invite(INVITER, admin_code)[0], "Verwalter konnte eine Einladung eines Admins widerrufen")
    for ziel in (BESITZER, ADMIN, INVITER):
        pruefe(not bot.remove_member(INVITER, ziel)[0] and bot.rang(ziel) > 0, f"Verwalter konnte {ziel} entfernen")
    for ziel in (MITGLIED, GAST):
        for rolle in ("admin", "verwalter", "mitglied", "gast"):
            pruefe(not bot.set_role(INVITER, ziel, rolle)[0], f"Verwalter konnte die Rolle {rolle} vergeben")
        pruefe(bot.allowed_roles(INVITER, ziel) == [], "Verwalter bekommt eine Rollenauswahl")
    pruefe(bot.set_role(BESITZER, MITGLIED, "verwalter")[0] and bot.is_inviter(MITGLIED), "Besitzer konnte keinen Verwalter ernennen")
    pruefe(bot.set_role(ADMIN, MITGLIED, "mitglied")[0] and not bot.is_inviter(MITGLIED), "Admin konnte keinen Verwalter absetzen")
    pruefe(bot.set_role(ADMIN, MITGLIED, "verwalter")[0] and not bot.set_role(ADMIN, MITGLIED, "admin")[0], "Admin ernennt Verwalter nicht oder Admins")
    ok, _ = bot.remove_member(INVITER, GAST)
    pruefe(ok and bot.rang(GAST) == 0, "Verwalter konnte einem Gast den Zugang nicht entziehen")
    # Der Knopf fuer Rollen geht nicht durch, auch nicht nachgebaut
    aufrufe = []
    bot.tg = lambda methode, **p: aufrufe.append(methode)
    bot.handle_member_admin_callback({"id": "x"}, INVITER, 1, "mr", f"{MITGLIED}:a")
    pruefe(not bot.is_admin(MITGLIED), "Verwalter konnte per Knopf einen Admin ernennen")
    pruefe(not any("mr:" in str(b) for z in bot.members_keyboard(INVITER)["inline_keyboard"] for b in z), "Verwalter sieht Rollen-Knoepfe")


def test_migration_verwalter():
    neu()
    bot.data["verwalter"] = [MITGLIED]
    bot.migrate()
    pruefe("verwalter" not in bot.data and MITGLIED in bot.data["admins"], "Verwalter wurden nicht zu Admins")


if __name__ == "__main__":
    for name, fn in sorted((n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)):
        fn()
        print("ok  ", name)
    if FEHLER:
        print(f"\n{len(FEHLER)} Fehler")
        sys.exit(1)
    print("\nAlle Rechtepruefungen bestanden.")
