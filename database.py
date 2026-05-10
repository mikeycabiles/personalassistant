"""SQLite-backed conversation history and pending action storage.

Two tables:
  * conversations    - rolling chat history per user (pruned to keep_last entries)
  * pending_actions  - proposals awaiting user confirmation, with 10-min expiry
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import config


PENDING_ACTION_TTL = timedelta(minutes=10)


@contextmanager
def _connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_conversations_user
                ON conversations(user_id, id);

            CREATE TABLE IF NOT EXISTS pending_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                action_data TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_pending_actions_user
                ON pending_actions(user_id, id);
            """
        )


# --- Conversation history --------------------------------------------------

def get_conversation_history(user_id: str, limit: int = 15) -> list[dict[str, str]]:
    """Return the last `limit` messages for the user, oldest first."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM conversations
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def save_message(user_id: str, role: str, content: str) -> None:
    if role not in ("user", "assistant"):
        raise ValueError(f"role must be 'user' or 'assistant', got {role!r}")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO conversations (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content),
        )


def clear_old_history(user_id: str, keep_last: int = 15) -> None:
    """Delete all but the most recent `keep_last` messages for the user."""
    with _connect() as conn:
        conn.execute(
            """
            DELETE FROM conversations
            WHERE user_id = ?
              AND id NOT IN (
                  SELECT id FROM conversations
                  WHERE user_id = ?
                  ORDER BY id DESC
                  LIMIT ?
              )
            """,
            (user_id, user_id, keep_last),
        )


def clear_all_history(user_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))


# --- Pending actions -------------------------------------------------------

def save_pending_action(
    user_id: str, action_type: str, action_data: dict[str, Any]
) -> None:
    """Replace any existing pending action for the user with a new one."""
    expires_at = datetime.now(timezone.utc) + PENDING_ACTION_TTL
    with _connect() as conn:
        conn.execute("DELETE FROM pending_actions WHERE user_id = ?", (user_id,))
        conn.execute(
            """
            INSERT INTO pending_actions (user_id, action_type, action_data, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, action_type, json.dumps(action_data), expires_at.isoformat()),
        )


def get_pending_action(user_id: str) -> dict[str, Any] | None:
    """Return the most recent non-expired pending action for the user, or None."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        # Sweep expired rows opportunistically.
        conn.execute(
            "DELETE FROM pending_actions WHERE expires_at < ?",
            (now_iso,),
        )
        row = conn.execute(
            """
            SELECT action_type, action_data
            FROM pending_actions
            WHERE user_id = ? AND expires_at >= ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id, now_iso),
        ).fetchone()
    if not row:
        return None
    return {
        "action_type": row["action_type"],
        "action_data": json.loads(row["action_data"]),
    }


def clear_pending_action(user_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM pending_actions WHERE user_id = ?", (user_id,))
