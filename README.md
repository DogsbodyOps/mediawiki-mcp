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

# Optional — comma-separated MediaWiki category names to stamp on every page write
# WIKI_MANDATORY_CATEGORIES=MCP Edited,Infrastructure
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

# Optional — comma-separated MediaWiki category names to stamp on every page write
# WIKI_MANDATORY_CATEGORIES=MCP Edited,Infrastructure
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

## Releasing a new version

Releases are driven by a `releases/vX.Y.Z` branch. Pushing one triggers the Azure Pipeline, which:

1. Builds and pushes `ghcr.io/dogsbodyops/mediawiki-mcp:vX.Y.Z` to GHCR
2. Opens a PR from the release branch back to `main` automatically

```bash
git checkout main
git pull
git checkout -b releases/v1.3.3
git push origin releases/v1.3.3
```

Once the pipeline completes, review and merge the PR. Then update `docker-compose.yml` to reference the new image tag and redeploy.

> **Prerequisite:** A `GITHUB_PAT` secret must be configured in Azure Pipelines (Pipeline → Edit → Variables) with a GitHub token (classic) that has **Pull requests: Read and write** access on this repo.

---

## Container signing & SBOM (Azure Container Registry)

Sign the image with [Notation](https://notaryproject.dev/) using an Azure Key Vault key, then attach a signed SBOM as an OCI referrer. Both are stored alongside the image in ACR and verifiable without a sidecar.

### Prerequisites

| Tool | Install |
|------|---------|
| `az` CLI | `winget install Microsoft.AzureCLI` / [docs](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) |
| `notation` | `brew install notation` / [releases](https://github.com/notaryproject/notation/releases) |
| notation-azure-kv plugin | see step 2 |
| `syft` | `brew install syft` / [releases](https://github.com/anchore/syft/releases) |
| `oras` | `brew install oras` / [releases](https://oras.land/docs/installation) |

### 1. One-time: create the signing key in Azure Key Vault

```bash
# Variables — set these for your environment
ACR=yourregistry.azurecr.io
KV=your-keyvault-name
KEY_NAME=mediawiki-mcp-signing
CERT_NAME=mediawiki-mcp-signing-cert

az login
az acr login --name "${ACR%%.*}"

# Create a key-backed self-signed certificate (RSA-4096 is fine for internal use)
az keyvault certificate create \
  --vault-name "$KV" \
  --name "$CERT_NAME" \
  --policy "$(az keyvault certificate get-default-policy)"
```

For production, replace the default policy with one that sets `keyUsage: ["digitalSignature"]` and uses your CA. See [AKV notation docs](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-tutorial-sign-build-push).

### 2. Install the notation Azure Key Vault plugin

```bash
notation plugin install \
  --url https://github.com/Azure/notation-azure-kv/releases/download/v1.2.0/notation-azure-kv_1.2.0_linux_amd64.tar.gz \
  --checksum <sha256-from-release-page>

# Add the key to notation's local config
KV_CERT_ID=$(az keyvault certificate show \
  --vault-name "$KV" --name "$CERT_NAME" \
  --query "id" -o tsv)

notation key add --plugin azure-kv \
  --id "$KV_CERT_ID" \
  "$KEY_NAME"
```

### 3. Sign the image

```bash
IMAGE="$ACR/mediawiki-mcp:v1.3.2"
DIGEST=$(az acr manifest show-metadata \
  --registry "${ACR%%.*}" \
  --name "mediawiki-mcp:v1.3.2" \
  --query "digest" -o tsv)

notation sign \
  --key "$KEY_NAME" \
  --signature-format cose \
  "$ACR/mediawiki-mcp@$DIGEST"
```

Verify immediately:

```bash
# Add the trust policy (once)
cat > trustpolicy.json <<'EOF'
{
  "version": "1.0",
  "trustPolicies": [{
    "name": "mediawiki-mcp",
    "registryScopes": ["yourregistry.azurecr.io/mediawiki-mcp"],
    "signatureVerification": {"level": "strict"},
    "trustStores": ["ca:mediawiki-mcp"],
    "trustedIdentities": ["x509.subject: CN=mediawiki-mcp-signing-cert"]
  }]
}
EOF
notation policy import trustpolicy.json

notation verify "$ACR/mediawiki-mcp@$DIGEST"
```

### 4. Generate and attach a signed SBOM

```bash
IMAGE_REF="$ACR/mediawiki-mcp@$DIGEST"

# Generate SBOM in SPDX JSON format
syft "$IMAGE_REF" -o spdx-json > mediawiki-mcp.spdx.json

# Push it as an OCI referrer (stored in the same repo, linked to the image digest)
oras attach \
  --artifact-type "application/spdx+json" \
  "$IMAGE_REF" \
  ./mediawiki-mcp.spdx.json:application/spdx+json

# Retrieve the SBOM referrer digest so we can sign it too
SBOM_DIGEST=$(oras discover \
  --artifact-type "application/spdx+json" \
  --format json \
  "$IMAGE_REF" | jq -r '.manifests[0].digest')

# Sign the SBOM referrer with the same key
notation sign \
  --key "$KEY_NAME" \
  --signature-format cose \
  "$ACR/mediawiki-mcp@$SBOM_DIGEST"
```

### 5. Verify the full chain

```bash
# List all referrers (signature + SBOM + SBOM signature)
oras discover --format tree "$IMAGE_REF"

# Verify image signature
notation verify "$IMAGE_REF"

# Pull and inspect the SBOM
oras pull "$ACR/mediawiki-mcp@$SBOM_DIGEST" -o ./sbom-out/
```

### Adding to the Azure Pipeline

Add these steps to [azure-pipelines.yml](azure-pipelines.yml) after the Docker build/push task. Requires:
- `AZURE_KV_NAME` pipeline variable (name of your Key Vault)
- `SIGNING_KEY_NAME` pipeline variable
- The pipeline's managed identity must have **Key Vault Crypto User** on the vault and **AcrPush** on the registry

```yaml
- bash: |
    # Install tools
    curl -sLO https://github.com/notaryproject/notation/releases/download/v1.2.1/notation_1.2.1_linux_amd64.tar.gz
    tar xzf notation_*.tar.gz -C /usr/local/bin notation
    notation plugin install --url https://github.com/Azure/notation-azure-kv/releases/download/v1.2.0/notation-azure-kv_1.2.0_linux_amd64.tar.gz --checksum <sha256>
    curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh | sh -s -- -b /usr/local/bin
    curl -sLO https://github.com/oras-project/oras/releases/download/v1.2.0/oras_1.2.0_linux_amd64.tar.gz
    tar xzf oras_*.tar.gz -C /usr/local/bin oras

    az login --identity
    ACR="yourregistry.azurecr.io"
    az acr login --name "${ACR%%.*}"

    DIGEST=$(az acr manifest show-metadata \
      --registry "${ACR%%.*}" \
      --name "mediawiki-mcp:$(Build.SourceBranchName)" \
      --query "digest" -o tsv)
    IMAGE_REF="$ACR/mediawiki-mcp@$DIGEST"

    # Signing key (pre-registered in notation config or add inline)
    notation sign --key "$(SIGNING_KEY_NAME)" --signature-format cose "$IMAGE_REF"

    # SBOM
    syft "$IMAGE_REF" -o spdx-json > sbom.spdx.json
    oras attach --artifact-type "application/spdx+json" "$IMAGE_REF" ./sbom.spdx.json:application/spdx+json

    SBOM_DIGEST=$(oras discover --artifact-type "application/spdx+json" --format json "$IMAGE_REF" | jq -r '.manifests[0].digest')
    notation sign --key "$(SIGNING_KEY_NAME)" --signature-format cose "$ACR/mediawiki-mcp@$SBOM_DIGEST"
  displayName: Sign image and attach signed SBOM
  env:
    AZURE_KV_NAME: $(AZURE_KV_NAME)
    SIGNING_KEY_NAME: $(SIGNING_KEY_NAME)
```

### Notes

- Signatures and the SBOM referrer live in the same ACR repo — no extra storage to manage
- ACR supports the [OCI Referrers API](https://github.com/opencontainers/distribution-spec/blob/main/spec.md#listing-referrers) natively; no separate registry needed
- Use `oras discover` to enumerate all referrers before pulling an image in a deployment pipeline
- For compliance, pair with an Azure Policy that uses the [AKS image integrity add-on](https://learn.microsoft.com/en-us/azure/aks/image-integrity) to enforce signature verification at admission

---

## Typical edit workflow

1. `wiki_search` — find the page
2. `wiki_get_sections` — see the section layout and indices
3. `wiki_get_page` — read the current wikitext
4. `wiki_section_edit` — update only the section that needs changing

Use `wiki_edit_page` only when creating a new page or doing a full rewrite.

---

## Mandatory page categories

Set `WIKI_MANDATORY_CATEGORIES` to a comma-separated list of MediaWiki category names. Every page that Claude **creates or overwrites** (`wiki_edit_page`, `wiki_append_to_page`) will have these categories appended automatically — skipped if they're already present, so repeated writes don't duplicate them.

```env
WIKI_MANDATORY_CATEGORIES=MCP Edited,Infrastructure,Automated
```

This produces wikitext at the bottom of each saved page:

```
[[Category:MCP Edited]]
[[Category:Infrastructure]]
[[Category:Automated]]
```

**`wiki_section_edit` is intentionally excluded** — it only owns a single section's wikitext, not the full page, so injecting categories there would put them in the wrong place. If you need categories on a page that's only ever touched via section edits, add them manually once and they'll persist.

---

## Security notes

- `.env` is in `.gitignore` — never commit credentials
- `WIKI_TOTP_SECRET` and `MCP_API_KEY` are sensitive — treat them as passwords
- Write tools (`wiki_edit_page`, `wiki_section_edit`, `wiki_append_to_page`) are hidden from Claude unless `WIKI_ALLOW_WRITE=true` — defaults to read-only for safety
- All edits are recorded in MediaWiki history under your account name, giving a full audit trail
- If `MCP_API_KEY` is not set, the HTTP server accepts all requests — only do this on a private network

## Use Cases

  Lookup & Research
  - "Find the static IP for host stg-app14" — search then read the relevant IP page
  - "What's the HAProxy config for the DTS environment?" — search + get sections
  - "Summarize everything we have documented on ESX performance tuning"

  Keeping Docs Up to Date
  - "We decommissioned BE-Build5 — find every page that mentions it and update them"
  - "Add stg-app14 with IP 192.168.x.x to the Static IP Addresses page"
  - "Append a post-mortem summary to the incident page for last night's outage"

  Onboarding / Knowledge Retrieval
  - "What hosts are in the DTS environment?" — search + read + summarize
  - "Give me a runbook for setting up a new QA server based on the wiki"

  Auditing & Cross-Referencing
  - "List all pages and flag any that haven't been updated recently" (via list + get)
  - "Find all pages referencing a specific VLAN or subnet"

  Assisted Authoring
  - "Draft a new wiki page for stg-app14 based on similar host pages" — read a template page, then create a new one
