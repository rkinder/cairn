# Cairn blackboard API — container image
#
# Build:  docker build -t cairn-api .
# Run:    docker run -p 8000:8000 -v $(pwd)/data:/data cairn-api

FROM python:3.11-slim

LABEL org.opencontainers.image.title="Cairn Blackboard API"
LABEL org.opencontainers.image.description="Multi-agent knowledge sharing system built on the Blackboard Pattern"
LABEL org.opencontainers.image.licenses="AGPL-3.0"

WORKDIR /app

# Install dependencies first (better layer caching).
COPY pyproject.toml ./
# Minimal stub so pip can resolve the package metadata before copying source.
RUN mkdir -p cairn && touch cairn/__init__.py \
    && pip install --no-cache-dir . \
    && rm -rf cairn

# Copy the full source.
COPY cairn/ cairn/

# Re-install in non-editable mode with the real source.
RUN pip install --no-cache-dir --no-deps .

# Default environment (overridden by docker-compose or -e flags).
ENV CAIRN_HOST=0.0.0.0
ENV CAIRN_PORT=8000
ENV CAIRN_DATA_DIR=/data
ENV CAIRN_RELOAD=false

# SQLite database files are stored on a mounted volume.
VOLUME ["/data"]

EXPOSE 8000

# cairn entry point defined in pyproject.toml [project.scripts].
CMD ["cairn"]
