# Android activity tracking: the four possible sources

Reference sheet. Everything below assumes Android 14/15 on a Pixel 6a.

| source | needs | granularity | latency | survives OTA | verdict |
|---|---|---|---|---|---|
| `UsageStatsManager` | an installed APK with `PACKAGE_USAGE_STATS` granted by the user | foreground app + resume/pause events, ~seconds | minutes (batched) | yes | the legitimate one. Requires shipping an app, or reusing aw-android |
| `AccessibilityService` | user grant + an APK; Play Store hostile to it | window title text as well as package | live | yes | most data, worst permission optics, easiest to break |
| `su -c dumpsys` | Magisk root | current foreground package+activity, screen state | live | **no — reverify after each Android release** | fastest path from zero to working when you already have root |
| Digital Wellbeing DB | root, undocumented sqlite | historical, complete | hours | no | last resort for backfill; schema changes without notice |

## The commands that matter (root path)

```bash
su -c "dumpsys activity activities" | grep -m1 -E "topResumedActivity|mResumedActivity"
#  ... topResumedActivity=ActivityRecord{... com.android.chrome/com.google.android.apps.chrome.Main ...}

su -c "dumpsys power" | grep mWakefulness      # Awake | Asleep | Dozing
su -c "dumpsys usagestats" | head -50          # historical events, fragile to parse
cmd package list packages -3                   # third-party packages, for a label map
```

`topResumedActivity` is the Android 10+ name; `mResumedActivity` is the older one. The
watcher greps for both so it does not break on either.

## The no-root trick worth knowing

aw-android holds `PACKAGE_USAGE_STATS` legitimately and runs an aw-server-rust on the
phone's **loopback** interface at `127.0.0.1:5600`. Anything else on the phone — Termux —
can read that API. So:

```
UsageStatsManager -> aw-android -> 127.0.0.1:5600 -> kt-bridge-awandroid.sh -> your box
```

You get properly-permissioned data with no root, at the cost of a ~5 minute lag and a
dependency on an app you do not control. `android/kt-bridge-awandroid.sh` implements it,
incrementally, tracking the last pushed timestamp per bucket.

## Keeping a background job alive on a Pixel

All four are needed; skipping any one of them means silent data loss:

1. `termux-wake-lock` (partial wake lock, shows a notification)
2. Settings → Apps → Termux → Battery → **Unrestricted**
3. Termux:Boot installed, script in `~/.termux/boot/` and `chmod +x`
4. Do not swipe Termux out of Recents. Android treats that as "user wants this gone"

Verify it is working with `ktq devices` — if "last seen" on the phone bucket exceeds an
hour of waking time, one of the four slipped.

## Battery cost, measured in principle

The root watcher runs two `dumpsys` calls every 10 s. `dumpsys activity activities` is
cheap (~10 ms of CPU) but it is a binder round trip. Expect low single-digit percent of
daily battery. If that is too much: raise `KT_POLL` to 30 and raise `KT_PULSETIME` with
it — you lose the ability to distinguish app switches shorter than 30 s, which for
"where did my day go" is an acceptable trade.
