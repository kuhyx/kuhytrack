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

  # Ask the server for everything after our watermark rather than the newest N events.
  # aw-server returns events NEWEST-first, so a bare limit=2000 against a bucket that
  # has already backfilled (aw-android imports ~8 days of UsageStats on first run --
  # 6473 events here) takes the newest 2000, records max(those) as the watermark, and
  # strands the older 4473 forever. `start` + a high limit makes the first sync total.
  events=$(curl -sf -m 60 --get \
    --data-urlencode "start=${since}" --data-urlencode "limit=50000" \
    "${AW_URL}/api/0/buckets/${src}/events") || return 1
  # `start` is inclusive, so drop the watermark event itself to avoid re-pushing it.
  new=$(printf '%s' "$events" | jq --arg s "$since" '[.[] | select(.timestamp > $s)]')
  n=$(printf '%s' "$new" | jq 'length')
  [ "$n" -eq 0 ] && return 0

  # strip local ids so the server assigns its own
  payload=$(printf '%s' "$new" | jq '[.[] | {timestamp,duration,data}]')
  # The server echoes every inserted event back, which is ~1.5 MB for a first sync of a
  # backfilled bucket. Send in chunks and judge success on the HTTP status alone -- a
  # long body over a slow phone link was being reported as a failed push even though
  # the events had landed. -m scales with chunk size rather than a flat 30s.
  total=0
  for off in $(seq 0 500 "$((n - 1))"); do
    chunk=$(printf '%s' "$payload" | jq --argjson o "$off" '.[$o:$o+500]')
    code=$(curl -s -m 120 -o /dev/null -w '%{http_code}' \
      -X POST -H 'Content-Type: application/json' $(auth) \
      --data "$chunk" "${KT_URL}/api/0/buckets/${dst}/events")
    case "$code" in
      2*) total=$((total + $(printf '%s' "$chunk" | jq 'length'))) ;;
      *)  echo "$(date +%H:%M:%S) !! POST ${dst} returned HTTP ${code} at offset ${off}"
          return 1 ;;
    esac
  done
  n="$total"

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
