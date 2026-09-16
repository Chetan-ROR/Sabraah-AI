"""Admin APIs for managing mock train catalog + live status."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import require_api_key
from app.catalog import get_train_catalog

router = APIRouter(
    prefix="/api/v1/admin",
    dependencies=[Depends(require_api_key)],
    tags=["admin"],
)


class TrainUpsertRequest(BaseModel):
    source: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    departure_time: str = Field(min_length=1)
    arrival_time: str = Field(min_length=1)
    duration: str = Field(min_length=1)
    travel_class: str = Field(default="3A", alias="class")
    unit_price: float = Field(gt=0)
    available_seats: int = Field(ge=0)
    rac_seats: int = Field(default=0, ge=0)
    waiting_list: int = Field(default=0, ge=0)
    max_waiting_list: int = Field(default=120, ge=0)

    model_config = {"populate_by_name": True}


@router.get("/trains")
async def list_train_routes() -> dict[str, Any]:
    catalog = get_train_catalog()
    return {
        "routes": catalog.list_routes(),
        "fallback": catalog.get_fallback(),
        "cities": sorted(
            {
                *(r["source"] for r in catalog.list_routes()),
                *(r["destination"] for r in catalog.list_routes()),
            }
        ),
    }


@router.post("/trains/reload")
async def reload_catalog() -> dict[str, Any]:
    catalog = get_train_catalog()
    catalog.reload()
    return {"ok": True, "routes": len(catalog.list_routes())}


@router.get("/live-status")
async def admin_live_status(
    source: Optional[str] = Query(default=None),
    destination: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    from app.services.live_status import list_live_status

    rows = list_live_status(source=source, destination=destination)
    summary = {
        "BOARDING": 0,
        "RUNNING": 0,
        "SCHEDULED": 0,
        "ARRIVED": 0,
        "AVAILABLE": 0,
        "RAC": 0,
        "WL": 0,
        "NOT_AVAILABLE": 0,
    }
    for row in rows:
        ls = str(row.get("live_status") or "UNKNOWN")
        if ls in summary:
            summary[ls] += 1
        av = str(row.get("availability_status") or "")
        if av in summary:
            summary[av] += 1
    return {
        "count": len(rows),
        "summary": summary,
        "trains": rows,
        "provider": "MOCK",
    }


@router.post("/trains")
async def upsert_train(body: TrainUpsertRequest) -> dict[str, Any]:
    catalog = get_train_catalog()
    train = catalog.upsert_train(
        body.source,
        body.destination,
        {
            "id": body.id,
            "name": body.name,
            "departure_time": body.departure_time,
            "arrival_time": body.arrival_time,
            "duration": body.duration,
            "class": body.travel_class,
            "unit_price": body.unit_price,
            "available_seats": body.available_seats,
            "rac_seats": body.rac_seats,
            "waiting_list": body.waiting_list,
            "max_waiting_list": body.max_waiting_list,
        },
    )
    return {
        "ok": True,
        "source": body.source.title(),
        "destination": body.destination.title(),
        "train": train,
    }


@router.delete("/trains/{source}/{destination}/{train_id}")
async def delete_train(
    source: str,
    destination: str,
    train_id: str,
) -> dict[str, Any]:
    catalog = get_train_catalog()
    deleted = catalog.delete_train(source, destination, train_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Train not found on that route.",
        )
    return {"ok": True, "deleted": train_id}
