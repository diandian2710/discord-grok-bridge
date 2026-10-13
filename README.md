<h1 align="center">discord-grok-bridge</h1>

<p align="center">
  <strong>Talk to your Grok Bot assistant from Discord.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/uv-managed-DE5FE9?style=flat-square" alt="uv">
  <img src="https://img.shields.io/badge/Linux-supported-FCC624?style=flat-square&logo=linux&logoColor=black" alt="Linux">
  <img src="https://img.shields.io/badge/macOS-dev%20mode-8E8E93?style=flat-square&logo=apple&logoColor=white" alt="macOS">
  <img src="https://img.shields.io/badge/Discord-bot-5865F2?style=flat-square&logo=discord&logoColor=white" alt="Discord bot">
  <img src="https://img.shields.io/badge/network-outbound%20only-2EA44F?style=flat-square" alt="outbound only">
</p>

<p align="center">
  You @mention your bot in a private Discord channel. <code>bridge</code> saves the message locally and wakes your Grok routine (webhook).
  The routine fetches it with <code>bridge claim</code>, answers with <code>bridge reply</code>, and the bridge posts the answer back to Discord.
</p>

---

> [!IMPORTANT]
> **🤖 If you are an agent: read [`AGENTS.md`](AGENTS.md) first and follow it step by step.**
> Ask the user for one thing at a time. The bot token and webhook key must **never** go through chat. Collect them only through masked or secure input.

## Features

- **No lost messages**: each request is saved to a local SQLite inbox before anything else happens. Messages missed while disconnected are recovered on reconnect, and duplicate replies are ignored.
- **Outbound only**: no public port or callback URL. The local API listens on `127.0.0.1` only.
- **Allowlisted by default**: one server, one channel, one user. Everyone and everything else is ignored.
- **Grok never sees the Discord token**: it only gets a scoped helper credential for `claim` / `reply`.
- Text in and out, with long answers split automatically. No privileged Discord intents needed.

```
Discord ──@mention──▶ bridge (SQLite inbox) ──webhook wake──▶ Grok routine
        ◀───────── bridge (outbox) ◀──── bridge claim / bridge reply ┘
```

## Quick start

Send this to your Grok Bot assistant (it needs to run commands on a persistent Linux machine):

```
Read AGENTS.md in https://github.com/diandian2710/discord-grok-bridge and set up the Discord bridge for me step by step.
```

It installs everything itself. You only need to **create the Discord bot when it asks**, copy three IDs, and enter the bot token and webhook key into a secure input.

> [!NOTE]
> When setup is done, **send `@YourBot hello` in your channel**. Setup only counts as finished once the real answer comes back.

Other ways to run it: [Docker](#other-deployments) · [native systemd](#other-deployments) · manual install ↓

### Manual install

```bash
git clone https://github.com/diandian2710/discord-grok-bridge.git && cd discord-grok-bridge
uv sync --frozen --no-editable
uv run --frozen --no-editable bridge setup --development   # hidden prompts: token, IDs, webhook URL/key
setsid nohup deploy/run-loop.sh >/dev/null 2>&1 </dev/null &   # auto-restart loop (no systemd needed)
.venv/bin/bridge doctor --agent-config work/bridge-local/agent.json   # all true = installed
```

You'll need:
- **Discord:** a bot with **no privileged intents**, invited with only **View Channels**, **Send Messages** and **Read Message History**; plus your server, channel and user IDs (turn on Developer Mode to copy them).
- **Grok:** a **webhook-triggered routine** on the same machine (URL `https://api2.cursor.sh/automations/webhook/<id>` and a key), using the instructions in [`prompts/grok-routine.md`](prompts/grok-routine.md).

## Integration

**Receive**: the routine runs `bridge claim`. It returns `{"job":null}`, or a job with `event_id`, `conversation_id`, `text`, `history` (the last six exchanges) and a private `handle` path. **A wake is only a hint; always claim. Duplicate replies with the same text are safe.**

**Reply**:

```bash
bridge claim --agent-config work/bridge-local/agent.json
bridge reply --agent-config work/bridge-local/agent.json --job '<handle>' < answer.txt   # text via stdin
bridge reply --agent-config work/bridge-local/agent.json --job '<handle>' --no-reply      # finish silently
```

- Input is text only, up to 4,000 characters. Replies can be up to 20,000 characters and are split into Discord-sized messages. Mentions and link previews are disabled.
- Requests run one at a time. Limits are 10 per minute and 100 queued, and a request expires with a notice after 15 minutes.
- **Usage:** in the channel, write `@YourBot your request`. It must be a direct user mention, not a role. Edits don't rerun a request; send a new one.
- Optional settings in `service.json`: `"ack_message": true` posts "Queued." first (off by default), and `"debug": true` logs why messages are ignored.

Design and delivery guarantees: [`docs/architecture.md`](docs/architecture.md).

## Operations

| What | Command |
| --- | --- |
| Health | `.venv/bin/bridge doctor --agent-config work/bridge-local/agent.json` |
| Request states | `.venv/bin/bridge status --config work/bridge-local/service.json` |
| Live log | `tail -f work/bridge-local/bridge.log` |
| Debug mode (log ignored messages) | restart with `BRIDGE_DEBUG=1` or `"debug": true` |
| Recover a stuck request | `bridge recover --config … --event <id> --action retry-wake\|retry-send\|skip` |
| Stop | `pkill -f run-loop.sh; pkill -f "bridge run --config"` |
| Upgrade after pulling code | `uv sync --frozen --no-editable --reinstall-package discord-grok-bridge`, then restart |

Health endpoint: `GET http://127.0.0.1:18792/v1/health` with the helper token. For the copy-paste curl that keeps the token hidden, see [troubleshooting](docs/troubleshooting.md#health-checks).

## Common problems

| Symptom | Fix |
| --- | --- |
| Bot doesn't reply | Run `doctor`, then turn on debug mode and look for `ignored … reason=`. See [troubleshooting](docs/troubleshooting.md) |
| Log loops `401` / `Improper token` | Wrong token. Use **Bot → Reset Token** (it has two dots); it is not the Client Secret |
| Replies come minutes late | `gateway_disconnected` / `gateway_resumed` in the log; the message was recovered by catch-up |
| `wake … result=rejected` | Wrong webhook URL or key, or the routine is disabled |
| Queue stuck / `uncertain` | Inspect with `bridge status`, then run `bridge recover` |
| `PyNaCl is not installed` warning | Harmless; voice isn't used |

Full guide, organised by symptom: **[`docs/troubleshooting.md`](docs/troubleshooting.md)**.

## Security

- Development mode (the quick-start path) trusts every process under your OS account, so **your assistant can read the Discord token**. Use it only on a machine you trust.
- Secrets go in through hidden prompts and are stored in 0600 files. Never paste them into Discord, chat, issues or commits.
- Conversation text is cleared from the database after 7 days. This is logical deletion, not encryption.

Details: [`SECURITY.md`](SECURITY.md).

## Other deployments

- **Docker** (Engine 28+): `./deploy/docker-setup.sh <grok-user>` then `docker compose up -d bridge`. Credentials live in a Docker volume, and the API is published only on `127.0.0.1:18792`. Grok must not have Docker access.
- **Native systemd** (Linux + sudo): `sudo ./deploy/install.sh <grok-user>`. The bridge runs under a dedicated service account, and Grok runs as a separate unprivileged user. Manage it with `systemctl` / `journalctl -u discord-grok-bridge`.

## Development

```bash
uv sync --frozen --no-editable
uv run --frozen --no-editable pytest
uv run --frozen --no-editable ruff check src tests && uv run --frozen --no-editable ruff format --check src tests
uv run --frozen --no-editable bridge mock --state-dir work/mock-bridge   # no credentials, no network
```

`src/discord_grok_bridge/` holds the code: `runtime` (Discord and the worker) · `store` (SQLite queue) · `api` (local API) · `helper` (claim/reply) · `cli` · `config`. Contributor rules are at the end of [`AGENTS.md`](AGENTS.md).

---

This project uses the official Discord API but is not affiliated with Discord Inc. "Grok Bot" refers to your own assistant; this is not an official Grok product.
