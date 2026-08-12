# crypto

Рабочее пространство для крипто-проектов.

## Структура

```text
crypto/
├── README.md
├── crypto_adviser/     # pump / Funding+OI / entry-watch
└── gold_adviser/       # XAU/USD M1 anomaly (отдельный repo)
```

| Папка | Описание |
|---|---|
| [`crypto_adviser/`](crypto_adviser/) | Crypto pump / Funding+OI / entry-watch |
| [`gold_adviser/`](gold_adviser/) | XAU/USD M1 anomaly scanner (отдельный GitHub + supervisor) |

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
