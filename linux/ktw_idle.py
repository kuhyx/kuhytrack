"""Idle sources for kt-watcher-linux, tried in order.

$KT_IDLE_CMD -> xprintidle -> GNOME Mutter IdleMonitor (gdbus) -> KDE
ScreenSaver (qdbus) -> /dev/input atime. Each returns idle seconds, or
``None`` when it cannot answer here.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from ktw_util import has, sh


def _idle_cmd():
    c = os.environ.get("KT_IDLE_CMD")
    if not c:
        return None
    out = sh(["sh", "-c", c])
    return float(out) / 1000 if out.strip().isdigit() else None


def _idle_xprintidle():
    if not has("xprintidle") or not os.environ.get("DISPLAY"):
        return None
    out = sh(["xprintidle"])
    return float(out) / 1000 if out.isdigit() else None


def _idle_mutter():
    if not has("gdbus"):
        return None
    out = sh(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.gnome.Mutter.IdleMonitor",
            "--object-path",
            "/org/gnome/Mutter/IdleMonitor/Core",
            "--method",
            "org.gnome.Mutter.IdleMonitor.GetIdletime",
        ]
    )
    m = re.search(r"(\d+)", out)
    return float(m.group(1)) / 1000 if m else None


def _idle_kde():
    if not has("qdbus"):
        return None
    out = sh(
        ["qdbus", "org.freedesktop.ScreenSaver", "/ScreenSaver", "GetSessionIdleTime"]
    )
    return float(out) if out.isdigit() else None


def _devinput_atime_is_live(mounts="/proc/mounts"):
    """Is /dev mounted so that st_atime actually tracks reads?

    Under the near-universal `relatime` (and obviously `noatime`) the kernel does not
    update atime on every read, so the timestamps freeze at boot. _idle_devinput then
    returns a large, entirely plausible float forever -- the watcher believes you have
    been idle for hours, records no windows at all, and logs a cheerful
    'idle source: devinput' while collecting nothing. A source that structurally cannot
    work must not be selectable, so this is checked before offering it.
    """
    try:
        with open(mounts) as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 4 and parts[1] == "/dev":
                    opts = parts[3].split(",")
                    return not ({"relatime", "noatime"} & set(opts))
    except OSError:
        pass
    return False


def _idle_devinput():
    """Wayland-agnostic: newest atime across /dev/input/event*. Needs group `input`,
    and a /dev whose atime is not frozen by relatime -- see _devinput_atime_is_live."""
    if not _devinput_atime_is_live():
        return None
    newest = 0.0
    try:
        for p in Path("/dev/input").glob("event*"):
            try:
                newest = max(newest, p.stat().st_atime)
            except OSError:
                pass
    except OSError:
        return None
    return (time.time() - newest) if newest else None


IDLE_SOURCES = [
    ("KT_IDLE_CMD", _idle_cmd),
    ("xprintidle", _idle_xprintidle),
    ("mutter", _idle_mutter),
    ("kde", _idle_kde),
    ("devinput", _idle_devinput),
]

IDLE_HINT = (
    "AFK reports not-afk until one answers. Fix: pacman -S xprintidle (X11/Xwayland). "
    "The /dev/input fallback needs group `input` AND a /dev without relatime -- on a "
    "stock Arch install relatime is on, so the group alone will not fix this."
)
