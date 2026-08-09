#!/usr/bin/env python3
"""ktq — query kuhytrack from the terminal. Zero deps.

  ktq today                      # today, all devices
  ktq day -3                     # three days ago
  ktq range --days 7 --top 15
  ktq apps --device pixel6a --days 7
  ktq timeline --limit 40
  ktq devices
  ktq habits
  ktq tick "5x5 squats"
  ktq raw /api/0/kt/summary?days=1
  add --json to any command for machine-readable output

Env: KT_URL (default http://127.0.0.1:5600), KT_TOKEN
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ktconf  # noqa: E402  (path must be set first; stdlib-only, no package install)

URL = ktconf.url()
TOKEN = ktconf.token()


def api(path, method="GET", body=None):
    req = urllib.request.Request(
        URL + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:200]}")
    except OSError as e:
        sys.exit(f"cannot reach {URL}: {e}")


def hms(sec: float) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}h{m:02d}m" if h else (f"{m}m{s:02d}s" if m else f"{s}s")


def bar(frac: float, width=22) -> str:
    n = max(0, min(width, round(frac * width)))
    return "█" * n + "·" * (width - n)


def local_day(offset_days=0):
    d = (datetime.now().astimezone() + timedelta(days=offset_days)).date()
    start = datetime.combine(d, dtime.min).astimezone()
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(
        timezone.utc
    )


def win(args):
    if getattr(args, "days", None):
        end = datetime.now(timezone.utc)
        return end - timedelta(days=args.days), end
    return local_day(getattr(args, "offset", 0))


def q(start, end, **kw):
    p = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        **{k: v for k, v in kw.items() if v},
    }
    return "?" + urllib.parse.urlencode(p)


def print_summary(s, top):
    total = s["total_seconds"]
    union = s.get("union_seconds", total)
    print(
        f"\n  {s['start'][:16]} → {s['end'][:16]}   active {hms(union)}"
        + (f"  (device-hours {hms(total)})" if round(total) != round(union) else "")
        + f"   devices: {', '.join(s['devices']) or '—'}"
    )
    for dev, d in sorted(s["per_device"].items(), key=lambda kv: -kv[1]["seconds"]):
        share = d["seconds"] / total if total else 0
        print(f"\n  {dev:<14} {hms(d['seconds']):>8}  {bar(share)} {share * 100:4.1f}%")
        mx = max(d["apps"].values(), default=1)
        for app, sec in list(d["apps"].items())[:top]:
            print(f"      {app[:34]:<34} {hms(sec):>8}  {bar(sec / mx, 14)}")
    if len(s["per_device"]) > 1 and s["top_apps"]:
        print("\n  across all devices:")
        mx = max(s["top_apps"].values())
        for app, sec in list(s["top_apps"].items())[:top]:
            print(f"      {app[:34]:<34} {hms(sec):>8}  {bar(sec / mx, 14)}")
    print()


def main():
    ap = argparse.ArgumentParser(
        prog="ktq",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--json", action="store_true")
    # --json must work both before and after the subcommand; SUPPRESS stops the
    # subparser default from clobbering a flag given up front.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    sub = ap.add_subparsers(
        dest="cmd",
        required=True,
        parser_class=lambda **k: argparse.ArgumentParser(parents=[common], **k),
    )

    def add_win(p, days_default=None):
        p.add_argument("--days", type=int, default=days_default)
        p.add_argument("--device")
        p.add_argument("--top", type=int, default=10)

    add_win(sub.add_parser("today"))
    p = sub.add_parser("day")
    p.add_argument("offset", type=int, nargs="?", default=0)
    add_win(p)
    add_win(sub.add_parser("range"), days_default=7)
    add_win(sub.add_parser("apps"), days_default=1)
    p = sub.add_parser("timeline")
    add_win(p, 1)
    p.add_argument("--limit", type=int, default=50)
    sub.add_parser("devices")
    sub.add_parser("habits")
    p = sub.add_parser("tick")
    p.add_argument("habit")
    p.add_argument("--day")
    p = sub.add_parser("raw")
    p.add_argument("path")
    args = ap.parse_args()

    if args.cmd == "raw":
        print(json.dumps(api(args.path), indent=2))
        return
    if args.cmd == "devices":
        b = api("/api/0/buckets/")
        devs: dict = {}
        for bid, meta in b.items():
            devs.setdefault(meta["device"], []).append(
                (bid, meta["type"], meta["last_updated"])
            )
        if args.json:
            print(json.dumps(devs, indent=2))
            return
        for d, rows in devs.items():
            print(f"\n  {d}")
            for bid, t, upd in sorted(rows):
                age = "?"
                try:
                    age = hms(
                        (
                            datetime.now(timezone.utc)
                            - datetime.fromisoformat(upd.replace("Z", "+00:00"))
                        ).total_seconds()
                    )
                except Exception:
                    pass
                print(f"      {bid:<38} {t:<16} last seen {age} ago")
        print()
        return
    if args.cmd == "habits":
        hs = api("/api/0/kt/habits")
        if args.json:
            print(json.dumps(hs, indent=2))
            return
        today = datetime.now().date().isoformat()
        for h in hs:
            mark = "✓" if today in h["ticks"] else "·"
            print(
                f"  {mark} {h['name']:<28} streak {h['streak']:>3}d   {len(h['ticks'])} total"
            )
        print()
        return
    if args.cmd == "tick":
        hs = api("/api/0/kt/habits")
        match = [h for h in hs if h["name"].lower() == args.habit.lower()]
        if not match:
            match = [api("/api/0/kt/habits", "POST", {"name": args.habit})]
            print(f"  created habit: {args.habit}")
        r = api(
            f"/api/0/kt/habits/{match[0]['id']}/tick",
            "POST",
            {"day": args.day} if args.day else {},
        )
        print(f"  ✓ {args.habit} — {r['day']}")
        return

    start, end = win(args)
    if args.cmd == "timeline":
        rows = api(
            "/api/0/kt/timeline" + q(start, end, device=args.device, limit=args.limit)
        )
        if args.json:
            print(json.dumps(rows, indent=2))
            return
        for r in rows:
            label = r["app"] or r["status"] or r["type"]
            ts = r["timestamp"][11:19]
            print(
                f"  {ts}  {r['device']:<10} {hms(r['duration']):>7}  {str(label)[:30]:<30} "
                f"{(r['title'] or '')[:40]}"
            )
        print()
        return

    s = api("/api/0/kt/summary" + q(start, end, device=args.device, top=args.top))
    if args.json:
        print(json.dumps(s, indent=2))
        return
    print_summary(s, args.top)


if __name__ == "__main__":
    main()
