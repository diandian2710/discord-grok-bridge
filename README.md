# discord-grok-bridge

A private Discord ↔ Grok Bot bridge that runs on **the same computer as Grok Bot**. One Discord bot, one server, one text channel, and one authorized human.

**Status:** runnable first implementation, verified with 92 offline tests and a mocked end-to-end round-trip. Docker execution is verified with mock transports; live Discord/Grok compatibility and the optional Linux system service still require verification on your Grok machine. No real credentials have been configured in this repository.

## How it works

```text
Discord @mention → authorize → SQLite inbox → wake Grok routine
                                              ↓
                                         bridge claim
                                              ↓
Discord reply ← SQLite outbox ← local API ← bridge reply
```

Messages are stored before “Queued.” is posted. Grok uses a terminal helper to claim a recorded request and submit its final answer. Reply destinations stay inside the bridge; Grok never needs the Discord token or a public callback URL.

Requests run one at a time in the channel. The queue and per-chunk delivery records survive restarts. Repeated submissions of the same answer are idempotent. Six completed exchanges are supplied as bounded context; native Grok chat history is not synchronized.

## Docker setup (recommended)

Run Docker on the same machine as Grok. Requirements: **Docker Engine 28+, Docker Compose, Python 3.12+ for the small host helper, and a separate unprivileged Grok OS user**. The human operator uses Docker; Grok must not have Docker daemon access, root, or sudo. No host uv installation or Python package installation is needed for this path.

Create the Discord bot and Grok webhook routine as described below. Obtain the bot token, guild/channel/owner IDs, and Grok webhook URL/key. Then, from an inspected checkout of `feat/local-bridge`, run as the human operator:

```bash
./deploy/docker-setup.sh <grok-user>
docker compose up -d bridge
```

The wizard builds the pinned image, accepts secret input locally with hidden prompts, and writes service credentials into the Docker-managed `state` volume. It writes only the scoped helper profile to Grok's own `~/.config/discord-grok-bridge/agent.json`. The one-off setup container has no network access and refuses to overwrite existing configurations. If Grok's profile folder needs creation, the operator script uses `sudo -u <grok-user>` for that step.

Place the checkout where Grok can read it. In the routine, use the absolute path to `deploy/bridge` as its helper executable, for example:

```bash
/path/to/discord-grok-bridge/deploy/bridge claim
/path/to/discord-grok-bridge/deploy/bridge reply --job /path/from/claim.json < /path/to/reply.txt
/path/to/discord-grok-bridge/deploy/bridge doctor
```

This helper uses Python's standard library and never invokes Docker. It shares the same claim/reply implementation as the installed `bridge` command. See [the routine prompt](prompts/grok-routine.md) and substitute the executable path throughout.

The runtime container runs as UID 10001, with a read-only root filesystem, dropped capabilities, no new privileges, bounded resources/logs, a healthcheck, and process restart unless stopped. Its authenticated API is published as **`127.0.0.1:18792` on the host**. Listening on all interfaces *inside* the container is enabled only by the explicit Docker mode; native deployments keep loopback binding. Service credentials are not stored in environment variables, the image, the source checkout, or the helper profile. Other containers on the same Docker network can reach the API but still need credentials; keep unrelated containers off this network.

Docker Engine 28+ is required because older engines have a documented exception to localhost-only published-port isolation. Keep the daemon's normal bridge/NAT networking; do not enable direct routing, host networking, or a remote daemon for this local-helper setup. [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/)

Operations:

```bash
docker compose ps
docker compose logs --tail 100 bridge
docker compose exec bridge bridge status --config /state/service.json
docker compose restart bridge
docker compose down
```

`down` preserves the queue and credentials. **Do not use `down -v`** unless you intend to delete that state. Stop an existing native bridge before starting Docker to avoid two listeners or a port conflict. A health failure marks the container unhealthy; `unless-stopped` restarts an exited process, not every unhealthy container.

After configuration, send one new Discord mention and observe the real final reply. That live round-trip is still required; the offline container test uses fake Discord/Grok transports.

## Native setup on Grok's Linux machine (optional)

The secure installer requires **Linux with systemd, system Python 3.12+, a system-wide uv installation, and sudo for the human installer**. Grok's routines must run as a separate unprivileged user without access to sudo, Docker administration, or the bridge's service files. Same-computer deployment avoids networking setup; separate OS accounts protect service credentials.

1. Create a Discord application and bot. Leave privileged intents disabled. Invite it with **View Channels, Send Messages, Read Message History**; restrict its visibility to your private text channel. Copy the server, channel, and your own user IDs using Discord Developer Mode.
2. Create a Grok webhook routine on this machine. Save the instructions from [prompts/grok-routine.md](prompts/grok-routine.md). Obtain its webhook URL and key. The currently supported URL is `https://api2.cursor.sh/automations/webhook/<id>`.
3. Clone this private repository onto that machine and inspect the installer. Until the implementation pull request is merged, select its branch:

   ```bash
   git clone --branch feat/local-bridge https://github.com/diandian2710/discord-grok-bridge.git
   cd discord-grok-bridge
   ```

   The private clone requires your existing GitHub access. From the inspected checkout, run:

   ```bash
   sudo ./deploy/install.sh <grok-user>
   ```

   Replace `<grok-user>` with the OS user that executes Grok's terminal commands. The wizard asks for the Discord token, IDs, and Grok webhook credentials. Secret input is hidden. It generates helper credentials, installs the service under its own account, and starts it. Existing installs/configurations are never overwritten.

4. From Grok's terminal, run `bridge doctor`. It checks Discord readiness, history catch-up, and worker liveness. Then send a fresh `@YourBridge hello` in the Discord channel. Verify **both** “Queued.” and the final answer. This real round-trip is required to establish Grok compatibility; doctor alone does not test the routine.

Do not paste tokens or webhook keys into Discord, Grok chat, issues, or commit messages. The installer executes the local inspected checkout; it does not download and execute a mutable remote script.

First startup starts at the current channel history. Later startups recover messages after the saved watermark. Messages sent while the service is offline are recovered when still present and readable. A bounded catch-up stops and requires another restart after 20,000 historical messages.

## Grok helper

```bash
bridge claim
bridge reply --job /absolute/path/from/claim.json < /path/to/private-reply.txt
bridge reply --job /absolute/path/from/claim.json --no-reply
bridge doctor
```

`claim` returns a JSON job or `{"job":null}`. The job includes a handle path, input, conversation ID, and bounded history. Its reply capability is stored privately in the handle rather than printed. `reply` reads text from stdin; no reply text is evaluated as shell code. The authenticated API always binds a reply to the stored destination.

Input is text only, at most 4,000 characters after removing the bot mention. Replies are at most 20,000 characters and split without discarding whitespace. Default limits: 100 active requests, 10 accepted requests per minute, 15 minutes from acceptance to a final reply. Queued work can expire while waiting behind a long request. All generated Discord mentions and link previews are suppressed.

## Operations

```bash
sudo systemctl status discord-grok-bridge
sudo journalctl -u discord-grok-bridge
sudo bridge status
sudo systemctl restart discord-grok-bridge
```

Status reports event IDs, states, failure categories, and uncertain delivery counts, without conversation text or secrets. A state of `delivered` means an outbox result was sent; an `error` field can indicate that this result was an expiry/failure notice rather than a successful Grok answer.

A wake timeout or interrupted send can have an uncertain outcome. Inspect Grok/Discord first. An operator can then explicitly retry or skip:

```bash
sudo bridge recover --event <event-id> --action retry-wake --accept-duplicate-risk
sudo bridge recover --event <event-id> --action retry-send --accept-duplicate-risk
sudo bridge recover --event <event-id> --action skip
```

Retry can repeat a remote action or message if it previously succeeded. Skip revokes the reply capability but cannot cancel tool actions Grok has already started. Expiry sends a failure notice and advances the queue; it does not assert that Grok's external work was cancelled.

Service credentials and SQLite state live in `/var/lib/discord-grok-bridge/`, owned by the service account with mode 0700/0600. Grok's helper profile lives in its own `~/.config/discord-grok-bridge/agent.json`. Completed database conversation bodies are cleared after seven days; minimal deduplication tombstones remain. Accepted helper handles are compacted immediately; old handles are removed on later helper invocations. Temporary reply files remain the routine's responsibility. Backups and SQLite pages may still contain old text; this is logical retention, not secure erasure or encryption.

## Local development and verification

On Linux or macOS, with Python 3.12+ and uv:

```bash
uv sync --frozen --no-editable
uv run --frozen --no-editable bridge mock --state-dir work/mock-bridge
uv run --frozen --no-editable pytest
uv run --frozen --no-editable ruff check src tests
uv run --frozen --no-editable ruff format --check src tests
```

Mock mode needs no credentials and makes no network requests. For a trusted local development session, `bridge setup --development` writes configuration under `work/bridge-local/`; run it with `bridge run --config work/bridge-local/service.json`. This opt-in mode trusts all processes under your OS account and does not isolate the Discord token from a terminal agent running under that same account. Use the separate-account installer for the intended deployment.

Dependencies are locked in `uv.lock`; CI uses pinned action commit SHAs and verifies Python 3.12 and 3.13, tests, formatting, and the command-line mock pipeline.

## Delivery and context boundaries

- Accepted events and final replies are durable and deduplicated. Agent tool execution is not guaranteed exactly once; Grok needs its own durable execution ledger to provide that.
- Discord enforces deterministic nonces only for recent creates. Automatic ambiguous-send retries use the same nonce for at most 120 seconds and three attempts, then stop for reconciliation.
- Direct mentions work without the privileged Message Content Intent. Role mentions, name prefixes, threads, attachments, streaming, shared servers, and remote callbacks are outside v1.
- Message edits do not rerun accepted work. Deleted messages or inaccessible history cannot be recovered. Rejected work receives a best-effort notice and is not queued.
- An authenticated Grok helper can access the configured owner's queued work. It cannot administer the bridge or choose another Discord destination. Host administrators are outside this credential-isolation boundary.

See [SECURITY.md](SECURITY.md), [architecture](docs/architecture.md), [implementation status](docs/implementation-plan.md), and the [reference review](docs/reference-review.md).
