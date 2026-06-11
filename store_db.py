"""SQLite store database: schema, previews, and safe read-only query execution."""

import re
import sqlite3
from pathlib import Path

from seed_data import DB_PATH, seed_database

FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|ATTACH|DETACH|PRAGMA|VACUUM)\b",
    re.IGNORECASE,
)


def ensure_store_db() -> Path:
    if not DB_PATH.exists():
        seed_database()
    return DB_PATH


def _connect() -> sqlite3.Connection:
    ensure_store_db()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def list_tables() -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
    return [row["name"] for row in rows]


def get_table_row_counts() -> dict[str, int]:
    counts = {}
    for table in list_tables():
        with _connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        counts[table] = int(row["n"])
    return counts


def get_sqlalchemy_engine():
    """SQLite engine for LangChain (handles Windows paths with spaces)."""
    from sqlalchemy import create_engine

    db_path = str(ensure_store_db().resolve())

    def connect() -> sqlite3.Connection:
        return sqlite3.connect(db_path, check_same_thread=False)

    return create_engine("sqlite://", creator=connect)


def clean_sql(sql: str) -> str:
    if not isinstance(sql, str):
        sql = str(sql)
    text = sql.strip()
    if text.startswith("SQLQuery:"):
        text = text.split(":", 1)[1].strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    text = text.strip().rstrip(";")
    return text


def is_safe_select(sql: str) -> bool:
    cleaned = clean_sql(sql)
    if not cleaned:
        return False
    if FORBIDDEN_SQL.search(cleaned):
        return False
    first = cleaned.split(None, 1)[0].upper()
    return first == "SELECT" or first == "WITH"


def execute_select(sql: str, max_rows: int = 100) -> tuple[list[dict], str | None]:
    """Run a read-only SELECT. Returns (rows, error_message)."""
    cleaned = clean_sql(sql)
    if not is_safe_select(cleaned):
        return [], "Only read-only SELECT queries are allowed."

    if not re.search(r"\bLIMIT\b", cleaned, re.IGNORECASE):
        cleaned = f"{cleaned} LIMIT {max_rows}"

    try:
        with _connect() as conn:
            cur = conn.execute(cleaned)
            rows = [dict(row) for row in cur.fetchall()]
        return rows, None
    except sqlite3.Error as exc:
        return [], str(exc)


def summarize_rows_for_context(rows: list[dict], max_rows: int = 8) -> str:
    if not rows:
        return "No rows."
    preview = rows[:max_rows]
    lines = [f"Columns: {', '.join(preview[0].keys())}"]
    for i, row in enumerate(preview, 1):
        lines.append(f"{i}. {row}")
    if len(rows) > max_rows:
        lines.append(f"... and {len(rows) - max_rows} more rows")
    return "\n".join(lines)
