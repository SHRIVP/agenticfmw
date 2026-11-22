FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY servers.yaml ./
RUN uv sync --frozen --no-dev
EXPOSE 8000
CMD ["uv", "run", "pcagent", "serve", "--host", "0.0.0.0"]
