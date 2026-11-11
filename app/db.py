# -*- coding: utf-8 -*-
"""SQLite-хранилище: схема, инициализация, сиды."""
import hashlib
import json
import re
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
  name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'manager',
  status TEXT NOT NULL DEFAULT 'pending',
  company_name TEXT DEFAULT '',
  created_at TEXT DEFAULT '',
  last_login_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS company (
  user_id INTEGER PRIMARY KEY,
  data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  user_id INTEGER NOT NULL DEFAULT 0,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS tenders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL DEFAULT 0,
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
CREATE TABLE IF NOT EXISTS ingest_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  source_id TEXT NOT NULL,
  status TEXT NOT NULL,
  message TEXT NOT NULL,
  found INTEGER DEFAULT 0,
  added INTEGER DEFAULT 0
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

# Стартовое наполнение профиля: реальные данные владелец вводит в «Профиле завода».
DEFAULT_PASSWORD = "Tender2026"
_LEGACY_PASSWORD = "demo1234"  # пароль установок предыдущих сборок — нужен только для миграции

INITIAL_COMPANY = {
    "name": "ООО «ПромАрматура»",
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



def _columns(conn, table):
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def migrate(conn):
    """Приводит базу к мультипользовательской схеме. Безопасно запускать повторно."""
    cols = _columns(conn, "users")
    for col, ddl in (
        ("role", "TEXT NOT NULL DEFAULT 'manager'"),
        ("status", "TEXT NOT NULL DEFAULT 'pending'"),
        ("company_name", "TEXT DEFAULT ''"),
        ("created_at", "TEXT DEFAULT ''"),
        ("last_login_at", "TEXT DEFAULT ''"),
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")

    # первый пользователь — владелец-администратор с существующими данными
    owner = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    owner_id = owner["id"] if owner else None
    if owner_id:
        conn.execute(
            "UPDATE users SET role='owner', status='active' WHERE id=? AND (role IS NULL OR role='manager')",
            (owner_id,),
        )

    # company: из единственной строки (id=1) в строку владельца
    ccols = _columns(conn, "company")
    if "user_id" not in ccols and ccols:
        conn.execute("CREATE TABLE IF NOT EXISTS company_new (user_id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
        row = conn.execute("SELECT data FROM company LIMIT 1").fetchone()
        if row and owner_id:
            conn.execute("INSERT OR REPLACE INTO company_new (user_id, data) VALUES (?,?)", (owner_id, row["data"]))
        conn.commit()
        conn.execute("DROP TABLE company")
        conn.execute("ALTER TABLE company_new RENAME TO company")

    # settings: добавляем владельца, глобальные настройки получают user_id=0
    scols = _columns(conn, "settings")
    if "user_id" not in scols and scols:
        conn.execute("CREATE TABLE IF NOT EXISTS settings_new (user_id INTEGER NOT NULL DEFAULT 0, "
                     "key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (user_id, key))")
        for row in conn.execute("SELECT key, value FROM settings").fetchall():
            uid = 0 if row["key"] in ("registration", "global") else (owner_id or 0)
            conn.execute("INSERT OR REPLACE INTO settings_new (user_id, key, value) VALUES (?,?,?)",
                         (uid, row["key"], row["value"]))
        conn.commit()
        conn.execute("DROP TABLE settings")
        conn.execute("ALTER TABLE settings_new RENAME TO settings")

    # tenders и ingest_log: привязка к владельцу
    if "user_id" not in _columns(conn, "tenders") and owner_id:
        conn.execute("ALTER TABLE tenders ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE tenders SET user_id=?", (owner_id,))
    if "user_id" not in _columns(conn, "ingest_log") and owner_id:
        conn.execute("ALTER TABLE ingest_log ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE ingest_log SET user_id=?", (owner_id,))


    # однократный переход со стартового пароля прошлых сборок (только миграция, в интерфейсе не встречается)
    old_hash = hash_pass(_LEGACY_PASSWORD)
    for row in conn.execute("SELECT id FROM users WHERE pass_hash=?", (old_hash,)).fetchall():
        conn.execute("UPDATE users SET pass_hash=? WHERE id=?", (hash_pass(DEFAULT_PASSWORD), row["id"]))
    # чистим служебные пометки в названии профиля
    for row in conn.execute("SELECT user_id, data FROM company").fetchall():
        if row["data"] and "\u0434\u0435\u043c\u043e" in row["data"]:
            cleaned = re.sub(r"\s*\(\u0434\u0435\u043c\u043e-\u043f\u0440\u043e\u0444\u0438\u043b\u044c\)", "", row["data"])
            conn.execute("UPDATE company SET data=? WHERE user_id=?", (cleaned, row["user_id"]))
    conn.commit()

    # индекс дедупликации внутри ленты пользователя
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tenders_user_ext ON tenders(user_id, ext_id, platform)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tenders_user ON tenders(user_id, id)")
    except sqlite3.Error:
        pass
    conn.commit()


def owner_id(conn):
    """Администратор по умолчанию (первый владелец) — для фоновых задач без контекста."""
    row = conn.execute("SELECT id FROM users ORDER BY (role='owner') DESC, id LIMIT 1").fetchone()
    return row["id"] if row else 1


def _uid(conn, uid=None):
    return int(uid) if uid else owner_id(conn)


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn



# ---------------- пользователи (админка) ----------------
ROLES = {
    "owner": "Администратор",
    "manager": "Менеджер по тендерам",
    "viewer": "Наблюдатель",
}
STATUSES = {"pending": "Ожидает подтверждения", "active": "Активен", "blocked": "Заблокирован"}


def user_by_email(conn, email):
    return conn.execute("SELECT * FROM users WHERE email=?", (str(email).strip().lower(),)).fetchone()


def user_by_id(conn, uid):
    return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def create_user(conn, email, password, name, role="manager", status="active", company_name=""):
    email = str(email).strip().lower()
    conn.execute(
        "INSERT INTO users (email, pass_hash, name, role, status, company_name, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (email, hash_pass(password), (name or email)[:120], role, status, (company_name or "")[:160], now_iso()),
    )
    conn.commit()
    return user_by_email(conn, email)


def update_user(conn, uid, patch):
    allowed = {"name", "role", "status", "company_name", "email"}
    sets, vals = [], []
    for key, val in (patch or {}).items():
        if key in allowed:
            sets.append(f"{key}=?")
            vals.append(str(val).strip()[:160] if key != "role" and key != "status" else val)
        elif key == "password" and val:
            sets.append("pass_hash=?")
            vals.append(hash_pass(val))
    if not sets:
        return user_by_id(conn, uid)
    vals.append(uid)
    conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()
    return user_by_id(conn, uid)


def delete_user(conn, uid):
    """Удаляет пользователя и все его данные (лента, профиль, настройки, сессии)."""
    conn.execute("DELETE FROM events WHERE tender_id IN (SELECT id FROM tenders WHERE user_id=?)", (uid,))
    conn.execute("DELETE FROM tenders WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM company WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM settings WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM ingest_log WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()


def active_users(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM users WHERE status='active' ORDER BY id").fetchall()]


def users_overview(conn):
    return [dict(r) for r in conn.execute(
        """SELECT u.id, u.email, u.name, u.role, u.status, u.company_name, u.created_at, u.last_login_at,
                  (SELECT COUNT(*) FROM tenders t WHERE t.user_id=u.id) AS tenders,
                  (SELECT COUNT(*) FROM tenders t WHERE t.user_id=u.id AND t.analysis IS NOT NULL) AS analyzed
           FROM users u ORDER BY u.id""").fetchall()]


def registration_settings(conn):
    cfg = {"open": True, "require_approval": True, "default_role": "manager"}
    row = conn.execute("SELECT value FROM settings WHERE user_id=0 AND key='registration'").fetchone()
    if row:
        try:
            cfg.update(json.loads(row["value"]) or {})
        except Exception:
            pass
    return cfg


def set_registration_settings(conn, patch):
    cfg = registration_settings(conn)
    cfg.update({k: v for k, v in (patch or {}).items() if k in cfg})
    conn.execute("INSERT INTO settings (user_id, key, value) VALUES (0,'registration',?) "
                 "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
                 (json.dumps(cfg, ensure_ascii=False),))
    conn.commit()
    return cfg

def init_db(reset=False):
    if reset and os.path.exists(DB_PATH):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(DB_PATH + suffix)
            except OSError:
                pass
    conn = connect()
    conn.executescript(SCHEMA)
    migrate(conn)
    cur = conn.execute("SELECT COUNT(*) AS c FROM users")
    if cur.fetchone()["c"] == 0:
        conn.execute(
            "INSERT INTO users (email, pass_hash, name, role, status, company_name, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("owner@zavod.ru", hash_pass(DEFAULT_PASSWORD), "Владелец завода", "owner", "active",
             "ООО «ПромАрматура»", now_iso()),
        )
    owner = owner_id(conn)
    if not conn.execute("SELECT 1 FROM company WHERE user_id=?", (owner,)).fetchone():
        seed_company = json.loads(json.dumps(DEFAULT_COMPANY, ensure_ascii=False))
        seed_company.update(json.loads(json.dumps(INITIAL_COMPANY, ensure_ascii=False)))
        conn.execute("INSERT INTO company (user_id, data) VALUES (?, ?)",
                     (owner, json.dumps(seed_company, ensure_ascii=False)))
    if not conn.execute("SELECT 1 FROM settings WHERE user_id=? AND key='ai'", (owner,)).fetchone():
        conn.execute("INSERT INTO settings (user_id, key, value) VALUES (?, 'ai', ?)",
                     (owner, json.dumps(DEFAULT_AI_SETTINGS, ensure_ascii=False)))
    if not conn.execute("SELECT 1 FROM settings WHERE user_id=0 AND key='registration'").fetchone():
        conn.execute("INSERT INTO settings (user_id, key, value) VALUES (0, 'registration', ?)",
                     (json.dumps({"open": True, "require_approval": True, "default_role": "manager"},
                                 ensure_ascii=False),))
    conn.commit()
    # сиды тендеров
    count = conn.execute("SELECT COUNT(*) AS c FROM tenders").fetchone()["c"]
    if count == 0:
        from . import seed
        n = seed.load_tenders(conn)
        conn.execute("UPDATE tenders SET user_id=? WHERE user_id=0", (owner,))
        conn.commit()
    conn.close()


# ---------- helpers ----------
def get_company(conn, uid=None):
    row = conn.execute("SELECT data FROM company WHERE user_id=?", (_uid(conn, uid),)).fetchone()
    if not row:
        return json.loads(json.dumps(DEFAULT_COMPANY, ensure_ascii=False))
    data = json.loads(row["data"])
    merged = json.loads(json.dumps(DEFAULT_COMPANY, ensure_ascii=False))
    for k, v in data.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k].update(v)
        else:
            merged[k] = v
    return merged


def set_company(conn, data, uid=None):
    conn.execute("INSERT INTO company (user_id, data) VALUES (?,?) "
                 "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data",
                 (_uid(conn, uid), json.dumps(data, ensure_ascii=False)))
    conn.commit()


def get_setting(conn, key, uid=None, default=None):
    row = conn.execute("SELECT value FROM settings WHERE user_id=? AND key=?", (_uid(conn, uid), key)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return default


def set_setting(conn, key, value, uid=None):
    conn.execute("INSERT INTO settings (user_id, key, value) VALUES (?,?,?) "
                 "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
                 (_uid(conn, uid), key, json.dumps(value, ensure_ascii=False)))
    conn.commit()
    return value


def get_company_row(conn, uid):
    return conn.execute("SELECT data FROM company WHERE user_id=?", (uid,)).fetchone()


def get_ai_settings(conn, mask=True, uid=None):
    cfg = get_setting(conn, "ai", uid, default=None) or dict(DEFAULT_AI_SETTINGS)
    merged = dict(DEFAULT_AI_SETTINGS)
    merged.update(cfg or {})
    if mask:
        key = merged.get("api_key") or ""
        merged["api_key_set"] = bool(key)
        merged["api_key_hint"] = (key[:4] + "…" + key[-4:]) if len(key) >= 10 else ("задан" if key else "")
        merged["api_key"] = ""
    return merged


def set_ai_settings(conn, patch, uid=None):
    cfg = get_setting(conn, "ai", uid, default=None) or dict(DEFAULT_AI_SETTINGS)
    for k, v in (patch or {}).items():
        if k == "api_key" and (v is None or str(v).strip() == ""):
            continue  # пустое поле = не менять ключ
        cfg[k] = v
    set_setting(conn, "ai", cfg, uid)
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
