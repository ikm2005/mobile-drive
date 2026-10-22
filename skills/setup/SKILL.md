---
name: setup
description: One-time machine setup for mobile-drive — installs Node 22 through nvm (your default stays), the pinned agent-device CLI and an `agent-device` shim in ~/.local/bin, then runs `agent-device doctor`.
disable-model-invocation: true
---

# mobile-drive setup

Run the setup script once, in one Bash call, and wait for it (the first install takes a minute or
two):

```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/setup.sh"
```

It is safe to re-run. It needs `nvm` already installed, plus Android SDK platform-tools (`adb`)
and/or Xcode with a simulator; it does not install those.

Then report to the user in a few lines:
- the installed `agent-device` version;
- the `doctor` result, for the platforms they use only;
- whether `~/.local/bin` is on their `PATH` (the script prints a NOTE when it isn't). If it isn't,
  give them the line to add to their shell profile. Don't edit the profile yourself.

If the script fails, show its last lines and stop. Don't run `npm` or `nvm` commands of your own
to work around it.
