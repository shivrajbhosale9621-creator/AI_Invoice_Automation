"""
chat_store.py - Persistent conversation history using the existing SQLite database (invoices.db).

Responsibilities (distinct from LangGraph checkpoint storage):
- Create and track conversations (conversation_id, user_id, timestamps, metadata)
- Persist individual chat messages (role, content, timestamp, metadata)
- List previous conversations for a user
- Retrieve messages in chronological order for context restoration
- Enforce user-level isolation (different users cannot see each other's conversations)
- Prevent duplicate messages via idempotency keys
- Provide atomic operations using SQLite transactions

This module is the *application-facing* chat history layer.
LangGraph graph state checkpointing is a separate concern handled in orchestrator.py.
"""

import sqlite3
import re
import uuid
import logging
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Default DB path – reuses the project's existing database file
_DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "invoices.db")

# ─────────────────────────────────────────────────────────────────────────────
# Schema initialisation
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    title           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    metadata        TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_conversations_updated_at ON conversations(updated_at);

CREATE TABLE IF NOT EXISTS messages (
    message_id      TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    role            TEXT NOT NULL,        -- 'user' | 'assistant' | 'system' | 'tool'
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    idempotency_key TEXT,                 -- optional caller-supplied deduplication key
    metadata        TEXT DEFAULT '{}'     -- e.g. tool_name, token_counts, summarized flag
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_created_at      ON messages(conversation_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_idempotency
    ON messages(conversation_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS conversation_summaries (
    summary_id      TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    summary_text    TEXT NOT NULL,
    summarized_up_to_message_id TEXT,     -- last message_id included in this summary
    created_at      TEXT NOT NULL,
    metadata        TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_summaries_conversation_id ON conversation_summaries(conversation_id);

CREATE TABLE IF NOT EXISTS dashboard_charts (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    title       TEXT NOT NULL,
    prompt      TEXT NOT NULL,
    chart_path  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dashboard_charts_user_id ON dashboard_charts(user_id);
"""


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Connection helpers
# ─────────────────────────────────────────────────────────────────────────────

def init_db(db_path: str = _DEFAULT_DB_PATH) -> None:
    """
    Create chat-history tables in the SQLite database if they do not already exist.
    Safe to call repeatedly (idempotent).
    """
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")   # better concurrency
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        conn.close()
        logger.info("Chat-history schema initialised in %s", db_path)
    except sqlite3.Error as exc:
        logger.error("Failed to initialise chat-history schema: %s", exc)
        raise


@contextmanager
def _get_conn(db_path: str = _DEFAULT_DB_PATH):
    """Context-managed SQLite connection with WAL mode and FK enforcement."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Conversation CRUD
# ─────────────────────────────────────────────────────────────────────────────

def create_conversation(
    user_id: str,
    title: str = "",
    conversation_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    db_path: str = _DEFAULT_DB_PATH,
) -> str:
    """
    Create a new conversation record and return its conversation_id.

    Parameters
    ----------
    user_id : str
        Identifier for the user who owns this conversation.
    title : str
        Optional short title for display in conversation lists.
    conversation_id : str, optional
        Caller-supplied stable ID (e.g. a UUID tied to a UI session).
        Generates a new UUID if not provided.
    metadata : dict, optional
        Arbitrary key-value pairs stored as JSON.

    Returns
    -------
    str
        The conversation_id of the newly created conversation.
    """
    cid = conversation_id or str(uuid.uuid4())
    now = _now_iso()
    meta_json = json.dumps(metadata or {})

    with _get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO conversations (conversation_id, user_id, title, created_at, updated_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(conversation_id) DO NOTHING
            """,
            (cid, user_id, title or "", now, now, meta_json),
        )

    logger.debug("Conversation created: %s (user=%s)", cid, user_id)
    return cid


def get_conversation(
    conversation_id: str,
    user_id: str,
    db_path: str = _DEFAULT_DB_PATH,
) -> Optional[dict]:
    """
    Return conversation metadata if it belongs to user_id; None otherwise.
    Enforces user-level isolation.
    """
    with _get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def list_conversations(
    user_id: str,
    limit: int = 50,
    db_path: str = _DEFAULT_DB_PATH,
) -> list[dict]:
    """
    Return a list of conversation summaries for user_id, most-recently-updated first.
    """
    with _get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM conversations
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _touch_conversation(conn: sqlite3.Connection, conversation_id: str) -> None:
    """Update the conversation's updated_at timestamp (call within an open transaction)."""
    conn.execute(
        "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
        (_now_iso(), conversation_id),
    )


def _derive_title(content: str, max_len: int = 48) -> str:
    """Build a sidebar title from the first user message."""
    text = " ".join((content or "").split())
    # Drop internal attachment markers the API appends to user messages
    text = re.sub(r"\[Attached file saved at:[^\]]*\]", "", text).strip()
    text = text.strip(" .!?")
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0] or text[:max_len]
    return cut.strip() + "..."


def _title_conversation_from_first_message(conn: sqlite3.Connection, conversation_id: str, content: str) -> None:
    """Name an untitled conversation after its first user message.

    Conversations are created with an empty title, so the sidebar used to
    show the same fallback label for every chat. Only fills the title when
    it is still empty and this is the conversation's first user message.
    """
    row = conn.execute(
        "SELECT title FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if not row or (row[0] or "").strip():
        return
    count = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND role = 'user'",
        (conversation_id,),
    ).fetchone()[0]
    if count != 1:
        return
    title = _derive_title(content)
    if title:
        conn.execute(
            "UPDATE conversations SET title = ? WHERE conversation_id = ?",
            (title, conversation_id),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Message CRUD
# ─────────────────────────────────────────────────────────────────────────────

def save_message(
    conversation_id: str,
    role: str,
    content: str,
    idempotency_key: Optional[str] = None,
    metadata: Optional[dict] = None,
    db_path: str = _DEFAULT_DB_PATH,
) -> Optional[str]:
    """
    Persist a single chat message.  Returns message_id, or None if the message
    was a duplicate (idempotency_key already present for this conversation).

    Parameters
    ----------
    conversation_id : str
    role : str
        One of 'user', 'assistant', 'system', 'tool'.
    content : str
        Message text.
    idempotency_key : str, optional
        Caller-supplied deduplication token.  If a message with the same key
        already exists for this conversation, the insert is silently skipped.
    metadata : dict, optional
        Arbitrary JSON metadata (e.g. tool_name, token_count).

    Returns
    -------
    str | None
        message_id of the inserted row, or None if skipped as a duplicate.
    """
    mid = str(uuid.uuid4())
    now = _now_iso()
    meta_json = json.dumps(metadata or {})

    with _get_conn(db_path) as conn:
        try:
            conn.execute(
                """
                INSERT INTO messages
                    (message_id, conversation_id, role, content, created_at, idempotency_key, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (mid, conversation_id, role, content, now, idempotency_key, meta_json),
            )
            _touch_conversation(conn, conversation_id)
            if role == "user":
                _title_conversation_from_first_message(conn, conversation_id, content)
        except sqlite3.IntegrityError:
            # Unique constraint on (conversation_id, idempotency_key) was violated
            logger.debug(
                "Duplicate message skipped for conversation %s key=%s",
                conversation_id,
                idempotency_key,
            )
            return None

    return mid


def get_messages(
    conversation_id: str,
    user_id: str,
    limit: Optional[int] = None,
    db_path: str = _DEFAULT_DB_PATH,
) -> list[dict]:
    """
    Return messages for a conversation in chronological order.
    Enforces user-level isolation by verifying that the conversation belongs to user_id.

    Parameters
    ----------
    conversation_id : str
    user_id : str
        Must match the owner of the conversation.
    limit : int, optional
        Return only the most recent N messages if specified.
    """
    # Verify ownership
    if not get_conversation(conversation_id, user_id, db_path):
        logger.warning(
            "Access denied: user %s tried to read conversation %s",
            user_id,
            conversation_id,
        )
        return []

    with _get_conn(db_path) as conn:
        if limit:
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM messages
                    WHERE conversation_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                ) ORDER BY created_at ASC
                """,
                (conversation_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM messages
                WHERE conversation_id = ?
                ORDER BY created_at ASC
                """,
                (conversation_id,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_all_messages_raw(
    conversation_id: str,
    db_path: str = _DEFAULT_DB_PATH,
) -> list[dict]:
    """
    Internal helper – returns all messages without ownership check.
    Used by the summarisation layer which already holds a validated conversation object.
    """
    with _get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Summary CRUD
# ─────────────────────────────────────────────────────────────────────────────

def save_summary(
    conversation_id: str,
    summary_text: str,
    summarized_up_to_message_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    db_path: str = _DEFAULT_DB_PATH,
) -> str:
    """
    Atomically persist a new conversation summary.
    Returns the summary_id.

    The insert is atomic – a summarisation failure (e.g. LLM error) before this
    call succeeds will not corrupt or partially overwrite existing summaries.
    """
    sid = str(uuid.uuid4())
    now = _now_iso()
    meta_json = json.dumps(metadata or {})

    with _get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO conversation_summaries
                (summary_id, conversation_id, summary_text, summarized_up_to_message_id, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (sid, conversation_id, summary_text, summarized_up_to_message_id, now, meta_json),
        )

    logger.info(
        "Summary saved for conversation %s (up_to=%s)", conversation_id, summarized_up_to_message_id
    )
    return sid


def get_latest_summary(
    conversation_id: str,
    db_path: str = _DEFAULT_DB_PATH,
) -> Optional[dict]:
    """Return the most recent summary record for a conversation, or None."""
    with _get_conn(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM conversation_summaries
            WHERE conversation_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
    return dict(row) if row else None


def get_all_summaries(
    conversation_id: str,
    db_path: str = _DEFAULT_DB_PATH,
) -> list[dict]:
    """Return all summaries for a conversation, oldest first."""
    with _get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM conversation_summaries
            WHERE conversation_id = ?
            ORDER BY created_at ASC
            """,
            (conversation_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Convenience helpers for the orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def get_or_create_conversation(
    conversation_id: str,
    user_id: str,
    db_path: str = _DEFAULT_DB_PATH,
) -> str:
    """
    Return conversation_id if it already exists for user_id, or create it.
    Used by the orchestrator to resume a conversation after a restart.
    """
    existing = get_conversation(conversation_id, user_id, db_path)
    if existing:
        return conversation_id
    return create_conversation(
        user_id=user_id,
        conversation_id=conversation_id,
        db_path=db_path,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard custom charts
# ─────────────────────────────────────────────────────────────────────────────

def add_dashboard_chart(
    user_id: str,
    title: str,
    prompt: str,
    chart_path: str,
    db_path: str = _DEFAULT_DB_PATH,
) -> dict:
    """Save a user-created custom chart to the dashboard folio."""
    init_db(db_path)
    chart_id = f"dchart_{uuid.uuid4().hex[:12]}"
    created_at = _now_iso()
    with _get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO dashboard_charts (id, user_id, title, prompt, chart_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chart_id, user_id, title, prompt, chart_path, created_at),
        )
    return {
        "id": chart_id,
        "user_id": user_id,
        "title": title,
        "prompt": prompt,
        "chart_path": chart_path,
        "created_at": created_at,
    }


def list_dashboard_charts(
    user_id: str,
    db_path: str = _DEFAULT_DB_PATH,
) -> list[dict]:
    """Retrieve all saved custom charts for this user, newest first."""
    init_db(db_path)
    with _get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, title, prompt, chart_path, created_at
            FROM dashboard_charts
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_dashboard_chart(
    chart_id: str,
    user_id: str,
    db_path: str = _DEFAULT_DB_PATH,
) -> bool:
    """Delete a custom chart belonging to this user."""
    init_db(db_path)
    with _get_conn(db_path) as conn:
        cursor = conn.execute(
            """
            DELETE FROM dashboard_charts
            WHERE id = ? AND user_id = ?
            """,
            (chart_id, user_id),
        )
        return cursor.rowcount > 0
