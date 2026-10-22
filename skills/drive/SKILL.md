---
name: drive
description: The default way to interact with ANY mobile app on an Android emulator/device or iOS simulator/device — looking at a screen, tapping, typing, navigating, testing, reproducing a bug, verifying a change, exploring any installed APK or build. Reads the accessibility tree with agent-device and replays saved flows instead of taking screenshots and tapping pixels, at a fraction of the tokens. Not for unit tests or builds.
---

# Drive a device through the accessibility tree, not screenshots

A phone screenshot costs ~3,300 input tokens and is re-read on every later turn. An
`agent-device` snapshot of the same screen is a few hundred tokens of text with tappable refs.
The tree comes from the OS (UIAutomator on Android, XCTest on iOS), so this works on **any
installed app**, not only ones whose source is here.

**Tree first, screenshots as the fallback.** Don't open with `adb exec-out screencap`, `xcrun
simctl io screenshot`, `uiautomator dump` or pixel taps; that is exactly the cost this skill
removes. But when the tree can't answer (§3), screenshots and coordinate taps are the right tool:
use them so the task still completes instead of failing.

**Project state** (`<state>` below) is `.claude/mobile-drive/` at the project root if it exists,
otherwise `~/.claude/mobile-drive/`. It holds `app-facts.md` and `flows/`. The plugin's own files
are read-only and are replaced on every update, so never save anything next to this file.

**First, read `<state>/app-facts.md`, once.** It holds this project's app ids, devices,
navigation, known quirks and side effects. If it is missing, or still the unfilled template, carry
on without it. At the end, report anything worth adding to it.

## The cost model — read this first

Every tool call re-sends the whole conversation: about 60k tokens before the first step, and
130k or more by the end of a walk. **Cost ≈ number of calls × context size × model price**, so
cutting calls saves more than shrinking any single output: a walk with one command per call can
cost more than a screenshot loop. Batch.

- **Chain every predictable step of a screen into one Bash call** with `&&`, each step `--settle`.
  For example: `agent-device fill @e27 "Item name" --settle && agent-device press 'label="Category"' --settle && agent-device fill @e1 "Value" --settle`.
  Stop the chain where the next target is unknown. For a known multi-step sequence, use
  `agent-device batch --steps-file`.
- **The screen is the source of truth.** Don't read app source, grep the repo or pull the app's
  database to navigate or verify. If Save does nothing, run `snapshot -i` and look for a
  validation message or an empty required field.
- **Verify once**, with `agent-device wait 'label="…"'` or `is exists '<selector>'`, at the end.
  **`find <selector>` taps what it finds** (its default action is click), so never use it to look.
- **Verify your own action, not the whole list.** "Add X" passes when Save closed the form and X
  shows. Don't count or audit existing rows; lists often hold identical names.
- **Delegate the walk.** A device task of more than ~5 steps goes to the
  `mobile-drive:mobile-runner` agent (Sonnet, small context): give it the intent and take back its
  ≤ 20-line PASS/FAIL. Snapshots and screenshots then never enter your context. If you *are* the
  runner, ignore this line.
- Don't open `help` topics unless a command fails in a way this file doesn't cover.

## 0. Is the tool there?

`agent-device --version` — if missing, stop and tell the user to run `/mobile-drive:setup` (Node
≥ 22.12, pinned agent-device). Do not run `npm` installs yourself mid-task.

## 1. Replay first

Saved flows live in `<state>/flows/`, one per platform: `<task>.android.ad`, `<task>.ios.ad`.
If one matches the task:

```
"${CLAUDE_PLUGIN_ROOT}/scripts/replay.sh" <state>/flows/<task>.android.ad android [serial]
"${CLAUDE_PLUGIN_ROOT}/scripts/replay.sh" <state>/flows/<task>.ios.ad ios [udid]
```

Run `replay.sh` straight away; it is one call. Don't `Read` the flow file and re-type its steps by
hand, because that costs a full walk. The script:
1. closes a stale session;
2. relaunches the app and waits for the screen to settle;
3. dismisses launch popups (a system alert, plus the buttons in `MOBILE_POPUP_SELECTORS`);
4. replays the flow.

**PASS → report its one line and stop.** The flow's own final `wait` is the verification, so don't
spend calls re-proving it.
- `DIVERGED` → the report names the failed step and carries a screen digest with ranked selector
  suggestions. The app is still on that screen. Finish the remaining steps by hand from there; don't
  start over. To fix the flow itself, heal with `replay <flow> --save-script` and leave the
  `.healed.ad` for review. Never overwrite the original. Hardening checklist:
  `${CLAUDE_PLUGIN_ROOT}/skills/drive/flows-guide.md`.

## 2. Walk it (no matching flow)

Arm recording on the first `open` so a successful walk becomes the next run's replay:

```
agent-device open <app-id> --platform android --serial <serial> --relaunch --save-script=<state>/flows/<task>.android.ad
agent-device snapshot -i
agent-device press @e12 --settle            # act on refs; --settle prints only the diff
agent-device fill 'label="Search"' "Item name" --settle
agent-device scroll down --until 'label="Value"'
agent-device wait 'label="Item name"'        # verify the named end state
agent-device session save-script             # publish the flow (only after the end state is verified)
agent-device close
```

- Continue from the `--settle` diff. Run `snapshot -i` again only when the diff lacks the next
  target or reports not settled. Refs go stale after any mutation.
- **No change, no retry.** If a tap's `--settle` diff shows nothing changed, don't repeat the tap.
  Look for the cause: the keyboard covering the target, a disabled button, an empty required
  field, a validation message.
- Prefer `@ref`, then `id=` (Android resource-id or Compose testTag, iOS accessibilityIdentifier),
  then `label=` selectors.
- No pipes, `grep` or `head` on agent-device output; the raw output carries the next refs. To
  reach an off-screen element, use `scroll … --until '<selector>'`, not a loop of scrolls.
- Unlabeled icons: run `screenshot <path> --overlay-refs --scale 0.4 --no-record` **once** per
  screen to map icons to refs. Don't take repeated full screenshots.
- While recording, add `--no-record` to every look-only command (screenshot, and anything else
  that doesn't change the app), so the saved flow has no temp paths or dead steps in it.
- Always finish with `agent-device close`; a session left open locks the device for the next run.
- iOS: same commands with `--platform ios --udid <udid>`. The first iOS command of the day builds
  the XCTest runner (slow, no token cost) — don't treat the wait as a failure.
- `agent-device help workflow` is the full reference; read a `help <topic>` only when stuck.

## 3. Screenshot ladder — only when the tree can't answer

1. Tree: `snapshot -i`, `wait`, `is`.
2. The question is visual (layout, colour, clipping, chart) **or** the snapshot warns sparse /
   AX-unavailable (canvas, game, custom-drawn view):
   `agent-device screenshot /tmp/s.png --scale 0.5 --crop-on 'label="Title"'` — always
   `--scale ≤ 0.5`, crop when one region answers it. `--overlay-refs` draws numbered refs on the
   image (set-of-mark) so you answer with a ref instead of x/y.
3. Still no target: `press <x> <y>` from the scaled screenshot (convert back to device pixels),
   then `snapshot -i` again on the new screen.
4. If agent-device itself can't capture or tap there (rare: secure surfaces, some system UI),
   fall back to the platform tools: `adb exec-out screencap -p`, `xcrun simctl io <udid>
   screenshot`, `adb shell input tap x y`. Downscale before reading (`sips -Z 1000 <png>`) and go
   back to the tree on the next screen.
5. Canvas-only apps where 1–4 keep failing: a local grounding model (OmniParser, UI-TARS-7B)
   returns boxes from a screenshot. Not installed — tell the user it would be needed.

Never give up on a step only because the tree lacks it: move down this ladder.

A screenshot is evidence, not verification: confirm named expectations with `wait`/`is`.

## 4. Keep the main context clean: `mobile-drive:mobile-runner`

Every call re-sends the caller's whole context at the caller's model price. A walk of more than
~5 steps therefore goes to the `mobile-drive:mobile-runner` agent, which runs on Sonnet with this skill
preloaded. It returns only `PASS|FAIL`, the flow path, what it created and ≤ 15 lines of notes,
so device output never reaches the main thread. Only a replay (one `replay.sh` call) or a single
look is cheaper to run directly.

## 5. Safety

- **Physical device** (not an emulator, not a simulator): it may be someone's personal phone.
  Before each input confirm Do-Not-Disturb is on and the foreground app is the target app;
  abort on anything else. Never tap into another app.
- Never type real credentials. Use `fill … --record-as NAME` with an env var so a flow never
  stores a secret (`help scripting`).
- Data created through the UI may be real (synced to a backend). Say what you created. **Never
  delete or edit data as "cleanup"** unless the user asks for that exact item: list rows that look
  identical cannot be told apart by label, and picking one by position deletes the wrong one.
- **Touch only what you create.** Tapping an existing list row can be a real change (it may
  select, favourite or open-and-mark it). `app-facts.md` lists the known side effects.
