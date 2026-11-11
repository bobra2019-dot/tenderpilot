#!/usr/bin/env bash
# Полное восстановление TenderPilot: зависимости + запуск сервера.
# Используйте после перезапуска среды/нового клона: ./bootstrap.sh
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8000}"

python3 -c "import fastapi, uvicorn, httpx, multipart, pypdf" 2>/dev/null || {
  echo "→ Устанавливаю зависимости…"
  pip install -q -r requirements.txt
}

echo "→ Проверяю базу данных…"
python3 -c "
from app import db
db.init_db()
c = db.connect()
n = c.execute('select count(*) c from tenders').fetchone()['c']
a = c.execute('select count(*) c from tenders where analysis is not null').fetchone()['c']
print(f'   закупок: {n}, с ИИ-заключением: {a}')
"

echo "→ Запускаю сервер на http://0.0.0.0:$PORT"
echo "   Вход: owner@zavod.ru / Tender2026"
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
