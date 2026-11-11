# -*- coding: utf-8 -*-
"""SQLite-хранилище: схема, инициализация, сиды."""
import hashlib
import json
import os
import sqlite3
import secrets
from datetime import datetime, timedelta, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tenderpilot.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  pass_hash TEXT NOT NULL,
  name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS company (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tenders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ext_id TEXT,
  platform TEXT,
  law TEXT,
  category TEXT,
  title TEXT,
  customer TEXT,
  customer_inn TEXT,
  region TEXT,
  okpd TEXT,
  qty REAL,
  unit TEXT,
  sum REAL,
  published TEXT,
  deadline TEXT,
  delivery_days INTEGER,
  payment_terms TEXT,
  bid_security_pct REAL,
  contract_security_pct REAL,
  penalty TEXT,
  description TEXT,
  tz_text TEXT,
  requirements TEXT,
  files TEXT,
  source_url TEXT,
  ingested INTEGER DEFAULT 0,
  stage TEXT DEFAULT 'new',
  note TEXT,
  analysis TEXT,
  analysis_source TEXT,
  analyzed_at TEXT,
  manual INTEGER DEFAULT 0,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tender_id INTEGER NOT NULL,
  type TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  text TEXT NOT NULL
);
"""

DEFAULT_COMPANY = {
    "name": "",
    "inn": "",
    "region": "",
    "site": "",
    "contact": "",
    "experience_years": 0,
    "categories": [],
    "products": "",
    "okpd": [],
    "capacity_note": "",
    "max_order_sum": 0,
    "min_order_sum": 0,
    "lead_days": 30,
    "certificates": [],
    "licenses": [],
    "delivery_regions": [],
    "references": "",
    "economics": {
        "tax_mode": "vat",
        "material_pct": 42,
        "labor_pct": 10,
        "overhead_pct": 12,
        "logistics_pct": 5,
        "min_margin_pct": 16,
        "target_margin_pct": 24,
        "bg_rate_pct": 3.5,
        "risk_reserve_pct": 3,
    },
    "scoring": {
        "w_okpd": 30,
        "w_keywords": 25,
        "w_region": 10,
        "w_sum": 15,
        "w_deadline": 10,
        "w_certificates": 10,
    },
    "onboarding_done": False,
}

DEMO_COMPANY = {
    "name": "ООО «ПромАрматура» (демо-профиль)",
    "inn": "5260123456",
    "region": "Нижегородская обл.",
    "site": "promarmatura.ru",
    "contact": "Иванов И.И., начальник тендерного отдела, +7 (831) 200-00-00",
    "experience_years": 12,
    "categories": ["valves", "metal"],
    "products": "задвижки чугунные и стальные Ду 50-600, затворы поворотные дисковые, краны шаровые, "
                "клапаны обратные и предохранительные, фланцы, крепеж, металлоконструкции",
    "okpd": ["28.14.13", "28.14.20", "25.94", "28.14"],
    "capacity_note": "Два механообрабатывающих цеха, литейный участок, испытательный стенд до Ду 600, "
                     "собственная покраска и упаковка. Загрузка ~70%.",
    "max_order_sum": 30000000,
    "min_order_sum": 500000,
    "lead_days": 40,
    "certificates": ["ТР ТС 010/2011", "ТР ТС 032/2013", "ИСО 9001", "ГОСТ 5761-2005", "ГОСТ 33259-2015"],
    "licenses": ["СРО на монтажные работы"],
    "delivery_regions": ["Нижегородская обл.", "Москва", "Московская обл.", "Республика Татарстан",
                         "Самарская обл.", "Саратовская обл.", "Челябинская обл.", "Свердловская обл.",
                         "Пермский край", "Воронежская обл.", "Санкт-Петербург"],
    "references": "12 исполненных контрактов за 3 года: Т Плюс, Водоканал Казань, Теплоэнерго НН, АК Транснефть (сервисные поставки).",
    "economics": {
        "tax_mode": "vat",
        "material_pct": 42, "labor_pct": 10, "overhead_pct": 12, "logistics_pct": 5,
        "bg_rate_pct": 3.5, "risk_reserve_pct": 3, "min_margin_pct": 16, "target_margin_pct": 24,
    },
    "scoring": {"w_okpd": 30, "w_keywords": 25, "w_region": 10, "w_sum": 15, "w_deadline": 10, "w_certificates": 10},
    "onboarding_done": False,
}

DEFAULT_AI_SETTINGS = {
    "provider": "openai",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "api_key": "",
    "temperature": 0.2,
    "auto_analyze": True,
    "match_threshold": 55,
}

PROVIDER_PRESETS = {
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "kind": "openai",
               "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "o4-mini"]},
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "kind": "openai",
                 "models": ["deepseek-chat", "deepseek-reasoner"]},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "anthropic/claude-sonnet-4.5", "kind": "openai",
                   "models": ["anthropic/claude-sonnet-4.5", "openai/gpt-4o-mini", "google/gemini-2.5-flash", "qwen/qwen3-235b-a22b"]},
    "anthropic": {"base_url": "https://api.anthropic.com", "model": "claude-sonnet-4-5", "kind": "anthropic",
                  "models": ["claude-sonnet-4-5", "claude-haiku-4-5", "claude-opus-4-1"]},
    "gigachat": {"base_url": "https://gigachat.devices.sberbank.ru/api/v1", "model": "GigaChat-Pro", "kind": "openai",
                 "models": ["GigaChat-Pro", "GigaChat-Max"]},
    "custom": {"base_url": "", "model": "", "kind": "openai", "models": []},
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_pass(password: str) -> str:
    return hashlib.sha256(("tenderpilot_salt::" + password).encode("utf-8")).hexdigest()


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(reset=False):
    if reset and os.path.exists(DB_PATH):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(DB_PATH + suffix)
            except OSError:
                pass
    conn = connect()
    conn.executescript(SCHEMA)
    cur = conn.execute("SELECT COUNT(*) AS c FROM users")
    if cur.fetchone()["c"] == 0:
        conn.execute(
            "INSERT INTO users (email, pass_hash, name) VALUES (?,?,?)",
            ("owner@zavod.ru", hash_pass("demo1234"), "Владелец завода"),
        )
    if not conn.execute("SELECT 1 FROM company WHERE id=1").fetchone():
        seed_company = json.loads(json.dumps(DEFAULT_COMPANY, ensure_ascii=False))
        seed_company.update(json.loads(json.dumps(DEMO_COMPANY, ensure_ascii=False)))
        conn.execute("INSERT INTO company (id, data) VALUES (1, ?)", (json.dumps(seed_company, ensure_ascii=False),))
    if not conn.execute("SELECT 1 FROM settings WHERE key='ai'").fetchone():
        conn.execute("INSERT INTO settings (key, value) VALUES ('ai', ?)", (json.dumps(DEFAULT_AI_SETTINGS, ensure_ascii=False),))
    conn.commit()
    # сиды тендеров
    count = conn.execute("SELECT COUNT(*) AS c FROM tenders").fetchone()["c"]
    if count == 0:
        from . import seed
        seed.load_tenders(conn)
        conn.commit()
    conn.close()


# ---------- helpers ----------
def get_company(conn):
    row = conn.execute("SELECT data FROM company WHERE id=1").fetchone()
    data = json.loads(row["data"]) if row else {}
    merged = json.loads(json.dumps(DEFAULT_COMPANY, ensure_ascii=False))
    for k, v in data.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k].update(v)
        else:
            merged[k] = v
    return merged


def set_company(conn, data):
    conn.execute("UPDATE company SET data=? WHERE id=1", (json.dumps(data, ensure_ascii=False),))
    conn.commit()


def get_ai_settings(conn, mask=True):
    row = conn.execute("SELECT value FROM settings WHERE key='ai'").fetchone()
    cfg = json.loads(row["value"]) if row else dict(DEFAULT_AI_SETTINGS)
    merged = dict(DEFAULT_AI_SETTINGS)
    merged.update(cfg or {})
    if mask:
        key = merged.get("api_key") or ""
        merged["api_key_set"] = bool(key)
        merged["api_key_hint"] = (key[:4] + "…" + key[-4:]) if len(key) >= 10 else ("задан" if key else "")
        merged["api_key"] = ""
    return merged


def set_ai_settings(conn, patch):
    row = conn.execute("SELECT value FROM settings WHERE key='ai'").fetchone()
    cfg = json.loads(row["value"]) if row else dict(DEFAULT_AI_SETTINGS)
    for k, v in patch.items():
        if k == "api_key" and (v is None or str(v).strip() == ""):
            continue  # пустое поле = не менять ключ
        cfg[k] = v
    conn.execute("UPDATE settings SET value=? WHERE key='ai'", (json.dumps(cfg, ensure_ascii=False),))
    conn.commit()
    return cfg


def create_session(conn, user_id):
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)", (token, user_id, now_iso()))
    conn.commit()
    return token


def user_by_session(conn, token):
    if not token:
        return None
    row = conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (token,)
    ).fetchone()
    return dict(row) if row else None


def add_event(conn, tender_id, type_, text):
    conn.execute(
        "INSERT INTO events (tender_id, type, text, created_at) VALUES (?,?,?,?)",
        (tender_id, type_, text, now_iso()),
    )
    conn.commit()
