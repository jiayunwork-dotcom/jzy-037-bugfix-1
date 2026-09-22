"""物性定义的校验、登记与复用。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.errors import (
    PropertyNameConflictError,
    PropertyNotFoundError,
    PropertyValidationError,
)
from app.persistence.property_repository import PropertyRepository
from app.schemas import PropertySetCreate
from app.thermo.units import PRESSURE_UNITS, TEMPERATURE_UNITS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _determine_source(payload: PropertySetCreate) -> tuple[str, list[dict[str, Any]]]:
    """确定物性来源：Antoine 或直给蒸汽压；二者必须全组分一致。

    组分顺序在本函数冻结为 components 列表的 [0]/[1] 下标，
    下游蒸汽压、K 值、进料、液汽组成全部沿用这一份下标。
    """

    components = []
    antoine_flags = []
    for comp in payload.components:
        antoine_flags.append(comp.antoine is not None)
        components.append(
            {
                "name": comp.name,
                "antoine": (
                    [comp.antoine.a, comp.antoine.b, comp.antoine.c]
                    if comp.antoine is not None
                    else None
                ),
            }
        )

    errors: list[dict[str, Any]] = []

    if all(antoine_flags):
        source = "antoine"
        psat = payload.psat
        if psat is not None:
            # antoine 组也允许给一个可被覆盖的缺省值？为避免歧义，这里直接拒绝
            errors.append(
                {
                    "type": "PROPERTY_SOURCE_AMBIGUOUS",
                    "message": "两组分均提供 Antoine 系数时不允许再给顶层 psat，"
                    "请改用作业点级 psat 覆盖",
                }
            )
    elif not any(antoine_flags):
        source = "direct"
        psat = payload.psat
        if psat is None:
            errors.append(
                {
                    "type": "MISSING_SATURATION_PRESSURE",
                    "message": "直给模式下必须提供两组分的饱和蒸汽压 psat",
                }
            )
        elif any((not isinstance(p, (int, float))) or p <= 0 for p in psat):
            errors.append(
                {
                    "type": "NON_POSITIVE_SATURATION_PRESSURE",
                    "message": "直给的饱和蒸汽压必须均为正数",
                    "psat": psat,
                }
            )
    else:
        source = "invalid"
        errors.append(
            {
                "type": "MIXED_PROPERTY_SOURCE",
                "message": "两个组分的物性来源必须一致：同时给 Antoine 系数，或都不给并直给 psat；"
                "不允许一个组分用 Antoine、另一个直给",
            }
        )

    if payload.pressure_unit not in PRESSURE_UNITS:
        errors.append(
            {"type": "UNSUPPORTED_UNIT", "message": f"未知压力单位 {payload.pressure_unit!r}"}
        )
    if payload.temperature_unit not in TEMPERATURE_UNITS:
        errors.append(
            {"type": "UNSUPPORTED_UNIT", "message": f"未知温度单位 {payload.temperature_unit!r}"}
        )

    if errors:
        raise PropertyValidationError(
            "物性定义校验失败", details={"errors": errors}
        )
    return source, components, psat  # type: ignore[return-value]


class PropertyService:
    def __init__(self, repo: PropertyRepository):
        self._repo = repo

    def register(
        self, payload: PropertySetCreate, *, builtin: bool = False, fixed_id: str | None = None
    ) -> dict[str, Any]:
        source, components, psat = _determine_source(payload)
        if self._repo.get_by_name(payload.name) is not None:
            raise PropertyNameConflictError(
                f"已存在同名物性定义: {payload.name!r}",
                details={"name": payload.name},
            )
        record = {
            "id": fixed_id or f"ps-{uuid.uuid4().hex[:12]}",
            "name": payload.name,
            "source": source,
            "components": components,
            "psat": psat,
            "pressure_unit": payload.pressure_unit,
            "temperature_unit": payload.temperature_unit,
            "description": payload.description,
            "builtin": builtin,
            "created_at": _now(),
        }
        self._repo.insert(**record)
        return record

    def get(self, property_set_id: str) -> dict[str, Any]:
        record = self._repo.get(property_set_id)
        if record is None:
            raise PropertyNotFoundError(
                f"物性定义不存在: {property_set_id!r}",
                details={"property_set_id": property_set_id},
            )
        return record

    def list_all(self) -> list[dict[str, Any]]:
        return self._repo.list_all()

    @staticmethod
    def serialize(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": record["id"],
            "name": record["name"],
            "source": record["source"],
            "components": [
                {
                    "name": c["name"],
                    "antoine": (
                        {"a": c["antoine"][0], "b": c["antoine"][1], "c": c["antoine"][2]}
                        if c["antoine"] is not None
                        else None
                    ),
                }
                for c in record["components"]
            ],
            "psat": record["psat"],
            "pressure_unit": record["pressure_unit"],
            "temperature_unit": record["temperature_unit"],
            "description": record["description"],
            "builtin": record["builtin"],
            "created_at": record["created_at"],
        }
