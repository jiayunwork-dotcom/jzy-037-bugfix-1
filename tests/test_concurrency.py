"""并发隔离与持久化：作业互不覆盖、进程重启后数据仍可取回。"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

DEMO_ID = "ps-demo-pentane-hexane"


def test_concurrent_job_submissions_do_not_cross_talk(client):
    pressures = [55.0 + 0.5 * i for i in range(24)]

    def submit(p):
        body = {
            "name": f"conc-{p}",
            "property_set_id": DEMO_ID,
            "points": [
                {"temperature": 313.15, "pressure": q, "feed": [0.5, 0.5]}
                for q in (p, p + 1.0, 90.0)
            ],
        }
        r = client.post("/jobs", json=body)
        assert r.status_code == 201, r.text
        return r.json()

    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(submit, pressures))

    ids = [j["id"] for j in jobs]
    assert len(set(ids)) == len(ids)  # 作业号唯一

    # 逐作业回读，结果必须与提交压力一一对应、不串号
    for p, job in zip(pressures, jobs):
        got = client.get(f"/jobs/{job['id']}").json()
        assert got["name"] == f"conc-{p}"
        assert len(got["points"]) == 3
        assert got["points"][0]["pressure"] == p
        assert got["points"][1]["pressure"] == p + 1.0
        assert got["points"][2]["regime"] == "subcooled_liquid"


def test_concurrent_submissions_with_distinct_inline_properties(client, demo_property_payload):
    def submit(i):
        body = {
            "name": f"inline-{i}",
            "property_set": dict(demo_property_payload, name=f"ps-inline-{i}"),
            "points": [{"temperature": 313.15, "pressure": 60.0 + i, "feed": [0.5, 0.5]}],
        }
        r = client.post("/jobs", json=body)
        assert r.status_code == 201, r.text
        return r.json()

    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(submit, range(16)))

    pids = {j["property_set_id"] for j in jobs}
    assert len(pids) == 16
    for j in jobs:
        again = client.get(f"/jobs/{j['id']}").json()
        assert again["points"][0]["pressure"] == j["points"][0]["pressure"]


def test_solver_intermediate_state_is_not_shared():
    # 同一进程内多线程直接驱动求解内核：每点结果只依赖入参
    from app.thermo.flash import flash

    cases = [((0.5, 0.5), (1.1 + 0.01 * i, 0.9 - 0.01 * i)) for i in range(40)]
    out = [None] * len(cases)

    def work(i):
        out[i] = flash(*cases[i])

    threads = [threading.Thread(target=work, args=(i,)) for i in range(len(cases))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for (z, K), r in zip(cases, out):
        if r.regime == "two_phase":
            b = r.beta
            for c in range(2):
                assert abs((1 - b) * r.liquid[c] + b * r.vapor[c] - z[c]) < 1e-8


def test_results_persist_across_app_restart(tmp_path):
    db = str(tmp_path / "persist.db")
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    app1 = create_app(Settings(db_path=db, seed_demo=False))
    c1 = TestClient(app1)
    ps = {
        "name": "persist-ps",
        "pressure_unit": "kPa",
        "temperature_unit": "K",
        "components": [
            {"name": "n-pentane", "antoine": {"a": 13.818327, "b": 2477.075, "c": -39.945}},
            {"name": "n-hexane", "antoine": {"a": 13.897213, "b": 2739.2473, "c": -46.87}},
        ],
    }
    pid = c1.post("/property-sets", json=ps).json()["id"]
    job = c1.post(
        "/jobs",
        json={
            "name": "persist-job",
            "property_set_id": pid,
            "points": [{"temperature": 313.15, "pressure": 70.0, "feed": [0.5, 0.5]}],
        },
    ).json()
    jid, beta = job["id"], job["points"][0]["beta"]
    app1.state.db.close()

    app2 = create_app(Settings(db_path=db, seed_demo=False))
    c2 = TestClient(app2)
    got = c2.get(f"/jobs/{jid}").json()
    assert got["points"][0]["beta"] == beta
    assert c2.get(f"/property-sets/{pid}").json()["name"] == "persist-ps"
    app2.state.db.close()
