"""非法输入：全部必须以带类型的错误拒绝，且不产生近似解/不落半截作业。"""

from __future__ import annotations

DEMO_ID = "ps-demo-pentane-hexane"


def _submit(client, **point):
    body = {
        "name": "bad",
        "property_set_id": DEMO_ID,
        "points": [{"temperature": 313.15, "pressure": 70.0, "feed": [0.5, 0.5]} | point],
    }
    return client.post("/jobs", json=body)


def test_non_positive_temperature_pressure(client):
    for key, val, etype in [
        ("temperature", 0.0, "NON_POSITIVE_TEMPERATURE"),
        ("temperature", -5.0, "NON_POSITIVE_TEMPERATURE"),
        ("pressure", 0.0, "NON_POSITIVE_PRESSURE"),
        ("pressure", -1.0, "NON_POSITIVE_PRESSURE"),
    ]:
        r = _submit(client, **{key: val})
        assert r.status_code == 422
        err = r.json()["error"]
        assert err["type"] == "JOB_VALIDATION_FAILED"
        types = [e["type"] for e in err["details"]["errors"]]
        assert etype in types


def test_non_positive_feed(client):
    r = _submit(client, feed=[0.0, 1.0])
    assert r.status_code == 422
    assert r.json()["error"]["details"]["errors"][0]["type"] == "NON_POSITIVE_FEED_COMPOSITION"
    r = _submit(client, feed=[0.6, -0.1])
    assert "NON_POSITIVE_FEED_COMPOSITION" in [
        e["type"] for e in r.json()["error"]["details"]["errors"]
    ]


def test_feed_sum_outside_tolerance(client):
    r = _submit(client, feed=[0.6, 0.5])
    types = [e["type"] for e in r.json()["error"]["details"]["errors"]]
    assert "FEED_SUM_NOT_ONE" in types
    # 容差内允许（不报错）
    ok = _submit(client, feed=[0.5000001, 0.4999999])
    assert ok.status_code == 201


def test_non_positive_direct_psat(client):
    body = {
        "name": "direct-bad",
        "pressure_unit": "kPa",
        "temperature_unit": "K",
        "components": [{"name": "a"}, {"name": "b"}],
        "psat": [0.0, -3.0],
    }
    r = client.post("/property-sets", json=body)
    assert r.status_code == 422
    types = [e["type"] for e in r.json()["error"]["details"]["errors"]]
    assert "NON_POSITIVE_SATURATION_PRESSURE" in types

    # 点级覆盖非正
    r2 = _submit(client, psat=[100.0, 0.0])
    assert r2.status_code == 422
    assert "NON_POSITIVE_SATURATION_PRESSURE" in [
        e["type"] for e in r2.json()["error"]["details"]["errors"]
    ]


def test_missing_antoine_and_denominator_zero(client):
    # 混合来源：一个给 Antoine 一个不给 → MIXED_PROPERTY_SOURCE
    body = {
        "name": "mixed",
        "components": [
            {"name": "a", "antoine": {"a": 1.0, "b": 2.0, "c": 3.0}},
            {"name": "b"},
        ],
        "pressure_unit": "kPa",
        "temperature_unit": "K",
    }
    r = client.post("/property-sets", json=body)
    assert r.status_code == 422
    assert "MIXED_PROPERTY_SOURCE" in [
        e["type"] for e in r.json()["error"]["details"]["errors"]
    ]

    # 两个都不给又没有 psat → MISSING_SATURATION_PRESSURE
    body2 = {
        "name": "none",
        "components": [{"name": "a"}, {"name": "b"}],
        "pressure_unit": "kPa",
        "temperature_unit": "K",
    }
    r2 = client.post("/property-sets", json=body2)
    assert "MISSING_SATURATION_PRESSURE" in [
        e["type"] for e in r2.json()["error"]["details"]["errors"]
    ]

    # Antoine 分母为零：戊烷 C=-39.945(K)，T=39.945 K 处分母为零
    r3 = _submit(client, temperature=39.945)
    assert r3.status_code == 422
    types = [e["type"] for e in r3.json()["error"]["details"]["errors"]]
    assert "ANTOINE_DENOMINATOR_ZERO" in types


def test_invalid_job_does_not_persist(client):
    before = len(client.get("/jobs").json())
    r = _submit(client, pressure=-1.0, feed=[0.9, 0.9])
    assert r.status_code == 422
    after = client.get("/jobs").json()
    assert len(after) == before  # 非法作业整单不落库


def test_multiple_errors_aggregated(client):
    r = _submit(client, temperature=-1, pressure=0, feed=[2.0, 2.0])
    errs = r.json()["error"]["details"]["errors"]
    types = {e["type"] for e in errs}
    assert {"NON_POSITIVE_TEMPERATURE", "NON_POSITIVE_PRESSURE"} <= types


def test_bad_shape_returns_typed_envelope(client):
    r = client.post(
        "/jobs",
        json={"name": "x", "property_set_id": DEMO_ID, "points": [{"temperature": "hot"}]},
    )
    assert r.status_code == 422
    assert r.json()["error"]["type"] == "REQUEST_VALIDATION_FAILED"


def test_non_finite_floats_rejected_at_any_depth(client):
    raw = (
        '{"name":"n","pressure_unit":"kPa","temperature_unit":"K",'
        '"components":[{"name":"a"},{"name":"b"}],"psat":[1.0, NaN]}'
    )
    r = client.post("/property-sets", content=raw,
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert r.json()["error"]["type"] == "REQUEST_VALIDATION_FAILED"

    raw_job = (
        '{"name":"n","property_set_id":"%s",'
        '"points":[{"temperature":313.15,"pressure":70.0,"feed":[0.5,NaN]}]}' % DEMO_ID
    )
    r2 = client.post("/jobs", content=raw_job,
                     headers={"Content-Type": "application/json"})
    assert r2.status_code == 422
    assert r2.json()["error"]["type"] == "REQUEST_VALIDATION_FAILED"


def test_ambiguous_property_reference(client):
    r = client.post(
        "/jobs",
        json={
            "name": "x",
            "property_set_id": DEMO_ID,
            "property_set": {
                "name": "dup",
                "components": [{"name": "a"}, {"name": "b"}],
                "psat": [1.0, 2.0],
            },
            "points": [{"temperature": 300.0, "pressure": 1.0, "feed": [0.5, 0.5]}],
        },
    )
    assert r.status_code == 422
    assert r.json()["error"]["details"]["errors"][0]["type"] == "AMBIGUOUS_PROPERTY_REFERENCE"


def test_celsius_declared_denominator_zero_rejected_with_point_index(client):
    # 回归：摄氏度声明的物性，临界温度（T+C 精确为零）必须与开尔文声明走同一条
    # 受理期整单拒收（JOB_VALIDATION_FAILED + point_index + component_index），
    # 而不是求解期才抛 PROPERTY_SET_VALIDATION_FAILED；且作业整单不落库。
    t_crit_c = 40.0  # °C
    # 组分 0 的 C 取成负的工况温度 → T_C + C_C = 0；组分 1 给正常系数。
    # 刻意取较小的 B：既保留“分母为零”这一非法性，又让非临界点上的正常点
    # K 值温和（不把闪蒸内核推到与本回归无关的极端 K 数值边界）。
    cels_body = {
        "name": "celsius-critical",
        "pressure_unit": "kPa",
        "temperature_unit": "C",
        "components": [
            {"name": "crit", "antoine": {"a": 10.0, "b": 100.0, "c": -t_crit_c}},
            {"name": "ok", "antoine": {"a": 10.0, "b": 100.0, "c": 200.0}},
        ],
    }
    r = client.post("/property-sets", json=cels_body)
    assert r.status_code == 201
    ps_c = r.json()["id"]

    # 等价的开尔文声明：C_K = C_C − 273.15，临界点 T_K = T_C + 273.15
    kelv_body = {
        "name": "kelvin-equivalent",
        "pressure_unit": "kPa",
        "temperature_unit": "K",
        "components": [
            {"name": "crit", "antoine": {"a": 10.0, "b": 100.0, "c": -t_crit_c - 273.15}},
            {"name": "ok", "antoine": {"a": 10.0, "b": 100.0, "c": 200.0 - 273.15}},
        ],
    }
    rk = client.post("/property-sets", json=kelv_body)
    assert rk.status_code == 201
    ps_k = rk.json()["id"]

    def _submit(ps_id, temperature, unit):
        return client.post(
            "/jobs",
            json={
                "name": f"crit-{unit}",
                "property_set_id": ps_id,
                "points": [
                    # 临界点放在第 1 个点（序号 0 前先放一个正常点也不必，直接验序号）
                    {"temperature": temperature, "pressure": 70.0, "feed": [0.5, 0.5]},
                ],
            },
        )

    before = len(client.get("/jobs").json())

    rc = _submit(ps_c, t_crit_c, "C")
    assert rc.status_code == 422
    err = rc.json()["error"]
    # 必须是作业级整单拒收，带工况点定位；不能退化成物性校验失败
    assert err["type"] == "JOB_VALIDATION_FAILED"
    assert err["details"]["errors"][0]["type"] == "ANTOINE_DENOMINATOR_ZERO"
    assert err["details"]["errors"][0]["point_index"] == 0
    assert err["details"]["errors"][0]["component_index"] == 0

    # 等价开尔文声明在同一临界物理状态上行为一致
    rke = _submit(ps_k, t_crit_c + 273.15, "K")
    assert rke.status_code == 422
    err_k = rke.json()["error"]
    assert err_k["type"] == "JOB_VALIDATION_FAILED"
    e0 = err_k["details"]["errors"][0]
    assert e0["type"] == "ANTOINE_DENOMINATOR_ZERO"
    assert e0["point_index"] == 0 and e0["component_index"] == 0

    # 整单不落库：两次拒收后作业数量不变
    assert len(client.get("/jobs").json()) == before

    # 临界点之外，摄氏度声明仍正常受理（数值路径未被改动）
    ro = _submit(ps_c, t_crit_c + 10.0, "C")
    assert ro.status_code == 201
