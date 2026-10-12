#!/usr/bin/env bash
# Run from an inspected, pinned checkout on the Grok Bot's Linux machine.
set -euo pipefail
if [[ $(id -u) != 0 || $# != 1 ]]; then
  echo 'usage: sudo ./deploy/install.sh <unprivileged-grok-user>' >&2
  exit 2
fi
command -v systemctl >/dev/null
command -v uv >/dev/null || { echo 'Install uv and Python 3.12+ first.' >&2; exit 1; }
if [[ -e /usr/local/bin/bridge || -L /usr/local/bin/bridge ]]; then
  echo 'Existing /usr/local/bin/bridge: refusing to replace it.' >&2
  exit 1
fi
id "$1" >/dev/null
if [[ $(id -u "$1") == 0 || $1 == discord-grok-bridge ]]; then
  echo 'Grok must run under a separate unprivileged user.' >&2
  exit 1
fi
bridge_source=$(cd "$(dirname "$0")/.." && pwd)
bridge_dest=/opt/discord-grok-bridge
if [[ -e $bridge_dest ]]; then
  echo 'Existing installation: refusing to overwrite it.' >&2
  exit 1
fi
if ! id discord-grok-bridge >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/discord-grok-bridge --shell /usr/sbin/nologin discord-grok-bridge
fi
if [[ $(getent passwd discord-grok-bridge | cut -d: -f7) != /usr/sbin/nologin ]]; then
  echo 'Service account must use /usr/sbin/nologin.' >&2
  exit 1
fi
install -d -m 0755 "$bridge_dest"
cp "$bridge_source/pyproject.toml" "$bridge_source/uv.lock" "$bridge_source/README.md" "$bridge_dest/"
cp -R "$bridge_source/src" "$bridge_dest/src"
UV_PYTHON_DOWNLOADS=never uv sync --directory "$bridge_dest" --frozen --no-dev --no-editable --python /usr/bin/python3
chmod -R go-w "$bridge_dest"
"$bridge_dest/.venv/bin/bridge" setup --agent-user "$1"
install -m 0644 "$bridge_source/deploy/discord-grok-bridge.service" /etc/systemd/system/discord-grok-bridge.service
# The helper loads only the unprivileged user's agent profile.
ln -s "$bridge_dest/.venv/bin/bridge" /usr/local/bin/bridge
systemctl daemon-reload
systemctl enable --now discord-grok-bridge
printf 'Installed. Configure the Grok routine, send a Discord @mention, then run bridge doctor.\n'
