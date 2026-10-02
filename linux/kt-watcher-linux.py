#!/usr/bin/env python3
"""
kt-watcher-linux — window + AFK watcher for Arch. Zero deps.

Replaces aw-watcher-window + aw-watcher-afk with one process, and fixes the
thing that actually bites on a modern Arch desktop: aw-watcher-window has no
working Wayland backend for most compositors, and aw-watcher-afk has no Wayland
idle source at all. This tries, in order:

  window : Hyprland IPC -> sway IPC -> X11 (xprop) -> KWin script   (ktw_window.py)
  idle   : $KT_IDLE_CMD -> xprintidle -> GNOME Mutter IdleMonitor (gdbus)
           -> KDE ScreenSaver (qdbus) -> /dev/input mtime            (ktw_idle.py)

Neither is picked once and forever: until a source answers, ktw_probe.SourceSlot
re-probes on a back-off and adopts DISPLAY/XAUTHORITY from the systemd user
manager, because at login this service starts before X has exported them.

Buffers to a spool file when the server is unreachable and replays on reconnect,
so a laptop that suspends or leaves the tailnet loses nothing.

Run: KT_URL=http://127.0.0.1:5600 KT_TOKEN=... python3 kt-watcher-linux.py
Env: KT_URL KT_TOKEN KT_DEVICE KT_POLL(=5s) KT_AFK_TIMEOUT(=180s) KT_PULSETIME(=poll+55)
     KT_IDLE_CMD  (command printing idle milliseconds)
     KT_SPOOL     (default ~/.cache/kuhytrack/spool.jsonl)
     KT_EXCLUDE_TITLE_RE  (regex; matching titles are replaced with '<redacted>')
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from ktw_idle import IDLE_HINT, IDLE_SOURCES
from ktw_probe import SourceSlot
from ktw_util import log
from ktw_window import WINDOW_HINT, WINDOW_SOURCES

URL = os.environ.get("KT_URL", "http://127.0.0.1:5600").rstrip("/")
TOKEN = os.environ.get("KT_TOKEN", "")
DEVICE = os.environ.get("KT_DEVICE", socket.gethostname())
POLL = float(os.environ.get("KT_POLL", "5"))
AFK_TIMEOUT = float(os.environ.get("KT_AFK_TIMEOUT", "180"))
PULSETIME = float(os.environ.get("KT_PULSETIME", str(POLL + 55)))
SPOOL = Path(os.environ.get("KT_SPOOL", Path.home() / ".cache/kuhytrack/spool.jsonl"))
EXCLUDE = os.environ.get("KT_EXCLUDE_TITLE_RE")
EXCLUDE_RE = re.compile(EXCLUDE) if EXCLUDE else None

WIN_BUCKET = f"kt-watcher-window_{DEVICE}"
AFK_BUCKET = f"kt-watcher-afk_{DEVICE}"


# ------------------------------------------------------------------- transport


def post(path, payload, params=""):
    req = urllib.request.Request(
        f"{URL}{path}{params}",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def spool_append(item):
    SPOOL.parent.mkdir(parents=True, exist_ok=True)
    with SPOOL.open("a") as f:
        f.write(json.dumps(item) + "\n")


def spool_flush():
    if not SPOOL.exists() or SPOOL.stat().st_size == 0:
        return
    lines = SPOOL.read_text().splitlines()
    kept = []
    for i, line in enumerate(lines):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            post(
                f"/api/0/buckets/{item['bucket']}/heartbeat",
                item["event"],
                f"?pulsetime={item['pulsetime']}",
            )
        except Exception:
            kept = lines[i:]
            break
    SPOOL.write_text("\n".join(kept) + ("\n" if kept else ""))
    if not kept:
        log(f"spool flushed ({len(lines)} events)")


def heartbeat(bucket, data, duration=0.0):
    ev = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration": duration,
        "data": data,
    }
    try:
        spool_flush()
        post(f"/api/0/buckets/{bucket}/heartbeat", ev, f"?pulsetime={PULSETIME}")
    except Exception as e:  # offline / server restarting / tailnet down
        spool_append({"bucket": bucket, "event": ev, "pulsetime": PULSETIME})
        if isinstance(e, urllib.error.HTTPError) and e.code == 404:
            ensure_buckets(quiet=True)


def ensure_buckets(quiet=False):
    for bid, btype, client in (
        (WIN_BUCKET, "currentwindow", "kt-watcher-window"),
        (AFK_BUCKET, "afkstatus", "kt-watcher-afk"),
    ):
        try:
            post(
                f"/api/0/buckets/{bid}",
                {"client": client, "type": btype, "hostname": DEVICE, "device": DEVICE},
            )
        except Exception as e:
            if not quiet:
                log(f"bucket {bid} not created yet: {e}")


# ------------------------------------------------------------------------- main


def tick(window, idle, last_status, beat=None):
    """One poll: report AFK status, and the focused window when not AFK.

    Returns the status, so the caller can log transitions. ``beat`` is the
    heartbeat sender, injectable for tests.
    """
    beat = beat or heartbeat
    seconds = idle.read() or 0.0
    status = "afk" if seconds >= AFK_TIMEOUT else "not-afk"
    beat(AFK_BUCKET, {"status": status})
    if status != last_status:
        log(f"{status} (idle {seconds:.0f}s)")
    if status == "not-afk":
        w = window.read()
        if w:
            title = w.get("title", "")
            if EXCLUDE_RE and EXCLUDE_RE.search(title):
                title = "<redacted>"
            beat(WIN_BUCKET, {"app": w.get("app", "?"), "title": title})
    return status


def main():
    ensure_buckets()
    window = SourceSlot("window", WINDOW_SOURCES, WINDOW_HINT)
    idle = SourceSlot("idle", IDLE_SOURCES, IDLE_HINT)
    last_status = None
    while True:
        try:
            last_status = tick(window, idle, last_status)
        except KeyboardInterrupt:
            log("bye")
            return 0
        except Exception as e:  # a watcher that dies is worse than a watcher that lies
            log(f"loop error: {type(e).__name__}: {e}")
        time.sleep(POLL)


if __name__ == "__main__":
    sys.exit(main())
