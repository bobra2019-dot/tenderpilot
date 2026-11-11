#!/usr/bin/env bash
# Публичная ссылка на приложение через Cloudflare Tunnel (без регистрации).
# Временный хост: живёт, пока запущен этот процесс.
#
# Использование:  ./tunnel.sh
# Требуется запущенный сервер на порту 8000 (см. run.sh / start.bat).
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8000}"
BIN="$HOME/bin/cloudflared"

if [ ! -x "$BIN" ]; then
  echo "→ Скачиваю cloudflared…"
  mkdir -p "$HOME/bin"
  OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
  case "$OS" in
    darwin*) ASSET="cloudflared-darwin-amd64.tgz";;
    *)       ASSET="cloudflared-linux-amd64";;
  esac
  if [ "$OS" = "darwin" ]; then
    curl -sL -o /tmp/cloudflared.tgz "https://github.com/cloudflare/cloudflared/releases/latest/download/$ASSET"
    tar -xzf /tmp/cloudflared.tgz -C "$HOME/bin"
  else
    curl -sL -o "$BIN" "https://github.com/cloudflare/cloudflared/releases/latest/download/$ASSET"
  fi
  chmod +x "$BIN"
fi

if ! curl -s -o /dev/null -m 3 "http://127.0.0.1:$PORT/"; then
  echo "Внимание: на порту $PORT сервер не отвечает. Сначала запустите ./run.sh"
  echo "Продолжаю — ссылка появится, но откроется только после старта сервера."
fi

echo "→ Поднимаю туннель. Ссылка появится ниже через 5-15 секунд:"
"$BIN" tunnel --url "http://127.0.0.1:$PORT" --no-autoupdate
