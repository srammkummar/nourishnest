from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    env: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./nourish_nest.db"
    llm_provider: str = "mock"
    food_data_provider: str = "fake"
    usda_api_key: str | None = None
    usda_base_url: str = "https://api.nal.usda.gov/fdc/v1"
    usda_timeout_seconds: float = 10.0
    usda_max_retries: int = 2
    usda_cache_ttl_seconds: int = 300


@lru_cache
def get_settings() -> Settings:
    return Settings()

