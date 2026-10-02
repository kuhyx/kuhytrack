#!/usr/bin/env python3
"""python3 tests/test_watcher.py  — no pytest, no deps.

Covers the failure that kept the linux watcher "active" and empty for seven
weeks: sources probed once at login, before X exported DISPLAY, and never
again. SourceSlot must keep re-probing, drop a source that goes quiet, and
adopt session variables from the user manager.
"""

import importlib
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "linux"))
os.environ["KT_SPOOL"] = str(Path(tempfile.mkdtemp()) / "spool.jsonl")
ktw_probe = importlib.import_module("ktw_probe")
ktw_window = importlib.import_module("ktw_window")

_spec = importlib.util.spec_from_file_location(
    "kt_watcher_linux", ROOT / "linux" / "kt-watcher-linux.py"
)
watcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watcher)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def slot(sources, clock, **kw):
    return ktw_probe.SourceSlot(
        "window", sources, "hint", clock=clock, adopt=list, **kw
    )


class AdoptSessionEnv(unittest.TestCase):
    MANAGER = (
        "DISPLAY=:0\nXAUTHORITY=/home/u/.Xauthority\nSWAYSOCK=$'odd\\nvalue'\nPATH=/x"
    )

    def test_fills_only_missing_variables(self):
        env = {"XAUTHORITY": "/keep"}
        adopted = ktw_probe.adopt_session_env(env, read=lambda: self.MANAGER)
        self.assertEqual(adopted, ["DISPLAY"])
        self.assertEqual(env["DISPLAY"], ":0")
        self.assertEqual(env["XAUTHORITY"], "/keep")
        self.assertNotIn("PATH", env)

    def test_skips_systemd_quoted_values(self):
        env = {}
        ktw_probe.adopt_session_env(env, read=lambda: self.MANAGER)
        self.assertNotIn("SWAYSOCK", env)

    def test_does_not_ask_the_manager_when_nothing_is_missing(self):
        env = {name: "set" for name in ktw_probe.SESSION_VARS}

        def boom():
            raise AssertionError("must not be called")

        self.assertEqual(ktw_probe.adopt_session_env(env, read=boom), [])

    def test_manager_unreachable_adopts_nothing(self):
        self.assertEqual(ktw_probe.adopt_session_env({}, read=lambda: ""), [])


class SourceSlotReprobe(unittest.TestCase):
    def test_retries_on_backoff_until_a_source_appears(self):
        clock, state = Clock(), {"up": False}
        source = ("x11", lambda: {"app": "a"} if state["up"] else None)
        s = slot([source], clock, min_delay=5, max_delay=20)
        self.assertIsNone(s.read())
        self.assertEqual(s.next_probe, 1005)
        clock.now = 1004
        self.assertIsNone(s.read())  # inside the back-off: no probe
        clock.now = 1005
        self.assertIsNone(s.read())
        self.assertEqual(s.next_probe, 1015)  # delay doubled to 10
        clock.now = 1015
        s.read()
        clock.now = 1035
        s.read()
        self.assertEqual(s.delay, 20)  # capped at max_delay
        state["up"] = True
        clock.now = 1055
        self.assertEqual(s.read(), {"app": "a"})
        self.assertEqual((s.name, s.delay), ("x11", 5))

    def test_a_source_that_goes_quiet_is_dropped_and_reprobed(self):
        clock, state = Clock(), {"up": True}
        source = ("x11", lambda: {"app": "a"} if state["up"] else None)
        s = slot([source], clock, lost_after=2)
        self.assertEqual(s.read(), {"app": "a"})
        state["up"] = False
        self.assertIsNone(s.read())
        self.assertEqual(s.name, "x11")
        self.assertIsNone(s.read())
        self.assertEqual(s.name, "none")
        state["up"] = True
        self.assertEqual(s.read(), {"app": "a"})  # re-probed immediately

    def test_raising_sources_are_skipped_and_never_escape(self):
        clock, calls = Clock(), {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("xprop died")
            return 3.0

        def boom():
            raise RuntimeError("no hyprctl")

        s = slot([("hypr", boom), ("idle", flaky)], clock)
        self.assertIsNone(s.read())  # probe succeeded, the read raised
        self.assertEqual((s.name, s.misses), ("idle", 1))

    def test_probe_adopts_the_session_before_trying_sources(self):
        order = []
        s = ktw_probe.SourceSlot(
            "idle",
            [("x", lambda: order.append("source") or 1.0)],
            clock=Clock(),
            adopt=lambda: order.append("adopt"),
        )
        self.assertEqual(s.read(), 1.0)
        self.assertEqual(order[:2], ["adopt", "source"])


class X11Source(unittest.TestCase):
    def test_unreachable_display_is_no_source_not_desktop(self):
        old = (ktw_window.has, ktw_window.sh, os.environ.get("DISPLAY"))
        try:
            os.environ["DISPLAY"] = ":9"
            ktw_window.has = lambda _b: True
            ktw_window.sh = lambda _cmd: ""
            self.assertIsNone(ktw_window._x11())
            ktw_window.sh = lambda _cmd: "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x0"
            self.assertEqual(ktw_window._x11(), {"app": "desktop", "title": ""})
        finally:
            ktw_window.has, ktw_window.sh = old[0], old[1]
            if old[2] is None:
                os.environ.pop("DISPLAY", None)
            else:
                os.environ["DISPLAY"] = old[2]


class Tick(unittest.TestCase):
    class Fixed:
        def __init__(self, value):
            self.value = value

        def read(self):
            return self.value

    def test_not_afk_records_the_window(self):
        sent = []
        status = watcher.tick(
            self.Fixed({"app": "Code", "title": "t"}),
            self.Fixed(1.0),
            None,
            beat=lambda b, d: sent.append((b, d)),
        )
        self.assertEqual(status, "not-afk")
        self.assertEqual([b for b, _ in sent], [watcher.AFK_BUCKET, watcher.WIN_BUCKET])

    def test_afk_records_no_window_and_no_idle_source_means_not_afk(self):
        sent = []

        def beat(b, d):
            sent.append((b, d))

        afk = watcher.tick(self.Fixed({"app": "x"}), self.Fixed(10_000.0), None, beat)
        self.assertEqual(afk, "afk")
        self.assertEqual(len(sent), 1)
        sent.clear()
        self.assertEqual(
            watcher.tick(self.Fixed(None), self.Fixed(None), afk, beat), "not-afk"
        )
        self.assertEqual(len(sent), 1)  # no window source: AFK heartbeat only


if __name__ == "__main__":
    unittest.main(verbosity=1)
