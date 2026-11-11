# -*- coding: utf-8 -*-
"""Фоновый планировщик: периодический сбор с источников + автоотсев + уведомления."""
import asyncio
import json
import os

from . import db, ingest, notify

TASK = None


def get_schedule(conn):
    row = conn.execute("SELECT value FROM settings WHERE key='schedule'").fetchone()
    cfg = {"enabled": False, "interval_minutes": 60, "notify_new": True, "auto_analyze": True}
    if row:
        try:
            cfg.update(json.loads(row["value"]) or {})
        except Exception:
            pass
    return cfg


def set_schedule(conn, patch):
    cfg = get_schedule(conn)
    cfg.update(patch or {})
    conn.execute("INSERT INTO settings (key, value) VALUES ('schedule', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (json.dumps(cfg, ensure_ascii=False),))
    conn.commit()
    return cfg


async def cycle():
    """Один проход: сбор → отсев → уведомления."""
    conn = db.connect()
    try:
        cfg = get_schedule(conn)
        company = db.get_company(conn)
        ai_cfg = db.get_ai_settings(conn)
        ncfg = notify.get_notify(conn, mask=False)

        before = conn.execute("SELECT COUNT(*) c FROM tenders").fetchone()["c"]
        results = await ingest.run_all(conn)
        after = conn.execute("SELECT COUNT(*) c FROM tenders").fetchone()["c"]
        new_count = after - before

        if cfg.get("auto_analyze", True):
            ingest.auto_analyze_new(conn, company, ai_cfg)

        sent = 0
        if ncfg.get("telegram_token") and ncfg.get("telegram_chat") and cfg.get("notify_new", True) and new_count:
            from . import scoring
            rows = conn.execute("SELECT * FROM tenders WHERE analysis IS NOT NULL ORDER BY id DESC LIMIT 10").fetchall()
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
        return {"new": new_count, "sources": results, "notified": sent}
    finally:
        conn.close()


async def _loop():
    while True:
        try:
            conn = db.connect()
            cfg = get_schedule(conn)
            conn.close()
            interval = max(5, int(cfg.get("interval_minutes") or 60)) * 60
        except Exception:
            interval = 3600
        try:
            await cycle()
        except Exception as e:
            conn = db.connect()
            try:
                ingest.log(conn, "scheduler", "error", str(e))
            finally:
                conn.close()
        await asyncio.sleep(interval)


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
