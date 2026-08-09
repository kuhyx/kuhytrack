#!/usr/bin/env python3
"""kt-import — pull other trackers' data into kuhytrack so there is one store.

  kt-import.py aw --file aw-buckets-export.json --device arch
  kt-import.py aw --from http://127.0.0.1:5600 --device oldlaptop   # live aw-server
  kt-import.py awdb --file ~/.local/share/activitywatch/aw-server/peewee-sqlite.v2.db
  kt-import.py wakapi --url https://wakapi.example --key <api key> --days 30

Idempotent-ish: aw import is keyed on bucket+timestamp and skips events already present;
wakapi import rewrites the last N days of its own bucket instead of appending duplicates.
Env: KT_URL, KT_TOKEN
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cli"))
import ktconf  # noqa: E402  (path must be set first; stdlib-only, no package install)

KT = ktconf.url()
TOKEN = ktconf.token()


def kt_call(path, method="GET", body=None):
    req = urllib.request.Request(
        KT + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


def get_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


# ------------------------------------------------------------------ aw import


def import_aw(args):
    if args.file:
        raw = json.loads(open(args.file).read())
        buckets = raw.get("buckets", raw)
    else:
        buckets = get_json(args.from_url.rstrip("/") + "/api/0/export")["buckets"]

    total = 0
    for bid, b in buckets.items():
        dev = args.device or b.get("hostname") or "imported"
        dst = f"{bid}_{dev}" if not bid.endswith(dev) else bid
        kt_call(
            f"/api/0/buckets/{dst}",
            "POST",
            {
                "client": b.get("client", "aw-import"),
                "type": b.get("type", "currentwindow"),
                "hostname": dev,
                "device": dev,
            },
        )
        existing = {
            e["timestamp"] for e in kt_call(f"/api/0/buckets/{dst}/events?limit=-1")
        }
        evs = [
            {
                "timestamp": e["timestamp"],
                "duration": e.get("duration", 0),
                "data": e["data"],
            }
            for e in b.get("events", [])
            if e["timestamp"] not in existing
        ]
        for i in range(0, len(evs), 500):
            kt_call(f"/api/0/buckets/{dst}/events", "POST", evs[i : i + 500])
        print(
            f"  {bid:<44} -> {dst:<46} +{len(evs)} (skipped {len(b.get('events', [])) - len(evs)})"
        )
        total += len(evs)
    print(f"imported {total} events")


# -------------------------------------------------------------- wakapi import


def import_wakapi(args):
    """wakapi speaks the WakaTime API. /api/compat/wakatime/v1/users/current/summaries
    gives per-day project/language/editor totals. We store one event per project per day,
    typed app.editor.activity so it lines up with aw-watcher-vscode data if you add it."""
    key = base64.b64encode(args.key.encode()).decode()
    hdr = {"Authorization": f"Basic {key}"}
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=args.days)
    url = (
        args.url.rstrip("/")
        + "/api/compat/wakatime/v1/users/current/summaries?"
        + urllib.parse.urlencode({"start": start.isoformat(), "end": end.isoformat()})
    )
    data = get_json(url, hdr)

    bucket = f"kt-import-wakapi_{args.device}"
    kt_call(
        f"/api/0/buckets/{bucket}",
        "POST",
        {
            "client": "kt-import-wakapi",
            "type": "app.editor.activity",
            "hostname": args.device,
            "device": args.device,
        },
    )
    existing = {
        e["timestamp"] for e in kt_call(f"/api/0/buckets/{bucket}/events?limit=-1")
    }
    evs = []
    for day in data.get("data", []):
        d = day["range"]["date"]
        for proj in day.get("projects", []):
            ts = f"{d}T12:00:00.000Z"
            key_ts = ts
            if key_ts in existing and not args.force:
                continue
            langs = ", ".join(x["name"] for x in day.get("languages", [])[:3])
            evs.append(
                {
                    "timestamp": ts,
                    "duration": proj["total_seconds"],
                    "data": {
                        "app": "code",
                        "project": proj["name"],
                        "language": langs,
                        "title": proj["name"],
                        "source": "wakapi",
                    },
                }
            )
    if evs:
        kt_call(f"/api/0/buckets/{bucket}/events", "POST", evs)
    print(f"imported {len(evs)} project-days from wakapi into {bucket}")


# ------------------------------------------------------- aw sqlite (raw db) import


def _bucket_window(dst):
    """Existing (min, max) timestamp in a target bucket, or (None, None).

    Deliberately NOT the `GET /events?limit=-1` dedupe that import_aw uses: a real
    aw-server db is ~137k events, which comes back as ~22 MB of JSON in a single body
    through stdlib http.server. Two aggregate queries cost nothing and make re-runs
    O(1) in payload instead of O(events).
    """
    try:
        r = kt_call(f"/api/0/buckets/{dst}/events?limit=1")
        if not r:
            return None, None
        newest = r[0]["timestamp"]
        oldest = kt_call(
            f"/api/0/buckets/{dst}/events?limit=-1&end={urllib.parse.quote(newest)}"
        )
        return (oldest[-1]["timestamp"] if oldest else newest), newest
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise


def import_awdb(args):
    """Import directly from an aw-server sqlite file (peewee-sqlite.v2.db).

    aw-server stores events as bucketmodel(key,id,type,client,hostname) joined to
    eventmodel(bucket_id,timestamp,duration,datastr). Opened read-only via a URI so the
    source database is never modified, and safe to run while aw-server is stopped --
    which is the normal case, since the alternative importers need it running.
    """
    import sqlite3

    src = Path(args.file).expanduser()
    if not src.exists():
        sys.exit(f"no such file: {src}")
    con = sqlite3.connect(f"file:{urllib.parse.quote(str(src))}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    cols = {r[1] for r in con.execute("PRAGMA table_info(bucketmodel)")}
    keycol = "key" if "key" in cols else "id"

    total = 0
    for b in con.execute(
        f"SELECT {keycol} AS pk, id, type, client, hostname FROM bucketmodel"
    ):
        dev = args.device or b["hostname"] or "imported"
        dst = b["id"] if b["id"].endswith(dev) else f"{b['id']}_{dev}"
        kt_call(
            f"/api/0/buckets/{dst}",
            "POST",
            {
                "client": b["client"] or "aw-import",
                "type": b["type"] or "currentwindow",
                "hostname": dev,
                "device": dev,
            },
        )

        lo, hi = _bucket_window(dst)
        n = skipped = 0
        batch = []
        for e in con.execute(
            "SELECT timestamp,duration,datastr FROM eventmodel WHERE bucket_id=?"
            " ORDER BY timestamp",
            (b["pk"],),
        ):
            ts = _norm_ts(e["timestamp"])
            # Anything inside the range already imported is a re-run; skip it. Events
            # outside that window (older backfill, newer data) still import.
            if lo and hi and lo <= _cmp_ts(ts) <= hi:
                skipped += 1
                continue
            try:
                data = json.loads(e["datastr"] or "{}")
            except json.JSONDecodeError:
                skipped += 1
                continue
            batch.append(
                {"timestamp": ts, "duration": e["duration"] or 0, "data": data}
            )
            if len(batch) >= 500:
                kt_call(f"/api/0/buckets/{dst}/events", "POST", batch)
                n += len(batch)
                batch = []
        if batch:
            kt_call(f"/api/0/buckets/{dst}/events", "POST", batch)
            n += len(batch)
        print(f"  {b['id']:<40} -> {dst:<42} +{n} (skipped {skipped})")
        total += n
    con.close()
    print(f"imported {total} events")


def _norm_ts(s):
    """aw-server writes '2025-07-07 08:29:07.636000+00:00'; the API wants ISO-T form."""
    return str(s).strip().replace(" ", "T")


def _cmp_ts(s):
    """Normalise to the server's own render ('...T08:29:07.636Z') for range comparison.

    The two formats are not string-comparable: '.060000+00:00' sorts BEFORE '.060Z'
    because '0' < 'Z', so the oldest event escaped an inclusive window and re-imported
    itself on every run. Compare datetimes, formatted the way the server stores them.
    """
    dt = datetime.fromisoformat(_norm_ts(s).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


ap = argparse.ArgumentParser(
    description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
)
sub = ap.add_subparsers(dest="cmd", required=True)
p = sub.add_parser("aw")
p.add_argument("--file")
p.add_argument("--from", dest="from_url")
p.add_argument("--device")
p.set_defaults(fn=import_aw)
p = sub.add_parser("awdb", help="import straight from an aw-server sqlite file")
p.add_argument(
    "--file",
    required=True,
    help="~/.local/share/activitywatch/aw-server/peewee-sqlite.v2.db",
)
p.add_argument("--device")
p.set_defaults(fn=import_awdb)
p = sub.add_parser("wakapi")
p.add_argument("--url", required=True)
p.add_argument("--key", required=True)
p.add_argument("--days", type=int, default=30)
p.add_argument("--device", default="code")
p.add_argument("--force", action="store_true")
p.set_defaults(fn=import_wakapi)
a = ap.parse_args()
if a.cmd == "aw" and not (a.file or a.from_url):
    sys.exit("aw import needs --file or --from")
a.fn(a)
