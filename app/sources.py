# -*- coding: utf-8 -*-
"""Реальные источники закупок: адаптеры, нормализация, дедупликация.

Поддерживаются:
  * eis_search  — живой поиск по zakupki.gov.ru (44-ФЗ / 223-ФЗ). Работает с российских IP.
  * eis_xml     — официальные открытые данные ЕИС (XML/ZIP): и из папки inbox, и загрузкой вручную.
  * etp_csv     — выгрузки Excel/CSV с любой ЭТП (Росэлторг, B2B-Center, ЭТП ГПБ, ТЭК-Торг и др.).
  * etp_api     — API площадки по договору: адрес + токен задаются в настройках.

Каждый адаптер возвращает список нормализованных словарей, совместимых со схемой tenders.
"""
import csv
import io
import json
import os
import re
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

EIS_BASE = "https://zakupki.gov.ru"
EIS_SEARCH = EIS_BASE + "/epz/order/extendedsearch/results.html"

DEFAULT_SOURCES = [
    {
        "id": "eis44",
        "type": "eis_search",
        "name": "ЕИС • 44-ФЗ (живой поиск)",
        "note": "Госзакупки. Нужен российский IP — zakupki.gov.ru блокирует зарубежные адреса.",
        "enabled": False,
        "params": {"fz44": True, "fz223": False, "pages": 2, "per_page": 50, "keywords": [], "region": "", "min_sum": 0},
    },
    {
        "id": "eis223",
        "type": "eis_search",
        "name": "ЕИС • 223-ФЗ (живой поиск)",
        "note": "Закупки госкорпораций и компаний с госучастием.",
        "enabled": False,
        "params": {"fz44": False, "fz223": True, "pages": 2, "per_page": 50, "keywords": [], "region": "", "min_sum": 0},
    },
    {
        "id": "inbox",
        "type": "files_watch",
        "name": "Папка inbox (XML/CSV/ZIP)",
        "note": "Кладите сюда выгрузки: XML ЕИС, Excel с ЭТП. Обрабатывается автоматически.",
        "enabled": True,
        "params": {"path": "inbox"},
    },
    {
        "id": "etp_api",
        "type": "etp_api",
        "name": "API площадки по договору",
        "note": "Для площадок с официальным API: укажите адрес, токен и путь к списку закупок.",
        "enabled": False,
        "params": {"url": "", "token": "", "items_path": "items", "field_map": {}},
    },
]


# ---------------------------------------------------------------- утилиты
def _money(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.]", "", str(v)).replace(",", ".")
    parts = s.split(".")
    if len(parts) > 2:
        s = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(s)
    except ValueError:
        return 0.0


def _date(v) -> str:
    """Приводит дату к ISO. Понимает 20.11.2026, 20.11.2026 10:00, ISO."""
    if not v:
        return ""
    s = str(v).strip()
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})(?:[ T](\d{2}):(\d{2}))?", s)
    if m:
        d, mo, y, hh, mm = m.groups()
        return f"{y}-{mo}-{d}T{hh or '10'}:{mm or '00'}"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return s[:16].replace(" ", "T")
    return ""


def _strip_tags(html: str) -> str:
    txt = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html or "", flags=re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = (txt.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
              .replace("&laquo;", "«").replace("&raquo;", "»").replace("&mdash;", "—"))
    return re.sub(r"\s{2,}", " ", txt).strip()


def _detect_category(text: str) -> str:
    from .catalog import CATEGORIES
    low = (text or "").lower().replace("ё", "е")
    best, best_hits = "valves", 0
    for cat in CATEGORIES:
        hits = sum(1 for k in cat["keywords"] if k in low)
        if hits > best_hits:
            best, best_hits = cat["id"], hits
    return best


# ---------------------------------------------------------------- ЕИС: живой поиск
def _eis_blocks(html: str) -> list:
    """Нарезает выдачу ЕИС на блоки отдельных закупок."""
    # 1) штатная вёрстка: контейнеры results
    parts = re.split(r'(?=<div[^>]+class="[^"]*search-registry-entry-block)', html)
    parts = [p for p in parts if re.search(r"\d{19}", p)]
    if len(parts) >= 1 and any("search-registry-entry-block" in p for p in parts):
        return parts
    # 2) резерв: режем по уникальным номерам извещений
    positions = {}
    for m in re.finditer(r"\b(\d{19})\b", html):
        positions.setdefault(m.group(1), m.start())
    ordered = sorted(positions.items(), key=lambda kv: kv[1])
    blocks = []
    for i, (reg, pos) in enumerate(ordered):
        nxt = ordered[i + 1][1] if i + 1 < len(ordered) else len(html)
        blocks.append(html[max(0, pos - 2500):nxt])
    return blocks


def _parse_eis_block(chunk: str, law: str) -> Optional[Dict[str, Any]]:
    reg_m = re.search(r"\b(\d{19})\b", chunk)
    if not reg_m:
        return None
    reg = reg_m.group(1)
    text = _strip_tags(chunk)

    # Заголовок: берём самый информативный якорь (не «№ 1234...»)
    candidates = []
    for m in re.finditer(r"<a\b[^>]*>([\s\S]{3,500}?)</a>", chunk, re.I):
        txt = _strip_tags(m.group(1))
        if not txt or len(txt) < 10:
            continue
        if re.fullmatch(r"№?\s*\d[\d\s/.-]{4,}", txt):
            continue
        if re.search(r"(?i)(заказчик|цена|окпд|дата|окончани|размещено)", txt) and len(txt) < 40:
            continue
        candidates.append(txt)
    if not candidates:
        # резерв: строки текста, похожие на наименование закупки
        for line in _strip_tags(chunk).split("  "):
            line = line.strip()
            if 12 <= len(line) <= 300 and re.search(r"[А-Яа-яЁё]{4}", line) and not re.search(
                    r"(?i)(№|заказчик|цена|окпд|дата|окончани|размещено|обновлено)", line):
                candidates.append(line)
    title = max(candidates, key=len) if candidates else ""
    title = re.sub(r"^№?\s*\d{19}\s*", "", re.sub(r"\s+", " ", title)).strip(" .;—-")
    if len(title) < 8:
        return None

    price = 0.0
    for pattern in (
        r"(?:Начальная \(максимальная\) цена|Начальная цена|Цена контракта|НМЦК)[^\d]{0,40}([\d\s.,]{5,20})",
        r"([\d\s]{4,12}[.,]\d{2})\s*(?:руб|₽)",
    ):
        m = re.search(pattern, text)
        if m and _money(m.group(1)) > 0:
            price = _money(m.group(1))
            break

    customer = ""
    m = re.search(r"(?:Наименование заказчика|Заказчик|Организатор)[^\w]{0,5}([^\n]{5,120})", text)
    if m:
        customer = re.sub(r"\s+", " ", m.group(1)).strip(" .,;")

    okpd = ""
    m = re.search(r"(?:ОКПД2?|ОКПД)\D{0,12}(\d{2}\.\d{2}(?:\.\d{1,3})?)", text)
    if m:
        okpd = m.group(1)

    upd = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    m = re.search(r"(?:Обновлено|Размещено|Опубликовано)[^\d]{0,15}(\d{2}\.\d{2}\.\d{4})", text)
    if m:
        upd = _date(m.group(1))[:10] or upd

    deadline = ""
    m = re.search(r"(?:Окончание подачи заявок|Дата и время окончания подачи заявок|Окончание приема заявок)"
                  r"[^\d]{0,20}(\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2})?)", text)
    if m:
        deadline = _date(m.group(1))
    if not deadline:
        deadline = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%dT10:00")

    return {
        "ext_id": reg,
        "platform": "eis",
        "law": law,
        "category": _detect_category(title + " " + text[:1500]),
        "title": title[:250],
        "customer": customer or "Не определён",
        "region": _detect_region(text),
        "okpd": okpd,
        "qty": 1, "unit": "компл",
        "sum": price,
        "published": upd,
        "deadline": deadline,
        "delivery_days": 45,
        "description": text[:600],
        "tz_text": text[:8000],
        "requirements": json.dumps([], ensure_ascii=False),
        "files": json.dumps([], ensure_ascii=False),
        "source_url": f"{EIS_BASE}/epz/order/notice/printForm/view.html?regNumber={reg}",
    }


def parse_eis_search_html(html: str, law: str = "44-ФЗ") -> List[Dict[str, Any]]:
    """Разбирает страницу выдачи ЕИС. Устойчив к смене вёрстки: опирается на номера извещений."""
    if not html:
        return []
    out: List[Dict[str, Any]] = []
    seen = set()
    for chunk in _eis_blocks(html):
        item = _parse_eis_block(chunk, law)
        if item and item["ext_id"] not in seen:
            seen.add(item["ext_id"])
            out.append(item)
    return out


def _detect_region(text: str) -> str:
    from .catalog import REGIONS
    low = (text or "").lower()
    for r in REGIONS:
        if r.lower().replace("обл.", "область") in low or r.lower() in low:
            return r
    return ""


async def fetch_eis_search(params: dict) -> List[Dict[str, Any]]:
    """Живой поиск по ЕИС. Возвращает нормализованные закупки."""
    keywords = [k for k in (params.get("keywords") or []) if k.strip()]
    if not keywords:
        keywords = [""]
    pages = max(1, min(int(params.get("pages") or 2), 5))
    per_page = int(params.get("per_page") or 50)
    out: List[Dict[str, Any]] = []
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": EIS_BASE + "/",
    }
    timeout = httpx.Timeout(40.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for kw in keywords[:5]:
            for page in range(1, pages + 1):
                q = {
                    "searchString": kw,
                    "morphology": "on",
                    "search-filter": "Дате размещения",
                    "sortDirection": "false",
                    "recordsPerPage": f"_{per_page}",
                    "pageNumber": page,
                    "fz44": "on" if params.get("fz44") else "off",
                    "fz223": "on" if params.get("fz223") else "off",
                    "af": "on", "ca": "on", "pc": "on", "pa": "on",
                }
                try:
                    r = await client.get(EIS_SEARCH, params=q)
                except Exception as e:
                    detail = str(e) or type(e).__name__
                    raise RuntimeError(
                        f"ЕИС недоступен ({detail}). zakupki.gov.ru блокирует зарубежные адреса: "
                        f"запустите приложение на компьютере или VPS в России, либо используйте "
                        f"выгрузки XML/CSV (они работают из любой сети).")
                if r.status_code != 200:
                    raise RuntimeError(f"ЕИС ответил HTTP {r.status_code}. Обычно это блокировка зарубежных IP или защита от роботов.")
                law = "44-ФЗ" if params.get("fz44") else "223-ФЗ"
                found = parse_eis_search_html(r.text, law)
                out.extend(found)
                if not found:
                    break
    # дедуп внутри пачки
    uniq, seen = [], set()
    for t in out:
        if t["ext_id"] in seen:
            continue
        seen.add(t["ext_id"])
        uniq.append(t)
    min_sum = float(params.get("min_sum") or 0)
    if min_sum:
        uniq = [t for t in uniq if t["sum"] >= min_sum]
    return uniq


# ---------------------------------------------------------------- ЕИС: открытые данные XML
def parse_eis_xml(xml_text: str) -> List[Dict[str, Any]]:
    """Разбирает XML ЕИС (44-ФЗ или 223-ФЗ): извещение, документация."""
    import xml.etree.ElementTree as ET

    if not xml_text or not xml_text.strip():
        return []
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        # иногда файл обрезан или в нём несколько документов — берём по одному
        return []

    def txt(node, *paths):
        for p in paths:
            found = node.find(p)
            if found is not None and (found.text or "").strip():
                return found.text.strip()
        return ""

    out = []
    # обходим все элементы, похожие на извещение
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag not in ("purchaseNotice", "purchaseNoticeOK", "purchaseNoticeEA", "purchaseNoticeZK",
                       "purchaseNoticeEP", "purchaseNoticeZKK", "notice", "purchase", "purchaseNoticeData"):
            continue
        reg = txt(el, "purchaseNoticeNumber", "regNumber", "purchaseNumber", "registrationNumber")
        if not reg:
            continue
        title = txt(el, "purchaseObjectInfo", "purchaseObjectInfoExtended", "objectInfo", "name")
        price = _money(txt(el, "maxPrice", "price", "initialPrice", "maxContractPrice"))
        pub = _date(txt(el, "publishDTInEIS", "publicationDateTime", "createDateTime", "publishDate"))
        end = _date(txt(el, "endDT", "endDate", "submissionCloseDateTime", "bidEndDate"))
        customer = txt(el, "customer/regNum", "customer/fullName", "customer/customerName", "customerName")
        cname = txt(el, "customer/fullName", "customer/shortName", "customerName")
        region = txt(el, "deliveryPlace", "customer/region", "regionName", "deliveryPlaceInfo")
        okpd = ""
        for code in el.iter():
            ctag = code.tag.split("}")[-1]
            if ctag in ("OKPD2", "okpd2", "OKPDCode", "okpd"):
                val = (code.text or "").strip()
                if re.match(r"\d{2}\.\d{2}", val):
                    okpd = val
                    break
                nested = code.find("code")
                if nested is not None and (nested.text or "").strip():
                    okpd = nested.text.strip()
                    break
        law = "223-ФЗ" if "223" in (root.find(".//fz223").text if root.find(".//fz223") is not None else "") else "44-ФЗ"
        if not title:
            title = f"Закупка {reg}"
        out.append({
            "ext_id": reg,
            "platform": "eis",
            "law": law,
            "category": _detect_category(title),
            "title": title[:250],
            "customer": cname or customer or "Не определён",
            "region": _detect_region(region + " " + customer) or region[:60],
            "okpd": okpd, "qty": 1, "unit": "компл", "sum": price,
            "published": (pub or datetime.now(timezone.utc).strftime("%Y-%m-%d"))[:10],
            "deadline": end or (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%dT10:00"),
            "delivery_days": 45, "description": title[:400], "tz_text": "",
            "requirements": json.dumps([], ensure_ascii=False),
            "files": json.dumps([], ensure_ascii=False),
            "source_url": f"{EIS_BASE}/epz/order/notice/printForm/view.html?regNumber={reg}",
        })
    return out


# ---------------------------------------------------------------- выгрузки ЭТП: CSV / Excel
CSV_ALIASES = {
    "ext_id": ["номер", "№", "номер закупки", "номер извещения", "id", "код", "рег", "regnumber", "закупка"],
    "title": ["наименование", "название", "предмет", "объект закупки", "тема", "title", "name"],
    "customer": ["заказчик", "организатор", "компания", "customer", "покупатель"],
    "sum": ["цена", "сумма", "нмцк", "начальная цена", "цена контракта", "стоимость", "sum", "price"],
    "region": ["регион", "регион поставки", "место поставки", "region", "адрес"],
    "deadline": ["окончание", "дедлайн", "окончание подачи", "дата окончания", "deadline", "срок подачи"],
    "published": ["размещено", "опубликовано", "дата публикации", "published"],
    "okpd": ["окпд", "окпд2", "код окпд", "okpd"],
    "qty": ["количество", "объем", "объём", "qty"],
    "unit": ["ед", "единица", "ед.изм", "unit"],
    "law": ["закон", "фз", "law"],
    "platform": ["площадка", "этп", "platform"],
    "category": ["категория", "category"],
    "tz_text": ["описание", "тз", "техническое задание", "описание предмета"],
}


def _map_columns(header: List[str]) -> Dict[str, int]:
    mapping, low = {}, [h.strip().lower() for h in header]
    for field, aliases in CSV_ALIASES.items():
        for i, h in enumerate(low):
            if any(a == h or a in h for a in aliases):
                mapping[field] = i
                break
    return mapping


def parse_csv_rows(text: str, platform_default: str = "manual") -> List[Dict[str, Any]]:
    if not text or not text.strip():
        return []
    sample = text[:4000]
    delim = max([";", ",", "\t"], key=lambda d: sample.count(d))
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if len(rows) < 2:
        return []
    mapping = _map_columns(rows[0])
    if "title" not in mapping:
        return []
    out = []
    for r in rows[1:]:
        def get(field):
            i = mapping.get(field)
            return r[i].strip() if i is not None and i < len(r) else ""
        title = get("title")
        if len(title) < 5:
            continue
        ext = get("ext_id") or f"CSV-{abs(hash(title)) % 10**10}"
        out.append({
            "ext_id": ext,
            "platform": (get("platform") or platform_default).lower()[:20],
            "law": get("law") or "коммерч.",
            "category": get("category") or _detect_category(title + " " + get("tz_text")),
            "title": title[:250],
            "customer": get("customer") or "Не определён",
            "region": get("region"),
            "okpd": get("okpd"), "qty": _money(get("qty")) or 1, "unit": get("unit") or "компл",
            "sum": _money(get("sum")),
            "published": (_date(get("published")) or datetime.now(timezone.utc).strftime("%Y-%m-%d"))[:10],
            "deadline": _date(get("deadline")) or (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%dT10:00"),
            "delivery_days": 45,
            "description": get("tz_text")[:400], "tz_text": get("tz_text")[:8000],
            "requirements": json.dumps([], ensure_ascii=False),
            "files": json.dumps([], ensure_ascii=False),
            "source_url": "",
        })
    return out


# ---------------------------------------------------------------- API площадки по договору
async def fetch_etp_api(params: dict) -> List[Dict[str, Any]]:
    url = (params.get("url") or "").strip()
    if not url:
        raise RuntimeError("Не задан URL API площадки в настройках источника")
    headers = {"Accept": "application/json"}
    if params.get("token"):
        headers["Authorization"] = f"Bearer {params['token']}"
    async with httpx.AsyncClient(timeout=40.0, follow_redirects=True, headers=headers) as client:
        r = await client.get(url)
    if r.status_code >= 400:
        raise RuntimeError(f"API площадки ответил HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    items = data
    for part in (params.get("items_path") or "items").split("."):
        if isinstance(items, dict) and part in items:
            items = items[part]
    if not isinstance(items, list):
        raise RuntimeError("В ответе API не найден список закупок — проверьте «путь к списку» (items_path)")
    fmap = params.get("field_map") or {}
    out = []
    for raw in items[:500]:
        if not isinstance(raw, dict):
            continue

        def pick(key, default=""):
            src = fmap.get(key, key)
            return raw.get(src, default)

        title = str(pick("title", "")) or str(pick("name", ""))
        if len(title) < 5:
            continue
        out.append({
            "ext_id": str(pick("ext_id") or pick("id") or f"API-{abs(hash(title)) % 10**10}"),
            "platform": params.get("platform") or "etp",
            "law": str(pick("law") or "коммерч."),
            "category": _detect_category(title),
            "title": title[:250],
            "customer": str(pick("customer") or "Не определён"),
            "region": str(pick("region") or ""),
            "okpd": str(pick("okpd") or ""),
            "qty": _money(pick("qty")) or 1, "unit": str(pick("unit") or "компл"),
            "sum": _money(pick("sum")),
            "published": (_date(pick("published")) or datetime.now(timezone.utc).strftime("%Y-%m-%d"))[:10],
            "deadline": _date(pick("deadline")) or (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%dT10:00"),
            "delivery_days": int(_money(pick("delivery_days")) or 45),
            "description": str(pick("description") or "")[:400],
            "tz_text": str(pick("tz_text") or "")[:8000],
            "requirements": json.dumps([], ensure_ascii=False),
            "files": json.dumps([], ensure_ascii=False),
            "source_url": str(pick("url") or ""),
        })
    return out


# ---------------------------------------------------------------- файлы из inbox
def parse_files_in_inbox(folder: str, processed_dir: Optional[str] = None) -> Dict[str, Any]:
    """Читает все XML/CSV/ZIP в папке. Возвращает {'items': [...], 'files': [...], 'errors': [...]}"""
    items, files, errors = [], [], []
    if not os.path.isdir(folder):
        return {"items": [], "files": [], "errors": [f"Папка {folder} не найдена"]}
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        low = name.lower()
        try:
            if low.endswith(".zip"):
                with zipfile.ZipFile(path) as z:
                    for inner in z.namelist():
                        if inner.lower().endswith((".xml", ".csv")):
                            blob = z.read(inner).decode("utf-8", "ignore")
                            parsed = parse_eis_xml(blob) if inner.lower().endswith(".xml") else parse_csv_rows(blob)
                            items.extend(parsed)
                files.append({"name": name, "parsed": "архив"})
            elif low.endswith(".xml"):
                with open(path, encoding="utf-8", errors="ignore") as f:
                    parsed = parse_eis_xml(f.read())
                items.extend(parsed)
                files.append({"name": name, "parsed": len(parsed)})
            elif low.endswith((".csv", ".txt")):
                with open(path, encoding="utf-8", errors="ignore") as f:
                    parsed = parse_csv_rows(f.read())
                items.extend(parsed)
                files.append({"name": name, "parsed": len(parsed)})
            else:
                continue
            if processed_dir:
                os.makedirs(processed_dir, exist_ok=True)
                os.replace(path, os.path.join(processed_dir, name))
        except Exception as e:
            errors.append(f"{name}: {e}")
    return {"items": items, "files": files, "errors": errors}
