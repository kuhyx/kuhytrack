"""Keep looking for a working window/idle source instead of giving up once.

Why this exists: kuhytrack-watcher.service starts at login, before the X
session has exported DISPLAY/XAUTHORITY into the systemd user manager. The
watcher used to probe its sources exactly once, find none, log a warning and
then run "active" for seven weeks while recording no window events at all
(2026-08-10 .. 2026-10-02). Two changes make that impossible:

* a SourceSlot with no working source re-probes on an exponential back-off;
  a source that stops answering is dropped and re-probed the same way;
* before each probe, session variables missing from this process are adopted
  from ``systemctl --user show-environment`` -- a running process never sees
  variables imported into the manager after it started.
"""

from __future__ import annotations

import os
import time

from ktw_util import log, sh

SESSION_VARS = (
    "DISPLAY",
    "XAUTHORITY",
    "WAYLAND_DISPLAY",
    "SWAYSOCK",
    "HYPRLAND_INSTANCE_SIGNATURE",
)


def _manager_env():
    return sh(["systemctl", "--user", "show-environment"])


def adopt_session_env(environ=None, read=_manager_env):
    """Copy session variables this process lacks from the user manager.

    Only fills gaps -- a variable already set is never overridden -- and skips
    systemd's $'...' quoting, which none of these variables legitimately need.
    Returns the names adopted.
    """
    env = os.environ if environ is None else environ
    missing = {name for name in SESSION_VARS if not env.get(name)}
    if not missing:
        return []
    adopted = []
    for line in read().splitlines():
        key, sep, value = line.partition("=")
        if sep and key in missing and value and not value.startswith("$'"):
            env[key] = value
            adopted.append(key)
    if adopted:
        log(f"adopted from the user manager: {', '.join(sorted(adopted))}")
    return adopted


class SourceSlot:
    """One kind of source (window or idle), re-probed until one answers.

    ``read()`` returns the current value, or None while no source works. It
    never raises: a watcher that dies is worse than one that waits.
    """

    def __init__(
        self,
        kind,
        sources,
        hint="",
        *,
        min_delay=5.0,
        max_delay=300.0,
        lost_after=3,
        clock=time.monotonic,
        adopt=adopt_session_env,
    ):
        self.kind = kind
        self.sources = sources
        self.hint = hint
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.lost_after = lost_after
        self.clock = clock
        self.adopt = adopt
        self.name = "none"
        self.fn = None
        self.delay = min_delay
        self.next_probe = 0.0
        self.misses = 0
        self.failed_probes = 0

    def _probe(self, now):
        self.adopt()
        for name, fn in self.sources:
            try:
                if fn() is not None:
                    self.name, self.fn = name, fn
                    self.delay, self.misses, self.failed_probes = self.min_delay, 0, 0
                    log(f"{self.kind} source: {name}")
                    return
            except Exception:
                continue
        self.failed_probes += 1
        detail = f" {self.hint}" if self.failed_probes == 1 and self.hint else ""
        log(f"!! no {self.kind} source yet; retrying in {self.delay:.0f}s.{detail}")
        self.next_probe = now + self.delay
        self.delay = min(self.delay * 2, self.max_delay)

    def read(self):
        now = self.clock()
        if self.fn is None:
            if now < self.next_probe:
                return None
            self._probe(now)
            if self.fn is None:
                return None
        try:
            value = self.fn()
        except Exception:
            value = None
        if value is not None:
            self.misses = 0
            return value
        self.misses += 1
        if self.misses >= self.lost_after:
            log(f"!! {self.kind} source {self.name} stopped answering; re-probing")
            self.name, self.fn, self.next_probe = "none", None, now
        return None
