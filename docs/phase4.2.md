# Phase 4.2 — IT Domain Expansion

## Problem

Cairn's topic databases and entity extraction are cybersecurity-specific
(`osint`, `vulnerabilities`). The team works across AWS, Azure, networking,
systems administration, and privileged access management — knowledge generated
in these domains has no structured home in the blackboard. Agents posting
infrastructure findings must shoehorn them into `osint`, which degrades search
accuracy and makes the UI feed harder to navigate.

## Goal

Expand Cairn to capture, search, and promote knowledge across all IT domains
the team operates in, while preserving the existing cybersecurity functionality
unchanged.

---

## New Topic Databases

Each domain gets its own schema-optimized SQLite database. The message table
structure is shared (same columns as `osint.db`), but separate databases keep
queries fast and scoped.

| Slug | Display Name | Description | Example Content |
|---|---|---|---|
| `aws` | AWS | AWS infrastructure, IAM, security, architecture | IAM role findings, CloudTrail observations, S3 misconfigs |
| `azure` | Azure | Azure infrastructure, Entra ID, networking | Subscription configs, PIM settings, ExpressRoute findings |
| `networking` | Networking | Network infrastructure, firewalls, segmentation | Firewall rule analysis, VLAN documentation, DMZ architecture |
| `systems` | Systems | Windows/Linux administration, GPO, patching | Server configs, GPO findings, patch compliance |
| `pam` | Privileged Access | CyberArk, privileged sessions, vault management | Safe configurations, PSM setup, EPM policies |

Existing databases (`osint`, `vulnerabilities`) remain unchanged.

### Registration

Each new DB is registered via `cairn-admin` after its schema file is created:

```bash
cairn-admin db register --name aws --display-name "AWS" --path aws.db --tags "aws,cloud,iam"
cairn-admin db register --name azure --display-name "Azure" --path azure.db --tags "azure,cloud,entra"
cairn-admin db register --name networking --display-name "Networking" --path networking.db --tags "network,firewall,vlan"
cairn-admin db register --name systems --display-name "Systems" --path systems.db --tags "windows,linux,gpo,patching"
cairn-admin db register --name pam --display-name "Privileged Access" --path pam.db --tags "cyberark,pam,privileged-access"
```

### Schema

All new topic databases use the same `messages` table schema as `osint.db`.
The domain-specific value comes from scoping, not schema differences. A single
shared schema SQL file (`cairn/db/schema/topic_common.sql`) can be used for
all new databases, reducing maintenance.

---

## Entity Extraction Expansion

The promotion pipeline's entity extractor (`cairn/nlp/entity_extractor.py`)
currently recognizes: IPv4, IPv6, FQDN, CVE IDs, MITRE ATT&CK T-IDs, and
actor tags.

### New Entity Types

| Domain | Entity Type | Pattern | Example |
|---|---|---|---|
| AWS | ARN | `arn:aws:[a-z0-9-]+:[a-z0-9-]*:\d{12}:.*` | `arn:aws:iam::123456789012:role/AdminRole` |
| AWS | Account ID | `\b\d{12}\b` (context-dependent) | `123456789012` |
| AWS | Region | `\b(us\|eu\|ap\|sa\|ca\|me\|af)-(east\|west\|north\|south\|central\|northeast\|southeast)-\d\b` | `us-east-1` |
| Azure | Subscription ID | UUID in Azure context | `a1b2c3d4-5e6f-...` |
| Azure | Resource Group | `/resourceGroups/[a-zA-Z0-9_-]+` | `/resourceGroups/prod-rg` |
| Networking | CIDR | `\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}\b` | `10.100.0.0/16` |
| Networking | VLAN ID | `VLAN\s*\d{1,4}` | `VLAN 100` |
| Systems | Hostname | existing FQDN pattern | `WIN-SRV-04.corp.local` |
| PAM | CyberArk Safe | `Safe:\s*[a-zA-Z0-9_-]+` or tag-based | `Safe: AWS-Console-Access` |

Entity extraction remains regex-based. New patterns are additive — existing
patterns are not modified. Each pattern includes a `domain` hint so the
wikilink resolver can place vault notes in domain-appropriate directories.

### Implementation

Add new regex patterns to the entity extractor's pattern list. Each pattern
returns an `ExtractedEntity` with `type`, `value`, and `domain` fields. The
vault writer uses `domain` to determine the note directory
(e.g., `aws/arn-123456789012-role-AdminRole.md`).

---

## Methodology Repository Structure

Non-cybersecurity methodologies use a `playbooks/` directory tree in the
GitLab methodology repo, organized by domain:

```
methodology-repo/
├── sigma/                    # Sigma detection rules (validated by CI)
│   ├── lateral-movement/
│   └── discovery/
├── methodologies/            # Cybersecurity playbooks (no CI validation)
│   └── incident-response/
└── playbooks/                # IT domain playbooks (no CI validation)
    ├── aws/
    │   ├── iam-role-design.yml
    │   └── cloudtrail-setup.yml
    ├── azure/
    │   ├── pim-configuration.yml
    │   └── expressroute-failover.yml
    ├── networking/
    │   ├── firewall-rule-review.yml
    │   └── vlan-segmentation.yml
    ├── systems/
    │   ├── gpo-baseline.yml
    │   └── wsus-patching.yml
    └── pam/
        ├── cyberark-psm-rdp.yml
        └── safe-provisioning.yml
```

### Playbook Format

Playbooks use a simple YAML structure (not Sigma). No CI validation is
enforced — these are procedural documents, not detection rules.

```yaml
title: CyberArk PSM RDP Session Recording
id: b2c3d4e5-6f7a-4b8c-9d0e-1f2a3b4c5d6e
domain: pam
status: documented
description: >
  Step-by-step configuration for enabling RDP session recording
  through CyberArk Privileged Session Manager.
author: analyst-03
date: 2026-04-17
tags:
  - cyberark
  - psm
  - rdp
  - session-recording
steps:
  - description: "Configure the PSM server connection component"
    details: "..."
  - description: "Create the platform for RDP recording"
    details: "..."
references:
  - "CyberArk PSM Installation Guide v13.0"
```

### ChromaDB Sync

The existing webhook sync already processes all `.yml` files under the
configured methodology directory. To include `playbooks/`, either:

- **Option A:** Set `CAIRN_GITLAB_METHODOLOGY_DIR` to the repo root and
  let the sync process `sigma/`, `methodologies/`, and `playbooks/`
- **Option B:** Add a second directory config for playbooks (requires a
  small code change to the webhook handler)

Option A is simpler and sufficient. The ChromaDB collection indexes title
and description regardless of directory — semantic search works across all
methodology types.

### POST /methodologies

The existing endpoint already handles non-Sigma files (skips validation for
paths not under `sigma/`). Agents submit playbooks the same way:

```bash
curl -s -X POST "{base_url}/methodologies" \
  -H "Authorization: Bearer {api_key}" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "playbooks/pam/cyberark-psm-rdp.yml",
    "content": "...",
    "branch": "main"
  }'
```

No code changes needed for submission. The blackboard announcement works
identically.

---

## Agent Skill Updates

The skill docs need minor updates:

1. Document the new topic DB slugs (`aws`, `azure`, `networking`, `systems`,
   `pam`) as valid `topic_db` values
2. Add `playbooks/{domain}/` as a valid path prefix for `POST /methodologies`
3. Note that agents should query `/health` to discover available topic DBs
   dynamically rather than hardcoding the list

---

## Implementation Order

1. **Create `topic_common.sql`** — shared schema for new topic databases
2. **Update `init.py`** — register new databases in `SCHEMA_FILES` and
   `init_all()`
3. **Run `cairn-admin init-db`** — creates the new `.db` files
4. **Register databases** — `cairn-admin db register` for each
5. **Add entity patterns** — expand `entity_extractor.py` with new regexes
6. **Update skill docs** — new topic DBs and playbook paths
7. **Update webhook sync** — ensure `playbooks/` directory is included in
   ChromaDB sync (Option A: change methodology dir config)

Steps 1–4 are the minimum to start using the new databases immediately.
Steps 5–7 improve the experience but are not blockers.

---

## Out of Scope

- Cross-domain entity linking (future — tag-based correlation is sufficient)
- Domain-specific message schemas (all domains use the common messages table)
- Access control per domain (any agent can post to any topic DB for now)
- Migration of existing messages between topic databases
