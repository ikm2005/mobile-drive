# mobile-drive

A Claude Code plugin that makes agents drive Android and iOS apps **cheaply**. They read the OS
accessibility tree instead of screenshotting every step, replay known paths in one call, and hand
long walks to a cheaper executor agent. They can also **check** a screen against a design: every
element in place, nothing under the notch or home indicator, nothing clipped, covered or too small
to tap. A script does the geometry; only small crops reach a Haiku model. It works on any installed
app (emulator, simulator or device) and needs no app source.

It is a discipline layer on top of [agent-device](https://github.com/callstack/agent-device), the
open-source CLI (MIT) that does the actual device work.

## Why: the cost model

Every tool call re-sends the whole conversation, so

**cost ≈ number of calls × context size × model price.**

A phone screenshot is about 3,300 input tokens and stays in the context for every later turn. An
accessibility snapshot of the same screen is a few hundred tokens of text with tappable `@refs`.
Cheaper inputs alone are not enough, though: one command per call can cost more than a screenshot
loop. The plugin attacks all three factors:

| Factor | What the plugin does |
|---|---|
| Calls | Chains the predictable steps of a screen in one call, verifies once, and replays saved flows in **one** call |
| Context | Snapshots instead of screenshots. Walks run in a separate agent whose context is thrown away |
| Price | Walks of more than ~5 steps go to `mobile-runner` on Sonnet. Only its ≤ 20-line report reaches the main model |

## Measured

One production app, one add-a-record task of about 20 UI steps, Opus 5.5 in the main session,
plugin 0.2.1 on agent-device 0.21.22. **n = 5 per row** (8 for the iOS walk). Each cell is the
median cost of one run, its range, and the change against the baseline.

| Condition | Android | iOS |
|---|---|---|
| Baseline: no plugin, no saved flow | $1.13 (0.91–1.20) | $1.19 (0.82–1.95) |
| Skill only: the main model walks | $0.71 (0.54–0.83), −37% | $0.67 (0.64–0.90), −44% |
| Walk handed to `mobile-runner` | $0.49 (0.47–0.58), −56% | $0.51 (0.46–1.11), −57% |
| Replay of a saved flow | $0.19 (0.19–0.46), −83%¹ | $0.21 (0.18–0.66), −82% |

The other medians, Android / iOS:

| Condition | Result confirmed² | Screenshots | Peak main-session context | Wall time |
|---|---|---|---|---|
| Baseline | 4 of 5 / 4 of 5 | 4 / 5 | 87k / 93k | 372 s / 287 s |
| Skill only | 5 of 5 / 5 of 5 | 3 / 1 | 69k / 72k | 262 s / 123 s |
| Walk handed to `mobile-runner` | 5 of 5 / 7 of 8 | 3 / 0 | 54k / 54k | 336 s / 175 s |
| Replay | 5 of 5 / 5 of 5 | 0 / 0 | 51k / 51k | 157 s / 71 s |

- **Delegation.** Whenever the `Agent` tool was available, the main model handed the walk to
  `mobile-runner`: 13 of 13 runs. Three of them are iOS runs made before the harness could withhold
  the tool; they count as walks, which is why that row has 8. Handing over is cheaper than walking
  in the main session, and slower.
- **Replay.** All 10 replays passed in one `replay.sh` call. The dearest ones started with a cold
  prompt cache, or the agent re-checked the result afterwards.
- **Baseline.** It costs less than the $1.57 to $1.92 measured in October with n = 2, so the
  savings above are smaller than the ones this page used to show for the walks.

¹ Measured after the replay time limit was raised (0.2.1). With agent-device's own 90 s limit all
five Android replays were cut off after they had saved the record, and cost $0.53 (0.38–0.68).
These five runs were made after the other rows, not interleaved with them.
² Checked independently of the agent's claim: exactly one new record with the right related item.
The misses: one baseline gave up, one baseline left two new rows, one walk left none.

**Read these numbers with care.** One app, one task, one machine, five runs per row. Method:
[tools/bench-method.md](tools/bench-method.md).

## How it compares

| | Device control | Saved, replayable flows | Cheap executor agent | Cost metrics + retrospective |
|---|---|---|---|---|
| Screenshot + `adb`/`simctl` loop | pixels | — | — | — |
| [mobile-mcp](https://github.com/mobile-next/mobile-mcp) (plugin + MCP) | accessibility tree | — | — | — |
| [Maestro](https://github.com/mobile-dev-inc/Maestro) MCP | view hierarchy | YAML flows (great for CI) | — | — |
| [Midscene](https://github.com/web-infra-dev/midscene) | vision model on screenshots | YAML scripts | — | — |
| agent-device's own skills | accessibility tree | `.ad` scripts | — | — |
| **mobile-drive** | accessibility tree, via agent-device | `.ad` + one-call replay with popup preflight | Sonnet `mobile-runner` | zero-token hook + `/mobile-drive:retro` |

These are complements more than rivals. agent-device can export a flow to Maestro YAML when it
should move into CI. Midscene is an option for canvas-only apps where no tree exists.
agent-device's own skill can stay installed beside this one.

## Install

```
/plugin marketplace add ikm2005/mobile-drive
/plugin install mobile-drive@mobile-drive
```

Then run `/mobile-drive:setup` once per machine. It installs Node 22 through nvm (your default
stays), the pinned agent-device and an `agent-device` shim in `~/.local/bin`, then runs
`agent-device doctor`.

Prerequisites: [nvm](https://github.com/nvm-sh/nvm); Android SDK platform-tools (`adb`) with an
emulator or a USB-debugging device; and/or Xcode with a simulator. The first iOS command of the day
builds an XCTest runner, which takes a few minutes and costs no tokens.

No GitHub account is needed. On a machine with no GitHub SSH key, set
`CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` first; without it, an install from `/plugin` tries SSH and fails.

**For a team:** add this to a shared repo's `.claude/settings.json`. Everyone who opens that repo
in Claude Code and trusts the folder gets the plugin enabled.

```json
{
  "extraKnownMarketplaces": {
    "mobile-drive": { "source": { "source": "github", "repo": "ikm2005/mobile-drive" } }
  },
  "enabledPlugins": { "mobile-drive@mobile-drive": true }
}
```

Updates arrive when `version` changes. Turn on auto-update under `/plugin` → Marketplaces, or run
`/plugin marketplace update mobile-drive`.

## First use

1. Sign the app in to a **test account** yourself. Agents never type credentials.
2. Optional: run `/mobile-drive:init` in your project. It creates `.claude/mobile-drive/` with
   `app-facts.md` (app ids, navigation, side effects, quirks) and a `flows/` folder. It can also
   add a short routing rule to `CLAUDE.md`, but only if you agree. The rule makes delegation to
   the runner more reliable. Commit the folder so your team shares the facts and flows.
3. Ask for the task: "add an item called X in the app on the emulator". The first successful walk
   is saved as `.claude/mobile-drive/flows/<task>.<platform>.ad`, and later requests replay it in
   one call.

[`examples/add-item/`](examples/add-item/) shows a filled-in `app-facts.md`, two hardened flows and a
screen spec for a fictional app.

If the app opens with a paywall or onboarding sheet whose dismiss button is not a plain "Close",
tell the replay preflight how to close it, one selector per line:
`export MOBILE_POPUP_SELECTORS=$'label="Skip"\nid="close_button"'`.

If a replay prints `DIVERGED … the app could not be opened, no step ran`, the flow is not at fault.
The usual cause is a device held by a session started from another folder (`DEVICE_IN_USE`). The
hint in the output names the `agent-device close --session …` command; run it only if that session
is not in use.

## Checking screens (`mobile-drive:ui-check`)

Ask "does the Items screen match iOS?" or "is anything under the notch on this screen?".
Vision models are poor at open-ended spot-the-difference, so the skill splits the work into tiers:

| Tier | Who | Does | Cost |
|---|---|---|---|
| 0 | `ui-audit.py` | Compares the accessibility tree's element frames with the screen spec and runs the rules for every screen | 0 tokens |
| 1 | `mobile-runner` (Sonnet) | Reaches the screen (replay first) and runs tier 0 | as for any walk |
| 2 | `ui-inspector` (Haiku, low effort) | One PASS/FAIL/UNSURE per small crop pair: icons, colours, and geometry failures worth a second look | ≈ 900 image tokens per crop |
| 3 | your main model | Judges only the FAIL and UNSURE items | a few hundred tokens |

**Checks:**
- missing elements, wrong order, moved or resized elements;
- content under the status bar, cutout, navigation bar, home indicator or keyboard;
- elements off-screen, covered or overlapping;
- tap targets under 48dp (Android) or 44pt (iOS);
- truncated text;
- icons whose pixels differ from the reference.

Android insets are read live from `dumpsys window`. The iOS simulator doesn't report them, so they
come from config.

**What a screen is compared against** is set once by `/mobile-drive:init`:

| `source` | Spec comes from |
|---|---|
| `reference-app` | The same screen on the other platform: `ui-audit.py capture` on, say, iOS, then `check` on Android |
| `figma` | A Figma frame. `figma-spec.py` uses your own `FIGMA_TOKEN` (zero tokens), or the Figma MCP's `get_metadata` |
| `baseline` | A screen you approved (`ui-audit.py approve`), plus a pixel diff on later runs |
| `spec` | A spec you write by hand |

With no spec, the rules that hold on every screen still run. Specs live in
`.claude/mobile-drive/screens/` (commit them). Reports with screenshots go to `reports/`, which is
git-ignored. The format is in
[`skills/ui-check/spec-format.md`](skills/ui-check/spec-format.md).

Contrast and Dynamic Type need test code, which this skill doesn't install. Use XCUITest
`performAccessibilityAudit()` (iOS 17+) or Android's Accessibility Test Framework for those. For
CI golden images, use Paparazzi, Roborazzi or swift-snapshot-testing.

## What's inside

| Component | Name |
|---|---|
| The method: cost model, replay first, the walk loop, screenshot fallback ladder, safety | skill `mobile-drive:drive` |
| Sonnet executor that returns PASS/FAIL in ≤ 20 lines | agent `mobile-drive:mobile-runner` |
| Screen checks against a design: geometry, safe area, clipping, tap size, eyes on crops | skill `mobile-drive:ui-check`, `scripts/ui-audit.py`, `scripts/figma-spec.py` |
| Haiku crop inspector that answers PASS/FAIL/UNSURE per item | agent `mobile-drive:ui-inspector` |
| One-call replay with stale-session cleanup and popup preflight; it replays nothing if the app cannot be opened | `scripts/replay.sh` |
| Reminder when an agent screenshots or pixel-taps instead of reading the tree: at the 1st, 4th and 12th raw call of a session. It names the skill and the runner, and never blocks | `PostToolUse` hook |
| Zero-token metrics collector | hooks on `PostToolUse`, `PostToolUseFailure`, `SubagentStop`, `SessionEnd` |
| Machine setup, project setup, monthly retrospective | `/mobile-drive:setup`, `/mobile-drive:init`, `/mobile-drive:retro` |

Turn off the hooks with `MOBILE_DRIVE_NO_NUDGE=1` or `MOBILE_DRIVE_NO_METRICS=1` in the `env` block
of your `settings.json`.

## Retrospective

The metrics hook never prints anything, so it costs no tokens. Its data stays on your machine in
`~/.claude/mobile-drive/metrics/` for 6 months. It records:
- replay outcomes and the step where a flow breaks;
- screenshot and coordinate-tap fallbacks, and the selector mix;
- repeated taps and scroll loops;
- batching and delegation;
- runner reports;
- screen-check verdicts and counts, and the inspector's PASS/FAIL/UNSURE counts;
- per-session tokens for the device work alone (the calls that issued a device command and the
  calls that read its output), next to the whole-session totals;
- sessions driven by raw screenshots with no tree command at all;
- taps on risky labels.

It never records typed text, screenshots or screen contents.

Monthly, or after a burst of mobile work, run `/mobile-drive:retro`. It reads a free digest and
proposes concrete edits: an id to add in the app, lines for `app-facts.md`, a sharper rule. It
applies only what you approve. Changes to the plugin itself come out as issue text for this
repository, because installed plugin files are replaced on update. Add
`~/.claude/mobile-drive/prices.json` (`{"<model id>": {"input": <$/M>, "output": <$/M>}}`) to see
costs next to token counts.

## Safety rules

- **No credentials.** Secrets in flows appear only as `${NAME}` (`fill … --record-as NAME`).
- **Touch only what you create.** Tapping an existing row can be a real change (select, mark
  read, favourite).
- **No "cleanup".** Nothing is deleted or edited unless you ask for that exact item.
- **`agent-device find` taps what it finds.** Verify with `wait` or `is exists`, never `find`.
- **Physical devices** may be someone's personal phone. Before every input, Do-Not-Disturb must be
  on and the target app in the foreground; the runner aborts otherwise.

## Making your app easier to drive (optional, needs source)

- **Android (Compose):** set `testTagsAsResourceId = true` at the root and add
  `Modifier.testTag("<screen>_<element>")` to icon buttons, text fields, list rows and tabs. The
  tags show up as `id=`.
- **Android (Views):** keep `android:id`, and set `contentDescription` on icon buttons.
- **iOS:** set `accessibilityIdentifier` on the same elements. Use the same names on both
  platforms, so one flow vocabulary works on both.
- Give icon-only buttons a real label ("Add", "Close"), not a generic one.

## Migrating from the copy-installed kit

If you used the earlier `install.sh`, remove its copies before enabling the plugin. Otherwise
every hook runs twice and the metrics double-count.
1. From `~/.claude` (or `<project>/.claude`), delete `skills/mobile-drive/`, `skills/mobile-retro/`,
   `agents/mobile-runner.md` and `hooks/mobile-drive-*.py`.
2. Remove their entries from `settings.json`.
3. Move your `app-facts.md` and saved flows into `<project>/.claude/mobile-drive/`.
4. Update a `CLAUDE.md` routing rule you added, or re-add it with `/mobile-drive:init`.

## Versions

- Semantic versioning; see [CHANGELOG.md](CHANGELOG.md).
- The plugin pins agent-device (`scripts/setup.sh`). The pin moves only to a release that is at
  least two weeks old, and only after the checks and an emulator replay pass on it.

## Development

```
tools/check.sh                      # lint, hook smoke tests, unit tests + coverage floor, leak scan
claude plugin validate . --strict
claude --plugin-dir .               # try local changes without installing
```

MIT licensed.

## Screenshot loop vs accessibility tree

![The same add-an-item task done two ways. Screenshot loop: 4 screenshots (about 13,200 image tokens) and 7 calls, including a missed coordinate tap and a retry. Accessibility tree: no screenshots, about 600 text tokens across 3 calls, with the form steps chained into one call; next time a saved flow replays in a single call.](docs/screenshot-vs-tree.svg)
