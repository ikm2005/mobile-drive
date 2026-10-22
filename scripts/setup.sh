#!/usr/bin/env bash
# One-time machine setup for the mobile-drive skill. Safe to re-run.
#
#   - installs Node 22 with nvm WITHOUT changing your nvm default
#   - installs the pinned agent-device under that Node
#   - puts an `agent-device` shim in ~/.local/bin so any shell (and Claude Code's Bash tool) finds it
#   - runs `agent-device doctor`
#
# Prerequisites you install yourself: nvm, Android SDK platform-tools (adb) and/or Xcode.
set -euo pipefail

AGENT_DEVICE_VERSION="0.21.22"   # pinned; bump deliberately after reading the changelog
NODE_MAJOR="22"                  # agent-device needs Node >= 22.12
SHIM_DIR="${HOME}/.local/bin"

export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
if [ ! -s "$NVM_DIR/nvm.sh" ]; then
  echo "nvm not found at $NVM_DIR — install nvm first: https://github.com/nvm-sh/nvm" >&2
  exit 1
fi
# shellcheck disable=SC1091
. "$NVM_DIR/nvm.sh"

default_alias="$(nvm alias default 2>/dev/null | awk '{print $3}' || true)"
nvm install "$NODE_MAJOR" >/dev/null
if [ -n "$default_alias" ]; then nvm alias default "$default_alias" >/dev/null; fi  # keep the user's default
NODE_BIN="$(dirname "$(nvm which "$NODE_MAJOR")")"

if [ "$("$NODE_BIN/agent-device" --version 2>/dev/null || true)" != "$AGENT_DEVICE_VERSION" ]; then
  PATH="$NODE_BIN:$PATH" npm install -g "agent-device@${AGENT_DEVICE_VERSION}"
fi

mkdir -p "$SHIM_DIR"
cat > "$SHIM_DIR/agent-device" <<EOF
#!/usr/bin/env bash
# shim written by mobile-drive/setup.sh — runs agent-device on Node ${NODE_MAJOR} regardless of the active nvm version
PATH="${NODE_BIN}:\$PATH" exec "${NODE_BIN}/agent-device" "\$@"
EOF
chmod +x "$SHIM_DIR/agent-device"

case ":$PATH:" in
  *":$SHIM_DIR:"*) ;;
  *) echo "NOTE: add $SHIM_DIR to PATH in your shell profile (export PATH=\"$SHIM_DIR:\$PATH\")" ;;
esac

"$SHIM_DIR/agent-device" --version
"$SHIM_DIR/agent-device" doctor || echo "doctor reported problems — fix the ones for the platform you use"
