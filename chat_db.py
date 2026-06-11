"""SQLite storage for chat history."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).parent / "chat.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                sql_text TEXT,
                result_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_conversation
                ON messages(conversation_id);
            """
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate_title(text: str, max_len: int = 48) -> str:
    cleaned = " ".join(text.strip().split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3] + "..."


def create_conversation(title: str = "New chat") -> int:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO conversations (title, created_at, updated_at)
            VALUES (?, ?, ?)
            """,
            (title, now, now),
        )
        return int(cur.lastrowid)


def update_conversation_title(conversation_id: int, title: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), conversation_id),
        )


def get_conversation(conversation_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, title FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    return dict(row) if row else None


def list_conversations(limit: int = 20) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, title, created_at, updated_at
            FROM conversations
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_first_user_message(conversation_id: int) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT content FROM messages
            WHERE conversation_id = ? AND role = 'user'
            ORDER BY id ASC
            LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
    return row["content"] if row else None


def get_display_title(conversation_id: int, title: str) -> str:
    if title != "New chat":
        return title
    first = get_first_user_message(conversation_id)
    if not first:
        return title
    resolved = _truncate_title(first)
    update_conversation_title(conversation_id, resolved)
    return resolved


def add_message(
    conversation_id: int,
    role: str,
    content: str,
    sql_text: str | None = None,
    rows: list[dict] | None = None,
) -> None:
    result_json = json.dumps(rows) if rows else None
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO messages (
                conversation_id, role, content, sql_text, result_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (conversation_id, role, content, sql_text, result_json, _now()),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (_now(), conversation_id),
        )


def load_messages(conversation_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT role, content, sql_text, result_json
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (conversation_id,),
        ).fetchall()

    messages = []
    for row in rows:
        msg: dict[str, Any] = {"role": row["role"], "content": row["content"]}
        if row["sql_text"]:
            msg["sql"] = row["sql_text"]
        if row["result_json"]:
            msg["rows"] = json.loads(row["result_json"])
        messages.append(msg)
    return messages


def delete_conversation(conversation_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))


def get_history_for_sql(conversation_id: int) -> list[dict]:
    """Last few Q&A turns for SQL context (user questions + SQL summaries)."""
    messages = load_messages(conversation_id)
    history: list[dict] = []
    pending_q: str | None = None
    for msg in messages:
        if msg["role"] == "user":
            pending_q = msg["content"]
        elif msg["role"] == "assistant" and pending_q:
            summary = ""
            if msg.get("rows"):
                cols = list(msg["rows"][0].keys()) if msg["rows"] else []
                summary = f"{len(msg['rows'])} rows, columns: {', '.join(cols)}"
            history.append(
                {
                    "question": pending_q,
                    "sql": msg.get("sql", ""),
                    "summary": summary,
                }
            )
            pending_q = None
    return history[-3:]
