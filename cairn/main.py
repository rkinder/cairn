"""Entry point for the Cairn blackboard server.

Run directly:
    python -m cairn.main

Or via the installed script:
    cairn

Or with uvicorn directly (useful for prod deployments):
    uvicorn cairn.api.app:create_app --factory --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging

import uvicorn

from cairn.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "cairn.api.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
