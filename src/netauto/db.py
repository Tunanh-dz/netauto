"""Lưu snapshot và sự kiện vào SQLite (thư viện có sẵn của Python)."""
import json
import sqlite3
from pathlib import Path

DB_PATH = "data/netauto.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at  TEXT    NOT NULL,
    devices   INTEGER NOT NULL,
    reachable INTEGER NOT NULL,
    errors    INTEGER NOT NULL,
    data      TEXT    NOT NULL      -- toàn bộ snapshot dạng JSON
);
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    at          TEXT    NOT NULL,
    node        TEXT    NOT NULL,
    kind        TEXT    NOT NULL,   -- 'mất' hoặc 'mới'
    fact        TEXT    NOT NULL
);
"""


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def save_snapshot(conn, data: dict) -> int:
    """Lưu một snapshot (dạng dict), trả về id."""
    devices = data["devices"]
    cur = conn.execute(
        "INSERT INTO snapshots (taken_at, devices, reachable, errors, data) "
        "VALUES (?, ?, ?, ?, ?)",
        (data["taken_at"], len(devices),
         sum(1 for d in devices if d["reachable"]),
         sum(len(d["errors"]) for d in devices),
         json.dumps(data, ensure_ascii=False)),
    )
    conn.commit()
    return cur.lastrowid


def save_events(conn, snapshot_id: int, at: str, node: str, change: dict) -> None:
    for kind in ("mất", "mới"):
        for fact in change[kind]:
            conn.execute(
                "INSERT INTO events (snapshot_id, at, node, kind, fact) "
                "VALUES (?, ?, ?, ?, ?)",
                (snapshot_id, at, node, kind, fact))
    conn.commit()


def latest_snapshot(conn):
    row = conn.execute("SELECT data FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    return json.loads(row[0]) if row else None


def list_snapshots(conn, limit: int = 10):
    return conn.execute(
        "SELECT id, taken_at, devices, reachable, errors FROM snapshots "
        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def list_events(conn, limit: int = 30):
    return conn.execute(
        "SELECT at, node, kind, fact FROM events ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()
