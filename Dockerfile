FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/
# `uv run` in CMD must not re-sync dev dependencies or touch the lockfile.
ENV UV_NO_DEV=1 UV_FROZEN=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY pcagent.yaml ./
RUN uv sync --frozen --no-dev
EXPOSE 8000
CMD ["uv", "run", "pcagent", "serve", "--host", "0.0.0.0"]
