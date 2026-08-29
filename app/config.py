"""All configuration for the service. No other module reads os.environ."""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)

    li_at: str | None = None
    li_jsessionid: str | None = None
    li_username: str | None = None
    li_password: str | None = None

    session_path: str = "/data/session.json"
    cache_ttl_seconds: int = 21600
    outbound_rate_seconds: float = 30.0
    inbound_rate_per_minute: int = 20

    # Observed from a live LinkedIn web session during fixture capture, then pinned.
    # An invented or stale value is a known bot signal.
    client_version: str = "1.13.46267"
    user_agent: str = DEFAULT_USER_AGENT

    @field_validator("api_keys", mode="before")
    @classmethod
    def _split_keys(cls, value: object) -> object:
        if isinstance(value, str):
            return [key.strip() for key in value.split(",") if key.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
