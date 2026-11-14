# Changelog

All notable changes to this plugin. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/). Pre-1.0: the evidence comes from one app.

## [0.2.2] — 2026-11-14

### Fixed
- `replay.sh` no longer replays after a failed launch. A device held by a session from another
  folder made the preflight `open` fail with `DEVICE_IN_USE`; the failure was discarded and the flow
  ran against whatever screen the app was on, so the report blamed a flow step that was fine. Any
  failed `open` (device in use, device not found, app not installed) now prints
  `DIVERGED replay <flow> (<n> steps) — the app could not be opened, no step ran:` with agent-device's
  error and hint, and exits 1. The other folder's session is not closed for you: it may be in use.

## [0.2.1] — 2026-11-14

Driven by a review of three weeks of real use: the one device session since 0.2.0 was driven by raw
screenshots after a single reminder, and the collector could not show it.

### Changed
- **Nudge:** it counts raw screenshot and pixel-tap calls per session and reminds at calls 1, 4 and
  12 (it used to remind once). The text now names the `mobile-drive:drive` skill and the
  `mobile-drive:mobile-runner` agent. It still never blocks.
- **Metrics collector:** an event is recorded only when `agent-device` is the command word of a
  shell segment, with a known verb. Text in quotes, heredocs, commit messages and `echo` no longer
  produces events. Flags before the verb, `npx agent-device@x`, redirections and pipes are handled.
- **Session tokens:** a session record now also holds `mobile_calls`, `mobile_input`,
  `mobile_output` and `mobile_by_model`: the API calls that issued a device command, plus the call
  that read its output. The digest uses these. Records written by 0.2.0 hold whole-session totals
  only; the digest lists them apart and leaves them out of the trend.
- **Digest:** every session gets a kind, including "raw loop (no tree)" and "look only". A session
  with four or more raw calls and no tree command adds the action "the skill did not load".
- `setup.sh` stops a daemon left running by the previous agent-device version after an upgrade
  (the new client refused it).
- `replay.sh` gives a replay 300 s (`MOBILE_REPLAY_TIMEOUT_MS`). agent-device's own limit is 90 s,
  which a 30-step Android flow reached in 5 of 5 benchmark runs: each one had saved its record and
  was still reported as diverged. A replay that does hit the limit now says so in its one line, and
  says that the steps so far have run.
- `replay.sh` removes the temporary copy of the flow when it exits.

### Fixed
- The collector recorded the text of `agent-device type "<text>"` as a label. It no longer does.
- `ui-audit.py check` against a baseline exited with an error when the whole-screen crop was the
  first crop written.

### Development
- Tests: `tools/test_metrics.py`, a wider `tools/test_ui_audit.py` (command paths against a stub
  `agent-device`), `tools/test_replay.sh`. `tools/cov.py` measures line coverage of `scripts/*.py`
  with the standard library only: 29% → 97%. `tools/check.sh` fails under 85%.
- shellcheck findings now fail CI; it is installed on the macOS runner too.

### Measured again
- The README table now holds the packaged plugin's numbers: four conditions on two platforms, five
  runs each, median and range, every result checked independently. `tools/bench-method.md`
  describes the method.

### Dependency
- agent-device stays pinned at `0.21.22`. `0.21.35` was tried: on iOS, replaying a saved flow lost
  the tap on a form's Save button in 3 of 3 runs, where the same flow passed on `0.21.22`.

## [0.2.0] — 2026-10-22

### Added
- Skill `mobile-drive:ui-check`, which checks that a screen is right, not just reachable:
  - **against a spec:** missing elements, order, placement;
  - **on every screen:** safe area (status bar, cutout, navigation bar, home indicator, keyboard),
    off-screen, covered, overlapping, tap-target size, truncated text.
- `scripts/ui-audit.py` (`check`, `capture`, `approve`, `audit`, `insets`): the tier-0 geometry
  audit, written in the Python standard library only. It reads agent-device `snapshot --json`
  frames and `dumpsys window` insets, and crops PNGs itself.
- Agent `mobile-drive:ui-inspector`, on Haiku at low effort. It answers PASS/FAIL/UNSURE per crop
  pair, so no full screenshot reaches a model.
- Four design sources, chosen at `/mobile-drive:init` (`ui-check.json`): `reference-app`,
  `figma`, `baseline` and `spec`.
  - `scripts/figma-spec.py` imports a frame through the Figma REST API using your own
    `FIGMA_TOKEN`.
- Metrics and the retro digest record screen-check verdicts and inspector answers.
- An example screen spec in `examples/add-item/screens/`, and synthetic tests in
  `tools/test_ui_audit.py`.

## [0.1.0] — 2026-10-22

### Added
- First release as a Claude Code plugin (a repository that is both the marketplace and the plugin).
- Skills `mobile-drive:drive`, `/mobile-drive:retro`, `/mobile-drive:setup`, `/mobile-drive:init`.
- Agent `mobile-drive:mobile-runner` (Sonnet executor, PASS/FAIL report of at most 20 lines).
- Hooks registered through `hooks/hooks.json`: a once-per-session nudge and a zero-token metrics
  collector. Opt out with `MOBILE_DRIVE_NO_NUDGE=1` / `MOBILE_DRIVE_NO_METRICS=1`.
- A generic "add an item" example (`examples/add-item/`).

### Changed (from the copy-installed kit)
- Project state (`app-facts.md`, saved flows) moved to `<project>/.claude/mobile-drive/`, with
  `~/.claude/mobile-drive/` as the fallback, because plugin files are replaced on update.
- `install.sh` is gone: the plugin system installs skills, the agent and hooks; `/mobile-drive:setup`
  installs agent-device; `/mobile-drive:init` creates project state and, opt-in, the CLAUDE.md rule.
- The metrics collector recognises the namespaced runner (`mobile-drive:mobile-runner`).

### Dependency
- agent-device pinned at `0.21.22`.
