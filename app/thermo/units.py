"""单位换算。

物性定义自带 ``pressure_unit`` / ``temperature_unit``，存储与计算统一使用
规范单位（kPa、K），避免 Antoine 蒸汽压与系统压力因单位不一致而错位。
"""

from __future__ import annotations

from app.errors import PropertyValidationError

PRESSURE_UNITS = ("kPa", "bar", "MPa", "atm", "mmHg", "Pa")
TEMPERATURE_UNITS = ("K", "C")

# 统一换算到 kPa
_PRESSURE_TO_KPA: dict[str, float] = {
    "kPa": 1.0,
    "Pa": 1.0e-3,
    "bar": 100.0,
    "MPa": 1.0e3,
    "atm": 101.325,
    "mmHg": 0.13332236842105263,
}


def pressure_to_kpa(value: float, unit: str) -> float:
    try:
        factor = _PRESSURE_TO_KPA[unit]
    except KeyError as exc:  # pragma: no cover - 入口已枚举校验
        raise PropertyValidationError(
            f"不支持的压力单位: {unit!r}，可选 {PRESSURE_UNITS}"
        ) from exc
    return value * factor


def temperature_to_kelvin(value: float, unit: str) -> float:
    if unit == "K":
        return value
    if unit == "C":
        return value + 273.15
    raise PropertyValidationError(  # pragma: no cover - 入口已枚举校验
        f"不支持的温度单位: {unit!r}，可选 {TEMPERATURE_UNITS}"
    )
