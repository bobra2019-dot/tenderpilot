#!/usr/bin/env bash
# TenderPilot — установка на VPS одной командой.
#
#   curl -fsSL https://raw.githubusercontent.com/bobra2019-dot/tenderpilot/main/install-vps.sh | bash
#
# Переменные (необязательно):
#   TENDERPILOT_DIR=/opt/tenderpilot   куда ставить
#   PUBLIC_URL=https://tenders.example.ru   адрес для ссылок в уведомлениях
#   PORT=8000                          внешний порт
#
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/bobra2019-dot/tenderpilot.git}"
DIR="${TENDERPILOT_DIR:-/opt/tenderpilot}"
PORT="${PORT:-8000}"
PUBLIC_URL="${PUBLIC_URL:-}"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31mОШИБКА: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "запустите от root: sudo bash -c '...' либо войдите как root"

# ---------- 1. что за система ----------
say "Проверяю систему"
if command -v apt-get >/dev/null 2>&1; then
    PKG=apt
elif command -v dnf >/dev/null 2>&1; then
    PKG=dnf
elif command -v yum >/dev/null 2>&1; then
    PKG=yum
else
    die "неизвестный пакетный менеджер (нужен Debian/Ubuntu или Alma/Rocky)"
fi
ok "пакеты: $PKG, ядро $(uname -r)"

# ---------- 2. базовые пакеты ----------
say "Ставлю git, curl, tar (если их нет)"
if [ "$PKG" = apt ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq git curl ca-certificates tar >/dev/null
else
    $PKG install -y -q git curl ca-certificates tar >/dev/null
fi
ok "готово"

# ---------- 3. docker ----------
say "Проверяю Docker"
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    ok "Docker уже установлен: $(docker --version)"
else
    warn "ставлю Docker через официальный скрипт get.docker.com"
    curl -fsSL https://get.docker.com | sh >/tmp/tp-docker-install.log 2>&1 || {
        tail -20 /tmp/tp-docker-install.log
        die "Docker не установился. Лог: /tmp/tp-docker-install.log"
    }
    systemctl enable --now docker >/dev/null 2>&1 || true
    ok "Docker установлен: $(docker --version)"
fi

# ---------- 4. код ----------
say "Скачиваю TenderPilot в $DIR"
if [ -d "$DIR/.git" ]; then
    git -C "$DIR" fetch --quiet origin main
    git -C "$DIR" reset --hard --quiet origin/main
    ok "обновил существующую установку"
else
    mkdir -p "$(dirname "$DIR")"
    [ -d "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ] && die "$DIR не пуста и это не git-репозиторий"
    git clone --depth 1 --quiet "$REPO_URL" "$DIR"
    ok "склонировал"
fi
cd "$DIR"
mkdir -p data inbox
[ -f .env ] || cp .env.example .env
if [ -n "$PUBLIC_URL" ]; then
    sed -i "s|^PUBLIC_URL=.*|PUBLIC_URL=$PUBLIC_URL|" .env
fi
ok "папки data/ и inbox/ готовы"

# ---------- 5. запуск ----------
say "Собираю контейнер (2-4 минуты, качает зависимости)"
docker compose up -d --build 2>&1 | tail -5
ok "контейнер поднят"

# ---------- 6. ждём ответа ----------
say "Жду, пока приложение ответит"
READY=0
for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${PORT}/api/registration" >/dev/null 2>&1; then READY=1; break; fi
    sleep 2
done
[ "$READY" = "1" ] || { docker compose logs --tail 40; die "приложение не ответило за 60 секунд"; }
ok "приложение отвечает"

# ---------- 7. файрвол ----------
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    say "Открываю порты в ufw"
    ufw allow "${PORT}/tcp" >/dev/null 2>&1 || true
    ufw allow 80/tcp  >/dev/null 2>&1 || true
    ufw allow 443/tcp >/dev/null 2>&1 || true
    ok "порты ${PORT}, 80, 443 открыты"
elif command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-port="${PORT}/tcp" >/dev/null 2>&1 || true
    firewall-cmd --reload >/dev/null 2>&1 || true
    ok "порт ${PORT} открыт в firewalld"
else
    warn "файрвол не активен — проверьте панель хостера (Security Group), что порт ${PORT} открыт"
fi

# ---------- 8. итог ----------
IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
cat <<EOF

$(printf '\033[1;32m')============================================================
 TenderPilot установлен
============================================================$(printf '\033[0m')

 Открыть:  http://${IP}:${PORT}
 Вход:     owner@zavod.ru  /  demo1234   <-- смените пароль сразу

 Папки данных:  $DIR/data (база) и $DIR/inbox (выгрузки площадок)
 Логи:          cd $DIR && docker compose logs -f --tail 100
 Обновление:    cd $DIR && git pull && docker compose up -d --build
 Остановить:    cd $DIR && docker compose down

 Дальше:
   1. «Настройки ИИ» — вставить ключ (OpenAI/Anthropic/DeepSeek/OpenRouter)
   2. «Профиль завода» — номенклатура, ОКПД2, регионы, экономика
   3. «Источники данных» — включить ЕИС, задать слова и прокси, «Собрать сейчас»
   4. «Администрирование» — подтверждать заявки, создавать пользователей

 Домен и HTTPS — по инструкции: $DIR/УСТАНОВКА-НА-СЕРВЕР.md (раздел 5)
EOF
