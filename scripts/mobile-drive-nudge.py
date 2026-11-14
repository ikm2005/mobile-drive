#!/usr/bin/env python3
"""PostToolUse nudge for Bash: when an agent drives a mobile app the screenshot-and-pixel way,
remind it that the accessibility tree comes first. It never blocks: the command has already run,
because screenshots and coordinate taps are the legitimate fallback when the tree can't answer
(canvas, games, custom-drawn views, a purely visual check).

Watched (the expensive path the mobile-drive skill replaces when the tree works):
  adb … screencap · uiautomator dump · xcrun simctl io … screenshot ·
  adb … shell input tap|swipe · idb ui tap|swipe · axe tap|swipe
Each matching Bash call is counted per session. A reminder is shown on the 1st, 4th and 12th raw
call only (one look is fine; four is a walk; twelve is a loop), never more than three per session,
and nothing is printed for any other call. Opt out with MOBILE_DRIVE_NO_NUDGE=1.
"""
import json
import os
import re
import sys
import tempfile

PATTERNS = [
    r"\badb\b[^|;&]*\b(exec-out|shell)\s+(\S+\s+)*screencap\b",
    r"\buiautomator\s+dump\b",
    r"\bsimctl\s+io\s+\S+\s+screenshot\b",
    r"\badb\b[^|;&]*\bshell\s+input\s+(tap|swipe|draganddrop)\b",
    r"\bidb\s+ui\s+(tap|swipe)\b",
    r"\baxe\s+(tap|swipe)\b",
]

FIRST = (
    "Reminder (mobile-drive): screenshots and pixel taps are the fallback, not the first move. "
    "Start with the accessibility tree — `agent-device snapshot -i` (a few hundred tokens with @refs) "
    "and `agent-device press @ref --settle` — and come back to screenshots only where the tree can't "
    "answer (canvas/game/custom-drawn view, a visual check, an element missing from the tree). Then "
    "prefer `agent-device screenshot <path> --scale 0.5 --crop-on '<selector>'` over a full-resolution "
    "capture. Load the `mobile-drive:drive` skill now for the commands and rules, and hand any walk of "
    "more than ~5 steps to the `mobile-drive:mobile-runner` agent. If that is already what you are "
    "doing, carry on."
)
SECOND = (
    "Reminder 2 of 3 (mobile-drive): {n} raw screenshot/tap calls in this session so far. A full "
    "screenshot costs roughly 2,400–3,300 tokens and is re-read on every later turn — this is now a "
    "walk, not a look. Load the `mobile-drive:drive` skill and delegate the walk to "
    "`mobile-drive:mobile-runner` now. Fine if the tree can't answer here (canvas, visual check)."
)
THIRD = (
    "Last reminder (mobile-drive): {n} raw screenshot/tap calls so far — switch to `agent-device` via "
    "the `mobile-drive:drive` skill or `mobile-drive:mobile-runner` unless the tree truly can't answer."
)
REMINDERS = {1: FIRST, 4: SECOND, 12: THIRD}


def bump(marker):
    """Count this raw call in the session's marker file and return the new count. An empty file left
    by the old once-per-session version counts as one call already made."""
    try:
        with open(marker) as f:
            text = f.read().strip()
        count = int(text) if text else 1
    except FileNotFoundError:
        count = 0
    except ValueError:
        count = 1
    count += 1
    with open(marker, "w") as f:
        f.write(str(count))
    return count


def main():
    if os.environ.get("MOBILE_DRIVE_NO_NUDGE") == "1":  # opt-out, set in settings.json "env"
        return
    try:
        data = json.load(sys.stdin)
        if data.get("tool_name") != "Bash":
            return
        cmd = (data.get("tool_input") or {}).get("command") or ""
        if not any(re.search(p, cmd) for p in PATTERNS):
            return
        session = re.sub(r"[^A-Za-z0-9_-]", "", str(data.get("session_id") or "nosession"))
        count = bump(os.path.join(tempfile.gettempdir(), f"mobile-drive-nudged-{session}"))
        text = REMINDERS.get(count)
        if text:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                     "additionalContext": text.format(n=count)}}))
    except Exception:  # a reminder is never worth breaking the session over
        return


if __name__ == "__main__":
    main()
