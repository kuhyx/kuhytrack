# How ActivityWatch actually works, and where it breaks for you

## The whole system in one paragraph

Dumb watchers push *heartbeats* to a local HTTP server. The server appends them into
*buckets*, merging consecutive heartbeats that carry identical `data` into one long
event. That is the entire model. Everything else — the Vue web UI, the aw-query DSL,
the category tree — is a reader on top of `(bucket, timestamp, duration, data)`.

| piece | what it is | runs where |
|---|---|---|
| `aw-server` / `aw-server-rust` | REST API + sqlite | one per device, `localhost:5600` |
| `aw-watcher-window` | polls the focused window every ~1s, heartbeats `{app,title}` | per desktop |
| `aw-watcher-afk` | polls input idle time, heartbeats `{status: afk\|not-afk}` | per desktop |
| `aw-watcher-web` | browser extension, heartbeats `{url,title,audible,incognito}` | browser |
| `aw-qt` | tray process that supervises the above | per desktop |
| `aw-android` | Kotlin app reading `UsageStatsManager`, pushing into an **embedded** aw-server-rust | phone |

## The heartbeat rule (this is the part everyone reimplements wrong)

From `aw-core/aw_transform/heartbeats.py`, fetched from source during this run:

```
merge(last, hb, pulsetime) iff  last.data == hb.data
                          and  last.ts <= hb.ts <= last.ts + last.duration + pulsetime
new duration = max(last.duration, (hb.ts - last.ts) + hb.duration)
```

Three consequences worth internalising:

1. **The bound is inclusive.** A heartbeat arriving exactly `pulsetime` after the last
   event ends still merges. Implement it as `<` and you silently fragment your day.
2. **`max`, not assignment.** A late or out-of-order heartbeat can never shorten an
   event. This is what makes the model tolerant of jittery pollers.
3. **Data equality is exact.** `{app, title}` where the title changes every second
   (a video player's timestamp, a terminal's cwd) produces one event per second.
   That is the number one cause of "why is my AW database 800 MB".

`kuhytrack/tests/test_kt.py` pins all three, including the inclusive boundary.

## REST cheat sheet (identical in kuhytrack)

```
GET    /api/0/info
GET    /api/0/buckets/                          -> {bucket_id: {...}}
POST   /api/0/buckets/<id>                      {client,type,hostname}
DELETE /api/0/buckets/<id>
GET    /api/0/buckets/<id>/events?limit=&start=&end=
POST   /api/0/buckets/<id>/events               event | [events]
POST   /api/0/buckets/<id>/heartbeat?pulsetime=60   {timestamp,duration,data}
GET    /api/0/buckets/<id>/events/count
GET    /api/0/export           POST /api/0/import
```

Event: `{"timestamp": ISO8601 with offset, "duration": seconds (float), "data": {...}}`
Bucket types in the wild: `currentwindow`, `afkstatus`, `web.tab.current`,
`app.editor.activity`, `os.lockscreen.unlocks`.

**Gotcha that cost 20 minutes during this build:** `datetime.isoformat()` yields
`...+00:00`, and a raw `+` in a query string decodes to a space. URL-encode your
timestamps. kuhytrack now answers 400 with that exact hint instead of 500.

## Where it breaks for "mine, cross phone/linux"

- **Every device is its own island.** The design assumes one server per machine.
  `aw-sync` bolts on file-level syncing of sqlite files through a shared folder
  (Syncthing) and is explicitly not a general solution; **Android is not part of it**.
  So "my phone and my laptop in one view" is the one thing the architecture does not do.
- **Wayland.** `aw-watcher-window` has no universal Wayland backend — compositors
  deliberately do not expose the focused window to unprivileged clients. On Arch with
  Hyprland/sway you need compositor-specific IPC. `aw-watcher-afk` is worse: on Wayland
  there is often no idle source at all, so it reports you as permanently active and
  inflates every total.
- **Android is a black box you cannot extend.** The app is a Kotlin project with an
  embedded Rust server. Adding "also push to my box" means a build toolchain, not a
  config line.
- **aw-query.** A bespoke DSL you have to learn to answer "how long was I in Firefox
  yesterday" — and it evaluates server-side against buckets you must name explicitly.

Each of those four is a design decision that a personal, single-user rebuild gets to
reverse for free. That is the case for building rather than deploying — see
`03-verdict.md`.
