# App facts — <App name>

Project-specific facts the `mobile-drive` skill and `mobile-runner` agent read once per task.
Keep it short: every line is paid for on every device task. Add a line whenever a walk learns
something the next walk would otherwise rediscover.

## Targets

| | Android | iOS |
|---|---|---|
| App id | `com.example.app` | `com.example.App` |
| Default device | `--serial emulator-5554` | `--udid <from agent-device devices --platform ios>` |
| Launch popups to dismiss | `MOBILE_POPUP_SELECTORS` (e.g. `label="Skip"`) | same |

## Navigation

- Where the main features are, e.g. "Settings are under the Profile tab → General".
- List order, e.g. "Lists are newest-first".

## Side effects — things a tap changes for real

- e.g. "Tapping a list row selects it as the active item, and that syncs to the backend."
- e.g. "Opening a message marks it read."

## Quirks learned from walks

- e.g. "Save stays disabled until fields A, B and C are non-empty, and shows no error."
- e.g. "Icon buttons have no labels; the Add button is `id=\"item_add\"`."

## Test data

- The account in use, and whether it is disposable (never write credentials here).
