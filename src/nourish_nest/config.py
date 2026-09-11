from functools import lru_cache

from pydantic import Field, SecretStr
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
    pantry_expiring_soon_days: int = 3
    ai_provider: str = "disabled"
    ai_model: str = ""
    ai_api_key: SecretStr | None = Field(default=None, repr=False)
    ai_timeout_seconds: float = Field(default=10, gt=0, le=60)
    ai_max_tool_calls: int = Field(default=4, ge=1, le=4)
    ollama_base_url: str = "http://127.0.0.1:11434"


@lru_cache
def get_settings() -> Settings:
    return Settings()
