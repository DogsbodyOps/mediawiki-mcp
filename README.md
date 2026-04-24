# MediaWiki MCP Server

Gives Claude the ability to search, read, and edit your internal MediaWiki instance via the [Model Context Protocol](https://modelcontextprotocol.io).

## Tools available to Claude

| Tool | Description | Requires `WIKI_ALLOW_WRITE` |
|------|-------------|:---:|
| `wiki_search` | Full-text search across all pages | |
| `wiki_get_page` | Fetch the wikitext of a specific page | |
| `wiki_list_pages` | List pages, optionally filtered by title prefix | |
| `wiki_get_sections` | List a page's sections with their index numbers | |
| `wiki_section_edit` | Replace a single section (safer than a full-page overwrite) | ✓ |
| `wiki_edit_page` | Create or overwrite an entire page | ✓ |
| `wiki_append_to_page` | Append text to the end of an existing page | ✓ |

---

## Running locally

### 1. Clone and install

```bash
git clone https://github.com/DogsbodyOps/mediawiki-mcp.git
cd mediawiki-mcp

python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Edit `.env` with your wiki credentials:

```env
WIKI_URL=https://wiki.your-org.com
WIKI_USERNAME=YourUsername
WIKI_PASSWORD=YourPassword
WIKI_TOTP_SECRET=YOURBASE32SECRET
```

`WIKI_TOTP_SECRET` is the base32 secret from your authenticator app. You can find it in your authenticator's account settings or via the `otpauth://` URI in BitWarden.

### 3. Connect

**VS Code (Claude Code extension):** Open this folder in VS Code — `.vscode/mcp.json` is already configured. Approve the server when prompted.

**Claude Code CLI:** Launch Claude Code from this directory:

```bash
claude
```

`.mcp.json` is already configured with relative paths and will work on any machine.

---

## Running in Docker

Deploy once, connect from anywhere. The server runs over HTTP and users connect with an API key instead of running it locally.

### 1. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```env
WIKI_URL=https://wiki.your-org.com
WIKI_USERNAME=YourUsername
WIKI_PASSWORD=YourPassword
WIKI_TOTP_SECRET=YOURBASE32SECRET

# Comma-separated list — issue a separate key per user/team
MCP_API_KEY=key-alice,key-bob,key-ops-team

PORT=8000

# Set to true to expose write tools (edit, section edit, append)
WIKI_ALLOW_WRITE=false
```

### 2. Start

```bash
docker compose up -d
```

The container exposes port 8000 and includes a health check at `/health`.

### 3. Connect (client config)

Users add this to their `.mcp.json` or `.vscode/mcp.json` instead of the local config:

```json
{
  "mcpServers": {
    "mediawiki": {
      "type": "http",
      "url": "https://your-host/mcp",
      "headers": { "Authorization": "Bearer key-alice" }
    }
  }
}
```

Put a reverse proxy (nginx, Caddy, etc.) in front for TLS — the container itself serves plain HTTP.

---

## Typical edit workflow

1. `wiki_search` — find the page
2. `wiki_get_sections` — see the section layout and indices
3. `wiki_get_page` — read the current wikitext
4. `wiki_section_edit` — update only the section that needs changing

Use `wiki_edit_page` only when creating a new page or doing a full rewrite.

---

## Security notes

- `.env` is in `.gitignore` — never commit credentials
- `WIKI_TOTP_SECRET` and `MCP_API_KEY` are sensitive — treat them as passwords
- Write tools (`wiki_edit_page`, `wiki_section_edit`, `wiki_append_to_page`) are hidden from Claude unless `WIKI_ALLOW_WRITE=true` — defaults to read-only for safety
- All edits are recorded in MediaWiki history under your account name, giving a full audit trail
- If `MCP_API_KEY` is not set, the HTTP server accepts all requests — only do this on a private network

## Use Cases

  Lookup & Research
  - "Find the static IP for host stg-cpa-app14" — search then read the relevant IP page
  - "What's the HAProxy config for the DTS environment?" — search + get sections
  - "Summarize everything we have documented on ESX performance tuning"

  Keeping Docs Up to Date
  - "We decommissioned BE-CPA-Build5 — find every page that mentions it and update them"
  - "Add stg-cpa-app14 with IP 192.168.x.x to the Static IP Addresses page"
  - "Append a post-mortem summary to the incident page for last night's outage"

  Onboarding / Knowledge Retrieval
  - "What hosts are in the Bourne End DTS environment?" — search + read + summarize
  - "Give me a runbook for setting up a new CPA QA server based on the wiki"

  Auditing & Cross-Referencing
  - "List all pages and flag any that haven't been updated recently" (via list + get)
  - "Find all pages referencing a specific VLAN or subnet"

  Assisted Authoring
  - "Draft a new wiki page for stg-cpa-app14 based on similar host pages" — read a template page, then create a new one
