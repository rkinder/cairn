import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

from cairn.kb.sync_worker import get_sync_queue, quartz_sync_worker
import os

@pytest.fixture
def clean_queue():
    import cairn.kb.sync_worker
    cairn.kb.sync_worker._sync_queue = None
    return get_sync_queue()

@pytest.mark.asyncio
@patch("os.getenv")
@patch("asyncio.create_subprocess_shell")
async def test_quartz_sync_worker_debounce(mock_subprocess, mock_getenv, clean_queue):
    # Setup mocks
    mock_getenv.side_effect = lambda k, default=None: "npx quartz sync" if k == "CAIRN_QUARTZ_SYNC_CMD" else "/tmp"
    
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock()
    mock_proc.returncode = 0
    mock_subprocess.return_value = mock_proc

    # Add 5 fast requests to the queue (simulating a burst of promotions)
    for _ in range(5):
        clean_queue.put_nowait(True)

    # Start the worker
    worker_task = asyncio.create_task(quartz_sync_worker())

    # Yield control to let worker process the debounced items
    await asyncio.sleep(0.1)

    # Cancel the worker cleanly
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

    # Assert that subprocess was only called ONCE despite 5 queued items
    assert mock_subprocess.call_count == 1
    mock_subprocess.assert_called_once_with(
        "npx quartz sync",
        cwd="/tmp",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    
    # Assert queue is now empty
    assert clean_queue.empty()

@pytest.mark.asyncio
@patch("os.getenv")
@patch("asyncio.create_subprocess_shell")
async def test_quartz_sync_worker_handles_failure(mock_subprocess, mock_getenv, clean_queue):
    # Setup mocks
    mock_getenv.side_effect = lambda k, default=None: "npx quartz sync" if k == "CAIRN_QUARTZ_SYNC_CMD" else "/tmp"
    
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock()
    mock_proc.returncode = 1  # Simulate a failure
    mock_subprocess.return_value = mock_proc

    # Add 1 request
    clean_queue.put_nowait(True)

    worker_task = asyncio.create_task(quartz_sync_worker())
    await asyncio.sleep(0.1)
    
    # Add a second request to ensure it didn't crash on failure
    clean_queue.put_nowait(True)
    await asyncio.sleep(0.1)

    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

    assert mock_subprocess.call_count == 2
