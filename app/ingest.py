# -*- coding: utf-8 -*-
"""Конвейер: собрать → дедуплицировать → сохранить → при необходимости проанализировать и уведомить."""
import json
import os
from datetime import datetime, timezone

from . import db, scoring, sources

INBOX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "inbox")
PROCESSED = os.path.join(INBOX, "_processed")


def ensure_dirs():
    os.makedirs(INBOX, exist_ok=True)
    os.makedirs(PROCESSED, exist_ok=True)


def get_sources(conn, uid=None):
    current = db.get_setting(conn, "sources", uid, default=None) or {}
    out = []
    for default in sources.DEFAULT_SOURCES:
        saved = current.get(default["id"], {})
        item = dict(default)
        item["enabled"] = saved.get("enabled", default["enabled"])
        item["params"] = {**default["params"], **(saved.get("params") or {})}
        out.append(item)
    # пользовательские источники (добавленные вручную)
    for sid, saved in current.items():
        if any(s["id"] == sid for s in out):
            continue
        out.append({
            "id": sid, "type": saved.get("type", "etp_api"), "name": saved.get("name", sid),
            "note": saved.get("note", ""), "enabled": saved.get("enabled", False),
            "params": saved.get("params") or {},
        })
    return out


def save_sources(conn, items, uid=None):
    payload = {s["id"]: {"enabled": bool(s.get("enabled")), "type": s.get("type"),
                         "name": s.get("name"), "note": s.get("note"),
                         "params": s.get("params") or {}} for s in items}
    db.set_setting(conn, "sources", payload, uid)
    return payload


def log(conn, source_id, status, message, found=0, added=0, uid=None):
    conn.execute(
        "INSERT INTO ingest_log (user_id, created_at, source_id, status, message, found, added) "
        "VALUES (?,?,?,?,?,?,?)",
        (db._uid(conn, uid), db.now_iso(), source_id, status, message[:500], found, added),
    )
    conn.commit()


def save_tender(conn, t, uid=None) -> bool:
    """Вставляет закупку в ленту пользователя, если её ещё нет. True — добавлена новая."""
    uid = db._uid(conn, uid)
    exists = conn.execute(
        "SELECT id FROM tenders WHERE user_id=? AND ext_id=? AND platform=?",
        (uid, t.get("ext_id"), t.get("platform")),
    ).fetchone()
    if exists:
        return False
    conn.execute(
        """INSERT INTO tenders (user_id, ext_id, platform, law, category, title, customer, customer_inn, region, okpd,
           qty, unit, sum, published, deadline, delivery_days, payment_terms, bid_security_pct,
           contract_security_pct, penalty, description, tz_text, requirements, files, source_url,
           ingested, stage, manual, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'new',0,?)""",
        (
            uid, str(t.get("ext_id") or "")[:64], t.get("platform") or "manual", t.get("law") or "коммерч.",
            t.get("category") or "valves", (t.get("title") or "")[:300], (t.get("customer") or "Не определён")[:200],
            "", (t.get("region") or "")[:80], (t.get("okpd") or "")[:32],
            float(t.get("qty") or 1), (t.get("unit") or "компл")[:16], float(t.get("sum") or 0),
            (t.get("published") or datetime.now(timezone.utc).strftime("%Y-%m-%d"))[:10],
            t.get("deadline") or (datetime.now(timezone.utc).strftime("%Y-%m-%dT10:00")),
            int(t.get("delivery_days") or 45), "", 0, 0, "",
            (t.get("description") or "")[:1000], (t.get("tz_text") or "")[:20000],
            t.get("requirements") or "[]", t.get("files") or "[]", t.get("source_url") or "",
            db.now_iso(),
        ),
    )
    return True


async def run_source(conn, source, cfg=None, uid=None) -> dict:
    """Выполняет один источник и сохраняет найденное."""
    sid, stype = source["id"], source["type"]
    params = source.get("params") or {}
    result = {"source": sid, "found": 0, "added": 0, "errors": []}
    try:
        if stype == "eis_search":
            items = await sources.fetch_eis_search(params)
        elif stype == "etp_api":
            items = await sources.fetch_etp_api(params)
        elif stype == "files_watch":
            sub = params.get("path") or "inbox"
            folder = sub if os.path.isabs(sub) else os.path.join(os.path.dirname(INBOX), sub)
            if not os.path.isdir(folder):
                folder = INBOX
            ensure_dirs()
            res = sources.parse_files_in_inbox(folder, PROCESSED)
            items = res["items"]
            result["errors"].extend(res["errors"])
            result["files"] = res["files"]
        else:
            raise RuntimeError(f"Неизвестный тип источника: {stype}")

        result["found"] = len(items)
        added = 0
        for t in items:
            if save_tender(conn, t, uid):
                added += 1
        conn.commit()
        result["added"] = added
        log(conn, sid, "ok", f"найдено {len(items)}, новых {added}", len(items), added, uid)
    except Exception as e:
        result["errors"].append(str(e))
        log(conn, sid, "error", str(e), 0, 0, uid)
    return result


async def run_all(conn, only_ids=None, uid=None) -> list:
    all_srcs = get_sources(conn, uid)
    srcs = [s for s in all_srcs if s.get("enabled")]
    if only_ids:
        srcs = [s for s in all_srcs if s["id"] in only_ids]
    out = []
    for s in srcs:
        out.append(await run_source(conn, s, uid=uid))
    return out


def auto_analyze_new(conn, company, cfg, uid=None) -> int:
    """Анализирует новые закупки пользователя, у которых ещё нет заключения (без LLM — быстро, для отсева)."""
    from . import ai
    rows = conn.execute("SELECT * FROM tenders WHERE user_id=? AND analysis IS NULL ORDER BY id DESC LIMIT 40",
                        (db._uid(conn, uid),)).fetchall()
    done = 0
    threshold = float(cfg.get("match_threshold") or 0)
    for row in rows:
        t = dict(row)
        match = scoring.compute_match(company, t)
        if threshold and match["score"] < threshold:
            continue
        margin = scoring.compute_margin(company, t)
        a = ai.heuristic_analysis(company, t, match, margin)
        a["computed_margin"] = margin
        a["computed_match"] = match["score"]
        a["analyzed_at"] = db.now_iso()
        conn.execute("UPDATE tenders SET analysis=?, analysis_source='heuristic', analyzed_at=? WHERE id=?",
                     (json.dumps(a, ensure_ascii=False), db.now_iso(), t["id"]))
        db.add_event(conn, t["id"], "ai", "Автоматический отсев при сборе: " +
                     {"go": "идём", "consider": "спорно", "no_go": "не идём"}.get(a["verdict"], a["verdict"]))
        done += 1
    conn.commit()
    return done
