# App facts — Items (example)

An illustrative, filled-in `app-facts.md` for a fictional "Items" app: a list of items and an
"add an item" form. Copy the shape, not the facts. In your project this file lives at
`.claude/mobile-drive/app-facts.md`.

## Targets

| | Android | iOS |
|---|---|---|
| App id | `com.example.items` | `com.example.Items` |
| Default device | `--serial emulator-5554` | `--udid` from `agent-device devices --platform ios` |
| Launch popups | a "What's new" sheet with a `label="Got it"` button | the same sheet; a system notification-permission alert |

`export MOBILE_POPUP_SELECTORS=$'label="Got it"'` lets `replay.sh` clear the sheet before a replay.

## Navigation

- Items are under the **Items** tab. The list is newest-first.
- The list's Add button is `id="item_add"`. **Category** opens a searchable picker.

## Side effects

- **Tapping an item row marks it as done**, and that syncs to the account. Never tap rows you
  didn't create.
- Everything created through the UI syncs to the backend: it is real data.

## Quirks

- **Save stays disabled until Name is filled**, and no error is shown.
- Form fields have ids on both platforms: `item_name`, `item_quantity`, `item_category`.
- Android icon buttons have no labels apart from the Add button; use `id=`.

## Test data

- A disposable test account, signed in by hand. Never write credentials here.
