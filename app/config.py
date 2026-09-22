"""运行期配置：全部来自环境变量，带可用默认值。"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str = os.getenv("FLASH_DB_PATH", "/data/flash.db")
    seed_demo: bool = os.getenv("FLASH_SEED_DEMO", "1") not in ("0", "false", "False", "")
    http_host: str = os.getenv("FLASH_HOST", "0.0.0.0")
    http_port: int = int(os.getenv("FLASH_PORT", "8000"))
