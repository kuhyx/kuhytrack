#!/data/data/com.termux/files/usr/bin/bash
# kt-watcher-android — live Android watcher for kuhytrack. Needs Termux + root (Magisk).
#
# Why root: on Android 10+ there is no unprivileged way to read the foreground app
# continuously. UsageStatsManager requires PACKAGE_USAGE_STATS, which requires an
# installed APK holding the permission. You have Magisk on the 6a, so `su -c dumpsys`
# is the shortest path from zero to working. No-root alternative: kt-bridge-awandroid.sh
#
# Setup (once):
#   pkg install curl termux-api termux-services
#   termux-wake-lock
#   # grant Termux root once when the su prompt appears
#   # Settings > Apps > Termux > Battery > Unrestricted     <-- mandatory, else Doze kills it
#   mkdir -p ~/.config/kuhytrack && cp kt-watcher-android.sh ~/bin/
#   # autostart: install Termux:Boot, then
#   mkdir -p ~/.termux/boot && printf '#!/data/data/com.termux/files/usr/bin/sh\ntermux-wake-lock\n~/bin/kt-watcher-android.sh &\n' > ~/.termux/boot/kt
#   chmod +x ~/.termux/boot/kt ~/bin/kt-watcher-android.sh
#
# Config: ~/.config/kuhytrack/env  (KT_URL, KT_TOKEN, KT_DEVICE, KT_POLL)
set -u
[ -f "$HOME/.config/kuhytrack/env" ] && . "$HOME/.config/kuhytrack/env"

KT_URL="${KT_URL:-http://100.64.0.1:5600}"     # tailscale IP of the Arch box
KT_TOKEN="${KT_TOKEN:-}"
KT_DEVICE="${KT_DEVICE:-pixel6a}"
KT_POLL="${KT_POLL:-10}"
KT_AFK_TIMEOUT="${KT_AFK_TIMEOUT:-180}"
PULSETIME=$((KT_POLL + 55))
SPOOL="$HOME/.cache/kuhytrack/spool.jsonl"
WIN_BUCKET="kt-watcher-window_${KT_DEVICE}"
AFK_BUCKET="kt-watcher-afk_${KT_DEVICE}"
mkdir -p "$(dirname "$SPOOL")"

auth() { [ -n "$KT_TOKEN" ] && printf -- "-HAuthorization: Bearer %s" "$KT_TOKEN"; }

post() { # post <path> <json>
  curl -sf -m 8 -X POST -H "Content-Type: application/json" $(auth) \
       --data "$2" "${KT_URL}$1" >/dev/null 2>&1
}

ensure_buckets() {
  post "/api/0/buckets/${WIN_BUCKET}" \
    "{\"client\":\"kt-watcher-android\",\"type\":\"currentwindow\",\"hostname\":\"${KT_DEVICE}\",\"device\":\"${KT_DEVICE}\"}"
  post "/api/0/buckets/${AFK_BUCKET}" \
    "{\"client\":\"kt-watcher-afk\",\"type\":\"afkstatus\",\"hostname\":\"${KT_DEVICE}\",\"device\":\"${KT_DEVICE}\"}"
}

flush_spool() {
  [ -s "$SPOOL" ] || return 0
  tmp="${SPOOL}.work"; mv "$SPOOL" "$tmp"
  while IFS= read -r line; do
    b=$(printf '%s' "$line" | cut -d'|' -f1)
    e=$(printf '%s' "$line" | cut -d'|' -f2-)
    post "/api/0/buckets/${b}/heartbeat?pulsetime=${PULSETIME}" "$e" || { printf '%s\n' "$line" >> "$SPOOL"; }
  done < "$tmp"
  rm -f "$tmp"
}

hb() { # hb <bucket> <data-json>
  ts=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
  ev="{\"timestamp\":\"${ts}\",\"duration\":0,\"data\":$2}"
  if ! post "/api/0/buckets/$1/heartbeat?pulsetime=${PULSETIME}" "$ev"; then
    printf '%s|%s\n' "$1" "$ev" >> "$SPOOL"
  fi
}

json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }

screen_on() {
  # mWakefulness=Awake|Asleep|Dozing ; also covers AOD as not-awake
  su -c "dumpsys power" 2>/dev/null | grep -q "mWakefulness=Awake"
}

foreground() {
  # topResumedActivity on Android 10+, mResumedActivity on older
  su -c "dumpsys activity activities" 2>/dev/null \
    | grep -m1 -E "topResumedActivity|mResumedActivity" \
    | sed -E 's/.* ([a-zA-Z0-9_.]+)\/([a-zA-Z0-9_.$]+).*/\1 \2/'
}

command -v su >/dev/null 2>&1 || { echo "!! no su in PATH — use kt-bridge-awandroid.sh instead"; exit 1; }
su -c true 2>/dev/null || { echo "!! root denied; grant Termux root in Magisk"; exit 1; }
ensure_buckets
echo "kt-watcher-android -> ${KT_URL} as ${KT_DEVICE}"

last_active=$(date +%s)
while :; do
  flush_spool
  if screen_on; then
    last_active=$(date +%s)
    hb "$AFK_BUCKET" '{"status":"not-afk"}'
    fg=$(foreground)
    pkg=$(printf '%s' "$fg" | awk '{print $1}')
    cls=$(printf '%s' "$fg" | awk '{print $2}')
    if [ -n "$pkg" ]; then
      hb "$WIN_BUCKET" "{\"app\":\"$(json_escape "$pkg")\",\"package\":\"$(json_escape "$pkg")\",\"classname\":\"$(json_escape "$cls")\",\"title\":\"\"}"
    fi
  else
    hb "$AFK_BUCKET" '{"status":"afk"}'
  fi
  sleep "$KT_POLL"
done
