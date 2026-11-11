# -*- coding: utf-8 -*-
"""Детерминированный движок: скоринг соответствия, проверка требований, расчёт маржи."""
import json
import re
from datetime import datetime, timezone

from .catalog import CATEGORY_BY_ID, REGION_LOGISTICS

CERT_VOCAB = [
    ("ИСО 9001", ["исо 9001", "iso 9001", "9001"]),
    ("ИСО 3834", ["исо 3834", "iso 3834"]),
    ("ТР ТС 010/2011", ["010/2011", "тр тс 010"]),
    ("ТР ТС 032/2013", ["032/2013", "тр тс 032"]),
    ("ТР ТС 004/2011", ["004/2011", "тр тс 004"]),
    ("ТР ТС 020/2011", ["020/2011", "тр тс 020"]),
    ("ТР ТС 012/2011", ["012/2011", "взрывозащ"]),
    ("СРО", ["сро"]),
    ("ГОСТ", ["гост", "госТ Р"]),
    ("СДС ГАЗСЕРТ", ["газсерт"]),
    ("РосатомрегисТр", ["росатомрегис", "роатомрегис"]),
    ("НП-071 / аттестация изготовителя", ["нп-071", "аттестац"]),
]


def _norm(s):
    return (s or "").lower().replace("ё", "е")


def _days_left(tender):
    try:
        dl = datetime.fromisoformat(tender["deadline"])
    except Exception:
        return 0
    if dl.tzinfo is None:
        dl = dl.replace(tzinfo=timezone.utc)
    return (dl - datetime.now(timezone.utc)).days


def _company_okpd_set(company):
    codes = set()
    for c in (company.get("okpd") or []):
        c = str(c).strip()
        if c:
            codes.add(c)
    for cid in (company.get("categories") or []):
        cat = CATEGORY_BY_ID.get(cid)
        if cat:
            codes.update(cat["okpd"])
    return codes


def _company_keywords(company):
    words = set()
    for cid in (company.get("categories") or []):
        cat = CATEGORY_BY_ID.get(cid)
        if cat:
            words.update(_norm(k) for k in cat["keywords"])
    for line in _norm(company.get("products")).replace(";", ",").split(","):
        line = line.strip()
        if len(line) > 2:
            words.add(line)
    return {w for w in words if w}


def okpd_match(codes, tender_okpd):
    t = str(tender_okpd or "")
    if not t:
        return 50, "ОКПД2 в закупке не указан"
    for c in codes:
        if t.startswith(c) or c.startswith(t):
            return 100, f"ОКПД2 {t} совпадает с вашим кодом {c}"
    g4 = {c[:4] for c in codes}
    if t[:4] in g4:
        return 80, f"ОКПД2 {t} входит в вашу товарную группу {t[:4]}"
    if t[:2] in {c[:2] for c in codes}:
        return 45, f"ОКПД2 {t} — смежная отрасль"
    return 5, f"ОКПД2 {t} вне вашей номенклатуры"


def keyword_match(keywords, tender):
    hay = _norm(" ".join([tender["title"] or "", tender["description"] or "", tender["tz_text"] or ""]))
    hits = sorted({w for w in keywords if w and w in hay})
    cat = CATEGORY_BY_ID.get(tender["category"] or "")
    cat_hit = 1 if cat and cat["id"] in hay + " " + (tender["category"] or "") else 0
    if hits:
        score = min(100, 55 + len(hits) * 15)
        return score, "Совпадения по номенклатуре: " + ", ".join(hits[:6])
    if cat_hit:
        return 40, "Категория совпадает, но ключевые слова в ТЗ не найдены"
    return 10, "Номенклатура в ТЗ не пересекается с вашей продукцией"


def region_match(company, tender):
    regions = company.get("delivery_regions") or []
    if not regions:
        return 60, "Регионы поставки не заданы — считаем как «по РФ»"
    if tender["region"] in regions:
        return 100, f"Регион {tender['region']} входит в вашу зону поставки"
    return 35, f"Регион {tender['region']} вне вашей зоны поставки"


def sum_match(company, tender):
    s = tender["sum"] or 0
    lo = company.get("min_order_sum") or 0
    hi = company.get("max_order_sum") or 0
    if hi and s > hi:
        over = s / hi
        return max(10, int(90 - (over - 1) * 60)), f"Сумма {fmt_money(s)} превышает вашу максимальную мощность ({fmt_money(hi)})"
    if lo and s < lo:
        return 55, f"Сумма {fmt_money(s)} ниже вашего минимального объёма ({fmt_money(lo)})"
    return 100, f"Сумма {fmt_money(s)} в пределах вашего диапазона"


def deadline_match(company, tender):
    """Оценивает два срока: успеваем ли подготовить заявку и успеваем ли произвести."""
    left = _days_left(tender)
    lead = company.get("lead_days") or 30
    delivery = tender["delivery_days"] or lead
    if left < 0:
        return 0, "Срок подачи заявки истёк"
    if left < 3:
        prep, prep_note = 35, f"на подготовку заявки всего {left} дн."
    elif left < 7:
        prep, prep_note = 75, f"{left} дн. на заявку — успеть можно"
    else:
        prep, prep_note = 100, f"{left} дн. на заявку — с запасом"

    if delivery >= lead:
        prod, prod_note = 100, f"срок поставки {delivery} дн. при вашем цикле {lead} дн."
    elif delivery >= lead * 0.75:
        prod, prod_note = 70, f"срок поставки {delivery} дн. против цикла {lead} дн. — нужно ускорение"
    else:
        prod, prod_note = 35, f"срок поставки {delivery} дн. существенно меньше цикла {lead} дн."

    score = round(prep * 0.45 + prod * 0.55)
    return score, f"{prod_note}; {prep_note}"


def _detect_certs(text):
    """Определяет, какие сертификаты/допуски упомянуты в требовании."""
    t = _norm(text)
    found = []
    for name, keys in CERT_VOCAB:
        if any(_norm(k) in t for k in keys):
            found.append(name)
    return found


def _company_cert_text(company):
    parts = list(company.get("certificates") or []) + list(company.get("licenses") or [])
    return _norm(" ".join(parts))


def check_requirements(company, tender):
    """Эвристическая проверка требований ТЗ против профиля завода."""
    raw = tender["requirements"]
    if isinstance(raw, str):
        try:
            reqs = json.loads(raw or "[]")
        except Exception:
            reqs = []
    else:
        reqs = list(raw or [])
    out = []
    cert_text = _company_cert_text(company)
    avail = _days_left(tender)
    lead = company.get("lead_days") or 30
    cats = set(company.get("categories") or [])
    exp_years = company.get("experience_years") or 0
    max_sum = company.get("max_order_sum") or 0

    for r in reqs:
        text = r.get("text", "")
        typ = r.get("type", "docs")
        status, comment = "unknown", "Требует уточнения у заказчика"

        if typ == "certificate":
            certs = _detect_certs(text)
            if not certs:
                status, comment = "unknown", "Формулировка нестандартная — проверить вручную"
            else:
                have = [c for c in certs if _norm(c) in cert_text or (
                    c == "ГОСТ" and "гост" in cert_text)]
                miss = [c for c in certs if c not in have]
                if not miss:
                    status, comment = "met", "Все документы есть в профиле: " + ", ".join(have)
                elif have:
                    status, comment = "partial", "Есть: " + ", ".join(have) + ". Нет: " + ", ".join(miss)
                else:
                    status, comment = "missing", "В профиле нет: " + ", ".join(miss)
        elif typ == "product":
            cat = CATEGORY_BY_ID.get(tender["category"] or "")
            if cat and cat["id"] in cats:
                status, comment = "met", "Категория «%s» заявлена в профиле" % cat["name"]
            else:
                status, comment = "partial", "Проверьте номенклатуру по спецификации закупки"
        elif typ == "experience":
            need = 5 if ("газпром" in _norm(text) or tender["law"] == "223-ФЗ") else 3
            if exp_years >= need:
                status, comment = "met", f"Опыт {exp_years} лет ≥ требуемых {need}"
            elif exp_years >= 2:
                status, comment = "partial", f"Опыт {exp_years} лет — требуется {need}, нужны референсы"
            else:
                status, comment = "missing", "Недостаточный подтверждённый опыт"
        elif typ == "finance":
            if max_sum and (tender["sum"] or 0) > max_sum * 1.02:
                status, comment = "partial", "Обеспечение и оборотка выше вашего лимита — нужна БГ или партнёр"
            else:
                status, comment = "met", "Обеспечение и отсрочка в пределах ваших возможностей"
        elif typ == "timing":
            delivery = tender["delivery_days"] or lead
            if avail < 3:
                status, comment = "partial", f"На подготовку заявки осталось {avail} дн."
            elif delivery >= lead:
                status, comment = "met", f"Срок поставки {delivery} дн. при вашем цикле {lead} дн. — успеваем"
            elif delivery >= lead * 0.75:
                status, comment = "partial", f"Срок поставки {delivery} дн. против цикла {lead} дн. — нужно ускорение"
            else:
                status, comment = "missing", f"Срок {delivery} дн. существенно меньше вашего цикла {lead} дн."
        elif typ == "docs":
            status, comment = "unknown", "Организационное требование — учесть в заявке"

        out.append({"type": typ, "text": text, "status": status, "comment": comment})
    return out


def fmt_money(v):
    try:
        v = float(v)
    except Exception:
        return "—"
    if v >= 1_000_000:
        s = f"{v/1e6:.2f}".rstrip("0").rstrip(".")
        return s.replace(".", ",") + " млн ₽"
    if v >= 1000:
        return f"{v/1000:.0f} тыс ₽"
    return f"{v:.0f} ₽"


def compute_match(company, tender):
    codes = _company_okpd_set(company)
    kws = _company_keywords(company)
    w = company.get("scoring") or {}
    parts = []

    s_okpd, c_okpd = okpd_match(codes, tender["okpd"])
    parts.append({"key": "okpd", "name": "Номенклатура / ОКПД2", "weight": w.get("w_okpd", 30), "score": s_okpd, "comment": c_okpd})

    s_kw, c_kw = keyword_match(kws, tender)
    parts.append({"key": "keywords", "name": "Совпадение по ТЗ", "weight": w.get("w_keywords", 25), "score": s_kw, "comment": c_kw})

    s_sum, c_sum = sum_match(company, tender)
    parts.append({"key": "sum", "name": "Объём / мощность", "weight": w.get("w_sum", 15), "score": s_sum, "comment": c_sum})

    s_reg, c_reg = region_match(company, tender)
    parts.append({"key": "region", "name": "География", "weight": w.get("w_region", 10), "score": s_reg, "comment": c_reg})

    s_dl, c_dl = deadline_match(company, tender)
    parts.append({"key": "deadline", "name": "Сроки", "weight": w.get("w_deadline", 10), "score": s_dl, "comment": c_dl})

    reqs = check_requirements(company, tender)
    cert_reqs = [r for r in reqs if r["type"] == "certificate"]
    if cert_reqs:
        map_ = {"met": 100, "partial": 55, "missing": 10, "unknown": 60}
        s_cert = sum(map_[r["status"]] for r in cert_reqs) / len(cert_reqs)
        miss = [r["text"] for r in cert_reqs if r["status"] in ("missing", "partial")]
        c_cert = "Все документы в наличии" if not miss else "Не хватает: " + "; ".join(miss[:2])
    else:
        s_cert, c_cert = 80, "Отдельных сертификатов в ТЗ не требуется"
    parts.append({"key": "certificates", "name": "Сертификаты / допуски", "weight": w.get("w_certificates", 10), "score": int(s_cert), "comment": c_cert})

    total_w = sum(p["weight"] for p in parts) or 1
    score = sum(p["score"] * p["weight"] for p in parts) / total_w

    blockers = [r["text"] for r in reqs if r["status"] == "missing"]
    if _days_left(tender) < 0:
        blockers.insert(0, "Срок подачи заявки истёк")

    label = "высокое" if score >= 75 else ("среднее" if score >= 50 else "низкое")
    return {"score": round(score), "label": label, "parts": parts, "requirements": reqs, "blockers": blockers}


def compute_margin(company, tender, override=None):
    """Экономика сделки. override — ручные корректировки из калькулятора."""
    e = dict(company.get("economics") or {})
    price = float(tender["sum"] or 0)
    o = override or {}
    price = float(o.get("price", price) or 0)

    tax_mode = e.get("tax_mode", "vat")
    net = price / 1.2 if tax_mode == "vat" else price

    mat = float(o.get("material_pct", e.get("material_pct", 48)))
    lab = float(o.get("labor_pct", e.get("labor_pct", 12)))
    oh = float(o.get("overhead_pct", e.get("overhead_pct", 15)))
    log_pct = float(o.get("logistics_pct", e.get("logistics_pct", 6)))
    region_factor = REGION_LOGISTICS.get(tender["region"], 1.25)
    risk_pct = float(e.get("risk_reserve_pct", 4))
    bg_rate = float(e.get("bg_rate_pct", 3.5))

    materials = net * mat / 100
    labor = net * lab / 100
    overhead = net * oh / 100
    logistics = net * log_pct / 100 * region_factor

    days = (tender["delivery_days"] or 45) + 60
    security_amount = price * ((tender["bid_security_pct"] or 0) + (tender["contract_security_pct"] or 0)) / 100
    bg_cost = security_amount * bg_rate / 100 * days / 365

    risk = net * risk_pct / 100
    usn_tax = price * 0.06 if tax_mode == "usn" else 0.0

    cost_total = materials + labor + overhead + logistics + bg_cost + risk + usn_tax
    margin = net - cost_total
    margin_pct = (margin / net * 100) if net else 0

    min_m = float(e.get("min_margin_pct", 18))
    target_m = float(e.get("target_margin_pct", 26))
    if margin_pct >= target_m:
        verdict, comment = "good", "Маржа выше целевой — можно играть по цене"
    elif margin_pct >= min_m:
        verdict, comment = "ok", "Маржа в допустимом коридоре"
    elif margin_pct > 0:
        verdict, comment = "thin", "Маржа ниже вашего минимума — риск работать в ноль"
    else:
        verdict, comment = "negative", "Убыток при текущей цене"

    vat = price - net if tax_mode == "vat" else 0
    
    return {
        "price": round(price, 2),
        "price_with_vat": round(price, 2),
        "net_revenue": round(net, 2),
        "vat": round(vat, 2),
        "tax_mode": tax_mode,
        "lines": [
            {"name": "Материалы и комплектующие", "value": round(materials, 2), "pct": mat},
            {"name": "Оплата труда производственного персонала", "value": round(labor, 2), "pct": lab},
            {"name": "Накладные расходы", "value": round(overhead, 2), "pct": oh},
            {"name": "Логистика до объекта", "value": round(logistics, 2), "pct": round(net and logistics / net * 100, 1),
             "region_factor": region_factor},
            {"name": "Банковские гарантии и обеспечение", "value": round(bg_cost, 2), "security_amount": round(security_amount, 2)},
            {"name": "Резерв на риски и штрафы", "value": round(risk, 2), "pct": risk_pct},
        ] + ([{"name": "Налог при УСН (6% с оборота)", "value": round(usn_tax, 2), "pct": 6}] if usn_tax else []),
        "cost_total": round(cost_total, 2),
        "margin": round(margin, 2),
        "margin_pct": round(margin_pct, 1),
        "min_margin_pct": min_m,
        "target_margin_pct": target_m,
        "verdict": verdict,
        "comment": comment,
        "working_capital": round(security_amount + net * 0.35, 2),
        "breakeven_price": round(cost_total * (1.2 if tax_mode == "vat" else 1.0), 2),
    }
