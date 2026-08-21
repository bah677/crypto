"""Лёгкие миграции для существующих БД (create_all не меняет колонки)."""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection


def ensure_trading_tasks_columns(connection: Connection) -> None:
    insp = inspect(connection)
    if "trading_tasks" not in insp.get_table_names():
        return
    names = {c["name"] for c in insp.get_columns("trading_tasks")}
    if "stop_loss_ticks" not in names:
        connection.execute(
            text(
                "ALTER TABLE trading_tasks ADD COLUMN stop_loss_ticks INTEGER NOT NULL DEFAULT 0"
            )
        )
    if "last_evaluated_bar_open_ms" not in names:
        connection.execute(
            text(
                "ALTER TABLE trading_tasks ADD COLUMN last_evaluated_bar_open_ms BIGINT NULL"
            )
        )
    if "trading_channel" not in names:
        connection.execute(
            text(
                "ALTER TABLE trading_tasks ADD COLUMN trading_channel VARCHAR(16) "
                "NOT NULL DEFAULT 'bybit_v5'"
            )
        )


def ensure_advisor_tasks_columns(connection: Connection) -> None:
    insp = inspect(connection)
    if "advisor_tasks" not in insp.get_table_names():
        return
    names = {c["name"] for c in insp.get_columns("advisor_tasks")}
    if "bybit_category" not in names:
        connection.execute(
            text(
                "ALTER TABLE advisor_tasks ADD COLUMN bybit_category VARCHAR(16) "
                "NOT NULL DEFAULT 'linear'"
            )
        )
    if "alias" not in names:
        connection.execute(
            text(
                "ALTER TABLE advisor_tasks ADD COLUMN alias VARCHAR(64) "
                "NOT NULL DEFAULT ''"
            )
        )


def ensure_bot_alerts_flags_columns(connection: Connection) -> None:
    insp = inspect(connection)
    if "bot_alerts_flags" not in insp.get_table_names():
        return
    names = {c["name"] for c in insp.get_columns("bot_alerts_flags")}
    if "funding_reports" not in names:
        connection.execute(
            text(
                "ALTER TABLE bot_alerts_flags ADD COLUMN funding_reports BOOLEAN "
                "NOT NULL DEFAULT TRUE"
            )
        )


def ensure_scalp_advisor_columns(connection: Connection) -> None:
    insp = inspect(connection)
    if "scalp_advisor_tasks" not in insp.get_table_names():
        return
    names = {c["name"] for c in insp.get_columns("scalp_advisor_tasks")}
    additions = [
        ("trade_state", "VARCHAR(16) NOT NULL DEFAULT 'idle'"),
        ("trade_side", "VARCHAR(8) NULL"),
        ("entry_price", "DOUBLE PRECISION NULL"),
        ("entry_ms", "BIGINT NULL"),
        ("trade_sl", "DOUBLE PRECISION NULL"),
        ("trade_tp1", "DOUBLE PRECISION NULL"),
        ("trade_tp2", "DOUBLE PRECISION NULL"),
        ("tp1_hit", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("tp2_hit", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("last_reported_sl", "DOUBLE PRECISION NULL"),
        ("last_m5_sl_bar_ms", "BIGINT NULL"),
        ("initial_sl", "DOUBLE PRECISION NULL"),
        ("strategy_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("notional_usd", "DOUBLE PRECISION NOT NULL DEFAULT 1000"),
    ]
    for col, ddl in additions:
        if col not in names:
            connection.execute(
                text(f"ALTER TABLE scalp_advisor_tasks ADD COLUMN {col} {ddl}")
            )


def ensure_pump_entry_watch_columns(connection: Connection) -> None:
    insp = inspect(connection)
    if "pump_entry_watches" not in insp.get_table_names():
        return
    names = {c["name"] for c in insp.get_columns("pump_entry_watches")}
    additions = [
        ("initial_analysis", "TEXT NOT NULL DEFAULT ''"),
        ("initial_entry_timing", "VARCHAR(16) NOT NULL DEFAULT 'unknown'"),
        ("analysis_history_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("high_watermark_price", "DOUBLE PRECISION NULL"),
        ("current_phase", "VARCHAR(32) NOT NULL DEFAULT 'squeeze_building'"),
        ("phase_history_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("last_phase_notified", "VARCHAR(32) NULL"),
    ]
    for col, ddl in additions:
        if col not in names:
            connection.execute(
                text(f"ALTER TABLE pump_entry_watches ADD COLUMN {col} {ddl}")
            )
