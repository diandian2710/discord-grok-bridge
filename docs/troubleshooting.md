# Troubleshooting

Organised by symptom. Each entry says **how to check** and **how to fix**. Commands assume you're in the repo folder with development-mode state in `work/bridge-local/`. With the Docker or native installs, use `docker compose exec bridge bridge …` or `sudo bridge …` instead.

> [!WARNING]
> Never paste the Discord bot token, the webhook key, or the contents of `service.json` / `agent.json` into chat, Discord, issues or logs. The commands below never print them.

- [Health checks](#health-checks)
- [Bot doesn't reply](#bot-doesnt-reply)
- [Replies arrive late](#replies-arrive-late)
- [401 Unauthorized / bad token](#401-unauthorized--bad-token)
- [Ignored-message reasons (debug mode)](#ignored-message-reasons-debug-mode)
- [Wake rejected or webhook problems](#wake-rejected-or-webhook-problems)
- [Stuck or uncertain queue](#stuck-or-uncertain-queue)
- [After a reboot or crash](#after-a-reboot-or-crash)
- [Code change has no effect](#code-change-has-no-effect)
- [PyNaCl / davey voice warning](#pynacl--davey-voice-warning)

## Health checks

| What | Command | Healthy result |
| --- | --- | --- |
| Doctor | `.venv/bin/bridge doctor --agent-config work/bridge-local/agent.json` | `{"discord_ready": true, "worker_alive": true, "catchup_ok": true}`, exit 0 |
| Request states | `.venv/bin/bridge status --config work/bridge-local/service.json` | Recent jobs `delivered`, `uncertain_chunks: 0` |
| Log | `tail -f work/bridge-local/bridge.log` | `INFO gateway_ready catchup_complete` after each start |
| Process | `pgrep -af "run-loop\|bridge run"` | One `run-loop.sh` and one `bridge run` |

**HTTP health endpoint**: `GET http://127.0.0.1:18792/v1/health` needs the **helper token** from `agent.json`. This passes it to curl through stdin, so it never appears on screen or in shell history:

```bash
python3 -c 'import json;print("Authorization: Bearer "+json.load(open("work/bridge-local/agent.json"))["token"])' \
  | curl -s -H @- http://127.0.0.1:18792/v1/health
```

`/v1/status` is the admin variant and uses `admin_token` from `service.json`. Prefer `bridge status`, which does this for you.

What the health fields mean:
- `discord_ready: false` means the bridge is disconnected or still catching up.
- `catchup_ok: false` means channel checks or catch-up failed.
- `worker_alive: false` means the background worker has stalled.

## Bot doesn't reply

**Check**, in this order:
1. Run doctor. If it can't reach the API, the bridge isn't running; see [After a reboot](#after-a-reboot-or-crash).
2. Send a fresh mention and watch the log. You should see `accepted event=…`, then `wake event=… result=accepted`.
3. No `accepted`? The message was ignored. Turn on [debug mode](#ignored-message-reasons-debug-mode) to see why.
4. `accepted` but no answer? Run `bridge status`:
   - `awaiting`: Grok was woken but never claimed the job. Check the routine is enabled and its commands use the correct `--agent-config` path.
   - `claimed`: Grok is working. It may be slow, or it never ran `bridge reply`. The request expires after 15 minutes.
   - `reply`: the answer is waiting to be posted. Look for `delivery_retry_or_uncertain` in the log.
   - `failed` / `uncertain`: see the sections below.

**Fix**: follow the matching section below.

## Replies arrive late

**Check**: look for `gateway_disconnected` followed by `gateway_resumed` (or a new `gateway_ready catchup_complete`) in the log. If `accepted` appears right after a reconnect, the message was sent while the bridge was disconnected and was recovered by catch-up.

**Fix**: usually nothing; short Discord outages happen. If it happens often, check the machine's network. Also note that requests run one at a time, so a long task delays the ones behind it.

## 401 Unauthorized / bad token

**Check**: the log repeats `operation failed`, or `Improper token has been passed`, and the loop restarts every few seconds. Check the token's shape without revealing it:

```bash
python3 -c 'import json;t=json.load(open("work/bridge-local/service.json"))["discord_token"];print("len",len(t),"dots",t.count("."))'
```

A real bot token has **two dots** and is about 70 characters long.

**Fix**:
1. In the Developer Portal, go to **Bot → Reset Token** and copy the new token. Don't use the OAuth2 *Client Secret*, which has no dots.
2. Stop the loop.
3. Delete `work/bridge-local/service.json` and `agent.json`; setup won't overwrite them.
4. Rerun `bridge setup --development` with hidden input. See AGENTS.md, Step 6.
5. Start the loop again.

## Ignored-message reasons (debug mode)

By default, ignored messages are not logged. **Turn debug on** by setting `BRIDGE_DEBUG=1` in the bridge's environment (for example `export BRIDGE_DEBUG=1` in your run-loop script) or adding `"debug": true` to `service.json`. Then restart. Each ignored message logs `ignored message=<id> reason=<reason>`, without its text.

| Reason | Check | Fix |
| --- | --- | --- |
| `wrong_guild` | The message came from another server | Use the configured server, or redo setup with the right server ID |
| `wrong_channel` | The message came from another channel | Post in the configured channel; the channel ID is not the server or user ID |
| `bot` | The author is a bot | Only humans are accepted |
| `not_author` | The author isn't the owner or in `authorized_user_ids` | Check the user ID (right-click the name → Copy User ID). Add extra users to `"authorized_user_ids"` in `service.json` and restart |
| `no_mention` | The bot wasn't directly mentioned | Type `@` and pick the bot. Role mentions, nicknames in text and plain replies don't count |
| `attachment` | The message had a file or image | Text only; a notice is posted in Discord |
| `rejected_429` | Rate limit (10/min) or a full queue (100) | Wait, or clear stuck requests |
| `rejected_422` | Empty or too-long text (more than 4,000 characters) | Shorten the request |
| `not_ready_recovered_on_catchup` | Arrived while disconnected | Nothing to do; it's picked up after reconnecting |

Turn debug off again once you're done, if you prefer quieter logs.

## Wake rejected or webhook problems

**Check**: the log shows `wake event=… result=rejected` (the routine refused: HTTP 4xx or a redirect), `result=retry` (rate-limited), or `result=uncertain` (a timeout or network error). `bridge status` shows `error: wake_rejected` or a state of `uncertain`.

**Fix**:
- **Rejected**: the webhook URL or key is wrong, or the routine is disabled or deleted. Copy both again from the routine panel and redo setup (delete the two config files first).
- **URL refused at setup**: only `https://api2.cursor.sh/automations/webhook/<id>` is accepted.
- **Uncertain**: the wake may or may not have started the routine. Check whether the routine ran. If it didn't, run `bridge recover … --action retry-wake --accept-duplicate-risk`.
- **Woken but nothing happens**: the routine must run `bridge claim` with the correct `--agent-config` path. A wake carries only an event ID, never the message.

## Stuck or uncertain queue

**Check**: in `bridge status`, a job is stuck in `uncertain`, or `uncertain_chunks` is above 0. Requests behind an unresolved one wait for it; requests run in order.

**Fix**: first check in Grok and Discord whether the action or message already happened, because a retry can duplicate it. Then:

```bash
.venv/bin/bridge recover --config work/bridge-local/service.json --event <id> --action retry-wake --accept-duplicate-risk  # wake Grok again
.venv/bin/bridge recover --config work/bridge-local/service.json --event <id> --action retry-send --accept-duplicate-risk  # repost the answer
.venv/bin/bridge recover --config work/bridge-local/service.json --event <id> --action skip                                # give up on it
```

Unresolved requests expire after 15 minutes with a notice, which frees the queue.

## After a reboot or crash

**Check**: `pgrep -af "run-loop|bridge run"` shows nothing, and doctor says it cannot reach the local API.

**Fix**: in development mode, nothing starts the bridge automatically. Start the loop again:

```bash
setsid nohup deploy/run-loop.sh >/dev/null 2>&1 </dev/null &
```

The queue and position are kept on disk. Messages sent while it was down are recovered if Discord still has them, up to 20,000 per start. For automatic start, use the native systemd installer, or add the command to your machine's startup or watchdog mechanism. The loop already restarts the bridge if it exits.

## Code change has no effect

**Check**: you edited or pulled code, restarted, and the behaviour didn't change. The package is installed **non-editable**, so `.venv` still runs the old copy.

**Fix**:

```bash
uv sync --frozen --no-editable --reinstall-package discord-grok-bridge
pkill -f run-loop.sh; pkill -f "bridge run --config"
setsid nohup deploy/run-loop.sh >/dev/null 2>&1 </dev/null &
```

## PyNaCl / davey voice warning

**Check**: on startup, the log shows `WARNING PyNaCl is not installed, voice will NOT be supported` (and the same for `davey`).

**Fix**: none needed. These come from the Discord library, and the bridge doesn't use voice.
