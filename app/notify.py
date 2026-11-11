# -*- coding: utf-8 -*-
"""Уведомления: Telegram и e-mail (SMTP). Настройки — в settings ключ 'notify'."""
import json

from . import db
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


def _read(conn, uid=None):
    cfg = dict(DEFAULT_NOTIFY)
    saved = db.get_setting(conn, "notify", uid, default=None) or {}
    cfg.update(saved or {})
    return cfg


def get_notify(conn, mask=True, uid=None):
    cfg = _read(conn, uid)
    if mask:
        cfg = json.loads(json.dumps(cfg))
        token = cfg.get("telegram_token") or ""
        cfg["telegram_token_set"] = bool(token)
        cfg["telegram_token"] = (token[:6] + "…") if len(token) > 10 else ("задан" if token else "")
        cfg["telegram_chat_hint"] = cfg.get("telegram_chat") or ""
    return cfg


def set_notify(conn, patch, uid=None):
    cfg = _read(conn, uid)
    for k, v in (patch or {}).items():
        if k in ("telegram_token", "telegram_chat") and (v is None or str(v).strip() == ""):
            continue
        if k == "email" and isinstance(v, dict):
            cfg["email"].update(v)
        else:
            cfg[k] = v
    db.set_setting(conn, "notify", cfg, uid)
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
