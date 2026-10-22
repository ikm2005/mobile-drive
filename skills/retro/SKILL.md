---
name: retro
description: Retrospective over the passively collected mobile-drive metrics — turns the digest into concrete, approved improvements to the mobile-drive skill, app-facts.md, the mobile-runner agent, hooks, saved flows, or the app itself (missing ids). Run it monthly or after a burst of mobile automation work.
disable-model-invocation: true
---

# Mobile-drive retrospective

The metrics were collected for free by the plugin's `scripts/mobile-drive-metrics.py` hook. This session is the only
place they cost tokens, so keep it short: read the digest, propose, apply only what the user
approves.

## 1. Read the digest (one call)

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mobile-drive-stats.py" --since 30
```

Use `--since 90` if the last retrospective was longer ago. Don't open the raw `.jsonl` files or
transcripts unless the user asks about a specific finding; the digest is the evidence.

## 2. Turn findings into proposals

Make one table. Every row is backed by a digest line:

| # | Finding (from the digest) | Proposed change | File | Kind |
|---|---|---|---|---|

Where each kind of finding usually lands:

| Finding | Change |
|---|---|
| A flow keeps diverging at the same step | Give that element an id in the **app** (Compose `testTag`, iOS `accessibilityIdentifier`), then re-record. Or harden the step (`wait`, unique selector). |
| A flow fails before any step | `replay.sh` or device state. Check the error code it shows. |
| High coordinate-tap share for an app | App ids or labels for those screens. A line in `app-facts.md` saying where the tree is thin. |
| Walks in the main session (delegation misses) | Sharpen the delegation line in `CLAUDE.md` / `SKILL.md`. |
| Retries on the same target | A quirk line in `app-facts.md`, or a sharper "no change, no retry" rule. |
| Long plain-scroll loops | Remind the runner to verify its own action, not audit the list. |
| Repeated `help` lookups | Put the answer into `SKILL.md`. |
| Pending runner notes | Keep the **durable** ones (navigation, side effects, quirks) for `app-facts.md`. Drop one-offs and anything already there. |
| Token use rising | Find the cause first (a plugin or agent-device upgrade, an app release) before changing anything. |
| Collector errors | Fix the hook. |

Rules for proposals:
- Prefer the narrowest home: `app-facts.md` (one app) before `SKILL.md` (every app) before a hook.
- A blocking hook needs repeated evidence across retrospectives, never one digest. The plugin's
  hooks are advisory.
- App-code changes (ids, labels) are proposals for the app's owners. List them separately.
- **Plugin files are read-only here.** `SKILL.md`, the runner agent, the hooks and the scripts live
  in the plugin cache, which every update replaces. Don't edit them. Write those rows up as a
  short issue or PR text for the mobile-drive repository instead. Only the project state
  (`.claude/mobile-drive/app-facts.md`, saved flows, the project `CLAUDE.md`) is edited locally.
- Keep `app-facts.md` and `SKILL.md` short; every line is paid for on every device task.

## 3. Apply what the user approves

Ask once, with the table. Apply only the approved rows, as small edits. Then:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mobile-drive-stats.py" --mark-reviewed
```

That marks the runner notes as handled, so the next digest shows only new ones. Report what
changed, file by file, in a few lines.
