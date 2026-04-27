import asyncio
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_sync_queue: Optional[asyncio.Queue] = None

def get_sync_queue() -> asyncio.Queue:
    global _sync_queue
    if _sync_queue is None:
        _sync_queue = asyncio.Queue()
    return _sync_queue

async def quartz_sync_worker():
    """Background worker that sequentially executes the Quartz sync command.
    
    Uses a debounce strategy: if multiple sync requests are queued rapidly,
    they are collapsed into a single execution to avoid Git index.lock errors.
    """
    queue = get_sync_queue()
    logger.info("Quartz background sync worker started.")
    
    while True:
        try:
            # Wait for at least one sync request
            await queue.get()
            
            # Debounce: drain the queue of any other pending requests
            while not queue.empty():
                queue.get_nowait()
                queue.task_done()
                
            cmd = os.getenv("CAIRN_QUARTZ_SYNC_CMD")
            cwd = os.getenv("CAIRN_QUARTZ_CONTENT_DIR", ".")
            
            if not cmd:
                logger.warning("Sync requested but CAIRN_QUARTZ_SYNC_CMD is not set. Skipping.")
                queue.task_done()
                continue
                
            logger.info("Executing Quartz sync command: '%s' in '%s'", cmd, cwd)
            
            # Non-blocking subprocess execution
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()
            
            if proc.returncode != 0:
                logger.error("Quartz sync command failed with exit code %s", proc.returncode)
            else:
                logger.info("Quartz sync completed successfully.")
                
            queue.task_done()
            
        except asyncio.CancelledError:
            logger.info("Quartz background sync worker shutting down.")
            break
        except Exception as exc:
            logger.exception("Exception in Quartz sync worker: %s", exc)
