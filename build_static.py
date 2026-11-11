# -*- coding: utf-8 -*-
"""Собирает статическую демо-версию в docs/ для GitHub Pages.
Данные берутся из SQLite, скоринг и маржа считаются в браузере (web/static-api.js).
"""
import json
import os
import shutil
import sqlite3

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "data", "tenderpilot.db")
OUT = os.path.join(ROOT, "docs")
WEB = os.path.join(ROOT, "web")

from app.catalog import CATEGORIES, PLATFORMS, REGIONS, REGION_LOGISTICS, STAGES  # noqa: E402
from app.db import PROVIDER_PRESETS, get_company  # noqa: E402


def main():
    os.makedirs(OUT, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    tenders = []
    for row in conn.execute("SELECT * FROM tenders ORDER BY id"):
        t = dict(row)
        for k in ("requirements", "files", "analysis"):
            if t.get(k):
                try:
                    t[k] = json.loads(t[k])
                except Exception:
                    t[k] = [] if k != "analysis" else None
            else:
                t[k] = [] if k != "analysis" else None
        t.pop("manual", None)
        t.pop("analysis_source", None)
        tenders.append(t)

    events = [dict(r) for r in conn.execute(
        """SELECT e.id, e.tender_id, e.type, e.text, e.created_at, t.title
           FROM events e LEFT JOIN tenders t ON t.id = e.tender_id
           ORDER BY e.id DESC LIMIT 60""").fetchall()]
    company = get_company(conn)
    conn.close()

    data = {
        "tenders": tenders,
        "company": company,
        "events": events,
        "catalog": {"platforms": PLATFORMS, "regions": REGIONS, "categories": CATEGORIES, "stages": STAGES},
        "region_logistics": REGION_LOGISTICS,
        "presets": PROVIDER_PRESETS,
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
    with open(os.path.join(OUT, "data.js"), "w", encoding="utf-8") as f:
        f.write("window.TP_DATA = ")
        json.dump(data, f, ensure_ascii=False)
        f.write(";\n")

    shutil.copy(os.path.join(WEB, "styles.css"), os.path.join(OUT, "styles.css"))
    shutil.copy(os.path.join(WEB, "app.js"), os.path.join(OUT, "app.js"))
    shutil.copy(os.path.join(WEB, "static-api.js"), os.path.join(OUT, "static-api.js"))

    html = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TenderPilot — автопастер тендеров для завода (демо)</title>
<link rel="stylesheet" href="styles.css">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='22' fill='%231d4ed8'/><text x='50' y='68' font-size='54' text-anchor='middle' fill='white' font-family='Arial' font-weight='bold'>TP</text></svg>">
</head>
<body>
<div id="root"></div>
<div class="toast" id="toast"></div>
<script src="data.js"></script>
<script src="app.js"></script>
<script src="static-api.js"></script>
</body>
</html>
"""
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    with open(os.path.join(OUT, ".nojekyll"), "w") as f:
        f.write("")

    total = sum(os.path.getsize(os.path.join(OUT, f)) for f in os.listdir(OUT))
    print("docs/ собрана:", len(os.listdir(OUT)), "файлов,", round(total / 1024, 1), "КБ")
    print("закупок:", len(tenders), "| событий:", len(events))


if __name__ == "__main__":
    main()
