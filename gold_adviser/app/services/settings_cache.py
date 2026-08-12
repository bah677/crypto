"""In-memory settings cache with TTL + explicit push/invalidate after admin edits."""

from __future__ import annotations

import logging
import time
from dataclasses import replace

from app.db.session import session_scope
from app.repository.settings import GoldRuntimeSettings, load_runtime_settings, update_runtime_settings

log = logging.getLogger(__name__)


class SettingsCache:
    def __init__(self) -> None:
        self._cached: GoldRuntimeSettings | None = None
        self._loaded_at: float = 0.0
        self._generation: int = 0

    @property
    def generation(self) -> int:
        return self._generation

    def invalidate(self) -> None:
        self._cached = None
        self._loaded_at = 0.0
        self._generation += 1
        log.info("gold settings cache invalidated gen=%s", self._generation)

    def push(self, settings: GoldRuntimeSettings) -> GoldRuntimeSettings:
        """Force-push freshly saved settings into cache (after admin edit)."""
        self._cached = settings
        self._loaded_at = time.monotonic()
        self._generation += 1
        log.info(
            "gold settings pushed enabled=%s body_mult=%s lookback=%s ttl=%s gen=%s",
            settings.enabled,
            settings.body_mult,
            settings.lookback,
            settings.settings_cache_ttl_sec,
            self._generation,
        )
        return settings

    async def get(self) -> GoldRuntimeSettings:
        now = time.monotonic()
        ttl = 30.0
        if self._cached is not None:
            ttl = float(max(5, self._cached.settings_cache_ttl_sec))
            if now - self._loaded_at < ttl:
                return self._cached
        async with session_scope() as session:
            settings = await load_runtime_settings(session)
        self._cached = settings
        self._loaded_at = now
        return settings

    async def set(
        self,
        *,
        updated_by: int | None = None,
        enabled: bool | None = None,
        body_mult: float | None = None,
        lookback: int | None = None,
        settings_cache_ttl_sec: int | None = None,
    ) -> GoldRuntimeSettings:
        async with session_scope() as session:
            settings = await update_runtime_settings(
                session,
                updated_by=updated_by,
                enabled=enabled,
                body_mult=body_mult,
                lookback=lookback,
                settings_cache_ttl_sec=settings_cache_ttl_sec,
            )
        return self.push(settings)


settings_cache = SettingsCache()
