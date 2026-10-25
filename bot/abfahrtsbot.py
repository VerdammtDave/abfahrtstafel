#!/usr/bin/env python3
"""Telegram-Bot: meldet Verspaetungen und Ausfaelle an den Favoriten-Haltestellen.

Daten: Transitous (MOTIS), gleiche Quelle wie die Abfahrtstafel. Nur Standardbibliothek.
Start:   python3 abfahrtsbot.py
Test:    python3 abfahrtsbot.py --selftest "Bocholt"
"""
import copy
import hashlib
import html
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Berlin")
except Exception:                      # z. B. Windows ohne tzdata
    TZ = None

HERE = os.path.dirname(os.path.abspath(__file__))
MOTIS = "https://api.transitous.org/api/v1"
UA = "abfahrtsbot/1.0 (privat)"
MODES = ["all", "bus", "bahn", "off"]
MODE_ICON = {"all": "\U0001f68c\U0001f686", "bus": "\U0001f68c", "bahn": "\U0001f686", "off": "\U0001f515"}

cfg = {}
data = {"owner": None, "chats": {}, "state": {}, "pending": {}, "menus": {}, "deps": {}, "guests": [], "shares": {}, "members": [], "invites": {}, "names": {}, "sessions": {}}
lock = threading.Lock()
last_check = None


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


# ---------- HTTP ----------

def force_ipv4():
    """Nur IPv4 nutzen. Ist IPv6 im Container kaputt, wartet Python sonst bei jeder Anfrage auf Timeouts."""
    orig = socket.getaddrinfo

    def v4(host, port, family=0, *a, **k):
        return orig(host, port, socket.AF_INET, *a, **k)
    socket.getaddrinfo = v4


def http_json(url, body=None, timeout=30):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"User-Agent": UA, "Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    finally:
        took = time.time() - t0
        path = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]      # ohne Token und Suchtext loggen
        if took > 2 and path != "getUpdates":
            log(f"langsame Anfrage: {path} {took:.1f}s")


def tg(method, **params):
    url = f"https://api.telegram.org/bot{cfg['bot_token']}/{method}"
    return http_json(url, params, timeout=70).get("result")


def send(chat_id, text, **extra):
    try:
        return tg("sendMessage", chat_id=chat_id, text=text, **extra)
    except Exception as e:
        log("sendMessage fehlgeschlagen:", e)


# ---------- Fahrplandaten ----------

def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def hhmm(dt):
    return (dt.astimezone(TZ) if TZ else dt.astimezone()).strftime("%H:%M")


def vehicle(mode):
    return "bus" if mode == "BUS" else "bahn"


def search_stops(text):
    q = urllib.parse.urlencode({"text": text, "type": "STOP"})
    seen, out = set(), []
    for s in http_json(f"{MOTIS}/geocode?{q}"):
        if s.get("type") == "STOP" and s["name"] not in seen:
            seen.add(s["name"])
            out.append({"id": s["id"], "name": s["name"]})
    return out[:8]


def fetch_departures(stop_id, n=40):
    q = urllib.parse.urlencode({"stopId": stop_id, "n": n})
    out = []
    for st in http_json(f"{MOTIS}/stoptimes?{q}").get("stopTimes", []):
        p = st["place"]
        line = st.get("displayName") or st.get("routeShortName") or "?"
        planned = parse_ts(p.get("scheduledDeparture") or p["departure"])
        actual = parse_ts(p.get("departure") or p["scheduledDeparture"])
        out.append({
            "key": f"{st['tripId']}|{p.get('stopId')}",
            "kind": vehicle(st["mode"]),
            "line": line,
            "lk": re.sub(r"\s*\(\d+\)$", "", line),     # "RE19 (77035)" -> "RE19"
            "dest": st.get("headsign", ""),
            "stop": p["name"],
            "track": p.get("track") or "",
            "planned": planned,
            "actual": actual,
            "delay": round((actual - planned).total_seconds() / 60),
            "cancelled": bool(st.get("cancelled") or st.get("tripCancelled")),
        })
    return out


# ---------- Aenderungserkennung ----------

def decide(prev, d, thr):
    """Gibt (Ereignis oder None, neuer Zustand) zurueck.

    Ereignisse: late (erstmals ueber Schwelle), change (Verspaetung hat sich um >= 2 min
    gegenueber der letzten Meldung veraendert), ok (wieder unter Schwelle), cancel (faellt aus).
    """
    st = dict(prev or {"nd": 0, "alerted": False, "cancelled": False})
    st["t"] = d["planned"].timestamp()
    event = None
    if d["cancelled"]:
        if not st["cancelled"]:
            event = "cancel"
        st["cancelled"] = True
        return event, st
    st["cancelled"] = False
    if not st["alerted"]:
        if d["delay"] >= thr:
            event, st["alerted"], st["nd"] = "late", True, d["delay"]
    elif d["delay"] < thr:
        event, st["alerted"], st["nd"] = "ok", False, 0
    elif abs(d["delay"] - st["nd"]) >= 2:
        event, st["nd"] = "change", d["delay"]
    return event, st


def wanted(f, d):
    """Passt die Abfahrt zu Bus/Bahn-Auswahl und Linienauswahl des Favoriten?"""
    if f["mode"] != "all" and f["mode"] != d["kind"]:
        return False
    return not f.get("lines") or d["lk"] in f["lines"]


REMIND_AT = (15, 5)        # Erinnerung so viele Minuten vor Abfahrt
REMIND_TOLERANCE = 2 / 60  # 2 Sekunden Toleranz: lieber knapp zu frueh als zu spaet
REMIND_LEAD = 1.0          # so viele Sekunden vor dem Zeitpunkt wird abgefragt (Abfrage und Versand brauchen Zeit)


def decide_trip(prev, d, now):
    """Einzeln beobachtete Fahrt. Gibt (Ereignis, alter Wert, neuer Zustand) zurueck.

    Ereignisse: cancel (faellt aus), remind (je einmal 15 und 5 min vor Abfahrt),
    change (Verspaetung hat sich um >= 2 min gegenueber der letzten Meldung geaendert).
    """
    # Standardwerte zuerst, damit auch Zustaende aus aelteren Bot-Versionen funktionieren
    st = {"nd": d["delay"], "cancelled": False, "rem": [], **(prev or {})}
    if prev and "rem" not in prev:
        st["rem"] = list(REMIND_AT) if prev.get("reminded") else []
    st["t"] = d["planned"].timestamp()
    st["eta"] = d["actual"].timestamp()            # voraussichtliche Abfahrt, daraus werden die Weckzeiten berechnet
    old = st["nd"]
    if d["cancelled"]:
        event = None if st["cancelled"] else "cancel"
        st["cancelled"] = True
        return event, old, st
    st["cancelled"] = False
    mins = (d["actual"] - now).total_seconds() / 60
    due = [t for t in REMIND_AT if mins <= t + REMIND_TOLERANCE and t not in st["rem"]]
    if due:
        st["rem"] = sorted(set(st["rem"]) | set(due))      # alle bereits erreichten Zeitpunkte als erledigt merken
        st["nd"] = d["delay"]
        return "remind", old, st
    if abs(d["delay"] - st["nd"]) >= 2:
        st["nd"] = d["delay"]
        return "change", old, st
    return None, old, st


def fmt_delay(delay):
    return f"+{delay} min" if delay >= 1 else f"{delay} min" if delay <= -1 else "p\u00fcnktlich"


def fmt_trip(event, d, old=None):
    icon = "\U0001f68c" if d["kind"] == "bus" else "\U0001f686"
    where = f"\U0001f4cd {d['stop']}" + (f" \u00b7 Gleis/Steig {d['track']}" if d["track"] else "")
    if d["cancelled"]:
        stand = "\u274c f\u00e4llt aus"
    elif d["delay"] >= 1 or d["delay"] <= -1:
        stand = f"{severity(d['delay'])} {fmt_delay(d['delay'])} \u00b7 jetzt {hhmm(d['actual'])}"
    else:
        stand = "\u2705 p\u00fcnktlich"
    mins = max(0, round((d["actual"] - datetime.now(timezone.utc)).total_seconds() / 60))
    title = {
        "status": "\U0001f514 Wird beobachtet",
        "change": "\U0001f504 \u00c4nderung" + (f" (vorher {fmt_delay(old)})" if old is not None else ""),
        "remind": f"\u23f0 Abfahrt in {mins} min",
        "now": "\u2139 Aktueller Stand",
        "routine": "\U0001f501 Wiederkehrende Fahrt heute",
        "shared": "\U0001f517 Geteilte Verbindung",
        "cancel": "\u274c Fahrt f\u00e4llt aus",
    }[event]
    return (f"{title}\n{icon} {d['line']} \u2192 {d['dest']}\n{where}\n"
            f"\U0001f550 geplant {hhmm(d['planned'])}\nStand: {stand}")


def severity(delay):
    return "\U0001f534" if delay >= 15 else "\U0001f7e0" if delay >= 5 else "\U0001f7e1"


def fmt(event, d):
    icon = "\U0001f68c" if d["kind"] == "bus" else "\U0001f686"
    head = f"{icon} {d['line']} \u2192 {d['dest']}"
    where = f"\U0001f4cd {d['stop']}" + (f" \u00b7 Gleis/Steig {d['track']}" if d["track"] else "")
    plan = f"geplant {hhmm(d['planned'])}"
    if event == "cancel":
        return f"\u274c {head} f\u00e4llt aus\n{where}\n\U0001f550 {plan}"
    if event == "ok":
        return f"\u2705 {head} ist wieder p\u00fcnktlich\n{where}\n\U0001f550 {plan}"
    label = "Versp\u00e4tung \u00e4ndert sich" if event == "change" else "Versp\u00e4tung"
    return (f"{severity(d['delay'])} {head}\n{label}: +{d['delay']} min\n{where}\n"
            f"\U0001f550 {plan} \u00b7 jetzt {hhmm(d['actual'])}")


# ---------- Ueberwachung ----------

def prune_trips(cid, chat, now_ts):
    """Abgefahrene Fahrten aus der Tracking-Liste entfernen (voraussichtliche Abfahrt laenger als 1 min her).
    Abgefahrene Fahrten stehen nicht mehr in der Abfahrtsliste, deshalb reicht der Abgleich mit der Liste nicht."""
    state = data["state"].get(str(cid), {})
    keep = []
    for w in chat.get("trips", []):
        skey = "w_" + str(w.get("wid"))
        eta = state.get(skey, {}).get("eta") or w.get("planned", 0)
        if eta < now_ts - 60:
            state.pop(skey, None)
        else:
            keep.append(w)
    removed = len(chat.get("trips", [])) - len(keep)
    if removed:
        chat["trips"][:] = keep
        log(f"{removed} abgefahrene Fahrt(en) aus der Tracking-Liste entfernt")
    return removed


def check_trip(cid, chat, w, fetched, now, outbox):
    deps = fetched.get(w["stop"])
    if deps is None:
        return
    state = data["state"].setdefault(cid, {})
    skey = "w_" + w["wid"]
    d = next((x for x in deps if x["key"] == w["key"]), None)
    if d is None:
        log(f"beobachtete Fahrt nicht in der Abfahrtsliste: {w['line']} {w['stop_name']}")
        if w["planned"] < (now - timedelta(minutes=15)).timestamp():
            chat["trips"].remove(w)                      # Fahrt ist vorbei
            state.pop(skey, None)
        return
    event, old, state[skey] = decide_trip(state.get(skey), d, now)
    if event:
        outbox.append((cid, fmt_trip(event, d, old)))
    if d["actual"] < now - timedelta(minutes=2) or event == "cancel":
        chat["trips"].remove(w)                          # abgefahren oder ausgefallen: fertig
        state.pop(skey, None)


def next_remind_due():
    """Naechster Erinnerungszeitpunkt aller beobachteten Fahrten: (Zeitstempel, Haltestellen) oder None."""
    now = time.time()
    best, stops = None, set()
    with lock:
        for cid, chat in data["chats"].items():
            if chat.get("paused"):
                continue
            for w in chat.get("trips", []):
                st = data["state"].get(cid, {}).get("w_" + str(w.get("wid")))
                if not st or "eta" not in st or st.get("cancelled") or not w.get("stop"):
                    continue
                for t in REMIND_AT:
                    due = st["eta"] - t * 60 - REMIND_LEAD
                    if t in st.get("rem", []) or due <= now:
                        continue
                    if best is None or due < best - 3:
                        best, stops = due, {w["stop"]}
                    elif due < best + 3:
                        best, stops = min(best, due), stops | {w["stop"]}
    return (best, stops) if best else None


def check_all(only=None):
    """Beobachtete Fahrten pruefen. only: nur diese Haltestellen abfragen (Erinnerungszeitpunkt)."""
    global last_check
    with lock:
        chats = copy.deepcopy(data["chats"])
    stops = {w["stop"] for c in chats.values() if not c.get("paused") for w in c.get("trips", []) if w.get("stop")}
    if only is not None:
        stops &= only
    fetched = {}
    for sid in stops:
        try:
            fetched[sid] = fetch_departures(sid)
        except Exception as e:
            log("Abruf fehlgeschlagen:", sid, e)
        time.sleep(0.3)

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(minutes=cfg["window_min"])
    outbox = []
    with lock:
        for cid, chat in data["chats"].items():
            prune_trips(cid, chat, now.timestamp())      # abgefahrene Fahrten aus der Tracking-Liste entfernen
            if chat.get("paused"):
                continue
            for w in list(chat.get("trips", [])):
                try:                                     # ein defekter Eintrag darf die anderen nicht blockieren
                    check_trip(cid, chat, w, fetched, now, outbox)
                except Exception as e:
                    log("Fehler bei beobachteter Fahrt:", w.get("line"), repr(e))
        last_check = datetime.now()
        save()
    for cid, text in outbox:
        send(int(cid), text)
    if outbox:
        log(f"{len(outbox)} Benachrichtigung(en) gesendet")


def monitor_loop():
    """Alle poll_seconds eine volle Pruefung. Dazwischen wacht der Bot genau zu den Erinnerungszeitpunkten
    (15 und 5 min vor Abfahrt) auf, damit die Nachricht nicht bis zur naechsten Minuten-Pruefung wartet."""
    next_full = 0
    while True:
        try:
            if time.time() >= next_full:
                check_all()
                try:
                    check_routines()
                    cleanup_shares()
                except Exception as e:
                    log("Fehler bei wiederkehrenden Fahrten oder Codes:", repr(e))
                next_full = time.time() + cfg["poll_seconds"]
            due = next_remind_due()
            if due and due[0] < next_full:
                time.sleep(max(0.0, due[0] - time.time()))
                check_all(only=due[1])
            else:
                time.sleep(max(0.2, next_full - time.time()))
        except Exception as e:
            log("Fehler in der Ueberwachung:", e)
            time.sleep(5)


# ---------- Daten speichern ----------

def save():
    path = os.path.join(HERE, cfg["data_file"])
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)    # enthaelt Sitzungen und Chat-IDs: nur fuer den Besitzer lesbar
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load():
    path = os.path.join(HERE, cfg["data_file"])
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            data.update(json.load(fh))


def get_chat(cid):
    c = data["chats"].setdefault(str(cid), {
        "favs": [], "threshold": cfg["default_threshold_min"], "paused": False})
    c.setdefault("trips", [])          # einzeln beobachtete Fahrten
    c.setdefault("routines", [])       # wiederkehrende Fahrten
    return c


def short_id(stop_id):
    return hashlib.sha1(stop_id.encode()).hexdigest()[:8]


# ---------- Telegram-Befehle ----------

HELP = (
    "Abfahrts-Bot \u2013 verfolge einzelne Fahrten und bekomme Meldungen, wenn sich etwas \u00e4ndert.\n\n"
    "/suche <Name> \u2013 Haltestelle suchen und als Favorit speichern\n"
    "/favoriten \u2013 deine Haltestellen, antippen f\u00fcr die n\u00e4chsten 10 Abfahrten\n"
    "/abfahrten [Name] \u2013 n\u00e4chste 10 Abfahrten einer beliebigen Haltestelle\n"
    "/tracking \u2013 deine verfolgten Fahrten\n"
    "/wiederkehrend \u2013 Fahrten, die sich an bestimmten Wochentagen automatisch wiederholen (z. B. Mo\u2013Fr 7:00)\n"
    "/code \u2013 Einladungscode f\u00fcr eine weitere Person erzeugen (Vollzugriff, 24 h g\u00fcltig, einmal nutzbar)\n"
    "/code CODE \u2013 Einladung einl\u00f6sen oder eine geteilte Verbindung ansehen\n"
    "/mitglieder \u2013 eingeladene Personen ansehen und entfernen\n"
    "/jetzt \u2013 aktuelle Versp\u00e4tungen an deinen Favoriten\n"
    "/pause und /weiter \u2013 Benachrichtigungen aus- und einschalten\n"
    "/status \u2013 Einstellungen anzeigen\n\n"
    "Tracking: In einer Abfahrtsliste eine Fahrt antippen. Du bekommst sofort den Stand, danach jede \u00c4nderung "
    "um 2 min sowie eine Erinnerung 15 und 5 min vor Abfahrt."
)


def fav_keyboard(chat):
    return {"inline_keyboard": [[{"text": "\U0001f4cd " + f["name"], "callback_data": "dep:" + f["sid"]}]
                                for f in chat["favs"]]}


FAV_TEXT = ("Deine Favoriten \u2013 Haltestelle antippen f\u00fcr die n\u00e4chsten 10 Abfahrten.\n"
            "Zum Entfernen dort unten auf \u201eFavorit entfernen\u201c tippen.")


def cmd_suche(cid, arg):
    if not arg:
        return send(cid, "Nutzung: /suche Bocholt Bahnhof")
    try:
        results = search_stops(arg)
    except Exception as e:
        return send(cid, f"Suche fehlgeschlagen: {e}")
    if not results:
        return send(cid, "Keine Haltestelle gefunden.")
    kb = []
    with lock:
        for r in results:
            sid = short_id(r["id"])
            data["pending"][sid] = r
            kb.append([{"text": "\u2b50 " + r["name"], "callback_data": "add:" + sid}])
        while len(data["pending"]) > 200:
            data["pending"].pop(next(iter(data["pending"])))
        save()
    send(cid, "Welche Haltestelle als Favorit speichern?", reply_markup={"inline_keyboard": kb})


def lines_keyboard(f, lines):
    chosen = set(f.get("lines") or lines)
    btns = [{"text": ("\u2705 " if l in chosen else "\u2b1c ") + l, "callback_data": f"ln:{f['sid']}:{i}"}
            for i, l in enumerate(lines)]
    rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
    rows.append([{"text": "Alle Linien", "callback_data": "lnall:" + f["sid"]}])
    return {"inline_keyboard": rows}


def open_lines_menu(cid, sid):
    with lock:
        f = next((x for x in get_chat(cid)["favs"] if x["sid"] == sid), None)
        f = copy.deepcopy(f)
    if not f:
        return
    try:
        deps = fetch_departures(f["id"], n=100)
    except Exception as e:
        return send(cid, f"Abruf fehlgeschlagen: {e}")
    lines = sorted({d["lk"] for d in deps if f["mode"] in ("all", d["kind"])} | set(f.get("lines") or []))[:30]
    if not lines:
        return send(cid, "Aktuell keine Linien gefunden.")
    with lock:
        data["menus"][sid] = lines
        save()
    send(cid, f"Welche Linien an {f['name']} sollen gemeldet werden?\n"
              "\u2705 = wird beobachtet. Antippen schaltet um, mindestens eine Linie bleibt aktiv.",
         reply_markup=lines_keyboard(f, lines))


BELL_ON = "\U0001f514"
BELL_OFF = "\U0001f515"


def dep_items(deps):
    return [{"key": d["key"], "line": d["line"], "dest": d["dest"], "kind": d["kind"],
             "planned": d["planned"].timestamp(), "delay": d["delay"], "cancelled": d["cancelled"]}
            for d in deps]


def deps_keyboard(chat, sid, items):
    watched = {w["key"] for w in chat["trips"]}
    rows = []
    for i, it in enumerate(items):
        t = datetime.fromtimestamp(it["planned"], timezone.utc)
        extra = " \u274c" if it["cancelled"] else f" +{it['delay']}" if it["delay"] >= 1 else ""
        label = f"{BELL_ON if it['key'] in watched else BELL_OFF} {hhmm(t)} {it['line']} \u2192 {it['dest']}{extra}"
        rows.append([{"text": label[:60], "callback_data": f"w:{sid}:{i}"}])
    is_fav = any(f["sid"] == sid for f in chat["favs"])
    rows.append([{"text": "\U0001f5d1 Favorit entfernen" if is_fav else "\u2b50 Als Favorit speichern",
                  "callback_data": "fav:" + sid}])
    return {"inline_keyboard": rows}


def trips_keyboard(chat):
    """Pro Fahrt eine volle Zeile. Antippen zeigt den Stand, dort gibt es auch den Knopf zum Beenden."""
    rows = []
    for w in sorted(chat["trips"], key=lambda x: x["planned"]):
        t = datetime.fromtimestamp(w["planned"], timezone.utc)
        icon = "\U0001f68c" if w["kind"] == "bus" else "\U0001f686"
        label = f"{icon} {hhmm(t)} {w['line']} \u2192 {w['dest']} \u00b7 {w['stop_name']}"
        rows.append([{"text": label[:60], "callback_data": "tw:" + w["wid"]}])
    return {"inline_keyboard": rows}


def detail_keyboard(w, cid=None):
    rows = [[
        {"text": "\U0001f504 Aktualisieren", "callback_data": "tr:" + w["wid"]},
        {"text": "\U0001f5d1 Beenden", "callback_data": "ud:" + w["wid"]},
        {"text": "\U0001f517 Teilen", "callback_data": "sh:" + w["wid"]},
    ]]
    if cid is None or is_full(cid):                      # Gaeste koennen keine wiederkehrenden Fahrten anlegen
        rows.append([{"text": "\U0001f501 Wiederholen (Wochentage)", "callback_data": "rep:" + w["wid"]}])
    return {"inline_keyboard": rows}


def cmd_abfahrten(cid, arg):
    if not arg:
        with lock:
            favs = copy.deepcopy(get_chat(cid)["favs"])
        if not favs:
            return send(cid, "Nutzung: /abfahrten Bocholt Bahnhof")
        kb = [[{"text": f["name"], "callback_data": "dep:" + f["sid"]}] for f in favs]
        return send(cid, "Von welcher Haltestelle? Oder /abfahrten <Name> zum Suchen.",
                    reply_markup={"inline_keyboard": kb})
    try:
        results = search_stops(arg)
    except Exception as e:
        return send(cid, f"Suche fehlgeschlagen: {e}")
    if not results:
        return send(cid, "Keine Haltestelle gefunden.")
    kb = []
    with lock:
        for r in results:
            sid = short_id(r["id"])
            data["pending"][sid] = r
            kb.append([{"text": r["name"], "callback_data": "dep:" + sid}])
        while len(data["pending"]) > 200:
            data["pending"].pop(next(iter(data["pending"])))
        save()
    send(cid, "Welche Haltestelle?", reply_markup={"inline_keyboard": kb})


def show_departures(cid, sid):
    with lock:
        chat = get_chat(cid)
        stop = next(({"id": f["id"], "name": f["name"]} for f in chat["favs"] if f["sid"] == sid), None) \
            or data["pending"].get(sid)
    if not stop:
        return send(cid, "Haltestelle nicht mehr bekannt. Bitte /abfahrten <Name> neu eingeben.")
    try:
        items = dep_items(fetch_departures(stop["id"], n=10))[:10]
    except Exception as e:
        return send(cid, f"Abruf fehlgeschlagen: {e}")
    if not items:
        return send(cid, "Keine Abfahrten gefunden.")
    with lock:
        data["deps"][sid] = {"stop": stop, "items": items}
        while len(data["deps"]) > 30:
            data["deps"].pop(next(iter(data["deps"])))
        save()
        kb = deps_keyboard(get_chat(cid), sid, items)
    send(cid, f"N\u00e4chste Abfahrten: {stop['name']}\n"
              f"Antippen schaltet die Benachrichtigung f\u00fcr genau diese Fahrt ein oder aus "
              f"({BELL_ON} = beobachtet). Du bekommst sofort den aktuellen Stand, danach jede \u00c4nderung "
              f"um 2 min und eine Erinnerung 5 min vor Abfahrt.",
         reply_markup=kb)


def cmd_beobachtet(cid):
    with lock:
        chat = get_chat(cid)
        if prune_trips(cid, chat, time.time()):
            save()
        if not chat["trips"]:
            return send(cid, "Keine einzelnen Fahrten beobachtet. Mit /abfahrten ausw\u00e4hlen.")
        send(cid, "Beobachtete Fahrten, die fr\u00fchesten oben.\nFahrt antippen = aktueller Stand und Tracking beenden.",
             reply_markup=trips_keyboard(chat))


def cmd_favoriten(cid):
    with lock:
        chat = get_chat(cid)
        if not chat["favs"]:
            return send(cid, "Noch keine Favoriten. Mit /suche <Name> hinzuf\u00fcgen.")
        send(cid, FAV_TEXT, reply_markup=fav_keyboard(chat))


def cmd_jetzt(cid):
    with lock:
        chat = copy.deepcopy(get_chat(cid))
    if not chat["favs"]:
        return send(cid, "Noch keine Favoriten.")
    horizon = datetime.now(timezone.utc) + timedelta(minutes=cfg["window_min"])
    for f in chat["favs"]:
        if f["mode"] == "off":
            continue
        try:
            deps = fetch_departures(f["id"])
        except Exception as e:
            send(cid, f"{f['name']}: Abruf fehlgeschlagen ({e})")
            continue
        lines = []
        for d in deps:
            if d["planned"] > horizon or not wanted(f, d):
                continue
            if d["cancelled"]:
                lines.append(f"\u274c {hhmm(d['planned'])} {d['line']} \u2192 {d['dest']} f\u00e4llt aus")
            elif d["delay"] >= 1:
                lines.append(f"{severity(d['delay'])} {hhmm(d['planned'])} {d['line']} \u2192 {d['dest']} "
                             f"+{d['delay']} min (jetzt {hhmm(d['actual'])})")
        send(cid, f"\U0001f4cd {f['name']}\n" + ("\n".join(lines) if lines else "Keine Versp\u00e4tungen."))


def cmd_schwelle(cid, arg):
    if not arg.isdigit() or not 1 <= int(arg) <= 60:
        return send(cid, "Nutzung: /schwelle 5   (1\u201360 Minuten)")
    with lock:
        get_chat(cid)["threshold"] = int(arg)
        save()
    send(cid, f"Ich melde mich ab {arg} min Versp\u00e4tung.")


GUEST_COMMANDS = {"/start", "/hilfe", "/help", "/code", "/tracking", "/pause", "/weiter", "/status"}
GUEST_ACTIONS = {"tw", "tr", "ud", "sh", "sr", "sf"}
GUEST_HELP = (
    "Abfahrts-Bot \u2013 du hast Zugriff auf geteilte Verbindungen.\n\n"
    "/code CODE \u2013 geteilte Verbindung ansehen und verfolgen\n"
    "/tracking \u2013 deine verfolgten Fahrten\n"
    "/pause und /weiter \u2013 Benachrichtigungen aus- und einschalten\n"
    "/status \u2013 Einstellungen anzeigen"
)


OWNER_COMMANDS = {"/mitglieder"}                          # Verwaltung nur fuer den Besitzer
INVITE_TTL = 24 * 3600                                    # Einladungscode gilt 24 h


def is_owner(cid):
    return data["owner"] == cid


def display_name(user):
    """Anzeigename aus dem Telegram-Konto: 'Vorname Nachname (@benutzername)'."""
    user = user or {}
    name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x)
    if user.get("username"):
        name = f"{name} (@{user['username']})".strip()
    return name


def who(cid):
    return data["names"].get(str(cid)) or f"Chat {cid}"


def is_full(cid):
    """Besitzer und eingeladene Mitglieder: voller Funktionsumfang."""
    return is_owner(cid) or cid in data["members"]


def may_use(cid, cmd=None, action=None):
    """Besitzer darf alles, Mitglieder (Einladungscode) alles ausser der Verwaltung.
    Gaeste (Code einer geteilten Verbindung) nur Verbindungen ansehen und verfolgen."""
    if is_owner(cid):
        return True
    if cid in data["members"]:
        return cmd not in OWNER_COMMANDS if cmd else action != "mrm"
    if cid in data["guests"]:
        return cmd in GUEST_COMMANDS if cmd else action in GUEST_ACTIONS
    return False


def cmd_help(cid):
    send(cid, HELP if is_owner(cid) else GUEST_HELP)


def cmd_pause(cid, paused):
    with lock:
        get_chat(cid)["paused"] = paused
        save()
    send(cid, "Benachrichtigungen pausiert." if paused else "Benachrichtigungen wieder aktiv.")


def cmd_status(cid):
    with lock:
        c = get_chat(cid)
        lc = last_check.strftime("%H:%M:%S") if last_check else "noch nicht"
        send(cid, f"Favoriten: {len(c['favs'])}\nVerfolgte Fahrten: {len(c['trips'])}\n"
                  f"Status: {'pausiert' if c['paused'] else 'aktiv'}\n"
                  f"Pr\u00fcfintervall: {cfg['poll_seconds']} s \u00b7 letzte Pr\u00fcfung: {lc}")


def handle_message(m):
    cid = m["chat"]["id"]
    text = (m.get("text") or "").strip()
    if not text.startswith("/"):
        return send(cid, "Befehle: /hilfe")
    cmd, _, arg = text.partition(" ")
    cmd, arg = cmd.split("@")[0].lower(), arg.strip()
    with lock:
        if data["owner"] is None and cmd == "/start" and not cfg["owner_id"]:
            data["owner"] = cid                       # erster Nutzer wird Besitzer (nur ohne owner_id in der Konfiguration)
            get_chat(cid)
            save()
        if is_full(cid) and display_name(m.get("from")) not in ("", data["names"].get(str(cid))):
            data["names"][str(cid)] = display_name(m.get("from"))      # Namensaenderung nachziehen
            save()
        if cmd != "/code" and not may_use(cid, cmd=cmd):
            return send(cid, "Das ist ein privater Bot. Mit einem Einladungs- oder Verbindungscode (/code CODE) bekommst du Zugriff.")
    {
        "/start": lambda: cmd_weblogin(cid, arg[4:]) if arg.startswith("web_") else cmd_help(cid),
        "/hilfe": lambda: cmd_help(cid),
        "/help": lambda: cmd_help(cid),
        "/code": lambda: cmd_code(cid, arg, m.get("from")),
        "/mitglieder": lambda: cmd_mitglieder(cid),
        "/suche": lambda: cmd_suche(cid, arg),
        "/favoriten": lambda: cmd_favoriten(cid),
        "/jetzt": lambda: cmd_jetzt(cid),
        "/abfahrten": lambda: cmd_abfahrten(cid, arg),
        "/tracking": lambda: cmd_beobachtet(cid),
        "/wiederkehrend": lambda: cmd_wiederkehrend(cid),
        "/routinen": lambda: cmd_wiederkehrend(cid),
        "/pause": lambda: cmd_pause(cid, True),
        "/weiter": lambda: cmd_pause(cid, False),
        "/status": lambda: cmd_status(cid),
    }.get(cmd, lambda: send(cid, "Unbekannter Befehl. /hilfe"))()


def send_current_status(cid, w):
    """Aktuellen Stand einer beobachteten Fahrt auf Wunsch schicken (aendert nichts an der Beobachtung)."""
    try:
        d = next((x for x in fetch_departures(w["stop"], n=40) if x["key"] == w["key"]), None)
    except Exception as e:
        return send(cid, f"Abruf fehlgeschlagen: {e}")
    if not d:
        return send(cid, "Diese Fahrt steht nicht mehr in der Abfahrtsliste, sie ist vermutlich schon abgefahren.")
    send(cid, fmt_trip("now", d), reply_markup=detail_keyboard(w, cid))


def refresh_status(cid, mid, w):
    """Die Standanzeige einer beobachteten Fahrt an Ort und Stelle aktualisieren."""
    try:
        d = next((x for x in fetch_departures(w["stop"], n=40) if x["key"] == w["key"]), None)
    except Exception as e:
        return log("Abruf fehlgeschlagen:", e)
    text = fmt_trip("now", d) if d else "Diese Fahrt steht nicht mehr in der Abfahrtsliste, sie ist vermutlich schon abgefahren."
    try:
        tg("editMessageText", chat_id=cid, message_id=mid, text=text, reply_markup=detail_keyboard(w, cid))
    except Exception as e:
        log("Aktualisieren ohne Aenderung:", e)          # Telegram meldet einen Fehler, wenn der Text gleich bleibt


def send_first_status(cid, w, event="status"):
    """Aktuellen Stand der gerade gewaehlten Fahrt sofort schicken und als Ausgangslage merken."""
    d = None
    try:
        d = next((x for x in fetch_departures(w["stop"], n=40) if x["key"] == w["key"]), None)
    except Exception as e:
        log("Abruf fehlgeschlagen:", e)
    if not d:
        return send(cid, "Fahrt wird beobachtet. Der aktuelle Stand ist gerade nicht abrufbar.")
    mins = (d["actual"] - datetime.now(timezone.utc)).total_seconds() / 60
    with lock:
        data["state"].setdefault(str(cid), {})["w_" + w["wid"]] = {
            "nd": d["delay"], "cancelled": d["cancelled"], "rem": [t for t in REMIND_AT if mins <= t + REMIND_TOLERANCE], "eta": d["actual"].timestamp(),
            "t": d["planned"].timestamp()}
        save()
    send(cid, fmt_trip(event, d), reply_markup=detail_keyboard(w, cid))


# ---------- Wiederkehrende Fahrten ----------

DAY_NAMES = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def local(dt):
    return dt.astimezone(TZ) if TZ else dt.astimezone()


def days_label(days):
    days = sorted(days)
    if days == [0, 1, 2, 3, 4]:
        return "Mo\u2013Fr"
    if days == list(range(7)):
        return "t\u00e4glich"
    if days == [5, 6]:
        return "Sa+So"
    return ", ".join(DAY_NAMES[d] for d in days)


def days_keyboard(wid, days):
    def b(i):
        return {"text": ("\u2705 " if i in days else "\u2b1c ") + DAY_NAMES[i], "callback_data": f"rd:{wid}:{i}"}
    return {"inline_keyboard": [
        [b(i) for i in range(5)],
        [b(5), b(6)],
        [{"text": "Mo\u2013Fr", "callback_data": f"rp:{wid}:wk"}, {"text": "T\u00e4glich", "callback_data": f"rp:{wid}:all"}],
        [{"text": "\U0001f4be Speichern", "callback_data": f"rs:{wid}"}, {"text": "\u2716 Abbrechen", "callback_data": f"rc:{wid}"}],
    ]}


def routine_text(r):
    return (f"{r['line']} \u2192 {r['dest']}\n\U0001f4cd {r['stop_name']}\n"
            f"\U0001f550 {r['hhmm']} Uhr \u00b7 {days_label(r['days'])}")


def cmd_wiederkehrend(cid):
    with lock:
        chat = get_chat(cid)
        if not chat["routines"]:
            return send(cid, "Noch keine wiederkehrenden Fahrten.\nIn einer Abfahrtsliste eine Fahrt antippen und dann "
                             "unter dem Stand auf \u201eWiederholen\u201c tippen.")
        rows = [[{"text": f"\U0001f501 {r['hhmm']} {r['line']} \u2192 {r['dest']} \u00b7 {days_label(r['days'])}"[:60],
                  "callback_data": "rv:" + r["rid"]}] for r in sorted(chat["routines"], key=lambda r: r["hhmm"])]
        send(cid, f"Wiederkehrende Fahrten. Ab {cfg['routine_lead_min']} min vor Abfahrt bekommst du automatisch den Stand "
                  "und danach die Meldungen wie beim Tracking.", reply_markup={"inline_keyboard": rows})


def handle_routine_callback(cb, cid, mid, action, rest):
    """Knoepfe rund um wiederkehrende Fahrten: rep, rd, rp, rs, rc, rv, rx."""
    answer = ""
    edit = None            # (Text, Tastatur oder None)
    with lock:
        if not is_full(cid):
            return
        chat = get_chat(cid)
        wid, _, extra = rest.partition(":")
        menu = data["menus"].get("r" + wid)
        if action == "rep":
            w = next((x for x in chat["trips"] if x["wid"] == wid), None)
            if not w:
                answer = "Fahrt nicht mehr beobachtet"
            else:
                data["menus"]["r" + wid] = {"w": dict(w), "days": [0, 1, 2, 3, 4]}
                hh = local(datetime.fromtimestamp(w["planned"], timezone.utc)).strftime("%H:%M")
                send(cid, f"\U0001f501 Wiederkehrend: {w['line']} \u2192 {w['dest']}\n\U0001f4cd {w['stop_name']} \u00b7 {hh} Uhr\n"
                          "An welchen Wochentagen?", reply_markup=days_keyboard(wid, [0, 1, 2, 3, 4]))
        elif action in ("rd", "rp", "rc", "rs") and not menu:
            answer = "Auswahl abgelaufen, bitte neu starten"
        elif action == "rd":
            i = int(extra)
            days = set(menu["days"]) ^ {i}
            menu["days"] = sorted(days) or [i]            # mindestens ein Tag
            edit = (None, days_keyboard(wid, menu["days"]))
        elif action == "rp":
            menu["days"] = [0, 1, 2, 3, 4] if extra == "wk" else list(range(7))
            edit = (None, days_keyboard(wid, menu["days"]))
        elif action == "rc":
            data["menus"].pop("r" + wid, None)
            edit = ("Abgebrochen.", None)
        elif action == "rs":
            w = menu["w"]
            local_planned = local(datetime.fromtimestamp(w["planned"], timezone.utc))
            line = re.sub(r"\s*\(\d+\)$", "", w["line"])
            hhmm_s = local_planned.strftime("%H:%M")
            rid = short_id(f"{w['stop']}|{line}|{w['dest']}|{hhmm_s}")
            r = next((x for x in chat["routines"] if x["rid"] == rid), None)
            if r:
                r["days"] = menu["days"]
            else:
                r = {"rid": rid, "stop": w["stop"], "stop_name": w["stop_name"], "line": line, "dest": w["dest"],
                     "kind": w["kind"], "hhmm": hhmm_s, "days": menu["days"], "last": ""}
                chat["routines"].append(r)
            r["last"] = local_planned.strftime("%Y-%m-%d")   # die gerade verfolgte Fahrt zaehlt fuer heute schon
            data["menus"].pop("r" + wid, None)
            answer = "Gespeichert"
            edit = (f"\u2705 Wiederkehrende Fahrt gespeichert\n{routine_text(r)}\n\n"
                    f"Ab {cfg['routine_lead_min']} min vor Abfahrt melde ich mich mit dem Stand.", None)
        elif action == "rv":
            r = next((x for x in chat["routines"] if x["rid"] == wid), None)
            if r:
                send(cid, "\U0001f501 " + routine_text(r), reply_markup={"inline_keyboard": [[
                    {"text": "\U0001f5d1 L\u00f6schen", "callback_data": "rx:" + wid}]]})
            else:
                answer = "Nicht mehr vorhanden"
        elif action == "rx":
            r = next((x for x in chat["routines"] if x["rid"] == wid), None)
            if r:
                chat["routines"].remove(r)
                edit = (f"\u2705 Gel\u00f6scht:\n{routine_text(r)}", None)
            else:
                answer = "Nicht mehr vorhanden"
        save()
    try:
        tg("answerCallbackQuery", callback_query_id=cb["id"], text=answer)
        if edit:
            text, kb = edit
            if text is None:
                tg("editMessageReplyMarkup", chat_id=cid, message_id=mid, reply_markup=kb)
            else:
                tg("editMessageText", chat_id=cid, message_id=mid, text=text)
    except Exception as e:
        log("Callback-Antwort fehlgeschlagen:", e)


def check_routines():
    """Wiederkehrende Fahrten: Im Zeitfenster vor der Abfahrt die heutige Fahrt suchen und automatisch verfolgen."""
    lnow = local(datetime.now(timezone.utc))
    today = lnow.strftime("%Y-%m-%d")
    todo = []
    with lock:
        for cid, chat in data["chats"].items():
            if chat.get("paused"):
                continue
            for r in chat.get("routines", []):
                if lnow.weekday() not in r["days"] or r.get("last") == today:
                    continue
                h, m = map(int, r["hhmm"].split(":"))
                mins = (lnow.replace(hour=h, minute=m, second=0, microsecond=0) - lnow).total_seconds() / 60
                if -2 <= mins <= cfg["routine_lead_min"]:
                    todo.append((cid, dict(r), mins))
    cache = {}
    for cid, r, mins in todo:
        try:
            if r["stop"] not in cache:
                cache[r["stop"]] = fetch_departures(r["stop"], n=100)
        except Exception as e:
            log("Abruf fehlgeschlagen:", e)
            continue
        same = [x for x in cache[r["stop"]] if x["lk"] == r["line"] and local(x["planned"]).strftime("%H:%M") == r["hhmm"]]
        d = next((x for x in same if x["dest"] == r["dest"]), None) or (same[0] if len(same) == 1 else None)
        if d:
            w = {"wid": short_id(d["key"]), "key": d["key"], "stop": r["stop"], "stop_name": r["stop_name"],
                 "line": d["line"], "dest": d["dest"], "kind": d["kind"], "planned": d["planned"].timestamp()}
            add = False
            with lock:
                chat = get_chat(cid)
                if len(chat["trips"]) < MAX_TRIPS and not any(t["key"] == w["key"] for t in chat["trips"]):
                    chat["trips"].append(w)
                    add = True
                for x in chat["routines"]:
                    if x["rid"] == r["rid"]:
                        x["last"] = today
                save()
            if add:
                send_first_status(cid, w, "routine")
        elif mins <= 5:                                   # kurz vor Abfahrt noch nicht gefunden: einmal warnen
            with lock:
                for x in get_chat(cid)["routines"]:
                    if x["rid"] == r["rid"]:
                        x["last"] = today
                save()
            send(cid, f"\u26a0 Wiederkehrende Fahrt heute nicht in der Abfahrtsliste (f\u00e4llt sie aus?)\n{routine_text(r)}")


ROUTINE_ACTIONS = ("rep", "rd", "rp", "rs", "rc", "rv", "rx")


# ---------- Verbindungen teilen ----------

SHARE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # ohne leicht verwechselbare Zeichen (0/O, 1/I)
SHARE_GRACE = 600                                         # Code gilt bis 10 min nach der Abfahrt
SHARE_ACTIONS = ("sh", "sr", "sf")
code_fails = {}                                           # cid -> Zeitpunkte falscher Codes (nur im Speicher)


def cleanup_shares():
    """Abgelaufene Codes loeschen: 10 min nach der (verspaeteten) Abfahrt."""
    now = time.time()
    with lock:
        for e in data["shares"].values():
            st = data["state"].get(str(e["by"]), {}).get("w_" + str(e["w"].get("wid")), {})
            e["eta"] = max(e.get("eta", 0), st.get("eta") or 0)          # voraussichtliche Abfahrt merken, solange bekannt
        gone = [c for c, e in data["shares"].items() if max(e["w"]["planned"], e.get("eta", 0)) + SHARE_GRACE < now]
        for c in gone:
            del data["shares"][c]
        old = [c for c, e in data["invites"].items() if e["exp"] < now]
        for c in old:
            del data["invites"][c]
        if gone or old:
            log(f"{len(gone) + len(old)} abgelaufene(r) Code(s) geloescht")
            save()


def create_share(cid, w):
    with lock:
        code = next((c for c, e in data["shares"].items() if e["by"] == cid and e["w"]["key"] == w["key"]), None)
        if not code:
            while True:
                code = "".join(secrets.choice(SHARE_ALPHABET) for _ in range(6))
                if code not in data["shares"]:
                    break
            data["shares"][code] = {"w": dict(w), "by": cid, "eta": w["planned"]}
            save()
    return code


def show_shared(cid, code, w, mid=None):
    """Aktuellen Stand einer geteilten Verbindung zeigen (neu senden oder eine vorhandene Nachricht aktualisieren)."""
    d = None
    try:
        d = next((x for x in fetch_departures(w["stop"], n=40) if x["key"] == w["key"]), None)
    except Exception as e:
        log("Abruf fehlgeschlagen:", e)
    text = fmt_trip("shared", d) if d else "Diese Fahrt steht nicht mehr in der Abfahrtsliste, sie ist vermutlich schon abgefahren."
    with lock:
        tracked = any(t["key"] == w["key"] for t in get_chat(cid)["trips"])
    if tracked:
        kb = detail_keyboard(w, cid)
    else:
        kb = {"inline_keyboard": [[
            {"text": "\U0001f504 Aktualisieren", "callback_data": "sr:" + code},
            {"text": "\U0001f514 Verfolgen", "callback_data": "sf:" + code},
        ]]}
    if mid is None:
        return send(cid, text, reply_markup=kb)
    try:
        tg("editMessageText", chat_id=cid, message_id=mid, text=text, reply_markup=kb)
    except Exception as e:
        log("Aktualisieren ohne Aenderung:", e)


def create_invite(cid):
    with lock:
        while True:
            code = "".join(secrets.choice(SHARE_ALPHABET) for _ in range(8))
            if code not in data["invites"] and code not in data["shares"]:
                break
        data["invites"][code] = {"by": cid, "exp": time.time() + INVITE_TTL}
        save()
    send(cid, "\U0001f511 Einladungscode (Vollzugriff)\n\n"
              "Schick diese Zeile der Person. Sie muss sie nur noch abschicken (Antippen kopiert sie):\n\n"
              f"<code>/code {code}</code>\n\n"
              "Der Code gilt 24 Stunden und nur für eine Person.", parse_mode="HTML")


def members_keyboard():
    return {"inline_keyboard": [[{"text": f"\U0001f5d1 {who(m)} entfernen"[:60], "callback_data": f"mrm:{m}"}]
                                for m in data["members"]]}


def cmd_mitglieder(cid):
    with lock:
        if not data["members"]:
            return send(cid, "Noch niemand eingeladen. /code erzeugt einen Einladungscode.")
        kb = members_keyboard()
    send(cid, "Eingeladene Personen:\n" + "\n".join("• " + who(m) for m in data["members"]) +
         "\n\nAntippen entfernt den Zugang.", reply_markup=kb)


def handle_members_callback(cb, cid, mid, member):
    with lock:
        if not is_owner(cid):
            return
        m = int(member) if member.lstrip("-").isdigit() else None
        if m in data["members"]:
            data["members"].remove(m)
            data["chats"].pop(str(m), None)               # Tracking und Favoriten der Person gehen mit
            data["state"].pop(str(m), None)
            data["names"].pop(str(m), None)
            data["sessions"] = {t: s for t, s in data["sessions"].items() if s["cid"] != m}   # Website-Anmeldungen beenden
            save()
            answer = "Zugang entfernt"
        else:
            answer = "Nicht mehr vorhanden"
        kb = members_keyboard() if data["members"] else None
    try:
        tg("answerCallbackQuery", callback_query_id=cb["id"], text=answer)
        if kb:
            tg("editMessageReplyMarkup", chat_id=cid, message_id=mid, reply_markup=kb)
        else:
            tg("editMessageText", chat_id=cid, message_id=mid, text="Keine eingeladenen Personen mehr.")
    except Exception as e:
        log("Callback-Antwort fehlgeschlagen:", e)


def cmd_code(cid, arg, user=None):
    code = arg.strip().upper().replace(" ", "")
    if not code:
        if is_owner(cid):
            return create_invite(cid)
        return send(cid, "Nutzung: /code ABC234")
    now = time.time()
    fails = [t for t in code_fails.get(cid, []) if t > now - 3600]
    if len(fails) >= 10:
        return send(cid, "Zu viele ung\u00fcltige Codes. Bitte sp\u00e4ter erneut versuchen.")
    cleanup_shares()
    invited = None
    with lock:
        inv = data["invites"].get(code)
        if inv and not is_full(cid):
            del data["invites"][code]                     # Einladung ist nur einmal nutzbar
            if cid in data["guests"]:
                data["guests"].remove(cid)
            data["members"].append(cid)
            data["names"][str(cid)] = display_name(user)
            get_chat(cid)
            save()
            invited = inv["by"]
            name = who(cid)
    if invited is not None:
        send(cid, "✅ Einladung eingelöst. Du kannst den Bot jetzt voll nutzen.\n\n" + HELP)
        send(invited, f"✅ Einladungscode {code} wurde von {name} eingelöst. /mitglieder zeigt alle Eingeladenen.")
        return
    with lock:
        e = data["shares"].get(code)
        if not e:
            code_fails[cid] = fails + [now]
            return send(cid, "Code ung\u00fcltig oder abgelaufen.")
        if not is_owner(cid) and cid not in data["guests"]:
            data["guests"].append(cid)                    # Zugriff nur auf geteilte Verbindungen
        get_chat(cid)
        save()
        w = dict(e["w"])
    show_shared(cid, code, w)


def handle_share_callback(cb, cid, mid, action, rest):
    """Knoepfe fuers Teilen: sh (Code erzeugen), sr (geteilte Verbindung aktualisieren), sf (verfolgen)."""
    answer = ""
    todo = None
    with lock:
        if not may_use(cid, action=action):
            return
        chat = get_chat(cid)
        if action == "sh":
            w = next((x for x in chat["trips"] if x["wid"] == rest), None)
            if not w:
                answer = "Nicht mehr beobachtet"
            else:
                todo = ("share", dict(w))
        else:
            e = data["shares"].get(rest)
            if not e:
                answer = "Code abgelaufen"
            else:
                w = dict(e["w"])
                if action == "sr":
                    todo = ("refresh", w)
                elif any(t["key"] == w["key"] for t in chat["trips"]):
                    answer = "Wird schon verfolgt"
                elif len(chat["trips"]) >= MAX_TRIPS:
                    answer = f"Es sind höchstens {MAX_TRIPS} verfolgte Fahrten möglich"
                else:
                    chat["trips"].append(w)
                    save()
                    answer = "Wird verfolgt"
                    todo = ("follow", w)
    try:
        tg("answerCallbackQuery", callback_query_id=cb["id"], text=answer)
    except Exception as e:
        log("Callback-Antwort fehlgeschlagen:", e)
    if not todo:
        return
    kind_, w = todo
    if kind_ == "share":
        code = create_share(cid, w)
        send(cid, "\U0001f517 Verbindung teilen\n"
                  f"{html.escape(w['line'])} \u2192 {html.escape(w['dest'])} \u00b7 {html.escape(w['stop_name'])}\n\n"
                  "Schick diese Zeile weiter. Die Person muss sie nur noch abschicken (Antippen kopiert sie):\n\n"
                  f"<code>/code {code}</code>\n\n"
                  "Der Code gilt bis 10 Minuten nach der Abfahrt.", parse_mode="HTML")
    elif kind_ == "refresh":
        show_shared(cid, rest, w, mid)
    else:
        send_first_status(cid, w)


def handle_callback(cb):
    cid = cb["message"]["chat"]["id"]
    mid = cb["message"]["message_id"]
    action, _, sid = cb["data"].partition(":")
    if action in ROUTINE_ACTIONS:
        return handle_routine_callback(cb, cid, mid, action, sid)
    if action in SHARE_ACTIONS:
        return handle_share_callback(cb, cid, mid, action, sid)
    if action == "mrm":
        return handle_members_callback(cb, cid, mid, sid)
    if action == "wl":
        return handle_weblogin_callback(cb, cid, mid, sid)
    answer = ""
    menu_kb = None
    new_watch = None
    show_w = None
    ended = None
    with lock:
        if not may_use(cid, action=action):
            return
        chat = get_chat(cid)
        if action == "add":
            stop = data["pending"].get(sid)
            if stop and not any(f["sid"] == sid for f in chat["favs"]):
                chat["favs"].append({"sid": sid, "id": stop["id"], "name": stop["name"],
                                     "mode": "all", "seeded": False})
                answer = f"{stop['name']} gespeichert"
            else:
                answer = "Schon vorhanden"
        elif action in ("ln", "lnall"):
            fsid, _, idx = sid.partition(":")
            f = next((x for x in chat["favs"] if x["sid"] == fsid), None)
            menu = data["menus"].get(fsid)
            if f and menu:
                if action == "lnall":
                    f["lines"] = []
                else:
                    chosen = set(f.get("lines") or menu) ^ {menu[int(idx)]}
                    if not chosen:
                        answer = "Mindestens eine Linie muss aktiv bleiben"
                    else:
                        f["lines"] = [] if chosen >= set(menu) else sorted(chosen)
                menu_kb = lines_keyboard(f, menu)
        elif action == "w":
            dsid, _, idx = sid.partition(":")
            entry = data["deps"].get(dsid)
            if entry:
                it = entry["items"][int(idx)]
                old = next((w for w in chat["trips"] if w["key"] == it["key"]), None)
                if old:
                    chat["trips"].remove(old)
                    data["state"].get(str(cid), {}).pop("w_" + old["wid"], None)
                    answer = "Nicht mehr beobachtet"
                elif len(chat["trips"]) >= MAX_TRIPS:
                    answer = f"Es sind höchstens {MAX_TRIPS} verfolgte Fahrten möglich"
                else:
                    chat["trips"].append({
                        "wid": short_id(it["key"]), "key": it["key"], "stop": entry["stop"]["id"],
                        "stop_name": entry["stop"]["name"], "line": it["line"], "dest": it["dest"],
                        "kind": it["kind"], "planned": it["planned"]})
                    new_watch = chat["trips"][-1]
                    answer = "Wird beobachtet"
                menu_kb = deps_keyboard(chat, dsid, entry["items"])
            else:
                answer = "Liste veraltet, bitte /abfahrten neu aufrufen"
        elif action == "fav":
            entry = data["deps"].get(sid)
            fav = next((f for f in chat["favs"] if f["sid"] == sid), None)
            if fav:
                chat["favs"].remove(fav)
                answer = "Favorit entfernt"
            else:
                stop = (entry or {}).get("stop") or data["pending"].get(sid)
                if stop:
                    chat["favs"].append({"sid": sid, "id": stop["id"], "name": stop["name"],
                                         "mode": "all", "seeded": False})
                    answer = "Favorit gespeichert"
            if entry:
                menu_kb = deps_keyboard(chat, sid, entry["items"])
        elif action == "tw":
            show_w = next((x for x in chat["trips"] if x["wid"] == sid), None)
            if not show_w:
                answer = "Nicht mehr beobachtet"
        elif action in ("tr", "ud"):
            w = next((x for x in chat["trips"] if x["wid"] == sid), None)
            if not w:
                answer = "Nicht mehr beobachtet"
            elif action == "tr":
                show_w = w
            else:
                chat["trips"].remove(w)
                data["state"].get(str(cid), {}).pop("w_" + sid, None)
                ended = w
                answer = "Tracking beendet"
        elif action == "uw":
            w = next((x for x in chat["trips"] if x["wid"] == sid), None)
            if w:
                chat["trips"].remove(w)
                data["state"].get(str(cid), {}).pop("w_" + sid, None)
                answer = "Beobachtung beendet"
            menu_kb = trips_keyboard(chat)
        elif action in ("mode", "del"):
            for f in chat["favs"]:
                if f["sid"] == sid:
                    if action == "mode":
                        f["mode"] = MODES[(MODES.index(f["mode"]) + 1) % len(MODES)]
                    else:
                        chat["favs"].remove(f)
                        data["state"].get(str(cid), {}).pop(sid, None)
                    break
        save()
        kb = fav_keyboard(chat)
        empty = not chat["favs"]
        no_trips = not chat["trips"]
    if action == "lines":
        tg("answerCallbackQuery", callback_query_id=cb["id"])
        return open_lines_menu(cid, sid)
    if action == "dep":
        tg("answerCallbackQuery", callback_query_id=cb["id"])
        return show_departures(cid, sid)
    if action in ("tw", "tr", "ud"):
        tg("answerCallbackQuery", callback_query_id=cb["id"], text=answer)
        if action == "tw" and show_w:
            send_current_status(cid, show_w)
        elif action == "tr" and show_w:
            refresh_status(cid, mid, show_w)
        elif action == "ud" and ended:
            try:
                tg("editMessageText", chat_id=cid, message_id=mid, text=f"\u2705 Tracking beendet: {ended['line']} \u2192 {ended['dest']} ({ended['stop_name']})")
            except Exception as e:
                log("Nachricht nicht aktualisiert:", e)
        return
    try:
        tg("answerCallbackQuery", callback_query_id=cb["id"], text=answer)
        if action == "uw" and no_trips:
            tg("editMessageText", chat_id=cid, message_id=mid, text="Keine beobachteten Fahrten mehr.")
        elif action in ("ln", "lnall", "w", "uw", "fav") and menu_kb:
            tg("editMessageReplyMarkup", chat_id=cid, message_id=mid, reply_markup=menu_kb)
        if action in ("mode", "del"):
            if empty:
                tg("editMessageText", chat_id=cid, message_id=mid, text="Keine Favoriten mehr.")
            else:
                tg("editMessageReplyMarkup", chat_id=cid, message_id=mid, reply_markup=kb)
    except Exception as e:
        log("Callback-Antwort fehlgeschlagen:", e)
    if new_watch:
        send_first_status(cid, new_watch)


def handle_update(u):
    """Jedes Update in eigenem Thread, damit eine langsame Anfrage die anderen nicht aufhaelt."""
    try:
        if "message" in u:
            handle_message(u["message"])
        elif "callback_query" in u:
            handle_callback(u["callback_query"])
    except Exception as e:
        log("Fehler bei Update:", e)


def poll_loop():
    offset = None
    while True:
        try:
            params = {"timeout": 50, "allowed_updates": ["message", "callback_query"]}
            if offset:
                params["offset"] = offset
            for u in tg("getUpdates", **params):
                offset = u["update_id"] + 1
                threading.Thread(target=handle_update, args=(u,), daemon=True).start()
        except Exception as e:
            log("getUpdates fehlgeschlagen:", e)
            time.sleep(5)


# ---------- Website-Anbindung ----------
# Die Website ruft diese kleine HTTP-API auf. Angemeldet wird ueber Telegram: Die Website zeigt einen Link zum Bot,
# der Bot fragt nach Bestaetigung, danach holt die Website ein Sitzungs-Token. Nur Besitzer und Mitglieder kommen durch.

MAX_TRIPS = 50                # verfolgte Fahrten pro Person
SESSION_TTL = 30 * 86400
LOGIN_TTL = 600
web_logins = {}            # Code -> {"poll": geheimes Token, "exp": Ablauf, "cid": Chat nach Bestaetigung} (nur im Speicher)


def cmd_weblogin(cid, code):
    if not is_full(cid):
        return send(cid, "Die Anmeldung auf der Website ist nur für den Besitzer und eingeladene Personen möglich.")
    with lock:
        e = web_logins.get(code)
        valid = bool(e) and e["exp"] > time.time()
    if not valid:
        return send(cid, "Der Anmeldelink ist abgelaufen. Bitte auf der Website erneut auf „Mit Telegram anmelden“ klicken.")
    ua = (e.get("ua") or "unbekannt")[:100]
    send(cid, "\U0001f310 Anmeldung auf der Website\n\n"
              f"Angefragt von IP: {e.get('ip', '?')}\nBrowser: {ua}\n\n"
              "⚠️ Bestätige nur, wenn du sie gerade selbst gestartet hast. Hat dir jemand den Link geschickt, tippe nicht auf „Ja“, "
              "sonst bekommt diese Person Zugriff auf deine Fahrten.",
         reply_markup={"inline_keyboard": [[{"text": "✅ Ja, anmelden", "callback_data": "wl:" + code}]]})


def handle_weblogin_callback(cb, cid, mid, code):
    with lock:
        e = web_logins.get(code)
        if not is_full(cid):
            return
        ok = bool(e) and e["exp"] > time.time()
        if ok:
            e["cid"] = cid
    try:
        tg("answerCallbackQuery", callback_query_id=cb["id"], text="Angemeldet" if ok else "Abgelaufen")
        tg("editMessageText", chat_id=cid, message_id=mid,
           text="✅ Angemeldet. Du kannst zur Website zurückgehen." if ok else "Der Anmeldelink ist abgelaufen.")
    except Exception as e:
        log("Callback-Antwort fehlgeschlagen:", e)


def web_cleanup():
    now = time.time()
    with lock:
        for c in [c for c, e in web_logins.items() if e["exp"] < now]:
            del web_logins[c]
        old = [t for t, s in data["sessions"].items() if s["exp"] < now]
        for t in old:
            del data["sessions"][t]
        if old:
            save()


def web_login_start(meta=None):
    meta = meta or {}
    with lock:
        if len(web_logins) >= 50:
            return 429, {"error": "zu viele Anmeldungen, bitte später erneut versuchen"}
        code = "".join(secrets.choice(SHARE_ALPHABET) for _ in range(10))
        poll = secrets.token_urlsafe(16)
        web_logins[code] = {"poll": poll, "exp": time.time() + LOGIN_TTL, "cid": None,
                            "ip": meta.get("ip", "?"), "ua": meta.get("ua", "")}
    return 200, {"poll": poll, "link": f"https://t.me/{cfg['bot_username']}?start=web_{code}"}


def web_login_poll(poll):
    with lock:
        code, e = next(((c, x) for c, x in web_logins.items() if secrets.compare_digest(x["poll"], poll)), (None, None))
        if not e:
            return 200, {"status": "expired"}
        if e["cid"] is None:
            return 200, {"status": "pending"}
        token = secrets.token_urlsafe(32)
        data["sessions"][token_hash(token)] = {"cid": e["cid"], "exp": time.time() + SESSION_TTL}   # im Klartext sieht nur die Website das Token
        del web_logins[code]
        save()
        return 200, {"status": "ok", "session": token, "name": who(e["cid"])}


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def web_session(auth):
    token = token_hash(auth[7:]) if auth.startswith("Bearer ") else ""
    with lock:
        s = data["sessions"].get(token)
        if s and s["exp"] > time.time() and is_full(s["cid"]):
            return token, s["cid"]
    return None, None


def clean_str(v, limit=200):
    if not isinstance(v, str) or not v or len(v) > limit:
        raise ValueError("ungültige Eingabe")
    return v


def web_favs(chat):
    return [{"id": f["id"], "name": f["name"]} for f in chat["favs"]]


def web_add_fav(chat, fid, name):
    if not any(f["id"] == fid for f in chat["favs"]):
        chat["favs"].append({"sid": short_id(fid), "id": fid, "name": name, "mode": "all", "seeded": False})


def web_trips(cid, chat):
    prune_trips(cid, chat, time.time())
    state = data["state"].get(str(cid), {})
    trips = []
    for w in chat["trips"]:
        st = state.get("w_" + str(w["wid"]), {})
        trips.append({"key": w["key"], "stop_name": w["stop_name"], "line": w["line"], "dest": w["dest"],
                      "kind": w["kind"], "planned": w["planned"], "eta": st.get("eta") or w["planned"],
                      "cancelled": bool(st.get("cancelled"))})
    routines = [{"line": r["line"], "dest": r["dest"], "stop_name": r["stop_name"], "hhmm": r["hhmm"],
                 "days": days_label(r["days"])} for r in chat["routines"]]
    return {"trips": sorted(trips, key=lambda t: t["planned"]), "routines": routines}


def web_route(method, path, q, body, auth, meta=None):
    web_cleanup()
    if method == "POST" and path == "/login/start":
        return web_login_start(meta)
    if method == "GET" and path == "/login/poll":
        return web_login_poll(q.get("token", [""])[0])
    token, cid = web_session(auth)
    if cid is None:
        return 401, {"error": "nicht angemeldet"}
    if method == "POST" and path == "/logout":
        with lock:
            data["sessions"].pop(token, None)
            save()
        return 200, {}
    if method == "GET" and path == "/me":
        with lock:
            return 200, {"name": who(cid)}
    if path.startswith("/favs"):
        with lock:
            chat = get_chat(cid)
            if method == "POST" and path == "/favs/merge":
                for f in body.get("favs", [])[:100]:
                    web_add_fav(chat, clean_str(f.get("id")), clean_str(f.get("name")))
                save()
            elif method == "POST" and path == "/favs":
                fid = clean_str(body.get("id"))
                if body.get("on"):
                    web_add_fav(chat, fid, clean_str(body.get("name")))
                else:
                    chat["favs"][:] = [f for f in chat["favs"] if f["id"] != fid]
                save()
            elif not (method == "GET" and path == "/favs"):
                return 404, {"error": "unbekannt"}
            return 200, {"favs": web_favs(chat)}
    if path == "/trips":
        if method == "GET":
            with lock:
                chat = get_chat(cid)
                out = web_trips(cid, chat)
                save()
            return 200, out
        if method == "POST":
            w = {"key": clean_str(body.get("key"), 400), "stop": clean_str(body.get("stop"), 400),
                 "stop_name": clean_str(body.get("stop_name")), "line": clean_str(body.get("line")),
                 "dest": clean_str(body.get("dest")), "kind": body.get("kind"), "planned": body.get("planned")}
            if w["kind"] not in ("bus", "bahn") or not isinstance(w["planned"], (int, float)):
                raise ValueError("ungültige Eingabe")
            w["wid"] = short_id(w["key"])
            with lock:
                chat = get_chat(cid)
                new = not any(t["key"] == w["key"] for t in chat["trips"])
                if new and len(chat["trips"]) >= MAX_TRIPS:
                    return 429, {"error": "zu viele verfolgte Fahrten"}
                if new:
                    chat["trips"].append(w)
                    save()
            if new:
                threading.Thread(target=send_first_status, args=(cid, w), daemon=True).start()
            return 200, {}
        if method == "DELETE":
            key = q.get("key", [""])[0]
            with lock:
                chat = get_chat(cid)
                for w in [t for t in chat["trips"] if t["key"] == key]:
                    chat["trips"].remove(w)
                    data["state"].get(str(cid), {}).pop("w_" + w["wid"], None)
                save()
            return 200, {}
    return 404, {"error": "unbekannt"}


rate_hits = {}             # (IP, Aktion) -> Zeitpunkte der letzten Aufrufe


def rate_ok(ip, action, limit, window):
    now = time.time()
    with lock:
        hits = [t for t in rate_hits.get((ip, action), []) if t > now - window]
        if len(hits) >= limit:
            rate_hits[(ip, action)] = hits
            return False
        rate_hits[(ip, action)] = hits + [now]
        if len(rate_hits) > 5000:      # Speicher begrenzen
            for k in [k for k, v in rate_hits.items() if not v or v[-1] < now - 3600]:
                del rate_hits[k]
        return True


class Api(BaseHTTPRequestHandler):
    timeout = 15               # haengende Verbindungen nach 15 s trennen

    def log_message(self, *a):
        pass

    def client_ip(self):
        if cfg["trust_proxy"]:     # nur hinter eigenem Proxy, sonst laesst sich der Header faelschen
            ip = (self.headers.get("X-Real-IP") or "").strip()
            if ip:
                return ip
        return self.client_address[0]

    def reply(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cfg["web_origin"]:      # CORS nur fuer die eigene Website. Gleiche Adresse (Proxy) braucht keins.
            self.send_header("Access-Control-Allow-Origin", cfg["web_origin"])
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_any(self, method):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > 20000:
                return self.reply(413, {"error": "zu groß"})
            body = json.loads(self.rfile.read(n)) if n else {}
            if not isinstance(body, dict):
                raise ValueError("ungültige Eingabe")
            url = urllib.parse.urlparse(self.path)
            if url.path.rstrip("/") == "/login/start" and not rate_ok(self.client_ip(), "login", 5, 600):
                return self.reply(429, {"error": "zu viele Anmeldeversuche, bitte später erneut versuchen"})
            code, out = web_route(method, url.path.rstrip("/"), urllib.parse.parse_qs(url.query), body,
                                  self.headers.get("Authorization", ""),
                                  {"ip": self.client_ip(), "ua": self.headers.get("User-Agent", "")})
        except (ValueError, AttributeError, TypeError, KeyError):
            code, out = 400, {"error": "ungültige Anfrage"}
        except Exception as e:
            log("Fehler in der Web-API:", repr(e))
            code, out = 500, {"error": "Serverfehler"}
        self.reply(code, out)

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_DELETE(self):
        self.handle_any("DELETE")

    def do_OPTIONS(self):
        self.reply(200, {})


def start_api():
    srv = ThreadingHTTPServer((cfg["api_host"], cfg["api_port"]), Api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"Web-API auf {cfg['api_host']}:{cfg['api_port']}")


# ---------- Start ----------

def load_config():
    path = os.path.join(HERE, "config.json")
    cfg.update({"bot_token": "", "poll_seconds": 60, "default_threshold_min": 5,
                "window_min": 120, "data_file": "data.json", "ipv4_only": True, "routine_lead_min": 60,
                "api_port": 8787, "api_host": "127.0.0.1", "web_origin": "", "bot_username": "",
                "owner_id": 0, "trust_proxy": False})
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    # Umgebungsvariablen haben Vorrang (praktisch im Container, dann liegen keine Geheimnisse in Dateien)
    cfg["bot_token"] = os.environ.get("BOT_TOKEN") or cfg["bot_token"]
    for env, key in (("BOT_API_HOST", "api_host"), ("BOT_DATA_FILE", "data_file"),
                     ("BOT_WEB_ORIGIN", "web_origin"), ("BOT_USERNAME", "bot_username")):
        cfg[key] = os.environ.get(env) or cfg[key]
    cfg["owner_id"] = int(os.environ.get("BOT_OWNER_ID") or cfg["owner_id"] or 0)
    cfg["trust_proxy"] = os.environ.get("BOT_TRUST_PROXY", str(cfg["trust_proxy"])).lower() in ("1", "true", "yes")
    if cfg["ipv4_only"]:
        force_ipv4()


def selftest(text):
    stops = search_stops(text)
    print("Treffer:", [s["name"] for s in stops])
    if not stops:
        return
    deps = fetch_departures(stops[0]["id"])
    print(f"{stops[0]['name']}: {len(deps)} Abfahrten")
    for d in deps[:8]:
        print(" ", hhmm(d["planned"]), d["kind"], d["line"], "->", d["dest"], f"{d['delay']:+d} min",
              "AUSFALL" if d["cancelled"] else "")


def main():
    load_config()
    if len(sys.argv) > 2 and sys.argv[1] == "--selftest":
        return selftest(sys.argv[2])
    if not cfg["bot_token"] or cfg["bot_token"].startswith("DEIN"):
        sys.exit("Kein bot_token. Trage ihn in config.json ein (oder setze BOT_TOKEN).")
    load()
    for k in [k for k in data["sessions"] if len(k) != 64]:     # alte Sitzungen mit Klartext-Token ungueltig machen
        del data["sessions"][k]
    if cfg["owner_id"]:
        data["owner"] = cfg["owner_id"]                          # Besitzer fest vorgegeben, niemand kann ihn per /start uebernehmen
        get_chat(cfg["owner_id"])
    save()
    if data["owner"] is None:
        log("WARNUNG: Kein Besitzer festgelegt. Wer dem Bot zuerst /start schreibt, wird Besitzer. BOT_OWNER_ID setzen!")
    tg("setMyCommands", commands=[
        {"command": "suche", "description": "Haltestelle suchen"},
        {"command": "favoriten", "description": "Deine Haltestellen"},
        {"command": "jetzt", "description": "Aktuelle Versp\u00e4tungen"},
        {"command": "abfahrten", "description": "Naechste Abfahrten, Fahrten beobachten"},
        {"command": "tracking", "description": "Beobachtete Fahrten"},
        {"command": "wiederkehrend", "description": "Wiederkehrende Fahrten"},
        {"command": "code", "description": "Einladungscode erzeugen oder einlösen"},
        {"command": "mitglieder", "description": "Eingeladene Personen verwalten"},
        {"command": "pause", "description": "Benachrichtigungen aus"},
        {"command": "weiter", "description": "Benachrichtigungen an"},
        {"command": "status", "description": "Einstellungen"},
    ])
    if cfg["api_port"]:
        cfg["bot_username"] = cfg["bot_username"] or tg("getMe")["username"]
        start_api()
    log("Bot l\u00e4uft.")
    threading.Thread(target=monitor_loop, daemon=True).start()
    poll_loop()


if __name__ == "__main__":
    main()
