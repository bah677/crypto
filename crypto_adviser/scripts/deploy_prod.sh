#!/usr/bin/env bash
#
# Упрощённый «деплой» crypto_adviser (EMA для подписчиков).
# Сейчас специально ВЫКЛЮЧЕН — не запускать одновременно с gold_adviser
# (общий Telegram-токен).
#
#   SKIP_RESTART=1 ./scripts/deploy_prod.sh   # только git
#   ./scripts/deploy_prod.sh                  # restart crypto_adviser (если заведён в supervisor)
#
set -euo pipefail

APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CRYPTO_ROOT="$(cd "${APP_ROOT}/.." && pwd)"
SUPERVISOR_NAME="${SUPERVISOR_NAME:-crypto_adviser}"
SKIP_GIT_PUSH="${SKIP_GIT_PUSH:-0}"
# По умолчанию НЕ рестартуем — бот выключен и делит токен с gold
SKIP_RESTART="${SKIP_RESTART:-1}"
GIT_REMOTE_URL="${GIT_REMOTE_URL:-git@github.com:bah677/crypto.git}"
RUN_USER="${DEPLOY_RUN_USER:-${SUDO_USER:-appuser}}"

echo "crypto_adviser deploy (подписчики; default SKIP_RESTART=1)"
echo "  root: ${APP_ROOT}"
echo "  supervisor: ${SUPERVISOR_NAME}"
echo "  ВНИМАНИЕ: общий TG-токен с gold_adviser — не гонять вместе."

if [[ "${SKIP_RESTART}" != "1" ]]; then
  echo "==> supervisorctl restart ${SUPERVISOR_NAME}"
  sudo supervisorctl restart "${SUPERVISOR_NAME}"
  sudo supervisorctl status "${SUPERVISOR_NAME}" || true
else
  echo "==> SKIP_RESTART=1 (подписчицкий бот не трогаем)"
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
