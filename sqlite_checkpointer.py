"""
sqlite_checkpointer.py - SQLite-backed LangGraph checkpoint saver.

Compatible with langgraph 0.6.3 / langgraph-checkpoint 2.1.1.

This is the *graph-state persistence* layer — distinct from chat_store.py
which handles application-facing conversation/message history.

Responsibilities:
- Persist LangGraph agent state (checkpoints, intermediate writes, blobs)
  to the existing invoices.db SQLite file.
- Restore graph state per thread_id across application restarts.
- Keep different threads (conversations/users) isolated.
- Mirror the InMemorySaver interface exactly so it can be swapped in as
  a drop-in replacement everywhere create_react_agent(..., checkpointer=...)
  is used.
"""

import json
import logging
import random
import sqlite3
import threading
from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

logger = logging.getLogger(__name__)

# Re-use the project's existing database
import os
_DEFAULT_DB = os.path.join(os.path.dirname(__file__), "invoices.db")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS lg_checkpoints (
    thread_id       TEXT NOT NULL,
    checkpoint_ns   TEXT NOT NULL DEFAULT '',
    checkpoint_id   TEXT NOT NULL,
    checkpoint_data BLOB NOT NULL,
    metadata_data   BLOB NOT NULL,
    parent_id       TEXT,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS lg_writes (
    thread_id       TEXT NOT NULL,
    checkpoint_ns   TEXT NOT NULL DEFAULT '',
    checkpoint_id   TEXT NOT NULL,
    task_id         TEXT NOT NULL,
    write_idx       INTEGER NOT NULL,
    channel         TEXT NOT NULL,
    value_data      BLOB NOT NULL,
    task_path       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, write_idx)
);

CREATE TABLE IF NOT EXISTS lg_blobs (
    thread_id       TEXT NOT NULL,
    checkpoint_ns   TEXT NOT NULL DEFAULT '',
    channel         TEXT NOT NULL,
    version         TEXT NOT NULL,
    type_tag        TEXT NOT NULL,
    value_data      BLOB NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

CREATE INDEX IF NOT EXISTS idx_lg_cp_thread
    ON lg_checkpoints(thread_id, checkpoint_ns);
"""

# Index map for special write channel names (mirrors InMemorySaver)
_WRITES_IDX_MAP: dict[str, int] = {}
try:
    from langgraph.checkpoint.base import WRITES_IDX_MAP as _WIM
    _WRITES_IDX_MAP = _WIM
except ImportError:
    pass

_local = threading.local()


def _sanitize_messages(messages: list) -> list:
    """
    Ensure every AIMessage with tool_calls is followed by a matching ToolMessage
    for each tool call ID. If any tool call ID has no corresponding ToolMessage,
    synthesize a recovering ToolMessage so that Groq/OpenAI never fail with
    INVALID_CHAT_HISTORY.
    """
    if not messages:
        return messages

    from langchain_core.messages import AIMessage, ToolMessage

    sanitized = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        sanitized.append(msg)

        tool_calls = getattr(msg, "tool_calls", None)
        if isinstance(msg, AIMessage) and tool_calls:
            expected_ids = {
                tc.get("id")
                for tc in tool_calls
                if isinstance(tc, dict) and tc.get("id")
            }
            found_ids = set()
            j = i + 1
            while j < n:
                next_msg = messages[j]
                if isinstance(next_msg, ToolMessage):
                    tc_id = getattr(next_msg, "tool_call_id", None)
                    if tc_id in expected_ids:
                        found_ids.add(tc_id)
                    j += 1
                else:
                    break

            missing_ids = expected_ids - found_ids
            if missing_ids:
                logger.warning(
                    "Sanitizing checkpoint: found AIMessage with unfulfilled tool_calls %s. Injecting synthetic ToolMessage(s).",
                    missing_ids,
                )
                for tc in tool_calls:
                    if isinstance(tc, dict) and tc.get("id") in missing_ids:
                        sanitized.append(
                            ToolMessage(
                                tool_call_id=tc["id"],
                                name=tc.get("name", "tool"),
                                content="[Tool execution was interrupted or incomplete. Recovered successfully.]",
                            )
                        )
        i += 1

    return sanitized


class SqliteCheckpointer(BaseCheckpointSaver):
    """
    SQLite-backed checkpoint saver for LangGraph 0.6.x.

    Thread-safe: each calling thread gets its own connection via threading.local.
    Writes are committed immediately after each operation.

    Usage::

        checkpointer = SqliteCheckpointer()
        agent = create_react_agent(llm, tools, checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "conv-123"}}
        result = agent.invoke({"messages": [...]}, config)
    """

    def __init__(self, db_path: str = _DEFAULT_DB, **kwargs):
        super().__init__(**kwargs)
        self.db_path = db_path
        self._init_schema()

    # ─────────────────────────── DB helpers ───────────────────────────

    def _conn(self) -> sqlite3.Connection:
        """Per-thread connection (created lazily)."""
        if not hasattr(_local, "conn") or _local.db_path != self.db_path:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            _local.conn = conn
            _local.db_path = self.db_path
        return _local.conn

    def _init_schema(self) -> None:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        conn.close()
        logger.info("LangGraph SQLite checkpoint schema ready at %s", self.db_path)

    # ─────────────────────────── blob helpers ────────────────────────

    def _load_blobs(
        self,
        thread_id: str,
        checkpoint_ns: str,
        versions: ChannelVersions,
    ) -> dict[str, Any]:
        conn = self._conn()
        channel_values: dict[str, Any] = {}
        for channel, version in versions.items():
            row = conn.execute(
                "SELECT type_tag, value_data FROM lg_blobs "
                "WHERE thread_id=? AND checkpoint_ns=? AND channel=? AND version=?",
                (thread_id, checkpoint_ns, channel, str(version)),
            ).fetchone()
            if row and row[0] != "empty":
                channel_values[channel] = self.serde.loads_typed((row[0], row[1]))
        return channel_values

    # ─────────────────────────── Checkpoint interface ────────────────

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"].get("checkpoint_id")

        conn = self._conn()
        if checkpoint_id:
            row = conn.execute(
                "SELECT checkpoint_data, metadata_data, parent_id "
                "FROM lg_checkpoints "
                "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                (thread_id, checkpoint_ns, checkpoint_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT checkpoint_data, metadata_data, parent_id "
                "FROM lg_checkpoints "
                "WHERE thread_id=? AND checkpoint_ns=? "
                "ORDER BY checkpoint_id DESC LIMIT 1",
                (thread_id, checkpoint_ns),
            ).fetchone()

        if row is None:
            return None

        cp_bytes, meta_bytes, parent_id = row
        checkpoint: Checkpoint = self.serde.loads_typed(("msgpack", cp_bytes))
        metadata: CheckpointMetadata = self.serde.loads_typed(("msgpack", meta_bytes))

        # Load channel values from blobs
        checkpoint["channel_values"] = self._load_blobs(
            thread_id, checkpoint_ns, checkpoint.get("channel_versions", {})
        )
        if "messages" in checkpoint["channel_values"]:
            checkpoint["channel_values"]["messages"] = _sanitize_messages(
                checkpoint["channel_values"]["messages"]
            )

        # Load pending writes
        pending_writes = []
        write_rows = conn.execute(
            "SELECT task_id, channel, value_data FROM lg_writes "
            "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=? "
            "ORDER BY write_idx",
            (thread_id, checkpoint_ns, checkpoint["id"]),
        ).fetchall()
        for task_id, channel, val_bytes in write_rows:
            value = self.serde.loads_typed(("msgpack", val_bytes))
            pending_writes.append((task_id, channel, value))

        actual_checkpoint_id = checkpoint["id"]
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": actual_checkpoint_id,
                }
            },
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": parent_id,
                    }
                }
                if parent_id
                else None
            ),
            pending_writes=pending_writes,
        )

    def list(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        if config is None:
            return

        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")

        query = (
            "SELECT checkpoint_data, metadata_data, parent_id, checkpoint_id "
            "FROM lg_checkpoints "
            "WHERE thread_id=? AND checkpoint_ns=? "
        )
        params: list = [thread_id, checkpoint_ns]

        if before:
            before_id = before["configurable"].get("checkpoint_id", "")
            query += " AND checkpoint_id < ? "
            params.append(before_id)

        query += " ORDER BY checkpoint_id DESC"
        if limit:
            query += f" LIMIT {int(limit)}"

        conn = self._conn()
        rows = conn.execute(query, params).fetchall()
        for cp_bytes, meta_bytes, parent_id, cp_id in rows:
            checkpoint: Checkpoint = self.serde.loads_typed(("msgpack", cp_bytes))
            metadata: CheckpointMetadata = self.serde.loads_typed(("msgpack", meta_bytes))
            checkpoint["channel_values"] = self._load_blobs(
                thread_id, checkpoint_ns, checkpoint.get("channel_versions", {})
            )
            yield CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": cp_id,
                    }
                },
                checkpoint=checkpoint,
                metadata=metadata,
                parent_config=(
                    {
                        "configurable": {
                            "thread_id": thread_id,
                            "checkpoint_ns": checkpoint_ns,
                            "checkpoint_id": parent_id,
                        }
                    }
                    if parent_id
                    else None
                ),
                pending_writes=[],
            )

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        parent_id = config["configurable"].get("checkpoint_id")

        c = checkpoint.copy()
        values: dict[str, Any] = c.pop("channel_values", {})

        conn = self._conn()

        # Persist blobs
        for channel, version in new_versions.items():
            type_tag, val_bytes = (
                self.serde.dumps_typed(values[channel])
                if channel in values
                else ("empty", b"")
            )
            conn.execute(
                "INSERT OR REPLACE INTO lg_blobs "
                "(thread_id, checkpoint_ns, channel, version, type_tag, value_data) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (thread_id, checkpoint_ns, channel, str(version), type_tag, val_bytes),
            )

        cp_type, cp_bytes = self.serde.dumps_typed(c)
        meta_type, meta_bytes = self.serde.dumps_typed(
            get_checkpoint_metadata(config, metadata)
        )

        conn.execute(
            "INSERT OR REPLACE INTO lg_checkpoints "
            "(thread_id, checkpoint_ns, checkpoint_id, checkpoint_data, metadata_data, parent_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (thread_id, checkpoint_ns, checkpoint["id"], cp_bytes, meta_bytes, parent_id),
        )
        conn.commit()

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]

        conn = self._conn()
        for idx, (channel, value) in enumerate(writes):
            write_idx = _WRITES_IDX_MAP.get(channel, idx)
            if write_idx < 0:
                continue  # skip special negative-index writes
            val_type, val_bytes = self.serde.dumps_typed(value)
            conn.execute(
                "INSERT OR IGNORE INTO lg_writes "
                "(thread_id, checkpoint_ns, checkpoint_id, task_id, write_idx, "
                "channel, value_data, task_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    thread_id,
                    checkpoint_ns,
                    checkpoint_id,
                    task_id,
                    write_idx,
                    channel,
                    val_bytes,
                    task_path,
                ),
            )
        conn.commit()

    def delete_thread(self, thread_id: str) -> None:
        conn = self._conn()
        conn.execute("DELETE FROM lg_checkpoints WHERE thread_id=?", (thread_id,))
        conn.execute("DELETE FROM lg_writes WHERE thread_id=?", (thread_id,))
        conn.execute("DELETE FROM lg_blobs WHERE thread_id=?", (thread_id,))
        conn.commit()

    def get_next_version(self, current: Optional[str], channel: Any) -> str:
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            try:
                current_v = int(str(current).split(".")[0])
            except (ValueError, AttributeError):
                current_v = 0
        next_v = current_v + 1
        next_h = random.random()
        return f"{next_v:032}.{next_h:016}"

    # ─────────────────────── Async pass-throughs ─────────────────────
    # The app is synchronous Streamlit; async wrappers delegate to sync.

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        return self.get_tuple(config)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item

    async def aput(self, config, checkpoint, metadata, new_versions):
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return self.put_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        return self.delete_thread(thread_id)

    # ─────────────────────── Context managers ────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass
