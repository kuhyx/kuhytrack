"""Window sources for kt-watcher-linux, tried in order.

Hyprland IPC -> sway IPC -> X11 (xprop) -> KWin (kdotool). Each returns
``{"app", "title"}`` when it works here and ``None`` when it cannot, so the
caller (ktw_probe.SourceSlot) can tell "no source" from "no focused window".
"""

from __future__ import annotations

import json
import os
import re

from ktw_util import has, sh


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
    if not root:
        # xprop printed nothing: the display is unreachable (wrong DISPLAY, no
        # XAUTHORITY), which is "no source", not "no focused window".
        return None
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

WINDOW_HINT = (
    "Install xprintidle/kdotool or run under Hyprland/sway/X11. The watcher keeps "
    "re-probing (DISPLAY is adopted from the user manager once X is up) and still "
    "records AFK status meanwhile."
)
