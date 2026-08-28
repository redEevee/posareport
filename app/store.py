"""Small local persistence layer for aggregate-only report data and OAuth tokens."""

import json
import os
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = Path(os.environ.get("REPORT_DATABASE_PATH", ROOT / "data" / "report.db"))


def connection():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(DATABASE_PATH)
    database.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return database


def get_json(key, default=None):
    with connection() as database:
        row = database.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_json(key, value):
    with connection() as database:
        database.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
