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

# On X11 xprintidle is not "recommended", it is required: without it the only remaining
# idle source is the /dev/input atime scan, which relatime freezes at boot. The watcher
# then reports permanent idle and records ZERO window events. Installing a tracker that
# silently captures nothing is worse than refusing to install.
if [ "${XDG_SESSION_TYPE:-}" = "x11" ] && ! command -v xprintidle >/dev/null; then
  echo "install: xprintidle is required on X11 -- without it the watcher reports" >&2
  echo "  permanent idle and records no window events at all." >&2
  echo "  Run: sudo pacman -S --needed xprintidle" >&2
  echo "  (override with KT_SKIP_IDLE_CHECK=1 if you know what you are doing)" >&2
  [ "${KT_SKIP_IDLE_CHECK:-}" = "1" ] || exit 1
fi

mkdir -p "$SHARE" "$BIN" "$CONF" "$DATA" "$UNITS"
# Every component ships, including hooks/ (the budget enforcement loop, which is the
# whole argument for running this over upstream AW) and android/ (staged here so the
# phone scripts can be copied to Termux). No 2>/dev/null || true: a missing source dir
# is a broken install, and silently "succeeding" is how the budget hook shipped dead.
for d in server web cli linux tools importers hooks android; do
  [ -d "$SRC/$d" ] || { echo "install: missing source dir $SRC/$d" >&2; exit 1; }
  cp -r "$SRC/$d" "$SHARE/"
done
chmod +x "$SHARE"/*/*.py "$SHARE"/*/*.sh
ln -sf "$SHARE/cli/ktq.py" "$BIN/ktq"

# ---- config (generated once, never overwritten)
if [ ! -f "$CONF/env" ]; then
  TOKEN="$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 32)"
  BINDHOST=127.0.0.1
  # If the tailnet bind was explicitly asked for, failing to get an IP is an error, not
  # a reason to quietly bind loopback -- that looks like a working install while the
  # phone can never reach it.
  if [ "${KT_BIND:-}" = "tailscale" ]; then
    command -v tailscale >/dev/null || {
      echo "install: KT_BIND=tailscale but tailscale is not installed" >&2; exit 1; }
    # '|| true': tailscale exits non-zero when logged out, and under `set -e` a bare
    # assignment from a failing substitution kills the script before the message below.
    BINDHOST="$(tailscale ip -4 2>/dev/null | head -1 || true)"
    [ -n "$BINDHOST" ] || {
      echo "install: KT_BIND=tailscale but no tailscale IPv4 address." >&2
      echo "  'tailscale status' probably says Logged out. Run: sudo tailscale up" >&2
      echo "  Then re-run this installer. Refusing to silently bind loopback." >&2
      exit 1; }
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
  # The env file is kept (never clobber a working token or db path), but an explicit
  # KT_BIND must still take effect -- otherwise `KT_BIND=tailscale ./install-arch.sh`
  # on an existing install prints "kept existing" and silently stays on loopback,
  # which is the same silent no-op this script now refuses everywhere else.
  if [ "${KT_BIND:-}" = "tailscale" ]; then
    command -v tailscale >/dev/null || {
      echo "install: KT_BIND=tailscale but tailscale is not installed" >&2; exit 1; }
    NEWHOST="$(tailscale ip -4 2>/dev/null | head -1 || true)"
    [ -n "$NEWHOST" ] || {
      echo "install: KT_BIND=tailscale but no tailscale IPv4 address." >&2
      echo "  'tailscale status' probably says Logged out. Run: sudo tailscale up" >&2
      echo "  Then re-run this installer. Refusing to silently keep the old bind." >&2
      exit 1; }
    sed -i "s|^KT_HOST=.*|KT_HOST=$NEWHOST|" "$CONF/env"
    say "rebound to $NEWHOST in $CONF/env"
  else
    say "kept existing $CONF/env"
  fi
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

# The budget hook is what turns this from a diary into a controller. Installed and
# ready, but left DISABLED: per docs/07-adversarial.md the first budget should be a
# notification you cannot unsee, not a lock you resent -- edit budgets.json, then
#   systemctl --user enable --now kuhytrack-budget.timer
cat > "$UNITS/kuhytrack-budget.service" <<EOF
[Unit]
Description=kuhytrack daily budget enforcement
After=kuhytrack.service

[Service]
Type=oneshot
EnvironmentFile=$CONF/env
# exit 10 means "a budget was blown and its action ran" -- success, not failure
SuccessExitStatus=0 10
ExecStart=/usr/bin/python3 $SHARE/hooks/kt-budget.py --enforce
EOF

cat > "$UNITS/kuhytrack-budget.timer" <<EOF
[Unit]
Description=run kuhytrack budget enforcement every 15 minutes

[Timer]
OnCalendar=*:0/15
Persistent=true

[Install]
WantedBy=timers.target
EOF

# A systemd --user unit does not inherit DISPLAY/XAUTHORITY unless the session exports
# them. Without DISPLAY the watcher's X11 window source silently bails and you get
# AFK-only data. Harmless to run when they are already present (they are, under i3).
systemctl --user import-environment DISPLAY XAUTHORITY 2>/dev/null || true

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
  budgets   : $CONF/budgets.json, then  systemctl --user enable --now kuhytrack-budget.timer
              (installed but disabled — start with a notification, not a lock)
  import AW : python3 $SHARE/importers/kt-import.py awdb --file ~/.local/share/activitywatch/aw-server/peewee-sqlite.v2.db
  demo data : python3 $SHARE/tools/kt-seed.py --days 3   (buckets suffixed _demo, delete any time)

EOF
