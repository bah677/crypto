#!/usr/bin/env bash
#
# Упрощённый «деплой» gold_adviser: процесс уже из dev/crypto, rsync не нужен.
#
#   1) sudo supervisorctl restart gold_adviser
#   2) git commit + push монорепо crypto (bah677/crypto)
#
# Запуск:
#   ./scripts/deploy_prod.sh
#   SKIP_GIT_PUSH=1 ./scripts/deploy_prod.sh
#   SKIP_RESTART=1 ./scripts/deploy_prod.sh
#
set -euo pipefail

APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CRYPTO_ROOT="$(cd "${APP_ROOT}/.." && pwd)"
SUPERVISOR_NAME="${SUPERVISOR_NAME:-gold_adviser}"
SKIP_GIT_PUSH="${SKIP_GIT_PUSH:-0}"
SKIP_RESTART="${SKIP_RESTART:-0}"
GIT_REMOTE_URL="${GIT_REMOTE_URL:-git@github.com:bah677/crypto.git}"
RUN_USER="${DEPLOY_RUN_USER:-${SUDO_USER:-appuser}}"

echo "gold_adviser deploy (restart-only)"
echo "  root: ${APP_ROOT}"
echo "  supervisor: ${SUPERVISOR_NAME}"

if [[ "${SKIP_RESTART}" != "1" ]]; then
  echo "==> supervisorctl restart ${SUPERVISOR_NAME}"
  sudo supervisorctl restart "${SUPERVISOR_NAME}"
  sudo supervisorctl status "${SUPERVISOR_NAME}" || true
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
