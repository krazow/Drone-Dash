"""Drone Dash - minimal drone detection dashboard server.

The display name comes from APP_NAME, so renaming the product needs no code change.

One file: receives detections over HTTP, classifies them, stores them in SQLite,
pushes live updates to browsers over a WebSocket, and sends phone alerts via ntfy.

Run:  uvicorn server:app --host 0.0.0.0 --port 8080
"""
import asyncio
import contextlib
import json
import math
import os
import re
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
import html

from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

HERE = Path(__file__).parent
DB_PATH = os.environ.get("APP_DB", str(HERE / "detections.sqlite"))
TOKEN = os.environ.get("APP_TOKEN", "")            # shared secret for /api/ingest (optional)
NTFY_URL = os.environ.get("APP_NTFY_URL", "")      # e.g. https://ntfy.sh/your-secret-topic
LIVE_SECS = int(os.environ.get("APP_LIVE_SECS", "120"))
RETENTION_DAYS = int(os.environ.get("APP_RETENTION_DAYS", "90"))
APP_NAME = os.environ.get("APP_NAME", "Drone Dash")   # change the product name here (or in the env file)
MAX_SPEED_MPS = 60.0                                # faster than this between fixes = spoof flag
RF_MIN_HITS = 3                                     # RF hits within RF_WINDOW before "unknown drone"
RF_WINDOW = 90

# Sensor nodes: name -> (lat, lon). Override with APP_NODES='{"name":[lat,lon],...}'
NODES = json.loads(os.environ.get("APP_NODES", "null")) or {
    "waitsburg-1": [46.2704, -118.1530],
    "dayton-1": [46.3207, -117.9719],
}

# ANSI/CTA-2063-A manufacturer codes (first 4 characters of a Remote ID serial).
# Extend this from the FAA Declaration of Compliance list: https://uasdoc.faa.gov/listDocs
MFR_CODES = {
    "1581": "DJI", "1748": "Autel Robotics", "1701": "Percepto", "1587": "AgEagle",
    "1596": "Dronetag", "1787": "BlueMark", "1710": "Atom UAV", "1785": "Watts Innovations",
}
ANSI_SERIAL = re.compile(r"^[0-9A-HJ-NP-Z]{4}[1-9A-F][0-9A-HJ-NP-Z]{1,15}$")

# Field-name aliases so Mesh-Mapper webhooks, DragonSync JSON and the simulator all work.
ALIASES = {
    "uas_id": ["uas_id", "basic_id", "id", "serial", "serial_number", "drone_id", "uasid"],
    "id_type": ["id_type", "idtype", "ua_id_type"],
    "source": ["source", "transport", "protocol"],
    "node": ["node", "node_id", "sensor", "receiver"],
    "lat": ["lat", "latitude", "drone_lat"],
    "lon": ["lon", "lng", "longitude", "drone_lon", "drone_long", "drone_lng"],
    "alt": ["alt_m", "alt", "altitude", "drone_altitude", "geodetic_altitude", "height"],
    "speed": ["speed_mps", "speed", "ground_speed"],
    "heading": ["heading", "direction", "course", "track"],
    "pilot_lat": ["pilot_lat", "operator_lat", "home_lat"],
    "pilot_lon": ["pilot_lon", "pilot_long", "pilot_lng", "operator_lon", "home_lon", "home_lng"],
    "rssi": ["rssi", "signal", "signal_strength"],
    "mac": ["mac", "mac_address", "addr"],
    "band": ["band", "freq", "frequency", "center_freq"],
    "ts": ["ts", "timestamp", "time"],
}

clients: set[WebSocket] = set()
db_lock = threading.Lock()


# ---------------------------------------------------------------- database
def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


CON = db()
CON.executescript("""
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS detections (
  id INTEGER PRIMARY KEY, ts REAL, node TEXT, track_id TEXT, cls TEXT, source TEXT,
  lat REAL, lon REAL, alt REAL, speed REAL, heading REAL,
  pilot_lat REAL, pilot_lon REAL, rssi REAL, raw TEXT);
CREATE INDEX IF NOT EXISTS det_track ON detections(track_id, ts);
CREATE INDEX IF NOT EXISTS det_ts ON detections(ts);
CREATE TABLE IF NOT EXISTS tracks (
  track_id TEXT PRIMARY KEY, cls TEXT, uas_id TEXT, id_type TEXT, manufacturer TEXT,
  first_seen REAL, last_seen REAL, lat REAL, lon REAL, alt REAL, speed REAL, heading REAL,
  pilot_lat REAL, pilot_lon REAL, rssi REAL, nodes TEXT, sources TEXT,
  hits INTEGER DEFAULT 0, spoof INTEGER DEFAULT 0, alerted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS nodes (node TEXT PRIMARY KEY, last_seen REAL, info TEXT);
""")


# ---------------------------------------------------------------- helpers
def pick(d: dict, key: str):
    for k in ALIASES[key]:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def metres(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def normalise(raw: dict, default_node: str | None) -> dict:
    # Mesh-Mapper / DragonSync sometimes nest the useful bits; flatten one level.
    flat = dict(raw)
    for v in raw.values():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                flat.setdefault(k2, v2)
    d = {k: pick(flat, k) for k in ALIASES}
    for k in ("lat", "lon", "alt", "speed", "heading", "pilot_lat", "pilot_lon", "rssi"):
        d[k] = num(d[k])
    ts = num(d["ts"])
    d["ts"] = ts if ts and ts > 1e9 else time.time()
    d["node"] = str(d["node"] or default_node or "unknown-node")
    d["source"] = str(d["source"] or "").lower()
    d["uas_id"] = str(d["uas_id"]).strip().upper() if d["uas_id"] else None
    if not d["uas_id"] and d["mac"] and d["source"] != "rf":
        d["uas_id"] = "MAC:" + str(d["mac"]).upper()
        d["id_type"] = d["id_type"] or "mac"
    if d["lat"] == 0 and d["lon"] == 0:   # no GPS fix yet
        d["lat"] = d["lon"] = None
    return d


def classify(d: dict) -> tuple[str, str | None]:
    """Return (class, manufacturer)."""
    if d["source"] == "rf":
        return "unknown", None
    if d["source"] in ("dji", "droneid", "dji_droneid"):
        mfr = "DJI"
        return "dji", mfr
    uid = d["uas_id"] or ""
    id_type = str(d["id_type"] or "").lower()
    if ANSI_SERIAL.match(uid) and id_type in ("", "serial", "1", "serial number"):
        mfr = MFR_CODES.get(uid[:4])
        if mfr:
            return "identified", mfr
        return "rid", f"Unlisted maker (code {uid[:4]})"
    return "rid", None


async def broadcast(msg: dict):
    dead = []
    for ws in clients:
        try:
            await ws.send_text(json.dumps(msg))
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


def notify(title: str, body: str, high: bool = False):
    if not NTFY_URL:
        return

    def send():
        try:
            req = urllib.request.Request(NTFY_URL, data=body.encode(), method="POST",
                                         headers={"Title": title, "Priority": "high" if high else "default",
                                                  "Tags": "warning" if high else "helicopter"})
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            print("ntfy failed:", e)
    threading.Thread(target=send, daemon=True).start()


def track_row(r: sqlite3.Row) -> dict:
    t = dict(r)
    t["nodes"] = json.loads(t["nodes"] or "[]")
    t["sources"] = json.loads(t["sources"] or "[]")
    return t


# ---------------------------------------------------------------- ingest
def store(d: dict) -> dict | None:
    """Store one normalised detection; return the updated track (or None if not shown yet)."""
    cls, mfr = classify(d)
    now = d["ts"]
    if cls == "unknown":
        band = str(d["band"] or "rf")
        track_id = f"RF:{d['node']}:{band}"
        node_pos = NODES.get(d["node"])
        if node_pos and d["lat"] is None:
            d["lat"], d["lon"] = node_pos
    else:
        track_id = d["uas_id"] or f"ANON:{d['node']}"

    with db_lock:
        cur = CON.cursor()
        cur.execute("INSERT INTO nodes(node,last_seen,info) VALUES(?,?,?) "
                    "ON CONFLICT(node) DO UPDATE SET last_seen=excluded.last_seen",
                    (d["node"], now, "{}"))
        cur.execute("INSERT INTO detections(ts,node,track_id,cls,source,lat,lon,alt,speed,heading,"
                    "pilot_lat,pilot_lon,rssi,raw) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (now, d["node"], track_id, cls, d["source"], d["lat"], d["lon"], d["alt"],
                     d["speed"], d["heading"], d["pilot_lat"], d["pilot_lon"], d["rssi"],
                     json.dumps(d.get("_raw", {}))[:4000]))
        old = cur.execute("SELECT * FROM tracks WHERE track_id=?", (track_id,)).fetchone()

        spoof = 0
        if old and old["lat"] is not None and d["lat"] is not None and cls != "unknown":
            dt = max(now - old["last_seen"], 0.5)
            if metres(old["lat"], old["lon"], d["lat"], d["lon"]) / dt > MAX_SPEED_MPS:
                spoof = 1

        nodes = set(json.loads(old["nodes"])) if old else set()
        nodes.add(d["node"])
        sources = set(json.loads(old["sources"])) if old else set()
        if d["source"]:
            sources.add(d["source"])
        # A DJI DroneID hit on a drone we already know by Remote ID keeps the better class.
        if old and old["cls"] == "identified" and cls in ("rid", "dji"):
            cls, mfr = old["cls"], old["manufacturer"]

        if old:
            cur.execute("""UPDATE tracks SET cls=?, manufacturer=COALESCE(?,manufacturer), last_seen=?,
                lat=COALESCE(?,lat), lon=COALESCE(?,lon), alt=COALESCE(?,alt), speed=COALESCE(?,speed),
                heading=COALESCE(?,heading), pilot_lat=COALESCE(?,pilot_lat), pilot_lon=COALESCE(?,pilot_lon),
                rssi=?, nodes=?, sources=?, hits=hits+1, spoof=MAX(spoof,?) WHERE track_id=?""",
                        (cls, mfr, now, d["lat"], d["lon"], d["alt"], d["speed"], d["heading"],
                         d["pilot_lat"], d["pilot_lon"], d["rssi"], json.dumps(sorted(nodes)),
                         json.dumps(sorted(sources)), spoof, track_id))
        else:
            cur.execute("""INSERT INTO tracks(track_id,cls,uas_id,id_type,manufacturer,first_seen,last_seen,
                lat,lon,alt,speed,heading,pilot_lat,pilot_lon,rssi,nodes,sources,hits,spoof)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0)""",
                        (track_id, cls, d["uas_id"], d["id_type"], mfr, now, now, d["lat"], d["lon"],
                         d["alt"], d["speed"], d["heading"], d["pilot_lat"], d["pilot_lon"], d["rssi"],
                         json.dumps(sorted(nodes)), json.dumps(sorted(sources))))
        CON.commit()
        t = track_row(cur.execute("SELECT * FROM tracks WHERE track_id=?", (track_id,)).fetchone())

        if cls == "unknown":
            recent = cur.execute("SELECT COUNT(*) FROM detections WHERE track_id=? AND ts>?",
                                 (track_id, now - RF_WINDOW)).fetchone()[0]
            identified_nearby = cur.execute(
                "SELECT COUNT(*) FROM detections WHERE node=? AND cls!='unknown' AND ts BETWEEN ? AND ?",
                (d["node"], now - 60, now + 60)).fetchone()[0]
            if recent < RF_MIN_HITS or identified_nearby:
                return None          # not confirmed yet, or explained by a known drone
        if not t["alerted"]:
            cur.execute("UPDATE tracks SET alerted=1 WHERE track_id=?", (track_id,))
            CON.commit()
            t["alerted"] = 1
            label = {"identified": t["manufacturer"] or "Identified drone", "rid": "Remote ID drone",
                     "dji": "DJI drone (DroneID)", "unknown": "UNKNOWN drone"}[cls]
            notify(f"{APP_NAME}: {label} near {d['node']}",
                   f"{t['uas_id'] or track_id}\nalt {t['alt'] or '?'} m, rssi {t['rssi'] or '?'}",
                   high=(cls == "unknown"))
    return t


async def ingest(request: Request):
    if TOKEN and request.headers.get("x-token") != TOKEN and request.query_params.get("token") != TOKEN:
        raise HTTPException(401, "bad token")
    body = await request.json()
    items = body if isinstance(body, list) else [body]
    default_node = request.query_params.get("node")
    shown = 0
    for raw in items:
        if not isinstance(raw, dict):
            continue
        if raw.get("type") == "status":                      # node heartbeat
            node = str(raw.get("node") or default_node)
            with db_lock:
                CON.execute("INSERT INTO nodes(node,last_seen,info) VALUES(?,?,?) ON CONFLICT(node) "
                            "DO UPDATE SET last_seen=excluded.last_seen, info=excluded.info",
                            (node, time.time(), json.dumps(raw)[:2000]))
                CON.commit()
            await broadcast({"type": "node", "node": node, "last_seen": time.time(), "info": raw})
            continue
        d = normalise(raw, default_node)
        d["_raw"] = raw
        if d["source"] != "rf" and not d["uas_id"]:
            continue
        t = store(d)
        if t:
            shown += 1
            await broadcast({"type": "track", "track": t,
                             "point": [d["lat"], d["lon"]] if d["lat"] is not None else None})
    return JSONResponse({"ok": True, "received": len(items), "shown": shown})


# ---------------------------------------------------------------- read API
def points(track_id: str, since: float) -> list:
    rows = CON.execute("SELECT lat,lon FROM detections WHERE track_id=? AND ts>? AND lat IS NOT NULL "
                       "ORDER BY ts", (track_id, since)).fetchall()
    return [[r[0], r[1]] for r in rows]


async def state(request: Request):
    now = time.time()
    with db_lock:
        rows = CON.execute("SELECT * FROM tracks WHERE last_seen>? ORDER BY last_seen DESC",
                           (now - LIVE_SECS,)).fetchall()
        live = []
        for r in rows:
            t = track_row(r)
            if t["cls"] == "unknown" and not t["alerted"]:
                continue
            t["trail"] = points(t["track_id"], now - 120)
            live.append(t)
        seen = {r["node"]: dict(r) for r in CON.execute("SELECT * FROM nodes").fetchall()}
        day = CON.execute("SELECT cls, COUNT(*) FROM tracks WHERE last_seen>? AND alerted=1 GROUP BY cls",
                          (now - 86400,)).fetchall()
    nodes = [{"node": n, "lat": p[0], "lon": p[1], "last_seen": seen.get(n, {}).get("last_seen")}
             for n, p in NODES.items()]
    return JSONResponse({"app_name": APP_NAME, "now": now, "live_secs": LIVE_SECS, "nodes": nodes, "tracks": live,
                         "last24h": {r[0]: r[1] for r in day}})


async def history(request: Request):
    hours = float(request.query_params.get("hours", 24))
    since = time.time() - hours * 3600
    with db_lock:
        rows = CON.execute("SELECT * FROM tracks WHERE last_seen>? AND alerted=1 ORDER BY last_seen DESC "
                           "LIMIT 500", (since,)).fetchall()
    return JSONResponse([track_row(r) for r in rows])


async def track(request: Request):
    track_id = request.path_params["track_id"]
    hours = float(request.query_params.get("hours", 24))
    with db_lock:
        r = CON.execute("SELECT * FROM tracks WHERE track_id=?", (track_id,)).fetchone()
        if not r:
            raise HTTPException(404, "no such track")
        t = track_row(r)
        t["path"] = points(track_id, time.time() - hours * 3600)
    return JSONResponse(t)


async def ws(socket: WebSocket):
    await socket.accept()
    clients.add(socket)
    try:
        while True:
            await socket.receive_text()   # keep-alive pings from the page
    except WebSocketDisconnect:
        clients.discard(socket)


async def index(request: Request):
    page = (HERE / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(page.replace("{{APP_NAME}}", html.escape(APP_NAME)))


# ---------------------------------------------------------------- housekeeping
async def prune_loop():
    while True:
        cutoff = time.time() - RETENTION_DAYS * 86400
        with db_lock:
            CON.execute("DELETE FROM detections WHERE ts<?", (cutoff,))
            CON.execute("DELETE FROM tracks WHERE last_seen<?", (cutoff,))
            CON.commit()
        await asyncio.sleep(3600)


@contextlib.asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(prune_loop())
    yield
    task.cancel()


app = Starlette(routes=[
    Route("/", index),
    Route("/api/ingest", ingest, methods=["POST"]),
    Route("/api/state", state),
    Route("/api/history", history),
    Route("/api/track/{track_id:path}", track),
    WebSocketRoute("/ws", ws),
], lifespan=lifespan)
