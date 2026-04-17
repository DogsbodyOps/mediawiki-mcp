# MediaWiki MCP Server

Gives Claude the ability to search, read, and edit your internal MediaWiki instance via the [Model Context Protocol](https://modelcontextprotocol.io).

## Tools available to Claude

| Tool | Description |
|------|-------------|
| `wiki_search` | Full-text search across all pages |
| `wiki_get_page` | Fetch the wikitext of a specific page |
| `wiki_list_pages` | List pages, optionally filtered by title prefix |
| `wiki_get_sections` | List a page's sections with their index numbers |
| `wiki_section_edit` | Replace a single section (safer than a full-page overwrite) |
| `wiki_edit_page` | Create or overwrite an entire page |
| `wiki_append_to_page` | Append text to the end of an existing page |

---

## Setup

### 1. Clone and install

```bash
git clone <your-internal-repo-url>
cd mediawiki-mcp

python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Edit `.env`:

```env
WIKI_URL=https://wiki.your-org.com
WIKI_USERNAME=YourUsername
WIKI_PASSWORD=YourPassword
WIKI_TOTP_SECRET=YOURBASE32SECRET
```

`WIKI_TOTP_SECRET` is the base32 secret from your authenticator app (the same one used to generate your TOTP codes). You can usually find it in your authenticator's account settings or via the `otpauth://` URI in BitWarden. This is required if your wiki enforces 2FA.

---

## Connecting to Claude Code (VS Code)

The `.vscode/mcp.json` file is already configured. Open this folder in VS Code with the Claude Code extension installed:

1. VS Code will prompt you to **approve the MCP server** — click Allow
2. The `mediawiki` server will appear in your MCP servers list
3. Claude will use the wiki tools automatically when relevant

---

## Connecting to Claude Code (CLI)

The `.mcp.json` file in the project root is already configured with relative paths. Simply launch Claude Code from this directory:

```bash
cd mediawiki-mcp
claude
```

The `mediawiki` tools will be available immediately.

---

## Typical edit workflow

For safe edits, follow this sequence:

1. `wiki_search` — find the page
2. `wiki_get_sections` — see the section layout and indices
3. `wiki_get_page` — read the current wikitext
4. `wiki_section_edit` — update only the section that needs changing

Use `wiki_edit_page` only when creating a new page or doing a full rewrite.

---

## Security notes

- `.env` is in `.gitignore` — never commit credentials
- `WIKI_TOTP_SECRET` is sensitive — treat it the same as a password
- All edits are recorded in MediaWiki history under your account name, giving a full audit trail
