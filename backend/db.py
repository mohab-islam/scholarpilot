import json
import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.getenv("SP_DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
DB_PATH = DATA_DIR / "scholarpilot.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, kind TEXT, text TEXT,
  created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS scholarships (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT, provider TEXT, country TEXT, level TEXT, field TEXT, funding TEXT,
  url TEXT UNIQUE, open_date TEXT, deadline TEXT,
  required_docs TEXT DEFAULT '[]', docs_done TEXT DEFAULT '[]',
  eligibility TEXT, notes TEXT, status TEXT DEFAULT 'new', source TEXT,
  created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS letters (
  id INTEGER PRIMARY KEY AUTOINCREMENT, scholarship_id INTEGER, kind TEXT, title TEXT,
  content TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS letter_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, letter_id INTEGER, n INTEGER, content TEXT, note TEXT,
  created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS playbook (
  id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT UNIQUE, body TEXT, sources TEXT DEFAULT '[]',
  created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, description TEXT, body TEXT,
  created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS professors (
  id INTEGER PRIMARY KEY AUTOINCREMENT, scholarship_id INTEGER, name TEXT, title TEXT, university TEXT,
  department TEXT, lab TEXT, research_interests TEXT, recent_work TEXT, email TEXT, homepage TEXT,
  why_fit TEXT, status TEXT DEFAULT 'found', letter_id INTEGER, created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS seen_posts (key TEXT PRIMARY KEY, verdict TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS ingest_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT, post TEXT, status TEXT DEFAULT 'pending', created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, title TEXT, body TEXT, ref_id INTEGER, read INTEGER DEFAULT 0,
  created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS dedupe_checked (sig TEXT PRIMARY KEY, created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS todos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, scholarship_id INTEGER, text TEXT, kind TEXT DEFAULT 'step',
  done INTEGER DEFAULT 0, source TEXT DEFAULT 'auto', detail TEXT DEFAULT '', created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS mail_events (
  msg_id TEXT PRIMARY KEY, scholarship_id INTEGER, professor_id INTEGER, kind TEXT, summary TEXT,
  subject TEXT, sender TEXT, mail_date TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
"""

MIGRATIONS = {
    "scholarships": [("pinned", "INTEGER DEFAULT 0"), ("steps_done", "TEXT DEFAULT '[]'"),
                     ("research", "TEXT DEFAULT ''"), ("fit", "TEXT DEFAULT ''"), ("fit_reason", "TEXT DEFAULT ''"), ("eligibility_check", "TEXT DEFAULT ''"), ("verdict", "TEXT DEFAULT ''"),
                     ("verdict_reason", "TEXT DEFAULT ''"), ("verified_at", "TEXT DEFAULT ''"),
                     ("confidence", "TEXT DEFAULT ''"), ("verify_tries", "INTEGER DEFAULT 0"), ("application_mode", "TEXT DEFAULT ''"), ("application_fee", "TEXT DEFAULT ''")],
}

JSON_COLS = ("required_docs", "docs_done", "steps_done")


def conn():
    DB_PATH.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)  # several background workers write at once
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        # one-off backfill: rows from before verification existed
        c.execute("UPDATE scholarships SET verdict='qualified' WHERE verdict='' AND (source IN ('manual','research') OR pinned=1)")
        c.execute("UPDATE scholarships SET verdict='pending' WHERE verdict=''")


def row(r):
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS:
        if k in d:
            try:
                d[k] = json.loads(d[k] or "[]")
            except ValueError:
                d[k] = []
    return d


def rows(rs):
    return [row(r) for r in rs]
