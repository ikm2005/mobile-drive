#!/usr/bin/env python3
"""PostToolUse nudge for Bash: when an agent drives a mobile app the screenshot-and-pixel way,
remind it — once per session — that the accessibility tree comes first. It never blocks: the
command has already run, because screenshots and coordinate taps are the legitimate fallback
when the tree can't answer (canvas, games, custom-drawn views, a purely visual check).

Watched (the expensive path the mobile-drive skill replaces when the tree works):
  adb … screencap · uiautomator dump · xcrun simctl io … screenshot ·
  adb … shell input tap|swipe · idb ui tap|swipe · axe tap|swipe
Silent for everything else, and silent after the first reminder in a session.
Opt out with MOBILE_DRIVE_NO_NUDGE=1.
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

REMINDER = (
    "Reminder (mobile-drive, shown once per session): screenshots and pixel taps are the fallback, "
    "not the first move. Start with the accessibility tree — `agent-device snapshot -i` (a few "
    "hundred tokens with @refs) and `agent-device press @ref --settle` — and come back to "
    "screenshots only where the tree can't answer (canvas/game/custom-drawn view, a visual check, "
    "an element missing from the tree). Then prefer `agent-device screenshot <path> --scale 0.5 "
    "--crop-on '<selector>'` over a full-resolution capture. If that is already what you are "
    "doing, carry on."
)


def main():
    if os.environ.get("MOBILE_DRIVE_NO_NUDGE") == "1":  # opt-out, set in settings.json "env"
        return
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if not any(re.search(p, cmd) for p in PATTERNS):
        return
    session = re.sub(r"[^A-Za-z0-9_-]", "", str(data.get("session_id") or "nosession"))
    marker = os.path.join(tempfile.gettempdir(), f"mobile-drive-nudged-{session}")
    if os.path.exists(marker):
        return
    open(marker, "w").close()
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                             "additionalContext": REMINDER}}))


if __name__ == "__main__":
    main()
