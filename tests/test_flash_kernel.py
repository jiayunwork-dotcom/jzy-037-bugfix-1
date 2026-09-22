"""闪蒸内核：闭合关系、相区判定、求解方法、手算对照。"""

from __future__ import annotations

import math

import pytest

from app.errors import FlashComputationError
from app.thermo import flash as fm

TOL = 1e-8


def test_hand_check_equimolar_70kpa():
    # 40C 戊烷/己烷：P1sat=115.769, P2sat=36.970 kPa；P=70 kPa
    K = (115.7694 / 70.0, 36.9705 / 70.0)
    z = (0.5, 0.5)
    r = fm.flash(z, K)
    assert r.regime == "two_phase"
    assert r.reason_code == "TWO_PHASE_ROOT"
    # 解析手算 beta≈0.294955
    assert r.beta == pytest.approx(0.294955, abs=1e-5)


def test_two_phase_closure_and_material_balance():
    cases = [
        ((0.5, 0.5), (1.653848, 0.528150)),
        ((0.6, 0.4), (2.1, 0.4)),
        ((0.6, 0.4), (1.5, 0.5)),
        ((0.5, 0.5), (4.0, 0.2)),
    ]
    for z, K in cases:
        r = fm.flash(z, K)
        assert r.regime == "two_phase"
        x, y, beta = r.liquid, r.vapor, r.beta
        assert abs(sum(x) - 1) < TOL
        assert abs(sum(y) - 1) < TOL
        for i in range(2):
            assert abs((1 - beta) * x[i] + beta * y[i] - z[i]) < TOL
            # 相平衡 y_i = K_i x_i
            assert abs(y[i] - K[i] * x[i]) < TOL
        assert 0.0 < beta < 1.0
        fm.check_hard_relations(r, z)


def test_bisection_is_the_only_method_and_residual_declared():
    r = fm.flash((0.5, 0.5), (1.653848, 0.528150))
    assert r.iterations > 0
    assert r.rr_residual is not None and r.rr_residual <= fm.RESIDUAL_TOL
    # 二分括号宽度：最终 beta 与 0/1 均不贴边时，iters 与 BRACKET_TOL 量级相容
    assert fm.BRACKET_TOL == 1e-10
    assert fm.MAX_ITERS >= 100


def test_bisection_converges_even_for_extreme_split():
    # 根靠近泡点侧与露点侧都不能跑出 [0,1]
    K = (10.0, 0.1)
    r = fm.flash((0.20, 0.80), K)
    assert r.regime == "two_phase"
    assert 0 < r.beta < 1
    assert r.beta < 0.2
    fm.check_hard_relations(r, (0.20, 0.80))

    r2 = fm.flash((0.80, 0.20), K)
    assert r2.regime == "two_phase"
    assert 0 < r2.beta < 1
    assert r2.beta > 0.8
    fm.check_hard_relations(r2, (0.80, 0.20))


def test_all_K_below_one_is_subcooled_liquid_without_fake_beta():
    r = fm.flash((0.5, 0.5), (0.8, 0.2))
    assert all(k < 1 for k in r.K)
    assert r.regime == "subcooled_liquid"
    assert r.reason_code == "BELOW_BUBBLE_POINT"
    assert r.beta is None
    assert r.vapor is None
    assert r.liquid == (0.5, 0.5)
    fm.check_hard_relations(r, (0.5, 0.5))


def test_all_K_above_one_is_superheated_vapor_without_fake_beta():
    r = fm.flash((0.5, 0.5), (2.3, 1.4))
    assert all(k > 1 for k in r.K)
    assert r.regime == "superheated_vapor"
    assert r.reason_code == "ABOVE_DEW_POINT"
    assert r.beta is None
    assert r.liquid is None
    assert r.vapor == (0.5, 0.5)
    fm.check_hard_relations(r, (0.5, 0.5))


def test_mixed_K_but_outside_two_phase_is_classified_single_phase():
    # 罕见但严格：K1>1、K2<1 时仍可能 g(0)<=0（压力恰好高于泡点）
    # z1 极小、K1 仅略大于 1 时 g0 可为负
    K = (1.0001, 0.1)
    r = fm.flash((1e-6, 1 - 1e-6), K)
    assert r.regime == "subcooled_liquid"
    assert r.beta is None


def test_beta_monotonic_nonincreasing_with_pressure():
    # 同一 T、z：升高压力 => beta 不得上升。
    # 汽相单相侧 beta 视为 1（继续升压直到进入两相 beta 再下降），
    # 液相单相侧 beta 视为 0；完整序列在 [0,1] 上对压力单调不增。
    z = (0.5, 0.5)
    p1sat, p2sat = 115.7694, 36.9705
    betas = []
    for P in [30.0, 45.0, 55.0, 58.0, 62.0, 66.0, 70.0, 74.0, 76.0, 80.0, 90.0]:
        r = fm.flash(z, (p1sat / P, p2sat / P))
        if r.beta is not None:
            betas.append(r.beta)
        elif r.regime == "superheated_vapor":
            betas.append(1.0)
        else:
            betas.append(0.0)
    for a, b in zip(betas, betas[1:]):
        assert b <= a + 1e-12
    # 且在两相区内部严格下降
    two_phase = [
        fm.flash(z, (p1sat / P, p2sat / P)).beta
        for P in (58.0, 62.0, 66.0, 70.0, 74.0)
    ]
    for a, b in zip(two_phase, two_phase[1:]):
        assert b < a


def test_beta_approaches_zero_near_bubble_point():
    # 从两相侧逼近泡点 P_b≈76.37，beta -> 0
    z = (0.5, 0.5)
    p1sat, p2sat = 115.7694, 36.9705
    p_b = z[0] * p1sat + z[1] * p2sat
    betas = []
    for eps in (1e-1, 1e-2, 1e-3, 1e-4):
        P = p_b - eps
        r = fm.flash(z, (p1sat / P, p2sat / P))
        assert r.regime == "two_phase"
        betas.append(r.beta)
    assert betas[-1] < betas[0] * 0.01
    assert betas[-1] < 1e-3
    # 跨过泡点：液相单相
    r_above = fm.flash(z, (p1sat / (p_b + 1.0), p2sat / (p_b + 1.0)))
    assert r_above.regime == "subcooled_liquid"
    assert r_above.beta is None


def test_beta_approaches_one_near_dew_point():
    # 从两相侧逼近露点 P_d≈56.04，beta -> 1
    z = (0.5, 0.5)
    p1sat, p2sat = 115.7694, 36.9705
    p_d = 1 / (z[0] / p1sat + z[1] / p2sat)
    betas = []
    for eps in (1e-1, 1e-2, 1e-3, 1e-4):
        P = p_d + eps
        r = fm.flash(z, (p1sat / P, p2sat / P))
        assert r.regime == "two_phase"
        betas.append(r.beta)
    assert betas[-1] > 1 - 1e-3
    assert betas[-1] > betas[0]
    r_below = fm.flash(z, (p1sat / (p_d - 1.0), p2sat / (p_d - 1.0)))
    assert r_below.regime == "superheated_vapor"
    assert r_below.beta is None


def test_solver_never_returns_fake_root_outside_interval():
    # 直接对单相工况调用二分求解器应当报错而不是返回区间外的“根”
    with pytest.raises(FlashComputationError):
        fm.solve_beta_bisection((0.5, 0.5), (0.8, 0.2))
