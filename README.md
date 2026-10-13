# discord-grok-bridge

Talk to your Grok Bot assistant from Discord. @mention your own bot in a private channel, and the answer comes back as a reply in the same channel.

## Quick start

Copy this into a chat with your Grok Bot assistant (it must be able to run commands on a persistent Linux machine):

> Read AGENTS.md in https://github.com/diandian2710/discord-grok-bridge and set up the Discord bridge for me step by step.

Your assistant will check prerequisites, then walk you through creating the Discord bot one step at a time. It collects your bot token and webhook key **without you ever pasting them into chat**, starts the bridge, and runs a test with you. The full playbook it follows is in [AGENTS.md](AGENTS.md).

## What it does

- Connects **one Discord bot, one server, one private text channel and one person** (you) to your Grok Bot.
- Runs on the **same machine as Grok Bot**. No public URL, port forwarding or cloud hosting is needed.
- Grok never gets the Discord token. It fetches requests and submits answers through a small local helper (`bridge claim` / `bridge reply`).
- Requests and replies are stored in SQLite before anything is acknowledged, so they survive restarts. Duplicate replies are ignored.

## What you need

- A Linux (or macOS) machine where Grok Bot runs commands, with **Python 3.12+** and **[uv](https://docs.astral.sh/uv/)**.
- A Discord account and a server you control.
- A **webhook-triggered Grok routine** on that machine. Its URL looks like `https://api2.cursor.sh/automations/webhook/<id>`, and it comes with a key.
- About 10 minutes.

## How it works

```text
You: @Bot do X  →  bridge checks it's you, in your channel  →  saves to SQLite
               →  pings your Grok routine (webhook, no message content)
Grok routine   →  bridge claim  →  does the work  →  bridge reply < answer.txt
Bridge         →  posts the answer as a reply to your message in Discord
```

- Requests run one at a time.
- The last six exchanges are passed to Grok as context; your native Grok chat history is not synced.
- An optional interim "Queued." message can be turned on with `"ack_message": true` in `service.json`. It is off by default.

## Using it

- In your configured channel, **@mention the bot** (a real user mention, not a role) and type your request, e.g. `@MyBridge summarise today's emails`.
- Text only, up to 4,000 characters. Attachments are rejected with a short notice.
- Answers up to 20,000 characters are split across several Discord messages.
- Limits: 10 requests per minute, 100 waiting, and 15 minutes per request before it expires with a notice.
- Editing a message doesn't rerun it; send a new mention instead.

## Health checks and debugging

All commands run from the repo folder. `work/bridge-local/` is the development-mode state folder.

```bash
# Overall health (Discord connected, worker alive, catch-up OK)
.venv/bin/bridge doctor --agent-config work/bridge-local/agent.json

# Live log
tail -f work/bridge-local/bridge.log

# Request states (queued/claimed/reply/delivered/failed/uncertain), no message text
.venv/bin/bridge status --config work/bridge-local/service.json
```

The local API also has an HTTP health endpoint at `http://127.0.0.1:18792/v1/health`. It needs the **helper token** from `agent.json`. This passes it to curl via stdin, so it never shows up in your terminal or shell history:

```bash
python3 -c 'import json;print("Authorization: Bearer "+json.load(open("work/bridge-local/agent.json"))["token"])' \
  | curl -s -H @- http://127.0.0.1:18792/v1/health
```

(`/v1/status` is the admin version. It uses `admin_token` from `service.json`; prefer `bridge status` for that.)

What to look for in the log:

- `gateway_ready catchup_complete`: connected. It reappears after every reconnect, and messages sent while disconnected are picked up then.
- `accepted event=…`: your message was taken.
- `wake event=… result=accepted`: Grok was pinged.
- `gateway_disconnected` / `gateway_resumed`: Discord connection dropped and came back. Messages sent in between are picked up by the catch-up that follows, so a short delay is normal.
- If you sent a message and see no `accepted`, the bridge ignored it. By default, ignored messages are not logged. Turn on **debug mode** to see why.

**Debug mode:** start the bridge with `BRIDGE_DEBUG=1` in its environment, or set `"debug": true` in `service.json`, then restart. With `deploy/run-loop.sh`, run `BRIDGE_DEBUG=1 setsid nohup deploy/run-loop.sh >/dev/null 2>&1 </dev/null &`. Each ignored message then logs one line with its ID and a reason, never its text:

| Reason | Meaning |
| --- | --- |
| `wrong_guild` / `wrong_channel` | Sent somewhere other than the configured server or channel |
| `bot` | Sent by a bot |
| `not_author` | Sent by someone other than the configured user (check your user ID) |
| `no_mention` | Didn't directly @mention the bot (role mentions and plain replies don't count) |
| `attachment` | Had an attachment (you also get a notice in Discord) |
| `rejected_429` / `rejected_422` | Rate limit or full queue / empty or too-long text |
| `not_ready_recovered_on_catchup` | Arrived while disconnected; it is picked up after reconnecting |

| Problem | Fix |
| --- | --- |
| Log loops `operation failed` / `401 Unauthorized` | Wrong Discord token. Use the **Bot → Reset Token** value, which has two dots; it is not the Client Secret. Delete `service.json` and `agent.json`, then rerun setup. |
| `catchup_failed`, or doctor shows `catchup_ok: false` | Wrong server/channel ID, or the bot lacks View Channels / Send Messages / Read Message History there. Fix it and restart. |
| No `accepted` after a mention | Turn on debug mode and read the `reason=`. Check the channel and user IDs; they are different numbers. |
| `wake … result=rejected` | Wrong webhook URL or key, or the routine is disabled. |
| A request stuck as `uncertain` or blocking the queue | Check what happened in Grok and Discord first. Then run `bridge recover --config work/bridge-local/service.json --event <id> --action retry-wake --accept-duplicate-risk` (or `retry-send`, or `skip`). Stuck requests expire after 15 minutes. |
| Nothing after a reboot | Start the loop again: `setsid nohup deploy/run-loop.sh >/dev/null 2>&1 </dev/null &` |

Stop the bridge with `pkill -f run-loop.sh; pkill -f "bridge run --config"`.

## Security summary

- Only your user ID, in your one channel, can trigger work. Bots, other users, other channels and attachments are refused.
- Replies have mentions and link previews disabled. Grok can't choose where replies go.
- Secrets are entered through hidden prompts and stored in owner-only (0600) files. Never paste them into Discord, chat, issues or commits.
- **Development mode** (the quick-start path) trusts every process under your OS account. Your assistant could read the Discord token. Use it only on a machine you trust. For credential isolation, use the Docker or native installer described in [SECURITY.md](SECURITY.md) and the docs, where Grok runs as a separate unprivileged user.
- Conversation text is cleared from the database after 7 days. This is logical deletion, not encryption.

## Other ways to run it

- **Docker** (Docker Engine 28+): `./deploy/docker-setup.sh <grok-user>` then `docker compose up -d bridge`. This keeps credentials in a Docker volume, and the API is published only on `127.0.0.1:18792`. Grok must not have Docker access.
- **Native systemd service** (Linux with sudo): `sudo ./deploy/install.sh <grok-user>`. This uses a dedicated service account. Manage it with `systemctl` / `journalctl -u discord-grok-bridge`.

## Development

```bash
uv sync --frozen --no-editable
uv run --frozen --no-editable pytest
uv run --frozen --no-editable ruff check src tests
uv run --frozen --no-editable bridge mock --state-dir work/mock-bridge   # no credentials, no network
```

After changing code, reinstall with `uv sync --frozen --no-editable --reinstall-package discord-grok-bridge` before restarting.

## More

- [AGENTS.md](AGENTS.md): the step-by-step setup guide your assistant follows, plus contributor rules.
- [SECURITY.md](SECURITY.md): threat model and isolation boundaries.
- [docs/architecture.md](docs/architecture.md): design and delivery guarantees.
- [prompts/grok-routine.md](prompts/grok-routine.md): instructions for the Grok webhook routine.
