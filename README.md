# crypto

Монорепозиторий трёх ботов (три каталога в `master`, не три git-ветки).

| Каталог | Роль |
|---------|------|
| [`gold_adviser/`](gold_adviser/) | Советчик по золоту |
| [`crypto_adviser/`](crypto_adviser/) | EMA/pump для подписчиков (урезанный; выключен) |
| [`crypto_trader/`](crypto_trader/) | Личный EMA/pump (материнский; в проде) |

Подробности: [`PROJECTS.md`](PROJECTS.md).

Деплой (restart + commit/push):

```bash
./gold_adviser/scripts/deploy_prod.sh
./crypto_trader/scripts/deploy_prod.sh
# подписчики по умолчанию без restart:
./crypto_adviser/scripts/deploy_prod.sh
```
