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
discarded. Known configured keys, common credential assignments, URLs, and email
addresses are redacted from text. Sanitization is heuristic: arbitrary sensitive text
embedded in a node name or an error message cannot be reliably detected. Use synthetic
data until you validate your workflows' error messages and data policy. Do not enable
an external AI provider for sensitive workloads without that review.

The database itself is not encrypted. Restrict filesystem access. Use HTTPS for remote
n8n/AI connections; base URLs are operator-controlled and cannot be supplied through
the API. HTTP redirects are not followed, preventing credential forwarding to redirect
targets. Access logging is disabled in the documented startup command and image.

The service is intended for local or trusted-network MVP use. Set `FLOWMEDIC_API_KEY`
and deploy behind a TLS reverse proxy before network exposure. There is no multi-user
authorization, rate limiting, secret manager, or audit trail yet. Diagnosing an incident
again replaces its last diagnosis and can incur another provider charge.

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

Schema upgrades run through Alembic when the process starts a file-backed database; test
suites create the schema directly. Back up SQLite before upgrading; migrations are
forward-only in normal operation. Database operations are synchronous and short; async
endpoints can briefly block during database work. Use a single application worker for the
initial SQLite deployment. Durable background jobs, bounded total sync deadlines, retry
policies and checkpoint storage are future work.

## Diagnosis

Mock responses are conservative deterministic rules, not AI inference. Live providers
must implement chat completions plus strict JSON schema output. Invalid output,
refusals, transport failures and timeouts return safe errors without updating the
incident. Diagnoses are advisory and may be wrong. The system has no workflow-write
API and never executes suggested fixes.

### Sanitized evidence bundle

The provider receives the normalized failure fields plus an explicit evidence allowlist:

- `error_description`: the detailed n8n error description, sanitized.
- `http_code` and `line_number` when n8n supplies them.
- `node_type`: the failing node's type, such as `n8n-nodes-base.stopAndError`.
- Item metadata derived only from counts: `item_count`, `output_branch_count`,
  `node_run_count`, and `item_shape` (`empty`, `object`, `array`, `scalar`, or `mixed`).

The bundle is stored on the incident after sanitization and re-sanitized immediately
before transmission. The following are deliberately excluded from both persistence and
the provider prompt: `runData`, raw input and output items, binary data, credentials,
authorization headers, workflow credential objects, node parameter values, stack traces,
and the complete execution object. Item bodies are inspected in memory only long enough
to classify their shape; their contents are discarded.

If a field is absent or malformed, it is omitted rather than guessed. For example, an
unparseable HTTP status or a non-positive line number does not become evidence. A node
failure with no detailed description still produces a diagnosis, but the model must say
the description was unavailable instead of inventing one.

### Diagnosis contract

`confidence_basis` is required. It must explain which observed fields drove the
confidence and which evidence is missing; it cannot be a restatement of the score.
`hypotheses[]` is a ranked list. Every hypothesis includes supporting evidence,
contradictory or missing evidence, and a practical verification step.
`recommendations[]` must contain at least one concrete action plus the observation that
would confirm or reject it. Empty lists, empty strings, and duplicate hypothesis ranks
are rejected as invalid provider output; when the root cause is uncertain, the action
must still gather the missing evidence. The older
`recommended_fix` field remains for compatibility, and diagnoses stored before this
contract are upgraded for display only; newly generated diagnoses must satisfy the full
schema.

These controls reduce risk but do not eliminate it. Sanitization is heuristic, providers
can still hallucinate or overstate confidence, and a diagnosis never proves a root cause.
Verify suggested steps in a safe environment, and never paste credentials or raw payloads
back into a workflow or issue tracker.
