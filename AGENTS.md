# Agent instructions

Read README.md, SECURITY.md, and docs/architecture.md before implementing this bridge. Security takes precedence over setup convenience. The current tree is a design foundation, not a runnable product. Do not label proposed commands or controls as implemented.

Keep authorization outside model prompts. Use stored request destinations, durable state transitions, request-scoped reply authorization, and explicit ambiguous-delivery states. Do not promise exactly-once agent execution or Discord delivery without a verifiable runtime contract.

Never request or commit credentials. Use local hidden input during setup. Tests must exercise failure and recovery behavior with fake transports before live integration. Keep protocol changes versioned and document integration assumptions.
