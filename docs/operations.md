# Phase 1 operational boundaries

## n8n compatibility

The client uses the [n8n public API](https://docs.n8n.io/api/api-reference/) and
`X-N8N-API-KEY`. Connectivity checks require workflow-read permission; execution
monitoring also requires execution-read permission. Recent execution lists are
paginated. The client independently checks actual execution status instead of
trusting a server filter. Older lists with no status require detail requests.
`finished=false` alone is not a failure: an execution may be running or waiting.
Unknown status values are not classified as failures.

Execution error extraction uses `data.resultData.error` and `lastNodeExecuted`.
If details are absent, incidents explicitly contain unknown fields. A deleted or
unavailable detail request fails the page and can be retried; there is no partial
success response. Live n8n behavior depends on version, API permissions, and execution
retention. There is no bundled n8n server or live credential requirement in tests.

## Data handling

Only normalized metadata and bounded error text are persisted or sent to AI. Node
inputs/outputs, raw execution payloads, credential objects, and stack fields are
discarded. Known configured keys, common credential assignments, URLs, email
addresses, and common bare token shapes are redacted from text before it is persisted
to SQLite and before it is sent to an AI provider. The AI provider repeats redaction
before building its prompt as defense in depth. Covered token shapes include
OpenAI-style `sk-...` keys, GitHub `ghp_...`/`github_pat_...` tokens, Slack
`xoxb-...` tokens, AWS `AKIA...`/`ASIA...` access key IDs, JWTs beginning with `eyJ`,
standalone hexadecimal strings of 32 or more characters, and conservative
high-entropy Base64/Base64URL-like strings of 32 or more characters.

Sanitization is heuristic and cannot guarantee detection of every possible secret
format, especially custom token formats, short secrets, or secrets split across
fields. Long high-entropy identifiers can be over-redacted. Do not deliberately
include credentials or other sensitive values in error messages or node names. Use
synthetic data until you validate your workflows' error messages and data policy. Do
not enable an external AI provider for sensitive workloads without that review.
Sanitization applies to new writes; rows persisted before this protection existed are
not retroactively scrubbed, so review and purge old incident records if needed.

The database itself is not encrypted. Restrict filesystem access. Use HTTPS for remote
n8n/AI connections; base URLs are operator-controlled and cannot be supplied through
the API. HTTP redirects are not followed, preventing credential forwarding to redirect
targets. Access logging is disabled in the documented startup command and image.

The service is intended for local or trusted-network MVP use. Set `FLOWMEDIC_API_KEY`
and deploy behind a TLS reverse proxy before network exposure. There is no multi-user
authorization, secret manager, or audit trail yet. Mutating routes return 503 until
`FLOWMEDIC_API_KEY` is set (`ALLOW_UNAUTHENTICATED_MUTATIONS=true` is a local-only opt-out).
`diagnose` is rate limited per caller in memory (per process). Diagnosing an incident
again requires `?force=true`, replaces its last diagnosis and can incur another provider charge.

## Public demo mode

`DEMO_MODE=true` is the exception for a portfolio demonstration. It seeds one documented,
synthetic failure and uses the deterministic mock provider. The setting rejects `N8N_API_KEY`
and `AI_API_KEY`, so the demo process cannot call a live n8n or AI service. Use a dedicated
database, leave `FLOWMEDIC_API_KEY` empty only for synthetic demo deployments, and do not
mix this mode with real incidents or credentials.

## Persistence and concurrency

One database belongs to one n8n instance. Execution IDs are unique within that instance;
use a separate database when switching instances. A database uniqueness constraint and
savepoint protect duplicate ingestion. SQLAlchemy's generic types and repository layer
allow a PostgreSQL driver/URL later without changing normalization or API logic.
PostgreSQL has not been integration-tested in Phase 1. SQLite may discard timezone
offset metadata; stored execution timestamps are normalized to UTC before persistence.

Schema creation runs at startup for this fresh MVP. It is not a schema migration system.
Back up SQLite and add Alembic before evolving a deployed schema. Database operations
are synchronous and short; async endpoints can briefly block during database work.
Use a single application worker for the initial SQLite deployment. Durable background
jobs, bounded total sync deadlines, retry policies and checkpoint storage are future work.

## Diagnosis

Mock responses are conservative deterministic rules, not AI inference. Live providers
must implement chat completions plus strict JSON schema output. Invalid output,
refusals, transport failures and timeouts return safe errors without updating the
incident. Diagnoses are advisory and may be wrong. The system has no workflow-write
API and never executes suggested fixes.
