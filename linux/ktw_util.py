"""Shared helpers for kt-watcher-linux: logging and fail-quiet subprocess calls.

Split out of kt-watcher-linux.py (250-line cap) so the window and idle source
modules can share them without importing the watcher's transport.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


def sh(cmd, **kw):
    """Run a probe command; any failure is an empty string, never an exception."""
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=3, **kw
        ).stdout.strip()
    except Exception:
        return ""


def has(binary):
    return shutil.which(binary) is not None
