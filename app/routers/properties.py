"""物性定义登记/查询路由。"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.schemas import PropertySetCreate, PropertySetOut

router = APIRouter(prefix="/property-sets", tags=["properties"])


@router.post("", response_model=PropertySetOut, status_code=status.HTTP_201_CREATED)
def create_property_set(payload: PropertySetCreate, request: Request) -> dict:
    record = request.app.state.property_service.register(payload)
    return request.app.state.property_service.serialize(record)


@router.get("", response_model=list[PropertySetOut])
def list_property_sets(request: Request) -> list[dict]:
    return [
        request.app.state.property_service.serialize(r)
        for r in request.app.state.property_service.list_all()
    ]


@router.get("/{property_set_id}", response_model=PropertySetOut)
def get_property_set(property_set_id: str, request: Request) -> dict:
    record = request.app.state.property_service.get(property_set_id)
    return request.app.state.property_service.serialize(record)
