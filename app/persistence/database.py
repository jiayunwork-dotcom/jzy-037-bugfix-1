"""SQLite 连接管理。

选择 SQLite + WAL 作为持久化存储：物性定义与作业结果落盘，进程重启后仍可
按作业号/物性号取回。全部写操作在服务层经同一把进程锁串行化，保证并发提交
作业时彼此不串号、不覆盖。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS property_sets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,                 -- antoine | direct
    components_json TEXT NOT NULL,        -- [{"name","antoine": [A,B,C]|null}]
    psat_json TEXT,                       -- direct 模式的固定蒸汽压(声明单位)
    pressure_unit TEXT NOT NULL,
    temperature_unit TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    builtin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    property_set_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (property_set_id) REFERENCES property_sets(id)
);

CREATE TABLE IF NOT EXISTS job_points (
    job_id TEXT NOT NULL,
    point_index INTEGER NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    temperature REAL NOT NULL,
    pressure REAL NOT NULL,
    feed_json TEXT NOT NULL,
    psat_json TEXT,                        -- 本点覆盖用蒸汽压(声明单位) 或 null
    result_json TEXT,                      -- 求解成功后的完整结果
    status TEXT NOT NULL DEFAULT 'pending', -- done | failed
    error_json TEXT,
    computed_at TEXT,
    PRIMARY KEY (job_id, point_index),
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
"""


class Database:
    """单个 SQLite 连接 + 进程级写锁。check_same_thread=False 由写锁保证安全。"""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        with self._lock:
            self._conn.close()
