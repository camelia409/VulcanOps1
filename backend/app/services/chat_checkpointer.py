"""
Custom LangGraph checkpointer backed by our existing SQLAlchemy/asyncpg connection.

Replaces AsyncPostgresSaver (which requires psycopg v3 / libpq, unavailable on Windows)
with a checkpointer that reuses the same asyncpg pool our ORM already manages.

Storage layout
--------------
  chat_checkpoints       — one row per checkpoint (full state blob)
  chat_checkpoint_writes — pending writes between nodes (used by LangGraph internals)

The checkpoint blob includes channel_values inline (no separate blob table).
"""

from __future__ import annotations

import random
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Iterator, Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal


_WRITES_IDX_SPECIAL: dict[str, int] = {
    "__error__": -1,
}


class VulcanOpsCheckpointer(BaseCheckpointSaver):
    """
    AsyncPostgresSaver-compatible checkpointer using our existing SQLAlchemy pool.

    Call await checkpointer.setup() once on startup to create the required tables.
    """

    serde = JsonPlusSerializer()

    # ------------------------------------------------------------------ setup

    async def setup(self) -> None:
        """Create the two checkpoint tables if they do not exist."""
        async with AsyncSessionLocal() as db:
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS chat_checkpoints (
                    thread_id        TEXT    NOT NULL,
                    checkpoint_ns    TEXT    NOT NULL DEFAULT '',
                    checkpoint_id    TEXT    NOT NULL,
                    parent_checkpoint_id TEXT,
                    type             TEXT    NOT NULL,
                    data             BYTEA   NOT NULL,
                    meta_type        TEXT    NOT NULL,
                    meta_data        BYTEA   NOT NULL,
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
                )
            """))
            await db.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_chat_checkpoints_thread_ns
                    ON chat_checkpoints (thread_id, checkpoint_ns)
            """))
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS chat_checkpoint_writes (
                    thread_id        TEXT    NOT NULL,
                    checkpoint_ns    TEXT    NOT NULL DEFAULT '',
                    checkpoint_id    TEXT    NOT NULL,
                    task_id          TEXT    NOT NULL,
                    task_path        TEXT    NOT NULL DEFAULT '',
                    idx              INTEGER NOT NULL,
                    channel          TEXT    NOT NULL,
                    type             TEXT    NOT NULL,
                    data             BYTEA   NOT NULL,
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
                )
            """))
            await db.commit()

    # ------------------------------------------------------------------ helpers

    def _cfg(self, config: RunnableConfig) -> tuple[str, str, str | None]:
        c = config["configurable"]
        return (
            c["thread_id"],
            c.get("checkpoint_ns", ""),
            c.get("checkpoint_id"),
        )

    def _load_writes(self, rows: list) -> list[tuple[str, str, Any]]:
        return [
            (task_id, channel, self.serde.loads_typed((typ, data)))
            for task_id, channel, typ, data in rows
        ]

    # ------------------------------------------------------------------ get_next_version

    def get_next_version(self, current: str | None, channel: None) -> str:
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(current.split(".")[0])
        next_v = current_v + 1
        return f"{next_v:032}.{random.random():016}"

    # ------------------------------------------------------------------ sync stubs (required by ABC)

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:  # type: ignore[override]
        raise NotImplementedError("Use aget_tuple")

    def list(self, config: RunnableConfig | None, **kwargs: Any) -> Iterator[CheckpointTuple]:
        raise NotImplementedError("Use alist")

    def put(self, config: RunnableConfig, checkpoint: Checkpoint,
            metadata: CheckpointMetadata, new_versions: ChannelVersions) -> RunnableConfig:
        raise NotImplementedError("Use aput")

    def put_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]],
                   task_id: str, task_path: str = "") -> None:
        raise NotImplementedError("Use aput_writes")

    # ------------------------------------------------------------------ aget_tuple

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id, checkpoint_ns, checkpoint_id = self._cfg(config)

        async with AsyncSessionLocal() as db:
            if checkpoint_id:
                row = (await db.execute(text("""
                    SELECT checkpoint_id, parent_checkpoint_id, type, data, meta_type, meta_data
                    FROM chat_checkpoints
                    WHERE thread_id = :tid AND checkpoint_ns = :ns AND checkpoint_id = :cid
                """), {"tid": thread_id, "ns": checkpoint_ns, "cid": checkpoint_id})).fetchone()
            else:
                row = (await db.execute(text("""
                    SELECT checkpoint_id, parent_checkpoint_id, type, data, meta_type, meta_data
                    FROM chat_checkpoints
                    WHERE thread_id = :tid AND checkpoint_ns = :ns
                    ORDER BY checkpoint_id DESC
                    LIMIT 1
                """), {"tid": thread_id, "ns": checkpoint_ns})).fetchone()

            if row is None:
                return None

            ckpt_id, parent_id, typ, data, meta_type, meta_data = row

            writes_rows = (await db.execute(text("""
                SELECT task_id, channel, type, data
                FROM chat_checkpoint_writes
                WHERE thread_id = :tid AND checkpoint_ns = :ns AND checkpoint_id = :cid
                ORDER BY idx
            """), {"tid": thread_id, "ns": checkpoint_ns, "cid": ckpt_id})).fetchall()

        restored_config: RunnableConfig = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": ckpt_id,
            }
        }
        return CheckpointTuple(
            config=restored_config,
            checkpoint=self.serde.loads_typed((typ, bytes(data))),
            metadata=self.serde.loads_typed((meta_type, bytes(meta_data))),
            pending_writes=self._load_writes(writes_rows),
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
        )

    # ------------------------------------------------------------------ alist

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        if config is None:
            return

        thread_id, checkpoint_ns, _ = self._cfg(config)
        before_id = get_checkpoint_id(before) if before else None

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(text("""
                SELECT checkpoint_id, parent_checkpoint_id, type, data, meta_type, meta_data
                FROM chat_checkpoints
                WHERE thread_id = :tid AND checkpoint_ns = :ns
                ORDER BY checkpoint_id DESC
            """), {"tid": thread_id, "ns": checkpoint_ns})).fetchall()

        count = 0
        for ckpt_id, parent_id, typ, data, meta_type, meta_data in rows:
            if before_id and ckpt_id >= before_id:
                continue
            metadata = self.serde.loads_typed((meta_type, bytes(meta_data)))
            if filter and not all(metadata.get(k) == v for k, v in filter.items()):
                continue
            yield CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": ckpt_id,
                    }
                },
                checkpoint=self.serde.loads_typed((typ, bytes(data))),
                metadata=metadata,
                pending_writes=None,
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
            )
            count += 1
            if limit and count >= limit:
                break

    # ------------------------------------------------------------------ aput

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id, checkpoint_ns, parent_id = self._cfg(config)
        ckpt_id = checkpoint["id"]

        typ, data = self.serde.dumps_typed(checkpoint)
        full_meta = get_checkpoint_metadata(config, metadata)
        meta_type, meta_data = self.serde.dumps_typed(full_meta)

        async with AsyncSessionLocal() as db:
            await db.execute(text("""
                INSERT INTO chat_checkpoints
                    (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
                     type, data, meta_type, meta_data)
                VALUES
                    (:tid, :ns, :cid, :pid, :typ, :data, :mtyp, :mdata)
                ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id)
                DO UPDATE SET
                    parent_checkpoint_id = EXCLUDED.parent_checkpoint_id,
                    type     = EXCLUDED.type,
                    data     = EXCLUDED.data,
                    meta_type = EXCLUDED.meta_type,
                    meta_data = EXCLUDED.meta_data
            """), {
                "tid": thread_id, "ns": checkpoint_ns, "cid": ckpt_id,
                "pid": parent_id, "typ": typ, "data": data,
                "mtyp": meta_type, "mdata": meta_data,
            })
            await db.commit()

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": ckpt_id,
            }
        }

    # ------------------------------------------------------------------ aput_writes

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id, checkpoint_ns, checkpoint_id = self._cfg(config)
        if not checkpoint_id:
            return

        async with AsyncSessionLocal() as db:
            for idx, (channel, value) in enumerate(writes):
                real_idx = _WRITES_IDX_SPECIAL.get(channel, idx)
                typ, data = self.serde.dumps_typed(value)
                await db.execute(text("""
                    INSERT INTO chat_checkpoint_writes
                        (thread_id, checkpoint_ns, checkpoint_id, task_id, task_path,
                         idx, channel, type, data)
                    VALUES
                        (:tid, :ns, :cid, :task_id, :task_path, :idx, :ch, :typ, :data)
                    ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
                    DO NOTHING
                """), {
                    "tid": thread_id, "ns": checkpoint_ns, "cid": checkpoint_id,
                    "task_id": task_id, "task_path": task_path, "idx": real_idx,
                    "ch": channel, "typ": typ, "data": data,
                })
            await db.commit()
