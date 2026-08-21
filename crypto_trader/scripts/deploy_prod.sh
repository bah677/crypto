#!/usr/bin/env bash
#
# «Деплой» crypto_trader (личный EMA/pump-бот, program:crypto_bot).
# Процесс из /home/appuser/dev/crypto/crypto_trader — rsync не нужен.
#
#   1) sudo supervisorctl restart crypto_bot
#   2) git commit + push монорепо crypto
#
#   ./scripts/deploy_prod.sh
#   SKIP_GIT_PUSH=1 ./scripts/deploy_prod.sh
#
set -euo pipefail

APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CRYPTO_ROOT="$(cd "${APP_ROOT}/.." && pwd)"
SUPERVISOR_NAME="${SUPERVISOR_NAME:-crypto_bot}"
SKIP_GIT_PUSH="${SKIP_GIT_PUSH:-0}"
SKIP_RESTART="${SKIP_RESTART:-0}"
GIT_REMOTE_URL="${GIT_REMOTE_URL:-git@github.com:bah677/crypto.git}"
RUN_USER="${DEPLOY_RUN_USER:-${SUDO_USER:-appuser}}"

echo "crypto_trader deploy (restart-only)"
echo "  root: ${APP_ROOT}"
echo "  supervisor: ${SUPERVISOR_NAME}"

if [[ ! -x "${APP_ROOT}/venv/bin/python3" ]]; then
  echo "ERROR: нет ${APP_ROOT}/venv/bin/python3" >&2
  exit 1
fi

if [[ "${SKIP_RESTART}" != "1" ]]; then
  echo "==> supervisorctl restart ${SUPERVISOR_NAME}"
  sudo supervisorctl restart "${SUPERVISOR_NAME}"
  sudo supervisorctl status "${SUPERVISOR_NAME}" || true
  # sanity: процесс из этого дерева
  sleep 1
  if ps -ef | awk -v p="${APP_ROOT}/main.py" '$0 ~ p && !/awk/ {found=1} END{exit !found}'; then
    echo "==> OK: процесс из ${APP_ROOT}"
  else
    echo "WARN: после рестарта не вижу ${APP_ROOT}/main.py — проверьте bots.conf" >&2
    ps -ef | awk '/main\.py/ && /traiding_bot_ema|crypto_trader|crypto_adviser/ && !/awk/{print}' || true
  fi
else
  echo "==> SKIP_RESTART=1"
fi

if [[ "${SKIP_GIT_PUSH}" != "1" ]]; then
  echo ""
  echo "==> [git] Обновление GitHub монорепозитория crypto..."
  if [[ "$(id -u)" -eq 0 ]]; then
    sudo -u "${RUN_USER}" env CRYPTO_ROOT="${CRYPTO_ROOT}" GIT_REMOTE_URL="${GIT_REMOTE_URL}" \
      bash "${CRYPTO_ROOT}/scripts/git_push_deploy.sh"
  else
    env CRYPTO_ROOT="${CRYPTO_ROOT}" GIT_REMOTE_URL="${GIT_REMOTE_URL}" \
      bash "${CRYPTO_ROOT}/scripts/git_push_deploy.sh"
  fi
fi

echo "Готово."
