# Options outside your ten links

## Same category, not on your list

| tool | what it is | why it might beat building |
|---|---|---|
| **arbtt** | Haskell automatic tracker, X11 only. Logs raw samples forever, then applies a *rule language* at query time to categorise retroactively | The categorisation model is the best idea in this space: never decide what a window "means" at capture time. Steal it. Dead end on Wayland and Android |
| **Tockler** | Electron, automatic window+idle tracking, local sqlite, decent charts | Zero setup on the desktop. No server, no phone, so it fails the brief |
| **selfspy** | Python, logs windows *and* keystrokes | Do not. Keylogging your own machine is a liability with no analytical payoff |
| **Kimai** | PHP timesheet for agencies | Only if you ever bill hours |
| **Super Productivity** | Electron + real Android app, manual timers with Jira/GitHub integrations | The best *manual* cross-device option, if you conclude you actually want task time, not activity |

## Categories you did not consider

**Home Assistant as the backend.** The HA Android companion app can expose a "last used
app" sensor (needs usage-access permission) plus screen state, charging, and location.
HA already solves multi-device ingest, long-term statistics, dashboards, and — crucially
— *automations*, which is the enforcement loop from `hooks/kt-budget.py` for free. If you
already run HA, this is a genuinely serious competitor to kuhytrack: worse data model
(sensor states, not intervals), far better ecosystem. If you do not run HA, adding it for
this is absurd.

**Atuin.** Self-hosted, end-to-end-encrypted *shell history* sync. It answers "what did I
actually do on which machine" with far higher signal-to-noise than window titles, because
commands are intentional and windows are ambient. It does not answer "how long", and it
has no phone story. Complementary, cheap, and you would probably get more behavioural
insight per byte from it than from a window watcher.

**Git as ground truth.** `git log --author=you --all --since` across your monorepos is a
zero-infrastructure activity record for the only work that produced artifacts. Useless for
the phone, unbeatable for "was that week productive".

**Grafana over the sqlite file.** Once kuhytrack is writing, a Grafana sqlite datasource
gives you every chart you would otherwise hand-write. The dashboard shipped here exists so
you have something on day one, not to compete with Grafana.

## The pure-consumption option

Install upstream ActivityWatch on the Arch box, install aw-android on the phone, and
simply look at two dashboards instead of one. Cost: zero code, zero maintenance, one
extra tap. If the honest requirement was "I want to see my usage" rather than "I want one
unified store I own", this wins outright and everything else here is over-engineering.
The brief said "but mine", so it does not — but it deserves to be written down.
