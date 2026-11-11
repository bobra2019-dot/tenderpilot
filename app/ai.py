# -*- coding: utf-8 -*-
"""ИИ-слой: анализ закупки через LLM (OpenAI-совместимый / Anthropic) + офлайн-эвристика."""
import json
import re

import httpx

from .catalog import CATEGORY_BY_ID, PLATFORM_BY_ID
from .scoring import fmt_money

SYSTEM_PROMPT = """Ты — старший тендерный аналитик промышленного завода в России. Твоя работа: за 1 минуту понять, стоит ли заводу участвовать в закупке.

Ты получаешь: профиль завода (номенклатура, ОКПД2, сертификаты, экономика) и карточку закупки (площадка, закон, ТЗ, требования, расчёт маржи).

Жёсткие правила:
1. Опирайся ТОЛЬКО на переданные данные. Ничего не выдумывай. Если данных нет — пиши «нет данных», ставь статус unknown.
2. Ищи в ТЗ скрытые ловушки: заточка под конкретного поставщика, нереальные сроки, запрет аналогов, требования опыта, которых у завода нет, кабальные условия оплаты и штрафы, приёмка с выездом, требования нострификации/сертификации, которые завод не покроет.
3. Считай экономику по расчёту маржи — не подменяй цифры, но оцени, реалистична ли себестоимость и есть ли запас для торга.
4. Оценивай вероятность победы по конкуренции на площадке и типу закупки.
5. Пиши по-русски, по-деловому, коротко и конкретно. Без воды и без общих фраз.

Верни СТРОГО один JSON-объект без markdown-обёртки, по схеме:
{
  "verdict": "go" | "consider" | "no_go",
  "confidence": 0-100,
  "summary": "3-4 предложения: подходит ли закупка заводу и почему",
  "match_notes": ["чем закупка подходит профилю завода", "..."],
  "requirements": [{"text": "требование", "type": "product|certificate|experience|finance|timing|docs", "status": "met|partial|missing|unknown", "comment": "короткий вывод"}],
  "risks": [{"level": "high|medium|low", "text": "риск и его последствие"}],
  "red_flags": ["признак заточки/дискриминации, если есть"],
  "competitive": {"win_probability": 0-100, "expected_bidders": 1-15, "comment": "почему такая оценка"},
  "pricing": {"recommended_price": число_руб, "max_discount_pct": 0-30, "comment": "стратегия по цене"},
  "margin_comment": "что сказать по марже и оборотке",
  "documents_needed": ["какие документы готовить к заявке"],
  "next_steps": ["3-5 конкретных шагов"],
  "questions": ["что уточнить у заказчика/в ТЗ"]
}"""


def _company_brief(company):
    cats = [CATEGORY_BY_ID[c]["name"] for c in (company.get("categories") or []) if c in CATEGORY_BY_ID]
    return {
        "Наименование": company.get("name") or "не указано",
        "ИНН": company.get("inn") or "—",
        "Регион": company.get("region") or "—",
        "Направления производства": cats or ["не выбраны"],
        "Продукция (свободным текстом)": company.get("products") or "—",
        "ОКПД2": company.get("okpd") or [],
        "Опыт, лет": company.get("experience_years"),
        "Производственный цикл, дней": company.get("lead_days"),
        "Максимальная сумма заказа, ₽": company.get("max_order_sum"),
        "Минимальная сумма заказа, ₽": company.get("min_order_sum"),
        "Сертификаты": company.get("certificates") or [],
        "Лицензии и допуски": company.get("licenses") or [],
        "Регионы поставки": company.get("delivery_regions") or [],
        "Референсы": company.get("references") or "—",
        "Экономика": company.get("economics"),
    }


def _tender_brief(tender, match, margin):
    p = PLATFORM_BY_ID.get(tender["platform"], {"name": tender["platform"]})
    try:
        files = json.loads(tender["files"] or "[]")
    except Exception:
        files = []
    return {
        "Площадка": p["name"],
        "Закон": tender["law"],
        "Номер закупки": tender["ext_id"],
        "Наименование": tender["title"],
        "Заказчик": tender["customer"],
        "Регион поставки": tender["region"],
        "ОКПД2": tender["okpd"],
        "Объём": f'{tender["qty"]} {tender["unit"]}',
        "НМЦК / сумма, ₽": tender["sum"],
        "Опубликовано": tender["published"],
        "Дедлайн подачи": tender["deadline"],
        "Срок поставки, дней": tender["delivery_days"],
        "Условия оплаты": tender["payment_terms"],
        "Обеспечение заявки, %": tender["bid_security_pct"],
        "Обеспечение контракта, %": tender["contract_security_pct"],
        "Ответственность": tender["penalty"],
        "Документы в пакете": [f["name"] for f in files],
        "Текст ТЗ": tender["tz_text"],
        "Формализованные требования": (json.loads(tender["requirements"]) if isinstance(tender["requirements"], str) else list(tender["requirements"] or [])),
        "Расчётный скоринг соответствия": {"итог_0_100": match["score"], "детализация": match["parts"]},
        "Расчётный маржинальный анализ": {
            "рекомендуемая_цена": margin["price"],
            "себестоимость": margin["cost_total"],
            "маржа_руб": margin["margin"],
            "маржа_%": margin["margin_pct"],
            "минимальная_маржа_%": margin["min_margin_pct"],
            "целевая_маржа_%": margin["target_margin_pct"],
            "потребность_в_оборотном_капитале": margin["working_capital"],
        },
    }


def build_prompt(company, tender, match, margin, question=None):
    ctx = {
        "профиль_завода": _company_brief(company),
        "закупка": _tender_brief(tender, match, margin),
    }
    user = "Данные для анализа:\n" + json.dumps(ctx, ensure_ascii=False, indent=1)
    if question:
        user += "\n\nВладелец завода дополнительно спрашивает: " + question + "\nУчти это в ответе (в полях summary/next_steps/risks)."
    user += "\n\nВерни только JSON по схеме."
    return user


def _extract_json(text):
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1:
        return None
    frag = t[start:end + 1]
    try:
        return json.loads(frag)
    except Exception:
        pass
    # мягкое исправление: убрать висячие запятые
    frag2 = re.sub(r",\s*([}\]])", r"\1", frag)
    try:
        return json.loads(frag2)
    except Exception:
        return None


async def call_llm(cfg, system, user, max_tokens=2600):
    kind = cfg.get("kind", "openai")
    base = (cfg.get("base_url") or "").rstrip("/")
    model = cfg.get("model")
    key = cfg.get("api_key") or ""
    if not base or not model:
        raise RuntimeError("Не заданы base_url и модель в настройках ИИ")
    if not key:
        raise RuntimeError("Не задан API-ключ в настройках ИИ")
    timeout = httpx.Timeout(120.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if kind == "anthropic":
            r = await client.post(
                base + "/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "temperature": float(cfg.get("temperature", 0.2)),
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
            )
            if r.status_code >= 400:
                raise RuntimeError(f"LLM вернул {r.status_code}: {r.text[:400]}")
            data = r.json()
            parts = data.get("content") or []
            return "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        else:
            r = await client.post(
                base + "/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "temperature": float(cfg.get("temperature", 0.2)),
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            if r.status_code >= 400:
                raise RuntimeError(f"LLM вернул {r.status_code}: {r.text[:400]}")
            data = r.json()
            return data["choices"][0]["message"]["content"]


# ---------------- офлайн-эвристика ----------------
def heuristic_analysis(company, tender, match, margin):
    reqs = match["requirements"]
    missing = [r for r in reqs if r["status"] == "missing"]
    partial = [r for r in reqs if r["status"] == "partial"]
    score = match["score"]
    mp = margin["margin_pct"]
    min_m = margin["min_margin_pct"]

    risks = []
    if missing:
        risks.append({"level": "high", "text": "Не выполнены требования ТЗ: " + "; ".join(r["text"] for r in missing[:3])})
    if partial:
        risks.append({"level": "medium", "text": "Частично закрытые требования: " + "; ".join(r["text"] for r in partial[:3])})
    if mp < min_m:
        risks.append({"level": "high" if mp < min_m - 6 else "medium",
                      "text": f"Маржа {mp}% ниже вашего минимума {min_m}% — нет запаса на торг"})
    if (tender["contract_security_pct"] or 0) >= 5:
        risks.append({"level": "medium",
                      "text": f"Обеспечение контракта {tender['contract_security_pct']}% — потребуется {fmt_money(margin['working_capital'])} оборотных средств"})
    days_pay = re.findall(r"(\d+)\s*дн", tender["payment_terms"] or "")
    if days_pay and int(days_pay[0]) >= 60:
        risks.append({"level": "medium", "text": f"Отсрочка оплаты {days_pay[0]} дней — кассовый разрыв"})
    if (tender["delivery_days"] or 0) <= 30:
        risks.append({"level": "low", "text": f"Короткий срок поставки {tender['delivery_days']} дней — нужен запас на складе"})

    red_flags = []
    tz = (tender["tz_text"] or "").lower()
    if "или аналог" not in tz and ("требуется" in tz and ("сертификат" in tz and "газсерт" in tz)):
        red_flags.append("Требуется отраслевой сертификат СДС ГАЗСЕРТ — фактически ограничивает круг поставщиков")
    if "росатом" in tz or "нп-071" in tz:
        red_flags.append("Требования атомной отрасли (НП-071, аттестация изготовителя) — без допуска участие невозможно")
    if "приемка" in tz.replace("ё", "е") and "выезд" in tz.replace("ё", "е"):
        red_flags.append("Приёмка с выездом представителя заказчика на завод — нужны ресурсы и время")
    if "субподряд" in tz and "запрет" in tz:
        red_flags.append("Запрет субподряда на поставку — весь объём нужно закрыть своими силами")
    if not red_flags:
        red_flags = []

    # жёсткие блокеры — нет сертификата/допуска/опыта; мягкие — сроки и орг. требования
    hard = [r for r in missing if r["type"] in ("certificate", "experience", "product")]
    soft = [r for r in missing if r["type"] in ("timing", "docs", "finance")]
    if mp <= 0 or score < 45 or hard:
        verdict = "no_go"
    elif score >= 72 and mp >= min_m and not soft:
        verdict = "go"
    else:
        verdict = "consider"

    if verdict == "go":
        summary = (f"Закупка подходит заводу: соответствие профилю {score}/100, маржа {mp}% при цене {fmt_money(margin['price'])}. "
                   f"Требования ТЗ закрываются имеющимися сертификатами и опытом. Рекомендуется готовить заявку.")
    elif verdict == "consider":
        soft_note = (" Критично по срокам: " + "; ".join(r["text"] for r in soft[:2])) if soft else ""
        summary = (f"Закупка частично подходит: соответствие {score}/100, маржа {mp}%. "
                   f"Есть {len(missing) + len(partial)} не до конца закрытых требования ТЗ — их надо снять до подачи.{soft_note}")
    else:
        reason = ("не закрываются обязательные требования ТЗ: " + "; ".join(r["text"] for r in hard[:2])) if hard \
            else ("маржа ниже вашего минимума" if mp < min_m else "профиль завода не совпадает с закупкой")
        summary = f"Участие не рекомендуется: {reason}. Соответствие профилю {score}/100, расчётная маржа {mp}%."

    win = max(10, min(85, int(score * 0.7 + (10 if tender["law"] == "44-ФЗ" else 0) - (10 if mp < min_m else 0))))
    bidders = 3 if tender["law"] == "44-ФЗ" else 5
    if (tender["sum"] or 0) < 3_000_000:
        bidders += 3

    docs = ["Заявка по форме площадки", "Сертификаты и декларации на продукцию",
            "Справка об опыте исполнения аналогичных договоров", "Уставные документы, выписка ЕГРЮЛ"]
    if (tender["contract_security_pct"] or 0) > 0:
        docs.append(f"Банковская гарантия на обеспечение контракта ({tender['contract_security_pct']}%)")

    steps = ["Проверить позиции спецификации на предмет аналогов и допусков",
             "Запросить у заказчика разъяснения по спорным пунктам ТЗ",
             "Согласовать с производством график под срок поставки",
             "Просчитать цену с запасом на торг и подготовить документы"]
    if missing:
        steps.insert(0, "Оценить, реально ли получить отсутствующие сертификаты/допуски до дедлайна")

    return {
        "verdict": verdict,
        "confidence": 55,
        "summary": summary,
        "match_notes": [f"{p['name']}: {p['score']}/100 — {p['comment']}" for p in match["parts"]],
        "requirements": reqs,
        "risks": risks,
        "red_flags": red_flags,
        "competitive": {"win_probability": win, "expected_bidders": bidders,
                        "comment": "Оценка по типу процедуры, сумме и отрасли (без LLM — быстрый расчёт)."},
        "pricing": {"recommended_price": round(margin["price"] * 0.97, 2),
                    "max_discount_pct": max(0, int(mp - min_m)),
                    "comment": f"Держать маржу не ниже {min_m}%. Целевая цена — {fmt_money(margin['price'])}."},
        "margin_comment": f"Маржа {mp}% ({fmt_money(margin['margin'])}). Потребность в оборотных средствах — {fmt_money(margin['working_capital'])}.",
        "documents_needed": docs,
        "next_steps": steps,
        "questions": [r["text"] for r in reqs if r["status"] in ("unknown", "partial")][:4],
        "source": "heuristic",
        "model": "встроенный расчёт (без LLM)",
    }


def normalize_analysis(data, company, tender, match, margin):
    """Приводит ответ LLM к предсказуемой структуре и подстраховывает пустые поля."""
    base = heuristic_analysis(company, tender, match, margin)
    if not isinstance(data, dict):
        return base
    out = dict(base)
    for k in ("verdict", "confidence", "summary", "match_notes", "requirements", "risks", "red_flags",
              "competitive", "pricing", "margin_comment", "documents_needed", "next_steps", "questions"):
        v = data.get(k)
        if v in (None, "", [], {}):
            continue
        out[k] = v
    if out.get("verdict") not in ("go", "consider", "no_go"):
        out["verdict"] = base["verdict"]
    try:
        out["confidence"] = int(out.get("confidence") or 55)
    except Exception:
        out["confidence"] = 55
    if not isinstance(out.get("risks"), list):
        out["risks"] = base["risks"]
    else:
        fixed = []
        for r in out["risks"]:
            if isinstance(r, str):
                fixed.append({"level": "medium", "text": r})
            elif isinstance(r, dict):
                fixed.append({"level": r.get("level", "medium") if r.get("level") in ("high", "medium", "low") else "medium",
                              "text": r.get("text", "")})
        out["risks"] = fixed
    if not isinstance(out.get("requirements"), list) or not out["requirements"]:
        out["requirements"] = match["requirements"]
    else:
        fixed = []
        for r in out["requirements"]:
            if not isinstance(r, dict):
                continue
            st = r.get("status") if r.get("status") in ("met", "partial", "missing", "unknown") else "unknown"
            fixed.append({"text": r.get("text", ""), "type": r.get("type", "docs"), "status": st,
                          "comment": r.get("comment", "")})
        out["requirements"] = fixed or match["requirements"]
    comp = out.get("competitive") or {}
    if not isinstance(comp, dict):
        comp = {}
    out["competitive"] = {
        "win_probability": int(comp.get("win_probability") or base["competitive"]["win_probability"]),
        "expected_bidders": int(comp.get("expected_bidders") or base["competitive"]["expected_bidders"]),
        "comment": comp.get("comment") or base["competitive"]["comment"],
    }
    pr = out.get("pricing") or {}
    if not isinstance(pr, dict):
        pr = {}
    out["pricing"] = {
        "recommended_price": float(pr.get("recommended_price") or base["pricing"]["recommended_price"]),
        "max_discount_pct": float(pr.get("max_discount_pct") or 0),
        "comment": pr.get("comment") or base["pricing"]["comment"],
    }
    for k in ("match_notes", "red_flags", "documents_needed", "next_steps", "questions"):
        if not isinstance(out.get(k), list):
            out[k] = base[k]
        else:
            out[k] = [x if isinstance(x, str) else json.dumps(x, ensure_ascii=False) for x in out[k]]
    out["source"] = "llm"
    return out
