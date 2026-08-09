#!/data/data/com.termux/files/usr/bin/bash
# kt-bridge-awandroid — no-root Android path.
#
# Trick: the official ActivityWatch Android app runs aw-server-rust *inside the phone*
# on 127.0.0.1:5600. It holds PACKAGE_USAGE_STATS, so it can read the foreground app
# and screen unlocks legally without root. Termux can reach that loopback port. So:
#
#     aw-android (collects)  ->  localhost:5600  ->  this bridge  ->  kuhytrack server
#
# You get correct UsageStats-based data with zero root, at the cost of a ~5 min lag
# and a dependency on aw-android staying installed and not being killed.
#
# It is incremental: it remembers the last timestamp pushed per bucket and only sends
# newer events, using /events (not /heartbeat) so aw-android's own merging is preserved.
#
# Setup:
#   pkg install curl jq
#   install ActivityWatch from F-Droid/Play, grant "Usage access", leave it running
#   Settings > Apps > {Termux,ActivityWatch} > Battery > Unrestricted
#   crontab-free loop: run under termux-services, or ~/.termux/boot/kt-bridge
set -u
[ -f "$HOME/.config/kuhytrack/env" ] && . "$HOME/.config/kuhytrack/env"

KT_URL="${KT_URL:-http://100.64.0.1:5600}"
KT_TOKEN="${KT_TOKEN:-}"
KT_DEVICE="${KT_DEVICE:-pixel6a}"
AW_URL="${AW_URL:-http://127.0.0.1:5600}"
INTERVAL="${KT_BRIDGE_INTERVAL:-300}"
STATE="$HOME/.cache/kuhytrack/bridge-state.json"
mkdir -p "$(dirname "$STATE")"
[ -f "$STATE" ] || echo '{}' > "$STATE"

auth() { [ -n "$KT_TOKEN" ] && printf -- "-HAuthorization: Bearer %s" "$KT_TOKEN"; }

push_bucket() { # push_bucket <aw_bucket_id>
  src="$1"
  dst="${src}_${KT_DEVICE}"
  since=$(jq -r --arg b "$src" '.[$b] // "1970-01-01T00:00:00Z"' "$STATE")

  meta=$(curl -sf -m 10 "${AW_URL}/api/0/buckets/${src}") || return 1
  btype=$(printf '%s' "$meta" | jq -r '.type // "currentwindow"')
  curl -sf -m 10 -X POST -H 'Content-Type: application/json' $(auth) \
    --data "{\"client\":\"kt-bridge-awandroid\",\"type\":\"${btype}\",\"hostname\":\"${KT_DEVICE}\",\"device\":\"${KT_DEVICE}\"}" \
    "${KT_URL}/api/0/buckets/${dst}" >/dev/null || return 1

  events=$(curl -sf -m 20 "${AW_URL}/api/0/buckets/${src}/events?limit=2000") || return 1
  new=$(printf '%s' "$events" | jq --arg s "$since" '[.[] | select(.timestamp > $s)]')
  n=$(printf '%s' "$new" | jq 'length')
  [ "$n" -eq 0 ] && return 0

  # strip local ids so the server assigns its own
  payload=$(printf '%s' "$new" | jq '[.[] | {timestamp,duration,data}]')
  curl -sf -m 30 -X POST -H 'Content-Type: application/json' $(auth) \
    --data "$payload" "${KT_URL}/api/0/buckets/${dst}/events" >/dev/null || return 1

  newest=$(printf '%s' "$new" | jq -r 'max_by(.timestamp).timestamp')
  jq --arg b "$src" --arg t "$newest" '.[$b]=$t' "$STATE" > "${STATE}.tmp" && mv "${STATE}.tmp" "$STATE"
  echo "$(date +%H:%M:%S) pushed $n events  ${src} -> ${dst}"
}

echo "kt-bridge-awandroid: ${AW_URL} -> ${KT_URL} every ${INTERVAL}s"
while :; do
  if buckets=$(curl -sf -m 10 "${AW_URL}/api/0/buckets/"); then
    for b in $(printf '%s' "$buckets" | jq -r 'keys[]'); do
      push_bucket "$b" || echo "$(date +%H:%M:%S) !! failed on $b (kuhytrack unreachable? retrying next cycle)"
    done
  else
    echo "$(date +%H:%M:%S) !! aw-android not reachable on ${AW_URL} — is the app running?"
  fi
  sleep "$INTERVAL"
done
