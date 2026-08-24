# Critique: what will actually go wrong

Ordered by probability of biting you, not by severity.

## 1. The phone watcher dies quietly (near-certain)

Android kills background work aggressively. Termux on a Pixel 6a with default battery
settings gets frozen within an hour of screen-off.

- **Symptom:** phone data stops at roughly the same time every night, resumes when you
  next open Termux.
- **Mitigations, all required together:** `termux-wake-lock`; Settings → Apps → Termux →
  Battery → **Unrestricted**; Termux:Boot installed so it survives reboots.
- **Detection, not hope:** `ktq devices` prints "last seen" per bucket. If the phone
  bucket is older than an hour while you are awake, the watcher is dead. This is the
  single most useful line in the CLI.

## 2. Title churn explodes the database (likely, and silent)

Any window whose title updates every second (video players, terminals with a clock,
progress bars) defeats data-equality merging and writes one row per poll.

- Measured here: 811 seeded events → 147 KB sqlite. Call it **~180 bytes/event**.
- Healthy: ~500–1500 events/device/day → **~5 MB/year/device**. Irrelevant.
- Pathological: 5 s polling with churning titles → 17 280 events/device/day →
  **~1.1 GB/year**. Very relevant.
- **Fix already in place:** `KT_EXCLUDE_TITLE_RE` replaces matching titles with
  `<redacted>`, which also collapses them into one merged event. Point it at your media
  player and your password manager. If it still grows, drop `title` from the heartbeat
  `data` entirely and keep only `app` — you lose almost no analytical value.

## 3. AFK is a lie on Wayland (certain, if unaddressed)

No idle source under Hyprland/sway means "always active", which inflates every total and
makes the whole dataset worthless for decisions. The watcher tries five idle sources and
**prints a loud warning naming the fix** if all fail. Do not ignore that line. On X11,
`pacman -S xprintidle` and it is solved; on Wayland, being in the `input` group makes the
`/dev/input` fallback work.

## 4. Clock skew and timezones (occasional, corrupting)

Everything is stored UTC and rendered in local time by the client. The phone and the box
disagree by seconds at worst if both use NTP — fine. But **DST transitions** make
"today" ambiguous: `ktq` builds day boundaries from the local timezone and converts, so a
23-hour or 25-hour day is handled. Naive `date +%Y-%m-%d` string comparisons are not; do
not add any.

## 5. Security surface

| surface | risk | current state |
|---|---|---|
| server binds `0.0.0.0` | anyone on the LAN reads your entire life | binds `127.0.0.1` by default; `KT_BIND=tailscale` binds the tailnet IP only |
| bearer token | replayable over plain HTTP | fine over Tailscale (WireGuard). **Never expose this to the public internet, with or without a token** |
| token in Termux config | readable by root/adb backup | Android app-private storage; acceptable given the threat model is "someone with your unlocked phone" |
| the data itself | window titles are the most sensitive log on your machine — banking, medical, private repos | `KT_EXCLUDE_TITLE_RE`, and the DB is a plain file you can delete |
| `su -c dumpsys` | Termux holds root indefinitely | it is your phone; but revoke Termux root in Magisk if you lend it out |

There is no user separation, no CSRF protection, and no rate limiting, because it is
single-user on a private network. **Any deviation from that assumption invalidates the
whole design.**

## 6. Maintenance cost, honestly

| component | who maintains it | realistic annual cost |
|---|---|---|
| kt_server.py (~600 lines stdlib) | you | near zero — stdlib does not churn |
| kt-watcher-linux.py | you | one afternoon whenever you change compositor |
| kt-watcher-android.sh | you | breaks on major Android upgrades when `dumpsys` output shifts. Budget an evening per Android release |
| aw-android (bridge path) | upstream, 84 open issues | free, but not yours |
| wakapi | upstream, healthy | free |

The genuine risk is not any of these breaking. It is that you keep a tracker running for
a year, look at it four times, and never change a single behaviour because of it. That is
the argument in `07-adversarial.md`.

## 7. Abandonment risk of the linked projects

`focus_flow_cloud` (43 stars, 1 author, **no LICENSE file**) is the only genuinely risky
dependency in the list — no license means no grant of rights, so vendoring its code is
legally murky. `traggo` and `timetagger` are mature and slow-moving, which for a
single-binary tool is a feature, not a warning. `wakapi` and `solidtime` are healthy.
`Ziit` is young and has one maintainer; it is the second-riskiest.
