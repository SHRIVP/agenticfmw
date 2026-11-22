"""Configuration: `pcagent.yaml` (packs, model, judge, rules, tracing, checkpointer) and env settings."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(ValueError):
    """pcagent.yaml is missing, invalid, or refers to an unset environment variable."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    model: str = Field("anthropic/claude-opus-5-5", validation_alias="PCAGENT_MODEL")
    database_url: str | None = Field(None, validation_alias="DATABASE_URL")
    api_url: str = Field("http://localhost:8000", validation_alias="PCAGENT_API")


class PackRef(BaseModel):
    use: str
    settings: dict[str, Any] = {}


class JudgeConfig(BaseModel):
    type: str = "jev"
    thresholds: dict[str, float] = {}


class TracingConfig(BaseModel):
    exporter: Literal["none", "console", "otlp"] = "none"
    endpoint: str | None = None
    headers: dict[str, str] = {}
    capture_content: bool = False


class AppConfig(BaseModel):
    packs: list[PackRef] = []
    model: str = "anthropic/claude-opus-5-5"
    judge: JudgeConfig = JudgeConfig()
    rules: dict[str, str] = {}
    tracing: TracingConfig = TracingConfig()
    checkpointer: str | None = None


_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}")


def expand_env(value: Any) -> Any:
    """Replace ${VAR} everywhere; an unset variable is an error, never a literal."""
    if isinstance(value, str):
        def sub(m):
            if m.group(1) not in os.environ:
                if m.group(2) is not None:  # ${VAR:-default}
                    return m.group(3)
                raise ConfigError(f"environment variable {m.group(1)} is not set (used in pcagent config)")
            return os.environ[m.group(1)]
        return _VAR.sub(sub, value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


MIGRATION_HINT = """found servers.yaml but no pcagent.yaml. pcagent now reads pcagent.yaml, for example:

packs:
  - use: pcagent.packs.everywire:make_pack
    settings:
      gwmcp: {url: https://your-pas.example/mcp}
checkpointer: ${DATABASE_URL}

PCAGENT_SERVERS and PCAGENT_POLICY are no longer used; see docs/guide/config.md."""


def load_config(path: str | Path | None = None) -> AppConfig:
    load_env()
    if path is None:
        path = Path(os.environ.get("PCAGENT_CONFIG", "pcagent.yaml"))
        if not path.exists() and Path("servers.yaml").exists():
            raise ConfigError(MIGRATION_HINT)
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file {path} not found (set PCAGENT_CONFIG or create pcagent.yaml)")
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML: {e}") from e
    data = expand_env(data)
    if os.environ.get("PCAGENT_MODEL"):
        data["model"] = os.environ["PCAGENT_MODEL"]
    if not data.get("checkpointer"):
        data["checkpointer"] = None
    try:
        return AppConfig(**data)
    except ValidationError as e:
        raise ConfigError(f"{path}: {e}") from e


def load_env() -> None:
    """Load ./.env into os.environ (shell values win). API keys for LiteLLM and TypeSafe
    are read from os.environ, which pydantic-settings does not populate."""
    load_dotenv(Path.cwd() / ".env", override=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
