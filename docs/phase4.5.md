# Phase 4.5: Procedural Methodology Ingestion

## Overview
Phase 4.5 introduces support for **Procedural Methodologies** alongside existing Sigma rules. While Sigma rules are used to express detection logic in an engine-agnostic way, procedural methodologies provide step-by-step guidance for manual investigation and triage.

Procedural methodologies are written as YAML files with the extension `.procedure.yml` and follow a strict structure. Like Sigma rules, they are version-controlled in GitLab and ingested into the Cairn Blackboard via ChromaDB for semantic search.

## Authoring Procedures

Procedures must adhere to the `ProcedureMethodology` schema:
```yaml
title: "Phishing Triage Procedure"
author: "analyst-1"
created_at: "2026-04-24T00:00:00Z"
version: "1.0"
tags: ["phishing", "triage", "investigation"]
summary: "Standard procedure for analyzing reported phishing emails."
steps:
  - "Extract email headers and check sender reputation."
  - "Identify and detonate suspicious attachments in a sandbox."
  - "Extract URLs and analyze them via Threat Intelligence feeds."
```

### Step Format Tips
- Use imperative language for steps.
- Steps should ideally be complete sentences, as they may be parsed using NLP models like spaCy for entity extraction and step segmentation.
- Steps shorter than 10 characters are filtered out during promotion or ingestion, so ensure steps carry meaningful instruction.

## Endpoints

1. **Methodology Search Endpoint** (`GET /methodologies/search`)
   - Added `kind` parameter which can be `sigma`, `procedure`, or `any`.
   - Returns both methodologies along with their type.

2. **Promotion Endpoint** (`POST /promotions/{id}/promote`)
   - Supports a `methodology_kind` field. If set to `procedure`, the extracted narrative is parsed into distinct procedural steps rather than being stored as a raw narrative blob.

## Route A vs Route C

- **Route A**: Creating a `.procedure.yml` file and committing it directly to GitLab. The standard methodology sync job picks this up and adds it to ChromaDB.
- **Route C**: A procedural methodology generated dynamically from a sequence of agent findings on the blackboard, flagged for promotion by an analyst. The steps are extracted from the finding's narrative and written to the Obsidian vault under `cairn/procedures/`.

## Optional spaCy Gate

To improve the segmentation of steps during Route C promotion, the `cairn` service supports an optional dependency on `spaCy`. Set `CAIRN_SPACY_ENABLED=true` in `.env` to enable it. When enabled, Cairn will use spaCy to split step text more robustly than standard regex heuristics.

