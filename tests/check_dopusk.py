# -*- coding: utf-8 -*-
"""Самопроверка правил «Проверки допуска». Запуск: python3 tests/check_dopusk.py

Никаких зависимостей: чистые assert'ы, чтобы можно было гонять на сервере без pytest.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import dopusk  # noqa: E402

PASS = []


def ok(name, cond):
    PASS.append((name, bool(cond)))


def codes(res):
    return {i["code"]: i for i in res["items"]}


# ---------- 1. срок поставки против цикла завода ----------
company = {"lead_days": 45, "max_order_sum": 5_000_000, "certificates": ["ИСО 9001"]}
t = {"title": "Тест", "sum": 3_000_000, "delivery_days": 30, "tz_text": "Поставка в течение 30 дней."}
r = dopusk.check(t, company)
ok("срок короче цикла → блокирует", codes(r)["lead_time"]["status"] == "fail")
ok("вердикт blocked", r["verdict"] == "blocked")

# ---------- 2. срок с запасом ----------
t2 = dict(t, delivery_days=60)
r2 = dopusk.check(t2, company)
ok("срок с запасом → ок", codes(r2)["lead_time"]["status"] == "ok")

# ---------- 3. сертификаты ----------
t3 = {"title": "Тест", "sum": 1_000_000, "tz_text": "Требуется сертификат ТР ТС 032/2013 на арматуру."}
r3 = dopusk.check(t3, {"lead_days": 10, "certificates": ["ИСО 9001"]})
ok("нет нужного сертификата → fail", codes(r3)["certs"]["status"] == "fail")
r3b = dopusk.check(t3, {"lead_days": 10, "certificates": ["ТР ТС 032/2013"]})
ok("сертификат есть → ok", codes(r3b)["certs"]["status"] == "ok")

# ---------- 4. «СРО» не должно ловиться внутри слова «срок» ----------
t4 = {"title": "Тест", "sum": 1_000_000, "tz_text": "Срок поставки 20 дней, сроки оплаты согласованы."}
r4 = dopusk.check(t4, {"lead_days": 10, "certificates": ["ИСО 9001"]})
ok("«срок» не считается СРО", "certs" not in codes(r4) or "СРО" not in codes(r4).get("what", ""))

# ---------- 5. нацрежим ----------
t5 = {"title": "Тест", "sum": 2_000_000, "okpd": "28.14.13",
      "tz_text": "Установлен запрет на допуск иностранных товаров. Требуется реестровая запись."}
r5 = dopusk.check(t5, {"lead_days": 5})
ok("нацрежим распознан", codes(r5)["nacrezhim"]["status"] == "warn")
ok("страна происхождения в чек-листе", "country" in codes(r5))

# ---------- 6. диапазоны ----------
t6 = {"title": "Тест", "sum": 900_000, "tz_text": "Диаметр от 100 до 300 мм, масса не более 25 кг."}
r6 = dopusk.check(t6, {"lead_days": 5})
ok("диапазонные характеристики", codes(r6)["ranges"]["status"] == "warn")

# ---------- 7. обеспечение ----------
t7 = {"title": "Тест", "sum": 1_000_000, "tz_text": "Обеспечение заявки 1%, обеспечение исполнения 10%."}
r7 = dopusk.check(t7, {"lead_days": 5})
ok("обеспечение посчитано в деньгах", "10 000 ₽" in codes(r7)["security"]["what"] or
   "100 000 ₽" in codes(r7)["security"]["what"])

# ---------- 8. антидемпинг ----------
a = dopusk.antidumping(10_000_000, 30, 10)
ok("антидемпинг применяется с 25%", a["applies"] is True)
ok("обеспечение ×1,5", a["security_x15"] == 1_500_000)
ok("доп. затраты посчитаны", a["extra_cost"] == 500_000)
a2 = dopusk.antidumping(10_000_000, 20, 10)
ok("до 25% не применяется", a2["applies"] is False)
a3 = dopusk.antidumping(20_000_000, 30, 10)
ok("НМЦК > 15 млн: минимум 10% НМЦК", a3["security_x15"] == 3_000_000)
ok("НМЦК > 15 млн: про выбор не пишем", any("выбора нет" in n for n in a3["notes"]))
a4 = dopusk.antidumping(10_000_000, 30, 10, is_smp=True)
ok("СМП: антидемпинг не применяется", a4["applies"] is False)

# ---------- 9. пустой профиль (гость) ----------
r9 = dopusk.check({"title": "Тест", "sum": 1_000_000,
                   "tz_text": "Требуется сертификат ТР ТС 032/2013. Поставка 30 дней."}, {})
ok("гость: нет ложного «в профиле нет»", "В профиле завода этого нет" not in codes(r9)["certs"]["what"])
ok("гость: подсказка заполнить профиль", "профиле" in codes(r9)["lead_time"]["fix"])

# ---------- 10. лимит суммы заказа ----------
r10 = dopusk.check({"title": "Тест", "sum": 9_000_000, "tz_text": "Поставка 10 дней."},
                   {"lead_days": 5, "max_order_sum": 5_000_000})
ok("сумма выше лимита → fail", codes(r10)["nmck"]["status"] == "fail")

# ---------- итог ----------
fails = [n for n, ok_ in PASS if not ok_]
for name, res in PASS:
    print(("  ok  " if res else " FAIL ") + name)
print(f"\nитог: {len(PASS) - len(fails)} из {len(PASS)} проверок пройдено")
sys.exit(1 if fails else 0)
