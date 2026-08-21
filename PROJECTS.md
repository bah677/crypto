# Три проекта в монорепо `bah677/crypto`

Рабочая область: `/home/appuser/dev/crypto` (dev = «прод», отдельного контура нет).

| # | Каталог | Кто | Telegram | Supervisor | Статус |
|---|---------|-----|----------|------------|--------|
| 1 | `gold_adviser/` | Советчик по золоту (XAU M1) | **Общий токен** с №2 | `gold_adviser` | Рабочий |
| 2 | `crypto_adviser/` | EMA/pump для **подписчиков** (без кнопок открытия позиции) | **Общий токен** с №1 | не в проде / выключен | Специально выключен |
| 3 | `crypto_trader/` | EMA/pump **личный** (материнский для №2; есть ордера) | **Отдельный бот** | `crypto_bot` | Сейчас в проде |

## Важно

- **№1 и №2** нельзя запускать одновременно (один `getUpdates` / один токен).
- **№3** — канон для торговых кнопок; №2 — урезанная версия под подписчиков.
- Правки «как у меня в проде» по крипте → **`crypto_trader`**, не `crypto_adviser`.

## Деплой (restart + git push)

```bash
# золото
cd /home/appuser/dev/crypto/gold_adviser && ./scripts/deploy_prod.sh

# личный крипто-бот (прод)
cd /home/appuser/dev/crypto/crypto_trader && ./scripts/deploy_prod.sh

# подписчики (когда включите)
cd /home/appuser/dev/crypto/crypto_adviser && ./scripts/deploy_prod.sh
```

Флаги: `SKIP_GIT_PUSH=1`, `SKIP_RESTART=1`.

Общий пуш: `crypto/scripts/git_push_deploy.sh` → `git@github.com:bah677/crypto.git`.

## Старые пути

- `/home/appuser/dev/traiding_bot_ema` — бывший каталог №3 (после миграции не использовать).
- `/home/appuser/dev/ema_podpiska` — переезд/заглушка, не канон.
