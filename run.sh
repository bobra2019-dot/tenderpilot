#!/usr/bin/env bash
# Запуск TenderPilot: http://localhost:8000
# Если зависимости ещё не установлены — они поставятся автоматически.
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8000}"
python3 -c "import fastapi, uvicorn, httpx, multipart, pypdf" 2>/dev/null || {
  echo "→ Устанавливаю зависимости…"
  pip install -q -r requirements.txt
}
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
