# Sigma Rule CI/CD Pipeline

Detection methodologies in Cairn are written in [Sigma](https://github.com/SigmaHQ/sigma) format
and live in a dedicated GitLab repository.  Every push to that repository runs a CI pipeline that
validates all rules before they can reach a `validated` state.

---

## Why Sigma

Sigma is an open, vendor-neutral rule format for SIEM detections.  A single `.yml` rule compiles
to CrowdStrike NG-SIEM queries, Splunk SPL, Elastic DSL, and others via `sigma-cli`.  This gives
the methodology repo cross-platform portability and makes CI validation trivial — if `sigma check`
passes, the detection logic is structurally correct for all target backends.

---

## Repository layout

```
methodology-repo/
├── .gitlab-ci.yml           # Includes the Cairn sigma-validate template
├── sigma/
│   ├── lateral-movement/
│   │   ├── named-pipe-cobalt-strike.yml
│   │   └── wmi-lateral-tool-transfer.yml
│   ├── persistence/
│   │   └── registry-run-key.yml
│   └── ...
└── methodologies/           # Non-Sigma methodology files (playbooks, etc.)
    └── ...
```

All `.yml` files under `sigma/` are validated on every push and merge request.
Files under `methodologies/` are synced to ChromaDB for semantic discovery (no validation).

---

## Setting up the pipeline

### 1. Create the methodology repository in GitLab

In your GitLab instance, create a project (e.g. `security-team/methodologies`).

### 2. Add the pipeline configuration

Copy the Cairn pipeline template into the repo:

```bash
# In the methodology repo root:
mkdir -p .gitlab-ci-includes
cp /path/to/cairn/gitlab-ci/sigma-validate.yml .gitlab-ci-includes/

cat > .gitlab-ci.yml <<'EOF'
include:
  - local: '.gitlab-ci-includes/sigma-validate.yml'
EOF
```

Or reference it from the Cairn project directly (if both repos are in the same GitLab instance):

```yaml
# .gitlab-ci.yml
include:
  - project: 'security-team/cairn'
    ref: master
    file: 'gitlab-ci/sigma-validate.yml'
```

### 3. Install sigma-cli locally for development

```bash
pip install sigma-cli

# Validate a single rule:
sigma check sigma/lateral-movement/named-pipe-cobalt-strike.yml

# Compile to a specific backend:
sigma convert -t splunk sigma/lateral-movement/named-pipe-cobalt-strike.yml
```

### 4. Configure the GitLab webhook in Cairn

So that Cairn's ChromaDB collection stays in sync with the methodology repo:

1. In the methodology GitLab project: **Settings → Webhooks**
2. Add a webhook:
   - **URL**: `http://<cairn-host>/webhooks/gitlab`
   - **Secret token**: the value of `CAIRN_GITLAB_WEBHOOK_SECRET`
   - **Trigger**: Push events
3. Set the same secret in Cairn's environment: `CAIRN_GITLAB_WEBHOOK_SECRET=<value>`

---

## Writing a Sigma rule

Minimum required fields for a valid Sigma rule:

```yaml
title: Named Pipe Matching Cobalt Strike Default Configuration
name: cobalt-strike-named-pipe-default  # Used as methodology_id in Cairn
status: experimental                    # proposed | experimental | test | stable | deprecated
description: >
  Detects named pipe creation events matching the default Cobalt Strike
  configuration. The pipe name follows the pattern \.\pipe\msagent_<hex>.
author: security-team
date: 2026-04-15
tags:
  - attack.lateral-movement
  - attack.t1021
  - cobalt-strike
logsource:
  category: pipe_created
  product: windows
detection:
  selection:
    PipeName|contains: 'msagent_'
  condition: selection
falsepositives:
  - Legitimate MSAgent processes (rare)
level: high
```

The `name` field is used as the `methodology_id` in Cairn's execution records.
If `name` is absent, Cairn derives an ID from the file path.

---

## Validation state machine

When an agent runs a Sigma rule and posts results to the blackboard, it creates an
execution record via `POST /methodologies/executions` with status `proposed`.

```
proposed → peer_reviewed   Agent B executes the same rule and confirms results
peer_reviewed → validated  Human analyst reviews and sets X-Human-Reviewer: true
any → deprecated           Human analyst retires a superseded rule
```

The `sigma-cli` CI pipeline ensures the rule is structurally correct before it is
ever committed. Human review (`validated`) adds the semantic check that the rule
detects what it claims to detect.

---

## Compiling to a target backend

```bash
# List available backends:
sigma list targets

# Convert to CrowdStrike NG-SIEM:
sigma convert -t crowdstrike sigma/lateral-movement/named-pipe-cobalt-strike.yml

# Convert all rules in a directory to Splunk SPL:
find sigma/ -name "*.yml" | xargs sigma convert -t splunk
```

Enable the optional `sigma-compile` job in the pipeline by setting the
`SIGMA_BACKEND` CI/CD variable in GitLab project settings.
