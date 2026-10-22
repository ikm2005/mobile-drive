# Saved flows

Flows live in the project, in `.claude/mobile-drive/flows/` (or `~/.claude/mobile-drive/flows/`
when there is no project folder). One `.ad` script per task and platform: `<task>.android.ad`,
`<task>.ios.ad`. A walk writes one with `agent-device open … --save-script=<path>`, then
`session save-script` once it has verified its end state. Replay with:

```
"${CLAUDE_PLUGIN_ROOT}/scripts/replay.sh" <flows>/<task>.android.ad android [serial]
"${CLAUDE_PLUGIN_ROOT}/scripts/replay.sh" <flows>/<task>.ios.ad ios [udid]
```

`replay.sh`:
1. closes a stale session;
2. relaunches the app and waits for it to settle;
3. dismisses launch popups (a system alert, plus the buttons in `MOBILE_POPUP_SELECTORS`);
4. replays attached to the running app;
5. prints one line: exit 0 is PASS, exit 1 is DIVERGED with the repair report.

A replay runs without a model, so a known path costs only one call and its one-line result.
Popups are cleared *before* the replay because one that appears mid-flow swallows a tap, and
resuming at the failed step can't recover that tap.

## Hardening a recorded flow before you keep it

- Recorded flows don't carry `--settle`, so add a `wait '<selector>' 10000` after every step that
  navigates.
- Drop `screenshot` steps; their paths point into another session's temp folder.
- **Never verify with `find`**, because it taps. End on `wait`/`is exists`.
- Wait for a state that only success produces. For example, the list's Add button comes back only
  after Save closed the form. A plain `wait 'label="Item"'` passes even when an older item with
  the same name already exists.
- Drop position guards (`# agent-device:target-v1 …` lines) on steps whose selector is already
  unique. A step that only works through a position guard (several identical, unlabelled fields)
  will break: give those elements ids in the app (Compose `Modifier.testTag` with
  `testTagsAsResourceId`, an Android resource-id, an iOS `accessibilityIdentifier`).
- Secrets must appear only as `${NAME}` placeholders (`fill … --record-as NAME`). Review every flow
  before committing it.
- A flow that creates data creates it again on every replay.
- On divergence, heal with `replay <flow> --save-script` and the repair steps in
  `agent-device help scripting`. Then review the `.healed.ad` diff before replacing the original.
