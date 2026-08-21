from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.db.base import Base
from app.db.advisor_seed import (
    migrate_cursors_from_json,
    seed_advisor_tasks_if_empty,
    sync_advisor_task_categories,
)
from app.db.alerts_flags_seed import seed_alerts_flags_if_empty
from app.db.admins_seed import ensure_superadmin_in_admins, seed_admins_if_empty
from app.db.pump_scan_seed import seed_pump_scan_config_if_empty
from app.db.migrate import (
    ensure_advisor_tasks_columns,
    ensure_bot_alerts_flags_columns,
    ensure_pump_entry_watch_columns,
    ensure_scalp_advisor_columns,
    ensure_trading_tasks_columns,
)

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        url = get_settings().database_url
        _engine = create_async_engine(
            url,
            echo=False,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


async def init_db() -> None:
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(ensure_trading_tasks_columns)
        await conn.run_sync(ensure_advisor_tasks_columns)
        await conn.run_sync(ensure_bot_alerts_flags_columns)
        await conn.run_sync(ensure_scalp_advisor_columns)
        await conn.run_sync(ensure_pump_entry_watch_columns)
    async with get_session_factory()() as session:
        await seed_advisor_tasks_if_empty(session)
        await seed_alerts_flags_if_empty(session)
        await seed_pump_scan_config_if_empty(session)
        await seed_admins_if_empty(session)
        await ensure_superadmin_in_admins(session)
        await migrate_cursors_from_json(session)
        await sync_advisor_task_categories(session)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    fac = get_session_factory()
    async with fac() as session:
        yield session
