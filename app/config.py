from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/access"

    # "username:role:token" через запятую
    seed_users: str = ""
    # "name:owner_username:criticality" через запятую
    seed_resources: str = ""

    worker_poll_interval: float = 1.0
    worker_lease_seconds: int = 30
    worker_max_attempts: int = 3

    provisioning_delay_seconds: float = 0.5
    provisioning_fail_resource_names: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
