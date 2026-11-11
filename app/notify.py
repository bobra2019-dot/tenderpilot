# -*- coding: utf-8 -*-
"""Уведомления: Telegram и e-mail (SMTP). Настройки — в settings ключ 'notify'."""
import json
import smtplib
from email.mime.text import MIMEText

import httpx

DEFAULT_NOTIFY = {
    "telegram_token": "",
    "telegram_chat": "",
    "only_verdicts": ["go"],
    "min_margin_pct": 0,
    "email": {"enabled": False, "host": "", "port": 465, "user": "", "password": "", "to": ""},
}


def get_notify(conn, mask=True):
    row = conn.execute("SELECT value FROM settings WHERE key='notify'").fetchone()
    cfg = dict(DEFAULT_NOTIFY)
    if row:
        try:
            cfg.update(json.loads(row["value"]) or {})
        except Exception:
            pass
    if mask:
        cfg = json.loads(json.dumps(cfg))
        for k in ("telegram_token",):
            v = cfg.get(k) or ""
            cfg[k] = (v[:6] + "…") if len(v) > 10 else ("задан" if v else "")
        cfg["telegram_token_set"] = bool((json.loads(row["value"]).get("telegram_token") if row else ""))
        cfg["telegram_chat_hint"] = (json.loads(row["value"]).get("telegram_chat") if row else "") or ""
        cfg["telegram_chat"] = cfg["telegram_chat_hint"]
    return cfg


def set_notify(conn, patch):
    row = conn.execute("SELECT value FROM settings WHERE key='notify'").fetchone()
    cfg = dict(DEFAULT_NOTIFY)
    if row:
        try:
            cfg.update(json.loads(row["value"]) or {})
        except Exception:
            pass
    for k, v in (patch or {}).items():
        if k in ("telegram_token", "telegram_chat") and (v is None or str(v).strip() == ""):
            continue
        if k == "email" and isinstance(v, dict):
            cfg["email"].update(v)
        else:
            cfg[k] = v
    conn.execute("INSERT INTO settings (key, value) VALUES ('notify', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (json.dumps(cfg, ensure_ascii=False),))
    conn.commit()
    return cfg


async def telegram_send(cfg, text):
    token = (cfg.get("telegram_token") or "").strip()
    chat = str(cfg.get("telegram_chat") or "").strip()
    if not token or not chat:
        return {"ok": False, "error": "Не заданы токен бота или chat_id"}
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.post(url, json={
            "chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True,
        })
    if r.status_code >= 400:
        return {"ok": False, "error": f"Telegram HTTP {r.status_code}: {r.text[:200]}"}
    return {"ok": True}


def email_send(cfg, subject, text):
    e = cfg.get("email") or {}
    if not e.get("enabled") or not e.get("host"):
        return {"ok": False, "error": "E-mail не настроен"}
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = e.get("user") or "tenderpilot@localhost"
    msg["To"] = e.get("to") or e.get("user")
    try:
        with smtplib.SMTP_SSL(e["host"], int(e.get("port") or 465), timeout=25) as s:
            if e.get("user"):
                s.login(e["user"], e.get("password") or "")
            s.send_message(msg)
        return {"ok": True}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}


def format_tender(t, base_url=""):
    verdict = {"go": "✅ идём", "consider": "⚠️ спорно", "no_go": "❌ не идём"}
    a = t.get("analysis") or {}
    m = t.get("margin") or {}
    lines = [
        f"<b>{t.get('title','')[:150]}</b>",
        f"{verdict.get(a.get('verdict'), '—')} · соответствие {a.get('computed_match', '—')}/100 · маржа {m.get('margin_pct','—')}%",
        f"Заказчик: {t.get('customer','—')}",
        f"Сумма: {int(t.get('sum') or 0):,} ₽".replace(",", " "),
        f"Дедлайн: {t.get('deadline','—')}",
    ]
    if base_url:
        lines.append(f'<a href="{base_url}/#/tender/{t.get("id")}">Открыть в TenderPilot</a>')
    if t.get("source_url"):
        lines.append(f'<a href="{t["source_url"]}">Извещение на площадке</a>')
    return "\n".join(lines)
