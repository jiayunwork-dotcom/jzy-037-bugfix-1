"""非法输入：全部必须以带类型的错误拒绝，且不产生近似解/不落半截作业。"""

from __future__ import annotations

import pytest

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


def test_antoine_denominator_zero_celsius_declaration_rejected(client):
    """摄氏度登记的物性撞临界温度点时，受理阶段必须整单拒收并带工况点定位。

    回归：预检中 C 的温标平移曾误用 ``C + 273.15``（求值路径是 ``C − 273.15``），
    导致摄氏度声明时临界点漏过预检、漏到求解阶段抛成无点序号的
    PROPERTY_SET_VALIDATION_FAILED，且作业已经落库。
    """

    # 组分 0 的 C=-50 → 摄氏 50 °C 处 T_C + C_C = 0（极点在正常可登记温区）
    celsius_ps = {
        "name": "crit-c",
        "pressure_unit": "kPa",
        "temperature_unit": "C",
        "components": [
            {"name": "a", "antoine": {"a": 13.8, "b": 2500.0, "c": -50.0}},
            {"name": "b", "antoine": {"a": 14.0, "b": 2800.0, "c": -40.0}},
        ],
    }
    reg = client.post("/property-sets", json=celsius_ps)
    assert reg.status_code == 201

    body = {
        "name": "crit-c-job",
        "property_set_id": reg.json()["id"],
        # 临界点放在第 2 个点，验证错误必须带上正确的点序号
        "points": [
            {"temperature": 60.0, "pressure": 100.0, "feed": [0.5, 0.5]},
            {"temperature": 50.0, "pressure": 100.0, "feed": [0.5, 0.5]},
        ],
    }
    before = len(client.get("/jobs").json())
    r = client.post("/jobs", json=body)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["type"] == "JOB_VALIDATION_FAILED"
    errs = err["details"]["errors"]
    assert [e for e in errs if e["type"] == "ANTOINE_DENOMINATOR_ZERO"]
    hit = next(e for e in errs if e["type"] == "ANTOINE_DENOMINATOR_ZERO")
    assert hit["point_index"] == 1
    assert hit["component_index"] == 0
    assert "分母" in hit["message"]
    # 整单不落库
    after = client.get("/jobs").json()
    assert len(after) == before

    # 近零（但非精确为零，|T+C|=5e-7 < 1e-6）同样必须在受理阶段拦截
    near_body = {
        "name": "crit-c-job-near",
        "property_set_id": reg.json()["id"],
        "points": [
            {"temperature": 50.0000005, "pressure": 100.0, "feed": [0.5, 0.5]},
        ],
    }
    r_near = client.post("/jobs", json=near_body)
    assert r_near.status_code == 422
    near_err = r_near.json()["error"]
    assert near_err["type"] == "JOB_VALIDATION_FAILED"
    assert "ANTOINE_DENOMINATOR_ZERO" in [e["type"] for e in near_err["details"]["errors"]]

    # 等价的开尔文声明（C_K = C_C − 273.15，同温度 323.15 K）行为一致
    kelvin_ps = {
        "name": "crit-k",
        "pressure_unit": "kPa",
        "temperature_unit": "K",
        "components": [
            {"name": "a", "antoine": {"a": 13.8, "b": 2500.0, "c": -323.15}},
            {"name": "b", "antoine": {"a": 14.0, "b": 2800.0, "c": -313.15}},
        ],
    }
    reg_k = client.post("/property-sets", json=kelvin_ps)
    assert reg_k.status_code == 201
    k_body = {
        "name": "crit-k-job",
        "property_set_id": reg_k.json()["id"],
        "points": [
            {"temperature": 313.15, "pressure": 100.0, "feed": [0.5, 0.5]},
            {"temperature": 323.15, "pressure": 100.0, "feed": [0.5, 0.5]},
        ],
    }
    r_k = client.post("/jobs", json=k_body)
    assert r_k.status_code == 422
    k_err = r_k.json()["error"]
    assert k_err["type"] == "JOB_VALIDATION_FAILED"
    k_errors = k_err["details"]["errors"]
    # 点 0 同时撞组分 1 的极点（C=-313.15），点 1 撞组分 0——后者必须被定位到
    k_hit = next(
        e
        for e in k_errors
        if e["type"] == "ANTOINE_DENOMINATOR_ZERO" and e["component_index"] == 0
    )
    assert k_hit["point_index"] == 1
    assert k_hit["component_index"] == 0

    # 非临界的正常温度点照常受理：用示范戊烷/己烷常数的摄氏登记形式
    # （C_C = C_K + 273.15），40 °C / 70 kPa 为两相区，验证正常路径不受影响
    realistic_c = {
        "name": "pent-hex-c",
        "pressure_unit": "kPa",
        "temperature_unit": "C",
        "components": [
            {"name": "n-pentane",
             "antoine": {"a": 13.818327, "b": 2477.075, "c": 233.205}},
            {"name": "n-hexane",
             "antoine": {"a": 13.897213, "b": 2739.2473, "c": 226.28}},
        ],
    }
    reg_ok = client.post("/property-sets", json=realistic_c)
    assert reg_ok.status_code == 201
    ok = client.post(
        "/jobs",
        json={
            "name": "crit-c-ok",
            "property_set_id": reg_ok.json()["id"],
            "points": [
                {"temperature": 40.0, "pressure": 70.0, "feed": [0.5, 0.5]},
            ],
        },
    )
    assert ok.status_code == 201
    point = ok.json()["points"][0]
    assert point["is_two_phase"] is True
    assert point["psat"][0] == pytest.approx(115.77, rel=5e-3)
    assert point["psat"][1] == pytest.approx(36.97, rel=5e-3)


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
