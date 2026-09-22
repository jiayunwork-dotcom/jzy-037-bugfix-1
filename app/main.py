"""FastAPI 应用工厂与入口。

启动时打开持久化 SQLite、装配仓储/服务、（可选）预置示范物性与作业。
"""

from __future__ import annotations

import math

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import Settings
from app.errors import TypedError
from app.persistence.database import Database
from app.persistence.job_repository import JobRepository
from app.persistence.property_repository import PropertyRepository
from app.routers import jobs as jobs_router
from app.routers import properties as properties_router
from app.services.job_service import JobService
from app.services.property_service import PropertyService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(
        title="二元等温闪蒸核算服务",
        version="1.0.0",
        description=(
            "理想溶液/理想汽相二元等温闪蒸。汽化率由 Rachford–Rice 方程"
            "**二分法**在 (0,1) 内求根（括号容差 1e-10、残差容差 1e-8、"
            "上限 200 次迭代，全程不使用牛顿迭代）。以「核算作业」为单位提交"
            "并逐点落库；物性定义可独立登记、跨作业复用。"
        ),
    )
    app.state.settings = settings

    db = Database(settings.db_path)
    app.state.db = db
    property_repo = PropertyRepository(db)
    job_repo = JobRepository(db)
    property_service = PropertyService(property_repo)
    job_service = JobService(job_repo, property_service, property_repo)
    app.state.property_service = property_service
    app.state.job_service = job_service

    @app.exception_handler(TypedError)
    async def _typed_error_handler(request: Request, exc: TypedError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.to_response()},
        )

    # 把请求体结构/类型错误统一成带类型的错误信封
    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        def _sanitize(obj):
            # exc.errors() 的 input 可能回带 NaN/Infinity/任意对象，需清洗为可序列化值
            if isinstance(obj, float):
                return obj if math.isfinite(obj) else f"<non-finite:{obj!s}>"
            if isinstance(obj, dict):
                return {k: _sanitize(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [_sanitize(v) for v in obj]
            if isinstance(obj, (str, int, bool)) or obj is None:
                return obj
            return repr(obj)

        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "type": "REQUEST_VALIDATION_FAILED",
                    "message": "请求体校验失败",
                    "details": {"errors": _sanitize(exc.errors())},
                }
            },
        )

    # 兜底未预期错误，保证不向前端吐出堆栈
    @app.exception_handler(Exception)
    async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "type": "INTERNAL_ERROR",
                    "message": f"服务内部错误: {exc.__class__.__name__}",
                    "details": {},
                }
            },
        )

    app.include_router(properties_router.router)
    app.include_router(jobs_router.router)

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/", tags=["meta"])
    def root() -> dict:
        return {
            "service": "binary-isothermal-flash",
            "docs": "/docs",
            "solver": {
                "method": "bisection",
                "bracket_tol": 1e-10,
                "residual_tol": 1e-8,
                "max_iters": 200,
            },
        }

    if settings.seed_demo:
        from app.seed import seed_demo

        seed_demo(property_service, job_service)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    s = app.state.settings
    uvicorn.run("app.main:app", host=s.http_host, port=s.http_port)
