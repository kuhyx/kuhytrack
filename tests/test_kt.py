#!/usr/bin/env python3
"""python3 tests/test_kt.py  — no pytest, no deps.

Covers the two things that actually break activity trackers:
  1. heartbeat merge semantics (must match aw-core exactly, or durations lie)
  2. multi-device isolation + offline backfill ordering
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
TMP = Path(tempfile.mkdtemp())
os.environ["KT_DB"] = str(TMP / "test.db")
os.environ["KT_TOKEN"] = ""
import kt_server as kt  # noqa: E402

T0 = datetime(2026, 8, 8, 10, 0, 0, tzinfo=timezone.utc)


def ts(offset_s):
    return kt.fmt_ts(T0 + timedelta(seconds=offset_s))


class Heartbeats(unittest.TestCase):
    def setUp(self):
        kt.db().execute("DELETE FROM events")
        kt.db().execute("DELETE FROM buckets")
        kt.create_bucket("b", {"type": "currentwindow", "client": "t", "hostname": "h"})

    def hb(self, off, dur, data, pulse=60):
        return kt.heartbeat("b", {"timestamp": ts(off), "duration": dur, "data": data}, pulse)

    def test_same_data_within_pulse_merges(self):
        self.hb(0, 0, {"app": "firefox"})
        e = self.hb(5, 0, {"app": "firefox"})
        self.assertEqual(e["duration"], 5)
        self.assertEqual(len(kt.get_events("b")), 1)

    def test_different_data_never_merges(self):
        self.hb(0, 0, {"app": "firefox"})
        self.hb(5, 0, {"app": "kitty"})
        self.assertEqual(len(kt.get_events("b")), 2)

    def test_gap_larger_than_pulsetime_splits(self):
        self.hb(0, 0, {"app": "firefox"})
        self.hb(61, 0, {"app": "firefox"}, pulse=60)
        self.assertEqual(len(kt.get_events("b")), 2)

    def test_gap_exactly_pulsetime_merges(self):
        # aw-core uses <=, so the boundary merges. Off-by-one here = 60s of phantom time.
        self.hb(0, 0, {"app": "firefox"})
        e = self.hb(60, 0, {"app": "firefox"}, pulse=60)
        self.assertEqual(e["duration"], 60)
        self.assertEqual(len(kt.get_events("b")), 1)

    def test_duration_is_max_not_overwrite(self):
        # a late heartbeat that ends before the current event must not shorten it
        self.hb(0, 30, {"app": "firefox"})
        e = self.hb(5, 0, {"app": "firefox"})
        self.assertEqual(e["duration"], 30)

    def test_heartbeat_with_duration_extends(self):
        self.hb(0, 10, {"app": "firefox"})
        e = self.hb(20, 15, {"app": "firefox"})
        self.assertEqual(e["duration"], 35)  # (20-0)+15

    def test_data_key_order_irrelevant(self):
        self.hb(0, 0, {"app": "firefox", "title": "x"})
        self.hb(5, 0, {"title": "x", "app": "firefox"})
        self.assertEqual(len(kt.get_events("b")), 1)

    def test_zero_pulsetime_only_merges_contiguous(self):
        self.hb(0, 10, {"app": "a"})
        self.hb(10, 0, {"app": "a"}, pulse=0)   # exactly at end -> merges
        self.assertEqual(len(kt.get_events("b")), 1)
        self.hb(11, 0, {"app": "a"}, pulse=0)   # 1s gap -> splits
        self.assertEqual(len(kt.get_events("b")), 2)


class Devices(unittest.TestCase):
    def setUp(self):
        kt.db().execute("DELETE FROM events")
        kt.db().execute("DELETE FROM buckets")
        kt.create_bucket("aw-watcher-window_arch", {"type": "currentwindow", "hostname": "arch"})
        kt.create_bucket("kt-watcher-android_pixel", {"type": "currentwindow", "hostname": "pixel",
                                                      "device": "pixel"})

    def test_devices_do_not_merge_into_each_other(self):
        kt.heartbeat("aw-watcher-window_arch", {"timestamp": ts(0), "duration": 0,
                                                "data": {"app": "firefox"}}, 60)
        kt.heartbeat("kt-watcher-android_pixel", {"timestamp": ts(5), "duration": 0,
                                                  "data": {"app": "firefox"}}, 60)
        self.assertEqual(len(kt.get_events("aw-watcher-window_arch")), 1)
        self.assertEqual(len(kt.get_events("kt-watcher-android_pixel")), 1)

    def test_summary_splits_by_device(self):
        for i in range(10):
            kt.heartbeat("aw-watcher-window_arch", {"timestamp": ts(i * 10), "duration": 0,
                                                    "data": {"app": "kitty"}}, 60)
        for i in range(5):
            kt.heartbeat("kt-watcher-android_pixel", {"timestamp": ts(i * 10), "duration": 0,
                                                      "data": {"app": "Signal"}}, 60)
        s = kt.summary(start=ts(-3600), end=ts(3600))
        self.assertEqual(set(s["per_device"]), {"arch", "pixel"})
        self.assertEqual(s["per_device"]["arch"]["seconds"], 90)
        self.assertEqual(s["per_device"]["pixel"]["seconds"], 40)
        self.assertEqual(list(s["top_apps"])[0], "kitty")

    def test_union_seconds_does_not_double_count_overlap(self):
        # phone and laptop active in the same 60s: sum=120, wall clock=60
        kt.insert_event("aw-watcher-window_arch", {"timestamp": ts(0), "duration": 60,
                                                   "data": {"app": "kitty"}})
        kt.insert_event("kt-watcher-android_pixel", {"timestamp": ts(0), "duration": 60,
                                                     "data": {"app": "Signal"}})
        s = kt.summary(start=ts(-3600), end=ts(3600))
        self.assertEqual(s["total_seconds"], 120)
        self.assertEqual(s["union_seconds"], 60)

    def test_union_seconds_adds_disjoint_intervals(self):
        kt.insert_event("aw-watcher-window_arch", {"timestamp": ts(0), "duration": 60,
                                                   "data": {"app": "kitty"}})
        kt.insert_event("kt-watcher-android_pixel", {"timestamp": ts(600), "duration": 60,
                                                     "data": {"app": "Signal"}})
        self.assertEqual(kt.summary(start=ts(-3600), end=ts(3600))["union_seconds"], 120)

    def test_offline_backfill_out_of_order_is_stored(self):
        # phone was offline; spool flushes 3h of old events after newer ones exist
        kt.heartbeat("kt-watcher-android_pixel", {"timestamp": ts(0), "duration": 5,
                                                  "data": {"app": "now"}}, 60)
        kt.insert_event("kt-watcher-android_pixel", {"timestamp": ts(-10800), "duration": 60,
                                                     "data": {"app": "earlier"}})
        evs = kt.get_events("kt-watcher-android_pixel")
        self.assertEqual(len(evs), 2)
        self.assertEqual(evs[0]["data"]["app"], "now")  # DESC order preserved


class TimeParsing(unittest.TestCase):
    def test_accepts_watcher_formats(self):
        for s in ["2026-08-08T10:00:00Z", "2026-08-08T10:00:00+00:00",
                  "2026-08-08T10:00:00.123456Z", "2026-08-08T10:00:00.123456789Z",
                  "2026-08-08T12:00:00+02:00"]:
            self.assertEqual(kt.parse_ts(s).tzinfo, timezone.utc if s.endswith("Z") or "+00:00" in s
                             else kt.parse_ts(s).tzinfo)
            self.assertIsInstance(kt.fmt_ts(kt.parse_ts(s)), str)

    def test_naive_timestamps_assumed_utc(self):
        self.assertEqual(kt.parse_ts("2026-08-08T10:00:00").tzinfo, timezone.utc)


class HttpApi(unittest.TestCase):
    proc = None
    port = 5699

    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, KT_DB=str(TMP / "http.db"), KT_PORT=str(cls.port),
                   KT_TOKEN="testtoken", KT_HOST="127.0.0.1")
        cls.proc = subprocess.Popen([sys.executable, str(ROOT / "server" / "kt_server.py")],
                                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/health", timeout=1)
                return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()

    def req(self, method, path, body=None, token="testtoken"):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                   data=json.dumps(body).encode() if body is not None else None,
                                   headers={"Content-Type": "application/json",
                                            **({"Authorization": f"Bearer {token}"} if token else {})})
        with urllib.request.urlopen(r, timeout=5) as resp:
            return json.loads(resp.read() or b"null")

    def test_auth_required(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.req("GET", "/api/0/buckets/", token=None)
        self.assertEqual(cm.exception.code, 401)

    def test_full_watcher_flow(self):
        self.req("POST", "/api/0/buckets/aw-watcher-window_test",
                 {"client": "aw-watcher-window", "type": "currentwindow", "hostname": "test"})
        for i in range(3):
            self.req("POST", "/api/0/buckets/aw-watcher-window_test/heartbeat?pulsetime=60",
                     {"timestamp": ts(i * 5), "duration": 0, "data": {"app": "kitty", "title": "zsh"}})
        evs = self.req("GET", "/api/0/buckets/aw-watcher-window_test/events")
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["duration"], 10)
        self.assertEqual(self.req("GET", "/api/0/buckets/aw-watcher-window_test/events/count"), 1)
        exp = self.req("GET", "/api/0/export")
        self.assertIn("aw-watcher-window_test", exp["buckets"])

    def test_habits_and_streak(self):
        h = self.req("POST", "/api/0/kt/habits", {"name": "5x5 squats"})
        today = datetime.now(timezone.utc).date()
        for d in range(3):
            self.req("POST", f"/api/0/kt/habits/{h['id']}/tick",
                     {"day": (today - timedelta(days=d)).isoformat()})
        hs = [x for x in self.req("GET", "/api/0/kt/habits") if x["id"] == h["id"]][0]
        self.assertEqual(hs["streak"], 3)

    def test_unknown_bucket_heartbeat_404s(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.req("POST", "/api/0/buckets/nope/heartbeat", {"timestamp": ts(0), "data": {}})
        self.assertEqual(cm.exception.code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
