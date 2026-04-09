from functools import lru_cache

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    database_url: str
    log_level: str = "INFO"
    timezone: str = "Europe/Minsk"
    dev_role_switch_enabled: bool = True
    superadmin_tg_id: int = 0

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, v: str) -> str:
        # Supabase gives: postgresql://...
        # For async SQLAlchemy we need: postgresql+asyncpg://...
        if v.startswith("postgresql+asyncpg://"):
            return v
        if v.startswith("postgresql://"):
            return "postgresql+asyncpg://" + v.removeprefix("postgresql://")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
