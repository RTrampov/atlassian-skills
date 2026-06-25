# atlassian-skills — CLAUDE.md

## Project Overview

A Python CLI + Claude Code Skill that lets LLM agents drive our internal Atlassian Server/DC stack (Jira, Confluence, Bitbucket, Bamboo) in a token-efficient way. Serves as a complete replacement for the `mcp-atlassian` MCP server.

- **Binary**: `atls`
- **Package**: `atlassian-skills`
- **Current version**: 0.2.8 (keyring + shell-command credential storage with per-product commands; keyring-only `atls setup` wizard; `atls auth status --resolve`; `atls doctor` PyPI freshness banner; legacy `setup all/codex/claude/paths/status` deprecated for removal in 0.3.0)

## Running from a local clone

```bash
cd ~/projects/atlassian-skills
uv tool install --editable .   # installs atls to ~/.local/bin, editable — source changes apply immediately
atls --help
```

### Configuration

Create `~/.config/atlassian-skills/config.toml`:

```toml
[profiles.default]
jira_url       = "https://jira.example.com"
confluence_url = "https://confluence.example.com"
bitbucket_url  = "https://bitbucket.example.com"

# env_file: path to a KEY=VALUE file loaded as fallback env on every invocation.
# Tokens and extra headers defined here are picked up in all contexts —
# interactive shells, scripts, and AI agent subprocesses — with no shell exports.
env_file = "~/.config/my-credentials"

# ca_bundle = "/path/to/ca.pem"   # optional: path to CA bundle, or false to skip TLS verify
```

### Credentials file

`env_file` points to any `KEY=VALUE` file (comments and blank lines are ignored).
Supported variables:

| Purpose | Accepted variable names (tried in order) |
|---------|------------------------------------------|
| Jira token | `ATLS_DEFAULT_JIRA_TOKEN`, `JIRA_PERSONAL_TOKEN`, `JIRA_API_TOKEN`, `JIRA_TOKEN` |
| Confluence token | `ATLS_DEFAULT_CONFLUENCE_TOKEN`, `CONFLUENCE_PERSONAL_TOKEN`, `CONFLUENCE_API_TOKEN`, `CONFLUENCE_TOKEN` |
| Bitbucket token | `ATLS_DEFAULT_BITBUCKET_TOKEN`, `BITBUCKET_TOKEN`, `BITBUCKET_API_TOKEN`, `BITBUCKET_PERSONAL_TOKEN` |
| Extra HTTP headers | `ATLS_DEFAULT_EXTRA_HEADERS` |

`ATLS_DEFAULT_EXTRA_HEADERS` accepts comma-separated `Header-Name=value` pairs. Values
containing `=` (JWTs, base64) are preserved — splitting is on the first `=` per pair:

```
ATLS_DEFAULT_EXTRA_HEADERS=X-My-Proxy-Token=eyJ...
```

Explicit env vars always override the file, so CI secrets and shell exports are never shadowed.

### Installing the Claude Code skill

**With a skill manager (skillshare or similar):**

```bash
cp -r src/atlassian_skills/_assets/skills/atls ~/.config/skillshare/skills/
skillshare sync
```

**Without a skill manager:**

```bash
atls setup   # interactive wizard — installs skill and injects routing block into ~/.claude/CLAUDE.md
```

After upstream updates, re-copy and re-sync (editable install picks up code changes; only the skill asset file needs manual re-copy).

### Verify

```bash
atls doctor
```

### Run checks before committing

```bash
uv run ruff check src/ tests/
uv run mypy src/
uv run pytest
```

## Build & Run

```bash
# Install (uv recommended)
uv sync

# Run the CLI
uv run atls --help
uv run atls jira issue get PROJ-1 --format=compact

# Tests
uv run pytest                          # unit + contract + snapshot
uv run pytest -m integration           # hit live internal instance (manual)
uv run pytest tests/benchmarks         # token benchmarks

# Lint & type check
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
```

## Project Structure

```
src/atlassian_skills/
├── cli/                    # Typer CLI (main.py = entry point, split by product)
├── core/
│   ├── client.py           # BaseClient (httpx, retry, auth, pagination)
│   ├── auth.py             # PAT/Basic, env > keyring > config
│   ├── config.py           # ~/.config/atlassian-skills/config.toml
│   ├── errors.py           # AtlasError, exit codes per §15.4
│   ├── pagination.py       # offset + link-follow patterns
│   └── format/             # compact, md, json, raw formatters
├── jira/
│   ├── client.py           # JiraClient(BaseClient)
│   ├── models.py           # pydantic response models
│   └── preprocessing.py    # mention / smart-link preprocessing (§5.1.1)
├── confluence/
│   ├── client.py
│   └── models.py
├── bitbucket/
│   ├── client.py           # BitbucketClient(BaseClient)
│   └── models.py           # pydantic response models (PR, Comment, Branch, Task, etc.)
└── bamboo/                 # planned

tests/
├── fixtures/               # MCP capture JSON (golden files)
├── unit/                   # respx mocking
├── contract/               # response ↔ pydantic models
├── snapshot/               # syrupy CLI output regression
└── benchmarks/             # tiktoken token measurements

src/atlassian_skills/_assets/skills/atls/SKILL.md   # canonical SKILL.md installed by `atls setup` for both Claude Code and Codex
docs/                       # design docs and analyses
```

## Coding Conventions

### Python style
- **Python 3.10+**, type hints required (`disallow_untyped_defs = true`)
- **ruff** formatter + linter (line-length=120)
- **pydantic v2** models (response parsing via `model_validate`)
- `from __future__ import annotations` at the top of every module
- Import order: stdlib → third-party → local (enforced by ruff isort)

### Naming
- Modules / variables: `snake_case`
- Classes: `PascalCase`
- CLI commands: `kebab-case` (Typer converts automatically)
- Constants: `UPPER_SNAKE_CASE`

### Error handling
- Wrap every API error in the `AtlasError` hierarchy
- Exit codes follow the §15.4 spec (0=OK, 2=not found, 6=auth, ...)
- With `--format=json`, errors are also emitted to stdout as a JSON envelope

### Tests
- Unit tests: mock httpx with `respx`, use fixtures/ JSON
- Snapshot tests: `syrupy` guards against CLI output regression
- Token benchmarks: `tiktoken` cl100k_base; the test fails if the budget is exceeded
- `@pytest.mark.integration` is skipped in CI; run manually

## Core Design Principles

1. **The CLI is the product** — the skill is a thin wrapper that calls it
2. **Token efficiency** — compact format is the default; ≥50% reduction at L1 vs MCP
3. **Single cfxmark dependency** — both Confluence XHTML and Jira wiki conversion go through cfxmark (>=0.4)
4. **Server/DC only** — Cloud compatibility is a non-goal
5. **Byte-preserving raw** — `--format=raw` does not alter a single byte of the server response

## Key Dependencies

| Package | Purpose |
|---|---|
| `httpx` | REST client (sync) |
| `typer` + `rich` | CLI framework |
| `pydantic` | Response models |
| `cfxmark>=0.4` | Confluence XHTML ↔ md, Jira wiki ↔ md |
| `platformdirs` | Config paths |

## Authentication (§7.1)

```bash
# Environment variables (default, recommended)
export ATLS_CORP_JIRA_TOKEN="your-pat"
export ATLS_CORP_CONFLUENCE_TOKEN="your-pat"
```

```toml
# ~/.config/atlassian-skills/config.toml

# Env vars (default)
[profiles.corp]
jira_url = "https://jira.corp.example.com"

# System keyring — uses platform native store (macOS Keychain, Windows Credential Manager, Linux Secret Service)
# The keyring package is a base dependency (bundled by default — no extra needed)
# Keyring entry: service="atls-<profile>", account="<product>_token"
[profiles.corp]
jira_url = "https://jira.corp.example.com"
storage = "keyring"

# Shell command — cross-platform; the command is whatever your OS/secret manager supports
# Examples: "op read op://vault/jira/token" (1Password, all platforms)
#           "security find-generic-password -s my-jira-token -w" (macOS)
#           "pass show jira/pat" (Linux)
#           "powershell -NoProfile -Command \"(Get-StoredCredential -Target jira-pat).GetNetworkCredential().Password\"" (Windows)
[profiles.corp]
jira_url = "https://jira.corp.example.com"
storage = "command"
credential_command = "op read op://vault/atlassian/token"      # shared across products
# jira_command / confluence_command / bitbucket_command override per product (optional)
```

Priority: CLI flag > env var > the profile's configured `storage` provider (keyring **or** command).
`storage` selects a single provider — it is not a fallback chain. `plaintext` is reserved (not yet implemented).

## Release Process

CI and PyPI publishing are automated via GitHub Actions — manual `uv build` / `twine upload` is no longer needed.

```bash
# 1. Add a ## [X.Y.Z] - YYYY-MM-DD section at the top of CHANGELOG.md
# 2. Bump pyproject.toml version = "X.Y.Z"
# 3. uv sync --all-extras  (refresh uv.lock)
# 4. Commit & push (CI verifies ruff + mypy + pytest on 3.10-3.13)
git commit -am "chore: release vX.Y.Z"
git push origin main

# 5. Push the tag → release.yml fires automatically
git tag vX.Y.Z && git push origin vX.Y.Z
```

**release.yml performs automatically**:
1. Verifies the tag version matches `pyproject.toml` version
2. Re-runs tests
3. `uv build` → PyPI publish (`PYPI_TOKEN` secret)
4. Extracts the matching CHANGELOG section and uses it as the GitHub Release body
5. Attaches wheel + sdist as Release assets

**Workflow files**: `.github/workflows/{ci,release}.yml`

## References

- `docs/DESIGN.md` — full design (§1-§16, appendices A-E)
- `docs/mcp-analysis.md` — MCP token-overhead analysis
- `docs/api-endpoint-mapping.md` — REST endpoint mapping (68 entries)
- `references/` — reference repos such as mcp-atlassian, cfxmark, atlassian-python-api (gitignored)
