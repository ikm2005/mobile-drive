#!/usr/bin/env bash
# Repository checks, run by CI and before every push.
#
#   tools/check.sh                    lint, hook smoke tests, generic leak scan
#   tools/check.sh --terms <file>     also fail on any term in <file> (one per line, case-insensitive).
#                                     Keep that file out of git (.terms.local is ignored): a committed
#                                     denylist would publish the very names it guards against.
#   tools/check.sh --release          also fail while the OWNER placeholder is still in the manifests
#
# `claude plugin validate . --strict` is run separately (CI installs the CLI for it).
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

terms=""; release=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --terms) terms="${2:-}"; shift 2 ;;
    --release) release=1; shift ;;
    *) echo "usage: tools/check.sh [--terms <file>] [--release]" >&2; exit 2 ;;
  esac
done

fail=0
ok()  { echo "ok    $*"; }
bad() { echo "FAIL  $*"; fail=1; }

# 1. Lint
for f in scripts/*.sh tools/*.sh; do bash -n "$f" && ok "bash -n $f" || bad "bash -n $f"; done
for f in scripts/*.py; do
  python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read(), sys.argv[1])" "$f" \
    && ok "python syntax $f" || bad "python syntax $f"
done
for f in .claude-plugin/plugin.json .claude-plugin/marketplace.json hooks/hooks.json; do
  python3 -m json.tool "$f" >/dev/null && ok "json $f" || bad "json $f"
done
for f in scripts/*.sh scripts/*.py; do [[ -x "$f" ]] && ok "executable $f" || bad "not executable: $f"; done
if command -v shellcheck >/dev/null; then
  shellcheck -S warning scripts/*.sh tools/*.sh && ok "shellcheck" || echo "warn  shellcheck reported issues (advisory)"
fi

# 2. Hook smoke tests, in a throwaway temp dir
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
nudge() { TMPDIR="$tmp" python3 scripts/mobile-drive-nudge.py; }
shot='{"tool_name":"Bash","tool_input":{"command":"adb exec-out screencap -p > s.png"},"session_id":"check1"}'
first="$(echo "$shot" | nudge)"; second="$(echo "$shot" | nudge)"
[[ "$first" == *additionalContext* && -z "$second" ]] && ok "nudge fires once per session" || bad "nudge once-per-session"
quiet='{"tool_name":"Bash","tool_input":{"command":"agent-device snapshot -i"},"session_id":"check2"}'
[[ -z "$(echo "$quiet" | nudge)" ]] && ok "nudge silent on agent-device" || bad "nudge fired on agent-device"
[[ -z "$(echo "${shot/check1/check3}" | MOBILE_DRIVE_NO_NUDGE=1 nudge)" ]] && ok "nudge opt-out" || bad "nudge opt-out"

export MOBILE_DRIVE_METRICS_DIR="$tmp/metrics"
metrics() { python3 scripts/mobile-drive-metrics.py; }
echo '{"hook_event_name":"PostToolUse","tool_name":"Bash","session_id":"check4","cwd":"/tmp/app","tool_input":{"command":"agent-device press @e3 --settle && agent-device wait '\''label=\"Saved\"'\''"},"tool_response":{"stdout":"ok","stderr":""}}' | metrics
echo '{"hook_event_name":"SubagentStop","agent_type":"mobile-drive:mobile-runner","session_id":"check4","last_assistant_message":"PASS\nflow: add-item.android.ad\ncreated: one item\nnotes: Save needs a name"}' | metrics
grep -qs '"kind": *"runner"' "$MOBILE_DRIVE_METRICS_DIR"/*.jsonl && ok "metrics records the namespaced runner" || bad "metrics runner event"
[[ ! -s "$MOBILE_DRIVE_METRICS_DIR/errors.log" ]] && ok "metrics hook raised no errors" || bad "metrics errors: $(cat "$MOBILE_DRIVE_METRICS_DIR/errors.log")"
before="$(cat "$MOBILE_DRIVE_METRICS_DIR"/*.jsonl | wc -l)"
echo '{"hook_event_name":"SubagentStop","agent_type":"mobile-drive:mobile-runner","session_id":"check5","last_assistant_message":"PASS"}' | MOBILE_DRIVE_NO_METRICS=1 metrics
[[ "$(cat "$MOBILE_DRIVE_METRICS_DIR"/*.jsonl | wc -l)" == "$before" ]] && ok "metrics opt-out" || bad "metrics opt-out"
python3 scripts/mobile-drive-stats.py --since 30 >/dev/null && ok "stats digest runs" || bad "stats digest"

# 3. Generic leak scan over tracked-or-new files (this script excluded: it holds the patterns)
files=()
while IFS= read -r f; do files+=("$f"); done < <(
  { git ls-files 2>/dev/null; git ls-files --others --exclude-standard 2>/dev/null; } | sort -u | grep -v '^tools/check.sh$')
[[ ${#files[@]} -gt 0 ]] || while IFS= read -r f; do files+=("${f#./}"); done < <(find . -type f -not -path './.git/*' -not -name check.sh)
scan() { # <label> <extended regex> [grep -v filter]
  local hits; hits="$(grep -nIE "$2" "${files[@]}" 2>/dev/null | { [[ -n "${3:-}" ]] && grep -vE "$3" || cat; })"
  [[ -z "$hits" ]] && ok "no $1" || { bad "$1:"; echo "$hits" | head -10 | sed 's/^/      /'; }
}
scan "absolute home paths" '(/Users|/home)/[A-Za-z0-9_.-]+/'
scan "email addresses" '[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z.]{2,}' 'noreply@|@example\.(com|org)'
scan "bundle ids outside com.example" '\b(com|org|net|io|de|uk)\.[a-z0-9_]+\.[A-Za-z0-9_.]+' '\b(com|org)\.(example|android|apple)\b|github\.com|claude\.com|anthropic\.com'
[[ -z "$(find . -name .DS_Store -not -path './.git/*')" ]] && ok "no .DS_Store" || bad ".DS_Store files present"
[[ ! -e CLAUDE.md ]] && ok "no root CLAUDE.md (plugins don't load it)" || bad "root CLAUDE.md"

if [[ -n "$terms" ]]; then
  [[ -f "$terms" ]] || { bad "terms file not found: $terms"; exit 1; }
  pattern="$(grep -v '^[[:space:]]*$' "$terms" | paste -sd'|' -)"
  hits="$(grep -nIiE "$pattern" "${files[@]}" 2>/dev/null)"
  [[ -z "$hits" ]] && ok "no project-specific terms ($(grep -cv '^[[:space:]]*$' "$terms") checked)" \
    || { bad "project-specific terms:"; echo "$hits" | head -20 | sed 's/^/      /'; }
fi

if grep -qs 'OWNER' .claude-plugin/plugin.json .claude-plugin/marketplace.json LICENSE README.md; then
  if [[ $release -eq 1 ]]; then bad "OWNER placeholder still present"; else echo "warn  OWNER placeholder still present (fails with --release)"; fi
fi

[[ $fail -eq 0 ]] && echo "all checks passed" || echo "checks FAILED"
exit $fail
