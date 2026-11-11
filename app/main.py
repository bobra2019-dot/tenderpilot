# -*- coding: utf-8 -*-
"""TenderPilot — закрытая CRM-платформа автопастера тендеров с ИИ-анализом."""
import io
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ai, db, ingest, notify, scheduler, scoring, sources as src_mod
from .catalog import CATEGORIES, CATEGORY_BY_ID, PLATFORM_BY_ID, PLATFORMS, REGIONS, STAGES

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

app = FastAPI(title="TenderPilot", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.on_event("startup")
def _startup():
    db.init_db()
    ingest.ensure_dirs()
    scheduler.start()


@app.on_event("shutdown")
def _shutdown():
    scheduler.stop()


# ---------------- авторизация ----------------
class LoginIn(BaseModel):
    email: str
    password: str


def current_user(request: Request):
    conn = db.connect()
    try:
        token = request.cookies.get("tp_session") or request.headers.get("x-session")
        user = db.user_by_session(conn, token)
    finally:
        conn.close()
    if not user:
        raise HTTPException(status_code=401, detail="Требуется вход")
    return user


@app.post("/api/login")
def login(data: LoginIn, response: Response):
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE email=?", (data.email.strip().lower(),)).fetchone()
        if not row or row["pass_hash"] != db.hash_pass(data.password):
            raise HTTPException(status_code=401, detail="Неверный email или пароль")
        token = db.create_session(conn, row["id"])
        user = {"id": row["id"], "email": row["email"], "name": row["name"]}
    finally:
        conn.close()
    response.set_cookie("tp_session", token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 14)
    return {"ok": True, "user": user, "token": token}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("tp_session")
    conn = db.connect()
    try:
        if token:
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            conn.commit()
    finally:
        conn.close()
    response.delete_cookie("tp_session")
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return {"user": {k: user[k] for k in ("id", "email", "name")}}


# ---------------- справочники и профиль ----------------
@app.get("/api/catalog")
def catalog(user=Depends(current_user)):
    return {"platforms": PLATFORMS, "regions": REGIONS, "categories": CATEGORIES, "stages": STAGES}


@app.get("/api/company")
def get_company(user=Depends(current_user)):
    conn = db.connect()
    try:
        return {"company": db.get_company(conn)}
    finally:
        conn.close()


@app.put("/api/company")
def put_company(payload: dict, user=Depends(current_user)):
    conn = db.connect()
    try:
        cur = db.get_company(conn)
        for k, v in (payload or {}).items():
            if k in ("economics", "scoring") and isinstance(v, dict):
                cur.setdefault(k, {})
                cur[k].update(v)
            else:
                cur[k] = v
        db.set_company(conn, cur)
        return {"ok": True, "company": cur}
    finally:
        conn.close()


# ---------------- настройки ИИ ----------------
@app.get("/api/ai/settings")
def ai_settings(user=Depends(current_user)):
    conn = db.connect()
    try:
        return {"settings": db.get_ai_settings(conn), "presets": db.PROVIDER_PRESETS}
    finally:
        conn.close()


@app.put("/api/ai/settings")
def ai_settings_put(payload: dict, user=Depends(current_user)):
    conn = db.connect()
    try:
        db.set_ai_settings(conn, payload or {})
        return {"ok": True, "settings": db.get_ai_settings(conn)}
    finally:
        conn.close()


@app.post("/api/ai/test")
async def ai_test(user=Depends(current_user)):
    conn = db.connect()
    try:
        cfg = db.get_ai_settings(conn, mask=False)
    finally:
        conn.close()
    preset = db.PROVIDER_PRESETS.get(cfg.get("provider", ""), {})
    cfg["kind"] = preset.get("kind", "openai")
    try:
        text = await ai.call_llm(cfg, "Ты — тестовый ассистент. Ответь одним словом.", "Ответь: работает", max_tokens=20)
        return {"ok": True, "model": cfg.get("model"), "answer": (text or "").strip()[:120]}
    except Exception as e:
        return JSONResponse(status_code=200, content={"ok": False, "error": str(e)})


# ---------------- сериализация тендеров ----------------
def row_to_tender(conn, row, company=None):
    t = dict(row)
    company = company or db.get_company(conn)
    try:
        t["requirements"] = json.loads(t.get("requirements") or "[]")
    except Exception:
        t["requirements"] = []
    try:
        t["files"] = json.loads(t.get("files") or "[]")
    except Exception:
        t["files"] = []
    try:
        t["analysis"] = json.loads(t["analysis"]) if t.get("analysis") else None
    except Exception:
        t["analysis"] = None
    p = PLATFORM_BY_ID.get(t["platform"], {"name": t["platform"], "short": t["platform"]})
    t["platform_name"] = p["name"]
    t["platform_short"] = p.get("short", p["name"])
    cat = CATEGORY_BY_ID.get(t["category"] or "")
    t["category_name"] = cat["name"] if cat else "—"
    t["days_left"] = scoring._days_left(t)
    t["match"] = scoring.compute_match(company, t)
    t["margin"] = scoring.compute_margin(company, t)
    st = next((s for s in STAGES if s["id"] == t.get("stage")), None)
    t["stage_name"] = st["name"] if st else t.get("stage")
    return t


@app.get("/api/tenders")
def list_tenders(user=Depends(current_user), q: str = "", stage: str = "", platform: str = "",
                law: str = "", category: str = "", verdict: str = "", sort: str = "match",
                limit: int = 200, ingested: str = ""):
    conn = db.connect()
    try:
        company = db.get_company(conn)
        rows = conn.execute("SELECT * FROM tenders ORDER BY id").fetchall()
        items = [row_to_tender(conn, r, company) for r in rows]
    finally:
        conn.close()

    if q:
        ql = q.lower()
        items = [t for t in items if ql in (t["title"] or "").lower() or ql in (t["customer"] or "").lower()
                 or ql in (t["ext_id"] or "").lower() or ql in (t["tz_text"] or "").lower()]
    if stage:
        items = [t for t in items if t["stage"] == stage]
    if platform:
        items = [t for t in items if t["platform"] == platform]
    if law:
        items = [t for t in items if t["law"] == law]
    if category:
        items = [t for t in items if t["category"] == category]
    if ingested == "1":
        items = [t for t in items if t["ingested"]]
    if verdict == "go":
        items = [t for t in items if (t["analysis"] or {}).get("verdict") == "go"]
    elif verdict == "consider":
        items = [t for t in items if (t["analysis"] or {}).get("verdict") == "consider"]
    elif verdict == "no_go":
        items = [t for t in items if (t["analysis"] or {}).get("verdict") == "no_go"]
    elif verdict == "unanalyzed":
        items = [t for t in items if not t["analysis"]]

    if sort == "match":
        items.sort(key=lambda t: -t["match"]["score"])
    elif sort == "deadline":
        items.sort(key=lambda t: t["deadline"] or "")
    elif sort == "sum":
        items.sort(key=lambda t: -(t["sum"] or 0))
    elif sort == "margin":
        items.sort(key=lambda t: -(t["margin"]["margin_pct"] or 0))
    return {"items": items[:limit], "total": len(items)}


@app.get("/api/tenders/{tid}")
def get_tender(tid: int, user=Depends(current_user)):
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM tenders WHERE id=?", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, "Закупка не найдена")
        company = db.get_company(conn)
        t = row_to_tender(conn, row, company)
        events = [dict(e) for e in conn.execute(
            "SELECT * FROM events WHERE tender_id=? ORDER BY id DESC", (tid,)).fetchall()]
    finally:
        conn.close()
    t["events"] = events
    return {"tender": t}


class PatchIn(BaseModel):
    stage: Optional[str] = None
    note: Optional[str] = None


@app.patch("/api/tenders/{tid}")
def patch_tender(tid: int, data: PatchIn, user=Depends(current_user)):
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM tenders WHERE id=?", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, "Не найдено")
        if data.stage:
            names = {s["id"]: s["name"] for s in STAGES}
            if data.stage not in names:
                raise HTTPException(400, "Неизвестный этап")
            conn.execute("UPDATE tenders SET stage=? WHERE id=?", (data.stage, tid))
            db.add_event(conn, tid, "stage", f"Этап изменён: {names[data.stage]}")
        if data.note is not None:
            conn.execute("UPDATE tenders SET note=? WHERE id=?", (data.note, tid))
            db.add_event(conn, tid, "note", "Заметка обновлена")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


class MarginIn(BaseModel):
    overrides: dict = {}


@app.post("/api/tenders/{tid}/margin")
def recalc_margin(tid: int, data: MarginIn, user=Depends(current_user)):
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM tenders WHERE id=?", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, "Не найдено")
        company = db.get_company(conn)
        t = dict(row)
        return {"margin": scoring.compute_margin(company, t, data.overrides or {})}
    finally:
        conn.close()


# ---------------- ИИ-анализ ----------------
class AnalyzeIn(BaseModel):
    question: Optional[str] = None
    force_llm: bool = True


@app.post("/api/tenders/{tid}/analyze")
async def analyze_tender(tid: int, data: AnalyzeIn = AnalyzeIn(), user=Depends(current_user)):
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM tenders WHERE id=?", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, "Не найдено")
        company = db.get_company(conn)
        cfg_masked = db.get_ai_settings(conn)
        cfg = db.get_ai_settings(conn, mask=False)
    finally:
        conn.close()

    t = dict(row)
    match = scoring.compute_match(company, t)
    margin = scoring.compute_margin(company, t)

    analysis = None
    warning = None
    if data.force_llm and cfg_masked.get("api_key_set"):
        preset = db.PROVIDER_PRESETS.get(cfg.get("provider", ""), {})
        cfg["kind"] = preset.get("kind", "openai")
        try:
            user_prompt = ai.build_prompt(company, t, match, margin, data.question)
            raw = await ai.call_llm(cfg, ai.SYSTEM_PROMPT, user_prompt)
            parsed = ai._extract_json(raw)
            if parsed:
                analysis = ai.normalize_analysis(parsed, company, t, match, margin)
            else:
                analysis = ai.heuristic_analysis(company, t, match, margin)
                warning = "LLM вернул не-JSON, применён встроенный расчёт"
        except Exception as e:
            analysis = ai.heuristic_analysis(company, t, match, margin)
            warning = f"Ошибка LLM: {e}"
    else:
        analysis = ai.heuristic_analysis(company, t, match, margin)
        if data.force_llm:
            warning = "API-ключ ИИ не задан — использован встроенный расчёт. Добавьте ключ в «Настройки ИИ»."

    analysis["computed_margin"] = margin
    analysis["computed_match"] = match["score"]
    analysis["analyzed_at"] = db.now_iso()
    if data.question:
        analysis["question"] = data.question

    conn = db.connect()
    try:
        conn.execute(
            "UPDATE tenders SET analysis=?, analysis_source=?, analyzed_at=? WHERE id=?",
            (json.dumps(analysis, ensure_ascii=False), analysis.get("source", "heuristic"), db.now_iso(), tid),
        )
        conn.commit()
        db.add_event(conn, tid, "ai",
                     f"ИИ-анализ: { {'go':'идём','consider':'спорно','no_go':'не идём'}.get(analysis['verdict'], analysis['verdict']) } "
                     f"({analysis.get('source')})")
    finally:
        conn.close()

    return {"analysis": analysis, "match": match, "margin": margin, "warning": warning}


# ---------------- парсер закупок (вставка текста / ссылка / файл) ----------------
def _extract_text_from_docx(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = xml.replace("</w:p>", "\n")
    return re.sub(r"<[^>]+>", "", xml)


def _extract_text_from_pdf(blob: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(blob))
    return "\n".join((p.extract_text() or "") for p in reader.pages[:40])


def guess_fields(text: str, company: dict):
    t = text or ""
    low = t.lower()
    out = {}

    money = re.findall(r"(\d[\d\s.,]{4,})\s*(?:руб|₽|российских рублей)", low)
    if money:
        raw = re.sub(r"[^\d.,]", "", money[0]).replace(",", ".")
        try:
            out["sum"] = float(raw)
        except Exception:
            pass
    if "sum" not in out:
        nmck = re.search(r"(?:нмцк|начальная \(максимальная\) цена|цена контракта)[^\d]{0,40}([\d\s]{6,})", low)
        if nmck:
            try:
                out["sum"] = float(re.sub(r"\s", "", nmck.group(1)))
            except Exception:
                pass

    num = re.search(r"\b(\d{19})\b", t)
    if num:
        out["ext_id"] = num.group(1)

    okpd = re.search(r"(?:окпд2?|окпд)\D{0,10}(\d{2}\.\d{2}(?:\.\d{1,3})?)", low)
    if okpd:
        out["okpd"] = okpd.group(1)

    region = next((r for r in REGIONS if r.lower() in low), "")
    if region:
        out["region"] = region
    else:
        out["region"] = company.get("region") or "Москва"

    law = "44-ФЗ" if "44-фз" in low else ("223-ФЗ" if "223-фз" in low else "коммерч.")
    out["law"] = law

    platform = ""
    for pid, p in PLATFORM_BY_ID.items():
        if p["name"].split(" (")[0].lower() in low or (p.get("short") or "").lower() in low:
            platform = pid
            break
    out["platform"] = platform or "eis"

    days = re.search(r"(?:срок поставки|поставка)[^\d]{0,40}(\d{1,3})\s*(?:календарн\w* |рабоч\w* )?дн", low)
    out["delivery_days"] = int(days.group(1)) if days else 45

    deadline = re.search(r"(\d{2})[.\-/](\d{2})[.\-/](\d{4})", t)
    if deadline:
        d, m, y = deadline.groups()
        out["deadline"] = f"{y}-{m}-{d}T10:00"

    # категория — по ключевым словам
    best, best_hits = None, 0
    for cat in CATEGORIES:
        hits = sum(1 for k in cat["keywords"] if k in low)
        if hits > best_hits:
            best, best_hits = cat["id"], hits
    out["category"] = best or (company.get("categories") or ["valves"])[0]

    # требования — строки с маркерами
    reqs = []
    chunks = re.split(r"[\n;•]|\.\s+(?=[А-ЯЁA-Z«])", t)
    for line in chunks:
        s = line.strip(" \t•-—")
        if len(s) < 12 or len(s) > 300:
            continue
        sl = s.lower()
        typ = None
        if any(k in sl for k in ("сертификат", "декларац", "допуск", "сро", "исо", "гост", "тр тс", "лиценз")):
            typ = "certificate"
        elif any(k in sl for k in ("опыт", "реестр", "аналогичн")):
            typ = "experience"
        elif any(k in sl for k in ("срок", "дней", "дн.")):
            typ = "timing"
        elif any(k in sl for k in ("обеспечен", "аванс", "оплат", "отсрочк")):
            typ = "finance"
        elif any(k in sl for k in ("постав", "изготов", "товар", "издели")):
            typ = "product"
        if typ:
            reqs.append({"type": typ, "text": s[:240]})
        if len(reqs) >= 14:
            break
    out["requirements"] = reqs

    # наименование
    lines = [l.strip() for l in t.splitlines() if len(l.strip()) > 18]
    title = ""
    m = re.search(r"((?:Поставка|Закупка|Изготовление|Оказание услуг|Выполнение работ|Приобретение)[^.;\n]{12,180})", t, re.I)
    if m:
        title = m.group(1)
    if not title:
        m = re.search(r"(?:наименование|предмет(?: договора| контракта)?|объект закупки)[:\s\-]+([^\n.;]{15,190})", t, re.I)
        if m:
            title = m.group(1)
    if not title:
        title = re.split(r"(?<=[.!?])\s", lines[0])[0] if lines else t.strip()[:120]
    out["title"] = re.sub(r"\s{2,}", " ", title).strip()[:180] or "Закупка, добавленная вручную"
    out["description"] = t[:400]
    out["customer"] = ""
    cust = re.search(r"(?:заказчик|организатор)[:\s]+([А-ЯA-Z«\"][^\n]{3,90})", t)
    if cust:
        out["customer"] = cust.group(1).strip()
    out["tz_text"] = t[:20000]
    return out


class ParseIn(BaseModel):
    text: str = ""
    url: str = ""


@app.post("/api/tenders/parse")
async def parse_tender(data: ParseIn, user=Depends(current_user)):
    text = data.text or ""
    note = None
    if data.url:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                         headers={"User-Agent": "Mozilla/5.0 (compatible; TenderPilot/1.0)"}) as cl:
                r = await cl.get(data.url)
            html = r.text
            cleaned = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html, flags=re.I)
            cleaned = re.sub(r"<[^>]+>", "\n", cleaned)
            cleaned = re.sub(r"\n{2,}", "\n", cleaned)
            cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
            text = (text + "\n" + cleaned)[:40000]
            note = "Текст загружен по ссылке" if len(cleaned) > 200 else "По ссылке получено мало текста — добавьте ТЗ вручную"
        except Exception as e:
            note = f"Не удалось открыть ссылку ({e}). Вставьте текст закупки вручную."

    if len(text.strip()) < 40:
        raise HTTPException(400, "Слишком мало текста для распознавания (минимум ~40 символов)")

    conn = db.connect()
    try:
        company = db.get_company(conn)
        f = guess_fields(text, company)
        cur = conn.execute(
            """INSERT INTO tenders (ext_id, platform, law, category, title, customer, customer_inn, region, okpd,
               qty, unit, sum, published, deadline, delivery_days, payment_terms, bid_security_pct,
               contract_security_pct, penalty, description, tz_text, requirements, files, source_url,
               ingested, stage, manual, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f.get("ext_id") or f"MANUAL-{int(datetime.now().timestamp())}",
                f["platform"], f["law"], f["category"], f["title"], f.get("customer") or "Не определён",
                "", f["region"], f.get("okpd") or "", 1, "компл", f.get("sum") or 0,
                datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                f.get("deadline") or (datetime.now(timezone.utc).strftime("%Y-%m-%dT10:00")),
                f["delivery_days"], "", 0, 0, "", f.get("description") or "",
                f["tz_text"], json.dumps(f["requirements"], ensure_ascii=False), "[]", data.url or "",
                0, "new", 1, db.now_iso(),
            ),
        )
        tid = cur.lastrowid
        conn.commit()
        db.add_event(conn, tid, "parse", "Закупка добавлена вручную и распознана парсером")
        row = conn.execute("SELECT * FROM tenders WHERE id=?", (tid,)).fetchone()
        tender = row_to_tender(conn, row, company)
    finally:
        conn.close()
    return {"tender": tender, "note": note, "recognized": {k: f.get(k) for k in ("title", "sum", "law", "platform", "region", "okpd", "deadline", "delivery_days", "category")}}


@app.post("/api/tenders/parse-file")
async def parse_file(file: UploadFile = File(...), user=Depends(current_user)):
    blob = await file.read()
    name = (file.filename or "").lower()
    try:
        if name.endswith(".docx"):
            text = _extract_text_from_docx(blob)
        elif name.endswith(".pdf"):
            text = _extract_text_from_pdf(blob)
        else:
            text = blob.decode("utf-8", "ignore")
    except Exception as e:
        raise HTTPException(400, f"Не удалось прочитать файл: {e}")
    if len(text.strip()) < 40:
        raise HTTPException(400, "В файле не найдено текста (возможно, скан без OCR)")
    return await parse_tender(ParseIn(text=text[:40000], url=""), user)


# ---------------- мониторинг площадок ----------------
@app.post("/api/sync")
def sync_platforms(user=Depends(current_user), payload: dict = None):
    platforms = (payload or {}).get("platforms") or []
    conn = db.connect()
    try:
        q = "SELECT * FROM tenders WHERE ingested=0"
        rows = conn.execute(q).fetchall()
        found = []
        for r in rows:
            if platforms and r["platform"] not in platforms:
                continue
            conn.execute("UPDATE tenders SET ingested=1 WHERE id=?", (r["id"],))
            db.add_event(conn, r["id"], "found",
                         f"Закупка найдена на площадке {PLATFORM_BY_ID.get(r['platform'], {}).get('name', r['platform'])}")
            found.append({"id": r["id"], "title": r["title"], "platform": r["platform"], "sum": r["sum"]})
        conn.commit()
        conn.execute("INSERT INTO sync_log (created_at, text) VALUES (?,?)",
                     (db.now_iso(), f"Сканирование площадок: найдено {len(found)} новых закупок"))
        conn.commit()
    finally:
        conn.close()
    return {"found": found, "count": len(found)}


@app.get("/api/activity")
def activity(user=Depends(current_user), limit: int = 30):
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT e.*, t.title FROM events e JOIN tenders t ON t.id=e.tender_id
               ORDER BY e.id DESC LIMIT ?""", (limit,)).fetchall()
        sync = conn.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 5").fetchall()
    finally:
        conn.close()
    return {"events": [dict(r) for r in rows], "sync": [dict(r) for r in sync]}


@app.get("/api/stats")
def stats(user=Depends(current_user)):
    conn = db.connect()
    try:
        company = db.get_company(conn)
        rows = conn.execute("SELECT * FROM tenders").fetchall()
        items = [row_to_tender(conn, r, company) for r in rows]
    finally:
        conn.close()

    def sm(lst, f):
        return sum(f(x) or 0 for x in lst)

    analyzed = [t for t in items if t["analysis"]]
    go = [t for t in items if (t["analysis"] or {}).get("verdict") == "go"]
    consider = [t for t in items if (t["analysis"] or {}).get("verdict") == "consider"]
    no_go = [t for t in items if (t["analysis"] or {}).get("verdict") == "no_go"]
    pipeline = [t for t in items if t["stage"] in ("go", "bid", "review")]
    won = [t for t in items if t["stage"] == "won"]
    bid = [t for t in items if t["stage"] in ("won", "lost", "bid")]

    by_platform = {}
    for t in items:
        by_platform.setdefault(t["platform_short"], {"count": 0, "sum": 0, "go": 0})
        by_platform[t["platform_short"]]["count"] += 1
        by_platform[t["platform_short"]]["sum"] += t["sum"] or 0
        if (t["analysis"] or {}).get("verdict") == "go":
            by_platform[t["platform_short"]]["go"] += 1

    return {
        "totals": {
            "tenders": len(items),
            "analyzed": len(analyzed),
            "go": len(go),
            "consider": len(consider),
            "no_go": len(no_go),
            "sum_total": sm(items, lambda t: t["sum"]),
            "sum_go": sm(go, lambda t: t["sum"]),
            "avg_margin_go": round(sm(go, lambda t: t["margin"]["margin_pct"]) / len(go), 1) if go else 0,
            "pipeline_sum": sm(pipeline, lambda t: t["sum"]),
            "won_sum": sm(won, lambda t: t["sum"]),
            "win_rate": round(len(won) / len(bid) * 100) if bid else 0,
        },
        "by_platform": by_platform,
        "top": sorted(go, key=lambda t: -(t["match"]["score"] + t["margin"]["margin_pct"]))[:5],
        "deadline_soon": sorted([t for t in items if 0 <= t["days_left"] <= 7], key=lambda t: t["days_left"])[:6],
    }


# ---------------- источники данных (реальный сбор) ----------------
@app.get("/api/sources")
def sources_list(user=Depends(current_user)):
    conn = db.connect()
    try:
        return {"sources": ingest.get_sources(conn), "schedule": scheduler.get_schedule(conn)}
    finally:
        conn.close()


@app.put("/api/sources")
def sources_put(payload: dict, user=Depends(current_user)):
    conn = db.connect()
    try:
        items = payload.get("sources")
        if isinstance(items, list):
            ingest.save_sources(conn, items)
        if isinstance(payload.get("schedule"), dict):
            scheduler.set_schedule(conn, payload["schedule"])
        return {"ok": True, "sources": ingest.get_sources(conn), "schedule": scheduler.get_schedule(conn)}
    finally:
        conn.close()


class RunSourceIn(BaseModel):
    ids: Optional[list] = None
    analyze: bool = True


@app.post("/api/sources/run")
async def sources_run(data: RunSourceIn = RunSourceIn(), user=Depends(current_user)):
    conn = db.connect()
    try:
        before = conn.execute("SELECT COUNT(*) c FROM tenders").fetchone()["c"]
        results = await ingest.run_all(conn, data.ids)
        after = conn.execute("SELECT COUNT(*) c FROM tenders").fetchone()["c"]
        analyzed = 0
        if data.analyze and after > before:
            company = db.get_company(conn)
            cfg = db.get_ai_settings(conn)
            analyzed = ingest.auto_analyze_new(conn, company, cfg)
        return {"results": results, "new": after - before, "analyzed": analyzed}
    finally:
        conn.close()


@app.get("/api/logs")
def logs(user=Depends(current_user), limit: int = 60):
    conn = db.connect()
    try:
        rows = conn.execute("SELECT * FROM ingest_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        inbox_files = []
        if os.path.isdir(ingest.INBOX):
            for f in sorted(os.listdir(ingest.INBOX)):
                fp = os.path.join(ingest.INBOX, f)
                if os.path.isfile(fp):
                    inbox_files.append({"name": f, "size": os.path.getsize(fp)})
    finally:
        conn.close()
    return {"logs": [dict(r) for r in rows], "inbox": inbox_files, "inbox_path": ingest.INBOX}


@app.post("/api/ingest/upload")
async def ingest_upload(file: UploadFile = File(...), user=Depends(current_user)):
    """Загрузка файла выгрузки: XML ЕИС, CSV/Excel-выгрузка ЭТП, ZIP-архив."""
    ingest.ensure_dirs()
    name = (file.filename or "upload.bin").replace("/", "_").replace("\\", "_")
    blob = await file.read()
    if len(blob) > 60 * 1024 * 1024:
        raise HTTPException(400, "Файл больше 60 МБ — загружайте частями")
    path = os.path.join(ingest.INBOX, name)
    with open(path, "wb") as f:
        f.write(blob)

    low = name.lower()
    items = []
    try:
        if low.endswith(".zip"):
            import zipfile as _zf
            with _zf.ZipFile(path) as z:
                for inner in z.namelist():
                    if inner.lower().endswith((".xml", ".csv")):
                        text = z.read(inner).decode("utf-8", "ignore")
                        items += src_mod.parse_eis_xml(text) if inner.lower().endswith(".xml") else src_mod.parse_csv_rows(text)
        elif low.endswith(".xml"):
            items = src_mod.parse_eis_xml(blob.decode("utf-8", "ignore"))
        elif low.endswith((".csv", ".txt")):
            items = src_mod.parse_csv_rows(blob.decode("utf-8", "ignore"))
        else:
            raise HTTPException(400, "Поддерживаются XML, CSV, TXT и ZIP")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Не удалось разобрать файл: {e}")

    conn = db.connect()
    try:
        added = 0
        for t in items:
            if ingest.save_tender(conn, t):
                added += 1
        conn.commit()
        ingest.log(conn, "upload", "ok", f"{name}: распознано {len(items)}, новых {added}", len(items), added)
        company = db.get_company(conn)
        cfg = db.get_ai_settings(conn)
        analyzed = ingest.auto_analyze_new(conn, company, cfg) if added else 0
    finally:
        conn.close()
    return {"file": name, "recognized": len(items), "added": added, "analyzed": analyzed}


@app.post("/api/ingest/scan-inbox")
def scan_inbox(user=Depends(current_user)):
    conn = db.connect()
    try:
        ingest.ensure_dirs()
        res = src_mod.parse_files_in_inbox(ingest.INBOX, ingest.PROCESSED)
        added = 0
        for t in res["items"]:
            if ingest.save_tender(conn, t):
                added += 1
        conn.commit()
        ingest.log(conn, "inbox", "ok" if not res["errors"] else "warn",
                   f"файлов {len(res['files'])}, распознано {len(res['items'])}, новых {added}"
                   + ("; ошибки: " + "; ".join(res["errors"][:3]) if res["errors"] else ""),
                   len(res["items"]), added)
        company = db.get_company(conn)
        cfg = db.get_ai_settings(conn)
        analyzed = ingest.auto_analyze_new(conn, company, cfg) if added else 0
        return {"files": res["files"], "recognized": len(res["items"]), "added": added,
                "analyzed": analyzed, "errors": res["errors"]}
    finally:
        conn.close()


@app.get("/api/sources/check")
async def sources_check(user=Depends(current_user)):
    """Проверка доступности площадок из текущей сети (важно для российских IP)."""
    import httpx as _httpx
    targets = {
        "ЕИС (zakupki.gov.ru)": "https://zakupki.gov.ru/epz/order/extendedsearch/results.html",
        "Росэлторг": "https://www.roseltorg.ru",
        "B2B-Center": "https://www.b2b-center.ru",
        "ЭТП ГПБ": "https://etpgpb.ru",
        "ТЭК-Торг": "https://tektorg.ru",
        "Росатом": "https://zakupki.rosatom.ru",
        "Сбер А": "https://www.sberbank-ast.ru",
    }
    import asyncio as _asyncio

    async with _httpx.AsyncClient(timeout=_httpx.Timeout(6.0, connect=4.0), follow_redirects=True,
                                  headers={"User-Agent": src_mod.UA}) as client:
        async def probe(name, url):
            try:
                r = await client.get(url)
                return {"name": name, "url": url, "status": r.status_code, "ok": r.status_code < 400}
            except Exception as e:
                return {"name": name, "url": url, "status": None, "ok": False, "error": str(e)[:120]}

        out = list(await _asyncio.gather(*[probe(n, u) for n, u in targets.items()]))
    return {"results": out}


# ---------------- уведомления ----------------
@app.get("/api/notify")
def notify_get(user=Depends(current_user)):
    conn = db.connect()
    try:
        return {"notify": notify.get_notify(conn)}
    finally:
        conn.close()


@app.put("/api/notify")
def notify_put(payload: dict, user=Depends(current_user)):
    conn = db.connect()
    try:
        notify.set_notify(conn, payload or {})
        return {"ok": True, "notify": notify.get_notify(conn)}
    finally:
        conn.close()


@app.post("/api/notify/test")
async def notify_test(user=Depends(current_user)):
    conn = db.connect()
    try:
        cfg = notify.get_notify(conn, mask=False)
    finally:
        conn.close()
    text = "🔔 TenderPilot: уведомления настроены. Сюда будут приходить закупки с вердиктом «идём»."
    res = await notify.telegram_send(cfg, text)
    if not res.get("ok") and cfg.get("email", {}).get("enabled"):
        res = notify.email_send(cfg, "TenderPilot: проверка", text)
    return res


# ---------------- статика ----------------
@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


# ---------------- скачивание проекта (обходит ограничение превью на загрузку файлов) ----------------
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "dist"}


def _project_files():
    out = []
    for root, dirs, files in os.walk(PROJECT_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".pyc") or "-wal" in f or "-shm" in f:
                continue
            if f in ("tenderpilot_single.py", "build_single.py"):
                continue  # не включаем установщик в архив самого проекта
            full = os.path.join(root, f)
            out.append((full, os.path.relpath(full, PROJECT_DIR)))
    return sorted(out, key=lambda x: x[1])


def _build_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for full, rel in _project_files():
            z.write(full, os.path.join("TenderPilot", rel))
    return buf.getvalue()


@app.get("/download/TenderPilot.zip")
def download_zip():
    data = _build_zip()
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="TenderPilot.zip"'},
    )


@app.get("/download/tenderpilot.py")
def download_single_file():
    path = os.path.join(PROJECT_DIR, "tenderpilot_single.py")
    if not os.path.isfile(path):
        raise HTTPException(404, "Одностраничный установщик не собран")
    return FileResponse(path, media_type="text/x-python", filename="tenderpilot.py")


PAGE_CSS = """
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial,sans-serif;background:#f4f6f9;
color:#0f172a;margin:0;padding:26px 16px;line-height:1.5}
.wrap{max-width:820px;margin:0 auto}
.card{background:#fff;border:1px solid #e2e8ef;border-radius:16px;padding:24px 26px;margin-bottom:14px;
box-shadow:0 8px 24px -14px rgba(15,23,42,.25)}
h1{margin:0 0 6px;font-size:24px;letter-spacing:-.4px}h2{margin:0 0 8px;font-size:16px}
p{color:#475569;font-size:14px;margin:6px 0}
.badge{display:inline-block;background:#1d4ed8;color:#fff;font-size:11px;font-weight:700;padding:3px 9px;
border-radius:20px;letter-spacing:.03em;vertical-align:middle;margin-left:8px}
.badge.gray{background:#e2e8f5;color:#334155}
.btn{display:inline-block;background:#1d4ed8;color:#fff;text-decoration:none;font-weight:650;padding:13px 22px;
border-radius:11px;margin:10px 8px 4px 0;font-size:15px;border:none;cursor:pointer;font-family:inherit}
.btn.alt{background:#fff;color:#1d4ed8;border:1px solid #c7d6ef}
.btn.sm{padding:8px 14px;font-size:13px;border-radius:9px}
.warn{background:#fff8e8;border:1px solid #f3e2c0;border-radius:12px;padding:14px 16px;font-size:13.5px;color:#7a4b06;margin:10px 0}
.warn b{color:#5b3704}
code{background:#f1f5f9;padding:2px 6px;border-radius:5px;font-size:12.8px;font-family:ui-monospace,Menlo,monospace}
pre{background:#0f172a;color:#e2e8f5;padding:14px;border-radius:11px;overflow:auto;font-size:12.5px;line-height:1.55}
ol,ul{margin:8px 0;padding-left:22px;color:#475569;font-size:13.5px}li{margin:6px 0}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}
td{padding:7px 4px;border-bottom:1px solid #eef2f6}a{color:#1d4ed8;text-decoration:none}
a:hover{text-decoration:underline}
small{color:#94a3b8;font-size:12px}
textarea{width:100%;height:220px;font-family:ui-monospace,Menlo,monospace;font-size:11px;line-height:1.4;
border:1px solid #e2e8ef;border-radius:11px;padding:10px;background:#fbfcfe;resize:vertical}
.step{display:flex;gap:12px}.num{width:26px;height:26px;flex:0 0 26px;border-radius:50%;background:#eef4ff;
color:#1d4ed8;font-weight:700;display:grid;place-items:center;font-size:13px}
"""


@app.get("/download")
def download_page():
    rows = []
    for full, rel in _project_files():
        if rel.startswith("tenderpilot_single.py") or rel.startswith("build_single.py"):
            continue
        size = os.path.getsize(full)
        rows.append(
            f'<tr><td><a href="/download/raw/{rel}">{rel}</a></td>'
            f'<td style="text-align:right;color:#94a3b8;white-space:nowrap">{size/1024:.1f} КБ</td></tr>'
        )
    has_single = os.path.isfile(os.path.join(PROJECT_DIR, "tenderpilot_single.py"))
    html = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Скачать TenderPilot</title><style>{PAGE_CSS}</style></head><body><div class="wrap">

<div class="card">
  <h1>Скачать TenderPilot</h1>
  <p>Проект целиком: бэкенд, интерфейс, демо-база и инструкция.</p>
  <div id="frameWarn"></div>
  <a class="btn" href="/download/TenderPilot.zip" download>⬇ Скачать архив TenderPilot.zip</a>
  {f'<a class="btn alt" href="/single">Способ без скачивания →</a>' if has_single else ''}
</div>

<div class="card">
  <h2>Если кнопка ничего не делает <span class="badge gray">частая ситуация</span></h2>
  <p>Вы сейчас смотрите приложение внутри предпросмотра. Предпросмотр — это песочница (iframe),
  в которой браузер <b>запрещает любые загрузки файлов</b>. Это ограничение платформы, а не архива.</p>
  <div class="step" style="margin-top:12px"><div class="num">1</div><div>
    <p><b>Откройте сайт в отдельной вкладке.</b> В панели предпросмотра нажмите значок <b>↗</b>
    («открыть в новой вкладке») и добавьте в конец адреса <code>/download</code>.</p>
  </div></div>
  <div class="step" style="margin-top:10px"><div class="num">2</div><div>
    <p><b>Либо вообще без скачивания:</b> откройте страницу
    <a href="/single">/single</a> — там весь проект одним файлом, который можно скопировать
    и сохранить у себя.</p>
  </div></div>
</div>

<div class="card">
  <h2>Файлы по отдельности</h2>
  <p><small>Ссылки открывают текст файла в браузере — его можно скопировать
  (Ctrl+A, затем Ctrl+C). Так работает и внутри предпросмотра.</small></p>
  <table><tr><td><b>Файл</b></td><td style="text-align:right"><b>Размер</b></td></tr>
  {''.join(rows)}
  </table>
</div>

<div class="card">
  <h2>Что делать с архивом</h2>
  <pre>cd TenderPilot
./bootstrap.sh</pre>
  <p>Откройте <code>http://localhost:8000</code>, вход: <code>owner@zavod.ru</code> / <code>demo1234</code>.</p>
  <p><small>Windows без bash: <code>pip install -r requirements.txt</code>, затем
  <code>python -m uvicorn app.main:app --port 8000</code></small></p>
</div>

<script>
if (window.self !== window.top) {{
  var box = document.getElementById('frameWarn');
  box.innerHTML = '<div class="warn"><b>Вы в предпросмотре.</b> Скачивание здесь заблокировано браузером — ' +
    'кнопка ниже не сработает. Используйте <a href="/single">способ без скачивания</a> ' +
    'или откройте приложение в новой вкладке (значок ↗) и добавьте в адрес <code>/download</code>.</div>';
}}
</script>
</div></body></html>"""
    return Response(content=html, media_type="text/html; charset=utf-8")


@app.get("/single")
def single_page():
    path = os.path.join(PROJECT_DIR, "tenderpilot_single.py")
    if not os.path.isfile(path):
        raise HTTPException(404, "Файл не собран")
    with open(path, encoding="utf-8") as f:
        content = f.read()
    html = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TenderPilot — один файл</title><style>{PAGE_CSS}</style></head><body><div class="wrap">
<div class="card">
  <h1>Способ без скачивания</h1>
  <p>Один файл со всем проектом внутри. Работает, даже если браузер блокирует загрузку файлов.</p>
  <ol>
    <li>Нажмите <b>«Выделить весь код»</b> и скопируйте (Ctrl+C).</li>
    <li>Откройте <b>Блокнот</b> (или любой текстовый редактор) и вставьте (Ctrl+V).</li>
    <li>Сохраните как <code>tenderpilot.py</code> — <b>тип файла «Все файлы»</b>, кодировка <b>UTF-8</b>.</li>
    <li>В терминале в папке с файлом: <code>python tenderpilot.py</code></li>
    <li>Откройте <code>http://localhost:8000</code>, вход <code>owner@zavod.ru</code> / <code>demo1234</code>.</li>
  </ol>
  <button class="btn" id="sel">Выделить весь код</button>
  <a class="btn alt" href="/download/tenderpilot.py" download>⬇ Скачать tenderpilot.py</a>
  <p style="margin-top:14px"><small>Размер: {len(content)/1024:.0f} КБ. Внутри — весь код проекта;
  при запуске файл сам распакует его в папку <code>TenderPilot</code>, поставит зависимости
  и поднимет сервер.</small></p>
</div>
<div class="card">
  <h2>Код файла</h2>
  <p><small>Первые строки — инструкция. Дальше зашитый архив проекта (base64) и код запуска.</small></p>
  <textarea id="code" readonly spellcheck="false">{content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')}</textarea>
</div>
<script>
document.getElementById('sel').addEventListener('click', function () {{
  var t = document.getElementById('code');
  t.focus(); t.select(); t.setSelectionRange(0, t.value.length);
  var ok = false;
  try {{ ok = document.execCommand('copy'); }} catch (e) {{}}
  this.textContent = ok ? '✓ Скопировано — вставьте в Блокнот' : 'Текст выделен — нажмите Ctrl+C';
}});
</script>
</div></body></html>"""
    return Response(content=html, media_type="text/html; charset=utf-8")


@app.get("/download/raw/{path:path}")
def download_raw(path: str):
    target = os.path.normpath(os.path.join(PROJECT_DIR, path))
    if not target.startswith(PROJECT_DIR + os.sep) or not os.path.isfile(target):
        raise HTTPException(404, "Файл не найден")
    if target.endswith((".py", ".md", ".sh", ".txt", ".html", ".css", ".js", ".json", ".db")):
        if target.endswith(".db"):
            return FileResponse(target, media_type="application/octet-stream", filename=os.path.basename(target))
        return FileResponse(target, media_type="text/plain; charset=utf-8")
    return FileResponse(target)
