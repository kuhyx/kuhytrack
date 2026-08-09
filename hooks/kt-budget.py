#!/usr/bin/env python3
"""kt-budget — the part that makes tracking worth doing: act on it.

A tracker you only read is a diary. This turns kuhytrack into a controller: define
per-app or per-tag daily budgets, and when one is blown, run a command. Designed to
drive github.com/kuhyx/screen-locker as the enforcement backend, but the action is
just a shell command, so it can equally send a notification or kill a process.

  kt-budget.py --check                       # print status, exit 0
  kt-budget.py --enforce                     # run actions for blown budgets
  kt-budget.py --enforce --watch 300         # loop every 5 min (use a systemd timer instead)

Config: ~/.config/kuhytrack/budgets.json — written with defaults on first run.
Exit codes: 0 all within budget, 10 at least one blown (useful in a timer/hook).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

KT = os.environ.get("KT_URL", "http://127.0.0.1:5600").rstrip("/")
TOKEN = os.environ.get("KT_TOKEN", "")
CONF = Path(os.environ.get("KT_BUDGETS",
                           Path.home() / ".config/kuhytrack/budgets.json"))

DEFAULTS = {
    "_comment": "match is a list of substrings tested against the app/package name. "
                "minutes is the daily budget. action runs once when it is first exceeded "
                "today; {app} {used} {budget} are substituted.",
    "budgets": [
        {"name": "doomscroll", "device": "pixel6a",
         "match": ["reddit", "youtube", "twitter", "instagram", "tiktok"],
         "minutes": 60,
         "action": "curl -sf -X POST http://127.0.0.1:8765/lock -d 'reason={app} {used}m'"},
        {"name": "browser", "device": "arch", "match": ["firefox", "chromium"],
         "minutes": 120,
         "action": "notify-send 'kuhytrack' '{app}: {used}m of {budget}m used'"},
    ],
}


def api(path):
    req = urllib.request.Request(
        KT + path, headers={"Authorization": f"Bearer {TOKEN}"} if TOKEN else {})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def today_window():
    d = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return d.astimezone(timezone.utc), (d + timedelta(days=1)).astimezone(timezone.utc)


def load_conf():
    if not CONF.exists():
        CONF.parent.mkdir(parents=True, exist_ok=True)
        CONF.write_text(json.dumps(DEFAULTS, indent=2))
        print(f"wrote default budgets to {CONF} — edit it, then re-run", file=sys.stderr)
    return json.loads(CONF.read_text())


def fired_path(name):
    return Path.home() / f".cache/kuhytrack/fired-{datetime.now().date()}-{name}"


def run(enforce: bool) -> int:
    conf = load_conf()
    start, end = today_window()
    qs = urllib.parse.urlencode({"start": start.isoformat(), "end": end.isoformat(), "top": 200})
    s = api("/api/0/kt/summary?" + qs)
    blown = 0
    for b in conf["budgets"]:
        pool = (s["per_device"].get(b["device"], {}) or {}).get("apps", {}) if b.get("device") \
            else s["top_apps"]
        used = sum(v for k, v in pool.items()
                   if any(m.lower() in k.lower() for m in b["match"]))
        used_m, budget_m = used / 60, b["minutes"]
        over = used_m >= budget_m
        bar = "!" if over else "."
        print(f"  {bar} {b['name']:<14} {used_m:6.1f}m / {budget_m}m"
              f"  [{b.get('device', 'all')}]")
        if not over:
            continue
        blown += 1
        f = fired_path(b["name"])
        if enforce and not f.exists():
            top = max(((k, v) for k, v in pool.items()
                       if any(m.lower() in k.lower() for m in b["match"])),
                      key=lambda kv: kv[1], default=("?", 0))[0]
            cmd = b["action"].format(app=top, used=int(used_m), budget=budget_m)
            print(f"    -> {cmd}")
            subprocess.run(cmd, shell=True, check=False)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(cmd)
    return 10 if blown else 0


if __name__ == "__main__":
    enforce = "--enforce" in sys.argv
    watch = 0
    if "--watch" in sys.argv:
        watch = int(sys.argv[sys.argv.index("--watch") + 1])
    while True:
        code = run(enforce)
        if not watch:
            sys.exit(code)
        time.sleep(watch)
