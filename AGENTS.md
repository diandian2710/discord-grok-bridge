# Agent instructions

Read README.md, SECURITY.md, and docs/architecture.md before editing this bridge. Security takes precedence over setup convenience. The runtime and Docker host-helper path are implemented and verified with offline/mock transports; live Grok compatibility and Linux installation remain unverified until exercised on the deployment host.

Keep authorization outside model prompts. Use stored request destinations, durable state transitions, request-scoped reply authorization, and explicit ambiguous-delivery states. Do not promise exactly-once agent execution or Discord delivery without a verifiable runtime contract.

Never request or commit credentials. Use local hidden input during setup. Tests must exercise failure and recovery behavior with fake transports before live integration. Keep protocol changes versioned and document integration assumptions.

Run uv sync --frozen --no-editable, pytest, Ruff checks/format checks, and the installed bridge mock command for substantive runtime changes. The Python test path must not conceal a broken installed entry point. Verify wheel packaging when packaging or installer behavior changes. Do not run privileged native installation on the developer's computer as a test. For Docker changes, build the image, validate Compose, and run deploy/docker-smoke.py with fake credentials and disposable state. Keep Grok away from the Docker daemon; the host helper must remain standard-library-only.
