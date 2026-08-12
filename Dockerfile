FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock README.md /app/
RUN uv sync --frozen --no-dev --no-install-project

COPY src /app/src
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 1001 sandbox && mkdir -p /work && chown sandbox /work
USER sandbox
ENV PATH="/app/.venv/bin:$PATH" \
    SYROS_WORK_DIR=/work

ENTRYPOINT ["python", "-m", "syros.runner"]
