# BUG 008: Quartz sync worker Queue bound to wrong event loop at test teardown

## Summary

Two integration test modules (`test_skill_client.py` and
`test_messages_delete_api.py`) fail during teardown with:

```
RuntimeError: <Queue at 0x...> is bound to a different event loop
```

The failure occurs inside `cairn/kb/sync_worker.py` during the ASGI
lifespan shutdown sequence. The test body passes; only teardown errors.
The timeout then fires because the `lifespan_task` cannot complete,
causing pytest-timeout to kill the test runner mid-traceback-formatting.

## Severity

**Low** — All test assertions pass. No production code is broken. The
error is cosmetic in CI output but is a real resource-leak in the test
process and will eventually cause flaky failures if the Queue singleton
accumulates state across modules.

## Affected Tests

| Test | Location |
|---|---|
| `TestBadAuth::test_invalid_key_raises_auth_error` | `tests/test_skill_client.py` |
| `test_delete_thread` | `tests/test_messages_delete_api.py` |

Both are the **last test** to run in their respective module, triggering
the `live_app` module-scoped fixture teardown.

## Reproduction

```bash
uv run pytest tests/test_skill_client.py tests/test_messages_delete_api.py \
    -v --timeout=30 2>&1 | grep -A5 "ERROR at teardown"
```

Both teardowns time out at 30 s and print the `Queue bound to a
different event loop` traceback.

## Root Cause

### The module-scoped event loop pattern

Both test modules define a module-scoped `event_loop` fixture:

```python
@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
```

All `scope="module"` async fixtures — including `live_app`, which starts
the FastAPI ASGI lifespan — run on this single long-lived loop.

### The global Queue singleton

`cairn/kb/sync_worker.py` stores the `asyncio.Queue` in a module-level
global:

```python
_sync_queue: Optional[asyncio.Queue] = None

def get_sync_queue() -> asyncio.Queue:
    global _sync_queue
    if _sync_queue is None:
        _sync_queue = asyncio.Queue()
    return _sync_queue
```

`asyncio.Queue` binds itself to the **running event loop at the moment
of first creation** (Python 3.10+). The first test module that starts
the app creates the Queue on **Module A's loop**. When the second test
module starts its own app, it calls `get_sync_queue()` — which returns
the already-created Queue still bound to **Module A's (now closed)
loop** — and tries to `await queue.get()` on **Module B's loop**.
Python detects the mismatch and raises `RuntimeError`.

### The teardown cascade

The `live_app` fixture teardown sequence is:

```python
sync_task.cancel()
await sync_task          # ← blocks here
```

The `sync_task` is `quartz_sync_worker()`, which catches the
`CancelledError` inside `await queue.get()`. But `queue.get()` raises
`RuntimeError` (wrong loop) instead of `CancelledError`, so the worker
falls into the `except Exception` handler and tries to log the
exception. The logger calls `traceback.format_exc()` which calls
`ast.parse()` — and pytest-timeout fires mid-traceback-format, producing
the mangled output seen in CI.

### Why it only fires on the last test in the module

The `live_app` fixture teardown only runs after the module's final test.
Earlier tests in the same module reuse the already-started app and never
trigger teardown.

## Sequence Diagram

```
Module A starts          Module B starts
────────────────         ────────────────
event_loop_A created
live_app_A starts
  Queue created, bound to loop_A
  sync_task_A starts (loop_A)
  [tests run]
                         event_loop_B created
                         live_app_B starts
                           get_sync_queue() → returns Queue (bound to loop_A!)
                           sync_task_B starts → await queue.get() → RuntimeError
live_app_A teardown
  sync_task_A.cancel()
  await sync_task_A
    queue.get() → RuntimeError (loop_A is closed)
    except Exception → logger.exception → timeout fires
```

## Files Involved

| File | Role |
|---|---|
| `cairn/kb/sync_worker.py` | Defines the global Queue singleton and `quartz_sync_worker()` |
| `cairn/api/app.py` | Creates and cancels `sync_task` in the lifespan |
| `tests/test_skill_client.py` | Module-scoped `event_loop` + `live_app` fixture |
| `tests/test_messages_delete_api.py` | Module-scoped `event_loop` + `live_app` fixture |

## Proposed Fix

There are two complementary changes needed:

### Fix 1 — Remove the global Queue singleton (production code)

The Queue must not be created at module import time or stored globally.
It must be created fresh each time the app starts, tied to the current
running loop. Pass it explicitly rather than fetching it through a
global accessor:

```python
# cairn/kb/sync_worker.py

import asyncio
import os
import logging

logger = logging.getLogger(__name__)


async def quartz_sync_worker(queue: asyncio.Queue) -> None:
    """Background worker that sequentially executes the Quartz sync command.

    Accepts the Queue as an argument so it is always bound to the
    caller's event loop. No global state.
    """
    logger.info("Quartz background sync worker started.")

    while True:
        try:
            await queue.get()

            while not queue.empty():
                queue.get_nowait()
                queue.task_done()

            cmd = os.getenv("CAIRN_QUARTZ_SYNC_CMD")
            cwd = os.getenv("CAIRN_QUARTZ_CONTENT_DIR", ".")

            if not cmd:
                logger.warning(
                    "Sync requested but CAIRN_QUARTZ_SYNC_CMD is not set. Skipping."
                )
                queue.task_done()
                continue

            logger.info(
                "Executing Quartz sync command: '%s' in '%s'", cmd, cwd
            )

            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()

            if proc.returncode != 0:
                logger.error(
                    "Quartz sync command failed with exit code %s",
                    proc.returncode,
                )
            else:
                logger.info("Quartz sync completed successfully.")

            queue.task_done()

        except asyncio.CancelledError:
            logger.info("Quartz background sync worker shutting down.")
            break
        except Exception as exc:
            logger.exception("Exception in Quartz sync worker: %s", exc)
```

### Fix 2 — Thread the Queue through the lifespan (production code)

In `cairn/api/app.py`, create the Queue inside the lifespan
(guaranteeing it is bound to the correct running loop) and pass it to
both the worker and any route that needs to enqueue a sync:

```python
# cairn/api/app.py  (lifespan excerpt)

from cairn.kb.sync_worker import quartz_sync_worker

@asynccontextmanager
async def lifespan(app: FastAPI):
    ...
    # Create Queue here — always bound to the current running loop.
    sync_queue: asyncio.Queue = asyncio.Queue()
    app.state.sync_queue = sync_queue          # routes read from app.state

    sync_task = asyncio.create_task(quartz_sync_worker(sync_queue))
    app.state.sync_task = sync_task

    yield

    sync_task.cancel()
    try:
        await sync_task
    except asyncio.CancelledError:
        pass
    ...
```

Routes that currently call `get_sync_queue()` should instead read
`request.app.state.sync_queue`.

### Fix 3 — Remove the module-scoped `event_loop` fixture (test code)

The module-scoped `event_loop` fixture is a pytest-asyncio anti-pattern
that was necessary in older versions but is discouraged in
`pytest-asyncio >= 0.21`. With `asyncio_mode = "auto"` the framework
manages loops correctly. Once Fix 1 and Fix 2 remove the shared Queue
singleton, the `live_app` fixture no longer needs a module-scoped loop
to share state — each test can get a fresh app instance.

If keeping module scope for performance (app startup is expensive),
annotate with `loop_scope="module"` on the fixture instead of providing
a custom `event_loop` fixture:

```python
@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def live_app(data_dir, agent_key_pair):
    ...
```

This tells pytest-asyncio to manage a module-scoped loop internally
rather than relying on the deprecated manual fixture.

## Workaround (current state)

Until the fix is implemented, the errors are noise-only. All test
assertions pass. The `--timeout` flag ensures the test run terminates
rather than hanging. The two teardown errors can be filtered in CI with:

```bash
uv run pytest --timeout=30 -q 2>&1 | grep -v "ERROR at teardown"
```

This is not recommended long-term — suppress the symptom, not the cause.

## Related

- Bug 008 was discovered while investigating the cross-module fixture
  import hang (the original issue that led to creating `tests/conftest.py`).
- The `conftest.py` fix (function-scoped `data_dir` / `agent_key_pair`
  fixtures) is a prerequisite mindset for Fix 3 above — the same
  principle of not sharing async state across event loop boundaries.
- `asyncio.Queue` loop-binding behaviour: Python docs §
  [asyncio-queues](https://docs.python.org/3/library/asyncio-queues.html)
