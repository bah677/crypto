# crypto_trader

Личный EMA/pump-бот (есть ордера). Отдельный Telegram-бот.  
Материнский код для урезанного `crypto_adviser` (подписчики).

См. [`../PROJECTS.md`](../PROJECTS.md).

```bash
./scripts/deploy_prod.sh
# один раз (нужен sudo), если supervisor ещё на traiding_bot_ema:
APPLY=1 ./scripts/point_supervisor.sh
```
