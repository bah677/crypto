#!/usr/bin/env bash
# Синхронизация dev/traiding_bot_ema → /home/appuser/crypto_bot для program:crypto_bot (supervisor).
set -euo pipefail

SRC="${SRC:-/home/appuser/dev/traiding_bot_ema}"
DST="${DST:-/home/appuser/crypto_bot}"

if [[ ! -d "$SRC" ]]; then
  echo "Источник не найден: $SRC" >&2
  exit 1
fi

mkdir -p "$DST"

if [[ -f "$DST/.env" && ! -f "$DST/.env.bak.before_ema_bot" ]]; then
  cp "$DST/.env" "$DST/.env.bak.before_ema_bot"
  echo "Бэкап старого .env → $DST/.env.bak.before_ema_bot"
fi

rsync -a --delete \
  --exclude venv/ \
  --exclude __pycache__/ \
  --exclude .git/ \
  --exclude data/ \
  --exclude '.env' \
  --exclude 'scripts/mt5linux-installer.sh' \
  "$SRC/" "$DST/"

if [[ -f "$SRC/.env" ]]; then
  cp "$SRC/.env" "$DST/.env"
  echo "Скопирован .env из $SRC"
fi

if [[ ! -x "$DST/venv/bin/python3" ]]; then
  echo "Создаю venv в $DST/venv"
  python3 -m venv "$DST/venv"
fi

"$DST/venv/bin/pip" install -U pip -q
"$DST/venv/bin/pip" install -r "$DST/requirements.txt" -q

# Остатки старого crypto_bot (до EMA-бота)
for legacy in tg_bot strategies utils models app.py bybit_client.py config.py readme.md __init__.py; do
  rm -rf "$DST/$legacy"
done

echo "OK: код в $DST"
echo "Проверка импорта:"
(cd "$DST" && "$DST/venv/bin/python3" -c "from app.config import get_settings, PROJECT_ROOT; s=get_settings(); print('root', PROJECT_ROOT); print('mode', s.bot_mode, 'tasks', len(s.parsed_advisor_tasks()))")

echo ""
echo "Supervisor (если есть sudo):"
echo "  sudo cp $SRC/deploy/supervisor/crypto_bot.conf /etc/supervisor/conf.d/crypto_bot.conf"
echo "  sudo supervisorctl reread && sudo supervisorctl update && sudo supervisorctl restart crypto_bot"
echo "  sudo supervisorctl status crypto_bot"
