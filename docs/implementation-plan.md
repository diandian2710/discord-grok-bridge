# Implementation plan

This is the initial repository plan; none of these runtime milestones is complete.

## 1. Resolve and prove the integration

Confirm Grok Bot routines vs xAI API, private vs shared use, host topology, webhook body availability, and required conversation-history behavior. Prove a text request and final reply using a fake Discord transport first, then a real Grok routine. Record the actual webhook authorization and response behavior; do not infer completion from HTTP 200.

## 2. Durable message core

Create versioned SQLite migrations, inbox/request/outbox tables, unique event and response keys, transactional transitions, per-conversation FIFO claims, leases, attempt fencing, expiry, and bounded queues. Record reply chunks independently. Add a single-instance service lock or fully specify multi-worker database ownership.

Required tests: duplicate event replay; restart with queued and active work; two concurrent claims; earlier retry blocks later same-conversation work; unrelated conversations progress; expiry releases capacity; stale generation rejected; callback arriving before webhook response; database write failure never produces an acceptance acknowledgment.

## 3. Security and transport

Add fail-closed configuration, authenticated loopback helper/API, scoped reply validation, constrained HTTPS wake requests without redirects, input/output limits, and Discord mention suppression. Keep the Discord credential in the bridge service boundary.

Required tests: wrong/missing credentials; unknown event; changed destination fields rejected; wrong bot credential; expired capability; duplicate final response produces one outbox result; conflicting final response returns conflict; redirect cannot forward wake credentials; unauthorized guild/channel/author never reaches storage or Grok; body and queue limits; credentials absent from log output.

## 4. Discord and Grok adapters

Implement mention-only Gateway receive, persist-before-ack, original-message references, bounded ordered catch-up, Grok wake/completion lifecycle, explicit no-reply, and persistent chunk delivery. Retry only when the outcome is known or can be reconciled safely. Keep one agent task active per conversation.

Required tests: HTTP 200 without callback expires visibly; transient failure recovery; permanent authentication failure; Discord rate limiting; partial chunk failure; crash after remote acceptance before database commit; duplicate reply submission; process restart; source message deletion; watermark/catch-up interleaving. Use a fake server for deterministic failure injection; run one real Discord/Grok smoke test after configuration.

## 5. Setup and operations

Implement setup wizard, secret generation, config validation, mock mode, topology checks, round-trip doctor, status/recovery commands, retention, service management, and locked release packaging. Choose the primary installation form once the actual host is known. Avoid an installer that executes a mutable remote script without an inspectable pinned release.

A new user should configure one Discord application and one Grok routine, answer one wizard, and start one service. Success requires an observed final reply, working allowlist rejection, and restart recovery. No credential entry through Discord or chat.

## Release gate

Deliver a runnable text bridge only after all security, recovery, and lifecycle tests above pass and the selected Grok runtime is verified. Publish the exact delivery limitations in the README. Broaden to multiple bots, threads, shared servers, attachments, or streaming only after their authorization and context contracts are defined.
