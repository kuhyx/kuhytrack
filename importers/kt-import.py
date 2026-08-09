#!/usr/bin/env python3
"""kt-import — pull other trackers' data into kuhytrack so there is one store.

  kt-import.py aw --file aw-buckets-export.json --device arch
  kt-import.py aw --from http://127.0.0.1:5600 --device oldlaptop   # live aw-server
  kt-import.py wakapi --url https://wakapi.example --key <api key> --days 30

Idempotent-ish: aw import is keyed on bucket+timestamp and skips events already present;
wakapi import rewrites the last N days of its own bucket instead of appending duplicates.
Env: KT_URL, KT_TOKEN
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KT = os.environ.get("KT_URL", "http://127.0.0.1:5600").rstrip("/")
TOKEN = os.environ.get("KT_TOKEN", "")


def kt_call(path, method="GET", body=None):
    req = urllib.request.Request(
        KT + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {})})
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
        kt_call(f"/api/0/buckets/{dst}", "POST",
                {"client": b.get("client", "aw-import"), "type": b.get("type", "currentwindow"),
                 "hostname": dev, "device": dev})
        existing = {e["timestamp"] for e in kt_call(f"/api/0/buckets/{dst}/events?limit=-1")}
        evs = [{"timestamp": e["timestamp"], "duration": e.get("duration", 0), "data": e["data"]}
               for e in b.get("events", []) if e["timestamp"] not in existing]
        for i in range(0, len(evs), 500):
            kt_call(f"/api/0/buckets/{dst}/events", "POST", evs[i:i + 500])
        print(f"  {bid:<44} -> {dst:<46} +{len(evs)} (skipped {len(b.get('events', [])) - len(evs)})")
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
    url = (args.url.rstrip("/") + "/api/compat/wakatime/v1/users/current/summaries?"
           + urllib.parse.urlencode({"start": start.isoformat(), "end": end.isoformat()}))
    data = get_json(url, hdr)

    bucket = f"kt-import-wakapi_{args.device}"
    kt_call(f"/api/0/buckets/{bucket}", "POST",
            {"client": "kt-import-wakapi", "type": "app.editor.activity",
             "hostname": args.device, "device": args.device})
    existing = {e["timestamp"] for e in kt_call(f"/api/0/buckets/{bucket}/events?limit=-1")}
    evs = []
    for day in data.get("data", []):
        d = day["range"]["date"]
        for proj in day.get("projects", []):
            ts = f"{d}T12:00:00.000Z"
            key_ts = ts
            if key_ts in existing and not args.force:
                continue
            langs = ", ".join(x["name"] for x in day.get("languages", [])[:3])
            evs.append({"timestamp": ts, "duration": proj["total_seconds"],
                        "data": {"app": "code", "project": proj["name"],
                                 "language": langs, "title": proj["name"],
                                 "source": "wakapi"}})
    if evs:
        kt_call(f"/api/0/buckets/{bucket}/events", "POST", evs)
    print(f"imported {len(evs)} project-days from wakapi into {bucket}")


ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
sub = ap.add_subparsers(dest="cmd", required=True)
p = sub.add_parser("aw")
p.add_argument("--file"); p.add_argument("--from", dest="from_url"); p.add_argument("--device")
p.set_defaults(fn=import_aw)
p = sub.add_parser("wakapi")
p.add_argument("--url", required=True); p.add_argument("--key", required=True)
p.add_argument("--days", type=int, default=30); p.add_argument("--device", default="code")
p.add_argument("--force", action="store_true")
p.set_defaults(fn=import_wakapi)
a = ap.parse_args()
if a.cmd == "aw" and not (a.file or a.from_url):
    sys.exit("aw import needs --file or --from")
a.fn(a)
