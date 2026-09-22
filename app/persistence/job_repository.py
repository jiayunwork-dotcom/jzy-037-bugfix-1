"""核算作业与工况点持久化。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.persistence.database import Database


class JobRepository:
    def __init__(self, db: Database):
        self._db = db

    def insert_job(
        self, *, id: str, name: str, property_set_id: str, created_at: str
    ) -> None:
        with self._db.lock:
            self._db.conn.execute(
                "INSERT INTO jobs (id, name, property_set_id, created_at) VALUES (?,?,?,?)",
                (id, name, property_set_id, created_at),
            )
            self._db.conn.commit()

    def insert_point(
        self,
        *,
        job_id: str,
        point_index: int,
        label: str,
        temperature: float,
        pressure: float,
        feed: list[float],
        psat: list[float] | None,
    ) -> None:
        with self._db.lock:
            self._db.conn.execute(
                """INSERT INTO job_points
                   (job_id, point_index, label, temperature, pressure,
                    feed_json, psat_json, status)
                   VALUES (?,?,?,?,?,?,?, 'pending')""",
                (
                    job_id,
                    point_index,
                    label,
                    temperature,
                    pressure,
                    json.dumps(feed),
                    json.dumps(psat) if psat is not None else None,
                ),
            )
            self._db.conn.commit()

    def save_point_result(
        self,
        *,
        job_id: str,
        point_index: int,
        result: dict[str, Any],
        computed_at: str,
    ) -> None:
        with self._db.lock:
            self._db.conn.execute(
                """UPDATE job_points
                   SET result_json=?, status='done', error_json=NULL, computed_at=?
                   WHERE job_id=? AND point_index=?""",
                (json.dumps(result, ensure_ascii=False), computed_at, job_id, point_index),
            )
            self._db.conn.commit()

    def get_job_row(self, job_id: str) -> sqlite3.Row | None:
        with self._db.lock:
            return self._db.conn.execute(
                "SELECT * FROM jobs WHERE id=?", (job_id,)
            ).fetchone()

    def get_point_rows(self, job_id: str) -> list[sqlite3.Row]:
        with self._db.lock:
            return list(
                self._db.conn.execute(
                    "SELECT * FROM job_points WHERE job_id=? ORDER BY point_index",
                    (job_id,),
                ).fetchall()
            )

    def get_point_row(self, job_id: str, point_index: int) -> sqlite3.Row | None:
        with self._db.lock:
            return self._db.conn.execute(
                "SELECT * FROM job_points WHERE job_id=? AND point_index=?",
                (job_id, point_index),
            ).fetchone()

    def list_jobs(self) -> list[sqlite3.Row]:
        with self._db.lock:
            return list(
                self._db.conn.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC, id"
                ).fetchall()
            )


def point_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "index": row["point_index"],
        "label": row["label"],
        "temperature": row["temperature"],
        "pressure": row["pressure"],
        "feed": json.loads(row["feed_json"]),
        "psat_override": json.loads(row["psat_json"]) if row["psat_json"] is not None else None,
        "status": row["status"],
        "result": json.loads(row["result_json"]) if row["result_json"] is not None else None,
        "error": json.loads(row["error_json"]) if row["error_json"] is not None else None,
        "computed_at": row["computed_at"],
    }
