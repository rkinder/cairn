# Quartz 4 Knowledge Base — Setup Guide

This guide covers standing up the Quartz 4 static site generator as the
Tier 2 Knowledge Base for Cairn. Quartz replaces Obsidian as the KB viewer,
providing web-native access to promoted intelligence without requiring
analysts to install a desktop application.

All build tooling runs inside containers. No Node.js installation is
required on the host.

---

## Prerequisites

- Docker and Docker Compose on the deployment host
- A running Cairn stack (cairn-api, chromadb)
- One available host port for the KB web server

---

## Step 1: Create the project directory

```bash
mkdir -p /opt/quartz-kb
```

## Step 2: Clone and install Quartz

```bash
docker run --rm -v /opt/quartz-kb:/app -w /app node:22-slim bash -c \
  "apt-get update && apt-get install -y git && \
   git clone https://github.com/jackyzha0/quartz.git . && npm install"
```

This clones the Quartz project and installs dependencies inside a
disposable container. The project files persist on the host via the
bind mount.

## Step 3: Configure Quartz

Edit `quartz.config.ts` in the project directory:

```bash
vi /opt/quartz-kb/quartz.config.ts
```

Key settings to change:

```typescript
const config: QuartzConfig = {
  configuration: {
    pageTitle: "Cairn Knowledge Base",
    // Set baseUrl to your host and port, e.g.:
    //   "kb.example.com" (if behind a reverse proxy)
    //   "host.example.com:8003" (if accessed directly on a port)
    baseUrl: "<your-kb-host>",
  },
  // ...
}
```

The `baseUrl` must match how analysts will access the site in their
browser. If serving on a non-standard port without a reverse proxy,
include the port.

## Step 4: Create initial content

```bash
cat > /opt/quartz-kb/content/index.md << 'EOF'
---
title: Cairn Knowledge Base
---

Curated intelligence from the Cairn blackboard. Findings promoted here
have been reviewed and validated by the cybersecurity team.
EOF
```

## Step 5: Build the static site

```bash
docker run --rm -v /opt/quartz-kb:/app -w /app node:22-slim npx quartz build
```

Generates static HTML into `/opt/quartz-kb/public/`. Verify the directory
is populated:

```bash
ls /opt/quartz-kb/public/
```

## Step 6: Add the web server to docker-compose

Add to the Cairn stack's `docker-compose.yml`:

```yaml
  quartz-server:
    image: nginx:stable-alpine
    container_name: cairn-kb
    ports:
      - "${CAIRN_KB_PORT:-8003}:80"
    volumes:
      - /opt/quartz-kb/public:/usr/share/nginx/html:ro
    restart: unless-stopped
```

The `CAIRN_KB_PORT` variable can be set in `.env` to override the default.

## Step 7: Deploy and verify

Redeploy the stack (via Portainer or `docker compose up -d`). The KB
should be accessible at `http://<host>:<port>`.

## Step 8: Configure Cairn's sync worker

After merging Phase 4.6, add these environment variables to the cairn-api
service:

```yaml
  cairn-api:
    environment:
      # ... existing vars ...
      CAIRN_QUARTZ_CONTENT_DIR: /opt/quartz-kb/content
      CAIRN_QUARTZ_SYNC_CMD: "docker run --rm -v /opt/quartz-kb:/app -w /app node:22-slim npx quartz build"
```

The content directory is where Cairn writes promoted notes. The sync
command rebuilds the static site after each promotion. The sync worker
processes requests sequentially with debouncing to prevent concurrent
Git lock errors.

---

## Architecture

```
Cairn promotes a finding
    │
    ▼
Writes markdown to CAIRN_QUARTZ_CONTENT_DIR
    │
    ▼
Sync worker queues a build
    │
    ▼
docker run node:22-slim npx quartz build
    │
    ▼
Static HTML written to /opt/quartz-kb/public/
    │
    ▼
Nginx serves it to analysts' browsers
```

## What runs where

| Component | Location |
|---|---|
| Quartz project files (config, content) | Host filesystem (bind mount) |
| Node.js / npm / Quartz CLI | Inside `node:22-slim` container (disposable) |
| Built static HTML | Host filesystem (`public/` directory) |
| Web server | `nginx:stable-alpine` container |
| Node.js on the host | **Not installed** |

## Rebuilding after content changes

Any time content is added or modified outside of Cairn's sync worker
(e.g., manual edits), rebuild with:

```bash
docker run --rm -v /opt/quartz-kb:/app -w /app node:22-slim npx quartz build
```

Nginx serves the updated files immediately — no restart required.
