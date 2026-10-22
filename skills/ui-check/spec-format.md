# ui-check: configuration and screen spec

## `<state>/ui-check.json`

Written by `/mobile-drive:init`. Every key is optional except `source`.

```json
{
  "source": "reference-app",
  "reference": "ios",
  "figma": { "file_key": "AbC123", "insets": { "top": 47, "bottom": 34 } },
  "tolerance": 0.04,
  "placement": "warn",
  "ios_insets": { "top": 62, "bottom": 34, "left": 0, "right": 0 },
  "min_target": { "ios": 44, "android": 48 },
  "max_crops": 8,
  "pixel_mismatch_max": 0.5,
  "eyes_on_fail": true
}
```

| Key | Meaning | Default |
|---|---|---|
| `source` | `reference-app`, `figma`, `baseline` or `spec`: where specs come from | (asked at init) |
| `reference` | For `reference-app`: the platform that is right, `ios` or `android` | `ios` |
| `figma.file_key` | For `figma`: the key in the file URL (`figma.com/design/<key>/…`) | |
| `figma.insets` | For frames with no layer named "Status Bar" / "Home Indicator" | 0 |
| `tolerance` | Allowed offset of an element's centre, as a share of the safe area | `0.04` |
| `placement` | `warn` or `fail` for moved/resized elements | `warn` |
| `ios_insets` | Safe-area insets of the simulator model, in points. iOS doesn't report them to agent-device | 62/34, an iPhone 17 in portrait |
| `min_target` | Minimum tap-target side, in pt (iOS) and dp (Android) | 44 / 48 |
| `max_crops` | At most this many crop pairs per run go to the inspector | 8 |
| `pixel_mismatch_max` | For `baseline`: percent of pixels allowed to differ | 0.5 |
| `eyes_on_fail` | Also send crops of geometry FAILs, so Haiku can dismiss false alarms | `true` |

Android insets are always read live from `adb shell dumpsys window` (status bar, navigation bar,
display cutout, visible keyboard). Nothing is configured for them.

## `<state>/screens/<screen>.spec.json`

```json
{
  "screen": "items",
  "source": "reference-app:ios",
  "platform": "ios",
  "viewport": { "width": 402, "height": 874, "unit": "pt" },
  "insets": { "top": 62, "bottom": 34, "left": 0, "right": 0 },
  "tolerance": 0.04,
  "placement": "warn",
  "elements": [
    { "key": "item_add", "id": "item_add", "label": "Add", "kind": "button",
      "rect_norm": [0.84, 0.0, 0.11, 0.06], "required": true },
    { "key": "item_icon", "id": "item_icon", "kind": "image", "visual": true,
      "crop": "<state>/screens/items/ref/item_icon.png", "rect_norm": [0.05, 0.07, 0.09, 0.05] },
    { "key": "header", "label": "Items", "kind": "image", "edge_to_edge": true,
      "rect_norm": [0, -0.07, 1, 0.2] }
  ],
  "map": { "item_add": { "android": { "label": "Add item" } } }
}
```

- **`rect_norm`:** `[x, y, width, height]` as shares of the **safe area** (viewport minus
  insets), not of the whole screen. This makes 402×874pt iOS and 1080×2400px Android comparable.
  A negative `y` means the element extends into the top inset.
- **Matching:** each element is matched to one node on the device, trying in turn:
  1. the platform's `map` id;
  2. the `map` label;
  3. `id`, which is the Android resource-id or Compose `testTag`, or the iOS
     `accessibilityIdentifier`;
  4. `label`.

  Matching ignores case. If several nodes match, the nearest wins.
- **`required: false`:** the element may be absent. A missing required element is a FAIL.
- **`visual: true`:** geometry can't judge this element. Its reference `crop` is compared with the
  same region now. Identical pixels pass silently; otherwise it goes to the inspector.
- **`edge_to_edge: true`:** this element may sit under the system bars.
- **Baseline specs** also carry `baseline_png`, the approved full screenshot used for the pixel
  gate.

### Writing a spec from the Figma MCP (no token)

Call `get_metadata` for the frame once. Then write the file as above:
- `viewport` is the frame's size;
- `insets` come from the frame's status-bar and home-indicator layers;
- add one element per text layer, button or icon that must be on screen, with `rect_norm`
  computed against the safe area;
- mark icons and images `visual`.

Leave out decoration (dividers, backgrounds, guides).

## The checks

| Check | Severity | Fires when |
|---|---|---|
| `missing` | FAIL | A required spec element matches no node |
| `order` | FAIL | Two elements that are clearly stacked in the spec appear the other way up |
| `moved`, `resized` | `placement` | The centre is off by more than `tolerance`, or a side by more than 2 × `tolerance` |
| `safe-area` | FAIL; WARN for scroll content | Content intersects a status/navigation bar, cutout, home indicator or visible keyboard |
| `off-screen` | FAIL | A non-scrolling element extends past the screen edge |
| `covered` | FAIL | agent-device reports a non-scrolling element as covered by another view |
| `overlap` | FAIL | Two tap targets overlap by more than 20% of the smaller one |
| `tap-size` | WARN | A visible tap target is smaller than `min_target` |
| `truncated` | WARN | Text ends in an ellipsis |
| `pixels` | WARN | For `baseline`: more than `pixel_mismatch_max`% of pixels differ |
