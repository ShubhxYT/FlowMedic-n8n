# FlowMedic AI — full setup from zero

This is the onboarding guide for a new machine: pull the repo, run the stack, connect your
own n8n, connect an AI provider, and wire up the agent MCP tooling.

FlowMedic is **read-only toward n8n**. It polls executions, stores incidents, and asks an LLM
for an advisory diagnosis. It never edits, retries, or repairs a workflow.

---

## 0. What you are setting up

| Piece | Port | Tech |
|---|---|---|
| Backend API | 8000 | FastAPI + SQLAlchemy + SQLite |
| Dashboard | 3000 | Next.js 16 + React 19 |
| n8n (external) | — | your instance, public API v1 |
| AI provider (external) | — | OpenAI-compatible, strict JSON schema |

---

## 1. Prerequisites

- **Git**
- **Python 3.12+** (`python3 --version`)
- **Node.js 22+** and npm (CI uses 24)
- **Docker** (optional, for the container path)
- An **n8n instance** you can reach over HTTP(S). n8n 2.x is what we validate against.

---

## 2. Pull the repo

```bash
git clone https://github.com/ShubhxYT/FlowMedic-n8n.git
cd FlowMedic-n8n
```

The project root is the repo root — there is no nested folder.

---

## 3. Backend

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"

cp .env.example .env                 # then edit it (section 5)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

On boot the app runs Alembic migrations automatically, creates `flowmedic.db`, and starts the
monitoring loop **only if** `N8N_BASE_URL` and `N8N_API_KEY` are both set.

Useful checks:

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/api/v1/system/status
curl -s http://127.0.0.1:8000/api/v1/n8n/status
```

### Fastest possible start (no n8n, no AI key)

```bash
DEMO_MODE=true DATABASE_URL=sqlite:///./flowmedic-demo.db \
  python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

This seeds one synthetic incident with a mock diagnosis. `DEMO_MODE` refuses to start if
`N8N_API_KEY` or `AI_API_KEY` is set.

---

## 4. Frontend

In a second terminal:

```bash
cd frontend
cp .env.example .env.local
npm ci
npm run dev
```

Open <http://127.0.0.1:3000>.

`frontend/.env.example` points at `http://127.0.0.1:8000` by default. In production the
dashboard talks to `/api/backend/*` and the Next route handler
(`frontend/app/api/backend/[...path]/route.ts`) forwards to the API with the bearer token
attached server-side — the key never reaches the browser.

---

## 5. Connect your n8n

### 5.1 Create an API key

In n8n: **Settings → n8n API → Create API key**.

Required scopes for monitoring:

- `workflow:read` (and `workflow:list`)
- `execution:read` (and `execution:list`)

Scope the key as narrowly as your n8n version allows. FlowMedic never needs write access.

### 5.2 Verify the key

```bash
curl -s -H "X-N8N-API-KEY: $N8N_KEY" \
  "https://your-n8n.example.com/api/v1/workflows?limit=1"
```

A `200` means you are good. `401` means a bad key; `403` means missing scopes.

### 5.3 Fill `.env`

```bash
N8N_BASE_URL=https://your-n8n.example.com     # origin only, no /api/v1
N8N_API_KEY=your-key
DATABASE_URL=sqlite:///./flowmedic.db
CORS_ALLOW_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
FLOWMEDIC_API_KEY=                            # leave empty for local dev
```

`N8N_BASE_URL` must be the origin. The client appends `/api/v1` itself.

Docker note: `http://localhost:5678` inside a container points at the container. Use the
compose service name (e.g. `http://n8n:5678`) or a public HTTPS origin.

### 5.4 Confirm ingestion

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/monitoring/sync
curl -s http://127.0.0.1:8000/api/v1/monitoring/status
curl -s "http://127.0.0.1:8000/api/v1/incidents?limit=5"
```

The background poller then runs every `MONITOR_POLL_INTERVAL_SECONDS` (default 60).

---

## 6. AI provider (optional)

Without a key, diagnosis uses a deterministic mock provider — no network, no cost.

To use a real model you need an **OpenAI-compatible** endpoint that supports
`response_format: { type: "json_schema", strict: true }`. Many "OpenAI-compatible" servers do
not, and will fail with `ai_error` (the incident is left unchanged).

```bash
AI_API_KEY=...
AI_BASE_URL=https://your-provider/v1
AI_MODEL=your-model
```

Test it:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/incidents/<id>/diagnose
```

---

## 7. Verification checklist

- [ ] `GET /health` → `{"status":"ok"}`
- [ ] `GET /api/v1/n8n/status` → `{"connected":true}`
- [ ] `POST /api/v1/monitoring/sync` → `status: completed`
- [ ] Run a workflow in n8n that fails → it appears in `/incidents` within one poll
- [ ] Run the same sync again → `incidents_created: 0` (idempotent)
- [ ] `POST /incidents/{id}/diagnose` → a diagnosis with `confidence` and `recommended_fix`
- [ ] Run a healthy workflow → **no** incident is created
- [ ] Dashboard renders all five pages

Full test suites:

```bash
python -m pytest -q
python -m ruff check .
python -m ruff format --check .

cd frontend
npm run lint && npm run typecheck && npm test && npm run build
```

---

## 8. Agent setup — MCP servers

The team uses OpenCode with the **n8n MCP server** so an agent can read and build workflows
directly. Ask a maintainer for the project's **`opencode.jsonc`** — it contains a bearer token
and must **never** be committed.

### 8.1 Config shape

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "n8n-mcp": {
      "type": "remote",
      "url": "https://<your-n8n-host>/mcp-server/http",
      "headers": {
        "Authorization": "Bearer <n8n-api-key>"
      },
      "enabled": true
    }
  }
}
```

The n8n MCP server is exposed by n8n itself at `/mcp-server/http`; it authenticates with a
normal n8n API key that has the `mcp-server-api` audience.

Restart the client after changing the file, then confirm the tools are loaded:

```
n8n-mcp: search_workflows, get_workflow_details, create_workflow_from_code,
         validate_workflow, execute_workflow, get_execution, ...
```

### 8.2 Enabling MCP access on a workflow

A workflow must opt in before an agent can execute it. Either toggle **Available in MCP** in
the workflow card/settings, or set it through the public API:

```bash
curl -X PUT "https://your-n8n.example.com/api/v1/workflows/<id>" \
  -H "X-N8N-API-KEY: $N8N_KEY" -H "Content-Type: application/json" \
  -d '{"name":"...","nodes":[],"connections":{},"settings":{"availableInMCP":true}}'
```

Otherwise every call returns:
`Workflow is not available in MCP.`

### 8.3 What the agent can do with it

| Tool | Use |
|---|---|
| `search_workflows` / `get_workflow_details` | inspect what exists |
| `get_sdk_reference` / `get_node_types` | correct SDK + parameter names |
| `validate_workflow` | validate before writing |
| `create_workflow_from_code` | create a workflow |
| `update_workflow` | atomic edits (nodes, connections, settings) |
| `execute_workflow` | run it |
| `get_execution` / `search_executions` | read results and errors |

⚠️ The token is powerful: it can create, edit, publish, execute, and archive workflows.
Treat it as production access and never paste it into a commit, issue, or screenshot.

---

## 9. Docker

```bash
cp .env.example .env
docker compose up --build
```

- API: <http://127.0.0.1:8000>
- Dashboard: <http://127.0.0.1:3000>
- SQLite lives in the named volume `flowmedic-data` at `/data/flowmedic.db`

Both ports bind to `127.0.0.1`. For a public deployment put a TLS reverse proxy in front
(Caddy/Nginx) and set `BACKEND_INTERNAL_URL` + `NEXT_PUBLIC_API_BASE_URL=/api/backend`.

---

## 10. Known issues

Tracked in <https://github.com/ShubhxYT/FlowMedic-n8n/issues>. Priorities are demo-oriented.

### P0 — demo blockers

| # | Issue |
|---|---|
| [#1](https://github.com/ShubhxYT/FlowMedic-n8n/issues/1) | `error_type` always `UnknownError` for Code nodes |
| [#2](https://github.com/ShubhxYT/FlowMedic-n8n/issues/2) | Successful runs stored as `Unknown workflow` |
| [#3](https://github.com/ShubhxYT/FlowMedic-n8n/issues/3) | `POST /diagnose` is unauthenticated when no key is set |
| [#4](https://github.com/ShubhxYT/FlowMedic-n8n/issues/4) | Incidents cannot be resolved → permanently `unhealthy` |

### P1 — high

| # | Issue |
|---|---|
| [#5](https://github.com/ShubhxYT/FlowMedic-n8n/issues/5) | Dashboard cannot send a bearer token |
| [#6](https://github.com/ShubhxYT/FlowMedic-n8n/issues/6) | Severity is effectively a constant `medium` |
| [#7](https://github.com/ShubhxYT/FlowMedic-n8n/issues/7) | Sanitization misses bare secret-shaped tokens |
| [#8](https://github.com/ShubhxYT/FlowMedic-n8n/issues/8) | Monitoring loop dies permanently on an unhandled exception |
| [#9](https://github.com/ShubhxYT/FlowMedic-n8n/issues/9) | Dashboard never auto-refreshes |
| [#10](https://github.com/ShubhxYT/FlowMedic-n8n/issues/10) | AI diagnosis is starved of evidence |

### P2 — medium

| # | Issue |
|---|---|
| [#11](https://github.com/ShubhxYT/FlowMedic-n8n/issues/11) | A 404 on execution detail aborts the poll cycle |
| [#12](https://github.com/ShubhxYT/FlowMedic-n8n/issues/12) | `>50` executions between polls are deferred/lost |
| [#13](https://github.com/ShubhxYT/FlowMedic-n8n/issues/13) | Metrics load entire tables into Python |
| [#14](https://github.com/ShubhxYT/FlowMedic-n8n/issues/14) | Retention deletes 500/day vs up to 1440 created/day |
| [#15](https://github.com/ShubhxYT/FlowMedic-n8n/issues/15) | Lease not renewed during long detail fetches |
| [#16](https://github.com/ShubhxYT/FlowMedic-n8n/issues/16) | Waiting/canceled/unknown statuses invisible |

### P3 — low

| # | Issue |
|---|---|
| [#17](https://github.com/ShubhxYT/FlowMedic-n8n/issues/17) | `execution_id` unique alone; one DB per n8n instance |
| [#18](https://github.com/ShubhxYT/FlowMedic-n8n/issues/18) | Alembic runs inside the app lifespan |
| [#19](https://github.com/ShubhxYT/FlowMedic-n8n/issues/19) | Retry ignores `Retry-After` |
| [#20](https://github.com/ShubhxYT/FlowMedic-n8n/issues/20) | CI/README badge |
| [#21](https://github.com/ShubhxYT/FlowMedic-n8n/issues/21) | Document the inherent detection blind spots |

---

## 11. Contributing

Read [`AGENTS.md`](../AGENTS.md) for agent rules and the branch/PR workflow. Short version:

- never push to `main`
- one issue per branch and PR
- `pytest`, `ruff`, and the frontend checks must pass before opening a PR
- never commit `.env`, keys, `*.db`, or `graphify-out/`
