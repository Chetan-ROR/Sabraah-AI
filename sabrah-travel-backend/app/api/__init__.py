"""Travel API routes."""

import logging
import time

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_travel_service, require_api_key
from app.schemas import (
    BookingResponse,
    BusSearchRequest,
    CancelBookingRequest,
    CharterRequest,
    CreateBookingRequest,
    FeedbackRequest,
    FlightSearchRequest,
    HotelSearchRequest,
    LocalTransportSearchRequest,
    PackagePlan,
    PackagePlanRequest,
    PackageSearchRequest,
    RefundRequest,
    SearchResponse,
    SupportEscalateRequest,
    TrainSearchRequest,
    WishlistAddRequest,
)
from app.services import TravelService
from app.services import extras as extras_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


@router.post("/trains/search", response_model=SearchResponse)
async def search_trains(
    body: TrainSearchRequest,
    service: TravelService = Depends(get_travel_service),
) -> SearchResponse:
    started = time.perf_counter()
    result = await service.search_trains(body)
    logger.info(
        "trains.search source=%s destination=%s count=%s latency_ms=%.1f",
        body.source,
        body.destination,
        result.count,
        (time.perf_counter() - started) * 1000,
    )
    return result


@router.post("/flights/search", response_model=SearchResponse)
async def search_flights(
    body: FlightSearchRequest,
    service: TravelService = Depends(get_travel_service),
) -> SearchResponse:
    started = time.perf_counter()
    result = await service.search_flights(body)
    logger.info(
        "flights.search source=%s destination=%s count=%s latency_ms=%.1f",
        body.source,
        body.destination,
        result.count,
        (time.perf_counter() - started) * 1000,
    )
    return result


@router.post("/buses/search", response_model=SearchResponse)
async def search_buses(
    body: BusSearchRequest,
    service: TravelService = Depends(get_travel_service),
) -> SearchResponse:
    started = time.perf_counter()
    result = await service.search_buses(body)
    logger.info(
        "buses.search source=%s destination=%s count=%s latency_ms=%.1f",
        body.source,
        body.destination,
        result.count,
        (time.perf_counter() - started) * 1000,
    )
    return result


@router.post("/hotels/search", response_model=SearchResponse)
async def search_hotels(
    body: HotelSearchRequest,
    service: TravelService = Depends(get_travel_service),
) -> SearchResponse:
    started = time.perf_counter()
    result = await service.search_hotels(body)
    logger.info(
        "hotels.search city=%s count=%s latency_ms=%.1f",
        body.city,
        result.count,
        (time.perf_counter() - started) * 1000,
    )
    return result


@router.post("/packages/search", response_model=SearchResponse)
async def search_packages(
    body: PackageSearchRequest,
    service: TravelService = Depends(get_travel_service),
) -> SearchResponse:
    started = time.perf_counter()
    result = await service.search_packages(body)
    logger.info(
        "packages.search destination=%s count=%s latency_ms=%.1f",
        body.destination,
        result.count,
        (time.perf_counter() - started) * 1000,
    )
    return result


@router.post("/packages/plan", response_model=PackagePlan)
async def plan_package(
    body: PackagePlanRequest,
    service: TravelService = Depends(get_travel_service),
) -> PackagePlan:
    started = time.perf_counter()
    result = await service.plan_package(body)
    logger.info(
        "packages.plan destination=%s latency_ms=%.1f",
        body.destination,
        (time.perf_counter() - started) * 1000,
    )
    return result


@router.post("/bookings", response_model=BookingResponse)
async def create_booking(
    body: CreateBookingRequest,
    service: TravelService = Depends(get_travel_service),
) -> BookingResponse:
    started = time.perf_counter()
    result = await service.create_booking(body)
    logger.info(
        "bookings.create booking_id=%s item_id=%s latency_ms=%.1f",
        result.booking_id,
        body.item_id,
        (time.perf_counter() - started) * 1000,
    )
    return result


@router.get("/bookings/{booking_id}", response_model=BookingResponse)
async def get_booking(
    booking_id: str,
    service: TravelService = Depends(get_travel_service),
) -> BookingResponse:
    return await service.get_booking(booking_id)


@router.post("/bookings/{booking_id}/cancel", response_model=BookingResponse)
async def cancel_booking(
    booking_id: str,
    body: CancelBookingRequest,
    service: TravelService = Depends(get_travel_service),
) -> BookingResponse:
    started = time.perf_counter()
    result = await service.cancel_booking(booking_id, body)
    logger.info(
        "bookings.cancel booking_id=%s latency_ms=%.1f",
        booking_id,
        (time.perf_counter() - started) * 1000,
    )
    return result


@router.get("/trains/live-status")
async def trains_live_status(
    source: Optional[str] = Query(default=None),
    destination: Optional[str] = Query(default=None),
    train_id: Optional[str] = Query(default=None),
) -> dict:
    from app.services.live_status import list_live_status

    rows = list_live_status(
        source=source, destination=destination, train_id=train_id
    )
    return {"count": len(rows), "trains": rows, "provider": "MOCK"}


@router.get("/trains/{train_id}/live-status")
async def train_live_status(train_id: str) -> dict:
    from app.services.live_status import live_status_for_train

    row = live_status_for_train(train_id)
    if not row:
        return {"error": "not_found", "message": f"Train {train_id} not found"}
    return row


@router.get("/trains/{train_id}/details")
async def train_details(train_id: str) -> dict:
    from app.catalog import get_train_catalog
    from app.catalog.trains import availability_for_passengers
    from app.services.live_status import live_status_for_train

    catalog = get_train_catalog()
    found = catalog.find_train(train_id)
    if not found:
        stub = {
            "id": train_id,
            "name": train_id,
            "source": "Origin",
            "destination": "Destination",
            "departure_time": "—",
            "arrival_time": "—",
            "duration": "—",
        }
        return extras_svc.enrich_train_row(stub)
    avail = availability_for_passengers(found, 1)
    enriched = extras_svc.enrich_train_row(found)
    live = live_status_for_train(train_id) or {}
    return {
        **enriched,
        **avail,
        "live": live,
    }


@router.post("/local-transport/search", response_model=SearchResponse)
async def search_local_transport(body: LocalTransportSearchRequest) -> SearchResponse:
    rows = extras_svc.search_local_transport(
        city=body.city,
        pickup=body.pickup,
        dropoff=body.dropoff,
        transport_type=body.transport_type,
    )
    return SearchResponse(results=rows, provider="MOCK", count=len(rows))


@router.get("/weather/{city}")
async def weather(city: str) -> dict:
    return extras_svc.weather_for_city(city)


@router.post("/wishlist")
async def add_wishlist(body: WishlistAddRequest) -> dict:
    train = {
        "id": body.train_id,
        "name": body.name,
        "source": body.source,
        "destination": body.destination,
        **body.metadata,
    }
    return extras_svc.wishlist_add(body.session_id, train)


@router.get("/wishlist")
async def get_wishlist(session_id: str = Query(...)) -> dict:
    return extras_svc.wishlist_list(session_id)


@router.post("/charter/request")
async def charter_request(body: CharterRequest) -> dict:
    return extras_svc.create_charter_request(body.model_dump())


@router.post("/support/escalate")
async def support_escalate(body: SupportEscalateRequest) -> dict:
    return extras_svc.escalate_to_sales(body.model_dump())


@router.post("/support/refund")
async def support_refund(body: RefundRequest) -> dict:
    if not body.confirmed:
        return {
            "error": "confirmation_required",
            "message": "Ask the user to confirm the refund request first.",
        }
    return extras_svc.request_refund(body.model_dump())


@router.post("/feedback")
async def feedback(body: FeedbackRequest) -> dict:
    return extras_svc.submit_feedback(body.model_dump())
