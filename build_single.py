# -*- coding: utf-8 -*-
"""Собирает tenderpilot_single.py — один файл, который разворачивает и запускает проект."""
import base64, io, os, textwrap, zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
INCLUDE = ["app", "web", "requirements.txt", "run.sh", "bootstrap.sh", "README.md", "КАК-ОТКРЫТЬ.md"]
SKIP_EXT = (".pyc",)

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    for item in INCLUDE:
        path = os.path.join(ROOT, item)
        if os.path.isfile(path):
            z.write(path, item)
        else:
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for f in files:
                    if f.endswith(SKIP_EXT):
                        continue
                    full = os.path.join(root, f)
                    z.write(full, os.path.relpath(full, ROOT))
payload = base64.b64encode(buf.getvalue()).decode()

script = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TenderPilot — один файл: разворачивает проект и запускает сервер.

Как пользоваться:
  1. Сохраните этот текст как файл  tenderpilot.py  (кодировка UTF-8)
  2. В терминале:  python tenderpilot.py
  3. Откройте http://localhost:8000   вход: owner@zavod.ru / demo1234

Всё содержимое проекта (бэкенд, фронтенд, демо-данные) зашито внутрь.
Зависимости (fastapi, uvicorn, httpx, pypdf) поставятся автоматически.
"""
import base64
import json
import os
import subprocess
import sys
import zipfile

PAYLOAD = "%%PAYLOAD%%"
TARGET = os.path.join(os.getcwd(), "TenderPilot")
PORT = os.environ.get("PORT", "8000")


def main():
    print("TenderPilot: распаковка проекта в", TARGET)
    os.makedirs(TARGET, exist_ok=True)
    with zipfile.ZipFile(io_bytes()) as z:
        z.extractall(TARGET)
    print("Готово:", len(os.listdir(TARGET)), "элементов")

    need = ["fastapi", "uvicorn", "httpx", "multipart", "pypdf"]
    missing = []
    for m in need:
        try:
            __import__(m)
        except ImportError:
            missing.append(m)
    if missing:
        print("Устанавливаю зависимости:", ", ".join(missing))
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "-r",
                               os.path.join(TARGET, "requirements.txt")])

    print("Запускаю сервер: http://localhost:%s  (вход owner@zavod.ru / demo1234)" % PORT)
    print("Остановить: Ctrl+C")
    os.chdir(TARGET)
    os.execv(sys.executable, [sys.executable, "-m", "uvicorn", "app.main:app",
                              "--host", "0.0.0.0", "--port", PORT])


def io_bytes():
    import io
    return io.BytesIO(base64.b64decode(PAYLOAD))


if __name__ == "__main__":
    main()
'''.replace("%%PAYLOAD%%", payload)

out = os.path.join(ROOT, "tenderpilot_single.py")
with open(out, "w", encoding="utf-8") as f:
    f.write(script)
print("собран:", out, round(os.path.getsize(out) / 1024, 1), "КБ")
