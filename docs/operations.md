# Operational boundaries and account operations

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

Real dashboard data requires a revocable user session and validated workspace membership.
Deploy behind a TLS reverse proxy with the cookie/CSRF settings below. An optional
`FLOWMEDIC_API_KEY` authenticates the internal service boundary independently of the user.
Authentication attempts are throttled and minimal account/business actions are audited.
Diagnosing an incident requires admin permission, replaces its last diagnosis, and can incur
another provider charge. Provider secrets remain in infrastructure environment variables.

## Public demo mode

`DEMO_MODE=true` is the exception for a portfolio demonstration. It seeds one documented,
synthetic failure and uses the deterministic mock provider. The setting rejects `N8N_API_KEY`
and `AI_API_KEY`, so the demo process cannot call a live n8n or AI service. Use a dedicated
database, leave `FLOWMEDIC_API_KEY` empty only for synthetic demo deployments, and do not
mix this mode with real incidents or credentials.

## Persistence and concurrency

A database can contain multiple isolated workspaces. The current environment's one n8n
instance and AI/webhook configuration belong only to the reserved legacy workspace;
per-workspace integration settings are a separate future change. Execution IDs, checkpoints,
alert names, event keys and condition keys are unique within their workspace. Savepoints
protect duplicate ingestion; immutable scoped sessions constrain reads, writes, leases,
retention and alert delivery. SQLite foreign keys are enabled on application connections.
Stored execution timestamps are normalized to UTC before persistence.

Alembic upgrades file-backed storage at startup. The ownership migration must run while old
application processes are stopped, after a consistent backup. In-memory tests register all
model modules and use create_all; separate file-backed tests verify real migration behavior.
Monitoring has persisted checkpoints, leases, retries and bounded retention. This deployment
continues to use SQLite on a single VPS; PostgreSQL and distributed queues are out of scope.

## Diagnosis

Mock responses are conservative deterministic rules, not AI inference. Live providers
must implement chat completions plus strict JSON schema output. Invalid output,
refusals, transport failures and timeouts return safe errors without updating the
incident. Diagnoses are advisory and may be wrong. The system has no workflow-write
API and never executes suggested fixes.


## Accounts and workspace ownership

Existing records are assigned to reserved legacy workspace `00000000-0000-0000-0000-000000000001`.
Only the operator bootstrap command creates its initial admin. Never bootstrap with an existing
public-signup identity; the command refuses this. New signups receive a new private workspace.
The synthetic demo uses `00000000-0000-0000-0000-000000000002`; its public reads cannot select
or access any real workspace. Reserved demo accounts are not created by public signup.

Run commands below from `flowmedic-ai/`. Production uses the same database volume for API,
migrations and account commands. Stop old application processes during the ownership migration.

```sh
docker compose build
docker compose stop dashboard api
docker compose run --rm --no-deps api python -m app.commands.backup /data/backups/pre-auth.db
docker compose run --rm --no-deps api python -c 'from app.core.config import Settings; from app.core.migrations import upgrade_database; upgrade_database(Settings().database_url)'
read -r FLOWMEDIC_OWNER_EMAIL
docker compose run --rm --no-deps api python -m app.commands.bootstrap_owner "$FLOWMEDIC_OWNER_EMAIL"
docker compose up -d
```

The backup filename is exclusive: an existing backup is never overwritten. For a fresh installation
with no database, omit the pre-auth backup command and run migration before bootstrap. Bootstrap
prompts twice with hidden input; repeat bootstrap with the same email leaves the password unchanged.
Environment n8n/AI/webhook credentials and monitoring belong exclusively to the legacy workspace.
Other workspaces show empty/unconfigured status until the GUI settings feature is implemented.

Set `AUTH_TRUSTED_ORIGINS` to the exact public HTTPS origin, without a trailing slash. Cookies are
host-only with Path=/ and SameSite=Lax; the session cookie is HttpOnly and both cookies are Secure.
Set `AUTH_ALLOW_INSECURE_COOKIES=true` only for local HTTP development; validation rejects
non-loopback origins in that mode. `AUTH_REGISTRATION_ENABLED=false` is the production default.
Enable registration to create invited users in this MVP, then close it again if desired. Invitations
are generated in Settings by admins, expire after `AUTH_INVITE_SECONDS`, are single-use, and carry
the token in a URL fragment. The invite page supports login/signup without transmitting that
fragment in a URL query. The admin API can revoke an invitation with DELETE `/api/v1/auth/invites/{id}`.

The browser always calls `/api/backend/*`. Compose sets dashboard `BACKEND_INTERNAL_URL=http://api:8000`.
`FLOWMEDIC_API_KEY` optionally authenticates this service boundary and must match on API/dashboard.
A correct internal token alone cannot read business data. The proxy forwards only `fm_session`,
`fm_csrf`, Origin and X-CSRF-Token, and relays each Set-Cookie individually. Identity and business
responses use no-store; do not enable CDN/shared caching on these paths. API documentation is
disabled unless `API_DOCS_ENABLED=true` is explicitly configured for a private development instance.

Authentication uses a 15-minute database-backed throttle: 60 attempts per peer address, 10 per
normalized account, and at most 10,000 current throttle keys. Signup/login share that account budget;
invite operations share the peer budget. By default all proxy requests share its backend peer IP.
To get individual client addresses in production, configure the trusted HTTPS ingress to overwrite
X-Forwarded-For and block direct access to port 3000, set the internal service token, and explicitly
set `TRUST_PROXY_IP=true`. Never trust client-supplied forwarding headers without that ingress.

All mutations require an allowed Origin and X-CSRF-Token matching the CSRF cookie. Authenticated
mutations additionally require the stored session CSRF hash. API clients must first GET
`/api/v1/auth/csrf`, retain cookies, then include Origin and X-CSRF-Token on login and subsequent
mutations. Membership and role are checked on every request. Switching workspace rotates the
session; the dashboard reloads to dispose of old workspace data. Sessions expire after
`AUTH_SESSION_SECONDS` and at most ten concurrent active sessions are retained per account.

### Password reset, disablement and revocation

```sh
read -r FLOWMEDIC_ACCOUNT_EMAIL
docker compose exec api python -m app.commands.account reset "$FLOWMEDIC_ACCOUNT_EMAIL"
docker compose exec api python -m app.commands.account revoke "$FLOWMEDIC_ACCOUNT_EMAIL"
docker compose exec api python -m app.commands.account disable "$FLOWMEDIC_ACCOUNT_EMAIL"
```

Reset asks for the new password twice with hidden input. Each operation revokes all that user's
sessions immediately. Removing a workspace member revokes that user's sessions for that workspace;
role changes apply on the next request. Logout revokes the current session. The membership API
prevents removal/demotion of the last workspace admin. The operator can provision a new identity
only through an explicit local command; no public endpoint grants ownership of legacy records.
SSO, MFA, email verification and email recovery are out of scope.

### Backup and restore

Use `python -m app.commands.backup /data/backups/next-backup.db` inside the API container for later
consistent backups. Keep backups private and copy them off the VPS. SQLite backups contain account
hashes, ownership, diagnoses, history and audit records. The auth migration intentionally refuses
Alembic downgrade because dropping accounts is not a safe rollback.

To restore the pre-auth snapshot, stop both services, restore the complete database, then run the
application image corresponding to that backup. Restoring a pre-auth database discards accounts and
activity created after its snapshot; this is a recovery action, never a normal downgrade.

```sh
docker compose stop dashboard api
docker compose run --rm --no-deps api python -c 'import os, sqlite3; from pathlib import Path; source = Path("/data/backups/pre-auth.db"); assert source.is_file(); restore = Path("/data/restore.db"); original = sqlite3.connect(source); target = sqlite3.connect(restore); original.backup(target); assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"; target.close(); original.close(); [Path("/data/flowmedic.db" + suffix).unlink(missing_ok=True) for suffix in ("-wal", "-shm")]; os.replace(restore, "/data/flowmedic.db")'
```

Before reopening traffic, verify foreign_key_check is empty, legacy row counts match the backup,
and the HTTPS dashboard passes the two-account smoke test in implementation.md. Successful
business writes, account operations and denied workspace mutations append minimal audit metadata;
passwords, session/invite tokens, API keys and upstream payloads are never audit payloads.
