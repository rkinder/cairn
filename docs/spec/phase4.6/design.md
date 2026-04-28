# Phase 4.6 Quartz Migration Design

## Architecture Overview
### Components
The Tier 2 Knowledge Base writer module (`cairn/vault/writer.py`) will be refactored into `cairn/kb/writer.py` (or aliased for backward compatibility). The logic for generating Obsidian-flavored Markdown remains intact, but the environment variables, terminology, and post-write hooks change.

```
[Blackboard API] 
      | POST /promotions/{id}/promote
      v
[Route C Logic] 
      |
      v
[cairn/kb/writer.py] ---> (Writes Markdown to CAIRN_QUARTZ_CONTENT_DIR)
      |
      +-----> (Adds task to asyncio.Queue)
                   |
                   v
        [Background Sync Worker Task] -> (Sequentially spawns subprocess: `npx quartz sync`)
```

## Core Classes

### `cairn.kb.writer` (formerly `cairn.vault.writer`)
```python
import os
import logging
from cairn.kb.sync_worker import get_sync_queue

logger = logging.getLogger(__name__)

class KBWriter:
    def write_note(self, content: str, path: str):
        # Write to CAIRN_QUARTZ_CONTENT_DIR
        ...
        queue = get_sync_queue()
        if queue:
            queue.put_nowait(True) # Just a signal to run a sync cycle
```

### `cairn.kb.sync_worker`
```python
import asyncio
import os
import logging

logger = logging.getLogger(__name__)
_sync_queue = None

def get_sync_queue() -> asyncio.Queue:
    global _sync_queue
    if _sync_queue is None:
        _sync_queue = asyncio.Queue()
    return _sync_queue

async def quartz_sync_worker():
    queue = get_sync_queue()
    while True:
        try:
            # Wait for a sync signal
            await queue.get()
            
            # Debounce: if multiple signals piled up, clear them out
            while not queue.empty():
                queue.get_nowait()
                queue.task_done()
                
            cmd = os.getenv("CAIRN_QUARTZ_SYNC_CMD")
            cwd = os.getenv("CAIRN_QUARTZ_CONTENT_DIR", ".")
            if not cmd:
                queue.task_done()
                continue
                
            logger.info("Starting sequential Quartz sync...")
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()
            
            if proc.returncode != 0:
                logger.error(f"Quartz sync failed with code {proc.returncode}")
            else:
                logger.info("Quartz sync completed successfully.")
                
            queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Exception in sync worker: {e}")
```

## Database Schema Changes
### Migration: Rename `vault_path` to `kb_path`
```sql
-- Create in cairn/db/schema/migrations/005_rename_vault_path.sql
-- We must rename it in promotion_candidates and messages

ALTER TABLE promotion_candidates RENAME COLUMN vault_path TO kb_path;
ALTER TABLE messages RENAME COLUMN vault_path TO kb_path;
```

## Integration Points

### Environment Configuration
```python
# In cairn/core/settings.py
class Settings(BaseSettings):
    quartz_content_dir: str = Field(
        default="./kb_content", 
        alias="CAIRN_QUARTZ_CONTENT_DIR"
    )
    quartz_sync_cmd: Optional[str] = Field(
        default=None, 
        alias="CAIRN_QUARTZ_SYNC_CMD"
    )
```

## Future Documentation Structure (`ROADMAP.md`)

```markdown
# Cairn Future Enhancements (Phase 5+)

## Enterprise Scale & Hardening (Phase 5)
- PostgreSQL Migration for Tier 1 `index.db`
- Namespace Partitioning for organizational units
- Advanced Authentication (JWT/mTLS for Agents)
- OpenTelemetry & Distributed Tracing

## Multi-Agent Collaboration
- Real-time SSE Peer Review Agents
- Methodology Feedback Scoring
- Cross-Domain Graph Traversal API

## Human-Analyst Tools
- TUI (Terminal UI) for live blackboard tailing
- ChatOps integrations (Slack/Teams Webhooks)
- SOAR playbook exports
```

## Migration Strategy

### Phase 1: Configuration & Database
1. Update `Settings` class to support `quartz_content_dir`.
2. Deprecate `CAIRN_VAULT_DIR` and log a warning if mapped.
3. Write `005_rename_vault_path.sql` and update Python data models (`cairn/models/`).

### Phase 2: Add Async Queue Worker
1. Implement `cairn.kb.sync_worker`.
2. Start the worker when the FastAPI application boots up (`@app.on_event("startup")` or Lifespan context manager).
3. Connect the signal in `cairn.kb.writer`.

### Phase 3: Documentation
1. Create `ROADMAP.md` tracking Phase 5 enhancements.
2. Update `README.md` and `CLAUDE.md` to reflect the transition from Obsidian to Quartz.

## Testing Strategy
### Property-Based Tests
- **WHEN** multiple sync requests are placed in the queue simultaneously, **THE system SHALL** execute the subprocess command exactly once due to debouncing.
- **IF** the subprocess returns a non-zero exit code, **THE system SHALL** log the error but keep the worker loop alive for future requests.
