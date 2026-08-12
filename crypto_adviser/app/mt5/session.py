from __future__ import annotations

"""Канал MT5 на сервере.

- ``MT5_TRANSPORT=local`` — пакет ``MetaTrader5`` в **этом же** Python (обычно Windows).
- ``MT5_TRANSPORT=linux_bridge`` — терминал MT5 под `Wine` + пакет `mt5linux`: на стороне Wine
  запускается ``python -m mt5linux`` (rpyc), Linux-бот подключается по ``MT5LINUX_HOST``/``PORT``.

Установка терминала под Linux: https://www.mql5.com/en/articles/625
"""

import logging
import time

from app.config import get_settings

log = logging.getLogger(__name__)

_mt5_native = None
try:
    import MetaTrader5 as _mt5_native  # type: ignore[assignment]
except ImportError:
    pass

_last_mt5_tasks_warn_ts: float = 0.0
_last_mt5_pkg_warn_ts: float = 0.0
_last_mt5_bridge_warn_ts: float = 0.0


def mt5_transport() -> str:
    return (get_settings().mt5_transport or "local").strip().lower()


def native_mt5_import_ok() -> bool:
    return _mt5_native is not None


def mt5linux_import_ok() -> bool:
    try:
        import mt5linux  # noqa: F401
    except ImportError:
        return False
    return True


def mt5_local_credentials_configured() -> bool:
    s = get_settings()
    return (
        s.mt5_login is not None
        and bool(s.mt5_password)
        and bool(s.mt5_server.strip())
    )


def mt5_connected() -> bool:
    from app.mt5 import runtime

    if not runtime.mt5_runtime_initialized():
        return False
    h = runtime.get_mt5()
    t = h.terminal_info()
    return t is not None and getattr(t, "connected", False)


def _startup_native() -> bool:
    from app.mt5 import runtime

    if not mt5_local_credentials_configured():
        return False
    if not native_mt5_import_ok():
        log.warning(
            "Пакет MetaTrader5 не установлен — режим local недоступен. "
            "На Linux сервере используйте MT5_TRANSPORT=linux_bridge + Wine + mt5linux "
            "(см. https://www.mql5.com/en/articles/625 и scripts/setup_mt5_linux_wine.sh)."
        )
        return False
    assert _mt5_native is not None
    s = get_settings()
    path = (s.mt5_path or "").strip() or None
    if not _mt5_native.initialize(path=path):
        log.error("MT5 initialize() failed: %s", _mt5_native.last_error())
        return False
    ok = _mt5_native.login(
        int(s.mt5_login),
        password=s.mt5_password,
        server=s.mt5_server.strip(),
    )
    if not ok:
        log.error("MT5 login failed: %s", _mt5_native.last_error())
        _mt5_native.shutdown()
        return False
    runtime.set_mt5_handle(_mt5_native)
    log.info("MT5: local, подключено к %s, login=%s", s.mt5_server, s.mt5_login)
    return True


def _startup_linux_bridge() -> bool:
    from app.mt5 import runtime

    if not mt5_local_credentials_configured():
        log.error("MT5 linux_bridge: задайте MT5_LOGIN, MT5_PASSWORD, MT5_SERVER")
        return False
    if not mt5linux_import_ok():
        log.error("Режим linux_bridge: установите пакет mt5linux (pip install mt5linux).")
        return False
    from mt5linux import MetaTrader5

    s = get_settings()
    host = (s.mt5linux_host or "127.0.0.1").strip()
    port = int(s.mt5linux_port)
    try:
        client = MetaTrader5(host=host, port=port)
    except Exception:
        log.exception(
            "MT5 linux_bridge: не удалось подключиться к rpyc %s:%s "
            "(на стороне Wine должен быть запущен: python -m mt5linux)",
            host,
            port,
        )
        return False

    path = (s.mt5_path or "").strip() or None
    kwargs: dict = {
        "login": int(s.mt5_login),
        "password": s.mt5_password,
        "server": s.mt5_server.strip(),
    }
    if path:
        ok = client.initialize(path, **kwargs)
    else:
        ok = client.initialize(**kwargs)
    if not ok:
        log.error("MT5 linux_bridge initialize/login failed: %s", client.last_error())
        try:
            client.shutdown()
        except Exception:
            pass
        return False
    runtime.set_mt5_handle(client)
    log.info("MT5: linux_bridge к %s:%s, сервер %s, login=%s", host, port, s.mt5_server, s.mt5_login)
    return True


def mt5_startup_if_configured() -> bool:
    """
    Инициализация MT5 API в ``app.mt5.runtime`` для режимов local и linux_bridge.
    """
    from app.mt5 import runtime

    runtime.clear_mt5_handle()
    t = mt5_transport()
    if t == "linux_bridge":
        return _startup_linux_bridge()
    if t == "local":
        return _startup_native()
    log.error("Неизвестный MT5_TRANSPORT=%r (ожидается local или linux_bridge)", t)
    return False


def mt5_shutdown_safe() -> None:
    from app.mt5 import runtime

    try:
        if runtime.mt5_runtime_initialized():
            h = runtime.get_mt5()
            try:
                h.shutdown()
            except Exception:
                log.exception("MT5 shutdown")
    finally:
        runtime.clear_mt5_handle()


async def mt5_shutdown_all_async() -> None:
    mt5_shutdown_safe()


def warn_mt5_tasks_no_native_package() -> None:
    global _last_mt5_pkg_warn_ts
    now = time.monotonic()
    if now - _last_mt5_pkg_warn_ts < 60.0:
        return
    _last_mt5_pkg_warn_ts = now
    log.warning(
        "Есть задания MT5 (режим local), но пакет MetaTrader5 недоступен в этом Python. "
        "На сервере Linux: MT5_TRANSPORT=linux_bridge + Wine + mt5linux, см. scripts/setup_mt5_linux_wine.sh"
    )


def warn_mt5_tasks_without_terminal() -> None:
    global _last_mt5_tasks_warn_ts
    now = time.monotonic()
    if now - _last_mt5_tasks_warn_ts < 60.0:
        return
    _last_mt5_tasks_warn_ts = now
    log.warning(
        "Есть задания MT5, но терминал не подключён (terminal_info.connected). "
        "local: запущен ли MT5 на этой машине. linux_bridge: запущены ли терминал в Wine и ``python -m mt5linux``?"
    )


def warn_mt5_linux_bridge_misconfigured() -> None:
    global _last_mt5_bridge_warn_ts
    now = time.monotonic()
    if now - _last_mt5_bridge_warn_ts < 60.0:
        return
    _last_mt5_bridge_warn_ts = now
    log.warning(
        "Есть задания MT5 (linux_bridge), но нет соединения с mt5linux. "
        "В Wine: pip install MetaTrader5 mt5linux, затем python -m mt5linux. "
        "В .env на боте: MT5_TRANSPORT=linux_bridge, MT5LINUX_HOST, MT5LINUX_PORT."
    )
