"""HTTP 请求/响应的 Pydantic 模型。

业务校验（正数、组成求和、Antoine 分母等）在 services 层做，
以便返回带类型、逐点归位的中文错误；这里只约束形状、基础类型与有限性。
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.thermo.units import PRESSURE_UNITS, TEMPERATURE_UNITS

PropertySource = Literal["antoine", "direct"]
Regime = Literal["two_phase", "subcooled_liquid", "superheated_vapor"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", check_fields=False)
    @classmethod
    def _reject_non_finite(cls, v):
        """递归拦截任何层级上的 NaN/Infinity（含列表元素）。"""

        def walk(x, info):
            if isinstance(x, float):
                if not math.isfinite(x):
                    raise ValueError("数值必须为有限实数（不允许 NaN/Infinity）")
            elif isinstance(x, (list, tuple)):
                for item in x:
                    walk(item, info)
            return x

        return walk(v, None)


# ---------------- 物性定义 ----------------
class AntoineIn(StrictModel):
    a: float = Field(description="Antoine A 系数，ln(P) = A - B/(T+C)")
    b: float = Field(description="Antoine B 系数（与温度同单位）")
    c: float = Field(description="Antoine C 系数（与温度单位的温标绑定）")


class ComponentIn(StrictModel):
    name: str = Field(min_length=1, max_length=64)
    antoine: AntoineIn | None = Field(default=None, description="Antoine 三系数；不给定则需走 direct 直给蒸汽压")


class PropertySetCreate(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    components: list[ComponentIn] = Field(min_length=2, max_length=2)
    # direct 模式下两组分固定直给的饱和蒸汽压（按 components 下标对齐）
    psat: list[float] | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description="直给模式：两组分的固定饱和蒸汽压，顺序与 components 一致",
    )
    pressure_unit: Literal["kPa", "bar", "MPa", "atm", "mmHg", "Pa"] = "kPa"
    temperature_unit: Literal["K", "C"] = "K"
    description: str = ""


class AntoineOut(StrictModel):
    a: float
    b: float
    c: float


class ComponentOut(StrictModel):
    name: str
    antoine: AntoineOut | None = None


class PropertySetOut(StrictModel):
    id: str
    name: str
    source: PropertySource
    components: list[ComponentOut]
    psat: list[float] | None = None
    pressure_unit: str
    temperature_unit: str
    description: str
    builtin: bool
    created_at: str


# ---------------- 作业 ----------------
class PointIn(StrictModel):
    temperature: float
    pressure: float
    feed: list[float] = Field(min_length=2, max_length=2, description="进料总组成 z0,z1，按组分下标")
    psat: list[float] | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description="可选：本点直接给定两组分饱和蒸汽压（覆盖 Antoine/固定值）",
    )
    label: str = ""


class JobCreate(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    # 二选一：引用已登记物性，或随作业临时给出（服务会登记并复用）
    property_set_id: str | None = None
    property_set: PropertySetCreate | None = None
    points: list[PointIn] = Field(min_length=1)


class SolverInfo(StrictModel):
    method: str
    bracket_tol: float
    residual_tol: float
    max_iters: float
    iterations: int | None = None
    rr_residual: float | None = None
    g0: float
    g1: float


class PointResult(StrictModel):
    index: int
    label: str
    temperature: float
    pressure: float
    feed: list[float]
    is_two_phase: bool
    regime: Regime
    reason_code: Literal["TWO_PHASE_ROOT", "BELOW_BUBBLE_POINT", "ABOVE_DEW_POINT"]
    reason: str
    beta: float | None = Field(description="汽化率；单相时为 null")
    liquid: list[float] | None = Field(description="液相摩尔组成 x；汽相单相时为 null")
    vapor: list[float] | None = Field(description="汽相摩尔组成 y；液相单相时为 null")
    K: list[float] = Field(description="各组分平衡常数，下标与组分定义一致")
    psat: list[float] = Field(description="本点实际使用的两组分饱和蒸汽压(kPa)")
    temperature_k: float
    pressure_kpa: float
    solver: SolverInfo
    computed_at: str


class JobOut(StrictModel):
    id: str
    name: str
    property_set_id: str
    points: list[PointResult]
    created_at: str


class ErrorBody(StrictModel):
    type: str
    message: str
    details: dict | None = None


class ErrorResponse(StrictModel):
    error: ErrorBody


# 供 OpenAPI / 文档引用
PRESSURE_UNIT_CHOICES = PRESSURE_UNITS
TEMPERATURE_UNIT_CHOICES = TEMPERATURE_UNITS
