# Gold Adviser

Самостоятельный Telegram-бот: сканер аномальных минутных свечей **XAU/USD**.

Репозиторий: [github.com/bah677/gold_adviser](https://github.com/bah677/gold_adviser)

> Использует того же Telegram-бота, что и `crypto_adviser`. Одновременно оба polling-процесса запускать нельзя — остановите старый, затем стартуйте `gold_adviser`.

---

## Что умеет

1. **Админ-only доступ** — middleware пропускает только `SUPERADMIN_TELEGRAM_ID` и id из таблицы `admins`
2. **Вкл/выкл** через `/on` `/off` или кнопку в панели `/gold` — без рестарта
3. **Скан каждую минуту в :03 сек (UTC)** минутных свечей XAU/USD
4. **Провайдеры данных** (цепочка):
   - **RealMarketAPI** (приоритет) — `XAUUSD` M1
   - **Twelve Data** (fallback) — `XAU/USD` 1min
5. Берёт последние N свечей (default **30**), сравнивает тело последней закрытой со средним телом остальных
6. Если тело ≥ **×2** среднего (настраивается) → алерт в личку всем админам с `alerts_enabled`
7. Настройки в PostgreSQL + **in-memory cache с TTL**; после изменения через панель настройки **сразу пушатся** в кеш (`settings_cache.push`)

---

## Команды

| Команда | Описание |
|---|---|
| `/gold` `/status` | Панель и статус |
| `/on` `/off` | Включить / выключить сканер |
| `/admins` | Список админов |
| `/admin_add` `/admin_del` | Только супер-админ из `.env` |

Панель: порог ×тела, размер окна, TTL кеша, ручной скан.

---

## Быстрый старт

### 1. БД (один раз, от postgres)

```bash
# scripts/create_db.sql
CREATE DATABASE gold_adviser OWNER traiding_bot_ema_sub;
```

Тот же `DB_USER` / `DB_PASSWORD`, что у `crypto_adviser`, **другое имя БД**.

### 2. Окружение

```bash
cd /home/appuser/dev/crypto/gold_adviser
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env
# заполнить TELEGRAM_BOT_TOKEN, SUPERADMIN, DB_*, REALMARKET_API_KEY / TWELVEDATA_API_KEY
```

### 3. Запуск

```bash
# убедитесь, что crypto_adviser / ema_podpiska остановлены (один токен = один polling)
./venv/bin/python main.py
```

### 4. Supervisor

```bash
sudo cp deploy/supervisor/gold_adviser.conf /etc/supervisor/conf.d/
sudo supervisorctl reread && sudo supervisorctl update
sudo supervisorctl start gold_adviser
```

---

## Настройки runtime (БД `gold_settings`)

| Поле | Default | Описание |
|---|---|---|
| `enabled` | true | Сканер вкл/выкл |
| `body_mult` | 2.0 | Порог: тело ≥ avg × mult |
| `lookback` | 30 | Число M1 свечей |
| `settings_cache_ttl_sec` | 30 | TTL кеша (если не было push) |

После правок через Telegram вызывается `settings_cache.set()` → запись в БД → **мгновенный push** в память.

---

## Структура

```text
gold_adviser/
├── main.py
├── app/
│   ├── bot/           # handlers, admin middleware, panel UI
│   ├── market/        # RealMarket + Twelve Data + provider chain
│   ├── services/      # scan, anomaly, settings cache, notify
│   ├── repository/    # admins, settings, alerts
│   └── db/            # models, session, seed
├── deploy/supervisor/
├── scripts/create_db.sql
└── tests/
```

---

## Тесты

```bash
./venv/bin/python -m unittest tests.test_anomaly -v
```
