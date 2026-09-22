"""Antoine 饱和蒸汽压（自然对数形式）与汽液平衡常数 K_i。

Antoine 关系采用自然对数形式：

    ln(P_i^sat) = A_i - B_i / (T + C_i)

其中温度/压力单位由物性定义的 ``temperature_unit`` / ``pressure_unit`` 声明，
转换到规范单位（K、kPa）后再求值。

平衡常数定义（理想溶液 + 理想汽相，Raoult 定律）：

    K_i = P_i^sat(T) / P

**组分下标约定（全服务唯一一份）**：组分 0、1 的顺序以物性定义
``components`` 的列表顺序为准；Antoine 系数、直接给定的饱和蒸汽压、
进料组成、液/汽相组成一律按下标 0、1 对齐。闪蒸求解与蒸汽压计算
共用本模块的 K 值，不允许各自维护互相矛盾的组分顺序。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.errors import PropertyValidationError
from app.thermo.units import pressure_to_kpa, temperature_to_kelvin

# 分母绝对值小于该阈值视为 Antoine 退化（接近极点），拒绝计算
_ANTOINE_DENOM_MIN = 1.0e-6


@dataclass(frozen=True)
class AntoineCoefficients:
    a: float
    b: float
    c: float


def antoine_psat_kpa(
    coeff: AntoineCoefficients,
    temperature: float,
    temperature_unit: str,
    pressure_unit: str,
) -> float:
    """按自然对数 Antoine 关系计算单组分饱和蒸汽压，返回 kPa。

    温度按 ``temperature_unit`` 解释（Antoine 的 C 系数与该温标绑定），
    先转成规范温度（K）代入 ``T_K + C'``，结果按 ``pressure_unit`` 反算。

    为了让调用方在同一温标下登记，C 系数与输入温度使用同一温标：
    内部统一转到 K，因此登记的 C 必须是与所声明温标一致的常数。
    """

    if not all(math.isfinite(v) for v in (coeff.a, coeff.b, coeff.c)):
        raise PropertyValidationError("Antoine 系数必须为有限实数")

    t_ref = temperature_to_kelvin(temperature, temperature_unit)
    # C 与声明温标绑定：摄氏度的 C 需平移到 Kelvin 平移量
    c_shifted = coeff.c if temperature_unit == "K" else coeff.c - 273.15
    denom = t_ref + c_shifted
    if abs(denom) < _ANTOINE_DENOM_MIN:
        raise PropertyValidationError(
            f"Antoine 分母 T + C 在 T={temperature:g} {temperature_unit} 处接近零"
            f"（|T+C|={abs(denom):.3e}），该系数无法在此温度使用",
            details={"denominator": denom},
        )

    ln_p_in_declared_unit = coeff.a - coeff.b / denom
    # ln(P) 的单位为声明压力单位，换算到 kPa
    ln_p_kpa = ln_p_in_declared_unit + math.log(
        pressure_to_kpa(1.0, pressure_unit)
    )
    p_sat = math.exp(ln_p_kpa)
    if not math.isfinite(p_sat):
        raise PropertyValidationError(
            "Antoine 计算得到非有限的饱和蒸汽压，请检查温度与系数"
        )
    return p_sat


def equilibrium_constants(p_sat_kpa: tuple[float, float], pressure_kpa: float) -> tuple[float, float]:
    """K_i = P_i^sat / P。蒸汽压与压力必须同为 kPa。"""
    return p_sat_kpa[0] / pressure_kpa, p_sat_kpa[1] / pressure_kpa
