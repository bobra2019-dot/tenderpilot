# -*- coding: utf-8 -*-
"""Фоновый планировщик: периодический сбор с источников + автоотсев + уведомления."""
import asyncio
import json
import os

from . import db, ingest, notify

TASK = None


DEFAULT_SCHEDULE = {"enabled": False, "interval_minutes": 60, "notify_new": True, "auto_analyze": True}


def get_schedule(conn, uid=None):
    cfg = dict(DEFAULT_SCHEDULE)
    saved = db.get_setting(conn, "schedule", uid, default=None) or {}
    cfg.update(saved or {})
    return cfg


def set_schedule(conn, patch, uid=None):
    cfg = get_schedule(conn, uid)
    cfg.update(patch or {})
    db.set_setting(conn, "schedule", cfg, uid)
    return cfg


async def cycle(uid=None):
    """Один проход по одному пользователю: сбор → отсев → уведомления."""
    conn = db.connect()
    try:
        uid = db._uid(conn, uid)
        cfg = get_schedule(conn, uid)
        company = db.get_company(conn, uid)
        ai_cfg = db.get_ai_settings(conn, uid=uid)
        ncfg = notify.get_notify(conn, mask=False, uid=uid)

        before = conn.execute("SELECT COUNT(*) c FROM tenders WHERE user_id=?", (uid,)).fetchone()["c"]
        results = await ingest.run_all(conn, uid=uid)
        after = conn.execute("SELECT COUNT(*) c FROM tenders WHERE user_id=?", (uid,)).fetchone()["c"]
        new_count = after - before

        if cfg.get("auto_analyze", True):
            ingest.auto_analyze_new(conn, company, ai_cfg, uid)

        sent = 0
        if ncfg.get("telegram_token") and ncfg.get("telegram_chat") and cfg.get("notify_new", True) and new_count:
            from . import scoring
            rows = conn.execute("SELECT * FROM tenders WHERE user_id=? AND analysis IS NOT NULL "
                                "ORDER BY id DESC LIMIT 10", (uid,)).fetchall()
            allowed = ncfg.get("only_verdicts") or ["go"]
            for row in rows:
                t = dict(row)
                t["analysis"] = json.loads(t["analysis"]) if t.get("analysis") else None
                t["margin"] = scoring.compute_margin(company, t)
                verdict = (t["analysis"] or {}).get("verdict")
                if verdict not in allowed:
                    continue
                if (t["margin"]["margin_pct"] or 0) < float(ncfg.get("min_margin_pct") or 0):
                    continue
                res = await notify.telegram_send(ncfg, notify.format_tender(t, os.environ.get("PUBLIC_URL", "")))
                if res.get("ok"):
                    sent += 1
        return {"user": uid, "new": new_count, "sources": results, "notified": sent}
    finally:
        conn.close()


async def cycle_all():
    """Проходит по всем активным пользователям, у кого включено расписание."""
    conn = db.connect()
    try:
        users = db.active_users(conn)
    finally:
        conn.close()
    out = []
    for u in users:
        conn = db.connect()
        try:
            cfg = get_schedule(conn, u["id"])
        finally:
            conn.close()
        if not cfg.get("enabled"):
            continue
        try:
            out.append(await cycle(u["id"]))
        except Exception as e:
            conn = db.connect()
            try:
                ingest.log(conn, "scheduler", "error", f"{u['email']}: {e}", uid=u["id"])
            finally:
                conn.close()
    return out


async def _loop():
    while True:
        try:
            conn = db.connect()
            users = db.active_users(conn)
            intervals = [int(get_schedule(conn, u["id"]).get("interval_minutes") or 60)
                         for u in users if get_schedule(conn, u["id"]).get("enabled")]
            conn.close()
            interval = max(5, min(intervals or [60]))
        except Exception:
            interval = 3600
        try:
            await cycle_all()
        except Exception as e:
            conn = db.connect()
            try:
                ingest.log(conn, "scheduler", "error", str(e))
            finally:
                conn.close()
        await asyncio.sleep(max(5, interval) * 60)


def start():
    global TASK
    if TASK is None or TASK.done():
        TASK = asyncio.create_task(_loop())
    return TASK


def stop():
    global TASK
    if TASK:
        TASK.cancel()
        TASK = None
