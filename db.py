"""SQLite: очередь исходных сообщений и очередь постов."""
import json
import os
import sqlite3
import threading
import time
import uuid

import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        os.makedirs(config.MEDIA_DIR, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA busy_timeout=5000")
    return _conn


def init() -> None:
    c = _connect()
    with _lock:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS raw_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id     TEXT NOT NULL,
                msg_id      INTEGER NOT NULL,
                group_key   TEXT NOT NULL,
                text        TEXT DEFAULT '',
                media_path  TEXT,
                created_at  REAL NOT NULL,
                consumed    INTEGER NOT NULL DEFAULT 0,
                UNIQUE(chat_id, msg_id)
            );

            CREATE TABLE IF NOT EXISTS posts (
                id             TEXT PRIMARY KEY,
                chat_id        TEXT NOT NULL,
                group_key      TEXT NOT NULL,
                first_msg_id   INTEGER NOT NULL,
                source_text    TEXT DEFAULT '',
                media_path     TEXT,
                tweet_text     TEXT,
                status         TEXT NOT NULL,
                attempts       INTEGER NOT NULL DEFAULT 0,
                error          TEXT,
                tweet_id       TEXT,
                review_msg_id  INTEGER,
                publish_after  REAL NOT NULL DEFAULT 0,
                created_at     REAL NOT NULL,
                updated_at     REAL NOT NULL,
                UNIQUE(chat_id, group_key)
            );

            CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
            CREATE INDEX IF NOT EXISTS idx_raw_consumed ON raw_messages(consumed);
            """
        )
        c.commit()
    _migrate()


def _migrate() -> None:
    """Добавляет недостающие колонки в уже существующую БД на Volume."""
    c = _connect()
    with _lock:
        for table, column, ddl in [
            ("posts", "tweet_id", "ALTER TABLE posts ADD COLUMN tweet_id TEXT"),
            ("posts", "review_msg_id", "ALTER TABLE posts ADD COLUMN review_msg_id INTEGER"),
            ("posts", "publish_after", "ALTER TABLE posts ADD COLUMN publish_after REAL NOT NULL DEFAULT 0"),
        ]:
            cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                c.execute(ddl)
        c.commit()


# --- raw_messages ---

def add_raw(chat_id: str, msg_id: int, group_key: str, text: str, media_path: str | None) -> bool:
    c = _connect()
    with _lock:
        try:
            c.execute(
                "INSERT INTO raw_messages (chat_id, msg_id, group_key, text, media_path, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (chat_id, msg_id, group_key, text or "", media_path, time.time()),
            )
            c.commit()
            return True
        except sqlite3.IntegrityError:
            return False  # уже видели это сообщение


def ready_groups(wait_seconds: int) -> list[tuple[str, str]]:
    """Группы, у которых последнее сообщение старше wait_seconds."""
    c = _connect()
    cutoff = time.time() - wait_seconds
    rows = c.execute(
        "SELECT chat_id, group_key, MAX(created_at) AS last_at FROM raw_messages"
        " WHERE consumed = 0 GROUP BY chat_id, group_key HAVING last_at <= ?",
        (cutoff,),
    ).fetchall()
    return [(r["chat_id"], r["group_key"]) for r in rows]


def take_group(chat_id: str, group_key: str) -> list[sqlite3.Row]:
    c = _connect()
    rows = c.execute(
        "SELECT * FROM raw_messages WHERE chat_id=? AND group_key=? AND consumed=0 ORDER BY msg_id",
        (chat_id, group_key),
    ).fetchall()
    with _lock:
        c.execute(
            "UPDATE raw_messages SET consumed=1 WHERE chat_id=? AND group_key=?",
            (chat_id, group_key),
        )
        c.commit()
    return rows


# --- posts ---

def create_post(chat_id: str, group_key: str, first_msg_id: int, text: str, media_path: str | None,
                status: str = "pending") -> str | None:
    c = _connect()
    pid = uuid.uuid4().hex
    now = time.time()
    with _lock:
        try:
            c.execute(
                "INSERT INTO posts (id, chat_id, group_key, first_msg_id, source_text, media_path,"
                " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (pid, chat_id, group_key, first_msg_id, text, media_path, status, now, now),
            )
            c.commit()
            return pid
        except sqlite3.IntegrityError:
            return None


def get_post(pid: str) -> sqlite3.Row | None:
    return _connect().execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()


def get_post_by_review_msg(msg_id: int) -> sqlite3.Row | None:
    return _connect().execute(
        "SELECT * FROM posts WHERE review_msg_id=? ORDER BY created_at DESC LIMIT 1", (msg_id,)
    ).fetchone()


def posts_by_status(status: str, limit: int = 20) -> list[sqlite3.Row]:
    return _connect().execute(
        "SELECT * FROM posts WHERE status=? AND attempts < ? ORDER BY created_at LIMIT ?",
        (status, config.MAX_ATTEMPTS, limit),
    ).fetchall()


def update_post(pid: str, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = time.time()
    sets = ", ".join(f"{k}=?" for k in fields)
    c = _connect()
    with _lock:
        c.execute(f"UPDATE posts SET {sets} WHERE id=?", (*fields.values(), pid))
        c.commit()


def bump_attempt(pid: str, error: str) -> None:
    c = _connect()
    with _lock:
        c.execute(
            "UPDATE posts SET attempts = attempts + 1, error = ?, updated_at = ? WHERE id=?",
            (error[:500], time.time(), pid),
        )
        c.commit()


def stats() -> dict:
    c = _connect()
    rows = c.execute("SELECT status, COUNT(*) AS n FROM posts GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


def recent(limit: int = 20) -> list[dict]:
    c = _connect()
    rows = c.execute("SELECT * FROM posts ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "status": r["status"],
            "msg_id": r["first_msg_id"],
            "has_media": bool(r["media_path"]),
            "tweet_text": r["tweet_text"],
            "tweet_id": r["tweet_id"],
            "attempts": r["attempts"],
            "error": r["error"],
            "source_preview": (r["source_text"] or "")[:120],
        })
    return out
