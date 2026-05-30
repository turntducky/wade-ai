from __future__ import annotations

import uuid
import json
import sqlite3

from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field

from app.core.config import EPISODES_DB_PATH

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS episodes (
    id         TEXT PRIMARY KEY,
    type       TEXT NOT NULL,
    content    TEXT NOT NULL,
    timestamp  TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    tags       TEXT NOT NULL DEFAULT '[]',
    linked_to  TEXT NOT NULL DEFAULT '[]'
)
"""

@dataclass
class Episode:
    content:    str
    type:       str
    id:         str       = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp:  datetime  = field(default_factory=datetime.now)
    session_id: str       = ""
    tags:       list[str] = field(default_factory=list)
    linked_to:  list[str] = field(default_factory=list)

class EpisodeStore:
    """Thread-safe SQLite store for Episode objects."""
    def __init__(self, db_path: Path = EPISODES_DB_PATH) -> None:
        self._db_path = str(db_path)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(_CREATE_SQL)

    def record(self, episode: Episode) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO episodes "
                "(id, type, content, timestamp, session_id, tags, linked_to) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    episode.id, episode.type, episode.content,
                    episode.timestamp.isoformat(), episode.session_id,
                    json.dumps(episode.tags), json.dumps(episode.linked_to),
                ),
            )

    def query_recent(self, limit: int = 50) -> list[Episode]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT id, type, content, timestamp, session_id, tags, linked_to "
                "FROM episodes ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_episode(r) for r in rows]

    def query_by_session(self, session_id: str) -> list[Episode]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT id, type, content, timestamp, session_id, tags, linked_to "
                "FROM episodes WHERE session_id=? ORDER BY timestamp ASC",
                (session_id,),
            ).fetchall()
        return [_row_to_episode(r) for r in rows]

    def query_temporal(self, since: datetime, until: datetime | None = None) -> list[Episode]:
        if until is None:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT id, type, content, timestamp, session_id, tags, linked_to "
                    "FROM episodes WHERE timestamp >= ? ORDER BY timestamp ASC",
                    (since.isoformat(),),
                ).fetchall()
        else:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT id, type, content, timestamp, session_id, tags, linked_to "
                    "FROM episodes WHERE timestamp >= ? AND timestamp <= ? "
                    "ORDER BY timestamp ASC",
                    (since.isoformat(), until.isoformat()),
                ).fetchall()
        return [_row_to_episode(r) for r in rows]

    def get_by_type(self, episode_type: str, limit: int | None = None) -> list[Episode]:
        sql = (
            "SELECT id, type, content, timestamp, session_id, tags, linked_to "
            "FROM episodes WHERE type=? ORDER BY timestamp DESC"
        )
        args: tuple = (episode_type,)
        if limit is not None:
            sql += " LIMIT ?"
            args = (episode_type, limit)
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(sql, args).fetchall()
        return [_row_to_episode(r) for r in rows]

    def link(self, episode_id: str, linked_ids: list[str]) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT linked_to FROM episodes WHERE id=?", (episode_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"No episode with id={episode_id!r}")
            existing: list[str] = json.loads(row[0])
            merged = list(dict.fromkeys(existing + linked_ids))
            conn.execute(
                "UPDATE episodes SET linked_to=? WHERE id=?",
                (json.dumps(merged), episode_id),
            )

    def delete(self, episode_id: str) -> None:
        """Delete a single episode by ID. Raises KeyError if not found."""
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                "DELETE FROM episodes WHERE id=?", (episode_id,)
            )
            if cur.rowcount == 0:
                raise KeyError(f"No episode with id={episode_id!r}")

    def prune_old(self, max_age_days: int = 30, monitor_age_days: int = 7) -> int:
        """Delete old episodes. monitor_event rows expire faster; daily_summary rows are retained longer."""
        from datetime import timedelta
        cutoff_general = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        cutoff_monitor = (datetime.now() - timedelta(days=monitor_age_days)).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            cur_mon = conn.execute(
                "DELETE FROM episodes WHERE type = 'monitor_event' AND timestamp < ?",
                (cutoff_monitor,),
            )
            cur_gen = conn.execute(
                "DELETE FROM episodes WHERE type != 'monitor_event' "
                "AND json_extract(tags, '$') NOT LIKE '%daily_summary%' "
                "AND timestamp < ?",
                (cutoff_general,),
            )
            deleted = cur_mon.rowcount + cur_gen.rowcount
        return deleted

    def delete_matching(self, query: str, limit: int = 20) -> int:
        """Delete episodes whose content contains any significant word from query.
        Returns count of episodes deleted."""
        keywords = [w.strip() for w in query.split() if len(w.strip()) > 3]
        if not keywords:
            return 0
        clauses = " OR ".join(["content LIKE ?" for _ in keywords])
        params = [f"%{kw}%" for kw in keywords]
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                f"SELECT id FROM episodes WHERE ({clauses}) LIMIT ?",
                params + [limit],
            ).fetchall()
            if not rows:
                return 0
            ids = [r[0] for r in rows]
            placeholders = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM episodes WHERE id IN ({placeholders})", ids)
            return len(ids)

def _row_to_episode(row: tuple) -> Episode:
    return Episode(
        id=row[0], type=row[1], content=row[2],
        timestamp=datetime.fromisoformat(row[3]),
        session_id=row[4],
        tags=json.loads(row[5]),
        linked_to=json.loads(row[6]),
    )

_episode_store: "EpisodeStore | None" = None

def get_episode_store() -> "EpisodeStore":
    """Module-level accessor for the singleton EpisodeStore instance. Initializes the store on first access."""
    global _episode_store
    if _episode_store is None:
        _episode_store = EpisodeStore()
    return _episode_store