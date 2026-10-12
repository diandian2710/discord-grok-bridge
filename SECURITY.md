# Security requirements

Status: design requirements, not implemented controls.

- Reject startup with missing credentials or empty guild/channel/author allowlists. Ignore DMs, bot authors, and unaddressed messages in v1. Authorize before storing or forwarding message content.
- Scope Discord visibility and permissions to selected channels. Never request Administrator.
- Require authentication even on loopback. Separate outbound wake credentials, agent claim credentials, and request-scoped reply capabilities. Compare tokens using constant-time primitives; store capability hashes and bind them to request, attempt, and expiry.
- Accept reply content only for a stored request and current generation. No caller-chosen destinations, callback URLs, shell commands, or filesystem paths. Exact accepted replays are idempotent; different payloads conflict.
- Keep remote APIs private and encrypted. Restrict the configured Grok webhook host/path, require HTTPS, and disable redirects so credentials cannot be forwarded to another destination. Never derive URLs from Discord text.
- Text only initially; bounded input/output lengths, request body limits, queue capacity, per-author rate limits, request expiry, and finite retry policy. Disable all Discord mentions and automatic link previews where appropriate.
- Persist state in a private directory (0700) with private secret/database files (0600), including SQLite sidecars. Lock dependencies. Keep credentials out of source, examples, logs, URLs, process arguments, and exception dumps.
- Configure retention for input/reply content separately from minimal deduplication records. Backups contain conversation content and require the same access controls. SQLite file permissions are not encryption at rest.
- Treat Discord text as untrusted input. Never interpolate it into shell commands. Adapter instructions do not substitute for enforced authorization or tool restrictions in Grok.
- The bridge must keep the Discord token outside the agent's accessible filesystem/process environment. A routine with unrestricted execution under the bridge's OS identity can read its secrets; use separate identities or containers for a stronger boundary. A compromised host administrator remains outside this threat model.
- Use structured logs containing request IDs and state/error categories, without message bodies or secrets by default. Health checks expose minimal liveness; authenticated diagnostics inspect Gateway readiness, worker progress, and queue age.
- Require a local mock round-trip and security regression checks before the first live deployment. Require a real Grok round-trip before declaring the adapter compatible.
