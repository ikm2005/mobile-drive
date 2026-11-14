#!/usr/bin/env bash
# Scenario tests for scripts/replay.sh against the fake agent-device in tools/stubs (no device).
#
#   bash tools/test_replay.sh
#
# Prints `ok   <name>` / `FAIL <name>` per assertion and ends with `all replay tests passed`.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2
root="$(pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
PATH="$root/tools/stubs:$PATH"
export PATH
export STUB_LOG="$tmp/stub.log"
export STUB_REPLAY_COPY="$tmp/replayed.ad"
unset MOBILE_SERIAL MOBILE_UDID MOBILE_POPUP_SELECTORS MOBILE_REPLAY_TIMEOUT_MS STUB_ALERT STUB_EXISTS STUB_REPLAY STUB_OPEN

fails=0
expect() { # <name> <command…>: ok when the command succeeds
  local name="$1"; shift
  if "$@"; then echo "ok   $name"; else echo "FAIL $name"; fails=$((fails + 1)); fi
}
has() { [[ "$1" == *"$2"* ]]; }
logged() { grep -Fxq -- "$1" "$STUB_LOG"; }
line_of() { grep -Fxn -- "$1" "$STUB_LOG" | head -1 | cut -d: -f1; }
before() { # <first log line> <second log line>: both logged, first one earlier
  local a b; a="$(line_of "$1")"; b="$(line_of "$2")"
  [[ -n "$a" && -n "$b" && "$a" -lt "$b" ]]
}
run() { # [VAR=value…] <replay.sh args…>: sets $out (stdout), $err (stderr), $rc; resets the stub
  rm -rf "$STUB_LOG" "$STUB_LOG.state" "$STUB_REPLAY_COPY"; : > "$STUB_LOG"
  mkdir -p "$tmp/t"  # replay.sh's own mktemp -d copy of the flow lands here and goes with the trap
  local envs=()
  while [[ $# -gt 0 && "$1" == *=* && "$1" != -* && "$1" != */* ]]; do envs+=("$1"); shift; done
  out="$(env TMPDIR="$tmp/t" ${envs[@]+"${envs[@]}"} bash scripts/replay.sh "$@" 2>"$tmp/err")"; rc=$?
  err="$(cat "$tmp/err")"
}

flow="$tmp/add-item.android.ad"
cat > "$flow" <<'EOF'
# Test flow for the stub: six steps, a comment, a context line and a blank line.
context platform=android device="Pixel 9" kind=emulator theme=unknown
open "com.example.items" --relaunch
wait "label=\"Items\"" 20000

press 'label="Save"'
wait "label=\"Saved\"" 10000
open "com.example.items" --relaunch
close
EOF
cp "$flow" "$tmp/pristine.ad"
target="--platform android --serial emulator-5554"
close_sel='role="button" label="Close"'

# (a) PASS
run "$flow" android
expect "pass: exit 0" [ "$rc" -eq 0 ]
expect "pass: one PASS line with the step count" [ "$out" == "PASS replay add-item.android.ad (6 steps)" ]
expect "pass: stale session closed first" [ "$(head -1 "$STUB_LOG")" == "close --platform android" ]
expect "pass: preflight relaunches the app on the default serial" \
  logged "open com.example.items --relaunch $target"
expect "pass: preflight waits for a stable screen" logged "wait stable 3000 20000 $target"
expect "pass: alert checked, none dismissed" eval '! grep -q "^alert dismiss" "$STUB_LOG"'
expect "pass: the default Close popup is looked for" logged "is exists $close_sel $target"
expect "pass: no press when there is no popup" eval '! grep -q "^press" "$STUB_LOG"'
expect "pass: preflight runs before the replay" before "open com.example.items --relaunch $target" \
  "$(grep '^replay ' "$STUB_LOG" | head -1)"
expect "pass: replay gets the default time limit and the target" \
  eval 'grep -q "^replay .*add-item.android.ad --timeout 300000 $target\$" "$STUB_LOG"'
expect "pass: replayed flow keeps its name, not the original path" \
  eval '! grep -Fq "replay $flow " "$STUB_LOG"'
expect "pass: first open loses --relaunch" [ "$(grep -m1 '^open ' "$STUB_REPLAY_COPY")" == 'open "com.example.items"' ]
expect "pass: a later open keeps --relaunch" \
  [ "$(grep '^open ' "$STUB_REPLAY_COPY" | sed -n 2p)" == 'open "com.example.items" --relaunch' ]
expect "pass: the rest of the flow is passed through unchanged" \
  cmp -s <(grep -v '^open ' "$STUB_REPLAY_COPY") <(grep -v '^open ' "$tmp/pristine.ad")
expect "pass: the original flow file is unchanged" cmp -s "$flow" "$tmp/pristine.ad"

run MOBILE_SERIAL=emulator-5556 "$flow" android
expect "serial: \$MOBILE_SERIAL is used" logged "open com.example.items --relaunch --platform android --serial emulator-5556"
run MOBILE_SERIAL=emulator-5556 "$flow" android R5CT000TEST
expect "serial: the third argument wins" logged "open com.example.items --relaunch --platform android --serial R5CT000TEST"
run "$flow" ios 00000000-AAAA-BBBB-CCCC-000000000000
expect "ios: exit 0 with an explicit udid" [ "$rc" -eq 0 ]
expect "ios: target is the udid" \
  logged "open com.example.items --relaunch --platform ios --udid 00000000-AAAA-BBBB-CCCC-000000000000"

# (b) divergence
run STUB_REPLAY=diverge "$flow" android
first="$(printf '%s\n' "$out" | head -1)"
expect "diverged: exit 1" [ "$rc" -eq 1 ]
expect "diverged: first line starts with DIVERGED replay" has "$first" "DIVERGED replay add-item.android.ad (6 steps)"
expect "diverged: the step line is passed through" \
  has "$out" "Replay failed at step 3 (press 'label=\"Save\"'): no element matches"
expect "diverged: repair details are passed through" has "$out" "resume: replay --from 3 --plan-digest"
expect "diverged: diagnostic id and log lines are dropped" eval '! grep -qE "^Diagnostic(s Log| ID):" <<<"$out"'
expect "diverged: the original flow file is unchanged" cmp -s "$flow" "$tmp/pristine.ad"

# (b2) time limit
run MOBILE_REPLAY_TIMEOUT_MS=45000 "$flow" android
expect "timeout: \$MOBILE_REPLAY_TIMEOUT_MS is passed to replay" \
  eval 'grep -q "^replay .*add-item.android.ad --timeout 45000 $target\$" "$STUB_LOG"'
run STUB_REPLAY=timeout "$flow" android
expect "timeout: exit 1" [ "$rc" -eq 1 ]
expect "timeout: one line" [ "$(printf '%s\n' "$out" | wc -l | tr -d ' ')" -eq 1 ]
expect "timeout: the line starts with DIVERGED replay and gives the limit" \
  has "$out" "DIVERGED replay add-item.android.ad (6 steps) — timed out after 300 s, no step named."
expect "timeout: it warns that the steps so far have run" has "$out" "check the screen and the data before repeating"
expect "timeout: it does not claim the report names the step" eval '! grep -q "names the step" <<<"$out"'

# (b3) app cannot be opened (device held by another folder's session)
run STUB_OPEN=in_use "$flow" android
first="$(printf '%s\n' "$out" | head -1)"
expect "no launch: exit 1" [ "$rc" -eq 1 ]
expect "no launch: first line says DIVERGED and that no step ran" \
  [ "$first" == "DIVERGED replay add-item.android.ad (6 steps) — the app could not be opened, no step ran:" ]
expect "no launch: the error line is passed through" has "$out" "Error (DEVICE_IN_USE): Device is already in use"
expect "no launch: the hint with the close command is passed through" \
  has "$out" "Run agent-device close --session cwd:0123abcd:android"
expect "no launch: diagnostic id and log lines are dropped" eval '! grep -qE "^Diagnostic(s Log| ID):" <<<"$out"'
expect "no launch: replay is never called" eval '! grep -q "^replay " "$STUB_LOG"'
expect "no launch: the other session is not closed for the user" \
  eval '! grep -q "^close --session" "$STUB_LOG"'
expect "no launch: the text does not say the report names the step" eval '! grep -q "names the step" <<<"$out"'
expect "no launch: the original flow file is unchanged" cmp -s "$flow" "$tmp/pristine.ad"

# (c) launch popups
run STUB_EXISTS="$close_sel" "$flow" android
expect "popup: exit 0" [ "$rc" -eq 0 ]
expect "popup: the Close button is pressed before the replay" before "press $close_sel $target" \
  "$(grep '^replay ' "$STUB_LOG" | head -1)"
expect "popup: PASS line names the dismissed popup" \
  [ "$out" == "PASS replay add-item.android.ad (6 steps, dismissed popup first)" ]
expect "popup: pressed once, then the second pass finds nothing" [ "$(grep -c '^press ' "$STUB_LOG")" -eq 1 ]

custom='id="paywall_close"'
run MOBILE_POPUP_SELECTORS="$custom" STUB_EXISTS="$custom" "$flow" android
expect "custom popup: the selector from \$MOBILE_POPUP_SELECTORS is pressed" logged "press $custom $target"
expect "custom popup: the default Close selector is not used" eval '! grep -Fq "$close_sel" "$STUB_LOG"'
expect "custom popup: PASS line names it" has "$out" "dismissed popup first"

two="$(printf '%s\n%s' 'label="Not now"' "$custom")"
run MOBILE_POPUP_SELECTORS="$two" STUB_EXISTS="$custom" "$flow" android
expect "popup list: every selector is tried in turn" \
  before "is exists label=\"Not now\" $target" "press $custom $target"

run STUB_ALERT=1 "$flow" android
expect "alert: a system alert is dismissed" logged "alert dismiss $target"
expect "alert: PASS line names it" has "$out" "dismissed alert first)"
run STUB_ALERT=1 STUB_EXISTS="$close_sel" "$flow" android
expect "alert and popup: both are named" has "$out" "dismissed alert+popup first)"

grep -v '^open ' "$flow" > "$tmp/no-open.ad"
run STUB_EXISTS="$close_sel" "$tmp/no-open.ad" android
expect "no open line: no preflight" eval '! grep -qE "^(open|wait|is|press|alert) " "$STUB_LOG"'
expect "no open line: still replays and passes" [ "$rc" -eq 0 ]

# (d) usage errors
usage="usage: replay.sh <flow.ad> android|ios [serial|udid]"
run "$tmp/missing.ad" android
expect "usage: missing flow file exits 2" [ "$rc" -eq 2 ]
expect "usage: missing flow file prints the usage line" [ "$err" == "$usage" ]
expect "usage: nothing is sent to the device" [ ! -s "$STUB_LOG" ]
run "$flow" windows
expect "usage: bad platform exits 2" [ "$rc" -eq 2 ]
expect "usage: bad platform prints the usage line" [ "$err" == "$usage" ]
run
expect "usage: no arguments exits 2" [ "$rc" -eq 2 ]
expect "usage: no arguments prints the usage line" [ "$err" == "$usage" ]
expect "usage: stdout stays empty" [ -z "$out" ]

if [[ $fails -eq 0 ]]; then echo "all replay tests passed"; else echo "$fails replay test(s) FAILED"; fi
[[ $fails -eq 0 ]]
