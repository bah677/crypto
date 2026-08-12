from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdminRow(Base):
    __tablename__ = "admins"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    note: Mapped[str] = mapped_column(String(255), default="")
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class GoldSettingsRow(Base):
    """Singleton runtime settings (id=1). Editable via admin panel without restart."""

    __tablename__ = "gold_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    body_mult: Mapped[float] = mapped_column(Float, default=2.0, nullable=False)
    lookback: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    settings_cache_ttl_sec: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class GoldAlertRow(Base):
    """Dedupe: one alert per closed candle open-time."""

    __tablename__ = "gold_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candle_open_time: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), default="")
    body: Mapped[float] = mapped_column(Float, default=0.0)
    avg_body: Mapped[float] = mapped_column(Float, default=0.0)
    ratio: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
