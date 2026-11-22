from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    model: str = Field("anthropic/claude-opus-5-5", validation_alias="PCAGENT_MODEL")
    database_url: str | None = Field(None, validation_alias="DATABASE_URL")
    servers_path: Path = Field(Path("servers.yaml"), validation_alias="PCAGENT_SERVERS")
    policy_path: Path | None = Field(None, validation_alias="PCAGENT_POLICY")
    api_url: str = Field("http://localhost:8000", validation_alias="PCAGENT_API")


def load_env() -> None:
    """Load ./.env into os.environ (shell values win). API keys for LiteLLM, TypeSafe and
    Langfuse are read from os.environ, which pydantic-settings does not populate."""
    load_dotenv(Path.cwd() / ".env", override=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
