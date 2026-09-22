"""二元等温闪蒸求解内核（理想溶液 / 理想汽相）。

汽化率 beta = 汽相摩尔流量 / 进料摩尔流量，由 Rachford–Rice 方程唯一确定：

    g(beta) = sum_i z_i (K_i - 1) / (1 + beta (K_i - 1)) = 0,  beta in (0, 1)

本服务**全程只使用二分法（bisection）**，不使用牛顿迭代：

* 两相区内 g(0) > 0 且 g(1) < 0，且 g 关于 beta 严格单调递减，二分必定收敛；
* 初始括号 [lo, hi] = [0, 1]，每次取中点并按符号保留异号半区间；
* 收敛判据（满足任一即停）：
    - 括号宽度 hi - lo <= ``BRACKET_TOL``（1e-10）；
    - 迭代次数达到 ``MAX_ITERS``（200）；
  收敛后额外校验 |g(beta)| <= ``RESIDUAL_TOL``（1e-8），不满足则报错，
  绝不返回看似正常的假根。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.errors import FlashComputationError

N_COMPONENTS = 2

BRACKET_TOL = 1.0e-10
RESIDUAL_TOL = 1.0e-8
MAX_ITERS = 200

# 组成求和 / 物料衡算硬关系容差
COMPOSITION_SUM_TOL = 1.0e-8
MATERIAL_BALANCE_TOL = 1.0e-8

# 泡点/露点边界判定的相对容差：g(0) 或 g(1) 距零小于该相对幅度即按单相接处理
BOUNDARY_REL_TOL = 1.0e-9


@dataclass(frozen=True)
class FlashResult:
    """单点闪蒸结果。

    单相时 ``beta`` 为 None；液相单相时 ``liquid`` 为进料组成、``vapor`` 为 None；
    汽相单相时反之。``K`` 在任何相态下都返回。
    """

    regime: str  # "two_phase" | "subcooled_liquid" | "superheated_vapor"
    reason_code: str  # TWO_PHASE_ROOT / BELOW_BUBBLE_POINT / ABOVE_DEW_POINT
    reason: str
    beta: float | None
    liquid: tuple[float, float] | None
    vapor: tuple[float, float] | None
    K: tuple[float, float]
    g0: float
    g1: float
    iterations: int
    rr_residual: float | None


def rachford_rice(beta: float, z: tuple[float, float], K: tuple[float, float]) -> float:
    total = 0.0
    for i in range(N_COMPONENTS):
        d = K[i] - 1.0
        denom = 1.0 + beta * d
        total += z[i] * d / denom
    return total


def _boundary_scale(z: tuple[float, float], K: tuple[float, float]) -> float:
    return max(1.0, *(abs(z[i] * (K[i] - 1.0)) for i in range(N_COMPONENTS)))


def _classify(g0: float, g1: float, z: tuple[float, float], K: tuple[float, float]):
    """按 RR 方程在端点的符号判定相区。

    * g(0) <= 0：压力高于泡点压力（液相侧），进料为过冷液体；
    * g(1) >= 0：压力低于露点压力（汽相侧），进料为过热蒸汽；
    * 否则两相，开区间 (0,1) 内存在唯一根。
    """

    scale = _boundary_scale(z, K)
    tol = BOUNDARY_REL_TOL * scale

    # g0 == 0 即泡点本身，按液相侧处理；微小正边也并入，避免边界数值抖动
    if g0 <= tol:
        return "subcooled_liquid", "BELOW_BUBBLE_POINT"
    if g1 >= -tol:
        return "superheated_vapor", "ABOVE_DEW_POINT"
    return "two_phase", "TWO_PHASE_ROOT"


def solve_beta_bisection(
    z: tuple[float, float], K: tuple[float, float]
) -> tuple[float, int, float]:
    """在 (0,1) 内二分 RR 方程。调用前须已确认 g(0)>0 且 g(1)<0。"""

    lo, hi = 0.0, 1.0
    f_lo = rachford_rice(lo, z, K)
    f_hi = rachford_rice(hi, z, K)
    if f_lo <= 0.0 or f_hi >= 0.0:
        raise FlashComputationError(
            "二分括号不满足异号条件，两相根不应被调用",
            details={"g0": f_lo, "g1": f_hi},
        )

    iters = 0
    mid = 0.5
    for iters in range(1, MAX_ITERS + 1):
        mid = 0.5 * (lo + hi)
        if hi - lo <= BRACKET_TOL:
            break
        f_mid = rachford_rice(mid, z, K)
        if f_mid > 0.0:
            lo = mid
            f_lo = f_mid
        else:
            hi = mid
            f_hi = f_mid

    residual = abs(rachford_rice(mid, z, K))
    if residual > RESIDUAL_TOL:
        raise FlashComputationError(
            "Rachford–Rice 二分收敛但残差超差",
            details={"beta": mid, "residual": residual, "tolerance": RESIDUAL_TOL},
        )
    return mid, iters, residual


def flash(
    z: tuple[float, float],
    K: tuple[float, float],
) -> FlashResult:
    """对单个工况点执行二元等温闪蒸核算。

    ``z`` 与 ``K`` 的下标 0/1 必须与物性定义的组分顺序一致（由上层保证，
    蒸汽压与进料组成共用同一份下标）。
    """

    g0 = rachford_rice(0.0, z, K)
    g1 = rachford_rice(1.0, z, K)
    regime, code = _classify(g0, g1, z, K)

    all_gt = all(K[i] > 1.0 for i in range(N_COMPONENTS))
    all_lt = all(K[i] < 1.0 for i in range(N_COMPONENTS))

    if regime == "subcooled_liquid":
        side = "（所有组分 K_i<1）" if all_lt else ""
        return FlashResult(
            regime=regime,
            reason_code=code,
            reason=(
                f"Rachford–Rice 在 beta=0 处 g(0)={g0:.6g}<=0，"
                f"压力不低于泡点压力{side}，体系为液相单相，不求解汽化率"
            ),
            beta=None,
            liquid=(z[0], z[1]),
            vapor=None,
            K=K,
            g0=g0,
            g1=g1,
            iterations=0,
            rr_residual=None,
        )

    if regime == "superheated_vapor":
        side = "（所有组分 K_i>1）" if all_gt else ""
        return FlashResult(
            regime=regime,
            reason_code=code,
            reason=(
                f"Rachford–Rice 在 beta=1 处 g(1)={g1:.6g}>=0，"
                f"压力不高于露点压力{side}，体系为汽相单相，不求解汽化率"
            ),
            beta=None,
            liquid=None,
            vapor=(z[0], z[1]),
            K=K,
            g0=g0,
            g1=g1,
            iterations=0,
            rr_residual=None,
        )

    beta, iters, residual = solve_beta_bisection(z, K)
    x = tuple(z[i] / (1.0 + beta * (K[i] - 1.0)) for i in range(N_COMPONENTS))
    y = tuple(K[i] * x[i] for i in range(N_COMPONENTS))
    return FlashResult(
        regime="two_phase",
        reason_code="TWO_PHASE_ROOT",
        reason=(
            f"g(0)={g0:.6g}>0 且 g(1)={g1:.6g}<0，(0,1) 内存在唯一两相根；"
            f"二分 {iters} 次，RR 残差 {residual:.2e}"
        ),
        beta=beta,
        liquid=x,  # type: ignore[arg-type]
        vapor=y,  # type: ignore[arg-type]
        K=K,
        g0=g0,
        g1=g1,
        iterations=iters,
        rr_residual=residual,
    )


def check_hard_relations(result: FlashResult, z: tuple[float, float]) -> None:
    """收敛后的硬性关系自检（供服务层/测试共同调用）。

    * 两相：sum(x)、sum(y) 在容差内为 1；物料衡算 z = (1-beta)x + beta y；
    * 单相：存留相组成等于进料组成。
    """

    if result.regime != "two_phase":
        surviving = result.liquid if result.liquid is not None else result.vapor
        assert surviving is not None
        for i in range(N_COMPONENTS):
            if not math.isclose(surviving[i], z[i], abs_tol=COMPOSITION_SUM_TOL):
                raise FlashComputationError(
                    "单相存留组成与进料不一致",
                    details={"component": i, "z": z[i], "surviving": surviving[i]},
                )
        return

    beta = result.beta
    assert beta is not None and result.liquid is not None and result.vapor is not None
    x, y = result.liquid, result.vapor

    sx, sy = sum(x), sum(y)
    if abs(sx - 1.0) > COMPOSITION_SUM_TOL or abs(sy - 1.0) > COMPOSITION_SUM_TOL:
        raise FlashComputationError(
            "相组成之和不闭合", details={"sum_x": sx, "sum_y": sy}
        )
    for i in range(N_COMPONENTS):
        bal = (1.0 - beta) * x[i] + beta * y[i]
        if abs(bal - z[i]) > MATERIAL_BALANCE_TOL:
            raise FlashComputationError(
                "物料衡算残差超差",
                details={"component": i, "z": z[i], "balance": bal, "residual": bal - z[i]},
            )
