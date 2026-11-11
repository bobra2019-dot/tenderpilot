# -*- coding: utf-8 -*-
"""«Проверка допуска» — предполётный чек-лист заявки.

Детерминированные правила по тексту ТЗ, карточке закупки и профилю завода.
Задача: до подачи поймать то, из-за чего заявку отклоняют (декларации, страна
происхождения, диапазонные характеристики, гарантии, доптребования, сроки)
и посчитать антидемпинговые последствия.

Нормы: 44-ФЗ ст. 31, 37, 45, 48; ПП № 1875 (нацрежим); ПП № 2571 (доптребования).
"""
import re

SEVERITY = {"fail": "блокирует", "warn": "проверить", "info": "к сведению", "ok": "ок"}

# ---------- утилиты разбора ----------
def _norm(s):
    return (s or "").lower().replace("ё", "е")


def _fmt(n):
    try:
        return f"{round(float(n)):,}".replace(",", " ") + " ₽"
    except Exception:
        return "—"


def _search(patterns, text):
    """Возвращает первый найденный фрагмент текста или None."""
    t = _norm(text)
    for p in patterns:
        m = re.search(p, t)
        if m:
            start = max(0, m.start() - 60)
            return re.sub(r"\s+", " ", text[start:m.end() + 90]).strip()
    return None


def _pct(text, keys):
    """Ищет процент рядом с ключевым словом: 'обеспечение заявки 1%'."""
    t = _norm(text)
    for key in keys:
        for m in re.finditer(key + r"[^%\n]{0,80}?(\d{1,2}(?:[.,]\d)?)\s*%", t):
            try:
                return float(m.group(1).replace(",", "."))
            except ValueError:
                continue
        for m in re.finditer(r"(\d{1,2}(?:[.,]\d)?)\s*%[^%\n]{0,80}?" + key, t):
            try:
                return float(m.group(1).replace(",", "."))
            except ValueError:
                continue
    return None


def _days(text):
    """Срок поставки в днях из формулировок 'в течение 30 дней', 'не позднее 45 календарных дней'."""
    t = _norm(text)
    pats = [
        r"(?:в течение|не позднее|не более|срок поставки|срок выполнения)[^\n]{0,40}?(\d{1,3})\s*(?:\([^)]*\)\s*)?(?:календарн|рабоч|банковск)?\w*\s*дн",
        r"(\d{1,3})\s*(?:календарных|рабочих)?\s*дн\w*[^\n]{0,30}?(?:с даты|с момента) (?:заключения|подписания)",
        r"(?:поставк\w*|срок\w*)[^\n]{0,25}?(\d{1,3})\s*(?:календарных|рабочих)?\s*дн",
    ]
    for p in pats:
        m = re.search(p, t)
        if m:
            try:
                v = int(m.group(1))
                if 1 <= v <= 400:
                    return v
            except ValueError:
                pass
    return None


def _text_of(tender):
    parts = [
        tender.get("title"), tender.get("description"), tender.get("tz_text"),
        tender.get("requirements"), tender.get("payment_terms"),
        tender.get("penalty"), tender.get("note"),
    ]
    return "\n".join(str(p) for p in parts if p)


# ---------- правила ----------
def _item(code, title, status, what, why="", norm="", where="", fix="", found=""):
    return {
        "code": code, "title": title, "status": status, "severity": SEVERITY[status],
        "what": what, "why": why, "norm": norm, "where": where, "fix": fix, "found": found,
    }


def _rule_deadline(tender, company, items):
    dl = tender.get("deadline") or ""
    found = None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(dl))
    if m:
        from datetime import date
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            left = (d - date.today()).days
            if left < 0:
                items.append(_item("deadline", "Срок подачи заявки", "fail",
                                   "Срок подачи истёк — участие невозможно.",
                                   "Заявку уже не подать, готовиться к следующей процедуре.",
                                   "ст. 42 44-ФЗ", "извещение в ЕИС", "Перенести в архив",
                                   found=f"до {d.strftime('%d.%m.%Y')}"))
            elif left <= 2:
                items.append(_item("deadline", "Срок подачи заявки", "fail",
                                   f"До окончания подачи {left} дн. Подготовить полный комплект не успеть.",
                                   "Без деклараций, гарантии и согласований заявка уйдёт с ошибками.",
                                   "ст. 42 44-ФЗ", "извещение в ЕИС",
                                   "Либо подавать сокращённым комплектом (чем рискуете), либо отказаться",
                                   found=f"до {d.strftime('%d.%m.%Y')}, осталось {left} дн."))
            elif left <= 5:
                items.append(_item("deadline", "Срок подачи заявки", "warn",
                                   f"До подачи {left} дн. — впритык.",
                                   "Гарантию и декларации банк и юрист готовят 2–4 дня.",
                                   "ст. 45 44-ФЗ", "извещение", "Заранее заказать гарантию",
                                   found=f"осталось {left} дн."))
            else:
                items.append(_item("deadline", "Срок подачи заявки", "ok",
                                   f"До подачи {left} дн. — время есть.",
                                   "", "ст. 42 44-ФЗ", "извещение", "",
                                   found=f"осталось {left} дн."))
        except ValueError:
            pass


def _rule_nmck(tender, company, items):
    nmck = float(tender.get("sum") or 0)
    limit = float(company.get("max_order_sum") or 0)
    if not nmck:
        return
    if limit and nmck > limit * 1.15:
        items.append(_item("nmck", "Сумма закупки против вашего лимита", "fail",
                           f"НМЦК {_fmt(nmck)} больше вашего максимального заказа {_fmt(limit)} "
                           f"на {round((nmck / limit - 1) * 100)}%.",
                           "Не хватит оборотных средств или обеспечение не даст банк.",
                           "ч. 1 ст. 31, ст. 37 44-ФЗ", "профиль завода → экономика",
                           "Отказаться или заранее согласовать кредитную линию"))
    elif limit and nmck > limit * 0.9:
        items.append(_item("nmck", "Сумма закупки против вашего лимита", "warn",
                           f"НМЦК {_fmt(nmck)} близка к вашему максимуму {_fmt(limit)}.",
                           "Свободного оборотного капитала почти не останется.",
                           "", "профиль → экономика", "Проверить кассовый разрыв на даты поставки"))
    else:
        items.append(_item("nmck", "Сумма закупки в пределах ваших возможностей", "ok",
                           f"НМЦК {_fmt(nmck)}" + (f" при лимите {_fmt(limit)}" if limit else ""),
                           "", "", "", ""))


def _rule_lead_time(tender, company, items, text):
    need = tender.get("delivery_days") or _days(text)
    own = company.get("lead_days")
    if not need:
        return
    if not own and _profile_known(company) is False:
        items.append(_item("lead_time", "Срок поставки", "warn",
                           f"Заказчик требует поставку за {need} дн.",
                           "Сверьте с производственным циклом: если ваш цикл дольше, "
                           "это не просрочка, а РНП на 2 года (в реестр попадает и ИНН учредителя).",
                           "ст. 34, 104 44-ФЗ", "проект контракта, раздел о сроках",
                           "Указать свой цикл в профиле завода — проверка посчитает автоматически",
                           found=f"{need} дн."))
        return
    if own and int(need) < int(own):
        items.append(_item("lead_time", "Срок поставки против вашего цикла", "fail",
                           f"Заказчик даёт {need} дн., ваш производственный цикл — {own} дн.",
                           "Просрочка = пени и риск попадания в РНП на 2 года (в реестр попадает и ИНН учредителя).",
                           "ст. 34, 104 44-ФЗ", "проект контракта, раздел о сроках",
                           "Искать субподряд/склад или не идти", found=f"{need} дн. против {own} дн."))
    elif own and int(need) < int(own) * 1.2:
        items.append(_item("lead_time", "Срок поставки впритык", "warn",
                           f"Заказчик даёт {need} дн., ваш цикл {own} дн. — запаса почти нет.",
                           "Задержка логистики или партии сырья уронит срок.",
                           "ст. 34 44-ФЗ", "проект контракта", "Заложить резерв и подтвердить у производства"))
    else:
        items.append(_item("lead_time", "Срок поставки выполним", "ok",
                           f"Требуется {need} дн., ваш цикл {own or '—'} дн." if own else f"Требуется {need} дн.",
                           "", "", "", ""))


def _has(t, key):
    """Поиск ключа с границей слова для коротких сокращений (иначе «сро» ловится в «срок»)."""
    key = _norm(key)
    if len(key) <= 4 and key.isalpha():
        return re.search(r"(?<![а-яa-z])" + re.escape(key) + r"(?![а-яa-z])", t) is not None
    return key in t


def _profile_known(company):
    """Есть ли смысл сравнивать с профилем: у гостя он пустой."""
    return bool(company.get("certificates") or company.get("licenses")
                or company.get("lead_days") or company.get("max_order_sum")
                or company.get("products") or company.get("okpd"))


def _rule_certs(tender, company, items, text):
    from .scoring import CERT_VOCAB
    known = _profile_known(company)
    have = _norm(" ".join(str(c) for c in (company.get("certificates") or []) + (company.get("licenses") or [])))
    t = _norm(text)
    needed, missing = [], []
    for name, keys in CERT_VOCAB:
        if any(_has(t, k) for k in keys):
            needed.append(name)
            if not any(_has(have, k) for k in keys):
                missing.append(name)
    if not needed:
        return
    if missing:
        if known:
            what = "ТЗ требует: " + ", ".join(missing) + ". В профиле завода этого нет."
            status = "fail"
            fix = "Получить документ до подачи или не участвовать"
        else:
            what = ("ТЗ требует документы: " + ", ".join(missing) +
                    ". Если их нет — заявку отклонят на рассмотрении.")
            status = "warn"
            fix = "Сверить список со своими сертификатами и допусками до подачи"
        items.append(_item("certs", "Сертификаты и допуски", status, what,
                           "Отсутствие требуемого сертификата или допуска — прямое основание "
                           "для отклонения (ч. 12 ст. 48). Требования, которых нет на рынке, "
                           "заодно признак заточки под конкретного поставщика.",
                           "ч. 1 ст. 31, ч. 12 ст. 48 44-ФЗ",
                           "профиль завода → сертификаты; реестры Росаккредитации",
                           fix, found=", ".join(missing)))
    else:
        items.append(_item("certs", "Сертификаты и допуски", "ok",
                           "Все требуемые документы есть в профиле: " + ", ".join(needed),
                           "", "ч. 1 ст. 31 44-ФЗ", "", ""))


def _rule_declaration_31(tender, company, items, text):
    found = _search([r"деклараци"], text)
    items.append(_item(
        "decl31", "Декларация соответствия ст. 31", "warn",
        "Проверить декларацию по всем пунктам: 1, 1.1, 2, 3, 4, 5, 7, 7.1, 8, 9, 11 ч. 1 ст. 31.",
        "Самая частая причина отклонения второй части: декларация старого образца без п. 7 и 7.1 "
        "(судимость руководителя, конфликт интересов).",
        "ч. 2 ст. 31, п. 3 ч. 6 ст. 43, п. 8 ч. 12 ст. 48 44-ФЗ",
        "форма декларации в документации закупки",
        "Сверить пункты построчно с редакцией документации на дату публикации извещения",
        found=found or ""))
    items.append(_item(
        "rnp_self", "Отсутствие в РНП", "warn",
        "Проверить себя, учредителей и директора в реестре недобросовестных поставщиков.",
        "Запись в РНП закрывает участие на 2 года и вносится не только по ИНН компании, "
        "но и по учредителю и главному бухгалтеру.",
        "ст. 104 44-ФЗ, п. 7 ч. 1 ст. 31",
        "zakupki.gov.ru → РНП, поиск по ИНН и ФИО",
        "Сделать скриншот проверки на дату подачи — пригодится при жалобе"))


def _rule_national_regime(tender, company, items, text):
    t = _norm(text)
    hits = []
    if re.search(r"запрет|не допускается|не допускаются к участию", t):
        hits.append("запрет")
    if re.search(r"ограничени", t):
        hits.append("ограничение")
    if re.search(r"преимуществ|15\s*%|приоритет", t):
        hits.append("преимущество")
    reestr = re.search(r"реестров\w* запис|ррпп|реестр российской промышленной|реестре евразийск", t)
    ball = re.search(r"балл\w*|719", t)
    okpd = tender.get("okpd") or company.get("okpd") or ""
    if not hits and not reestr:
        return
    detail = []
    if hits:
        detail.append("в ТЗ упомянуты меры: " + ", ".join(sorted(set(hits))))
    if reestr:
        detail.append("требуется реестровая запись (РРПП)")
    if ball:
        detail.append("упоминаются баллы локализации (ПП № 719)")
    items.append(_item(
        "nacrezhim", "Национальный режим (ПП № 1875)", "warn",
        "; ".join(detail) + (f". ОКПД2 закупки: {okpd}." if okpd else "."),
        "Товар без подтверждённого происхождения приравнивается к иностранному и отклоняется "
        "или проигрывает 15% к цене. Реестровая запись может быть изменена или исключена — "
        "её надо проверить на дату подачи, а не «когда-то была».",
        "ст. 14 44-ФЗ, ПП РФ № 1875 (прил. № 1 — запрет, № 2 — ограничение), ПП № 719",
        "ГИСП → реестр РРПП, карточка КТРУ в ЕИС",
        "Сверить конкретную позицию приложения по ОКПД2 (не только первые цифры), "
        "запросить у производителя актуальный номер реестровой записи и баллы"))
    items.append(_item(
        "country", "Страна происхождения товара", "warn",
        "Указать код страны происхождения по ОКСМ в заявке и подтвердить реестровой записью, "
        "если товар из перечня ПП № 1875.",
        "Отсутствие страны происхождения — прямое основание для отклонения, особенно "
        "в закупках с нацрежимом.",
        "ст. 14 44-ФЗ, п. 1 ч. 12 ст. 48", "структурированная форма заявки на ЭТП",
        "Проверить, что код ОКСМ и реестровая запись совпадают с предложенным товаром"))


def _rule_ranges(tender, company, items, text):
    found = _search([r"не более\s*\d", r"не менее\s*\d", r"от\s*\d+\s*до\s*\d+", r"или эквивалент",
                     r"диапазон"], text)
    if not found:
        return
    items.append(_item(
        "ranges", "Диапазонные характеристики в ТЗ", "warn",
        "В ТЗ есть характеристики в виде диапазонов и формулировки «не более / не менее».",
        "В заявке нельзя повторять диапазон — нужно конкретное значение. "
        "За диапазон вместо значения отклоняют по п. 1 ч. 12 ст. 48.",
        "п. 1 ч. 12 ст. 48, ч. 3 ст. 66 44-ФЗ", "структурированная форма заявки",
        "По каждой характеристике подставить одно конкретное значение, которое проходит по диапазону",
        found=found))


def _rule_pp2571(tender, company, items, text):
    found = _search([r"дополнительн\w* требован", r"2571", r"опыт исполнения контракт",
                     r"опыт поставки", r"не менее\s*\d+\s*(?:контракт|договор)"], text)
    if not found:
        return
    exp = company.get("experience_years")
    items.append(_item(
        "pp2571", "Дополнительные требования к опыту (ПП № 2571)", "warn",
        "Закупка содержит доптребования к опыту участника." + (f" Опыт завода: {exp} лет." if exp else ""),
        "Опыт подтверждается исполненным контрактом и реестровой записью. ФАС признаёт отклонение "
        "незаконным, если опыт подтверждён реестровой записью без приложений к контракту, "
        "но время на жалобу вы уже потеряете.",
        "ч. 2, 2.1 ст. 31 44-ФЗ, ПП РФ № 2571",
        "приложение к извещению с позицией доптребований, реестр контрактов",
        "Подготовить контракт-аналог + реестровую запись; проверить, что сумма/цена опыта "
        "не ниже требуемого порога", found=found))


def _rule_security(tender, company, items, text):
    bid = tender.get("bid_security_pct") or _pct(text, ["обеспечение заявки", "обеспечения заявки"])
    con = tender.get("contract_security_pct") or _pct(text, ["обеспечение исполнения", "обеспечения исполнения"])
    nmck = float(tender.get("sum") or 0)
    garant = re.search(r"независим\w* гаранти|банковск\w* гаранти", _norm(text))
    if not bid and not con and not garant:
        return
    parts = []
    if bid and nmck:
        parts.append(f"заявка {bid:g}% = {_fmt(nmck * bid / 100)}")
    if con and nmck:
        parts.append(f"исполнение {con:g}% = {_fmt(nmck * con / 100)}")
    if garant:
        parts.append("требуется независимая гарантия")
    items.append(_item(
        "security", "Обеспечение заявки и контракта", "warn",
        "; ".join(parts) if parts else "В документации есть требования по обеспечению.",
        "Гарантию готовят 2–4 дня, банк берёт 1,5–4% от суммы. Ошибка в сумме, сроке или ИКЗ — "
        "отклонение (ч. 6, 8.2 ст. 45). При снижении цены на 25%+ обеспечение исполнения ×1,5.",
        "ст. 44, 45, 96 44-ФЗ", "извещение + проект контракта",
        "Заказать гарантию заранее; проверить в ней ИКЗ всех заказчиков (в совместных закупках), "
        "срок действия и сумму прописью"))


def _rule_advance(tender, company, items, text):
    found = _search([r"аванс", r"казначейск\w* сопровожд", r"отсрочка платеж", r"оплата в течение\s*\d+\s*(?:рабочих\s*)?дн",
                     r"по факту поставки"], text)
    if not found:
        return
    items.append(_item(
        "cash", "Условия оплаты и кассовый разрыв", "warn",
        "В документации есть особые условия оплаты: " + found[:180],
        "Оплата по факту без аванса при цикле производства 30–45 дней означает, что контракт "
        "финансируете вы. Это съедает оборотку и увеличивает стоимость денег в марже.",
        "ст. 34 44-ФЗ", "проект контракта, раздел «Цена и порядок расчётов»",
        "Посчитать кассовый разрыв и заложить стоимость денег в цену"))


def _rule_penalties(tender, company, items, text):
    found = _search([r"пен[яи]", r"штраф", r"неустойк", r"расторжен\w* в одностороннем"], text)
    if not found:
        return
    items.append(_item(
        "penalty", "Штрафы, пени и односторонний отказ", "info",
        "В проекте контракта есть пени/штрафы/право заказчика расторгнуть в одностороннем порядке.",
        "Неустойка и расторжение — путь в РНП. Штрафы надо заложить в маржу до подачи, "
        "а не обнаружить после просрочки.",
        "ст. 34, 95, 104 44-ФЗ", "проект контракта", "Посчитать худший сценарий: пени за 10 дней просрочки"))


def _rule_smb(tender, company, items, text):
    if not _search([r"\bсмп\b", r"субъект\w* малого", r"сонко"], text):
        return
    items.append(_item(
        "smb", "Закупка только для СМП/СОНКО", "warn",
        "Закупка проводится среди субъектов малого предпринимательства.",
        "Участвовать может только компания из реестра МСП. Проверить статус на дату подачи; "
        "для СМП действуют особенности обеспечения и антидемпинга.",
        "ст. 30 44-ФЗ", "реестр МСП (rmsp.nalog.ru)",
        "Проверить себя в реестре МСП; уточнить, применяются ли антидемпинговые меры"))


def _rule_ktru(tender, company, items, text):
    if not _search([r"ктру"], text):
        return
    items.append(_item(
        "ktru", "Позиция КТРУ", "info",
        "В закупке используется КТРУ — характеристики берутся из карточки позиции.",
        "Заказчик обязан применять характеристики КТРУ без подмены; если он подменил — "
        "это основание для запроса разъяснений или жалобы.",
        "п. 5 ч. 1 ст. 42 44-ФЗ", "карточка КТРУ в ЕИС",
        "Сверить карточку КТРУ с описанием объекта закупки"))


# ---------- антидемпинг ----------
def antidumping(nmck, discount_pct, contract_security_pct=None, is_smp=False, is_vital=False):
    """Возвращает последствия снижения цены по ст. 37 44-ФЗ."""
    nmck = float(nmck or 0)
    d = float(discount_pct or 0)
    sec_pct = float(contract_security_pct or 0)
    out = {"discount_pct": d, "applies": d >= 25, "nmck": nmck,
           "notes": [], "security_now": None, "security_x15": None, "extra_cost": None}
    if is_smp:
        out["notes"].append("Закупка для СМП: обеспечение и антидемпинговые меры не применяются (ч. 6 ст. 96).")
        out["applies"] = False
        return out
    if d < 25:
        out["notes"].append("Снижение меньше 25% — антидемпинговые меры не применяются.")
        return out
    if not nmck:
        out["notes"].append("НМЦК не указана — расчёт обеспечения невозможен.")
        return out
    base = nmck * (sec_pct / 100) if sec_pct else 0.0
    x15 = base * 1.5
    floor = nmck * 0.10
    required = max(x15, floor) if sec_pct else floor
    out.update({"security_now": base or None, "security_x15": round(required, 2),
                "extra_cost": round(max(0, required - base), 2)})
    if nmck > 15_000_000:
        out["notes"].append(
            f"НМЦК больше 15 млн ₽: выбора нет — обеспечение исполнения в 1,5 раза "
            f"({_fmt(x15)}), но не менее 10% НМЦК ({_fmt(floor)}) и не менее аванса.")
    else:
        out["notes"].append(
            f"НМЦК до 15 млн ₽: либо обеспечение ×1,5 ({_fmt(x15)}, не менее 10% НМЦК — {_fmt(floor)}), "
            f"либо подтверждение добросовестности: 1 исполненный контракт не менее 20% от цены "
            f"({_fmt(nmck * (1 - d / 100) * 0.2)}) из реестра контрактов за последние 3 года.")
    if not sec_pct:
        out["notes"].append("В документации обеспечение исполнения не установлено — тогда применяется "
                            "минимум 10% от НМЦК.")
    if is_vital:
        out["notes"].append("Товары жизнеобеспечения: дополнительно нужно обоснование цены (ч. 9 ст. 37).")
    out["notes"].append("Банковская гарантия стоит денег: при ставке 3% дополнительное обеспечение "
                        f"обойдётся примерно в {_fmt(max(0, required - base) * 0.03)} — уберите из маржи.")
    return out


# ---------- основной вход ----------
def check(tender, company, discount_pct=None, text=None):
    """Полная проверка. tender/company — словари из базы, text — переопределение текста ТЗ."""
    tender = tender or {}
    company = company or {}
    body = text if text is not None else _text_of(tender)
    items = []
    _rule_deadline(tender, company, items)
    _rule_nmck(tender, company, items)
    _rule_lead_time(tender, company, items, body)
    _rule_certs(tender, company, items, body)
    _rule_declaration_31(tender, company, items, body)
    _rule_national_regime(tender, company, items, body)
    _rule_ranges(tender, company, items, body)
    _rule_pp2571(tender, company, items, body)
    _rule_security(tender, company, items, body)
    _rule_advance(tender, company, items, body)
    _rule_penalties(tender, company, items, body)
    _rule_smb(tender, company, items, body)
    _rule_ktru(tender, company, items, body)

    # антидемпинг — отдельным блоком, если задано планируемое снижение
    anti = None
    if discount_pct is not None:
        anti = antidumping(tender.get("sum"), discount_pct,
                           tender.get("contract_security_pct") or _pct(body, ["обеспечение исполнения"]),
                           is_smp=bool(_search([r"\bсмп\b", r"субъект\w* малого"], body)),
                           is_vital=bool(_search([r"жизнеобеспеч"], body)))
    fails = sum(1 for i in items if i["status"] == "fail")
    warns = sum(1 for i in items if i["status"] == "warn")
    oks = sum(1 for i in items if i["status"] == "ok")
    infos = sum(1 for i in items if i["status"] == "info")
    score = max(0, 100 - fails * 25 - warns * 6)
    verdict = "blocked" if fails else ("attention" if warns >= 4 else "ok")
    if verdict == "ok" and warns:
        verdict = "attention" if warns else "ok"
    summary = {
        "blocked": "Есть блокирующие пункты — в текущем виде заявку отклонят или вы не успеете.",
        "attention": "Блокирующих пунктов нет, но есть что проверить до подачи.",
        "ok": "Критичных препятствий не найдено. Пройдите чек-лист перед подачей.",
    }[verdict]
    order = {"fail": 0, "warn": 1, "info": 2, "ok": 3}
    items.sort(key=lambda i: order.get(i["status"], 4))
    return {
        "score": score, "verdict": verdict, "summary": summary,
        "counts": {"fail": fails, "warn": warns, "info": infos, "ok": oks, "total": len(items)},
        "items": items,
        "antidumping": anti,
        "disclaimer": "Проверка автоматическая и не заменяет юриста: сверяйте с редакцией "
                      "документации и проектом контракта на дату подачи.",
    }
