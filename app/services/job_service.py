"""作业受理、逐点求解与结果取回。

受理流程（整单事务性校验，任何一点非法则整单 422、不落库）：
1. 解析引用的或随单内联的物性定义（内联会先登记，供后续作业复用）；
2. 逐点校验温度/压力/进料组成/点级 psat 覆盖，并预检 Antoine 分母；
3. 全部通过后写作业与各点，逐点求解，结果（相态、beta、x、y、K 等）落库。

中间量（K、beta、组成）都在点级纯函数调用内独立产生，作业间/点间不共享
任何可变状态；数据库写操作由 Database 的锁串行化。
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any

from app.errors import (
    JobNotFoundError,
    JobValidationError,
    PointNotFoundError,
)
from app.persistence.job_repository import JobRepository, point_row_to_dict
from app.persistence.property_repository import PropertyRepository
from app.schemas import JobCreate
from app.services.property_service import PropertyService
from app.thermo import flash as flash_mod
from app.thermo.antoine import (
    ANTOINE_DENOM_MIN,
    AntoineCoefficients,
    antoine_denominator,
    antoine_psat_kpa,
    equilibrium_constants,
)
from app.thermo.units import pressure_to_kpa, temperature_to_kelvin

_FEED_SUM_TOL = 1.0e-6


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _err(index: int, type_: str, message: str, **extra: Any) -> dict[str, Any]:
    d = {"point_index": index, "type": type_, "message": message}
    d.update(extra)
    return d


class JobService:
    def __init__(
        self,
        jobs: JobRepository,
        properties: PropertyService,
        property_repo: PropertyRepository,
    ):
        self._jobs = jobs
        self._properties = properties
        self._property_repo = property_repo

    # ------------------------------------------------------------------
    def submit(self, payload: JobCreate) -> dict[str, Any]:
        # 1) 解析物性定义
        if payload.property_set_id is not None and payload.property_set is not None:
            raise JobValidationError(
                "property_set_id 与内联 property_set 只能二选一",
                details={
                    "errors": [
                        {
                            "type": "AMBIGUOUS_PROPERTY_REFERENCE",
                            "message": "引用已登记物性与临时内联物性不能同时出现",
                        }
                    ]
                },
            )

        if payload.property_set_id is not None:
            prop = self._properties.get(payload.property_set_id)
        elif payload.property_set is not None:
            prop = self._properties.register(payload.property_set)
        else:
            raise JobValidationError(
                "作业必须引用 property_set_id 或内联 property_set",
                details={
                    "errors": [
                        {
                            "type": "MISSING_PROPERTY_SET",
                            "message": "请提供已登记物性定义 id 或随作业临时给出",
                        }
                    ]
                },
            )

        # 2) 逐点校验（聚合全部错误一次性返回）
        errors: list[dict[str, Any]] = []
        for i, pt in enumerate(payload.points):
            errors.extend(self._validate_point(i, pt, prop))
        if errors:
            raise JobValidationError(
                f"作业含 {len(errors)} 处非法输入，已拒绝受理",
                details={"errors": errors},
            )

        # 3) 落库作业与点
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        created_at = _now()
        self._jobs.insert_job(
            id=job_id, name=payload.name, property_set_id=prop["id"], created_at=created_at
        )
        for i, pt in enumerate(payload.points):
            self._jobs.insert_point(
                job_id=job_id,
                point_index=i,
                label=pt.label,
                temperature=pt.temperature,
                pressure=pt.pressure,
                feed=list(pt.feed),
                psat=list(pt.psat) if pt.psat is not None else None,
            )

        # 4) 逐点独立求解并落库
        results = []
        for i, pt in enumerate(payload.points):
            result = self._solve_point(prop, pt, i)
            self._jobs.save_point_result(
                job_id=job_id, point_index=i, result=result, computed_at=_now()
            )
            results.append(result)
        return {
            "id": job_id,
            "name": payload.name,
            "property_set_id": prop["id"],
            "points": results,
            "created_at": created_at,
        }

    # ------------------------------------------------------------------
    def _validate_point(self, index: int, pt: Any, prop: dict[str, Any]) -> list[dict[str, Any]]:
        errs: list[dict[str, Any]] = []

        if not math.isfinite(pt.temperature) or pt.temperature <= 0:
            errs.append(
                _err(
                    index,
                    "NON_POSITIVE_TEMPERATURE",
                    f"温度必须为正数（收到 {pt.temperature!r}）",
                )
            )
        if not math.isfinite(pt.pressure) or pt.pressure <= 0:
            errs.append(
                _err(
                    index,
                    "NON_POSITIVE_PRESSURE",
                    f"压力必须为正数（收到 {pt.pressure!r}）",
                )
            )

        feed = list(pt.feed)
        if any((not math.isfinite(v)) or v <= 0 for v in feed):
            errs.append(
                _err(
                    index,
                    "NON_POSITIVE_FEED_COMPOSITION",
                    "进料组成必须全部为正数（二元闪蒸不允许纯组分边界）",
                    feed=feed,
                )
            )
        else:
            s = sum(feed)
            if abs(s - 1.0) > _FEED_SUM_TOL:
                errs.append(
                    _err(
                        index,
                        "FEED_SUM_NOT_ONE",
                        f"进料组成之和 {s:.9g} 与 1 的偏差超过容差 {_FEED_SUM_TOL:g}",
                        feed=feed,
                        sum=s,
                    )
                )

        if pt.psat is not None:
            ps = list(pt.psat)
            if any((not math.isfinite(v)) or v <= 0 for v in ps):
                errs.append(
                    _err(
                        index,
                        "NON_POSITIVE_SATURATION_PRESSURE",
                        "点级直给饱和蒸汽压必须全部为正数",
                        psat=ps,
                    )
                )

        # Antoine 预检：分母为零/近零必须在受理阶段拦截
        if (
            prop["source"] == "antoine"
            and pt.psat is None
            and math.isfinite(pt.temperature)
            and pt.temperature > 0
        ):
            t_k = temperature_to_kelvin(pt.temperature, prop["temperature_unit"])
            for ci, comp in enumerate(prop["components"]):
                a, b, c = comp["antoine"]
                denom = antoine_denominator(t_k, c, prop["temperature_unit"])
                if not all(math.isfinite(v) for v in (a, b, c)):
                    errs.append(
                        _err(index, "INVALID_ANTOINE_COEFFICIENTS",
                             f"组分 {ci} 的 Antoine 系数缺失或非有限数")
                    )
                elif abs(denom) < ANTOINE_DENOM_MIN:
                    errs.append(
                        _err(
                            index,
                            "ANTOINE_DENOMINATOR_ZERO",
                            f"组分 {ci}({comp['name']}) 的 Antoine 分母 T+C 在该温度下接近零"
                            f"（|T+C|={abs(denom):.3e}）",
                            component_index=ci,
                            denominator=denom,
                        )
                    )
        return errs

    # ------------------------------------------------------------------
    def _resolve_psat_kpa(
        self, prop: dict[str, Any], pt: Any, point_index: int
    ) -> tuple[float, float]:
        """按唯一组分下标顺序取本点蒸汽压（kPa）。

        优先级：点级直给 psat > 物性 direct 固定值 > Antoine(T)。
        无论哪种来源，返回的 (P0sat, P1sat) 下标都与 components 一致，
        K 值计算与闪蒸求解共用同一顺序。
        """

        t_unit, p_unit = prop["temperature_unit"], prop["pressure_unit"]

        if pt.psat is not None:
            vals = [pressure_to_kpa(float(v), p_unit) for v in pt.psat]
        elif prop["source"] == "direct":
            vals = [pressure_to_kpa(float(v), p_unit) for v in prop["psat"]]
        else:
            vals = []
            for comp in prop["components"]:
                a, b, c = comp["antoine"]
                vals.append(
                    antoine_psat_kpa(
                        AntoineCoefficients(a, b, c),
                        float(pt.temperature),
                        t_unit,
                        p_unit,
                    )
                )

        for ci, v in enumerate(vals):
            if not math.isfinite(v) or v <= 0:
                # 理论上已在受理校验拦截；这里是求解内核前的最后防线
                raise JobValidationError(
                    "饱和蒸汽压非法，拒绝求解",
                    details={
                        "errors": [
                            _err(
                                point_index,
                                "NON_POSITIVE_SATURATION_PRESSURE",
                                f"组分 {ci} 的饱和蒸汽压非正或非有限: {v!r}",
                            )
                        ]
                    },
                )
        return vals[0], vals[1]

    # ------------------------------------------------------------------
    def _solve_point(self, prop: dict[str, Any], pt: Any, index: int) -> dict[str, Any]:
        p_sat = self._resolve_psat_kpa(prop, pt, index)
        pressure_kpa = pressure_to_kpa(float(pt.pressure), prop["pressure_unit"])
        temperature_k = temperature_to_kelvin(float(pt.temperature), prop["temperature_unit"])

        K = equilibrium_constants(p_sat, pressure_kpa)
        z = (float(pt.feed[0]), float(pt.feed[1]))
        res = flash_mod.flash(z, K)
        # 硬性关系自检：组成闭合 + 物料衡算；不满足直接抛出而非存近似解
        flash_mod.check_hard_relations(res, z)

        return {
            "index": index,
            "label": pt.label,
            "temperature": float(pt.temperature),
            "pressure": float(pt.pressure),
            "feed": [z[0], z[1]],
            "is_two_phase": res.regime == "two_phase",
            "regime": res.regime,
            "reason_code": res.reason_code,
            "reason": res.reason,
            "beta": res.beta,
            "liquid": list(res.liquid) if res.liquid is not None else None,
            "vapor": list(res.vapor) if res.vapor is not None else None,
            "K": [K[0], K[1]],
            "psat": [p_sat[0], p_sat[1]],
            "temperature_k": temperature_k,
            "pressure_kpa": pressure_kpa,
            "solver": {
                "method": "bisection",
                "bracket_tol": flash_mod.BRACKET_TOL,
                "residual_tol": flash_mod.RESIDUAL_TOL,
                "max_iters": flash_mod.MAX_ITERS,
                "iterations": res.iterations if res.regime == "two_phase" else None,
                "rr_residual": res.rr_residual,
                "g0": res.g0,
                "g1": res.g1,
            },
            "computed_at": _now(),
        }

    # ------------------------------------------------------------------
    def _load_job(self, job_id: str) -> dict[str, Any]:
        row = self._jobs.get_job_row(job_id)
        if row is None:
            raise JobNotFoundError(
                f"作业不存在: {job_id!r}", details={"job_id": job_id}
            )
        points = [point_row_to_dict(r) for r in self._jobs.get_point_rows(job_id)]
        return {
            "id": row["id"],
            "name": row["name"],
            "property_set_id": row["property_set_id"],
            "created_at": row["created_at"],
            "points": points,
        }

    def get_job(self, job_id: str) -> dict[str, Any]:
        job = self._load_job(job_id)
        return self._serialize_job(job)

    def list_jobs(self) -> list[dict[str, str]]:
        rows = self._jobs.list_jobs()
        return [
            {"id": r["id"], "name": r["name"], "property_set_id": r["property_set_id"],
             "created_at": r["created_at"]}
            for r in rows
        ]

    def get_point(self, job_id: str, point_ref: str) -> dict[str, Any]:
        job = self._load_job(job_id)
        try:
            idx = int(point_ref)
        except ValueError as exc:
            raise PointNotFoundError(
                f"工况点标识必须为从 0 开始的整数序号: {point_ref!r}",
                details={"job_id": job_id, "point_ref": point_ref},
            ) from exc
        for p in job["points"]:
            if p["index"] == idx:
                return p["result"]
        raise PointNotFoundError(
            f"作业 {job_id!r} 中不存在工况点 {idx}",
            details={"job_id": job_id, "point_index": idx},
        )

    @staticmethod
    def _serialize_job(job: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": job["id"],
            "name": job["name"],
            "property_set_id": job["property_set_id"],
            "points": [p["result"] for p in job["points"]],
            "created_at": job["created_at"],
        }
