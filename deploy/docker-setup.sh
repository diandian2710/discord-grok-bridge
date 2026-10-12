#!/usr/bin/env bash
set -euo pipefail
if [[ $# != 1 ]]; then
  echo 'usage: ./deploy/docker-setup.sh <unprivileged-grok-user>' >&2
  exit 2
fi
bridge_root=$(cd "$(dirname "$0")/.." && pwd)
cd "$bridge_root"
bridge_engine=$(docker version --format '{{.Server.Version}}')
bridge_major=${bridge_engine%%.*}
if [[ ! $bridge_major =~ ^[0-9]+$ || $bridge_major -lt 28 ]]; then
  echo 'Docker Engine 28+ is required for localhost port isolation. Update Docker first.' >&2
  exit 1
fi
command -v python3 >/dev/null
{
  IFS= read -r BRIDGE_AGENT_UID
  IFS= read -r BRIDGE_AGENT_GID
  IFS= read -r BRIDGE_AGENT_DIR
} < <(python3 - "$1" <<'PY'
import pwd, sys
if sys.version_info < (3, 12):
    raise SystemExit("The host helper requires Python 3.12+")
from pathlib import Path
account = pwd.getpwnam(sys.argv[1])
if account.pw_uid == 0:
    raise SystemExit('Grok must run under an unprivileged user')
print(account.pw_uid)
print(account.pw_gid)
print(Path(account.pw_dir) / '.config/discord-grok-bridge')
PY
)
if [[ $BRIDGE_AGENT_UID == "$(id -u)" ]]; then
  echo 'Run Docker setup as the human operator; Grok must use a different account without Docker access.' >&2
  exit 1
fi
if [[ ! -d $BRIDGE_AGENT_DIR ]]; then
  sudo -u "$1" -- /bin/mkdir -p "$BRIDGE_AGENT_DIR"
  sudo -u "$1" -- /bin/chmod 0700 "$BRIDGE_AGENT_DIR"
fi
if [[ -L $BRIDGE_AGENT_DIR ]]; then
  echo 'Helper directory must not be a symlink.' >&2
  exit 1
fi
export BRIDGE_AGENT_UID BRIDGE_AGENT_GID BRIDGE_AGENT_DIR
docker compose build bridge
docker compose --profile tools run --rm setup
printf 'Configured. Start with: docker compose up -d bridge\n'
printf 'Grok helper: %s/deploy/bridge claim\n' "$bridge_root"
