# Security

The implemented v1 assumes one trusted Discord owner, one private channel, one Grok runtime, and a trusted host administrator. This is not a shared public bot service.

## Implemented boundaries

- Startup requires a bot token, webhook credential, valid guild/channel/owner IDs, and strong generated credentials. Only the configured human's direct mentions in the configured guild/channel are admitted. DMs, bots, and attachments are excluded.
- The only inbound API binds to `127.0.0.1`. All routes require authentication; there is no public health endpoint. Access logs are disabled. Request bodies, text sizes, queue capacity, message rate, completion time, and retries are bounded.
- Agent and administration credentials are separate. The agent credential is stored only as a SHA-256 hash in the service config. Replies additionally require a hashed, request-scoped capability bound to the current attempt. Comparisons use constant-time primitives. Capabilities are not printed by the helper.
- Reply requests accept a strict field set and cannot specify a destination, filesystem path, URL, or command. The server chooses the stored originating Discord channel and checks its current configuration before sending. Replayed accepted answers are idempotent; different final answers conflict.
- Wakes carry no user text or reply capability. Configured webhook URLs require the known HTTPS provider host/path; redirects and environment proxy inheritance are disabled. Unknown provider hosts require an explicit reviewed code change.
- The Linux installer gives the service its own account, private state directory, and hardened systemd unit. Grok receives only the separate helper profile. The service's dependencies and source are installed under `/opt`, outside Grok's writable files. The installer requires a system Python and does not fetch a managed interpreter into an inaccessible root home directory.
- Database/secrets and atomic temporary config files use restrictive modes. SQLite uses WAL and FULL synchronous commits. A service lock excludes multiple runtime owners. Conversation bodies are logically cleared after retention; minimal tombstones prevent old-message duplication. Helper handles are compacted after accepted replies and aged out on future helper invocations.
- Messages and history are never evaluated as shell commands. The routine uses file tools plus stdin redirection. Discord sends disable all mentions and automatic link previews. Logs contain event IDs and error categories, not message bodies or credentials.
- A persisted request is required before acknowledgment. A reply is stored before the API returns success. No indefinite replay of uncertain wakes or old ambiguous sends is performed.

## Boundaries that still depend on deployment

The default installer requires Linux/systemd and Grok running as an unprivileged user. A Grok process with root, sudo, Docker administration, or access to the service files can defeat OS isolation. The explicit development mode runs under one account and trusts that account's processes. Loopback authentication remains required in both modes.

Prompt instructions do not prevent an authorized Grok runtime from abusing its own tools. Restrict those tools and permissions independently. Request deduplication prevents repeated visible answers; exactly-once external tool actions require an agent execution ledger. A claimed job is not automatically reassigned after a crash.

SQLite file permissions are not encryption. Retention does not securely erase old SQLite pages, journals, backups, or separately created reply files. Protect disk/backups and remove temporary reply files in the routine.

Offline tests exercise the security boundaries and failures. They do not prove live provider compatibility or the Linux installer/systemd behavior. No live credentials or live deployment have been used in verification. Perform a real request/final-answer smoke test before relying on the service.

## Reporting

Keep reports private while they contain deployment details or credentials. Never put credentials in GitHub issues or Discord. Stop the service, revoke affected tokens, and regenerate local profiles if credentials are exposed.
