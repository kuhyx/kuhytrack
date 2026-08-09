#!/usr/bin/env python3
"""
kuhytrack server — a single-file, zero-dependency, ActivityWatch-API-compatible
activity store with first-class multi-device support.

Why stdlib only: it has to run as a systemd unit on Arch and inside Termux on a
Pixel with no pip, forever, without a dependency bumping and breaking it.
http.server + sqlite3 is more than enough for ~1 write/5s from 3 devices.

Wire-compatible with ActivityWatch's REST API (/api/0/...), so every existing
aw-watcher-* binary can point at this and Just Work. Extensions live under
/api/0/kt/ so they can never collide with upstream.

Run:  KT_DB=~/.local/share/kuhytrack/kt.db KT_TOKEN=secret python3 kt_server.py
Env:
  KT_DB      sqlite path         (default ~/.local/share/kuhytrack/kt.db)
  KT_HOST    bind host           (default 127.0.0.1)
  KT_PORT    bind port           (default 5600 — same as ActivityWatch)
  KT_TOKEN   bearer token        (default empty = no auth; set it if not on loopback)
  KT_WEB     dashboard dir       (default ../web next to this file)
"""

from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VERSION = "kuhytrack 0.1.0"
DB_PATH = Path(
    os.environ.get("KT_DB", Path.home() / ".local/share/kuhytrack/kt.db")
).expanduser()
HOST = os.environ.get("KT_HOST", "127.0.0.1")
PORT = int(os.environ.get("KT_PORT", "5600"))
TOKEN = os.environ.get("KT_TOKEN", "")
WEB_DIR = Path(os.environ.get("KT_WEB", Path(__file__).resolve().parent.parent / "web"))
HOSTNAME = socket.gethostname()

# ---------------------------------------------------------------- time helpers

ISO_Z = "%Y-%m-%dT%H:%M:%S.%f%z"


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(s) -> datetime:
    """Accept anything ActivityWatch watchers emit."""
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # 2024-01-01T10:00:00.123456789+00:00 -> trim ns to us
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---------------------------------------------------------------------- schema

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS buckets (
  id TEXT PRIMARY KEY, created TEXT, name TEXT, type TEXT, client TEXT,
  hostname TEXT, device TEXT, data TEXT DEFAULT '{}', last_updated TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bucket_id TEXT NOT NULL, timestamp TEXT NOT NULL, duration REAL NOT NULL DEFAULT 0,
  data TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(bucket_id) REFERENCES buckets(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_events_bucket_ts ON events(bucket_id, timestamp DESC);
CREATE TABLE IF NOT EXISTS habits (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL,
  created TEXT, meta TEXT DEFAULT '{}', archived INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS habit_ticks (
  habit_id INTEGER NOT NULL, day TEXT NOT NULL, value REAL DEFAULT 1,
  ts TEXT, device TEXT, PRIMARY KEY(habit_id, day)
);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, tag TEXT, device TEXT,
  start TEXT, end TEXT, notes TEXT
);
"""

_local = threading.local()


def db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        _local.conn = conn
    return conn


# ------------------------------------------------------------------ core logic


def bucket_row_to_dict(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "created": r["created"],
        "name": r["name"],
        "type": r["type"],
        "client": r["client"],
        "hostname": r["hostname"],
        "device": r["device"],
        "data": json.loads(r["data"] or "{}"),
        "last_updated": r["last_updated"],
    }


def get_bucket(bid: str):
    r = db().execute("SELECT * FROM buckets WHERE id=?", (bid,)).fetchone()
    return bucket_row_to_dict(r) if r else None


def create_bucket(bid: str, body: dict):
    hostname = body.get("hostname") or "unknown"
    db().execute(
        "INSERT OR IGNORE INTO buckets(id,created,name,type,client,hostname,device,data,last_updated)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (
            bid,
            body.get("created") or fmt_ts(now()),
            body.get("name") or bid,
            body.get("type") or "unknown",
            body.get("client") or "unknown",
            hostname,
            body.get("device") or hostname,
            json.dumps(body.get("data") or {}),
            fmt_ts(now()),
        ),
    )


def row_to_event(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "timestamp": r["timestamp"],
        "duration": r["duration"],
        "data": json.loads(r["data"]),
    }


def get_events(bid: str, limit=-1, start=None, end=None) -> list:
    q = "SELECT * FROM events WHERE bucket_id=?"
    args: list = [bid]
    if start:
        q += " AND timestamp >= ?"
        args.append(fmt_ts(parse_ts(start)))
    if end:
        q += " AND timestamp <= ?"
        args.append(fmt_ts(parse_ts(end)))
    q += " ORDER BY timestamp DESC"
    if limit and int(limit) > 0:
        q += " LIMIT ?"
        args.append(int(limit))
    return [row_to_event(r) for r in db().execute(q, args)]


def insert_event(bid: str, ev: dict) -> dict:
    ts = fmt_ts(parse_ts(ev["timestamp"]))
    dur = float(ev.get("duration") or 0)
    data = json.dumps(ev.get("data") or {}, sort_keys=True)
    cur = db().execute(
        "INSERT INTO events(bucket_id,timestamp,duration,data) VALUES(?,?,?,?)",
        (bid, ts, dur, data),
    )
    db().execute("UPDATE buckets SET last_updated=? WHERE id=?", (fmt_ts(now()), bid))
    return {
        "id": cur.lastrowid,
        "timestamp": ts,
        "duration": dur,
        "data": json.loads(data),
    }


def heartbeat(bid: str, ev: dict, pulsetime: float) -> dict:
    """Exact port of aw-core aw_transform.heartbeats.heartbeat_merge.

    Merge iff data is identical AND
    last.timestamp <= hb.timestamp <= last.timestamp + last.duration + pulsetime.
    New duration = max(old, (hb.ts - last.ts) + hb.duration).
    """
    hb_ts = parse_ts(ev["timestamp"])
    hb_dur = float(ev.get("duration") or 0)
    hb_data = json.dumps(ev.get("data") or {}, sort_keys=True)

    last = (
        db()
        .execute(
            "SELECT * FROM events WHERE bucket_id=? ORDER BY timestamp DESC, id DESC LIMIT 1",
            (bid,),
        )
        .fetchone()
    )
    if last is not None and last["data"] == hb_data:
        last_ts = parse_ts(last["timestamp"])
        last_dur = float(last["duration"])
        if last_dur >= 0:
            window_end = last_ts + timedelta(seconds=last_dur + pulsetime)
            if last_ts <= hb_ts <= window_end:
                new_dur = max(last_dur, (hb_ts - last_ts).total_seconds() + hb_dur)
                db().execute(
                    "UPDATE events SET duration=? WHERE id=?", (new_dur, last["id"])
                )
                db().execute(
                    "UPDATE buckets SET last_updated=? WHERE id=?", (fmt_ts(now()), bid)
                )
                return {
                    "id": last["id"],
                    "timestamp": last["timestamp"],
                    "duration": new_dur,
                    "data": json.loads(hb_data),
                }
    return insert_event(
        bid,
        {"timestamp": fmt_ts(hb_ts), "duration": hb_dur, "data": json.loads(hb_data)},
    )


# ------------------------------------------------------------------- summaries

AFK_TYPES = ("afkstatus",)


def summary(start=None, end=None, device=None, top=25) -> dict:
    end_dt = parse_ts(end) if end else now()
    start_dt = parse_ts(start) if start else end_dt - timedelta(days=1)
    args_win = (fmt_ts(start_dt), fmt_ts(end_dt))
    buckets = [bucket_row_to_dict(r) for r in db().execute("SELECT * FROM buckets")]
    if device:
        buckets = [b for b in buckets if b["device"] == device]

    per_device: dict = {}
    per_app: dict = {}
    per_type: dict = {}
    intervals: list = []
    for b in buckets:
        rows = (
            db()
            .execute(
                "SELECT timestamp,duration,data FROM events WHERE bucket_id=? "
                "AND timestamp>=? AND timestamp<=?",
                (b["id"], *args_win),
            )
            .fetchall()
        )
        if b["type"] in ("currentwindow", "app.usage", "android.usage"):
            for r in rows:
                t0 = parse_ts(r["timestamp"]).timestamp()
                intervals.append((t0, t0 + float(r["duration"])))
        tot = sum(float(r["duration"]) for r in rows)
        per_type[b["type"]] = per_type.get(b["type"], 0) + tot
        if b["type"] in ("currentwindow", "app.usage", "android.usage"):
            d = per_device.setdefault(b["device"], {"seconds": 0.0, "apps": {}})
            d["seconds"] += tot
            for r in rows:
                data = json.loads(r["data"])
                app = data.get("app") or data.get("package") or data.get("title") or "?"
                d["apps"][app] = d["apps"].get(app, 0) + float(r["duration"])
                per_app[app] = per_app.get(app, 0) + float(r["duration"])
    for d in per_device.values():
        d["apps"] = dict(sorted(d["apps"].items(), key=lambda kv: -kv[1])[:top])
    # wall-clock time where *any* device was active: phone + laptop overlap, so summing
    # per-device totals double-counts. Both numbers are useful; report both.
    union = 0.0
    cur_s = cur_e = None
    for s, e in sorted(intervals):
        if cur_e is None or s > cur_e:
            union += (cur_e - cur_s) if cur_e is not None else 0
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        union += cur_e - cur_s
    return {
        "start": fmt_ts(start_dt),
        "end": fmt_ts(end_dt),
        "union_seconds": union,
        "total_seconds": sum(d["seconds"] for d in per_device.values()),
        "per_device": per_device,
        "per_type": per_type,
        "top_apps": dict(sorted(per_app.items(), key=lambda kv: -kv[1])[:top]),
        "devices": sorted({b["device"] for b in buckets}),
    }


def timeline(start=None, end=None, device=None, limit=2000) -> list:
    end_dt = parse_ts(end) if end else now()
    start_dt = parse_ts(start) if start else end_dt - timedelta(days=1)
    q = (
        "SELECT e.timestamp,e.duration,e.data,b.device,b.type,b.id AS bucket "
        "FROM events e JOIN buckets b ON b.id=e.bucket_id "
        "WHERE e.timestamp>=? AND e.timestamp<=?"
    )
    args = [fmt_ts(start_dt), fmt_ts(end_dt)]
    if device:
        q += " AND b.device=?"
        args.append(device)
    q += " ORDER BY e.timestamp DESC LIMIT ?"
    args.append(int(limit))
    out = []
    for r in db().execute(q, args):
        data = json.loads(r["data"])
        out.append(
            {
                "timestamp": r["timestamp"],
                "duration": r["duration"],
                "device": r["device"],
                "type": r["type"],
                "bucket": r["bucket"],
                "app": data.get("app") or data.get("package"),
                "title": data.get("title") or data.get("classname"),
                "status": data.get("status"),
                "data": data,
            }
        )
    return out


# ---------------------------------------------------------------- HTTP handler


class Handler(BaseHTTPRequestHandler):
    server_version = "kuhytrack"
    protocol_version = "HTTP/1.1"

    # ---- plumbing
    def log_message(self, fmt, *a):
        if os.environ.get("KT_VERBOSE"):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % a))

    def _send(self, code, payload=None, ctype="application/json", raw=None):
        body = raw if raw is not None else json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _authed(self) -> bool:
        if not TOKEN:
            return True
        h = self.headers.get("Authorization", "")
        return h == f"Bearer {TOKEN}" or self.headers.get("X-Api-Key", "") == TOKEN

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_OPTIONS(self):
        self._send(204, raw=b"")

    # ---- routes
    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path.rstrip("/") or "/", parse_qs(u.query)
        if p == "/health":
            return self._send(200, {"ok": True, "version": VERSION})
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        try:
            if p == "/" or p == "/index.html":
                f = WEB_DIR / "dashboard.html"
                if f.exists():
                    return self._send(
                        200, raw=f.read_bytes(), ctype="text/html; charset=utf-8"
                    )
                return self._send(404, {"error": "no dashboard"})
            if p == "/api/0/info":
                return self._send(
                    200,
                    {
                        "hostname": HOSTNAME,
                        "version": VERSION,
                        "testing": False,
                        "device_id": HOSTNAME,
                    },
                )
            if p == "/api/0/buckets":
                return self._send(
                    200,
                    {
                        b["id"]: b
                        for b in (
                            bucket_row_to_dict(r)
                            for r in db().execute("SELECT * FROM buckets")
                        )
                    },
                )
            m = re.fullmatch(r"/api/0/buckets/([^/]+)", p)
            if m:
                b = get_bucket(m.group(1))
                return (
                    self._send(200, b)
                    if b
                    else self._send(404, {"error": "no such bucket"})
                )
            m = re.fullmatch(r"/api/0/buckets/([^/]+)/events", p)
            if m:
                return self._send(
                    200,
                    get_events(
                        m.group(1),
                        limit=int(q.get("limit", [-1])[0]),
                        start=q.get("start", [None])[0],
                        end=q.get("end", [None])[0],
                    ),
                )
            m = re.fullmatch(r"/api/0/buckets/([^/]+)/events/count", p)
            if m:
                r = (
                    db()
                    .execute(
                        "SELECT COUNT(*) c FROM events WHERE bucket_id=?", (m.group(1),)
                    )
                    .fetchone()
                )
                return self._send(200, r["c"])
            if p == "/api/0/export":
                out = {}
                for r in db().execute("SELECT * FROM buckets"):
                    b = bucket_row_to_dict(r)
                    b["events"] = get_events(b["id"])
                    out[b["id"]] = b
                return self._send(200, {"buckets": out})
            if p == "/api/0/kt/summary":
                return self._send(
                    200,
                    summary(
                        q.get("start", [None])[0],
                        q.get("end", [None])[0],
                        q.get("device", [None])[0],
                        int(q.get("top", [25])[0]),
                    ),
                )
            if p == "/api/0/kt/timeline":
                return self._send(
                    200,
                    timeline(
                        q.get("start", [None])[0],
                        q.get("end", [None])[0],
                        q.get("device", [None])[0],
                        int(q.get("limit", [2000])[0]),
                    ),
                )
            if p == "/api/0/kt/habits":
                habits = []
                for r in db().execute(
                    "SELECT * FROM habits WHERE archived=0 ORDER BY id"
                ):
                    ticks = [
                        t["day"]
                        for t in db().execute(
                            "SELECT day FROM habit_ticks WHERE habit_id=? ORDER BY day DESC LIMIT 400",
                            (r["id"],),
                        )
                    ]
                    habits.append(
                        {
                            "id": r["id"],
                            "name": r["name"],
                            "created": r["created"],
                            "meta": json.loads(r["meta"] or "{}"),
                            "ticks": ticks,
                            "streak": streak(ticks),
                        }
                    )
                return self._send(200, habits)
            if p == "/api/0/kt/sessions":
                return self._send(
                    200,
                    [
                        dict(r)
                        for r in db().execute(
                            "SELECT * FROM sessions ORDER BY start DESC LIMIT 500"
                        )
                    ],
                )
            return self._send(404, {"error": "no route", "path": p})
        except ValueError as e:  # bad timestamp/int in the query string
            return self._send(
                400,
                {
                    "error": "bad request",
                    "detail": str(e),
                    "hint": "url-encode ISO offsets: '+' becomes a space",
                },
            )
        except Exception as e:  # noqa: BLE001 — an activity server must never die
            return self._send(500, {"error": type(e).__name__, "detail": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        p, q = u.path.rstrip("/") or "/", parse_qs(u.query)
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        body = self._body()
        try:
            m = re.fullmatch(r"/api/0/buckets/([^/]+)", p)
            if m:
                create_bucket(m.group(1), body if isinstance(body, dict) else {})
                return self._send(200, get_bucket(m.group(1)))
            m = re.fullmatch(r"/api/0/buckets/([^/]+)/events", p)
            if m:
                bid = m.group(1)
                if not get_bucket(bid):
                    return self._send(404, {"error": "no such bucket"})
                evs = body if isinstance(body, list) else [body]
                return self._send(200, [insert_event(bid, e) for e in evs])
            m = re.fullmatch(r"/api/0/buckets/([^/]+)/heartbeat", p)
            if m:
                bid = m.group(1)
                if not get_bucket(bid):
                    return self._send(404, {"error": "no such bucket"})
                pulse = float(q.get("pulsetime", ["60"])[0])
                return self._send(200, heartbeat(bid, body, pulse))
            if p == "/api/0/import":
                n = 0
                for bid, b in (body.get("buckets") or {}).items():
                    create_bucket(bid, b)
                    for e in b.get("events", []):
                        insert_event(bid, e)
                        n += 1
                return self._send(200, {"imported_events": n})
            if p == "/api/0/kt/habits":
                db().execute(
                    "INSERT OR IGNORE INTO habits(name,created,meta) VALUES(?,?,?)",
                    (body["name"], fmt_ts(now()), json.dumps(body.get("meta") or {})),
                )
                r = (
                    db()
                    .execute("SELECT * FROM habits WHERE name=?", (body["name"],))
                    .fetchone()
                )
                return self._send(200, {"id": r["id"], "name": r["name"]})
            m = re.fullmatch(r"/api/0/kt/habits/(\d+)/tick", p)
            if m:
                day = body.get("day") or now().strftime("%Y-%m-%d")
                if body.get("value") == 0:
                    db().execute(
                        "DELETE FROM habit_ticks WHERE habit_id=? AND day=?",
                        (m.group(1), day),
                    )
                else:
                    db().execute(
                        "INSERT OR REPLACE INTO habit_ticks(habit_id,day,value,ts,device)"
                        " VALUES(?,?,?,?,?)",
                        (
                            m.group(1),
                            day,
                            body.get("value", 1),
                            fmt_ts(now()),
                            body.get("device") or HOSTNAME,
                        ),
                    )
                return self._send(200, {"ok": True, "day": day})
            if p == "/api/0/kt/sessions":
                cur = db().execute(
                    "INSERT INTO sessions(kind,tag,device,start,end,notes) VALUES(?,?,?,?,?,?)",
                    (
                        body.get("kind", "focus"),
                        body.get("tag"),
                        body.get("device") or HOSTNAME,
                        fmt_ts(parse_ts(body["start"])),
                        fmt_ts(parse_ts(body["end"])) if body.get("end") else None,
                        body.get("notes"),
                    ),
                )
                return self._send(200, {"id": cur.lastrowid})
            return self._send(404, {"error": "no route", "path": p})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": type(e).__name__, "detail": str(e)})

    def do_DELETE(self):
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        m = re.fullmatch(
            r"/api/0/buckets/([^/]+)", urlparse(self.path).path.rstrip("/")
        )
        if m:
            db().execute("DELETE FROM events WHERE bucket_id=?", (m.group(1),))
            db().execute("DELETE FROM buckets WHERE id=?", (m.group(1),))
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "no route"})


def streak(days: list) -> int:
    """Consecutive-day streak ending today or yesterday."""
    if not days:
        return 0
    s = set(days)
    d = now().date()
    if d.isoformat() not in s:
        d = d - timedelta(days=1)
        if d.isoformat() not in s:
            return 0
    n = 0
    while d.isoformat() in s:
        n += 1
        d -= timedelta(days=1)
    return n


def main():
    db()
    print(
        f"{VERSION} -> http://{HOST}:{PORT}  db={DB_PATH}  auth={'on' if TOKEN else 'OFF'}",
        flush=True,
    )
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
