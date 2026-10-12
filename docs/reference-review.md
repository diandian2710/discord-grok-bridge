# Reference review

Reviewed on 12 October 2026, against the snapshots recorded in the README. Scope: setup, authorization boundaries, durability, retries, correlation, and ordering. This is a targeted source review with local reproduction, not a complete security audit or live Grok integration test.

## Recommendation

Build a small independent bridge. Use the Discord project's routing concepts and wx-bot's durable inbox pattern. Add a durable outbox, scoped reply authorization, strict conversation ordering, and explicit recovery states.

| Project | Useful patterns | Main gap for this project |
| --- | --- | --- |
| grokbot-discord | Discord Gateway, allowlists, routing, reply references | Callback destination authorization and volatile state |
| wx-bot | SQLite inbox, leases, retries, private state files, operational tooling | WeChat-specific transport; inbound delivery does not ensure ordered, deduplicated replies |

## grokbot-discord findings

**P1 — callback destination bypass.** `_on_callback` accepts caller-supplied channel/message IDs when the correlation ID is unknown. A caller with the callback secret can post into another channel the bot can access. Require a stored request and derive its destination exclusively from that record. [bot.py, lines 150–174](https://github.com/RudeDude/grokbot-discord/blob/7d096fc678385d2835b2a65f2e69bd55b839e284/src/grokbot_discord/bot.py#L150-L174)

**P1 — replies lack durable idempotency.** The pending record is removed before sending. Repeated callbacks can resend replies; a Discord send failure loses correlation state. [bot.py, lines 153–176](https://github.com/RudeDude/grokbot-discord/blob/7d096fc678385d2835b2a65f2e69bd55b839e284/src/grokbot_discord/bot.py#L153-L176)

**P1 — accepted work can disappear or stall.** Deduplication precedes capacity admission, so busy-channel messages are silently dropped and remembered as handled. Successful wakes without callbacks retain their slots indefinitely. Restart loses all pending state. [bot.py](https://github.com/RudeDude/grokbot-discord/blob/7d096fc678385d2835b2a65f2e69bd55b839e284/src/grokbot_discord/bot.py), [filters.py](https://github.com/RudeDude/grokbot-discord/blob/7d096fc678385d2835b2a65f2e69bd55b839e284/src/grokbot_discord/filters.py)

**P2 — response mentions remain enabled.** Generated reply text can notify users or roles. Disable mentions on every send. [bot.py, line 174](https://github.com/RudeDude/grokbot-discord/blob/7d096fc678385d2835b2a65f2e69bd55b839e284/src/grokbot_discord/bot.py#L174), [Discord allowed mentions](https://docs.discord.com/developers/resources/message#allowed-mentions-object)

Local mocked probes reproduced the first two findings, pending removal after a simulated Discord failure, and deduplication before busy-channel admission. No live Discord messages were sent.

## wx-bot findings and contract limits

**P1 for our requirements — reply sending has no durable outbox.** `/v1/send` immediately invokes the sender; retrying the same request can send it again. Long text is sent chunk by chunk with newly generated client IDs. A failure after one chunk can leave a partial reply, and retry can repeat earlier chunks. Add a response ID and persistent per-chunk delivery records. [api.ts, lines 105–108](https://github.com/lawrencewzen/wx-bot/blob/2fd366a60802a230583cebc89add2fb1c660ce8c/packages/core/src/api.ts#L105-L108), [client.ts, lines 98–102 and 148–160](https://github.com/lawrencewzen/wx-bot/blob/2fd366a60802a230583cebc89add2fb1c660ce8c/packages/core/src/client.ts#L98-L160)

**P2 — local API authentication is optional.** An unset token authorizes every request. CLI integration also permits reading supplied local file paths for sending. A process able to reach the loopback API can invoke these capabilities when authentication is unset; recipient allowlists still constrain sending. This is a local trust-boundary issue, not evidence of a public unauthenticated endpoint. Require authentication and omit arbitrary file paths in the text-only version. [api.ts, line 83](https://github.com/lawrencewzen/wx-bot/blob/2fd366a60802a230583cebc89add2fb1c660ce8c/packages/core/src/api.ts#L83), [api.ts, lines 150–170](https://github.com/lawrencewzen/wx-bot/blob/2fd366a60802a230583cebc89add2fb1c660ce8c/packages/core/src/api.ts#L150-L170)

**Ordering is intentionally relaxed.** A failed event's backoff lets later events overtake it. Pull leases also allow later work to proceed while earlier work is leased. This is documented behavior rather than a violation of wx-bot's own specification. Sorting already delivered events cannot undo agent side effects; order must be enforced before execution. [inbox.ts, lines 107–132](https://github.com/lawrencewzen/wx-bot/blob/2fd366a60802a230583cebc89add2fb1c660ce8c/packages/core/src/inbox.ts#L107-L132), [protocol](https://github.com/lawrencewzen/wx-bot/blob/2fd366a60802a230583cebc89add2fb1c660ce8c/docs/protocol.md)

**Webhook acceptance is distinct from task completion.** Delivery is acknowledged on HTTP success, with no requirement that the agent has completed or replied. The Grok adapter must separately track completion. [webhook.ts, lines 89–92](https://github.com/lawrencewzen/wx-bot/blob/2fd366a60802a230583cebc89add2fb1c660ce8c/packages/core/src/webhook.ts#L89-L92)

**HMAC supplies integrity, not freshness.** The signature covers body bytes; the timestamp is separate and unsigned. Deduplicating event IDs is still necessary. Our own reply protocol should bind request, attempt, and expiry, or sign timestamp + nonce + body if remote HMAC is used. [webhook.ts, lines 38–52](https://github.com/lawrencewzen/wx-bot/blob/2fd366a60802a230583cebc89add2fb1c660ce8c/packages/core/src/webhook.ts#L38-L52)

Local probes reproduced retry overtaking and successful unauthenticated API access when the token is unset. They used an in-memory database and a mocked send handler.

## Verification

| Check | Result |
| --- | --- |
| grokbot-discord pytest | 24 passed |
| grokbot-discord Ruff | Passed |
| wx-bot Bun tests | 69 passed, 364 assertions |
| wx-bot TypeScript checks | Passed for core, CLI, and Cloudflare packages |
| Additional local probes | Reproduced the behaviors described above |

Python review environment: Python 3.13.4, discord.py 2.7.1, aiohttp 3.14.5. wx-bot: Bun 1.3.14, frozen lockfile. Existing tests passing does not establish security or end-to-end consistency. Installers, Docker builds, live Discord, and live Grok routines were not exercised.
