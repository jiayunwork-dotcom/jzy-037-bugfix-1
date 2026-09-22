"""核算作业提交与结果取回路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.schemas import JobCreate, JobOut, PointResult

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
def submit_job(payload: JobCreate, request: Request) -> dict:
    return request.app.state.job_service.submit(payload)


@router.get("", response_model=list[dict])
def list_jobs(request: Request) -> list[dict]:
    return request.app.state.job_service.list_jobs()


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, request: Request) -> dict:
    return request.app.state.job_service.get_job(job_id)


@router.get("/{job_id}/points/{point_ref}", response_model=PointResult)
def get_point(job_id: str, point_ref: str, request: Request) -> dict:
    return request.app.state.job_service.get_point(job_id, point_ref)


@router.get("/{job_id}/points", response_model=list[PointResult])
def list_points(job_id: str, request: Request) -> list[dict]:
    """某作业的全部工况点结果（等价于取作业中的 points 字段）。"""
    return request.app.state.job_service.get_job(job_id)["points"]
