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
sudo pacman -S --needed xprintidle   # see "idle detection" below — do this first
./deploy/install-arch.sh            # server + watcher as systemd --user units
KT_BIND=tailscale ./deploy/install-arch.sh   # if the phone should reach it
python3 tools/kt-seed.py --days 3   # fake data so the dashboard isn't empty
ktq today
```

Config lands in `~/.config/kuhytrack/env` (token generated once, `chmod 600`). The CLI
tools read it directly, so `ktq today` works in any shell without sourcing anything.
Dashboard at `http://<host>:5600/`, token via the "set token" button. The dashboard
*shell* is served unauthenticated so that button is reachable; every `/api/0/*` route
behind it still requires the token.

**Import your existing ActivityWatch history** (reads the sqlite file directly,
read-only, and dedupes on re-run):

```bash
python3 importers/kt-import.py awdb --file ~/.local/share/activitywatch/aw-server/peewee-sqlite.v2.db
```

### Idle detection is the thing that breaks capture

The watcher only records windows while you are *not* AFK, so a broken idle source means
an empty database, not just a wrong AFK number. On X11 install `xprintidle`. The
`/dev/input` fallback the docs suggest is **not** a reliable substitute: it reads
`st_atime`, and `/dev` is normally mounted `relatime`, so the timestamp is frozen at boot
and the watcher concludes you have been idle for hours. Being in group `input` does not
change that. Check with `journalctl --user -u kuhytrack-watcher`: the startup line must
read `idle source: xprintidle`, and a `!!` line means no window source was found at all.

## Files

| path | what |
|---|---|
| `server/kt_server.py` | the whole server: buckets, events, heartbeat merge, habits, sessions, summaries |
| `linux/kt-watcher-linux.py` | window + AFK. Hyprland → sway → X11 → KWin; 5 idle sources; offline spool |
| `android/kt-watcher-android.sh` | Termux + Magisk root. Live foreground app + screen state |
| `android/kt-bridge-awandroid.sh` | no root: pulls aw-android's loopback API, pushes here, incrementally |
| `cli/ktq.py` | `today` `day -1` `range --days 7` `apps` `timeline` `devices` `habits` `tick` `raw` |
| `cli/ktconf.py` | env → `~/.config/kuhytrack/env` → default, shared by all four client tools |
| `web/dashboard.html` | one file, no CDN, no webfonts; 24h tape per device |
| `hooks/kt-budget.py` | daily per-app budgets → runs a command once when blown |
| `importers/kt-import.py` | `aw` (export file or live server), `awdb` (raw aw-server sqlite), `wakapi`; all dedupe |
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
| port 5600, same as ActivityWatch | deliberate (aw watchers point here unchanged), but the two cannot run at once. If you later start `aw-qt` it will fail to bind — pick one, or move kuhytrack with `KT_PORT` |

## Verified in this run

`python3 tests/test_kt.py` → **19 passed**, covering: heartbeat merge on identical data,
non-merge on differing data, the *inclusive* pulsetime boundary, `max()` duration
semantics, key-order-independent data equality, zero-pulsetime contiguity, per-device
isolation, out-of-order backfill, interval-union correctness, timestamp format tolerance,
401 without token, 404 on unknown bucket, full watcher flow over HTTP, habit streaks.

Also exercised end-to-end: seed 811 events → `ktq today/devices/timeline/--json` →
`/api/0/export` → re-import into a second server (811 in, 0 duplicates on a second run) →
budget hook fires once and not twice.

### Corrections from the first real deployment (2026-08-09)

Three claims above did not survive contact with an actual install. All three had been
"verified" through a path the real deployment does not take — worth knowing about, since
it is the failure mode this file is most likely to repeat.

| claimed | actually |
|---|---|
| "dashboard served (HTTP 200, 10.8 KB)" | measured with `curl -H "Authorization: Bearer …"`. A browser sends no such header, got 401, and could never reach the "set token" button. Fixed: the shell is public, the API is not |
| the installer installs the system | `hooks/` and `android/` were missing from its copy list, under `2>/dev/null \|\| true`. `kt-budget.py` — "the only file here that can actually change your week" — was never installed and no timer ran it |
| `kt-import.py aw` imports ActivityWatch | it takes a JSON export or a live server. The normal case is a stopped aw-server and a raw sqlite file, which it could not read. Added `awdb` |

**Now verified on real hardware** (Arch, i3/X11, 2026-08-09): both systemd units active;
watcher reporting `window source: x11` / `idle source: xprintidle` and recording live
window events; dashboard rendering in Chrome with no token, then charting after the token
is set; 137,133 real events imported from a 28 MB aw-server database in 7s, with a re-run
importing 0.

**Still not verified, because it needs the phone:** both Android scripts, and any
tailnet bind. `tailscale status` must not say "Logged out" — the installer now refuses
`KT_BIND=tailscale` rather than silently binding loopback and looking like it worked.
