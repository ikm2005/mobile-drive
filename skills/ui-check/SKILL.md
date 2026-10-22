---
name: ui-check
description: Checks that a mobile screen is RIGHT, not just reachable — compares it with a design (a reference app, a Figma frame, an approved baseline or a written spec) and validates every element in place, the safe area (status bar, notch, home indicator, keyboard), nothing off-screen, covered, overlapping or truncated, and tap-target size. Use for "does this screen match the design/iOS", "check the layout", "is anything under the notch", "UI/visual QA", "verify the screen looks right". Geometry is checked by a script for zero tokens; only small crops go to a Haiku inspector. Android emulator/device and iOS simulator, via agent-device.
---

# Check a screen, cheaply: geometry first, pixels second, a model last

`mobile-drive:drive` gets you **to** a screen. This skill checks that the screen is **right**.
Vision models are poor at open-ended "spot the difference", and a full phone screenshot costs
about 3,350 input tokens. So the work is split into tiers, and each tier sees as little as possible:

| Tier | Who | Does | Sees |
|---|---|---|---|
| 0 | `ui-audit.py` (script) | Presence, order, placement against the spec; safe area, off-screen, covered, overlap, tap size, truncation; a pixel gate on icons and on a baseline | JSON, 0 tokens |
| 1 | `mobile-drive:mobile-runner` (Sonnet) | Reaches the screen (replay first) and runs tier 0 there | the ≤ 15-line summary |
| 2 | `mobile-drive:ui-inspector` (Haiku, low effort) | One narrow PASS/FAIL/UNSURE per crop pair | small crops only |
| 3 | you | Decide bug vs intended for FAIL and UNSURE items; write the verdict | a few hundred tokens |

**Never Read a full-screen screenshot into the main context, and never ask a model "what is
different".** Ask one narrow question about one region.

**Project state** `<state>` is `.claude/mobile-drive/` at the project root, else
`~/.claude/mobile-drive/`.
- `<state>/ui-check.json`: the configuration. If it is missing, ask the user to run
  `/mobile-drive:init`, or ask its design-source question yourself and write the file as init
  would.
- `<state>/screens/<screen>.spec.json`: one spec per screen. Commit these.
- `<state>/reports/`: run output. Git-ignored.

The spec format and every option are in `${CLAUDE_PLUGIN_ROOT}/skills/ui-check/spec-format.md`.
Read it only when you create or edit a spec by hand.

Script: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ui-audit.py"` (written `ui-audit.py` below).
Run it from the **same directory as the walk** that reached the screen: agent-device sessions are
keyed on the working directory.

## 1. Make the spec once per screen (by `source` in `ui-check.json`)

- **`reference-app`** (for example `"reference": "ios"`):
  1. reach the screen on the reference platform with its saved flow (`replay.sh`, see
     `mobile-drive:drive` §1);
  2. run `ui-audit.py capture <screen> --platform <ref> [--udid|--serial …]`;
  3. review the printed counts. Then edit the spec so it holds only what should match:
     - delete live data (dates, counts, user content);
     - set `"edge_to_edge": true` on full-bleed headers and backgrounds;
     - add a `map` entry where the other platform names an element differently.
  Reference crops for icons are saved next to the spec.
- **`figma`:**
  - With the user's own `FIGMA_TOKEN` in the environment (never ask for it in chat, never echo
    it), run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/figma-spec.py" <screen> --node <id> --images`.
    The file key comes from `ui-check.json`.
  - With no token but the Figma MCP connected: call `get_metadata` for the frame **once**, then
    write the spec JSON yourself as `spec-format.md` describes.
- **`baseline`:** when the screen is approved as it is, run
  `ui-audit.py approve <screen> --platform <p> …`. Later runs add a pixel diff against it.
- **`spec`:** the user writes `screens/<screen>.spec.json` by hand, from `spec-format.md`.

With no spec, `check` still runs every rule that holds on any screen, so a quick "is anything
under the notch" needs no setup.

## 2. Check

On the target screen, in one call:

```
ui-audit.py check <screen> --platform android --serial <serial>
ui-audit.py check <screen> --platform ios --udid <udid>
```

It snapshots the screen (`snapshot --json`), takes a scaled screenshot, reads the insets
(`dumpsys window` on Android; `ios_insets` from config on iOS), audits, and prints ≤ 15 lines:
the verdict line, the findings with FAIL first, `eyes: …/eyes.txt` when crops need a look, and
`report: …/report.md`. Exit code 1 means at least one FAIL.

- **The screen needs more than ~5 steps to reach:** delegate to `mobile-drive:mobile-runner`.
  Tell it the task ("reach X"), then "run `<the check line above>` and put its output in notes".
  That way you get only its ≤ 20-line report.
- **Several screens:** one runner task can walk and check them in turn. Ask for one summary per
  screen.

## 3. Eyes, only when the summary lists them

Don't `Read` the crops yourself. Launch `mobile-drive:ui-inspector` with
"Read `<run>/eyes.txt` and answer each line." It returns one `PASS|FAIL|UNSURE` line per item.
- An icon whose pixels match the reference never reaches this step; the script passes it.
- Geometry FAILs (safe area, covered, overlap, off-screen) get a crop too, so Haiku can confirm or
  dismiss a false alarm before you spend tokens on it.
- For an `UNSURE` that decides the verdict, you may `Read` that one crop pair yourself. It is
  small. Never the full screen.

## 4. Verdict

Report to the user in a few lines:
- the screen, the platform and the spec source;
- PASS or FAIL;
- each FAIL with element, check and detail, saying whether eyes confirmed it;
- WARNs grouped;
- the `report.md` path.

Then judge the result. Note intended differences, for example a platform-native control, and
propose spec edits for them (`edge_to_edge`, `map`, removing live data). Don't apply those edits
without a yes.

Read the checks for what they are:
- **missing:** the element is not on screen, or it is named differently. A `map` fixes naming.
  On Android, a missing test tag shows up here: suggest `Modifier.testTag`.
- **moved / resized:** a WARN by default, because platforms differ. Set `"placement": "fail"` for
  a baseline or Figma spec.
- **safe-area:**
  - a FAIL means static content under a system bar, the notch or the keyboard;
  - a WARN means scroll content showing under a translucent bar, which is often intended.
- **tap-size:** smaller than 48dp on Android or 44pt on iOS.
- Platform audits catch more (contrast, Dynamic Type) but need test code: XCUITest
  `performAccessibilityAudit()` on iOS 17+, the Accessibility Test Framework on Android. Suggest
  them; don't install them.

## Safety

This skill is **read-only**. It looks, snapshots and screenshots. It never fills, saves or deletes.
Reaching the screen follows the `mobile-drive:drive` safety rules, including the physical-device
rules (Do-Not-Disturb on, target app in the foreground, abort otherwise). Screenshots and crops stay
in `<state>/reports/`, and nothing is uploaded.
