"""预置可手工核对的示范物性定义与示范作业。

物性：正戊烷 (n-pentane) / 正己烷 (n-hexane)，自然对数 Antoine 形式
``ln(P/kPa) = A - B/(T_K + C)``，系数由常用 log10(mmHg, °C) 数据换算：

    n-pentane: log10 P/mmHg = 6.87632 - 1075.780/(t + 233.205)
    n-hexane : log10 P/mmHg = 6.91058 - 1189.640/(t + 226.280)

换算到 ln、kPa、K 后（40 °C = 313.15 K）：

    P_pent^sat ≈ 115.77 kPa, P_hex^sat ≈ 36.97 kPa

等摩尔进料 z=(0.5,0.5) 时手算：

    泡点压力 P_b ≈ 0.5*115.77 + 0.5*36.97 ≈ 76.37 kPa
    露点压力 P_d ≈ 1 / (0.5/115.77 + 0.5/36.97) ≈ 56.04 kPa

示范作业（40 °C）：
* P = 85 kPa > P_b：液相单相（K 均小于 1），beta = null
* P = 70 kPa 介于 P_b、P_d：两相，beta ≈ 0.295（与二分结果一致）
* P = 50 kPa < P_d：汽相单相（K 均大于 1），beta = null
"""

from __future__ import annotations

from app.schemas import AntoineIn, ComponentIn, JobCreate, PointIn, PropertySetCreate
from app.services.job_service import JobService
from app.services.property_service import PropertyService

DEMO_PROPERTY_ID = "ps-demo-pentane-hexane"

_PROPERTY = PropertySetCreate(
    name="demo-pentane-hexane",
    description="预置：正戊烷/正己烷，ln(P/kPa)=A-B/(T_K+C)，可手工核对（见 README）",
    pressure_unit="kPa",
    temperature_unit="K",
    components=[
        ComponentIn(
            name="n-pentane",
            antoine=AntoineIn(a=13.818327, b=2477.0750, c=-39.9450),
        ),
        ComponentIn(
            name="n-hexane",
            antoine=AntoineIn(a=13.897213, b=2739.2473, c=-46.8700),
        ),
    ],
)

_JOB = JobCreate(
    name="demo-40C-sweep",
    property_set_id=DEMO_PROPERTY_ID,
    points=[
        PointIn(label="40C, 85kPa (高于泡点, 液相单相)",
                temperature=313.15, pressure=85.0, feed=[0.5, 0.5]),
        PointIn(label="40C, 70kPa (两相, beta≈0.295)",
                temperature=313.15, pressure=70.0, feed=[0.5, 0.5]),
        PointIn(label="40C, 50kPa (低于露点, 汽相单相)",
                temperature=313.15, pressure=50.0, feed=[0.5, 0.5]),
    ],
)


def seed_demo(property_service: PropertyService, job_service: JobService) -> None:
    if property_service._repo.get(DEMO_PROPERTY_ID) is None:
        property_service.register(_PROPERTY, builtin=True, fixed_id=DEMO_PROPERTY_ID)
        job_service.submit(_JOB)
