from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import AliasChoices, Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень проекта (main.py, .env) — для supervisor: directory=/home/appuser/crypto_bot
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"


def _normalize_postgres_async_url(url: str) -> str:
    """postgresql:// и postgres:// → драйвер asyncpg для SQLAlchemy async."""
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

    bybit_api_key: str
    bybit_api_secret: str
    bybit_network: str = "mainnet"  # mainnet | testnet
    bybit_category: str = "linear"
    bybit_position_idx: int = 0
    # spot Market: baseCoin | quoteCoin — для покупки золота за USDT обычно baseCoin (qty в базе)
    bybit_spot_market_unit: str = ""

    # Полный URL (postgresql+asyncpg://… или postgresql://… — будет нормализован)
    database_url_override: str | None = Field(default=None, validation_alias="DATABASE_URL")

    db_host: str | None = Field(default=None, validation_alias="DB_HOST")
    db_port: int = Field(default=5432, validation_alias="DB_PORT")
    db_name: str | None = Field(default=None, validation_alias="DB_NAME")
    db_user: str | None = Field(default=None, validation_alias="DB_USER")
    db_password: str = Field(default="", validation_alias="DB_PASSWORD")

    bybit_default_position_lots: str = Field(
        default="0.01",
        validation_alias=AliasChoices(
            "BYBIT_DEFAULT_POSITION_LOTS",
            "BYBIT_DEFAULT_ORDER_QTY",
        ),
    )

    log_level: str = "INFO"

    # bot_mode: advisor — только сигналы в Telegram; trading — автоторговля (Bybit + MT5)
    bot_mode: str = Field(default="advisor", validation_alias="BOT_MODE")

    @field_validator("bot_mode", mode="before")
    @classmethod
    def _bot_mode_norm(cls, v: object) -> str:
        if v is None or v == "":
            return "advisor"
        s = str(v).strip().lower()
        if s not in ("advisor", "trading"):
            raise ValueError("BOT_MODE должен быть advisor или trading")
        return s

    @property
    def is_advisor_mode(self) -> bool:
        return self.bot_mode == "advisor"

    @property
    def is_trading_mode(self) -> bool:
        return self.bot_mode == "trading"

    funding_scan_enabled: bool = Field(
        default=True,
        validation_alias="FUNDING_SCAN_ENABLED",
    )
    funding_annual_threshold: float = Field(
        default=5000.0,
        validation_alias="FUNDING_ANNUAL_THRESHOLD",
    )
    funding_top_n: int = Field(
        default=100,
        validation_alias="FUNDING_TOP_N",
    )

    # Группа-форум: сигналы EMA и funding — в топики; личка бота — только настройка
    telegram_alerts_chat_id: int | None = Field(
        default=None,
        validation_alias="TELEGRAM_ALERTS_CHAT_ID",
    )
    telegram_alerts_topic_signals: int | None = Field(
        default=None,
        validation_alias="TELEGRAM_ALERTS_TOPIC_SIGNALS",
    )
    telegram_alerts_topic_funding: int | None = Field(
        default=None,
        validation_alias="TELEGRAM_ALERTS_TOPIC_FUNDING",
    )
    telegram_alerts_topic_price_spike: int | None = Field(
        default=None,
        validation_alias="TELEGRAM_ALERTS_TOPIC_PRICE_SPIKE",
    )
    telegram_alerts_topic_ema_sl: int | None = Field(
        default=None,
        validation_alias="TELEGRAM_ALERTS_TOPIC_EMA_SL",
    )
    telegram_alerts_topic_sl_follow: int | None = Field(
        default=None,
        validation_alias="TELEGRAM_ALERTS_TOPIC_SL_FOLLOW",
    )
    telegram_alerts_topic_pump: int | None = Field(
        default=870,
        validation_alias="TELEGRAM_ALERTS_TOPIC_PUMP",
    )
    telegram_alerts_topic_dump: int | None = Field(
        default=965,
        validation_alias="TELEGRAM_ALERTS_TOPIC_DUMP",
    )

    ema_sl_monitor_enabled: bool = Field(
        default=True,
        validation_alias="EMA_SL_MONITOR_ENABLED",
    )
    sl_follow_monitor_enabled: bool = Field(
        default=True,
        validation_alias="SL_FOLLOW_MONITOR_ENABLED",
    )

    sl_anom_close_monitor_enabled: bool = Field(
        default=True,
        validation_alias="SL_ANOM_CLOSE_MONITOR_ENABLED",
    )

    price_spike_monitor_enabled: bool = Field(
        default=True,
        validation_alias="PRICE_SPIKE_MONITOR_ENABLED",
    )
    price_spike_ratio: float = Field(
        default=3.0,
        validation_alias="PRICE_SPIKE_RATIO",
    )
    price_spike_alert_cooldown_min: int = Field(
        default=5,
        validation_alias="PRICE_SPIKE_ALERT_COOLDOWN_MIN",
    )

    # Сигнал EMA: обе последние свечи шире среднего фона × этот множитель → предупреждение
    advisor_volatility_spike_factor: float = Field(
        default=1.5,
        validation_alias="ADVISOR_VOLATILITY_SPIKE_FACTOR",
    )
    fast_ema_inflection_enabled: bool = Field(
        default=True,
        validation_alias="FAST_EMA_INFLECTION_ENABLED",
    )
    atr_pullback_enabled: bool = Field(
        default=True,
        validation_alias="ATR_PULLBACK_ENABLED",
    )
    atr_pullback_debug_enabled: bool = Field(
        default=False,
        validation_alias="ATR_PULLBACK_DEBUG_ENABLED",
    )
    atr_pullback_debug_verbose: bool = Field(
        default=False,
        validation_alias="ATR_PULLBACK_DEBUG_VERBOSE",
    )
    atr_pullback_debug_dir: str = Field(
        default="",
        validation_alias="ATR_PULLBACK_DEBUG_DIR",
    )
    scalp_advisor_enabled: bool = Field(
        default=True,
        validation_alias="SCALP_ADVISOR_ENABLED",
    )
    scalp_advisor_debug_enabled: bool = Field(
        default=False,
        validation_alias="SCALP_ADVISOR_DEBUG_ENABLED",
    )
    scalp_advisor_debug_verbose: bool = Field(
        default=False,
        validation_alias="SCALP_ADVISOR_DEBUG_VERBOSE",
    )
    scalp_advisor_debug_dir: str = Field(
        default="",
        validation_alias="SCALP_ADVISOR_DEBUG_DIR",
    )
    # Только pump-сканер в планировщике: без советчика, funding и прочих мониторов
    pump_only_mode: bool = Field(
        default=False,
        validation_alias="PUMP_ONLY_MODE",
    )
    pump_scan_enabled: bool = Field(
        default=True,
        validation_alias="PUMP_SCAN_ENABLED",
    )
    # Временно: pump-алерты в личку SUPERADMIN_TELEGRAM_ID вместо топика группы
    pump_alerts_to_private: bool = Field(
        default=True,
        validation_alias="PUMP_ALERTS_TO_PRIVATE",
    )
    bot_order_watch_enabled: bool = Field(
        default=True,
        validation_alias="BOT_ORDER_WATCH_ENABLED",
    )
    bot_order_watch_interval_sec: int = Field(
        default=60,
        validation_alias="BOT_ORDER_WATCH_INTERVAL_SEC",
    )
    deepseek_api_key: str = Field(
        default="",
        validation_alias="DEEPSEEK_API_KEY",
    )
    deepseek_enabled: bool = Field(
        default=True,
        validation_alias="DEEPSEEK_ENABLED",
    )
    deepseek_model: str = Field(
        default="deepseek-v4-flash",
        validation_alias="DEEPSEEK_MODEL",
    )
    pump_ema_alarm_enabled: bool = Field(
        default=True,
        validation_alias="PUMP_EMA_ALARM_ENABLED",
    )
    pump_ema_alarm_interval_sec: int = Field(
        default=120,
        validation_alias="PUMP_EMA_ALARM_INTERVAL_SEC",
    )
    pump_entry_watch_enabled: bool = Field(
        default=True,
        validation_alias="PUMP_ENTRY_WATCH_ENABLED",
    )
    pump_entry_watch_interval_sec: int = Field(
        default=180,
        validation_alias="PUMP_ENTRY_WATCH_INTERVAL_SEC",
    )
    pump_entry_watch_llm_cooldown_sec: int = Field(
        default=3600,
        validation_alias="PUMP_ENTRY_WATCH_LLM_COOLDOWN_SEC",
    )
    pump_entry_watch_max_per_user: int = Field(
        default=20,
        validation_alias="PUMP_ENTRY_WATCH_MAX_PER_USER",
    )
    lunarcrush_api_key: str = Field(
        default="",
        validation_alias="LUNARCRUSH_API_KEY",
    )
    lunarcrush_enabled: bool = Field(
        default=True,
        validation_alias="LUNARCRUSH_ENABLED",
    )

    @field_validator("lunarcrush_enabled", mode="before")
    @classmethod
    def _lunarcrush_enabled(cls, v: object) -> bool:
        if v is None or v == "":
            return True
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @property
    def lunarcrush_ready(self) -> bool:
        return (
            self.lunarcrush_enabled
            and bool((self.lunarcrush_api_key or "").strip())
        )

    @field_validator("pump_only_mode", mode="before")
    @classmethod
    def _pump_only_mode(cls, v: object) -> bool:
        if v is None or v == "":
            return False
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @field_validator("pump_scan_enabled", mode="before")
    @classmethod
    def _pump_scan_enabled(cls, v: object) -> bool:
        if v is None or v == "":
            return True
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @field_validator("pump_alerts_to_private", mode="before")
    @classmethod
    def _pump_alerts_to_private(cls, v: object) -> bool:
        if v is None or v == "":
            return True
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @field_validator("bot_order_watch_enabled", mode="before")
    @classmethod
    def _bot_order_watch_enabled(cls, v: object) -> bool:
        if v is None or v == "":
            return True
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @field_validator("bot_order_watch_interval_sec", mode="before")
    @classmethod
    def _bot_order_watch_interval_sec(cls, v: object) -> int:
        if v is None or v == "":
            return 60
        n = int(v)
        return max(15, min(n, 600))

    @field_validator("deepseek_enabled", mode="before")
    @classmethod
    def _deepseek_enabled(cls, v: object) -> bool:
        if v is None or v == "":
            return True
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @field_validator("pump_ema_alarm_enabled", mode="before")
    @classmethod
    def _pump_ema_alarm_enabled(cls, v: object) -> bool:
        if v is None or v == "":
            return True
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @field_validator("pump_ema_alarm_interval_sec", mode="before")
    @classmethod
    def _pump_ema_alarm_interval_sec(cls, v: object) -> int:
        if v is None or v == "":
            return 120
        n = int(v)
        return max(60, min(n, 180))

    @field_validator("pump_entry_watch_enabled", mode="before")
    @classmethod
    def _pump_entry_watch_enabled(cls, v: object) -> bool:
        if v is None or v == "":
            return True
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @field_validator("pump_entry_watch_interval_sec", mode="before")
    @classmethod
    def _pump_entry_watch_interval_sec(cls, v: object) -> int:
        if v is None or v == "":
            return 180
        n = int(v)
        return max(60, min(n, 600))

    @field_validator("pump_entry_watch_llm_cooldown_sec", mode="before")
    @classmethod
    def _pump_entry_watch_llm_cooldown_sec(cls, v: object) -> int:
        if v is None or v == "":
            return 3600
        n = int(v)
        return max(300, min(n, 86400))

    @field_validator("pump_entry_watch_max_per_user", mode="before")
    @classmethod
    def _pump_entry_watch_max_per_user(cls, v: object) -> int:
        if v is None or v == "":
            return 20
        n = int(v)
        return max(1, min(n, 50))

    @property
    def deepseek_ready(self) -> bool:
        return self.deepseek_enabled and bool((self.deepseek_api_key or "").strip())

    @field_validator("scalp_advisor_enabled", mode="before")
    @classmethod
    def _scalp_advisor_enabled(cls, v: object) -> bool:
        if v is None or v == "":
            return True
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @field_validator("scalp_advisor_debug_enabled", mode="before")
    @classmethod
    def _scalp_advisor_debug_enabled(cls, v: object) -> bool:
        if v is None or v == "":
            return False
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @field_validator("scalp_advisor_debug_verbose", mode="before")
    @classmethod
    def _scalp_advisor_debug_verbose(cls, v: object) -> bool:
        if v is None or v == "":
            return False
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @field_validator("atr_pullback_debug_enabled", mode="before")
    @classmethod
    def _atr_pullback_debug_enabled(cls, v: object) -> bool:
        if v is None or v == "":
            return False
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @field_validator("atr_pullback_debug_verbose", mode="before")
    @classmethod
    def _atr_pullback_debug_verbose(cls, v: object) -> bool:
        if v is None or v == "":
            return False
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @field_validator("fast_ema_inflection_enabled", mode="before")
    @classmethod
    def _fast_ema_inflection_enabled(cls, v: object) -> bool:
        if v is None or v == "":
            return True
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        return s in ("1", "true", "yes", "on")

    @field_validator("sl_follow_monitor_enabled", mode="before")
    @classmethod
    def _sl_follow_monitor_enabled(cls, v: object) -> bool:
        if v is None or v == "":
            return True
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        return s in ("1", "true", "yes", "on")

    @field_validator("price_spike_monitor_enabled", mode="before")
    @classmethod
    def _price_spike_monitor_enabled(cls, v: object) -> bool:
        if v is None or v == "":
            return True
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        return s in ("1", "true", "yes", "on")

    @field_validator("price_spike_ratio", mode="before")
    @classmethod
    def _price_spike_ratio(cls, v: object) -> float:
        if v is None or v == "":
            return 3.0
        f = float(v)
        if f < 1.0:
            raise ValueError("PRICE_SPIKE_RATIO должен быть >= 1.0")
        return f

    @field_validator("price_spike_alert_cooldown_min", mode="before")
    @classmethod
    def _price_spike_cooldown(cls, v: object) -> int:
        if v is None or v == "":
            return 5
        n = int(v)
        return max(1, min(n, 120))

    @field_validator("funding_scan_enabled", mode="before")
    @classmethod
    def _funding_scan_enabled(cls, v: object) -> bool:
        if v is None or v == "":
            return True
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        return s in ("1", "true", "yes", "on")

    @field_validator("funding_annual_threshold", mode="before")
    @classmethod
    def _funding_threshold(cls, v: object) -> float:
        if v is None or v == "":
            return 5000.0
        return float(v)

    @field_validator("funding_top_n", mode="before")
    @classmethod
    def _funding_top_n(cls, v: object) -> int:
        if v is None or v == "":
            return 100
        n = int(v)
        return max(1, min(n, 250))

    @field_validator("advisor_volatility_spike_factor", mode="before")
    @classmethod
    def _advisor_volatility_spike_factor(cls, v: object) -> float:
        if v is None or v == "":
            return 1.5
        f = float(v)
        if f < 1.0:
            raise ValueError("ADVISOR_VOLATILITY_SPIKE_FACTOR должен быть >= 1.0")
        return f

    @field_validator(
        "telegram_alerts_chat_id",
        "telegram_alerts_topic_signals",
        "telegram_alerts_topic_funding",
        "telegram_alerts_topic_price_spike",
        "telegram_alerts_topic_ema_sl",
        "telegram_alerts_topic_sl_follow",
        "telegram_alerts_topic_pump",
        "telegram_alerts_topic_dump",
        mode="before",
    )
    @classmethod
    def _empty_telegram_alerts_id(cls, v: object) -> object:
        if v is None or v == "":
            return None
        return int(v)  # type: ignore[arg-type]

    @property
    def telegram_signals_channel_ready(self) -> bool:
        return (
            self.telegram_alerts_chat_id is not None
            and self.telegram_alerts_topic_signals is not None
        )

    @property
    def telegram_funding_channel_ready(self) -> bool:
        return (
            self.telegram_alerts_chat_id is not None
            and self.telegram_alerts_topic_funding is not None
        )

    @property
    def telegram_price_spike_channel_ready(self) -> bool:
        return (
            self.telegram_alerts_chat_id is not None
            and self.telegram_alerts_topic_price_spike is not None
        )

    @property
    def telegram_ema_sl_channel_ready(self) -> bool:
        return (
            self.telegram_alerts_chat_id is not None
            and self.telegram_alerts_topic_ema_sl is not None
        )

    @property
    def telegram_sl_follow_channel_ready(self) -> bool:
        return (
            self.telegram_alerts_chat_id is not None
            and self.telegram_alerts_topic_sl_follow is not None
        )

    @property
    def telegram_pump_channel_ready(self) -> bool:
        return (
            self.telegram_alerts_chat_id is not None
            and self.telegram_alerts_topic_pump is not None
        )

    @property
    def telegram_dump_channel_ready(self) -> bool:
        return (
            self.telegram_alerts_chat_id is not None
            and self.telegram_alerts_topic_dump is not None
        )

    # MT5: local = пакет MetaTrader5 в этом Python (часто Windows).
    # linux_bridge = терминал MT5 под Wine + пакет mt5linux (rpyc), см. https://www.mql5.com/en/articles/625
    mt5_transport: str = Field(default="local", validation_alias="MT5_TRANSPORT")

    # MetaTrader 5 — логин брокера (для local и linux_bridge)
    mt5_login: int | None = Field(default=None, validation_alias="MT5_LOGIN")
    mt5_password: str = Field(default="", validation_alias="MT5_PASSWORD")
    mt5_server: str = Field(default="", validation_alias="MT5_SERVER")
    mt5_path: str = Field(default="", validation_alias="MT5_PATH")
    mt5_magic: int = Field(default=902001, validation_alias="MT5_MAGIC")

    # mt5linux: куда стучится Linux-бот (на той же машине — Wine слушает 127.0.0.1:18812)
    mt5linux_host: str = Field(default="127.0.0.1", validation_alias="MT5LINUX_HOST")
    mt5linux_port: int = Field(default=18812, validation_alias="MT5LINUX_PORT")

    @field_validator("mt5_transport", mode="before")
    @classmethod
    def _mt5_transport_norm(cls, v: object) -> str:
        if v is None or v == "":
            return "local"
        s = str(v).strip().lower()
        if s == "metaapi":
            raise ValueError(
                "MT5_TRANSPORT=metaapi больше не поддерживается. Используйте local или linux_bridge."
            )
        if s not in ("local", "linux_bridge"):
            raise ValueError("MT5_TRANSPORT должен быть local или linux_bridge")
        return s

    @field_validator("mt5_login", mode="before")
    @classmethod
    def _mt5_login_empty_to_none(cls, v: object) -> object:
        if v is None or v == "":
            return None
        return int(v)  # type: ignore[arg-type]

    @field_validator("database_url_override", mode="before")
    @classmethod
    def _empty_database_url_to_none(cls, v: object) -> object:
        if v == "":
            return None
        return v

    @computed_field
    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return _normalize_postgres_async_url(self.database_url_override)
        if self.db_host and self.db_name and self.db_user:
            u = quote_plus(self.db_user)
            p = quote_plus(self.db_password)
            return (
                f"postgresql+asyncpg://{u}:{p}@{self.db_host}:{self.db_port}/{self.db_name}"
            )
        raise ValueError(
            "Требуется PostgreSQL: задайте DATABASE_URL "
            "или связку DB_HOST, DB_NAME, DB_USER (опционально DB_PASSWORD, DB_PORT)."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
