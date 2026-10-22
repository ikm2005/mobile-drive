#!/usr/bin/env bash
# One-call replay of a saved flow. Prints one line: PASS, or the divergence report to repair from.
#
#   replay.sh <flow.ad> android [serial]     (serial defaults to $MOBILE_SERIAL or emulator-5554)
#   replay.sh <flow.ad> ios [udid]           (udid defaults to $MOBILE_UDID or the booted simulator)
#
# 1. closes a stale session left by an earlier run (it would lock the device);
# 2. preflight: relaunches the app, waits until the screen is stable, and dismisses launch popups
#    (system alert, plus any button in $MOBILE_POPUP_SELECTORS — default a "Close" button), which
#    otherwise break a replay at a random step;
# 3. replays the flow attached to the running app (its first `open … --relaunch` loses --relaunch).
# Exit 0 = PASS, 1 = divergence (report printed; repair per `agent-device help scripting`), 2 = usage.
# Never edits the .ad file. A flow that creates data creates it again on every replay.
set -uo pipefail

flow="${1:-}"; platform="${2:-}"
[[ -f "$flow" && ( "$platform" == android || "$platform" == ios ) ]] || {
  echo "usage: replay.sh <flow.ad> android|ios [serial|udid]" >&2; exit 2; }

if [[ "$platform" == android ]]; then
  target=(--platform android --serial "${3:-${MOBILE_SERIAL:-emulator-5554}}")
else
  udid="${3:-${MOBILE_UDID:-$(xcrun simctl list devices booted | grep -oE '[0-9A-F-]{36}' | head -1)}}"
  target=(--platform ios --udid "$udid")
fi
app=$(grep -m1 -oE '^open "[^"]+"' "$flow" | cut -d'"' -f2)
# Selectors of buttons that close launch popups (paywall, onboarding, "what's new"), one per line.
# Set per project in MOBILE_POPUP_SELECTORS; the default covers a plain "Close" button.
popups="${MOBILE_POPUP_SELECTORS:-role=\"button\" label=\"Close\"}"

agent-device close --platform "$platform" >/dev/null 2>&1 || true

# Preflight — none of this is recorded or part of the flow.
dismissed=""
if [[ -n "$app" ]]; then
  agent-device open "$app" --relaunch "${target[@]}" >/dev/null 2>&1
  for _ in 1 2; do
    agent-device wait stable 3000 20000 "${target[@]}" >/dev/null 2>&1
    hit=""
    if agent-device alert get "${target[@]}" --json 2>/dev/null | grep -q '"success": *true' \
       && ! agent-device alert get "${target[@]}" 2>&1 | grep -qiE 'no (active )?alert'; then
      agent-device alert dismiss "${target[@]}" >/dev/null 2>&1 && hit=1 && dismissed="${dismissed:+$dismissed+}alert"
    fi
    while IFS= read -r sel; do
      [[ -z "$sel" ]] && continue
      if agent-device is exists "$sel" "${target[@]}" >/dev/null 2>&1; then
        agent-device press "$sel" "${target[@]}" >/dev/null 2>&1 \
          && hit=1 && dismissed="${dismissed:+$dismissed+}popup" && break
      fi
    done <<<"$popups"
    [[ -z "$hit" ]] && break
  done
  agent-device close --platform "$platform" >/dev/null 2>&1 || true
fi

attached="$(mktemp -d)/$(basename "$flow")"
awk '!done && /^open "/ { sub(/ --relaunch/, ""); done = 1 } { print }' "$flow" > "$attached"

out=$(agent-device replay "$attached" "${target[@]}" 2>&1); rc=$?
note="$(grep -cvE '^(#|context|[[:space:]]*$)' "$flow") steps${dismissed:+, dismissed $dismissed first}"
if [[ $rc -eq 0 ]]; then
  echo "PASS replay $(basename "$flow") ($note)"; exit 0
fi
echo "DIVERGED replay $(basename "$flow") ($note) — the report below names the step; finish it by hand:"
grep -vE '^(Diagnostic ID|Diagnostics Log):' <<<"$out" | head -30
exit 1
