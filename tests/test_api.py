"""HTTP 层：作业生命周期、逐点取回、复用物性、闭合关系落库。"""

from __future__ import annotations

import pytest

DEMO_ID = "ps-demo-pentane-hexane"


def _job(pid=DEMO_ID, pressure=70.0, feed=(0.5, 0.5), T=313.15, label=""):
    return {
        "name": "t",
        "property_set_id": pid,
        "points": [
            {"temperature": T, "pressure": pressure, "feed": list(feed), "label": label}
        ],
    }


def test_health_and_demo_seed(client):
    assert client.get("/health").json() == {"status": "ok"}
    props = client.get("/property-sets").json()
    assert any(p["id"] == DEMO_ID and p["builtin"] for p in props)
    jobs = client.get("/jobs").json()
    assert len(jobs) == 1
    j = client.get(f"/jobs/{jobs[0]['id']}").json()
    regimes = {p["regime"] for p in j["points"]}
    assert regimes == {"subcooled_liquid", "two_phase", "superheated_vapor"}


def test_submit_and_retrieve_single_point(client):
    r = client.post("/jobs", json=_job(pressure=70.0))
    assert r.status_code == 201, r.text
    job = r.json()
    p = job["points"][0]
    assert p["is_two_phase"] is True
    assert p["beta"] == pytest.approx(0.294955, abs=1e-5)
    assert abs(sum(p["liquid"]) - 1) < 1e-8
    assert abs(sum(p["vapor"]) - 1) < 1e-8
    b = p["beta"]
    for i in range(2):
        assert abs((1 - b) * p["liquid"][i] + b * p["vapor"][i] - p["feed"][i]) < 1e-8
    assert p["solver"]["method"] == "bisection"
    assert p["solver"]["rr_residual"] <= 1e-8

    # 按作业号 + 点序号取回
    jid = job["id"]
    got = client.get(f"/jobs/{jid}/points/0").json()
    assert got["beta"] == p["beta"]
    listed = client.get(f"/jobs/{jid}/points").json()
    assert [q["index"] for q in listed] == [0]


def test_single_phase_points_have_null_beta(client):
    j_high = client.post("/jobs", json=_job(pressure=200.0)).json()
    p = j_high["points"][0]
    assert p["regime"] == "subcooled_liquid"
    assert p["beta"] is None and p["vapor"] is None
    assert all(k < 1 for k in p["K"])

    j_low = client.post("/jobs", json=_job(pressure=20.0)).json()
    p = j_low["points"][0]
    assert p["regime"] == "superheated_vapor"
    assert p["beta"] is None and p["liquid"] is None
    assert all(k > 1 for k in p["K"])


def test_register_property_and_reuse_across_jobs(client, demo_property_payload):
    payload = dict(demo_property_payload)
    payload["name"] = "reuse-me"
    r = client.post("/property-sets", json=payload)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    j1 = client.post("/jobs", json=_job(pid=pid, pressure=70.0)).json()
    j2 = client.post("/jobs", json=_job(pid=pid, pressure=72.0)).json()
    assert j1["property_set_id"] == j2["property_set_id"] == pid
    assert j1["id"] != j2["id"]
    # 两份作业结果各自独立
    assert j1["points"][0]["beta"] != j2["points"][0]["beta"]


def test_inline_property_set_is_persisted_and_reusable(client, demo_property_payload):
    body = {
        "name": "inline-job",
        "property_set": dict(demo_property_payload, name="inline-ps"),
        "points": [{"temperature": 313.15, "pressure": 70.0, "feed": [0.5, 0.5]}],
    }
    r = client.post("/jobs", json=body)
    assert r.status_code == 201, r.text
    pid = r.json()["property_set_id"]
    assert client.get(f"/property-sets/{pid}").status_code == 200
    again = client.post("/jobs", json=_job(pid=pid, pressure=68.0))
    assert again.status_code == 201


def test_direct_psat_bypasses_antoine_with_same_index_order(client):
    # 直给模式：下标 0=重组分(小蒸汽压)、下标 1=轻组分(大蒸汽压)，顺序必须被尊重
    ps = {
        "name": "direct-1",
        "pressure_unit": "kPa",
        "temperature_unit": "K",
        "components": [{"name": "heavy"}, {"name": "light"}],
        "psat": [36.9705, 115.7694],
    }
    r = client.post("/property-sets", json=ps)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    job = client.post("/jobs", json=_job(pid=pid, pressure=70.0)).json()
    p = job["points"][0]
    assert p["K"][0] == pytest.approx(36.9705 / 70.0, rel=1e-12)
    assert p["K"][1] == pytest.approx(115.7694 / 70.0, rel=1e-12)
    # 重组分在液相富集、轻组分在汽相富集
    assert p["liquid"][0] > p["feed"][0]
    assert p["vapor"][1] > p["feed"][1]


def test_point_level_psat_override(client, demo_property_payload):
    body = {
        "name": "override-job",
        "property_set": dict(demo_property_payload, name="override-ps"),
        "points": [
            {
                "temperature": 313.15,
                "pressure": 70.0,
                "feed": [0.5, 0.5],
                "psat": [100.0, 50.0],
            }
        ],
    }
    p = client.post("/jobs", json=body).json()["points"][0]
    assert p["psat"] == [100.0, 50.0]
    assert p["K"] == pytest.approx([100 / 70, 50 / 70])


def test_multiple_points_in_one_job_are_independent(client):
    body = {
        "name": "multi",
        "property_set_id": DEMO_ID,
        "points": [
            {"temperature": 313.15, "pressure": P, "feed": [0.5, 0.5]}
            for P in (70.0, 60.0, 200.0)
        ],
    }
    job = client.post("/jobs", json=body).json()
    assert [q["index"] for q in job["points"]] == [0, 1, 2]
    assert job["points"][0]["beta"] != job["points"][1]["beta"]
    assert job["points"][2]["beta"] is None


def test_404s(client):
    assert client.get("/jobs/nope").status_code == 404
    assert client.get("/property-sets/nope").status_code == 404
    jid = client.post("/jobs", json=_job(pressure=70.0)).json()["id"]
    r = client.get(f"/jobs/{jid}/points/9")
    assert r.status_code == 404
    assert r.json()["error"]["type"] == "POINT_NOT_FOUND"


def test_bar_units_roundtrip(client):
    # 同一物理体系改用 bar 登记：ln(P/bar) = ln(P/kPa) - ln(100)，只平移 A
    import math

    shift = math.log(100.0)
    ps = {
        "name": "bar-unit",
        "pressure_unit": "bar",
        "temperature_unit": "K",
        "components": [
            {"name": "n-pentane",
             "antoine": {"a": 13.818327 - shift, "b": 2477.075, "c": -39.945}},
            {"name": "n-hexane",
             "antoine": {"a": 13.897213 - shift, "b": 2739.2473, "c": -46.87}},
        ],
    }
    r = client.post("/property-sets", json=ps)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    job = client.post("/jobs", json=_job(pid=pid, pressure=0.70)).json()
    p = job["points"][0]
    assert p["beta"] == pytest.approx(0.294955, abs=1e-5)
