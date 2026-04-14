"""FastAPI application factory for the Cairn blackboard API."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from cairn.api.broadcast import MessageBroadcaster
from cairn.api.routes import messages, stream
from cairn.config import get_settings
from cairn.db.connections import DatabaseManager
from cairn.db.init import init_all
from cairn.ingest.parser import ParseError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan: database init, connection management, broadcaster setup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    logger.info("Cairn starting — data directory: %s", settings.data_dir)

    # Initialise databases (creates files if they don't exist).
    db_paths = await init_all(settings.data_dir)

    # Build the topic_paths dict (everything except index).
    topic_paths = {slug: path for slug, path in db_paths.items() if slug != "index"}

    db = DatabaseManager()
    await db.open(index_path=db_paths["index"], topic_paths=topic_paths)

    broadcaster = MessageBroadcaster()

    app.state.db = db
    app.state.broadcaster = broadcaster

    logger.info("Cairn ready — %d topic DB(s) active", len(topic_paths))

    yield

    # Shutdown
    await db.close()
    logger.info("Cairn shut down cleanly.")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Cairn Blackboard API",
        description=(
            "Multi-agent knowledge sharing system built on the Blackboard Pattern. "
            "Agents post YAML+markdown messages; the API routes them to topic databases "
            "and maintains a cross-domain index for coordinated queries."
        ),
        version="0.1.0",
        # The agent skill fetches the spec from this URL to self-update.
        openapi_url="/api/spec.json",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        lifespan=lifespan,
    )

    # CORS — permissive in dev; tighten origins list in production.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---------------------------------------------------------------------------
    # Exception handlers
    # ---------------------------------------------------------------------------

    @app.exception_handler(ParseError)
    async def parse_error_handler(request: Request, exc: ParseError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(exc)},
        )

    @app.exception_handler(KeyError)
    async def key_error_handler(request: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)},
        )

    # ---------------------------------------------------------------------------
    # Routers
    # ---------------------------------------------------------------------------

    app.include_router(messages.router)
    app.include_router(stream.router)

    # ---------------------------------------------------------------------------
    # Health check
    # ---------------------------------------------------------------------------

    @app.get("/health", tags=["meta"], summary="Service health check")
    async def health(request: Request) -> dict:
        db: DatabaseManager = request.app.state.db
        broadcaster: MessageBroadcaster = request.app.state.broadcaster
        return {
            "status": "ok",
            "topic_dbs": db.known_topics(),
            "sse_subscribers": broadcaster.subscriber_count,
        }

    return app
