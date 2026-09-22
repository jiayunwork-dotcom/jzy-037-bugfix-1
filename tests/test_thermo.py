"""Antoine / K 值与单位、组分下标一致性。"""

from __future__ import annotations

import math

import pytest

from app.errors import PropertyValidationError
from app.thermo.antoine import AntoineCoefficients, antoine_psat_kpa, equilibrium_constants
from app.thermo.units import pressure_to_kpa, temperature_to_kelvin

# 与示范物性同一组常数：ln(P/kPa) = A - B/(T_K + C)
PENT = AntoineCoefficients(13.818327, 2477.0750, -39.9450)
HEX = AntoineCoefficients(13.897213, 2739.2473, -46.8700)


def test_antoine_normal_boiling_points():
    # 戊烷 NBP 36.06C≈101.3 kPa；己烷 68.72C 处约 100.65 kPa（该简化常数的误差）
    assert antoine_psat_kpa(PENT, 309.21, "K", "kPa") == pytest.approx(101.3, rel=5e-3)
    assert antoine_psat_kpa(HEX, 341.87, "K", "kPa") == pytest.approx(100.65, rel=2e-3)


def test_antoine_40c_values():
    p1 = antoine_psat_kpa(PENT, 313.15, "K", "kPa")
    p2 = antoine_psat_kpa(HEX, 313.15, "K", "kPa")
    assert p1 == pytest.approx(115.77, rel=5e-3)
    assert p2 == pytest.approx(36.97, rel=5e-3)


def test_antoine_celsius_declaration_matches_kelvin():
    # 用 log10 mmHg/C 原始系数（高精度）登记，结果必须与 K/kPa 版一致，
    # 差异只来自登记系数的舍入
    ln10 = math.log(10)
    pent_c = AntoineCoefficients(6.87632 * ln10, 1075.780 * ln10, 233.205)
    p_k = antoine_psat_kpa(PENT, 313.15, "K", "kPa")
    p_c = antoine_psat_kpa(pent_c, 40.0, "C", "mmHg")
    assert p_c == pytest.approx(p_k, rel=1e-5)


def test_antoine_denominator_zero_rejected():
    # T_K + C = 0 即 T = 39.945 K
    with pytest.raises(PropertyValidationError) as ei:
        antoine_psat_kpa(PENT, 39.945, "K", "kPa")
    assert ei.value.error_type == "PROPERTY_SET_VALIDATION_FAILED"
    assert "分母" in ei.value.message


def test_equilibrium_constants_index_order_and_units():
    p_sat = (115.7694, 36.9705)
    K = equilibrium_constants(p_sat, 70.0)
    assert K == pytest.approx((1.65385, 0.52815), rel=1e-4)
    # 用 bar 给压：70 kPa = 0.7 bar，换算后 K 必须相同
    K_bar = equilibrium_constants(
        (pressure_to_kpa(1.157694, "bar"), pressure_to_kpa(0.369705, "bar")),
        pressure_to_kpa(0.7, "bar"),
    )
    assert K_bar == pytest.approx(K, rel=1e-9)


def test_temperature_conversion():
    assert temperature_to_kelvin(40.0, "C") == pytest.approx(313.15)
    assert temperature_to_kelvin(313.15, "K") == 313.15
