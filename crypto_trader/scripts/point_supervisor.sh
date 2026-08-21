#!/usr/bin/env bash
#
# Переключить supervisor program:crypto_bot на crypto_trader (workspace).
#
#   ./scripts/point_supervisor.sh           # показать команды
#   APPLY=1 ./scripts/point_supervisor.sh   # sudo sed + restart
#
set -euo pipefail

APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BOTS_CONF="${BOTS_CONF:-/etc/supervisor/conf.d/bots.conf}"
APPLY="${APPLY:-0}"
OLD1="/home/appuser/dev/traiding_bot_ema"
NEW="/home/appuser/dev/crypto/crypto_trader"

echo "point crypto_bot → ${NEW}"

cat <<EOF
sudo sed -i \\
  -e 's|${OLD1}/venv/bin/python3|${NEW}/venv/bin/python3|g' \\
  -e 's|${OLD1}/main.py|${NEW}/main.py|g' \\
  -e 's|directory=${OLD1}/|directory=${NEW}|g' \\
  -e 's|PYTHONPATH="${OLD1}"|PYTHONPATH="${NEW}"|g' \\
  -e 's|PATH="${OLD1}/venv/bin:|PATH="${NEW}/venv/bin:|g' \\
  ${BOTS_CONF}
sudo supervisorctl reread && sudo supervisorctl update
sudo supervisorctl restart crypto_bot
sudo supervisorctl status crypto_bot
EOF

if [[ "${APPLY}" == "1" ]]; then
  sudo sed -i \
    -e "s|${OLD1}/venv/bin/python3|${NEW}/venv/bin/python3|g" \
    -e "s|${OLD1}/main.py|${NEW}/main.py|g" \
    -e "s|directory=${OLD1}/|directory=${NEW}|g" \
    -e "s|PYTHONPATH=\"${OLD1}\"|PYTHONPATH=\"${NEW}\"|g" \
    -e "s|PATH=\"${OLD1}/venv/bin:|PATH=\"${NEW}/venv/bin:|g" \
    "${BOTS_CONF}"
  sudo supervisorctl reread
  sudo supervisorctl update
  sudo supervisorctl restart crypto_bot
  sudo supervisorctl status crypto_bot || true
  sleep 2
  ps -ef | awk '/crypto_trader\/main\.py|traiding_bot_ema\/main\.py/ && !/awk/{print}'
fi
