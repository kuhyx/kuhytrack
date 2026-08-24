# Comparison and verdict

## The matrix

Numbers fetched live 2026-08-08. Full structured version: `02-projects.json`.

| project | category | capture | phone | linux | server | stars | last commit | stack |
|---|---|---|---|---|---|---|---|---|
| ActivityWatch | activity | **auto** | aw-android (local only) | X11 ok / Wayland patchy | per-device | 18.5k | 2026-08-06 | Py+Rust+Vue |
| aw-server-rust | server only | – | embedded in app | yes | yes | 310 | 2026-08-03 | Rust |
| wakapi | coding | **auto (editor only)** | no | editor plugin | **one central** | 4.4k | 2026-08-03 | Go |
| Ziit | coding | auto (editor only) | no | VS Code | one central | 249 | 2026-07-02 | Nuxt+PG |
| solidtime | billing | manual | browser | browser | docker stack | 8.8k | 2026-08-06 | Laravel |
| timetagger | time | manual | PWA | PWA | yes | 1.8k | 2026-05-17 | Python |
| traggo | time | manual | browser | browser | single Go binary | 1.6k | 2026-07-31 | Go+GraphQL |
| beaverhabits | habits | manual | PWA | browser | yes | 1.8k | 2026-07-27 | Py/NiceGUI |
| OpenHabitTracker | habits | manual | MAUI app | yes | optional | 268 | 2026-08-07 | .NET 9 |
| focus_flow_cloud | pomodoro | manual | Flutter app | web | yes | 43 | 2026-06-16 | Rust+Flutter |

Sort that table by the only column that matters to the brief — **capture: auto** — and
nine of the ten rows disappear. Eight of them are stopwatches with different UIs. The
ninth (wakapi) is automatic but blind outside your editor.

## Verdict

**Build the server, keep ActivityWatch's wire protocol, do not keep its topology.**

Concretely, what is in `kuhytrack/`:

- one server, on the Arch box, reachable over Tailscale — **not** one server per device
- watchers are dumb HTTP clients with a local spool file, so a suspended laptop or a
  phone off the tailnet loses nothing
- the API is byte-compatible with `/api/0`, so every existing `aw-watcher-*`, the
  browser extension, and aw-android's own export all work against it unchanged
- extensions live under `/api/0/kt/` where upstream can never collide

**Why keeping the AW protocol beats inventing one:** it is the only decision that makes
the browser extension, aw-android and a decade of watchers free. Inventing a protocol
buys you nothing and costs you every client.

**Why one central server beats sync:** sync between N sqlite files is a distributed
systems problem (clock skew, conflict resolution, partial merges). One server plus a
spool file is a queue. You are one person with three devices — you do not have a sync
problem, you have a queue problem, and queues are two orders of magnitude easier.

### Why each other option lost

| option | why it lost |
|---|---|
| **Deploy AW unchanged** | its one architectural gap is exactly your requirement; the phone stays an island |
| **AW + aw-sync + Syncthing** | closest runner-up, and it works for laptop↔desktop. Android is not in scope for aw-sync, so it fails the actual brief. Also: syncing sqlite files through a folder-sync tool is how you get a corrupted sqlite file |
| **Fork aw-android** | Kotlin + embedded Rust + Gradle to add one HTTP POST. Reconsider only if the Termux path proves flaky in a week of use |
| **wakapi** | correct architecture, wrong scope. Keep it, feed it in (`kt-import.py wakapi`) |
| **Ziit** | strictly dominated by wakapi: same scope, 18× fewer stars, heavier runtime |
| **solidtime / traggo / timetagger** | manual capture. "Where did my hours go" cannot be answered by a tool that requires you to already know |
| **beaverhabits / OpenHabitTracker** | different question entirely (see `07-adversarial.md`); habit ticks are now 3 endpoints in your own server |
| **focus_flow_cloud** | 43 stars, no LICENSE file, single author. Its idea is a `sessions` table; that table now exists in kuhytrack |

### What you should still run alongside

- **wakapi** — editor-level detail no window watcher can see (project, language, file).
- **aw-android** — even on the root path, it is a free correctness check on your own
  numbers, and it is the fallback if Magisk breaks after an OTA.

### Honest counterargument

If, in two weeks, you have not opened the dashboard once, the correct action is to delete
all of it and install upstream ActivityWatch on the laptop only. Building this is right
because the gap is real and the code is ~600 lines you own — not because a tracker is
inherently worth running. `07-adversarial.md` argues the stronger version of this.
