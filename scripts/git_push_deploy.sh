#!/usr/bin/env bash
# Пуш монорепозитория crypto (crypto_adviser + gold_adviser) в GitHub.
#
# Вызывается из */scripts/deploy_prod.sh или вручную:
#   ./scripts/git_push_deploy.sh
#
set -euo pipefail

CRYPTO_ROOT="${CRYPTO_ROOT:-/home/appuser/dev/crypto}"
GIT_REMOTE_URL="${GIT_REMOTE_URL:-git@github.com:bah677/crypto.git}"
GIT_BRANCH="${GIT_BRANCH:-master}"
GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-bah677}"
GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-bah677@users.noreply.github.com}"
GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME:-$GIT_AUTHOR_NAME}"
GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL:-$GIT_AUTHOR_EMAIL}"

die() { echo "ERROR [git_push]: $*" >&2; exit 1; }

[[ -d "${CRYPTO_ROOT}" ]] || die "Нет каталога ${CRYPTO_ROOT}"
cd "${CRYPTO_ROOT}"

command -v git >/dev/null || die "нужен git"

# Safety: never commit secrets
for envf in crypto_adviser/.env gold_adviser/.env crypto_trader/.env; do
  if [[ -f "${envf}" ]] && ! git check-ignore -q "${envf}" 2>/dev/null; then
    if [[ -d .git ]]; then
      die "${envf} не в .gitignore — пуш отменён"
    fi
  fi
done

[[ -d .git ]] || die "В ${CRYPTO_ROOT} нет .git"

git remote get-url origin &>/dev/null || git remote add origin "${GIT_REMOTE_URL}"
git remote set-url origin "${GIT_REMOTE_URL}"

git add -A

if git diff --cached --quiet; then
  echo "==> [git] Нет изменений для коммита — только push (если есть непушенные)"
else
  msg="deploy $(date +%Y-%m-%d_%H:%M:%S)"
  GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME}" GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL}" \
  GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME}" GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL}" \
    git commit -m "${msg}"
  echo "==> [git] Коммит: ${msg}"
fi

echo "==> [git] fetch + rebase origin/${GIT_BRANCH}"
git fetch origin "${GIT_BRANCH}"
if ! git rev-parse --verify "origin/${GIT_BRANCH}" >/dev/null 2>&1; then
  echo "==> [git] remote-ветка origin/${GIT_BRANCH} пока не существует — push создаст её"
elif ! git merge-base --is-ancestor "origin/${GIT_BRANCH}" HEAD 2>/dev/null; then
  GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME}" GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL}" \
  GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME}" GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL}" \
    git rebase "origin/${GIT_BRANCH}" || die "rebase не удался — разрешите конфликты и запустите снова"
fi

echo "==> [git] push origin ${GIT_BRANCH}"
if git push -u origin "${GIT_BRANCH}"; then
  echo "==> [git] OK: ${GIT_REMOTE_URL} (ветка ${GIT_BRANCH})"
else
  echo "!!! [git] push не удался для ${GIT_REMOTE_URL}" >&2
  exit 1
fi
