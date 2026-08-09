#!/usr/bin/env bash
# install-arch.sh — unattended kuhytrack install for Arch. Run as your user, not root.
#   ./install-arch.sh              # server + linux watcher, loopback only
#   KT_BIND=tailscale ./install-arch.sh   # bind to the tailscale IP so the phone can reach it
# Idempotent: re-running upgrades files and restarts units. Never touches an existing DB.
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
SHARE="$PREFIX/share/kuhytrack"
BIN="$PREFIX/bin"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}/kuhytrack"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/kuhytrack"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
say(){ printf '\033[1;36m::\033[0m %s\n' "$*"; }

command -v python3 >/dev/null || { echo "python3 missing: pacman -S python"; exit 1; }
say "python $(python3 -V | cut -d' ' -f2) — no other runtime deps"

# optional but strongly recommended helpers; installed only if pacman is non-interactive-able
for pkg in xprintidle jq curl; do
  command -v "$pkg" >/dev/null || MISSING+=("$pkg")
done
if [ "${MISSING+x}" ]; then
  say "recommended but missing: ${MISSING[*]}  ->  sudo pacman -S --needed ${MISSING[*]}"
fi

mkdir -p "$SHARE" "$BIN" "$CONF" "$DATA" "$UNITS"
cp -r "$SRC/server" "$SRC/web" "$SRC/cli" "$SRC/linux" "$SRC/tools" "$SRC/importers" "$SHARE/" 2>/dev/null || true
chmod +x "$SHARE"/*/*.py 2>/dev/null || true
ln -sf "$SHARE/cli/ktq.py" "$BIN/ktq"

# ---- config (generated once, never overwritten)
if [ ! -f "$CONF/env" ]; then
  TOKEN="$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 32)"
  BINDHOST=127.0.0.1
  if [ "${KT_BIND:-}" = "tailscale" ] && command -v tailscale >/dev/null; then
    BINDHOST="$(tailscale ip -4 2>/dev/null | head -1)"
    [ -n "$BINDHOST" ] || BINDHOST=127.0.0.1
  fi
  cat > "$CONF/env" <<EOF
KT_DB=$DATA/kt.db
KT_HOST=$BINDHOST
KT_PORT=5600
KT_TOKEN=$TOKEN
KT_WEB=$SHARE/web
KT_URL=http://127.0.0.1:5600
KT_POLL=5
KT_AFK_TIMEOUT=180
# KT_EXCLUDE_TITLE_RE=(private|Incognito|KeePass|password)
EOF
  chmod 600 "$CONF/env"
  say "generated $CONF/env  (token: $TOKEN)"
else
  say "kept existing $CONF/env"
fi
# shellcheck disable=SC1091
set -a; . "$CONF/env"; set +a

cat > "$UNITS/kuhytrack.service" <<EOF
[Unit]
Description=kuhytrack activity server
After=network-online.target

[Service]
Type=simple
EnvironmentFile=$CONF/env
ExecStart=/usr/bin/python3 $SHARE/server/kt_server.py
Restart=always
RestartSec=3
# it only ever needs its own data dir and a socket
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$DATA
PrivateTmp=yes
NoNewPrivileges=yes

[Install]
WantedBy=default.target
EOF

cat > "$UNITS/kuhytrack-watcher.service" <<EOF
[Unit]
Description=kuhytrack linux window/afk watcher
After=kuhytrack.service graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
EnvironmentFile=$CONF/env
ExecStart=/usr/bin/python3 $SHARE/linux/kt-watcher-linux.py
Restart=always
RestartSec=5

[Install]
WantedBy=graphical-session.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now kuhytrack.service
systemctl --user enable --now kuhytrack-watcher.service
loginctl enable-linger "$USER" >/dev/null 2>&1 || say "note: run 'sudo loginctl enable-linger $USER' to keep the server up when logged out"

sleep 1
if curl -sf -m 3 "http://${KT_HOST}:${KT_PORT}/health" >/dev/null; then
  say "server healthy on http://${KT_HOST}:${KT_PORT}"
else
  say "server NOT healthy — journalctl --user -u kuhytrack -n 40"; exit 1
fi

cat <<EOF

  dashboard : http://${KT_HOST}:${KT_PORT}/    (token: $KT_TOKEN)
  cli       : ktq today        ktq devices        ktq range --days 7
  logs      : journalctl --user -u kuhytrack-watcher -f
  phone     : copy android/kt-watcher-android.sh to Termux, then
              printf 'KT_URL=http://%s:5600\\nKT_TOKEN=%s\\nKT_DEVICE=pixel6a\\n' "\$(tailscale ip -4 | head -1)" "$KT_TOKEN" > ~/.config/kuhytrack/env
  demo data : python3 $SHARE/tools/kt-seed.py --days 3   (buckets suffixed _demo, delete any time)

EOF
