"""Shared config resolution for the kuhytrack client tools.

The installer writes KT_URL/KT_TOKEN to ~/.config/kuhytrack/env and points the systemd
units at it with EnvironmentFile=. Interactive shells never read that file, so `ktq
today` -- which the README presents as the primary interface -- returned 401 unless the
user hand-sourced the env first. Falling back to the same file the installer already
writes makes the documented command work as documented.

Precedence: real environment first (so KT_URL=... overrides for one-off runs), then the
config file, then the loopback default. stdlib only, like everything else here.

The other tools live in sibling directories and are run as standalone scripts by
absolute path (systemd, a ~/.local/bin symlink), so they cannot `import ktconf`
normally. They load it with `load()` below rather than inserting onto sys.path, which
would put an import below top-of-file and need a lint suppression at every call site.
"""

from __future__ import annotations

import os
from pathlib import Path

CONF = Path(
    os.environ.get(
        "KT_ENV_FILE",
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "kuhytrack"
        / "env",
    )
)


def _from_file() -> dict:
    """Parse the KEY=value env file. Not a shell: no expansion, no `export`."""
    out: dict[str, str] = {}
    try:
        text = CONF.read_text()
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def get(key: str, default: str = "") -> str:
    if key in os.environ:
        return os.environ[key]
    return _from_file().get(key, default)


def url() -> str:
    return get("KT_URL", "http://127.0.0.1:5600").rstrip("/")


def token() -> str:
    return get("KT_TOKEN", "")
