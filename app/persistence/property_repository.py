"""物性定义持久化。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.persistence.database import Database


class PropertyRepository:
    def __init__(self, db: Database):
        self._db = db

    def insert(
        self,
        *,
        id: str,
        name: str,
        source: str,
        components: list[dict[str, Any]],
        psat: list[float] | None,
        pressure_unit: str,
        temperature_unit: str,
        description: str,
        builtin: bool,
        created_at: str,
    ) -> None:
        with self._db.lock:
            try:
                self._db.conn.execute(
                    """INSERT INTO property_sets
                       (id, name, source, components_json, psat_json,
                        pressure_unit, temperature_unit, description, builtin, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        id,
                        name,
                        source,
                        json.dumps(components, ensure_ascii=False),
                        json.dumps(psat) if psat is not None else None,
                        pressure_unit,
                        temperature_unit,
                        description,
                        1 if builtin else 0,
                        created_at,
                    ),
                )
                self._db.conn.commit()
            except sqlite3.IntegrityError as exc:
                self._db.conn.rollback()
                raise ValueError(f"物性名称冲突: {name}") from exc

    def get(self, id: str) -> dict[str, Any] | None:
        with self._db.lock:
            row = self._db.conn.execute(
                "SELECT * FROM property_sets WHERE id=?", (id,)
            ).fetchone()
        return _row_to_dict(row) if row is not None else None

    def get_by_name(self, name: str) -> dict[str, Any] | None:
        with self._db.lock:
            row = self._db.conn.execute(
                "SELECT * FROM property_sets WHERE name=?", (name,)
            ).fetchone()
        return _row_to_dict(row) if row is not None else None

    def list_all(self) -> list[dict[str, Any]]:
        with self._db.lock:
            rows = self._db.conn.execute(
                "SELECT * FROM property_sets ORDER BY builtin DESC, created_at, name"
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "source": row["source"],
        "components": json.loads(row["components_json"]),
        "psat": json.loads(row["psat_json"]) if row["psat_json"] is not None else None,
        "pressure_unit": row["pressure_unit"],
        "temperature_unit": row["temperature_unit"],
        "description": row["description"],
        "builtin": bool(row["builtin"]),
        "created_at": row["created_at"],
    }
