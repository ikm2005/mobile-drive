# How the README numbers are measured

The table under "Measured" in the README comes from a harness that runs one fixed task in fresh
headless sessions and checks the outcome itself. The harness is not in this repository: its
verifiers read one app's local database and network log, so it lives with that app. This page
describes the method so the numbers can be read, and repeated on another app.

## The task

One sentence, the same for every run: add one named record through the app's UI on a named
emulator or simulator, with one named related item, without touching source code or existing
data, and reply `PASS` or `FAIL: <reason>`. The walk is about 20 UI steps: three screens, four
text fields, two pickers, a save.

## One run

1. Close any agent-device session left by an earlier run and cold-start the app.
2. Start `claude -p "<task>" --output-format json --model <model> --max-turns 120` from the app's
   project root, so its `CLAUDE.md`, hooks and skills load as in a normal session.
3. Read cost, turns and tokens from the JSON result, and tool calls, screenshots and peak context
   from the session transcript (subagent transcripts included).
4. **Verify independently of the agent's claim:** exactly one new record with the right name and
   the right related item must exist, read from the app's database (Android) or from its logged
   API calls (iOS). A run that says PASS without that record counts as not verified.

Runs are sequential, never parallel, on one account. Nothing is reset, edited or deleted between
runs, so the lists the agent scrolls through grow by one row per run. To spread that drift evenly,
runs are interleaved by condition: one of each, then the next round.

## Conditions

| Condition | Plugin | Saved flows | `Agent` tool | Routing line in `CLAUDE.md` |
|---|---|---|---|---|
| Baseline | off | hidden | allowed | hidden for the run |
| Walk, main session | on | hidden | not allowed | present |
| Walk | on | hidden | allowed | present |
| Replay | on | present | allowed | present |

- **Plugin on** means this checkout loaded with `--plugin-dir`. An installed copy is switched off
  for every run with `--settings '{"enabledPlugins": {...: false}}'`, so hooks never run twice.
- **Walk** leaves delegation to the model. The report says in how many runs it handed the walk to
  `mobile-runner`; that rate is a result, not a setting.
- **Walk, main session** removes the `Agent` tool, so the main model walks with the skill alone.
- The plugin's metrics hook writes into the run folder (`MOBILE_DRIVE_METRICS_DIR`), not into the
  machine's own store.

## What is reported

Per condition and platform: the median and the range (min–max) of cost, input tokens, tool calls,
screenshots and peak main-session context; the number of verified runs; and the delegation rate.
Medians, because with five runs a mean follows its one outlier.

Each run record stores the plugin version with its git commit, the agent-device version and the
model, so a row can be traced to what produced it.

## Limits

- One app, one task, one machine. A different app has different screens, ids and popups.
- n = 5 per row shows the size of an effect, not its third digit.
- Costs are what the CLI reported at the time, at that model's prices.
- A replay needs a flow that still matches the app and its data. When a saved flow has drifted, the
  replay condition measures "replay, then finish by hand", and the README says so where it happened.
