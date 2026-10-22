# Changelog

All notable changes to this plugin. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/). Pre-1.0: the evidence comes from one app.

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
