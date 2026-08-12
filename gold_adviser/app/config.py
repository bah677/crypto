from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"


def _normalize_postgres_async_url(url: str) -> str:
    u = url.strip()
    if u.startswith("postgresql+asyncpg://"):
        return u
    if u.startswith("postgresql://"):
        return "postgresql+asyncpg://" + u.removeprefix("postgresql://")
    if u.startswith("postgres://"):
        return "postgresql+asyncpg://" + u.removeprefix("postgres://")
    return u


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str
    superadmin_telegram_id: int

    database_url_override: str | None = Field(default=None, validation_alias="DATABASE_URL")
    db_host: str = Field(default="localhost", validation_alias="DB_HOST")
    db_port: int = Field(default=5432, validation_alias="DB_PORT")
    db_name: str = Field(default="gold_adviser", validation_alias="DB_NAME")
    db_user: str = Field(default="", validation_alias="DB_USER")
    db_password: str = Field(default="", validation_alias="DB_PASSWORD")

    log_level: str = "INFO"

    # Market data
    realmarket_api_key: str = Field(default="", validation_alias="REALMARKET_API_KEY")
    realmarket_base_url: str = Field(
        default="https://api.realmarketapi.com",
        validation_alias="REALMARKET_BASE_URL",
    )
    twelvedata_api_key: str = Field(default="", validation_alias="TWELVEDATA_API_KEY")
    twelvedata_base_url: str = Field(
        default="https://api.twelvedata.com",
        validation_alias="TWELVEDATA_BASE_URL",
    )

    symbol: str = Field(default="XAUUSD", validation_alias="GOLD_SYMBOL")
    scan_second: int = Field(default=3, validation_alias="GOLD_SCAN_SECOND")

    # Defaults also seeded into DB (runtime values live in gold_settings)
    default_enabled: bool = Field(default=True, validation_alias="GOLD_DEFAULT_ENABLED")
    default_body_mult: float = Field(default=2.0, validation_alias="GOLD_DEFAULT_BODY_MULT")
    default_lookback: int = Field(default=30, validation_alias="GOLD_DEFAULT_LOOKBACK")
    default_settings_cache_ttl_sec: int = Field(
        default=30,
        validation_alias="GOLD_SETTINGS_CACHE_TTL_SEC",
    )

    @field_validator("scan_second", mode="before")
    @classmethod
    def _scan_second(cls, v: object) -> int:
        if v is None or v == "":
            return 3
        return max(0, min(int(v), 50))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return _normalize_postgres_async_url(self.database_url_override)
        user = quote_plus(self.db_user or "")
        password = quote_plus(self.db_password or "")
        return (
            f"postgresql+asyncpg://{user}:{password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
