# Public synthetic demo deployment

This is an **optional, low-cost portfolio demonstration**, not a production deployment. Never configure real n8n, AI, or patient/customer data.

## Backend on Render
1. In Render, choose **New → Blueprint** and connect this GitHub repository.
2. Select the branch containing `render.yaml` (merge only after checks pass).
3. Verify the Blueprint creates a Python Web Service on the Free plan.
4. Wait for a successful health check at `https://<your-render-service>.onrender.com/health`.
5. Copy the **actual URL assigned by Render** (do not invent it).

The Blueprint sets `DEMO_MODE=true` and `PUBLIC_DEMO_READONLY=true` and stores the synthetic SQLite index under `/tmp` (ephemeral storage). This is deliberately **read-only** for public visitors. No real credentials should be added. Free services can spin down, and the demo database may reset across restarts.

## Dashboard on Vercel
1. In Vercel, choose **Add New → Project**, import this repository.
2. Set **Root Directory** to `frontend`; Next.js is the framework.
3. Set `NEXT_PUBLIC_API_BASE_URL` to your Render backend's complete HTTPS origin, with no trailing slash.
4. Redeploy the frontend after setting the build-time environment variable.
5. Open the actual `*.vercel.app` URL and check dashboard pages.

## Required backend CORS change
Render must permit requests from your Vercel site. Add `CORS_ALLOW_ORIGINS` to the Render service, setting it to exactly your Vercel origin (e.g. `https://your-actual-project.vercel.app`). Redeploy/restart the backend. Never use wildcard origins with real secrets.

## Verify
- `/health` responds with `{"status":"ok"}`.
- Dashboard overview and incidents load from the actual API.
- A `POST` to backend's `/api/v1/monitoring/sync` must return HTTP 403 in public read-only mode.
- Confirm the dashboard labels data as synthetic. Do not show any real workflow names or tokens.
- Record an **actual** screenshot and attach a `Live Demo` link to README only after testing the public URL.

**Limits:** No live n8n account, no live LLM and no writable demo actions. Render free instances may be slow to wake; the temporary SQLite data may be reset.
