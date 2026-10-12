# discord-grok-bridge

A planned private Discord ↔ Grok Bot middleware, prioritizing security, simple setup, and consistent message handling.

**Status: design foundation. There is no runnable bridge yet.** The initial repository contains the source review, proposed architecture, and implementation acceptance criteria. Backend and deployment details remain open.

## Start here

- [Review of the two reference projects](docs/reference-review.md)
- [Architecture and delivery contract](docs/architecture.md)
- [Implementation plan and acceptance criteria](docs/implementation-plan.md)
- [Security requirements](SECURITY.md)

## Proposed first version

One Discord bot, one private server, one Grok Bot, and text messages addressed directly to the Discord bot. One service and one SQLite database; no Redis, separate queue server, or public dashboard.

Receive authorized messages, persist them before acknowledging them, wake Grok, accept a reply only for a stored request, and deliver it through a persistent outbox. Keep each conversation in order and preserve its context boundary.

The setup target is an interactive setup command followed by a start command. These commands are requirements, not currently implemented features. Secrets must be entered locally through hidden input, never pasted into Discord or committed.

## Open decisions

1. Grok Bot webhook/routines, as in the reference projects, or xAI's model API?
2. A private personal bridge or a shared service?
3. Can Grok run a helper on the bridge's machine, or does it require a remote callback?
4. Does “message consistency” also require shared history between Discord and the existing Grok chat? That needs explicit backend support and cannot be inferred from successful webhook delivery.

Provisional repository name: `discord-grok-bridge`; visibility: private.

## Reference snapshots

Reviewed on 12 October 2026:

- `RudeDude/grokbot-discord`: `7d096fc678385d2835b2a65f2e69bd55b839e284`
- `lawrencewzen/wx-bot`: `2fd366a60802a230583cebc89add2fb1c660ce8c`

No upstream application code has been copied into this repository.
