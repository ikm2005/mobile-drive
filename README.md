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

Measured on a production app, add-a-record task, Opus, **n = 2 per row**.

| Condition | Android cost | iOS cost |
|---|---|---|
| Baseline (screenshot/adb loop) | $1.92 | $1.57 |
| Skill only (round 1) | $1.17 (−39%) | $1.38 (−12%) |
| Walk via `mobile-runner` | $0.87 (−54%) | $0.82 (−48%)² |
| Replay | $0.83 (−57%)¹ | $0.32 (−80%)³ |

¹ Hand-finished after the flow diverged at a form field with no label or id.
² The main model delegated to the runner in 1 of the 2 runs.
³ The clean run. The 2-run mean was $0.72 (−54%): the other run hit a launch popup, which
`replay.sh` now clears before replaying.

Peak main-session context fell from about 120k to about 67k tokens. Every run's result was
checked independently of the agent's claim.

**Read these numbers with care.** n = 2, one app, one task. They were measured with the earlier
copy-installed version of this kit plus a `CLAUDE.md` routing line; the plugin packaging has not
been re-benchmarked.

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
| One-call replay with stale-session cleanup and popup preflight | `scripts/replay.sh` |
| Once-per-session reminder when an agent screenshots or pixel-taps first. It never blocks | `PostToolUse` hook |
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
- per-session token totals;
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
tools/check.sh                      # lint, hook smoke tests, ui-audit tests, leak scan
claude plugin validate . --strict
claude --plugin-dir .               # try local changes without installing
```

MIT licensed.

## Screenshot loop vs accessibility tree

![The same add-an-item task done two ways. Screenshot loop: 4 screenshots (about 13,200 image tokens) and 7 calls, including a missed coordinate tap and a retry. Accessibility tree: no screenshots, about 600 text tokens across 3 calls, with the form steps chained into one call; next time a saved flow replays in a single call.](docs/screenshot-vs-tree.svg)
