# GridDesk Python services (API and MCP servers). One image, different commands.
FROM python:3.12-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
COPY docs ./docs
COPY evals ./evals
RUN uv sync --locked --no-dev
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
# The code and the venv stay root-owned (read-only for the service); only the cache dir is writable.
RUN useradd --create-home griddesk && mkdir -p /app/.griddesk && chown griddesk /app/.griddesk
USER griddesk
EXPOSE 8000
CMD ["uvicorn", "griddesk.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
