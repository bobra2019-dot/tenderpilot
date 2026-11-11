# -*- coding: utf-8 -*-
"""Заявки с публичной страницы: приём, хранение, статусы."""
import re
from datetime import datetime, timezone

STATUSES = ("new", "taken", "client", "refused")

DDL = """CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL DEFAULT '',
  company TEXT NOT NULL DEFAULT '',
  contact TEXT NOT NULL DEFAULT '',
  comment TEXT NOT NULL DEFAULT '',
  plan TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  ip TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'new',
  note TEXT NOT NULL DEFAULT ''
)"""


def add_leads_table(conn):
    conn.execute(DDL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status)")


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean(text, limit=400):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:limit]


def looks_like_spam(name, contact, comment):
    """Дешёвая защита: ссылки в тексте заявки и слишком короткий контакт."""
    blob = f"{name} {comment}".lower()
    if re.search(r"https?://|www\.|t\.me/|\.ru/", blob):
        return True
    digits = re.sub(r"\D", "", contact)
    if len(digits) < 6 and "@" not in contact:
        return True
    return False


def create(conn, name, company, contact, comment, plan, source, ip):
    conn.execute(
        "INSERT INTO leads (created_at, name, company, contact, comment, plan, source, ip, status, note) "
        "VALUES (?,?,?,?,?,?,?,?,'new','')",
        (_now(), clean(name, 120), clean(company, 160), clean(contact, 160),
         clean(comment, 800), clean(plan, 40), clean(source, 60), clean(ip, 60)),
    )
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
    return row["id"] if row else 0


def listing(conn, limit=200):
    rows = conn.execute(
        "SELECT * FROM leads ORDER BY CASE status WHEN 'new' THEN 0 ELSE 1 END, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def counts(conn):
    rows = conn.execute("SELECT status, COUNT(*) AS c FROM leads GROUP BY status").fetchall()
    out = {s: 0 for s in STATUSES}
    for r in rows:
        out[r["status"]] = r["c"]
    out["total"] = sum(v for k, v in out.items() if k in STATUSES)
    return out


def set_status(conn, lead_id, status, note=None):
    if status not in STATUSES:
        raise ValueError("unknown status")
    conn.execute("UPDATE leads SET status=? WHERE id=?", (status, lead_id))
    if note is not None:
        conn.execute("UPDATE leads SET note=? WHERE id=?", (clean(note, 600), lead_id))
    conn.commit()
    return True


def to_csv(conn):
    import csv
    import io
    rows = listing(conn, limit=5000)
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Дата", "Имя", "Компания", "Контакт", "Тариф", "Что нужно", "Источник", "Статус", "Заметка"])
    for r in rows:
        w.writerow([r["created_at"][:16].replace("T", " "), r["name"], r["company"], r["contact"],
                    r["plan"], r["comment"], r["source"], r["status"], r["note"]])
    return buf.getvalue()
