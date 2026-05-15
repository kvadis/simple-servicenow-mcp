# syntax=docker/dockerfile:1.7

# ── builder ─────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Install build dependencies first to let layer cache help on src-only changes.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --upgrade pip build && python -m build --wheel -o /wheels

# ── runtime ─────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Non-root user (UID/GID 10001 for compatibility with restricted runtimes)
RUN groupadd --system --gid 10001 mcp \
 && useradd  --system --uid 10001 --gid mcp --shell /usr/sbin/nologin --no-create-home mcp

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install /wheels/*.whl && rm -rf /wheels

USER mcp

# Defaults — override at runtime
ENV SN_LOG_FORMAT=json \
    SN_LOG_LEVEL=INFO

# HTTP transport listens on 8000 by default; stdio doesn't expose a port.
EXPOSE 8000

# Stdio is the most common mode for Claude Desktop / Cursor. For container
# deployments behind a proxy, override CMD with: --transport http --host 0.0.0.0
ENTRYPOINT ["simple-servicenow-mcp"]
CMD []
