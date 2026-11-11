# -*- coding: utf-8 -*-
"""Тарифы, лимиты и оплата.

Модель: у пользователя есть тариф (plan), дата окончания подписки и месячный лимит
ИИ-разборов. Пока подписка не оформлена — действует пробный период с урезанным лимитом.

Оплата: два режима.
  1. Счёт на оплату (по умолчанию) — B2B-норма: выставляем счёт, клиент платит по реквизитам,
     администратор подтверждает поступление кнопкой. Работает сразу, без эквайринга.
  2. Онлайн-оплата картой — включается, когда в настройках указаны ключи ЮKassa
     (shop_id + secret_key) или другого провайдера. До этого кнопка показывает инструкцию.
"""
import json
import secrets
from datetime import datetime, timedelta, timezone

from . import db

PLANS = {
    "trial": {
        "id": "trial", "name": "Пробный доступ", "price_month": 0, "price_year": 0,
        "users": 1, "analyses": 10, "days": 7,
        "features": ["10 разборов закупок", "1 пользователь", "лента и аналитика",
                     "ручное добавление закупок", "сбор из выгрузок площадок"],
    },
    "solo": {
        "id": "solo", "name": "Соло", "price_month": 6900, "price_year": 69000,
        "users": 1, "analyses": 30, "days": None,
        "features": ["30 разборов закупок в месяц", "1 пользователь",
                     "ЕИС 44-ФЗ и 223-ФЗ, сбор по расписанию",
                     "расчёт маржи по номенклатуре", "уведомления в Telegram"],
    },
    "factory": {
        "id": "factory", "name": "Завод", "price_month": 14900, "price_year": 149000,
        "users": 3, "analyses": 150, "days": None, "popular": True,
        "features": ["150 разборов закупок в месяц", "3 пользователя",
                     "проверка каждого требования ТЗ", "экономика и калькулятор маржи",
                     "выгрузки ЭТП и XML ЕИС", "уведомления в Telegram и e-mail"],
    },
    "department": {
        "id": "department", "name": "Отдел", "price_month": 29900, "price_year": 299000,
        "users": 15, "analyses": 600, "days": None,
        "features": ["600 разборов закупок в месяц", "до 15 пользователей и роли",
                     "все площадки и приоритетный сбор",
                     "журнал действий и разграничение доступа",
                     "персональная поддержка"],
    },
    "setup": {
        "id": "setup", "name": "Внедрение", "price_month": 0, "price_year": 0,
        "users": 0, "analyses": 0, "days": None, "one_time": 49000,
        "features": ["заполнение профиля завода и номенклатуры", "настройка ОКПД2 и сертификатов",
                     "настройка источников и Telegram", "обучение сотрудников (2 часа)"],
    },
}

DEFAULT_TRIAL = "trial"


def _now():
    return datetime.now(timezone.utc)


def add_billing_columns(conn):
    """Добавляет поля подписки к пользователям (безопасно при повторном вызове)."""
    cols = set()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    except Exception:
        return
    for name, ddl in (
        ("plan", "TEXT NOT NULL DEFAULT 'trial'"),
        ("plan_until", "TEXT DEFAULT ''"),
        ("trial_until", "TEXT DEFAULT ''"),
        ("analyses_used", "INTEGER NOT NULL DEFAULT 0"),
        ("analyses_period", "TEXT DEFAULT ''"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
    conn.execute("""CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        plan TEXT NOT NULL,
        period TEXT NOT NULL DEFAULT 'month',
        amount REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        method TEXT NOT NULL DEFAULT 'invoice',
        created_at TEXT NOT NULL,
        paid_at TEXT DEFAULT '',
        note TEXT DEFAULT ''
    )""")
    conn.commit()


def _plan_of(conn, uid):
    row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        return None
    u = dict(row)
    plan = PLANS.get(u.get("plan") or DEFAULT_TRIAL, PLANS[DEFAULT_TRIAL])
    until = u.get("plan_until") or ""
    trial_until = u.get("trial_until") or ""
    now = _now()

    expired = False
    if plan["id"] != "trial" and until:
        try:
            expired = datetime.fromisoformat(until) < now
        except ValueError:
            expired = False
    elif plan["id"] == "trial":
        if not trial_until:
            trial_until = (now + timedelta(days=7)).isoformat()
            conn.execute("UPDATE users SET trial_until=? WHERE id=?", (trial_until, uid))
            conn.commit()
        try:
            expired = datetime.fromisoformat(trial_until) < now
        except ValueError:
            expired = False

    period = now.strftime("%Y-%m")
    used = int(u.get("analyses_used") or 0)
    if (u.get("analyses_period") or "") != period:
        conn.execute("UPDATE users SET analyses_used=0, analyses_period=? WHERE id=?", (period, uid))
        conn.commit()
        used = 0

    limit = int(plan.get("analyses") or 0)
    return {
        "plan": {k: v for k, v in plan.items() if k != "features"},
        "features": plan.get("features", []),
        "plan_until": until,
        "trial_until": trial_until,
        "expired": expired,
        "used": used,
        "limit": limit,
        "left": max(0, limit - used),
        "can_analyze": (not expired) and (used < limit),
        "period": period,
        "days_left": _days_left(until if plan["id"] != "trial" else trial_until),
    }


def _days_left(date_str):
    if not date_str:
        return None
    try:
        return max(0, (datetime.fromisoformat(date_str) - _now()).days)
    except ValueError:
        return None


def get_billing(conn, uid):
    info = _plan_of(conn, uid)
    rows = conn.execute("SELECT * FROM payments WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,)).fetchall()
    info["payments"] = [dict(r) for r in rows]
    info["plans"] = [{k: v for k, v in p.items()} for p in PLANS.values()]
    return info


def consume_analysis(conn, uid):
    """Списывает один разбор. Возвращает (ok, сообщение)."""
    info = _plan_of(conn, uid)
    if info["expired"]:
        return False, ("Подписка не активна. Оформите тариф в разделе «Тариф и оплата», "
                       "чтобы продолжить разбор закупок.")
    if info["used"] >= info["limit"]:
        return False, (f"Лимит тарифа «{info['plan']['name']}» исчерпан: "
                       f"{info['used']} из {info['limit']} разборов в этом месяце. "
                       f"Перейдите на тариф выше или дождитесь нового месяца.")
    conn.execute("UPDATE users SET analyses_used=analyses_used+1, analyses_period=? WHERE id=?",
                 (info["period"], uid))
    conn.commit()
    return True, ""


def set_plan(conn, uid, plan_id, period="month", days=None):
    if plan_id not in PLANS:
        raise ValueError("Неизвестный тариф")
    plan = PLANS[plan_id]
    now = _now()
    if plan_id == "trial":
        until = (now + timedelta(days=days or 7)).isoformat()
        conn.execute("UPDATE users SET plan='trial', trial_until=?, plan_until='', analyses_used=0 WHERE id=?",
                     (until, uid))
    else:
        base = now
        row = conn.execute("SELECT plan_until FROM users WHERE id=?", (uid,)).fetchone()
        if row and row["plan_until"]:
            try:
                current = datetime.fromisoformat(row["plan_until"])
                if current > now:  # продление: добавляем к текущему сроку
                    base = current
            except ValueError:
                pass
        span = days or (365 if period == "year" else 30)
        until = (base + timedelta(days=span)).isoformat()
        conn.execute("UPDATE users SET plan=?, plan_until=?, analyses_used=0, analyses_period=? WHERE id=?",
                     (plan_id, until, now.strftime("%Y-%m"), uid))
    conn.commit()
    return _plan_of(conn, uid)


def plan_price(plan_id, period="month"):
    plan = PLANS.get(plan_id) or {}
    if plan.get("one_time"):
        return float(plan["one_time"])
    return float(plan.get("price_year") if period == "year" else plan.get("price_month") or 0)


def create_payment(conn, uid, plan_id, period="month", method="invoice", note=""):
    if plan_id not in PLANS or plan_id in ("trial",):
        raise ValueError("Этот тариф нельзя оплатить")
    amount = plan_price(plan_id, period)
    if amount <= 0:
        raise ValueError("Некорректная сумма тарифа")
    cur = conn.execute(
        "INSERT INTO payments (user_id, plan, period, amount, status, method, created_at, note) "
        "VALUES (?,?,?,?, 'pending', ?, ?, ?)",
        (uid, plan_id, period, amount, method, _now().isoformat(), note[:200]),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM payments WHERE id=?", (cur.lastrowid,)).fetchone())


def invoice_text(conn, payment, user, company):
    """Готовый счёт: реквизиты поставщика берутся из настроек, если заполнены."""
    cfg = db.get_setting(conn, "billing", 0, default={}) or {}
    plan = PLANS[payment["plan"]]
    period_label = "12 месяцев" if payment["period"] == "year" else "1 месяц"
    num = f"{payment['id']:05d}/{_now().strftime('%m-%Y')}"
    supplier = cfg.get("company_name") or "ООО «TenderPilot»"
    inn = cfg.get("inn") or "— не заполнено в настройках —"
    account = cfg.get("account") or "— не заполнено в настройках —"
    bank = cfg.get("bank") or "— не заполнено в настройках —"
    return {
        "number": num,
        "date": _now().strftime("%d.%m.%Y"),
        "supplier": supplier,
        "supplier_inn": inn,
        "supplier_account": account,
        "supplier_bank": bank,
        "payer": (company.get("name") or user["name"] or user["email"]),
        "payer_inn": company.get("inn") or "",
        "payer_email": user["email"],
        "items": [{"name": f"Доступ к TenderPilot, тариф «{plan['name']}», {period_label}",
                   "qty": 1, "price": payment["amount"], "sum": payment["amount"]}],
        "total": payment["amount"],
        "vat": "Без НДС",
        "pay_days": 5,
        "comment": f"Оплата по счёту №{num}. Доступ активируется в течение рабочего дня после поступления средств.",
    }


def mark_paid(conn, payment_id, uid=None):
    row = conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    if not row:
        raise ValueError("Счёт не найден")
    p = dict(row)
    if p["status"] == "paid":
        return p
    conn.execute("UPDATE payments SET status='paid', paid_at=? WHERE id=?", (_now().isoformat(), payment_id))
    conn.commit()
    set_plan(conn, p["user_id"], p["plan"], p["period"])
    return dict(conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone())


def provider_config(conn):
    cfg = db.get_setting(conn, "payments", 0, default={}) or {}
    return {
        "provider": cfg.get("provider") or "invoice",
        "shop_id": cfg.get("shop_id") or "",
        "secret_key": cfg.get("secret_key") or "",
        "enabled": bool(cfg.get("shop_id") and cfg.get("secret_key")),
        "invoice_mode": cfg.get("provider", "invoice") != "yookassa",
    }


def payment_link(conn, payment):
    """Ссылка на оплату. Пока провайдер не настроен — None, интерфейс покажет счёт."""
    return None


def new_token():
    return secrets.token_urlsafe(16)
