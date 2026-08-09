# The reading you probably did not intend

## Your links are two different products, and you pasted them as one list

Split them by the question they answer:

- **"Where did my hours go?"** — ActivityWatch, wakapi, Ziit. Passive, involuntary,
  descriptive. You never touch them.
- **"Did I do the thing?"** — beaverhabits, OpenHabitTracker, focus_flow_cloud, and to a
  degree traggo/timetagger/solidtime. Active, voluntary, prescriptive. They only exist
  because you touch them.

Nobody builds one tool for both, because the second only works if it costs you an
intentional tap, and the first only works if it costs you nothing. Which is precisely why
they are worth putting in **one store**: the descriptive stream is the honest check on
the prescriptive one. "Habit: no phone after 23:00 ✓" is worth nothing next to a device
tape showing 90 minutes of YouTube at 00:30.

That is why kuhytrack has `habits` and `sessions` tables and endpoints alongside buckets —
about 60 lines total, replacing beaverhabits and focus_flow_cloud outright for your usage
level, and letting you overlay a tick against what your devices say you actually did.

```
ktq tick "no phone after 23:00"     # your claim
ktq timeline --limit 40             # the evidence
```

## The harder version: a tracker you only read is a diary

Every project you linked has the same failure mode, and it has nothing to do with the
tech: you install it, you look at the charts three times, you feel briefly bad about
YouTube, and six months later you have a 400 MB sqlite file and identical behaviour.

The variable that predicts whether tracking changes anything is **whether the data is
wired to a consequence**. You already own the consequence:
`github.com/kuhyx/screen-locker`.

`hooks/kt-budget.py` closes that loop, and it is the only file here that can actually
change your week:

```jsonc
{ "name": "doomscroll", "device": "pixel6a",
  "match": ["reddit","youtube","tiktok"], "minutes": 60,
  "action": "curl -X POST http://127.0.0.1:8765/lock -d 'reason={app} {used}m'" }
```

Verified in this run against seeded data: budget blown → action fires exactly once per
day, exit code 10, second run does not re-fire. Point `action` at whatever interface
screen-locker exposes (an HTTP endpoint, a CLI, a systemd unit) and the tracker stops
being a diary.

**Suggested first budget, and only one:** pick the single app you would be embarrassed to
see at the top of the list, set the budget 20% below your current daily average (`ktq
range --days 7` will tell you), and wire it to a notification — not a lock — for the
first week. A lock you resent gets uninstalled; a number you cannot unsee does the work.

## The uncomfortable third reading

"ActivityWatch but mine" may be a request for a **project**, not a tool. If so, that is
legitimate and you should say so out loud, because it changes the right answer: a project
should be optimised for interesting problems (Wayland idle detection, offline queueing,
interval algebra — all genuinely interesting) rather than for lowest maintenance. This
build leans that way deliberately: 600 lines of stdlib you own instead of a docker-compose
you inherit. But if you notice yourself adding features you never look at, that is the
tell that it was a project all along, and the honest move is to stop shipping features and
just enjoy the one that works.
