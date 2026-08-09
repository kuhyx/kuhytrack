# kuhytrack

ActivityWatch's data model, one central server, dumb spooling watchers on every device.
Python stdlib only — no pip, no docker, no runtime that can rot.

```
  arch (hyprland/sway/X11)                    pixel 6a (termux)
  kt-watcher-linux.py                         kt-watcher-android.sh   (root)
        │ heartbeat + local spool                   │ or kt-bridge-awandroid.sh (no root)
        └──────────────► kt_server.py ◄─────────────┘
                         :5600 (tailnet only)
                         sqlite + /api/0 (AW-compatible) + /api/0/kt/*
                              │
              dashboard ──────┼────── ktq (cli) ────── kt-budget.py ──► screen-locker
```

## Quickstart

```bash
./deploy/install-arch.sh            # server + watcher as systemd --user units
KT_BIND=tailscale ./deploy/install-arch.sh   # if the phone should reach it
python3 tools/kt-seed.py --days 3   # fake data so the dashboard isn't empty
ktq today
```

Config lands in `~/.config/kuhytrack/env` (token generated once, `chmod 600`).
Dashboard at `http://<host>:5600/`, token via the "set token" button.

## Files

| path | what |
|---|---|
| `server/kt_server.py` | the whole server: buckets, events, heartbeat merge, habits, sessions, summaries |
| `linux/kt-watcher-linux.py` | window + AFK. Hyprland → sway → X11 → KWin; 5 idle sources; offline spool |
| `android/kt-watcher-android.sh` | Termux + Magisk root. Live foreground app + screen state |
| `android/kt-bridge-awandroid.sh` | no root: pulls aw-android's loopback API, pushes here, incrementally |
| `cli/ktq.py` | `today` `day -1` `range --days 7` `apps` `timeline` `devices` `habits` `tick` `raw` |
| `web/dashboard.html` | one file, no CDN, no webfonts; 24h tape per device |
| `hooks/kt-budget.py` | daily per-app budgets → runs a command once when blown |
| `importers/kt-import.py` | `aw` (file or live server) and `wakapi` importers, both dedupe |
| `tools/kt-seed.py` | plausible fake data for a 2-device day |
| `tests/test_kt.py` | 19 tests, stdlib unittest |

## API

Everything ActivityWatch serves at `/api/0` (see `01-activitywatch-teardown.md`), plus:

```
GET  /api/0/kt/summary?start=&end=&device=&top=     union_seconds, per_device, top_apps, per_type
GET  /api/0/kt/timeline?start=&end=&device=&limit=
GET  /api/0/kt/habits          POST /api/0/kt/habits        {"name": "..."}
POST /api/0/kt/habits/<id>/tick   {"day": "YYYY-MM-DD", "value": 1|0}
GET  /api/0/kt/sessions        POST /api/0/kt/sessions      {kind,tag,start,end,notes}
GET  /health                                        (unauthenticated, for systemd/uptime)
```

**`union_seconds` vs `total_seconds`:** phone and laptop overlap constantly. Summing
per-device totals double-counts — the demo day reads 20h33m of device-hours against
14h04m of wall clock. `union_seconds` merges intervals across devices and is what the
dashboard and `ktq` show as "active".

## Design decisions, and what they cost

| decision | cost |
|---|---|
| stdlib `http.server`, no framework | no async, ~hundreds of req/s ceiling. Irrelevant at 3 devices |
| one central server, no peer sync | the box must be up, or watchers spool to disk until it is |
| AW wire protocol | stuck with bucket-per-watcher naming and the `data`-equality merge rule |
| sqlite, no retention policy | see the growth math in `04-critique.md`; add a cron `DELETE` if titles churn |
| bearer token, no users | LAN/tailnet only. Never expose publicly |

## Verified in this run

`python3 tests/test_kt.py` → **19 passed**, covering: heartbeat merge on identical data,
non-merge on differing data, the *inclusive* pulsetime boundary, `max()` duration
semantics, key-order-independent data equality, zero-pulsetime contiguity, per-device
isolation, out-of-order backfill, interval-union correctness, timestamp format tolerance,
401 without token, 404 on unknown bucket, full watcher flow over HTTP, habit streaks.

Also exercised end-to-end: seed 811 events → `ktq today/devices/timeline/--json` →
dashboard served (HTTP 200, 10.8 KB) → `/api/0/export` → re-import into a second server
(811 in, 0 duplicates on a second run) → budget hook fires once and not twice.

**Not verified, because it needs your hardware:** the Linux watcher against a real
Hyprland/sway/X11 session, and both Android scripts on the Pixel. The window and idle
source detection is written to fail loudly with the fix printed rather than silently
report zeros — if `kt-watcher-linux.py` prints a `!!` line, that line is the thing to act
on.
