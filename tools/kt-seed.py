#!/usr/bin/env python3
"""kt-seed — fill a kuhytrack server with a plausible arch+pixel day, so you can look at
the dashboard before you've collected anything real. Safe: writes to buckets suffixed
_demo unless --real. Usage: KT_URL=... KT_TOKEN=... python3 kt-seed.py [--days 3]"""

import argparse
import importlib.util
import json
import random
import urllib.request
from datetime import datetime, time as dtime, timedelta
from pathlib import Path


def _load_ktconf():
    """Load the shared config module by path -- see cli/ktconf.py."""
    spec = importlib.util.spec_from_file_location(
        "ktconf", Path(__file__).resolve().parent.parent / "cli" / "ktconf.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ktconf = _load_ktconf()
URL = ktconf.url()
TOKEN = ktconf.token()
ap = argparse.ArgumentParser()
ap.add_argument("--days", type=int, default=1)
ap.add_argument("--real", action="store_true", help="use real device names, not _demo")
a = ap.parse_args()
SUF = "" if a.real else "_demo"

ARCH = [
    ("kitty", "nvim src/kt_server.py", 900),
    ("firefox", "github.com/kuhyx", 600),
    ("claude", "Claude Code", 1500),
    ("Slack", "#eng", 300),
    ("kitty", "zsh", 240),
    ("firefox", "awesome-selfhosted", 420),
    ("obsidian", "notes", 300),
]
PIXEL = [
    ("com.android.chrome", "", 300),
    ("org.thoughtcrime.securesms", "", 180),
    ("com.google.android.youtube", "", 900),
    ("com.spotify.music", "", 600),
    ("com.reddit.frontpage", "", 480),
]


def call(path, body):
    r = urllib.request.Request(
        URL + path,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
        },
    )
    urllib.request.urlopen(r, timeout=10).read()


def gen(device, apps, day_start, awake_hours):
    win, afk = f"kt-watcher-window_{device}{SUF}", f"kt-watcher-afk_{device}{SUF}"
    for bid, t, c in ((win, "currentwindow", "kt-seed"), (afk, "afkstatus", "kt-seed")):
        call(
            f"/api/0/buckets/{bid}",
            {"type": t, "client": c, "hostname": device + SUF, "device": device + SUF},
        )
    t = day_start + timedelta(hours=awake_hours[0])
    endt = day_start + timedelta(hours=awake_hours[1])
    evs, afks = [], []
    while t < endt:
        app, title, base = random.choice(apps)
        dur = max(30, int(random.gauss(base, base * 0.4)))
        evs.append(
            {
                "timestamp": t.isoformat(),
                "duration": dur,
                "data": {"app": app, "title": title}
                if not app.startswith("com.")
                else {"app": app, "package": app, "classname": ".Main", "title": ""},
            }
        )
        afks.append(
            {"timestamp": t.isoformat(), "duration": dur, "data": {"status": "not-afk"}}
        )
        t += timedelta(seconds=dur)
        if random.random() < 0.15:  # a break
            g = random.randint(300, 2400)
            afks.append(
                {"timestamp": t.isoformat(), "duration": g, "data": {"status": "afk"}}
            )
            t += timedelta(seconds=g)
    call(f"/api/0/buckets/{win}/events", evs)
    call(f"/api/0/buckets/{afk}/events", afks)
    return len(evs)


total = 0
for d in range(a.days):
    day = datetime.combine(
        (datetime.now().astimezone() - timedelta(days=d)).date(), dtime.min
    ).astimezone()
    total += gen("arch", ARCH, day, (9, 19))
    total += gen("pixel6a", PIXEL, day, (7.5, 23))
print(
    f"seeded {total} events over {a.days} day(s) -> {URL}  (buckets suffixed '{SUF or '(real)'}')"
)
