# AGENTS.md

Guidance for AI coding agents working in this repository. Read `SETUP.md` first for the
environment.

## What this project is

FlowMedic watches n8n executions, stores sanitized incidents, and produces advisory AI
diagnoses. **It is read-only toward n8n.** Do not add code that writes, retries, or repairs
workflows without an explicit request and a design discussion — the read-only boundary is the
product's main safety guarantee.

## Hard rules

1. **Never push to `main`.** Create a branch, open a PR, let CI run.
2. **One issue per branch/PR.** Branch names: `fix/<issue>-<slug>`, `docs/<slug>`, `feat/<slug>`.
3. **Never commit secrets or data**: `.env`, `*.db`, `graphify-out/`, tokens, MCP configs.
4. **The n8n MCP token is production access.** Never print it, commit it, or paste it into an
   issue. Keep it in the provided `opencode.jsonc` only.
5. **Do not weaken sanitization.** Anything persisted or sent to the AI goes through
   `sanitize()` first. No `runData`, credentials, stacks, or raw payloads.
6. **Deterministic over clever.** Health and severity are rules, not LLM output. Keep it that
   way.

## Layout

```
app/integrations/n8n/client.py   the only place that talks to n8n (GET only)
app/services/monitoring.py       polling loop, lease, backfill, retention
app/services/incidents.py        normalize() — the sanitized Failure record
app/services/health.py           deterministic health rules
app/repositories/                persistence + idempotency
app/api/routes.py                typed HTTP surface
frontend/app/api/backend/        server-side proxy that injects the bearer token
tests/                           offline tests; live n8n tests are opt-in
```

## Commands

```bash
# backend
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
alembic upgrade head

# frontend
cd frontend && npm run lint && npm run typecheck && npm test && npm run build
```

## Conventions

- Python 3.12+, 100-char lines, Ruff rules `E,F,I,UP,B`.
- Pydantic models for every request/response; no dict-shaped responses.
- Error responses use `{"error": {"code": "...", "message": "..."}}`.
- Tests are offline by default — mock HTTP transports, never real credentials.
- Add a regression test with every bug fix. The issue body usually states the acceptance
  criteria; satisfy them literally.

## Working with the n8n MCP

- Read `get_sdk_reference` before writing workflow code.
- `validate_workflow` before `create_workflow_from_code` or `update_workflow`.
- A workflow must have `settings.availableInMCP = true` before it can be executed by the agent.
- Test workflows you create should be named `[FM-TEST] ...` or `[FM-REAL] ...` so they are
  easy to find and remove.

## Definition of done

- [ ] Tests added/updated and passing
- [ ] `ruff check` and `ruff format --check` clean
- [ ] No secrets, DBs, or generated output staged
- [ ] PR description links the issue (`Closes #N`)
- [ ] Read-only boundary unchanged (or the PR explains why it changed)
