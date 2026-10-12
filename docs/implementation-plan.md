# Implementation status

## Implemented and verified offline

- A Python package, installed `bridge` command, versioned SQLite schema, and locked runtime/development dependencies.
- Private single-owner, single-channel Discord mention ingress; durable admission acknowledgments, rate/capacity limits, text-only input, and bounded history catch-up with watermark coordination.
- Persistent per-conversation FIFO work, lease expiry, request capabilities, explicit no-reply completion, completed-exchange context, and refusal to automatically reexecute claimed/uncertain work.
- HTTPS wake requests to the configured provider with redirects disabled. Wake success, helper completion, and Discord delivery are separate transitions. Fast replies cannot be overwritten by late wake responses.
- Authenticated loopback API, separate helper/admin credentials, strict reply fields, stored destinations, body limits, redacted errors, and local terminal claim/reply helpers.
- Durable per-chunk outbox with deterministic nonces, restart recovery, finite ambiguous-send retries, recorded permanent failures, and explicit operator retry/skip controls.
- Private atomic configuration, service locking, logical retention, helper capability storage/compaction, and mock operation without credentials.
- 92 tests cover authorization, duplicate events/replies, conflicting responses, expiry, queue order, separate conversations, restart states, database failure rollback, response validation, body limits, redirects, partial sends, ambiguous sends, mention suppression, catch-up, and a mock HTTP/helper/Discord round-trip.

## Docker deployment

Implemented a digest-pinned multi-stage image, hardened Compose runtime, isolated setup wizard, authenticated container healthcheck, and standard-library host helper. A real Docker smoke test verifies non-root/readonly settings, unauthenticated rejection, host-helper round-trip, idempotent final submission, healthcheck execution, and persistence after container recreation. Tests use only disposable volumes and fake external transports. Production setup requires Engine 28+; the mock smoke test also runs on older engines because it contains no live credentials or external actions.

## Delivered but not exercised on the target host

- Interactive setup with hidden credential input and generated helper credentials.
- Inspected-checkout Linux installer, separate service account, and hardened systemd unit. Installer shell syntax is checked; privileged installation is not run on the developer machine.
- Pinned-action CI passed on Linux with Python 3.12 and 3.13, including tests, formatting, the installed mock command, and installer shell syntax.

Additional verification (initial native implementation): the built wheel was installed into a separate Python 3.13 environment and its mock command passed; local Ruff checks and installer shell syntax passed. A pip-audit check of the locked runtime requirements reported no known vulnerabilities in the local environment.

## Required live verification

On Grok's computer, choose Docker or the optional native Linux/systemd installer and establish the unprivileged routine identity. Install using the wizard, create/configure the supplied routine, send a Discord mention, and observe the final answer. Confirm one duplicate submission does not add a second reply and that the service recovers after restart. Validate actual webhook key handling and routine execution behavior.

`bridge doctor` checks transport readiness, catch-up, and worker liveness. It does not certify the Grok routine. The live round-trip is required before declaring the adapter compatible or the deployment ready.

## Explicitly outside v1

Remote callbacks, multiple bots/owners/channels, threads, attachments, streaming responses, shared native-Grok history, Windows, and native macOS service installation. Exactly-once external agent tool execution requires a verified agent ledger; history recovery cannot recover deleted or inaccessible messages.
