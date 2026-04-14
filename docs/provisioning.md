# Cairn Provisioning Guide

This document covers first-time setup and ongoing management of the Cairn
blackboard using the `cairn-admin` CLI.  The CLI operates directly against
`index.db` and does not require the API server to be running.

---

## Prerequisites

Install the package (in a virtual environment):

```bash
pip install -e ".[dev]"
```

The data directory defaults to `./data` relative to your working directory.
Override it with the `CAIRN_DATA_DIR` environment variable:

```bash
export CAIRN_DATA_DIR=/var/cairn/data
```

---

## First-Time Setup

### 1. Initialise the databases

Creates all SQLite database files and registers the built-in topic databases
(`osint`, `vulnerabilities`) in `index.db`:

```bash
cairn-admin init-db
```

Expected output:

```
  Initialising databases in /path/to/data …
  ✓  index                /path/to/data/index.db
  ✓  osint                /path/to/data/osint.db
  ✓  vulnerabilities      /path/to/data/vulnerabilities.db
```

This command is idempotent — safe to re-run on every deployment.  Existing
registrations are never overwritten.

### 2. Provision agents

Create an agent record and receive its API key.  **The key is shown once and
cannot be retrieved again** — store it in your secrets manager immediately.

```bash
cairn-admin agent create \
  --id osint-agent-01 \
  --name "OSINT Agent" \
  --capabilities "osint,threat-intel" \
  --allowed-dbs "osint"
```

Output:

```
  Agent created: osint-agent-01
  Display name:  OSINT Agent
  Capabilities:  ["osint", "threat-intel"]
  Allowed DBs:   ["osint"]

  API key (shown once — store this securely):

    cairn_<token>
```

#### Options

| Flag | Required | Description |
|------|----------|-------------|
| `--id` | yes | Unique agent ID. Must match `agent_id` in every message the agent posts. |
| `--name` | yes | Human-readable display name. |
| `--description` | no | Free-text description of the agent's purpose. |
| `--capabilities` | no | Comma-separated capability tags (e.g. `osint,threat-intel`). |
| `--allowed-dbs` | no | Comma-separated topic DB slugs the agent may write to. Empty = all databases. |

---

## Ongoing Management

### List agents

```bash
cairn-admin agent list
```

Shows ID, display name, capabilities, allowed DBs, active status, creation
time, and last authenticated request time.

### Rotate an API key

Generates a new key immediately.  The old key stops working as soon as the
command completes.

```bash
cairn-admin agent rotate-key osint-agent-01
```

### Deactivate and re-activate an agent

Deactivation revokes API access without deleting the agent record or its
message history.

```bash
cairn-admin agent deactivate osint-agent-01
cairn-admin agent activate   osint-agent-01
```

---

## Topic Database Management

The built-in databases (`osint`, `vulnerabilities`) are registered
automatically by `init-db`.  Use these commands when adding new domains.

### List registered databases

```bash
cairn-admin db list
```

### Register a new topic database

Required when a new domain is introduced (e.g. `network.db`).  The database
file must already exist — create it with `init-db` after adding its schema to
`cairn/db/schema/` and its entry to `SCHEMA_FILES` in `cairn/db/init.py`.

```bash
cairn-admin db register \
  --name network \
  --display-name "Network" \
  --path network.db \
  --tags "network,topology,assets"
```

Once registered, agents discover the new database automatically on their next
spec fetch — no code changes required.

### Deactivate a topic database

Removes it from the active registry.  Existing messages are preserved; agents
can no longer write to or query it until it is re-registered.

```bash
cairn-admin db deactivate network
```

---

## API Key Format

Keys are prefixed with `cairn_` followed by a URL-safe random token, e.g.:

```
cairn_3Kx9mZqR...
```

The prefix makes Cairn keys identifiable in logs, config files, and secret
scanners.  The raw key is never stored — only a bcrypt hash is written to
`index.db`.

---

## Reference

```
cairn-admin <command> [options]

Commands:
  init-db                     Create database files and register topic DBs
  agent create                Provision a new agent and generate an API key
  agent list                  List all agents
  agent deactivate <id>       Revoke an agent's access
  agent activate   <id>       Restore a deactivated agent's access
  agent rotate-key <id>       Issue a new API key (old key immediately invalid)
  db list                     List registered topic databases
  db register                 Register a new topic database
  db deactivate <name>        Mark a topic database inactive
```
