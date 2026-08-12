# Crypto Telegram Bot

Многомодульный Telegram-бот для крипто-трейдинга на **Bybit linear**: сканер пампов, EMA-сигналы, funding, управление рисками и опциональная автоторговля.

Репозиторий: [github.com/bah677/crypto](https://github.com/bah677/crypto)

> **Важно:** это инструмент для сигналов и автоматизации. Не финансовый совет. Торговля криптой несёт высокий риск потери капитала.

---

## Содержание

1. [Что умеет бот](#что-умеет-бот)
2. [Архитектура](#архитектура)
3. [Структура репозитория](#структура-репозитория)
4. [Pump scanner (основной модуль)](#pump-scanner-основной-модуль)
5. [Funding + OI Trajectory](#funding--oi-trajectory)
6. [Entry Watch — слежение до входа](#entry-watch--слежение-до-входа)
7. [Другие модули](#другие-модули)
8. [Требования](#требования)
9. [Быстрый старт](#быстрый-старт)
10. [Конфигурация `.env`](#конфигурация-env)
11. [Команды Telegram](#команды-telegram)
12. [База данных](#база-данных)
13. [Планировщик (APScheduler)](#планировщик-apscheduler)
14. [Деплой через Supervisor](#деплой-через-supervisor)
15. [Тесты](#тесты)
16. [Безопасность](#безопасность)
17. [Типичные проблемы](#типичные-проблемы)

---

## Что умеет бот

| Модуль | Назначение |
|---|---|
| **Pump scanner** | Ищет импульсные пампы на Bybit linear, фильтрует по даунтренду / OI / climax / funding |
| **Funding + OI Trajectory** | Композитный индикатор траектории фандинга и Open Interest для fade-входа |
| **Entry Watch** | Кнопка «👀 Следить до входа»: фазовый мониторинг + повторный анализ DeepSeek |
| **DeepSeek** | LLM-анализ pump-алертов и re-eval точки входа |
| **EMA Advisor** | Сигналы по кроссу EMA на 5/15/30/60m |
| **Funding scan** | Топ альтов с экстремальным годовым фандингом |
| **EMA SL / SL Follow** | Уровни стопа и автоследование SL на Bybit |
| **ATR Pullback / Scalp** | Отдельные стратегии входа |
| **Price spike** | Алерты на аномальные минутные ходы |
| **Trading mode** | Опциональные ордера на Bybit (+ MT5 на Windows) |

Режимы:

- `BOT_MODE=advisor` — только сигналы в Telegram (рекомендуется для старта)
- `BOT_MODE=trading` — автоторговля
- `PUMP_ONLY_MODE=1` — в планировщике только pump-сканер / entry-watch / outcome eval

---

## Архитектура

```text
Telegram (aiogram 3)
        │
        ▼
   handlers/  ──►  services/  ──►  bybit REST / DeepSeek / LunarCrush
        │              │
        ▼              ▼
   PostgreSQL ◄── repository/ ◄── models + migrate
        │
   APScheduler (cron / interval jobs в main.py)
```

- **Handlers** — команды и callback-кнопки Telegram
- **Services** — бизнес-логика и фоновые тики
- **Pump scan** — детекция импульсов, контекст рынка, графики, параметры
- **Repository** — SQLAlchemy async CRUD
- **Bybit** — REST-клиент с приоритетами и throttling (чтобы фон не душил интерактив)

---

## Структура репозитория

```text
.
├── main.py                 # Точка входа: бот + scheduler
├── requirements.txt
├── .env.example            # Шаблон секретов (без реальных ключей)
├── app/
│   ├── config.py           # Pydantic Settings из .env
│   ├── bot/                # Handlers, middleware, help
│   ├── bybit/              # REST, throttle, kline cache
│   ├── db/                 # models, session, migrate, seeds
│   ├── indicators/         # EMA, ATR, Bollinger, …
│   ├── market/             # Funding math, Binance rank, LunarCrush
│   ├── mt5/                # Опционально (Windows)
│   ├── pump_scan/          # Детекция pump, funding/OI, entry-watch plan
│   ├── repository/         # Доступ к БД
│   ├── services/           # Фоновые сервисы и тики
│   ├── advisor/            # EMA advisor logic
│   ├── atr_pullback/
│   └── scalp_advisor/
├── deploy/supervisor/      # Пример unit для supervisord
├── scripts/                # Деплой, ретро-скрипты, init_db
└── tests/                  # Unit-тесты
```

В репозиторий **не** входят: `.env`, `venv/`, `logs/`, `data/`, `__pycache__/`, IDE-папки.

---

## Pump scanner (основной модуль)

Сканер ищет импульсные движения на пуле ликвидных linear-пар Bybit и формирует алерты для стратегии **pump-in-downtrend fade** (шорт после выжимания шортистов / локального пампа в даунтренде).

### Что приходит в алерте

- Символ, ТФ, ход %, объём ×
- Контекст даунтренда (% от хая, дни)
- OI-анализ (сквиз шортов / new money)
- Композитный тег **Funding + OI Trajectory** (всегда)
- Дистанция до EMA50 в ATR
- EMA 1D / 1W и позиция цены импульса
- Галерея графиков 1W + 1D + 5m
- DeepSeek-мнение (если включено)
- Кнопки: ордер / EMA-будильник / **👀 Следить до входа**

### Настройки

Через `/pump` в Telegram (группы параметров: universe, detect, downtrend, oi, funding_roc, **funding_oi**, climax, …).

Ключевые идеи фильтров:

- **Downtrend context** — памп на монете, уже сильно упавшей от хая
- **OI analysis** — штраф/бонус/hard-block за «новые деньги» vs squeeze
- **Funding RoC** — мгновенная динамика funding (отдельный модуль)
- **Funding + OI Trajectory** — *историческая* траектория (не дублирует RoC)

---

## Funding + OI Trajectory

Модуль: `app/pump_scan/funding_oi_trajectory.py`

Смотрит не «фандинг сейчас», а **как он шёл** за lookback-окно, плюс тренд Open Interest.

### Состояния funding

| State | Смысл |
|---|---|
| `no_extreme` | Экстремума нет — композитный сигнал слабый |
| `extending` | Сквиз ещё усиливается |
| `peak_reversing` | После пика фандинг разворачивается — зона интереса для fade |
| `normalized` | Уже остыло |
| `unknown` | Данных нет |

### OI trend

`rising` / `falling` / `flat` / `unknown`

### Что даёт модуль

1. **Строка в алерте** — всегда (даже если сигнала нет, явно пишет об этом)
2. **Score multiplier** — влияет на приоритет hit’а в сканере
3. **Метрики для Entry Watch** — те же поля использует вотчлист

Confidence зависит от funding interval Bybit (1h / 4h / 8h): чем реже interval, тем осторожнее интерпретация.

---

## Entry Watch — слежение до входа

Когда DeepSeek (или вы) считаете, что **рано шортить**, но сетап хороший — жмёте **👀 Следить до входа**.

### Как работает

1. LLM (или default plan) задаёт **watch plan**: условия `all_of` / `any_of` / `invalidate_if` из фиксированного каталога метрик
2. Бот каждые N секунд (`PUMP_ENTRY_WATCH_INTERVAL_SEC`) снимает метрики с Bybit
3. Классифицирует **фазу сквиза**:
   - `squeeze_building` — сквиз разгоняется
   - `squeeze_deep` — глубокий сквиз (рост цены сам по себе **не** снимает вотч)
   - `at_resistance` — у сильной EMA
   - `capitulation` — фандинг развернулся, OI ещё держится
   - `entry_ready` — фандинг разворачивается + OI сдаёт
   - `cooled` — импульс выдохся
4. На смене фазы / срабатывании плана — повторный анализ DeepSeek **с историей** предыдущих заключений
5. Если `entry_ok` — отдельный алерт «окно входа», вотч закрывается

### Команды

- `/pump_watches` — список активных слежений, снятие 🔕

### Почему не инвалидируем по росту цены

В deep squeeze цена может уйти сильно выше импульса (к EMA100/200). Это часть сценария капитуляции шортов. Поэтому hard-stop вида `price_vs_impulse_high_pct >= X` убран; вместо него — **фазы + high watermark + план по funding/OI**.

Файлы:

- `app/pump_scan/entry_watch_plan.py` — план, фазы, русские тексты
- `app/pump_scan/entry_watch_metrics.py` — снимок метрик
- `app/services/pump_entry_watch.py` — тик и уведомления
- `app/bot/handlers/pump_entry_watch.py` — кнопки / команда
- `app/repository/pump_entry_watch.py` — БД

---

## Другие модули

### EMA Advisor

Задания `/task_add` → `/tasks`. Сигнал на закрытой свече при кроссе EMA. Топики группы или личка.

### Funding scan

Cron `:55` MSK. Топ альтов с |годовой funding| выше порога.

### SL EMA / SL Follow / SL anom close

Отчёты по уровням стопа, автоперенос SL на Bybit, закрытие по аномальному минутному телу.

### ATR Pullback / Scalp M5/M1

Отдельные стратегии с виртуальным (или реальным) трейлом.

### Price spike

Алерт, если ход 1m аномально больше среднего фона.

---

## Требования

- Python **3.11+** (проверено на 3.12)
- PostgreSQL **14+**
- Telegram Bot Token ([@BotFather](https://t.me/BotFather))
- Bybit API key/secret (для market data; для trading — с правами на торговлю)
- Опционально: DeepSeek API, LunarCrush API

---

## Быстрый старт

### 1. Клонировать

```bash
git clone git@github.com:bah677/crypto.git
cd crypto/crypto_adviser
```

### 2. Виртуальное окружение

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

### 3. PostgreSQL

```sql
CREATE USER crypto_user WITH PASSWORD 'strong_password';
CREATE DATABASE crypto_bot OWNER crypto_user;
```

### 4. Конфиг

```bash
cp .env.example .env
# отредактируйте .env — токены, БД, ключи
```

### 5. Инициализация БД

При старте `main.py` вызывается `init_db()` (создание таблиц + миграции колонок).  
Опционально:

```bash
./venv/bin/python scripts/init_db.py
```

### 6. Запуск

```bash
./venv/bin/python main.py
```

В Telegram: `/start`, `/help`, `/pump`.

---

## Конфигурация `.env`

Полный шаблон — в [`.env.example`](.env.example).

Минимум для advisor + pump:

| Переменная | Описание |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен бота |
| `SUPERADMIN_TELEGRAM_ID` | Ваш Telegram user id |
| `BYBIT_API_KEY` / `BYBIT_API_SECRET` | Ключи Bybit |
| `DATABASE_URL` или `DB_*` | PostgreSQL |
| `BOT_MODE` | `advisor` или `trading` |
| `PUMP_SCAN_ENABLED` | `1` |
| `DEEPSEEK_API_KEY` | Для анализа алертов |
| `PUMP_ENTRY_WATCH_ENABLED` | `1` для вотчлиста |

Группа алертов (forum topics) настраивается через `TELEGRAM_ALERTS_CHAT_ID` и `TELEGRAM_ALERTS_TOPIC_*`.

Для «только пампы» без остальных мониторов:

```env
PUMP_ONLY_MODE=1
PUMP_SCAN_ENABLED=1
PUMP_ALERTS_TO_PRIVATE=1
```

---

## Команды Telegram

### Общие

| Команда | Описание |
|---|---|
| `/help` | Полная справка |
| `/status` | Статус и задания |
| `/cancel` | Отмена FSM-мастера |
| `/alerts` | Вкл/выкл автоалертов в топики |

### Pump

| Команда | Описание |
|---|---|
| `/pump` | Сканер, пул, параметры |
| `/pump_fade` | A/B: downtrend_mode + OI hard block |
| `/pump_alarms` | Личные EMA-будильники |
| `/pump_watches` | Слежение до окна входа |
| `/test_order` | Тестовый алерт с кнопками |

### EMA / риск

| Команда | Описание |
|---|---|
| `/task_add` · `/tasks` | Задания EMA advisor |
| `/zones` | Зоны EMA |
| `/sl` | Уровни SL EMA |
| `/sl_follow` · `/sl_follow_list` | Авто-SL на позициях |
| `/watch_add` · `/watch_list` | Мониторинг цены / spike |
| `/funding_scan` | Ручной funding scan |

### Стратегии

| Команда | Описание |
|---|---|
| `/atr_add` · `/atr_tasks` | ATR Pullback |
| `/scalp_add` · `/scalp_tasks` | Scalp M5/M1 |
| `/sl_anom_follow` · `/sl_anom_list` | Закрытие по аномалии |

Админ-команды зависят от таблицы `admins` и `SUPERADMIN_TELEGRAM_ID`.

---

## База данных

SQLAlchemy 2 async + asyncpg.

Основные сущности (неполный список):

- задания advisor / atr / scalp
- конфиг pump-сканера
- outcomes pump-алертов
- EMA-будильники
- **entry watch** + suggestions (план, фазы, high watermark, history LLM)
- флаги алертов, админы, подписчики (если используются)

Миграции «мягкие»: `app/db/migrate.py` добавляет недостающие колонки при старте (`ensure_*`).

---

## Планировщик (APScheduler)

Задаётся в `main.py`. Примеры:

| Job | Когда |
|---|---|
| `pump_universe_refresh` | каждый час `:00` MSK |
| `pump_scan` | каждые 5 мин (`:01,:06,…`) |
| `pump_slow_tf_scan` | `:08` |
| `pump_entry_watch` | каждые `PUMP_ENTRY_WATCH_INTERVAL_SEC` (default 180с) |
| `pump_outcome_eval` | каждые 10 мин |
| `funding_scan` | `:55` MSK |
| `strategy_tick` | каждые 2с (EMA advisor) |
| SL / spike / atr / scalp | по своим cron |

Фоновые Bybit-запросы идут через `background_request_scope`, чтобы не блокировать интерактивные команды.

---

## Деплой через Supervisor

Пример unit: [`deploy/supervisor/crypto_adviser.conf`](deploy/supervisor/crypto_adviser.conf)

```bash
# один раз от root
sudo cp deploy/supervisor/crypto_adviser.conf /etc/supervisor/conf.d/
sudo supervisorctl reread && sudo supervisorctl update
sudo supervisorctl status crypto_adviser
```

Логи supervisor — в `/var/log/supervisor/…`  
Логи приложения — в локальной папке `logs/` (в git не коммитится).

---

## Тесты

```bash
./venv/bin/python -m unittest tests.test_entry_watch_plan -v
./venv/bin/python -m unittest tests.test_funding_oi_trajectory -v
./venv/bin/python -m unittest discover -s tests -v
```

Покрытие unit-тестами в первую очередь: ATR, trend context, funding/OI trajectory, entry-watch plan / фазы.

---

## Безопасность

- **Не коммитьте** `.env`, ключи API, дампы БД, логи
- Bybit ключи с минимально нужными правами; для advisor достаточно read market (+ positions если смотрите SL)
- DeepSeek / LunarCrush ключи — только в `.env`
- В публичном репозитории лежат только `.env.example` и код
- Callback-кнопки из групп фильтруются middleware; для pump-кнопок нужен `/start` у бота в личке

---

## Типичные проблемы

| Симптом | Что проверить |
|---|---|
| Бот молчит | `TELEGRAM_BOT_TOKEN`, сеть, `supervisorctl status`, `logs/` |
| Нет pump-алертов | `PUMP_SCAN_ENABLED`, пул `/pump`, cooldown, Bybit API |
| Нет тега Funding+OI | модуль включён в params (`funding_oi`); при `unknown` тег всё равно пишется явно |
| Кнопка «Следить» не реагирует | middleware / личка; смотрите `/pump_watches` |
| Вотч снялся рано | старый план с `price_vs_impulse_high_pct`; новый default без hard invalidate |
| DeepSeek пустой | `DEEPSEEK_API_KEY`, `DEEPSEEK_ENABLED` |
| Ошибки БД про колонку | перезапуск `main.py` (migrate) или `scripts/init_db.py` |
| API throttle / бот тормозит | load сервера, `PUMP_ONLY_MODE`, интервалы тиков |

---

## Лицензия / дисклеймер

Код предоставляется as-is для личного использования и исследований.  
Автор не несёт ответственности за торговые убытки, баны биржи или утечки ключей при неверном деплое.

---

## Roadmap идей

- Больше фазовых уведомлений / web-дашборд вотчлиста
- Бэктест Funding+OI Trajectory на истории алертов
- Разделение «subscription-only» и «full trading» сборками
