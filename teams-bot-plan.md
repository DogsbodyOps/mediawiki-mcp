# Plan: Teams Bot + MCP Server on Kubernetes

## Context

The team constantly asks questions without checking the wiki. This plan deploys a Microsoft Teams bot that intercepts those questions, passes them to Claude Sonnet with read-only MediaWiki tools, and returns a synthesised answer with citations — nudging people to check the wiki themselves next time.

**Architecture:**
- **MCP Server pod** — existing `server_http.py`, internal-only (ClusterIP). No public exposure needed.
- **Teams Bot pod** — new `teams_bot.py`, externally exposed (LoadBalancer + Ingress) for Teams webhook.
- **Communication** — Teams Bot connects to MCP Server via K8s internal DNS (`http://mcp-server:8000/mcp`). Teams Bot runs the Claude tool-use loop; MCP Server executes wiki lookups. Claude never connects directly to the MCP server — the bot proxies tool calls.

```
Teams ──[HTTPS]──► Teams Bot Pod (LoadBalancer)
                        │  Claude API (outbound)
                        │  MCP calls (internal K8s) ──► MCP Server Pod (ClusterIP)
                                                              │
                                                         MediaWiki
```

---

## Files to Create

### `teams_bot.py`
aiohttp web app. Key parts:

**Startup (`on_startup` hook):**
- Open a `streamablehttp_client` to `MCP_SERVER_URL` with `Authorization: Bearer MCP_API_KEY` header — stored on `app` state for reuse across all requests (server is stateless, one connection per pod is fine)
- List tools from MCP server, filter to allowlist: `{"wiki_search", "wiki_get_page", "wiki_list_pages", "wiki_get_sections"}` (read-only; write tools excluded at listing time)
- Convert filtered tools to Anthropic format using `anthropic.lib.tools.mcp.async_mcp_tool`; store on `app`

**Routes:**
- `POST /api/messages` — Teams webhook; handled via botbuilder `BotFrameworkAdapter` + `BotFrameworkAdapterSettings`
- `GET /health` — unauthenticated, returns 200; used by K8s readiness/liveness probes

**Message handler:**
- Strip `<at>BotName</at>` @mention tags with regex
- Run Claude tool-use loop (see below)
- `turn_context.send_activity(answer)`

**Claude tool-use loop (sync, runs via `asyncio.to_thread`):**
```
messages = [{"role": "user", "content": question}]
while True:
    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        tools=anthropic_tools,   # pre-built at startup
        messages=messages,
    )
    if stop_reason == "end_turn": return text
    if stop_reason == "tool_use":
        execute each tool via session.call_tool() — double-check name is in allowlist
        append assistant + tool_result messages, loop
```
Cache note: `cache_control` on the system block caches tools + system together (render order: tools → system → messages), reducing cost on repeated calls.

**System prompt** — brief, direct:
> You are a helpful assistant for a technical team. Answer questions using the internal wiki. Always cite which page(s) your answer came from. If the answer is well-documented in the wiki, gently note that checking the wiki first would have been faster.

**Env vars:** `ANTHROPIC_API_KEY`, `MICROSOFT_APP_ID`, `MICROSOFT_APP_PASSWORD`, `MCP_SERVER_URL`, `MCP_API_KEY`, `BOT_PORT` (default 3978)

---

### `Dockerfile.teams`
```
FROM python:3.13-slim
WORKDIR /app
COPY requirements-teams-bot.txt .
RUN pip install --no-cache-dir -r requirements-teams-bot.txt
COPY teams_bot.py .
EXPOSE 3978
CMD ["python3", "teams_bot.py"]
```

### `requirements-teams-bot.txt`
```
anthropic[mcp]>=0.40.0
botbuilder-core>=4.16.0
botbuilder-integration-aiohttp>=4.16.0
aiohttp>=3.9.0
python-dotenv>=1.0.0
```

### `k8s/configmap.yaml`
Non-sensitive values shared across both deployments:
- `WIKI_URL`, `WIKI_USERNAME`, `PORT=8000`, `BOT_PORT=3978`, `MCP_SERVER_URL=http://mcp-server:8000/mcp`

### `k8s/secrets.yaml` *(template only — never commit real values)*
Credentials for both pods in one Secret `mediawiki-mcp-secrets` (using `stringData`):
- `WIKI_PASSWORD`, `WIKI_TOTP_SECRET`, `MCP_API_KEY`, `ANTHROPIC_API_KEY`, `MICROSOFT_APP_ID`, `MICROSOFT_APP_PASSWORD`

Add `k8s/secrets.yaml` to `.gitignore`.

### `k8s/mcp-server.yaml`
- **Deployment** `mediawiki-mcp`: 2 replicas (stateless, safe to scale), image `your-registry/mediawiki-mcp:latest`, port 8000, `envFrom` both ConfigMap and Secret
  - Liveness/readiness: `tcpSocket: {port: 8000}` — avoids needing an unauthed HTTP route (avoids auth bypass complexity)
- **Service** `mcp-server`: `type: ClusterIP`, port 8000 — internal only

### `k8s/teams-bot.yaml`
- **Deployment** `teams-bot`: 1 replica, image `your-registry/teams-bot:latest`, port 3978, `envFrom` both ConfigMap and Secret
  - Readiness probe: `httpGet /health :3978`, `initialDelaySeconds: 15` (allows MCP connect + tool list on startup)
  - Liveness probe: same, `initialDelaySeconds: 30`
- **Service** `teams-bot`: `type: LoadBalancer`, port 443/80 → 3978
- **Ingress** (placeholder with notes for two TLS options — cert-manager or cloud-managed cert)

---

## Files to Modify

### `server_http.py`
Add unauthenticated `/healthz` path exception inside `APIKeyMiddleware.dispatch`:
```python
async def dispatch(self, request: Request, call_next):
    if request.url.path == "/healthz":           # ← add this
        return await call_next(request)
    ...existing auth logic...
```
Also add `Mount("/healthz", app=health_handler)` to the Starlette routes. Unblocks using `httpGet` K8s probes without embedding the API key in manifests.

### `.env.example`
Add new vars: `ANTHROPIC_API_KEY`, `MICROSOFT_APP_ID`, `MICROSOFT_APP_PASSWORD`, `MCP_SERVER_URL=http://mcp-server:8000/mcp`, `BOT_PORT=3978`

---

## Implementation Order

1. `server_http.py` — add `/healthz` bypass (unblocks K8s MCP server probes)
2. `.env.example` — add new vars
3. `requirements-teams-bot.txt` — create
4. `teams_bot.py` — create (startup, routes, Claude loop)
5. `Dockerfile.teams` — create
6. `k8s/configmap.yaml` — create
7. `k8s/secrets.yaml` — create template, add to `.gitignore`
8. `k8s/mcp-server.yaml` — create
9. `k8s/teams-bot.yaml` — create

**Deployment order:**
```
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secrets.yaml    # with real values substituted
kubectl apply -f k8s/mcp-server.yaml
kubectl apply -f k8s/teams-bot.yaml
```

---

## Verification

1. **MCP server internal connectivity** — from a temp pod in-cluster: `curl -H "Authorization: Bearer $MCP_API_KEY" http://mcp-server:8000/mcp` — confirms ClusterIP DNS and auth work. `curl http://mcp-server:8000/healthz` — confirms unauthenticated health route.
2. **Bot health** — `kubectl logs -f teams-bot-<pod>` — confirm startup log shows MCP session opened and tools listed. `curl http://<bot-external-ip>/health`.
3. **End-to-end without Teams** — run `teams_bot.py` locally with `MCP_SERVER_URL` port-forwarded from the K8s MCP service (`kubectl port-forward svc/mcp-server 8000:8000`); use Bot Framework Emulator to send a question and verify a wiki-sourced answer.
4. **Teams channel test** — register the Azure Bot with the LoadBalancer IP/domain as the messaging endpoint, add to a Teams channel, ask a question about something documented in the wiki.
5. **Write-tool guard** — verify `wiki_edit_page` does not appear in the tools Claude sees (filtered at startup listing); confirm the bot's execution guard also rejects it if somehow requested.
