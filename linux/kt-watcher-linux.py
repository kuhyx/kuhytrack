#!/usr/bin/env python3
"""
kt-watcher-linux — window + AFK watcher for Arch. Zero deps.

Replaces aw-watcher-window + aw-watcher-afk with one process, and fixes the
thing that actually bites on a modern Arch desktop: aw-watcher-window has no
working Wayland backend for most compositors, and aw-watcher-afk has no Wayland
idle source at all. This tries, in order:

  window : Hyprland IPC -> sway IPC -> X11 (xprop) -> KWin script -> give up loudly
  idle   : $KT_IDLE_CMD -> xprintidle -> GNOME Mutter IdleMonitor (gdbus)
           -> KDE ScreenSaver (qdbus) -> /dev/input mtime -> never-afk (warns once)

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
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

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


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


def sh(cmd, **kw):
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=3, **kw
        ).stdout.strip()
    except Exception:
        return ""


def has(binary):
    return shutil.which(binary) is not None


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


# ---------------------------------------------------------------- window source


def _hypr():
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or not has("hyprctl"):
        return None
    out = sh(["hyprctl", "activewindow", "-j"])
    if not out:
        return None
    try:
        w = json.loads(out)
    except json.JSONDecodeError:
        return None
    if not w.get("class"):
        return {"app": "desktop", "title": ""}
    return {"app": w.get("class", "?"), "title": w.get("title", "")}


def _sway():
    if not has("swaymsg"):
        return None
    out = sh(["swaymsg", "-t", "get_tree"])
    if not out:
        return None
    try:
        tree = json.loads(out)
    except json.JSONDecodeError:
        return None

    def find(node):
        if node.get("focused") and node.get("type") in ("con", "floating_con"):
            return node
        for k in ("nodes", "floating_nodes"):
            for c in node.get(k, []):
                r = find(c)
                if r:
                    return r
        return None

    n = find(tree)
    if not n:
        return {"app": "desktop", "title": ""}
    app = n.get("app_id") or (n.get("window_properties") or {}).get("class") or "?"
    return {"app": app, "title": n.get("name") or ""}


def _x11():
    if not os.environ.get("DISPLAY") or not has("xprop"):
        return None
    root = sh(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
    m = re.search(r"(0x[0-9a-fA-F]+)", root)
    if not m or m.group(1) == "0x0":
        return {"app": "desktop", "title": ""}
    wid = m.group(1)
    props = sh(["xprop", "-id", wid, "WM_CLASS", "_NET_WM_NAME", "WM_NAME"])
    cls = re.search(r'WM_CLASS\(STRING\) = "[^"]*", "([^"]*)"', props)
    title = re.search(r'_NET_WM_NAME\(UTF8_STRING\) = "(.*)"', props) or re.search(
        r'WM_NAME\(STRING\) = "(.*)"', props
    )
    return {
        "app": cls.group(1) if cls else "?",
        "title": title.group(1) if title else "",
    }


def _kwin():
    if not has("qdbus") or "kwin" not in sh(["sh", "-c", "pgrep -l kwin || true"]):
        return None
    # KWin 6 blocks scripting eval for privacy; kdotool is the working path.
    if has("kdotool"):
        wid = sh(["kdotool", "getactivewindow"])
        if wid:
            return {
                "app": sh(["kdotool", "getwindowclassname", wid]) or "?",
                "title": sh(["kdotool", "getwindowname", wid]) or "",
            }
    return None


WINDOW_SOURCES = [("hyprland", _hypr), ("sway", _sway), ("x11", _x11), ("kwin", _kwin)]


def pick_window_source():
    for name, fn in WINDOW_SOURCES:
        try:
            if fn() is not None:
                log(f"window source: {name}")
                return name, fn
        except Exception:
            continue
    log(
        "!! no window source works here. Install xprintidle/kdotool or run under "
        "Hyprland/sway/X11. Watcher will still record AFK status."
    )
    return "none", lambda: None


# ------------------------------------------------------------------ idle source

_input_dirs = [Path("/dev/input")]


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


def _devinput_atime_is_live():
    """Is /dev mounted so that st_atime actually tracks reads?

    Under the near-universal `relatime` (and obviously `noatime`) the kernel does not
    update atime on every read, so the timestamps freeze at boot. _idle_devinput then
    returns a large, entirely plausible float forever -- the watcher believes you have
    been idle for hours, records no windows at all, and logs a cheerful
    'idle source: devinput' while collecting nothing. A source that structurally cannot
    work must not be selectable, so this is checked before offering it.
    """
    try:
        with open("/proc/mounts") as fh:
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


def pick_idle_source():
    for name, fn in IDLE_SOURCES:
        try:
            v = fn()
            if v is not None:
                log(f"idle source: {name}")
                return name, fn
        except Exception:
            continue
    log(
        "!! no idle source. AFK will always report not-afk, so no window events will be "
        "recorded at all. Fix: pacman -S xprintidle (X11/Xwayland). The /dev/input "
        "fallback needs group `input` AND a /dev without relatime — on a stock Arch "
        "install relatime is on, so the group alone will not fix this."
    )
    return "none", lambda: 0.0


# ------------------------------------------------------------------------- main


def main():
    ensure_buckets()
    _, win_fn = pick_window_source()
    _, idle_fn = pick_idle_source()
    last_status = None
    while True:
        try:
            idle = idle_fn() or 0.0
            afk = idle >= AFK_TIMEOUT
            status = "afk" if afk else "not-afk"
            heartbeat(AFK_BUCKET, {"status": status})
            if status != last_status:
                log(f"{status} (idle {idle:.0f}s)")
                last_status = status
            if not afk:
                w = win_fn()
                if w:
                    title = w.get("title", "")
                    if EXCLUDE_RE and EXCLUDE_RE.search(title):
                        title = "<redacted>"
                    heartbeat(WIN_BUCKET, {"app": w.get("app", "?"), "title": title})
        except KeyboardInterrupt:
            log("bye")
            return 0
        except Exception as e:  # a watcher that dies is worse than a watcher that lies
            log(f"loop error: {type(e).__name__}: {e}")
        time.sleep(POLL)


if __name__ == "__main__":
    sys.exit(main())
