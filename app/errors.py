"""带类型的业务错误。

所有可预期的输入/资源错误都抛出 :class:`TypedError`，由 FastAPI 异常处理器
统一渲染为 ``{"error": {"type": ..., "message": ..., "details": ...}}``，
避免服务崩溃或把非法输入静默地算成近似解。
"""

from __future__ import annotations

from typing import Any


class TypedError(Exception):
    """带机器可读类型码的业务异常基类。"""

    error_type: str = "INTERNAL_ERROR"
    status_code: int = 400

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_response(self) -> dict[str, Any]:
        return {
            "type": self.error_type,
            "message": self.message,
            "details": self.details,
        }


# ---- 物性定义相关 ----
class PropertyNotFoundError(TypedError):
    error_type = "PROPERTY_SET_NOT_FOUND"
    status_code = 404


class PropertyNameConflictError(TypedError):
    error_type = "PROPERTY_SET_NAME_CONFLICT"
    status_code = 409


class PropertyValidationError(TypedError):
    error_type = "PROPERTY_SET_VALIDATION_FAILED"
    status_code = 422


# ---- 作业相关 ----
class JobNotFoundError(TypedError):
    error_type = "JOB_NOT_FOUND"
    status_code = 404


class PointNotFoundError(TypedError):
    error_type = "POINT_NOT_FOUND"
    status_code = 404


class JobValidationError(TypedError):
    """作业（含其全部工况点）校验失败。

    details["errors"] 为逐点/逐字段的错误列表，每个元素都带 ``type``。
    """

    error_type = "JOB_VALIDATION_FAILED"
    status_code = 422


# ---- 求解内核相关（理论上对外部输入已提前拦截） ----
class FlashComputationError(TypedError):
    error_type = "FLASH_COMPUTATION_FAILED"
    status_code = 500
