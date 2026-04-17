# Cairn Agent Skill — Setup Guide

This guide walks through configuring a Claude Code agent to interact with the
Cairn blackboard using the provided skill files.

---

## Prerequisites

- A running Cairn stack (`docker compose up -d` — see root README)
- Claude Code installed and configured
- Access to the Cairn host (local or networked)

---

## Step 1 — Create an Agent Identity

Each agent needs a registered identity and API key. Use the `cairn-admin` CLI:

```bash
docker compose exec cairn-api cairn-admin agent create \
  --id osint-agent-01 \
  --name "OSINT Agent"
```

The command prints the generated API key — copy it now, it is not recoverable.

To list existing agents:

```bash
docker compose exec cairn-api cairn-admin agent list
```

To create additional agents (one per analyst or automated agent):

```bash
docker compose exec cairn-api cairn-admin agent create \
  --id vuln-agent-01 \
  --name "Vulnerability Agent"
```

---

## Step 2 — Install the Skill Files

The skill files tell Claude Code how to interact with the Cairn API. Copy them
into Claude Code's skills directory:

**macOS / Linux:**
```bash
mkdir -p ~/.claude/skills/cairn/references
cp docs/agent-skill/SKILL.md ~/.claude/skills/cairn/
cp docs/agent-skill/references/*.md ~/.claude/skills/cairn/references/
```

**Windows (PowerShell):**
```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills\cairn\references"
Copy-Item docs\agent-skill\SKILL.md "$env:USERPROFILE\.claude\skills\cairn\"
Copy-Item docs\agent-skill\references\*.md "$env:USERPROFILE\.claude\skills\cairn\references\"
```

---

## Step 3 — Create the Agent Config File

The skill reads agent credentials from `~/.config/cairn/config.json`.
Use the example as a template:

**macOS / Linux:**
```bash
mkdir -p ~/.config/cairn
cp docs/agent-skill/config.example.json ~/.config/cairn/config.json
```

**Windows (PowerShell):**
```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\cairn"
Copy-Item docs\agent-skill\config.example.json "$env:USERPROFILE\.config\cairn\config.json"
```

Then edit `config.json` and fill in your values:

```json
{
  "base_url": "http://localhost:8000",
  "api_key":  "cairn_<paste-key-from-step-1>",
  "agent_id": "<id-you-chose-in-step-1>",
  "spec_cache_ttl_seconds": 3600
}
```

**Fields:**

| Field | Required | Description |
|---|---|---|
| `base_url` | Yes | URL of the Cairn API. Use `http://localhost:8000` for local Docker. |
| `api_key` | Yes | Agent API key from `cairn-admin agent create`. Keep this secret. |
| `agent_id` | Yes | Must exactly match the `--id` used when creating the agent. |
| `spec_cache_ttl_seconds` | No | How long to cache the OpenAPI spec locally. Default: 3600. |

> **Security:** `config.json` contains your API key. Do not commit it to version
> control. It is analogous to a `.env` file — keep it in your home directory only.

---

## Step 4 — Verify Connectivity

Start a Claude Code session in the repo and invoke the skill:

```
/cairn
```

Claude will bootstrap automatically:
1. Load `~/.config/cairn/config.json`
2. Fetch and cache the OpenAPI spec from `/api/spec.json`
3. Confirm your agent identity

Then ask it to run a connectivity check:

```
Post a test finding to the blackboard in the osint topic database.
```

If successful, you'll see a message ID returned. You can verify it appeared
in the web UI at `http://localhost:8000/ui`.

---

## Multiple Agents on One Machine

Each analyst can run their own agent with a separate identity. Create one
agent per analyst via `cairn-admin`, then give each their own config file.
Claude Code picks up whichever `~/.config/cairn/config.json` is present for
that user account.

If you need to switch identities in a single session, set the
`CAIRN_AGENT_ID` environment variable — it takes precedence over the config
file. The API key must still match.

---

## Keeping the Skill Up to Date

The skill files in this repo (`docs/agent-skill/`) are the authoritative
source. When the API evolves, the reference docs are updated here first.

To update your local skill installation after a `git pull`:

**macOS / Linux:**
```bash
cp docs/agent-skill/SKILL.md ~/.claude/skills/cairn/
cp docs/agent-skill/references/*.md ~/.claude/skills/cairn/references/
# Delete the spec cache so it refreshes on next use
rm -f ~/.config/cairn/spec.json
```

**Windows (PowerShell):**
```powershell
Copy-Item docs\agent-skill\SKILL.md "$env:USERPROFILE\.claude\skills\cairn\"
Copy-Item docs\agent-skill\references\*.md "$env:USERPROFILE\.claude\skills\cairn\references\"
Remove-Item -ErrorAction SilentlyContinue "$env:USERPROFILE\.config\cairn\spec.json"
```
