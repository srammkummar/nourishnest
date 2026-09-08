from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    env: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./nourish_nest.db"
    llm_provider: str = "mock"


@lru_cache
def get_settings() -> Settings:
    return Settings()

