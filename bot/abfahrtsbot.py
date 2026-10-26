#!/usr/bin/env python3
"""Telegram-Bot: meldet Verspaetungen und Ausfaelle an den Favoriten-Haltestellen.

Daten: Transitous (MOTIS), gleiche Quelle wie die Abfahrtstafel. Nur Standardbibliothek.
Start:   python3 abfahrtsbot.py
Test:    python3 abfahrtsbot.py --selftest "Berlin Hauptbahnhof"
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
data = {"owner": None, "chats": {}, "state": {}, "pending": {}, "menus": {}, "deps": {}, "guests": [], "shares": {}, "members": [], "verwalter": [], "invited_by": {}, "msglog": {}, "invites": {}, "names": {}, "sessions": {}}
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


AUTOCLEAR_HOURS = 47               # Telegram erlaubt Bots nur Nachrichten der letzten 48 Stunden zu loeschen
AUTOCLEAR_MAX = 5000               # so viele Nachrichten merkt sich der Bot hoechstens pro Chat
msg_log = {}                       # Chat -> [[Nachrichten-ID, Zeitpunkt], ...], nur fuer Chats mit Auto-Loeschen
msg_lock = threading.Lock()        # eigener Lock, weil send() auch unter dem Haupt-Lock aufgerufen wird
msg_dirty = False


AUTOCLEAR_MIN_HOURS = 3            # kuerzer ist nicht erlaubt, das hoechste ist AUTOCLEAR_HOURS (Telegram-Grenze)
REMIND_AT = (15, 5)                # Standard: Erinnerung so viele Minuten vor Abfahrt
REMIND_MIN, REMIND_MAX = 1, 120    # erlaubter Bereich je Erinnerung in Minuten
NICK_MAX = 30


def autoclear_on(cid):
    chat = data["chats"].get(str(cid))
    return bool(chat and chat.get("autoclear"))


# ---------- Persoenliche Einstellungen (Website und Telegram nutzen dieselben Werte in data["chats"][cid]) ----------

def clear_hours_value(chat):
    h = (chat or {}).get("autoclear_h")
    return h if isinstance(h, int) and not isinstance(h, bool) and AUTOCLEAR_MIN_HOURS <= h <= AUTOCLEAR_HOURS else AUTOCLEAR_HOURS


def clear_hours(cid):
    """Nach so vielen Stunden werden Nachrichten dieses Chats geloescht (Standard 47)."""
    return clear_hours_value(data["chats"].get(str(cid)))


def remind_pair(chat):
    """(erste, zweite) Erinnerung in Minuten vor Abfahrt, None = aus. Ohne eigene Werte gilt der Standard 15 und 5."""
    r = (chat or {}).get("remind")
    if isinstance(r, list) and len(r) == 2:
        try:
            return check_remind(r[0], r[1])
        except ValueError:
            pass
    return REMIND_AT


def active_remind(chat):
    return [t for t in remind_pair(chat) if t]


def nick_of(cid):
    return (data["chats"].get(str(cid)) or {}).get("nick") or ""


def parse_number(v, einheit):
    """Zahl aus Zahl oder Text wie '12', '12 h', '30 min'. Alles andere ist ein Fehler."""
    if isinstance(v, bool):
        raise ValueError("Bitte eine Zahl angeben.")
    if isinstance(v, int):
        return v
    m = re.fullmatch(r"\s*(\d{1,4})\s*(" + einheit + r")?\s*", str(v or ""), re.I)
    if not m:
        raise ValueError("Bitte eine ganze Zahl angeben.")
    return int(m.group(1))


def check_hours(v):
    h = parse_number(v, "h|std|stunden")
    if h < AUTOCLEAR_MIN_HOURS:
        raise ValueError(f"Mindestens {AUTOCLEAR_MIN_HOURS} Stunden.")
    if h > AUTOCLEAR_HOURS:
        raise ValueError(f"Höchstens {AUTOCLEAR_HOURS} Stunden, denn Telegram erlaubt dem Bot nur Nachrichten der letzten 48 Stunden zu löschen.")
    return h


def check_minutes(v):
    """Eine Erinnerung: Minuten vor Abfahrt, oder None (aus)."""
    if v is None or (isinstance(v, str) and v.strip().lower() in ("aus", "off", "")):
        return None
    m = parse_number(v, "min|minuten")
    if not REMIND_MIN <= m <= REMIND_MAX:
        raise ValueError(f"Die Erinnerung muss zwischen {REMIND_MIN} und {REMIND_MAX} Minuten vor der Abfahrt liegen.")
    return m


def check_remind(first, second):
    a, b = check_minutes(first), check_minutes(second)
    if a and b and a <= b:
        raise ValueError("Die erste Erinnerung muss früher kommen als die zweite, also mehr Minuten vor der Abfahrt haben, und die beiden Zeiten müssen verschieden sein.")
    return a, b


def check_nick(v):
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ValueError("Der Name muss Text sein.")
    v = " ".join(v.split())
    if len(v) > NICK_MAX:
        raise ValueError(f"Der Name darf höchstens {NICK_MAX} Zeichen lang sein.")
    if any(ord(c) < 32 or ord(c) == 127 for c in v):
        raise ValueError("Der Name enthält ungültige Zeichen.")
    return v


def settings_view(cid):
    """Alle persoenlichen Einstellungen einer Person (Aufrufer haelt den Lock)."""
    chat = get_chat(cid)
    a, b = remind_pair(chat)
    return {"nick": chat.get("nick") or "",
            "autoclear": {"on": bool(chat.get("autoclear")), "hours": clear_hours(cid),
                          "min": AUTOCLEAR_MIN_HOURS, "max": AUTOCLEAR_HOURS, "default": AUTOCLEAR_HOURS},
            "remind": {"first": a, "second": b, "min": REMIND_MIN, "max": REMIND_MAX, "default": list(REMIND_AT)}}


def resync_reminders(cid, chat):
    """Nach geaenderten Vorwarnzeiten: bereits erreichte Zeitpunkte gelten als erledigt, damit nichts nachtraeglich
    oder doppelt gesendet wird. Noch nicht erreichte werden mit den neuen Werten ganz normal ausgeloest."""
    state = data["state"].get(str(cid), {})
    active = active_remind(chat)
    now = time.time()
    for w in chat.get("trips", []):
        st = state.get("w_" + str(w.get("wid")))
        if st and st.get("eta"):
            mins = (st["eta"] - now) / 60
            st["rem"] = [t for t in active if mins <= t + REMIND_TOLERANCE]


def apply_settings(cid, ch):
    """Einstellungen ueberpruefen und speichern. ch darf diese Schluessel enthalten: nick, autoclear_on, autoclear_hours,
    remind_first, remind_second. Wirft ValueError mit verstaendlicher Meldung. Es wird erst gespeichert, wenn alles stimmt."""
    global msg_dirty
    with lock:
        chat = get_chat(cid)
        new = {}
        if "nick" in ch:
            new["nick"] = check_nick(ch["nick"])
        if "autoclear_hours" in ch:
            new["autoclear_h"] = check_hours(ch["autoclear_hours"])
        if "autoclear_on" in ch:
            if not isinstance(ch["autoclear_on"], bool):
                raise ValueError("Ungültige Eingabe.")
            new["autoclear"] = ch["autoclear_on"]
        a, b = remind_pair(chat)
        if "remind_first" in ch or "remind_second" in ch:
            a, b = check_remind(ch["remind_first"] if "remind_first" in ch else a,
                                ch["remind_second"] if "remind_second" in ch else b)
            new["remind"] = [a, b]
        chat.update(new)
        if "remind" in new:
            resync_reminders(cid, chat)
        save()
        out = settings_view(cid)
    if new.get("autoclear") is False:
        with msg_lock:
            msg_log.pop(cid, None)
            msg_dirty = True
    return out


def log_message(cid, mid):
    """Nachricht fuer das spaetere automatische Loeschen merken (nur wenn der Chat es eingeschaltet hat)."""
    global msg_dirty
    if not isinstance(mid, int) or not autoclear_on(cid):
        return
    with msg_lock:
        eintraege = msg_log.setdefault(cid, [])
        eintraege.append([mid, int(time.time())])
        del eintraege[:-AUTOCLEAR_MAX]
        msg_dirty = True


def send(chat_id, text, **extra):
    try:
        res = tg("sendMessage", chat_id=chat_id, text=text, **extra)
        log_message(chat_id, (res or {}).get("message_id"))
        return res
    except Exception as e:
        log("sendMessage fehlgeschlagen:", e)


# ---------- Fahrplandaten ----------

def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def hhmm(dt):
    return (dt.astimezone(TZ) if TZ else dt.astimezone()).strftime("%H:%M")


def vehicle(mode):
    return "bus" if mode == "BUS" else "bahn"


def clean_name(n):
    """Haltestellennamen bereinigen: manche Quellen liefern Rohnamen wie 'Praha,,Hlavní nádraží' oder 'Ostrava,hlavní nádraží'."""
    n = re.sub(r"\s*,\s*,\s*", ", ", str(n or ""))
    return re.sub(r"\s{2,}", " ", re.sub(r"\s*,\s*", ", ", n)).strip()


def search_stops(text):
    q = urllib.parse.urlencode({"text": text, "type": "STOP"})
    seen, out = set(), []
    for s in http_json(f"{MOTIS}/geocode?{q}"):
        name = clean_name(s["name"])
        area = next((a["name"] for a in s.get("areas", []) if a.get("default")), "")
        if s.get("type") == "STOP" and (name, area) not in seen:      # gleiche Namen in verschiedenen Orten bleiben getrennt
            seen.add((name, area))
            ort = re.sub(r"^(SO|obvod)\s+", "", area, flags=re.I)
            out.append({"id": s["id"], "name": name + (f" ({ort})" if ort and ort.lower() not in name.lower() else "")})
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
            "dest": clean_name(st.get("headsign", "")),
            "stop": clean_name(p["name"]),
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


REMIND_TOLERANCE = 2 / 60  # 2 Sekunden Toleranz: lieber knapp zu frueh als zu spaet
REMIND_LEAD = 1.0          # so viele Sekunden vor dem Zeitpunkt wird abgefragt (Abfrage und Versand brauchen Zeit)


def decide_trip(prev, d, now, remind_at=REMIND_AT):
    """Einzeln beobachtete Fahrt. Gibt (Ereignis, alter Wert, neuer Zustand) zurueck.

    Ereignisse: cancel (faellt aus), remind (je einmal zu den Zeitpunkten remind_at, Standard 15 und 5 min vor Abfahrt),
    change (Verspaetung hat sich um >= 2 min gegenueber der letzten Meldung geaendert).
    st["greet"] ist wahr, wenn die erste Erinnerung dabei ist (dann darf der Bot den Namen verwenden).
    """
    # Standardwerte zuerst, damit auch Zustaende aus aelteren Bot-Versionen funktionieren
    st = {"nd": d["delay"], "cancelled": False, "rem": [], **(prev or {})}
    st["greet"] = False
    if prev and "rem" not in prev:
        st["rem"] = list(remind_at) if prev.get("reminded") else []
    st["t"] = d["planned"].timestamp()
    st["eta"] = d["actual"].timestamp()            # voraussichtliche Abfahrt, daraus werden die Weckzeiten berechnet
    old = st["nd"]
    if d["cancelled"]:
        event = None if st["cancelled"] else "cancel"
        st["cancelled"] = True
        return event, old, st
    st["cancelled"] = False
    mins = (d["actual"] - now).total_seconds() / 60
    # nach Abfahrtsbeginn (mins <= 0) wird nichts mehr erinnert, auch wenn eine Pruefung verspaetet kommt
    due = [t for t in remind_at if 0 < mins <= t + REMIND_TOLERANCE and t not in st["rem"]]
    if due:
        st["rem"] = sorted(set(st["rem"]) | set(due))      # alle bereits erreichten Zeitpunkte als erledigt merken
        st["nd"] = d["delay"]
        st["greet"] = bool(remind_at) and remind_at[0] in due
        return "remind", old, st
    if abs(d["delay"] - st["nd"]) >= 2:
        st["nd"] = d["delay"]
        return "change", old, st
    return None, old, st


def fmt_delay(delay):
    return f"+{delay} min" if delay >= 1 else f"{delay} min" if delay <= -1 else "p\u00fcnktlich"


def fmt_trip(event, d, old=None, nick=""):
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
        "remind": f"\u23f0 Hallo {nick}, deine Fahrt beginnt in {mins} min" if nick else f"\u23f0 Abfahrt in {mins} min",
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
    event, old, state[skey] = decide_trip(state.get(skey), d, now, active_remind(chat))
    if event:
        nick = chat.get("nick") if state[skey].get("greet") else ""       # den Namen nur bei der ersten Erinnerung nutzen
        outbox.append((cid, fmt_trip(event, d, old, nick or "")))
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
                for t in active_remind(chat):
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


def migrate():
    """Beim Start ausfuehren, wiederholbar. Alte Daten bleiben unveraendert: Fehlt eine persoenliche Einstellung, gilt der
    bisherige Standard (47 Stunden, Erinnerungen 15 und 5 Minuten, kein Name). Ungueltige gespeicherte Werte werden entfernt."""
    data.setdefault("invite_hist", [])
    data.pop("inviters", None)                   # frueheres Einladerecht: gibt es nicht mehr
    data.setdefault("verwalter", [])
    data.setdefault("invited_by", {})
    for alt in data.pop("admins", []):           # die bisherige Rolle Admin heisst jetzt Verwalter
        if alt not in data["verwalter"]:
            data["verwalter"].append(alt)
    data["verwalter"] = [m for m in data["verwalter"] if m in data["members"] and m != data["owner"]]
    for chat in data["chats"].values():
        if chat.get("autoclear_h") is not None and chat["autoclear_h"] != clear_hours_value(chat):
            del chat["autoclear_h"]
        if "remind" in chat and remind_pair(chat) == REMIND_AT and chat["remind"] != list(REMIND_AT):
            del chat["remind"]
        if "nick" in chat:
            try:
                chat["nick"] = check_nick(chat["nick"])
            except ValueError:
                del chat["nick"]


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
    "/mitglieder \u2013 eingeladene Personen und Einladungen verwalten\n"
    "/jetzt \u2013 aktuelle Versp\u00e4tungen an deinen Favoriten\n"
    "/pause und /weiter \u2013 Benachrichtigungen aus- und einschalten\n"
    "/clear \u2013 Chatverlauf l\u00f6schen (nur Nachrichten der letzten 48 Stunden)\n"
    "/autoclear \u2013 Nachrichten automatisch l\u00f6schen, an oder aus\n"
    "/einstellungen \u2013 Name, automatisches L\u00f6schen und Erinnerungszeiten einstellen\n"
    "/menue \u2013 Tasten-Men\u00fc anzeigen (Taste Ausblenden blendet es aus)\n"
    "/restart \u2013 Bot neu starten (nur Besitzer)\n"
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
        return send(cid, "Nutzung: /suche Berlin Hauptbahnhof")
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
        awaiting[cid] = ("/abfahrten", time.time())            # die naechste Nachricht ist der Name der Haltestelle
        with lock:
            favs = copy.deepcopy(get_chat(cid)["favs"])
        if not favs:
            return send(cid, "Wie heißt die Haltestelle? Schreib den Namen, zum Beispiel: Berlin Hauptbahnhof")
        kb = [[{"text": f["name"], "callback_data": "dep:" + f["sid"]}] for f in favs]
        return send(cid, "Von welcher Haltestelle? Tippe einen Favoriten an oder schreib den Namen.",
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


GUEST_COMMANDS = {"/start", "/hilfe", "/help", "/code", "/tracking", "/menue", "/menueaus"}
GUEST_ACTIONS = {"tw", "tr", "ud", "sr", "sf"}              # Gaeste: nur Verbindungen ansehen, verfolgen und das Verfolgen beenden
GUEST_HELP = (
    "Abfahrts-Bot \u2013 du hast Zugriff auf geteilte Verbindungen.\n\n"
    "/code CODE \u2013 geteilte Verbindung ansehen und verfolgen\n"
    "/tracking \u2013 deine verfolgten Fahrten\n"
    "/menue \u2013 Tasten-Men\u00fc anzeigen (Taste Ausblenden blendet es aus)"
)


ADMIN_ACTIONS = {"mrm", "mr", "md", "mo", "mx", "mn", "mm"}  # Mitgliederverwaltung: nur Besitzer und Verwalter
MAX_OPEN_INVITES = 5                                      # offene Einladungscodes pro Verwalter
OWNER_COMMANDS = {"/mitglieder"}                          # Verwaltung: Besitzer und Verwalter
BETRIEB_COMMANDS = {"/restart"}                           # Betrieb: nur der Besitzer, sonst niemand
BETRIEB_ACTIONS = {"rst"}                                 # Knoepfe dazu (Bestaetigung)
RESTART_ASK_TTL = 120                                     # so lange gilt die Rueckfrage "Bot neu starten?"
restart_pending = {}                                      # Chat -> Zeitpunkt der Rueckfrage, nur im Speicher
poll_offset = 0                                           # letzter bestaetigter Telegram-Update-Stand
INVITE_TTL = 24 * 3600                                    # Einladungscode gilt 24 h


# REGELN FUER SPAETERE AENDERUNGEN (siehe auch CLAUDE.md und bot/README.md)
# Rollen von oben nach unten: Besitzer (genau einer), Verwalter, Mitglied, Gast. Jede Rolle darf nur Rollen UNTER sich aendern.
# 1. Der Besitzer wird nur ueber BOT_OWNER_ID (Umgebung) oder "owner_id" (config.json) bestimmt, sonst ist es die erste Person mit
#    /start. Kein Befehl, Knopf oder API-Aufruf macht jemanden zum Besitzer, ersetzt ihn oder entfernt ihn.
# 2. Verwalter ernennt und entlaesst nur der Besitzer. Ein Verwalter kann den Besitzer und andere Verwalter nicht entfernen,
#    nicht aendern und ihre Einladungen nicht widerrufen. Er darf einladen und Mitglieder und Gaeste verwalten.
# 3. Betrieb (Server, config.json, Umgebung, Token, Ports, Dateien, Dienste, Neustart, Logs) gehoert dem Besitzer. Die einzige
#    Betriebsfunktion im Bot ist /restart (BETRIEB_COMMANDS), und die darf nur der Besitzer. Verwalter, Mitglieder und Gaeste nie.
# 4. Mitglieder koennen den Bot nutzen, sonst nichts. Gaeste koennen nur per /code erhaltene Verbindungen ansehen und verfolgen.
# 5. Token, Besitzer-ID und Dateipfade erscheinen nie in Nachrichten, Fehlermeldungen oder API-Antworten.
# 6. Der Betreiber behaelt per Server immer die Kontrolle (data.json und config.json bearbeiten, Dienst neu starten).
# 7. Alle Pruefungen laufen serverseitig. Rechte immer ueber may_use, is_manager oder rang pruefen, nie aus Callback-Daten.
def is_owner(cid):
    return data["owner"] == cid


def ist_besitzer(cid):
    """Der eine Besitzer. Fuer Schutz, Anzeige und Betrieb (/restart). Sonst Rechte ueber is_manager, rang oder may_use pruefen."""
    return is_owner(cid)


def is_verwalter(cid):
    return cid in data["verwalter"] and cid in data["members"] and not ist_besitzer(cid)


def is_manager(cid):
    """Besitzer oder Verwalter: darf einladen, Einladungen widerrufen und Rollen unter sich verwalten."""
    return ist_besitzer(cid) or is_verwalter(cid)


def rang(cid):
    """4 Besitzer, 3 Verwalter, 2 Mitglied, 1 Gast, 0 keine Rolle. Aendern darf man nur, wer einen hoeheren Rang hat."""
    return 4 if ist_besitzer(cid) else 3 if is_verwalter(cid) else 2 if cid in data["members"] else 1 if cid in data["guests"] else 0


def can_invite(cid):
    return is_manager(cid)


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
    """Besitzer, Verwalter und Mitglieder: voller Funktionsumfang."""
    return ist_besitzer(cid) or cid in data["members"]


def may_use(cid, cmd=None, action=None):
    """Besitzer darf alles. Verwalter alles ausser dem Betrieb (/restart). Mitglieder alles ausser Verwaltung und Betrieb.
    Gaeste nur Verbindungen ansehen und verfolgen."""
    if ist_besitzer(cid):
        return True
    if cmd in BETRIEB_COMMANDS or action in BETRIEB_ACTIONS:
        return False
    if is_verwalter(cid):
        return True
    if cid in data["members"]:
        return cmd not in OWNER_COMMANDS if cmd else action not in ADMIN_ACTIONS
    if cid in data["guests"]:
        return cmd in GUEST_COMMANDS if cmd else action in GUEST_ACTIONS
    return False


# Tasten unter dem Eingabefeld. Beschriftung -> Befehl. Jede Taste laeuft durch dieselbe Rechtepruefung wie der Befehl.
MENU_BUTTONS = {
    "⭐ Favoriten": "/favoriten", "🔍 Suche": "/suche", "🚏 Abfahrten": "/abfahrten", "⏱ Verspätungen": "/jetzt",
    "📌 Verfolgte Fahrten": "/tracking", "🔁 Wiederkehrend": "/wiederkehrend", "🔕 Meldungen aus": "/pause",
    "🔔 Meldungen an": "/weiter", "⚙️ Status": "/status", "🛠 Einstellungen": "/einstellungen", "🧹 Chat löschen": "/clear", "🕒 Auto-Löschen": "/autoclear", "👥 Mitglieder": "/mitglieder", "🔄 Neustart": "/restart",
    "🔑 Einladen": "/code", "🎟 Code eingeben": "/code", "❓ Hilfe": "/hilfe", "🙈 Ausblenden": "/menueaus",
}
# Tasten, nach denen der Bot erst noch einen Text braucht (Name der Haltestelle, Code)
MENU_ASK = {
    "🔍 Suche": ("/suche", "Wie heißt die Haltestelle? Schreib den Namen, zum Beispiel: Berlin Hauptbahnhof"),
    "🎟 Code eingeben": ("/code", "Schick mir jetzt den Code, zum Beispiel: ABC234"),
}
ASK_TTL = 300                      # so lange wartet der Bot auf die Antwort nach einer Taste
awaiting = {}                      # Chat -> (Befehl, Zeitpunkt), nur im Speicher


def menu_keyboard(cid):
    """Tasten je nach Rolle. Es erscheinen nur Tasten, die die Person auch nutzen darf."""
    if is_full(cid):
        rows = [["⭐ Favoriten", "🔍 Suche"], ["🚏 Abfahrten", "⏱ Verspätungen"],
                ["📌 Verfolgte Fahrten", "🔁 Wiederkehrend"], ["🔕 Meldungen aus", "🔔 Meldungen an"],
                ["⚙️ Status", "🛠 Einstellungen"], ["🕒 Auto-Löschen", "🧹 Chat löschen"], ["🎟 Code eingeben"]]
        extra = (["🔑 Einladen"] if can_invite(cid) else []) + (["👥 Mitglieder"] if is_manager(cid) else []) + (["🔄 Neustart"] if ist_besitzer(cid) else [])
        if extra:
            rows.append(extra)
        rows.append(["❓ Hilfe", "🙈 Ausblenden"])
    elif cid in data["guests"]:
        rows = [["🎟 Code eingeben", "📌 Verfolgte Fahrten"], ["❓ Hilfe", "🙈 Ausblenden"]]
    else:
        rows = [["🎟 Code eingeben"]]
    return {"keyboard": [[{"text": t} for t in row] for row in rows], "resize_keyboard": True}


def cmd_menue(cid):
    send(cid, "Hier ist das Menü. Mit dem Tastatur-Symbol im Eingabefeld klappst du es ein und aus.", reply_markup=menu_keyboard(cid))


def cmd_menue_aus(cid):
    send(cid, "Menü ausgeblendet. Mit /menue holst du es zurück.", reply_markup={"remove_keyboard": True})


def help_text(cid):
    """Hilfe nur mit den Befehlen, die die Person auch nutzen darf."""
    if not is_full(cid):
        return GUEST_HELP
    return "\n".join(z for z in HELP.split("\n") if not z.startswith("/") or may_use(cid, cmd=z.split(" ")[0]))


def cmd_help(cid):
    send(cid, help_text(cid), reply_markup=menu_keyboard(cid))


CLEAR_BATCH = 100                  # deleteMessages nimmt höchstens 100 Nachrichten auf einmal
CLEAR_MAX_BATCHES = 20             # also bis zu 2000 Nachrichten pro Aufruf


def cmd_clear(cid, mid):
    """Chatverlauf loeschen. Telegram erlaubt Bots nur Nachrichten der letzten 48 Stunden, aeltere bleiben stehen.
    Favoriten, Tracking und Einstellungen bleiben erhalten."""
    if not isinstance(mid, int) or mid < 1:
        return
    if not rate_ok(str(cid), "clear", 3, 60):
        return send(cid, "Bitte warte einen Moment, bevor du den Chat erneut löschst.")
    bis = mid
    for _ in range(CLEAR_MAX_BATCHES):
        ids = list(range(bis, max(bis - CLEAR_BATCH, 0), -1))
        if not ids:
            break
        try:
            tg("deleteMessages", chat_id=cid, message_ids=ids)   # nicht vorhandene oder zu alte Nachrichten werden uebersprungen
        except Exception as e:
            log("Chat löschen fehlgeschlagen:", e)
            break
        bis -= CLEAR_BATCH
        time.sleep(0.3)
    with msg_lock:
        msg_log.pop(cid, None)
    send(cid, "Chatverlauf gelöscht. Nachrichten, die älter als 48 Stunden sind, kann Telegram nicht entfernen. "
              "Deine Favoriten und verfolgten Fahrten bleiben erhalten.")


def autoclear_text(cid):
    if autoclear_on(cid):
        return (f"Auto-Löschen ist AN: Nachrichten in diesem Chat werden nach {clear_hours(cid)} Stunden gelöscht "
                "(Telegram erlaubt höchstens 48). Es gilt für Nachrichten ab dem Einschalten.")
    return (f"Auto-Löschen ist AUS. Wenn du es einschaltest, löscht der Bot Nachrichten in diesem Chat nach {clear_hours(cid)} Stunden "
            "(Telegram erlaubt höchstens 48).")


def autoclear_keyboard(cid):
    an = autoclear_on(cid)
    return {"inline_keyboard": [[{"text": "Ausschalten" if an else "Einschalten", "callback_data": "ac:" + ("0" if an else "1")}]]}


def set_autoclear(cid, an):
    global msg_dirty
    with lock:
        get_chat(cid)["autoclear"] = an
        save()
    if not an:
        with msg_lock:
            msg_log.pop(cid, None)
            msg_dirty = True


def cmd_autoclear(cid, arg=""):
    arg = arg.strip().lower()
    if arg in ("an", "aus"):
        set_autoclear(cid, arg == "an")
    send(cid, autoclear_text(cid), reply_markup=autoclear_keyboard(cid))


def handle_autoclear_callback(cb, cid, mid, wert):
    with lock:
        if not may_use(cid, action="ac"):
            return
    set_autoclear(cid, wert == "1")
    try:
        tg("answerCallbackQuery", callback_query_id=cb["id"], text="Gespeichert")
        tg("editMessageText", chat_id=cid, message_id=mid, text=autoclear_text(cid), reply_markup=autoclear_keyboard(cid))
    except Exception as e:
        log("Callback-Antwort fehlgeschlagen:", e)


# ---------- Einstellungen (/einstellungen) ----------
# Dieselben Werte wie auf der Website (data["chats"][cid]). Knoepfe: st:<Ansicht oder Aktion>:<Wert>.
# Eingaben per Text laufen ueber awaiting (wie bei der Suche) und werden wie alles andere zentral geprueft.

SETTING_INPUTS = {"/_name", "/_clearh", "/_r1", "/_r2"}
SETTING_KEYS = {"/_name": "nick", "/_clearh": "autoclear_hours", "/_r1": "remind_first", "/_r2": "remind_second"}
SETTING_VIEW = {"/_name": "p", "/_clearh": "c", "/_r1": "r", "/_r2": "r"}
CLEAR_PRESETS = (3, 5, 12, 24, 47)
REMIND_PRESETS = (60, 30, 20, 15, 10, 5, 3, 2, 1)


def remind_label(m):
    return f"{m} min vor Abfahrt" if m else "aus"


def chunk(items, n):
    return [items[i:i + n] for i in range(0, len(items), n)]


def st_view(cid, view):
    """Text und Knoepfe einer Ansicht des Einstellungsmenues."""
    with lock:
        v = settings_view(cid)
    nick = v["nick"] or "nicht festgelegt"
    ac, rm = v["autoclear"], v["remind"]
    back = [{"text": "⬅️ Zurück", "callback_data": "st:m"}]
    if view == "p":
        rows = [[{"text": "✏️ Name festlegen oder ändern", "callback_data": "st:pn"}]]
        if v["nick"]:
            rows.append([{"text": "🗑 Name entfernen", "callback_data": "st:px"}])
        return (f"👤 Mein Profil\n\nName: {nick}\n\nMit diesem Namen spricht dich der Bot in Erinnerungen an. "
                "Er hat nichts mit deinem Telegram-Namen zu tun und wird nicht für Rechte oder Anmeldung genutzt."), {"inline_keyboard": rows + [back]}
    if view == "c":
        an = "AN" if ac["on"] else "AUS"
        rows = [[{"text": "Ausschalten" if ac["on"] else "Einschalten", "callback_data": "st:co:" + ("0" if ac["on"] else "1")}]]
        rows += chunk([{"text": ("✅ " if h == ac["hours"] else "") + f"{h} h", "callback_data": f"st:ch:{h}"} for h in CLEAR_PRESETS], 3)
        rows.append([{"text": "✏️ Eigene Stundenzahl", "callback_data": "st:cc"}])
        return (f"🕒 Automatische Löschung\n\nStatus: {an}\nLöschen nach: {ac['hours']} Stunden (möglich: {ac['min']} bis {ac['max']})\n\n"
                "Betroffen sind Nachrichten in diesem Chat, die der Bot sich ab dem Einschalten gemerkt hat: seine eigenen und deine. "
                "Favoriten, verfolgte Fahrten und Einstellungen bleiben erhalten. "
                f"Telegram erlaubt dem Bot nur Nachrichten der letzten 48 Stunden zu löschen, darum sind höchstens {ac['max']} Stunden möglich."), {"inline_keyboard": rows + [back]}
    if view in ("r1", "r2"):
        num = 1 if view == "r1" else 2
        aktuell = rm["first"] if num == 1 else rm["second"]
        rows = chunk([{"text": ("✅ " if t == aktuell else "") + f"{t} min", "callback_data": f"st:s{num}:{t}"} for t in REMIND_PRESETS], 3)
        rows.append([{"text": "✏️ Eigene Minuten", "callback_data": f"st:s{num}c"}, {"text": "🔕 Ausschalten", "callback_data": f"st:s{num}:off"}])
        return (f"⏰ {num}. Erinnerung\n\nJetzt: {remind_label(aktuell)}\n\nWie viele Minuten vor der Abfahrt soll ich dich erinnern? "
                f"Erlaubt sind {rm['min']} bis {rm['max']}. Die erste Erinnerung muss früher kommen als die zweite."), {"inline_keyboard": rows + [[{"text": "⬅️ Zurück", "callback_data": "st:r"}]]}
    if view == "r":
        rows = [[{"text": "1. Erinnerung ändern", "callback_data": "st:r1"}], [{"text": "2. Erinnerung ändern", "callback_data": "st:r2"}]]
        return (f"⏰ Fahrtbenachrichtigungen\n\n1. Erinnerung: {remind_label(rm['first'])}\n2. Erinnerung: {remind_label(rm['second'])}\n\n"
                f"Gilt für alle verfolgten Fahrten. Standard ist {REMIND_AT[0]} und {REMIND_AT[1]} Minuten vorher."), {"inline_keyboard": rows + [back]}
    erinn = " und ".join(f"{t} min" for t in (rm["first"], rm["second"]) if t) or "aus"
    rows = [[{"text": "👤 Mein Profil", "callback_data": "st:p"}], [{"text": "🕒 Automatische Löschung", "callback_data": "st:c"}],
            [{"text": "⏰ Fahrtbenachrichtigungen", "callback_data": "st:r"}], [{"text": "🏠 Zurück zum Hauptmenü", "callback_data": "st:h"}]]
    return (f"⚙️ Einstellungen\n\n👤 Name: {nick}\n🕒 Auto-Löschen: " + (f"an, nach {ac['hours']} Stunden" if ac["on"] else "aus") +
            f"\n⏰ Erinnerungen: {erinn}"), {"inline_keyboard": rows}


def cmd_einstellungen(cid):
    text, kb = st_view(cid, "m")
    send(cid, text, reply_markup=kb)


def ask_setting(cid, ask_cmd, frage):
    awaiting[cid] = (ask_cmd, time.time())
    send(cid, frage, reply_markup={"inline_keyboard": [[{"text": "Abbrechen", "callback_data": "st:x"}]]})


ASK_TEXT = {"/_name": "Wie soll ich dich nennen? Schreib den Namen, höchstens %d Zeichen." % NICK_MAX,
            "/_clearh": "Nach wie vielen Stunden sollen Nachrichten gelöscht werden? Schreib eine Zahl von %d bis %d." % (AUTOCLEAR_MIN_HOURS, AUTOCLEAR_HOURS),
            "/_r1": "Wie viele Minuten vor der Abfahrt soll die erste Erinnerung kommen? Schreib eine Zahl, oder „aus“.",
            "/_r2": "Wie viele Minuten vor der Abfahrt soll die zweite Erinnerung kommen? Schreib eine Zahl, oder „aus“."}


def handle_settings_callback(cb, cid, mid, rest):
    with lock:
        if not may_use(cid, action="st"):
            return
    cmd, _, arg = rest.partition(":")
    answer, view, ask = "", "m", None
    try:
        if cmd in ("m", "p", "c", "r", "r1", "r2"):
            view = cmd
        elif cmd == "h":
            answer_cb(cb)
            return cmd_menue(cid)
        elif cmd == "x":
            awaiting.pop(cid, None)                          # Eingabe abbrechen
            answer = "Abgebrochen"
        elif cmd == "pn":
            view, ask = "p", "/_name"
        elif cmd == "px":
            apply_settings(cid, {"nick": None})
            view, answer = "p", "Name entfernt"
        elif cmd == "co":
            apply_settings(cid, {"autoclear_on": arg == "1"})
            view, answer = "c", "Gespeichert"
        elif cmd == "ch":
            apply_settings(cid, {"autoclear_hours": arg})
            view, answer = "c", "Gespeichert"
        elif cmd == "cc":
            view, ask = "c", "/_clearh"
        elif cmd in ("s1", "s2"):
            apply_settings(cid, {"remind_first" if cmd == "s1" else "remind_second": arg})
            view, answer = "r", "Gespeichert"
        elif cmd in ("s1c", "s2c"):
            view, ask = "r", "/_r1" if cmd == "s1c" else "/_r2"
    except ValueError as e:
        answer_cb(cb, str(e), alert=True)                    # ungueltiger Wert: nichts wurde gespeichert, Ansicht bleibt
        return
    answer_cb(cb, answer)
    text, kb = st_view(cid, view)
    edit_or_log(cid, mid, text, kb)
    if ask:
        ask_setting(cid, ask, ASK_TEXT[ask])


def handle_setting_input(cid, ask_cmd, text, fresh):
    """Antwort auf eine Frage des Einstellungsmenues. Ungueltiges wird erklaert, die Frage bleibt dann offen."""
    with lock:
        erlaubt = may_use(cid, action="st")
    if not erlaubt:
        return
    if not fresh:
        return send(cid, "Die Eingabe ist abgelaufen. /einstellungen öffnet das Menü erneut.")
    try:
        apply_settings(cid, {SETTING_KEYS[ask_cmd]: text})
    except ValueError as e:
        return ask_setting(cid, ask_cmd, f"{e}\nBitte noch einmal eingeben oder abbrechen.")
    view, kb = st_view(cid, SETTING_VIEW[ask_cmd])
    send(cid, "✅ Gespeichert.\n\n" + view, reply_markup=kb)


# ---------- Neustart (/restart) ----------
# Einzige Betriebsfunktion ausser dem Verwalter: Besitzer und Verwalter duerfen den Bot neu starten lassen.
# Der Bot beendet sich, Docker (restart: unless-stopped) oder systemd (Restart=always) starten ihn wieder.

def cmd_restart(cid):
    with lock:
        if not may_use(cid, cmd="/restart"):
            return
        restart_pending[cid] = time.time()
    send(cid, "\U0001f504 Bot neu starten?\n\nEr ist danach etwa eine halbe Minute nicht erreichbar. Favoriten, verfolgte Fahrten und Einstellungen bleiben.",
         reply_markup={"inline_keyboard": [[{"text": "✅ Ja, neu starten", "callback_data": "rst:1"}, {"text": "Abbrechen", "callback_data": "rst:0"}]]})


def restart_now():
    """Daten sichern, offene Telegram-Updates bestaetigen (sonst kaeme dieselbe Taste nach dem Start noch einmal) und beenden."""
    try:
        with msg_lock:
            stand = {str(c): [list(e) for e in l] for c, l in msg_log.items()}
        with lock:
            data["msglog"] = stand
            save()
        if poll_offset:
            tg("getUpdates", offset=poll_offset, timeout=0)
    except Exception as e:
        log("Vor dem Neustart:", e)
    log("Neustart auf Wunsch")
    os._exit(0)


def schedule_restart():
    threading.Timer(1.5, restart_now).start()               # kurz warten, damit die Antwort an Telegram noch rausgeht


def handle_restart_callback(cb, cid, mid, wert):
    with lock:
        if not may_use(cid, action="rst"):                   # serverseitig, nicht nur wegen des Knopfes
            return
        seit = restart_pending.pop(cid, 0)
        los = wert == "1" and time.time() - seit < RESTART_ASK_TTL
    if los:
        answer_cb(cb, "Neustart")
        edit_or_log(cid, mid, "\U0001f504 Der Bot startet neu und ist gleich wieder da.")
        return schedule_restart()
    answer_cb(cb, "Abgebrochen" if wert != "1" else "Abgelaufen")
    edit_or_log(cid, mid, "Neustart abgebrochen." if wert != "1" else "Die Rückfrage ist abgelaufen. /restart fragt neu.")


def autoclear_step():
    """Nachrichten loeschen, die aelter als die Einstellung des Chats sind (Standard 47 Stunden), und das Protokoll sichern.
    Die Grenze wird bei jedem Durchlauf neu gelesen, eine geaenderte Einstellung gilt deshalb sofort fuer alle gemerkten Nachrichten."""
    global msg_dirty
    jetzt = time.time()
    faellig = {}
    with msg_lock:
        for cid, eintraege in list(msg_log.items()):
            if not autoclear_on(cid):
                del msg_log[cid]
                msg_dirty = True
                continue
            grenze = clear_hours(cid) * 3600
            alt = [mid for mid, ts in eintraege if jetzt - ts >= grenze]
            if alt:
                faellig[cid] = alt
                msg_log[cid] = [e for e in eintraege if jetzt - e[1] < grenze]
                msg_dirty = True
    for cid, ids in faellig.items():
        for i in range(0, len(ids), CLEAR_BATCH):
            try:
                tg("deleteMessages", chat_id=cid, message_ids=ids[i:i + CLEAR_BATCH])
            except Exception as e:
                log("Auto-Löschen fehlgeschlagen:", e)
                break
            time.sleep(0.3)
    if msg_dirty:
        with msg_lock:
            stand = {str(c): [list(e) for e in l] for c, l in msg_log.items()}
            msg_dirty = False
        with lock:
            data["msglog"] = stand
            save()


def autoclear_loop():
    while True:
        try:
            autoclear_step()
        except Exception as e:
            log("Fehler beim Auto-Löschen:", e)
        time.sleep(300)


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
    log_message(cid, m.get("message_id"))
    text = (m.get("text") or "").strip()
    if text.startswith("/"):
        awaiting.pop(cid, None)                                  # ein Befehl bricht eine offene Frage ab
    elif text in MENU_BUTTONS:
        awaiting.pop(cid, None)
        if text in MENU_ASK:
            ask_cmd, frage = MENU_ASK[text]
            with lock:
                erlaubt = ask_cmd == "/code" or may_use(cid, cmd=ask_cmd)
            if erlaubt:
                awaiting[cid] = (ask_cmd, time.time())
                return send(cid, frage)
        text = MENU_BUTTONS[text]
    elif cid in awaiting and text:
        ask_cmd, seit = awaiting.pop(cid)
        if ask_cmd in SETTING_INPUTS:
            return handle_setting_input(cid, ask_cmd, text, time.time() - seit < ASK_TTL)
        if time.time() - seit < ASK_TTL:
            text = f"{ask_cmd} {text}"
    if not text.startswith("/"):
        return send(cid, "Nutze die Tasten unten oder /hilfe.", reply_markup=menu_keyboard(cid))
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
            return send(cid, "Das ist ein privater Bot. Mit einem Einladungs- oder Verbindungscode (/code CODE) bekommst du Zugriff.",
                        reply_markup=menu_keyboard(cid))
    {
        "/start": lambda: cmd_weblogin(cid, arg[4:]) if arg.startswith("web_") else cmd_help(cid),
        "/hilfe": lambda: cmd_help(cid),
        "/help": lambda: cmd_help(cid),
        "/code": lambda: cmd_code(cid, arg, m.get("from")),
        "/mitglieder": lambda: cmd_mitglieder(cid),
        "/einstellungen": lambda: cmd_einstellungen(cid),
        "/restart": lambda: cmd_restart(cid),
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
        "/clear": lambda: cmd_clear(cid, m.get("message_id")),
        "/autoclear": lambda: cmd_autoclear(cid, arg),
        "/menue": lambda: cmd_menue(cid),
        "/menueaus": lambda: cmd_menue_aus(cid),
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
            "nd": d["delay"], "cancelled": d["cancelled"], "rem": [t for t in active_remind(get_chat(cid)) if mins <= t + REMIND_TOLERANCE], "eta": d["actual"].timestamp(),
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
            note_invite(data["invites"][c]["by"], "abgelaufen")
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


def note_invite(by, status, wer=""):
    """Verlauf der Einladungen ohne Codes, damit Besitzer und Verwalter den Status nachvollziehen koennen."""
    h = data.setdefault("invite_hist", [])
    h.append({"by": by, "status": status, "who": wer, "ts": int(time.time())})
    del h[:-50]


def make_invite(cid):
    """Neuen Einladungscode erzeugen (Aufrufer haelt den Lock). Gibt (Code, Ablauf) zurueck oder wirft ValueError.
    Eine Einladung gibt nur den Mitglieder-Zugang, nie mehr."""
    if not can_invite(cid):
        raise ValueError("Keine Berechtigung.")
    if not ist_besitzer(cid):                              # Verwalter haben nur wenige offene Codes
        offen = [c for c, i in data["invites"].items() if i["by"] == cid and i["exp"] > time.time()]
        if len(offen) >= MAX_OPEN_INVITES:
            raise ValueError(f"Du hast schon {MAX_OPEN_INVITES} offene Einladungscodes. Warte, bis sie eingelöst werden oder ablaufen.")
    while True:
        code = "".join(secrets.choice(SHARE_ALPHABET) for _ in range(8))
        if code not in data["invites"] and code not in data["shares"]:
            break
    exp = time.time() + INVITE_TTL
    data["invites"][code] = {"by": cid, "exp": exp}
    save()
    return code, exp


def create_invite(cid):
    with lock:
        try:
            code, _ = make_invite(cid)
        except ValueError as e:
            return send(cid, str(e))
    send(cid, "\U0001f511 Einladungscode (Vollzugriff)\n\n"
              "Schick diese Zeile der Person. Sie muss sie nur noch abschicken (Antippen kopiert sie):\n\n"
              f"<code>/code {code}</code>\n\n"
              "Der Code gilt 24 Stunden und nur für eine Person.", parse_mode="HTML")


def revoke_invite(actor, code):
    """Offenen Einladungscode widerrufen (Aufrufer haelt den Lock). Gibt (geklappt, Text) zurueck.
    Eigene Einladungen und die von Personen unter sich, der Besitzer alle."""
    if not is_manager(actor):
        return False, "Keine Berechtigung"
    inv = data["invites"].get(code)
    if not inv or inv["exp"] <= time.time():
        data["invites"].pop(code, None)
        return False, "Nicht mehr offen"
    if not (inv["by"] == actor or ist_besitzer(actor) or rang(inv["by"]) < rang(actor)):
        return False, "Nicht möglich"
    del data["invites"][code]
    note_invite(inv["by"], "widerrufen")
    save()
    return True, "Einladung widerrufen"


def drop_invites(m):
    """Offene Einladungscodes einer Person loeschen (wenn sie entfernt wird oder nicht mehr einladen darf)."""
    data["invites"] = {c: i for c, i in data["invites"].items() if i["by"] != m}


ROLLEN = {"verwalter": "Verwalter", "mitglied": "Mitglied", "gast": "Gast"}


def people():
    """Alle Personen ausser dem Besitzer: Mitglieder (auch Verwalter) und Gaeste."""
    return list(data["members"]) + [g for g in data["guests"] if g not in data["members"]]


def rolle_von(m):
    return "Besitzer" if ist_besitzer(m) else "Verwalter" if is_verwalter(m) else "Mitglied" if m in data["members"] else "Gast" if m in data["guests"] else "Keine"


def can_touch(actor, m):
    """Darf actor die Rolle von m aendern oder m entfernen? Nur wer hoeher steht, nie den Besitzer."""
    return m is not None and is_manager(actor) and not ist_besitzer(m) and 0 < rang(m) < rang(actor)


def allowed_roles(actor, m):
    """Rollen, auf die actor m setzen darf. Verwalter ernennt nur der Besitzer."""
    if not can_touch(actor, m):
        return []
    return ["verwalter", "mitglied", "gast"] if ist_besitzer(actor) else ["mitglied", "gast"]


def remove_member(actor, m):
    """Zugang einer Person entfernen (Aufrufer haelt den Lock). Gibt (geklappt, Text) zurueck.
    Besitzer entfernt jeden ausser sich, Verwalter nur Mitglieder und Gaeste."""
    if not is_manager(actor):
        return False, "Keine Berechtigung"
    if m is None or ist_besitzer(m) or rang(m) == 0:
        return False, "Nicht möglich" if m is not None and ist_besitzer(m) else "Nicht mehr vorhanden"
    if not can_touch(actor, m):
        return False, "Nicht möglich"
    for liste in ("members", "verwalter", "guests"):
        if m in data[liste]:
            data[liste].remove(m)
    data.setdefault("invited_by", {}).pop(str(m), None)
    drop_invites(m)
    data["chats"].pop(str(m), None)               # Tracking, Favoriten und Einstellungen der Person gehen mit
    data["state"].pop(str(m), None)
    data["names"].pop(str(m), None)
    data["sessions"] = {t: s for t, s in data["sessions"].items() if s["cid"] != m}   # Website-Anmeldungen beenden
    save()
    return True, "Zugang entfernt"


def set_role(actor, m, role):
    """Rolle einer Person setzen: verwalter, mitglied oder gast (Aufrufer haelt den Lock).
    Nur auf Personen unter sich, Verwalter ernennt nur der Besitzer. Es gibt keine zweite Besitzer-Rolle."""
    if not is_manager(actor):
        return False, "Keine Berechtigung"
    if role not in allowed_roles(actor, m):
        return False, "Nicht möglich"
    if role == "gast":
        for liste in ("members", "verwalter"):
            if m in data[liste]:
                data[liste].remove(m)
        if m not in data["guests"]:
            data["guests"].append(m)
        drop_invites(m)
        data["sessions"] = {t: s for t, s in data["sessions"].items() if s["cid"] != m}   # Gaeste haben keinen Website-Zugang
    else:
        if m in data["guests"]:
            data["guests"].remove(m)
        if m not in data["members"]:
            data["members"].append(m)
        if role == "verwalter" and m not in data["verwalter"]:
            data["verwalter"].append(m)
        elif role == "mitglied" and m in data["verwalter"]:
            data["verwalter"].remove(m)
            drop_invites(m)
    save()
    return True, f"Ist jetzt {ROLLEN[role]}"


def eingeladen_von(m, namen=who):
    by = data.get("invited_by", {}).get(str(m))
    return namen(by) if by else ""


def parse_member(value):
    return int(value) if isinstance(value, str) and value.lstrip("-").isdigit() else None


def member_ref(m):
    """Nicht umkehrbare Kennung fuer die Website, damit dort keine Chat-IDs auftauchen."""
    return token_hash(f"{cfg['bot_token']}|{m}")[:12]


def find_member(ref):
    return next((m for m in people() if secrets.compare_digest(member_ref(m), str(ref))), None)


def open_invites():
    return [(c, i) for c, i in data["invites"].items() if i["exp"] > time.time()]


ROLLE_KURZ = {"v": "verwalter", "m": "mitglied", "g": "gast"}


def members_keyboard(viewer=None):
    """Knoepfe der Mitgliederverwaltung. Ohne viewer: aus Sicht des Besitzers. Person, Rolle, Rauswerfen nur fuer Personen unter dem Betrachter."""
    ich = data["owner"] if viewer is None else viewer
    rows = [[{"text": "\U0001f511 Neue Einladung", "callback_data": "mn:0"},
             {"text": "\U0001f4e8 Offene Einladungen", "callback_data": "mo:0"}]]
    for m in people():
        if not can_touch(ich, m):
            continue
        rows.append([{"text": f"\U0001f464 {who(m)}"[:26], "callback_data": f"md:{m}"},
                     {"text": f"⚙️ {rolle_von(m)}", "callback_data": f"mr:{m}"},
                     {"text": "\U0001f528", "callback_data": f"mrm:{m}"}])
    return {"inline_keyboard": rows}


def role_keyboard(viewer, m):
    namen = {"verwalter": "⭐ Verwalter", "mitglied": "\U0001f642 Mitglied", "gast": "\U0001f440 Gast"}
    kurz = {v: k for k, v in ROLLE_KURZ.items()}
    rows = [[{"text": ("✅ " if rolle_von(m).lower() == r else "") + namen[r], "callback_data": f"mr:{m}:{kurz[r]}"}] for r in allowed_roles(viewer, m)]
    rows.append([{"text": "⬅️ Zurück", "callback_data": "mm:0"}])
    return {"inline_keyboard": rows}


def members_text():
    zeilen = [f"• {who(data['owner'])} (Besitzer)"] if data["owner"] else []
    zeilen += [f"• {who(m)}" + ("" if rolle_von(m) == "Mitglied" else f" ({rolle_von(m)})") +
               (f" · eingeladen von {eingeladen_von(m)}" if eingeladen_von(m) else "") for m in people()]
    return ("Personen:\n" + "\n".join(zeilen) if zeilen else "Noch niemand eingeladen.") + \
        "\n\nName antippen für Details, ⚙️ ändert die Rolle (nur Personen unter dir), \U0001f528 wirft die Person raus (Zugang und Daten gehen weg)."


def cmd_mitglieder(cid):
    with lock:
        if not is_manager(cid):
            return
        text, kb = members_text(), members_keyboard(cid)
    send(cid, text, reply_markup=kb)


def edit_or_log(cid, mid, text, kb=None):
    try:
        params = {"chat_id": cid, "message_id": mid, "text": text}
        if kb is not None:
            params["reply_markup"] = kb
        tg("editMessageText", **params)
    except Exception as e:
        log("Nachricht nicht aktualisiert:", e)             # Telegram meldet einen Fehler, wenn der Text gleich bleibt


def answer_cb(cb, text="", alert=False):
    try:
        tg("answerCallbackQuery", callback_query_id=cb["id"], text=text[:190], show_alert=alert)
    except Exception as e:
        log("Callback-Antwort fehlgeschlagen:", e)


def stamp(ts):
    return (datetime.fromtimestamp(ts, TZ) if TZ else datetime.fromtimestamp(ts)).strftime("%d.%m. %H:%M")


def invites_view():
    """Text und Knoepfe der offenen Einladungen samt Verlauf (Aufrufer haelt den Lock)."""
    offen = open_invites()
    zeilen = [f"• {c} · von {who(i['by'])} · bis {stamp(i['exp'])}" for c, i in offen]
    verlauf = [f"• {h['status']}" + (f" ({h['who']})" if h.get("who") else "") + " · " + stamp(h["ts"])
               for h in reversed(data.get("invite_hist", [])[-5:])]
    text = "\U0001f4e8 Offene Einladungen\n" + ("\n".join(zeilen) if zeilen else "Keine offenen Einladungen.")
    if verlauf:
        text += "\n\nZuletzt:\n" + "\n".join(verlauf)
    rows = [[{"text": f"\U0001f6ab {c} widerrufen", "callback_data": f"mx:{c}"}] for c, _ in offen[:10]]
    rows.append([{"text": "⬅️ Zurück", "callback_data": "mm:0"}])
    return text, {"inline_keyboard": rows}


def member_details(m):
    offen = len([1 for _, i in open_invites() if i["by"] == m])
    von = eingeladen_von(m) or "unbekannt"
    return f"\U0001f464 {who(m)}\nRolle: {rolle_von(m)}\nEingeladen von: {von}\nOffene Einladungen von dieser Person: {offen}"


def handle_member_admin_callback(cb, cid, mid, action, rest):
    """Mitgliederverwaltung per Knopf: md Details, mo offene Einladungen, mx widerrufen, mn neue Einladung, mm zurueck,
    mrm rauswerfen, mr Rolle (ohne Rolle: Auswahl zeigen). Jede Aktion prueft erneut serverseitig die Berechtigung."""
    with lock:
        if not is_manager(cid):
            return
        answer, view, detail, ziel = "", "members", "", None
        if action == "mrm":
            _, answer = remove_member(cid, parse_member(rest))
        elif action == "mr":
            wer, _, kurz = rest.partition(":")
            ziel = parse_member(wer)
            if kurz:
                _, answer = set_role(cid, ziel, ROLLE_KURZ.get(kurz))
            elif allowed_roles(cid, ziel):
                view = "role"
            else:
                answer = "Nicht möglich"
        elif action == "mx":
            _, answer = revoke_invite(cid, rest)
            view = "invites"
        elif action == "mo":
            view = "invites"
        elif action == "md":
            m = parse_member(rest)
            if rang(m or 0) > 0 and not ist_besitzer(m) and can_touch(cid, m):
                view, detail = "details", member_details(m)
        if view == "invites":
            text, kb = invites_view()
        elif view == "details":
            text, kb = detail, {"inline_keyboard": [[{"text": "⬅️ Zurück", "callback_data": "mm:0"}]]}
        elif view == "role":
            text, kb = f"Rolle für {who(ziel)} (jetzt: {rolle_von(ziel)})", role_keyboard(cid, ziel)
        else:
            text, kb = members_text(), members_keyboard(cid)
    answer_cb(cb, answer)
    if action == "mn":
        return create_invite(cid)
    edit_or_log(cid, mid, text, kb)


def cmd_code(cid, arg, user=None):
    code = arg.strip().upper().replace(" ", "")
    if not code:
        if can_invite(cid):
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
        if inv and inv["exp"] > now and not is_full(cid):
            del data["invites"][code]                     # Einladung ist nur einmal nutzbar
            if cid in data["guests"]:
                data["guests"].remove(cid)
            data["members"].append(cid)
            data["names"][str(cid)] = display_name(user)
            get_chat(cid)
            invited = inv["by"]
            name = who(cid)
            data.setdefault("invited_by", {})[str(cid)] = invited
            note_invite(invited, "eingelöst", name)
            save()
    if invited is not None:
        send(cid, "✅ Einladung eingelöst. Du kannst den Bot jetzt voll nutzen.\n\n" + help_text(cid), reply_markup=menu_keyboard(cid))
        if ist_besitzer(invited):
            send(invited, f"✅ Einladungscode {code} wurde von {name} eingelöst. /mitglieder zeigt alle Eingeladenen.")
        else:
            send(invited, f"✅ Dein Einladungscode wurde von {name} eingelöst.")
            if data["owner"]:
                send(data["owner"], f"ℹ️ {name} wurde von {who(invited)} eingeladen. /mitglieder zeigt alle Eingeladenen.")
        return
    with lock:
        e = data["shares"].get(code)
        if not e:
            code_fails[cid] = fails + [now]
            return send(cid, "Code ung\u00fcltig oder abgelaufen.")
        neu_gast = not is_full(cid) and cid not in data["guests"]
        if neu_gast:
            data["guests"].append(cid)                    # Zugriff nur auf geteilte Verbindungen
        get_chat(cid)
        save()
        w = dict(e["w"])
    if neu_gast:
        send(cid, "Unten findest du die Tasten für geteilte Verbindungen.", reply_markup=menu_keyboard(cid))
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
    if action in ADMIN_ACTIONS:
        return handle_member_admin_callback(cb, cid, mid, action, sid)
    if action == "st":
        return handle_settings_callback(cb, cid, mid, sid)
    if action in BETRIEB_ACTIONS:
        return handle_restart_callback(cb, cid, mid, sid)
    if action == "ac":
        return handle_autoclear_callback(cb, cid, mid, sid)
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
    global poll_offset
    offset = None
    while True:
        try:
            params = {"timeout": 50, "allowed_updates": ["message", "callback_query"]}
            if offset:
                params["offset"] = offset
            for u in tg("getUpdates", **params):
                offset = u["update_id"] + 1
                poll_offset = offset
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


def opt_str(v, limit=200):
    """Optionaler Text: fehlt er, kommt ein leerer Text zurueck."""
    if v in (None, ""):
        return ""
    return clean_str(v, limit)


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
                      "cancelled": bool(st.get("cancelled")), "platform": w.get("platform", ""),
                      "platform_label": w.get("platform_label", ""), "via": w.get("via", ""),
                      "mode_name": w.get("mode_name", "")})
    routines = [{"line": r["line"], "dest": r["dest"], "stop_name": r["stop_name"], "hhmm": r["hhmm"],
                 "days": days_label(r["days"])} for r in chat["routines"]]
    return {"trips": sorted(trips, key=lambda t: t["planned"]), "routines": routines}


def web_name(m):
    """Anzeigename fuer die Website: nie eine Chat-ID."""
    return data["names"].get(str(m)) or "Ohne Namen"


def web_members(viewer):
    """Personen, Einladungen und Verlauf fuer die Website (Besitzer und Verwalter, Aufrufer haelt den Lock).
    Es gehen keine Chat-IDs raus, nur eine nicht umkehrbare Kennung. roles: Rollen, auf die der Betrachter die Person setzen darf."""
    rows = []

    def add(m):
        von = data.get("invited_by", {}).get(str(m))
        rows.append({"ref": member_ref(m), "name": web_name(m), "role": rolle_von(m), "invited_by": web_name(von) if von else "",
                     "removable": can_touch(viewer, m), "roles": allowed_roles(viewer, m)})
    if data["owner"]:
        add(data["owner"])
    for m in people():
        add(m)
    return {"members": rows,
            "invites": [{"code": c, "by": web_name(i["by"]), "expires": int(i["exp"]), "status": "offen"} for c, i in open_invites()],
            "history": [{"by": web_name(h["by"]), "status": h["status"], "who": h.get("who", ""), "ts": h["ts"]}
                        for h in reversed(data.get("invite_hist", []))][:20]}


def web_members_route(method, path, q, body, cid):
    with lock:
        if not is_manager(cid):                                 # bei jeder Aktion erneut serverseitig pruefen
            return 403, {"error": "keine Berechtigung"}
        msg = ""
        if method == "GET" and path == "/members":
            pass
        elif method == "POST" and path == "/members/invite":
            try:
                make_invite(cid)
            except ValueError as e:
                return 400, {"error": str(e)}
            msg = "Einladung erstellt"
        elif method == "DELETE" and path == "/members/invite":
            ok, msg = revoke_invite(cid, q.get("code", [""])[0].upper())
            if not ok:
                return 400, {"error": msg}
        elif method == "DELETE" and path == "/members":
            ok, msg = remove_member(cid, find_member(q.get("ref", [""])[0]))
            if not ok:
                return 400, {"error": msg}
        elif method == "POST" and path == "/members/role":
            ok, msg = set_role(cid, find_member(clean_str(body.get("ref"), 40)), body.get("role"))
            if not ok:
                return 400, {"error": msg}
        else:
            return 404, {"error": "unbekannt"}
        return 200, {**web_members(cid), "message": msg}


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
    if path == "/settings" and method in ("GET", "POST"):
        # Immer die eigenen Einstellungen der angemeldeten Person (cid kommt aus der Sitzung, nie aus der Anfrage)
        if method == "POST":
            erlaubt = ("nick", "autoclear_on", "autoclear_hours", "remind_first", "remind_second")
            try:
                apply_settings(cid, {k: body[k] for k in erlaubt if k in body})
            except ValueError as e:
                return 400, {"error": str(e)}
        with lock:
            return 200, {**settings_view(cid), "admin": is_manager(cid)}
    if path.startswith("/members"):
        return web_members_route(method, path, q, body, cid)
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
                 "dest": clean_str(body.get("dest")), "kind": body.get("kind"), "planned": body.get("planned"),
                 "platform": opt_str(body.get("platform"), 40), "via": opt_str(body.get("via"), 400),
                 "mode_name": opt_str(body.get("mode_name"), 40)}
            w["platform_label"] = "Steig" if body.get("platform_label") == "Steig" else "Gleis"
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

def parse_id(value):
    """Telegram-Chat-ID als Zahl. Alles andere (leer, Text, 0, Kommazahl) ergibt 0, also keine Rolle."""
    if isinstance(value, bool):
        return 0
    try:
        return int(str(value).strip())
    except ValueError:
        return 0


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
    cfg["verwalter_id_ignoriert"] = bool(os.environ.get("VERWALTER_ID") or cfg.get("verwalter_id"))
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
        if os.environ.get("BOT_IDLE_WITHOUT_TOKEN", "").lower() == "true":
            # z. B. Preview-Deployment ohne eigenen Bot: nichts tun, aber nicht ständig neu starten
            log("Kein BOT_TOKEN gesetzt: Der Bot bleibt aus. Die Website läuft weiter.")
            while True:
                time.sleep(3600)
        sys.exit("Kein bot_token. Trage ihn in config.json ein (oder setze BOT_TOKEN).")
    load()
    for k in [k for k in data["sessions"] if len(k) != 64]:     # alte Sitzungen mit Klartext-Token ungueltig machen
        del data["sessions"][k]
    if cfg["owner_id"]:
        data["owner"] = cfg["owner_id"]                          # Besitzer fest vorgegeben, niemand kann ihn per /start uebernehmen
        get_chat(cfg["owner_id"])
    migrate()
    save()
    if cfg["verwalter_id_ignoriert"]:
        log("Hinweis: VERWALTER_ID gibt es nicht mehr. Verwalter ernennt jetzt der Besitzer im Bot (/mitglieder). Die Einstellung wird ignoriert.")
    if data["owner"] is None:
        log("WARNUNG: Kein Besitzer festgelegt. Wer dem Bot zuerst /start schreibt, wird Besitzer. BOT_OWNER_ID setzen!")
    cmds = [
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
        {"command": "clear", "description": "Chatverlauf löschen"},
        {"command": "autoclear", "description": "Nachrichten automatisch löschen"},
        {"command": "einstellungen", "description": "Name, Löschen, Erinnerungen"},
        {"command": "menue", "description": "Tasten-Menü anzeigen"},
        {"command": "status", "description": "Einstellungen"},
    ]
    tg("setMyCommands", commands=cmds)
    if data["owner"]:                                                  # /restart steht nur in der Befehlsliste des Besitzers
        try:
            tg("setMyCommands", commands=cmds + [{"command": "restart", "description": "Bot neu starten"}], scope={"type": "chat", "chat_id": data["owner"]})
        except Exception as e:
            log("Befehlsliste fuer den Besitzer nicht gesetzt:", e)
    if cfg["api_port"]:
        cfg["bot_username"] = cfg["bot_username"] or tg("getMe")["username"]
        start_api()
    log("Bot l\u00e4uft.")
    with msg_lock:
        for c, l in data.get("msglog", {}).items():
            msg_log[int(c)] = [list(e) for e in l]
    threading.Thread(target=autoclear_loop, daemon=True).start()
    threading.Thread(target=monitor_loop, daemon=True).start()
    poll_loop()


if __name__ == "__main__":
    main()
