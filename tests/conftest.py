"""测试夹具：每个用例独立的内存数据库 + TestClient。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 必须在导入 app.main（其模块级 create_app()）前指定可写的库路径
os.environ.setdefault("FLASH_DB_PATH", ":memory:")
os.environ.setdefault("FLASH_SEED_DEMO", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture()
def client():
    app = create_app(Settings(db_path=":memory:", seed_demo=True))
    with TestClient(app) as c:
        yield c
    app.state.db.close()


@pytest.fixture()
def app_no_seed():
    app = create_app(Settings(db_path=":memory:", seed_demo=False))
    yield app
    app.state.db.close()


@pytest.fixture(scope="session")
def demo_property_payload():
    return {
        "name": "pentane-hexane",
        "pressure_unit": "kPa",
        "temperature_unit": "K",
        "components": [
            {"name": "n-pentane", "antoine": {"a": 13.818327, "b": 2477.075, "c": -39.945}},
            {"name": "n-hexane", "antoine": {"a": 13.897213, "b": 2739.2473, "c": -46.87}},
        ],
    }
