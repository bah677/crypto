# crypto

Рабочее пространство для крипто-проектов.

## Структура

```text
crypto/
├── README.md
└── crypto_adviser/     # Telegram-бот: pump scanner, Funding+OI, entry-watch, EMA/SL
```

| Папка | Описание |
|---|---|
| [`crypto_adviser/`](crypto_adviser/) | Текущая сборка советника/сканера (бывший `ema_podpiska`) |

## Быстрый старт (`crypto_adviser`)

```bash
cd crypto_adviser
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env   # если ещё нет .env
./venv/bin/python main.py
```

Подробности — в [`crypto_adviser/README.md`](crypto_adviser/README.md).

## GitHub

Репозиторий: [github.com/bah677/crypto](https://github.com/bah677/crypto)

Код бота лежит в подпапке `crypto_adviser/` (monorepo-layout).
