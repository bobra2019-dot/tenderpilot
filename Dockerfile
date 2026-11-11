FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --only-binary=:all: -r requirements.txt

COPY app ./app
COPY web ./web
COPY build_static.py build_single.py README.md ./

RUN mkdir -p data inbox && python -c "from app import db; db.init_db()"

EXPOSE 8000
# один воркер обязателен: планировщик и SQLite живут в процессе
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
