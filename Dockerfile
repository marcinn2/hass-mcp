# Multi-stage build for smaller final image (base image without VectorDB)
# Stage 1: Build stage with uv and build tools
FROM ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm AS builder

WORKDIR /app

# Copy project files
COPY . .

# Install the locked dependency set, then the package itself without letting
# pip re-resolve. This keeps the image identical to the set that CI audits,
# rather than resolving pyproject constraints afresh at build time.
RUN uv export --frozen --no-hashes --no-dev --no-emit-project \
        --format requirements-txt -o /tmp/requirements.txt \
    && uv pip install --system -r /tmp/requirements.txt \
    && uv pip install --system --no-deps -e .

# Stage 2: Runtime stage with minimal base
FROM python:3.13-slim

# OCI image labels. org.opencontainers.image.source is what links the published
# package to this repository on GHCR (and lets it inherit repo visibility);
# CI additionally supplies version/revision/created via docker/metadata-action.
LABEL org.opencontainers.image.source="https://github.com/marcinn2/hass-mcp" \
      org.opencontainers.image.url="https://github.com/marcinn2/hass-mcp" \
      org.opencontainers.image.documentation="https://marcinn2.github.io/hass-mcp" \
      org.opencontainers.image.vendor="marcinn2" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.title="Hass-MCP" \
      org.opencontainers.image.description="Home Assistant Model Context Protocol (MCP) server"

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
WORKDIR /app
COPY . .

# Set environment for MCP communication
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Disable VectorDB by default for this base image
# Users can enable it by setting HASS_MCP_VECTOR_DB_ENABLED=true
# and connecting to an external VectorDB server
ENV HASS_MCP_VECTOR_DB_ENABLED=false

# Run the MCP server with stdio communication using the module directly
ENTRYPOINT ["python", "-m", "app"]
