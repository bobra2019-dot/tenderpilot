#!/usr/bin/env bash
# Заливка TenderPilot в ваш GitHub-репозиторий.
#
# Использование:
#   ./push-to-github.sh https://github.com/ВАШ_ЛОГИН/tenderpilot.git
#   ./push-to-github.sh git@github.com:ВАШ_ЛОГИН/tenderpilot.git      # по SSH-ключу
#
# Требуется: установленный git и права на запись в репозиторий.
# Если репозитория ещё нет — создайте пустой на github.com (без README), затем запустите скрипт.

set -e
cd "$(dirname "$0")"

REPO_URL="${1:-}"
if [ -z "$REPO_URL" ]; then
  echo "Укажите адрес репозитория."
  echo "Пример: ./push-to-github.sh https://github.com/username/tenderpilot.git"
  read -r -p "URL репозитория: " REPO_URL
fi
[ -z "$REPO_URL" ] && { echo "URL не задан, выходим."; exit 1; }

if ! command -v git >/dev/null 2>&1; then
  echo "git не найден. Установите: https://git-scm.com/downloads"; exit 1
fi

# 1. инициализация репозитория (если ещё не создан)
if [ ! -d .git ]; then
  echo "→ git init"
  git init -q -b main 2>/dev/null || { git init -q && git checkout -q -b main; }
fi

# 2. имя и почта автора — только если не заданы глобально
if ! git config user.email >/dev/null 2>&1; then
  git config user.email "tenderpilot@local"
  git config user.name "TenderPilot"
  echo "→ подставлены локальные user.email/user.name (поправьте: git config user.email ...)"
fi

# 3. удалённый репозиторий
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REPO_URL"
else
  git remote add origin "$REPO_URL"
fi
echo "→ origin = $REPO_URL"

# 4. коммит и пуш
git add -A
if git diff --cached --quiet; then
  echo "→ изменений нет, коммит не нужен"
else
  git commit -q -m "TenderPilot: CRM автопастера тендеров с ИИ-анализом ТЗ и расчётом маржи"
  echo "→ создан коммит"
fi

echo "→ пуш в main…"
git branch -M main
git push -u origin main

echo
echo "Готово. Репозиторий: ${REPO_URL%.git}"
echo "Открыть: ${REPO_URL%.git}"
