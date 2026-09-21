"""Travel domain services."""

from datetime import date
from typing import Any, Optional

from fastapi import HTTPException, status

from app.providers import (
    BusProvider,
    FlightProvider,
    HotelProvider,
    PackageProvider,
    TrainProvider,
)
from app.repositories import BookingRepository
from app.schemas import (
    BookingResponse,
    BusSearchRequest,
    CancelBookingRequest,
    CreateBookingRequest,
    FlightSearchRequest,
    HotelSearchRequest,
    PackagePlan,
    PackagePlanRequest,
    PackageSearchRequest,
    SearchResponse,
    TrainSearchRequest,
)


# Approximate catalog prices for booking totals (MOCK).
_MOCK_PRICES: dict[str, float] = {
    "TRAIN-001": 1850,
    "TRAIN-002": 1620,
    "TRAIN-003": 2450,
    "TRAIN-KND-IND-001": 890,
    "TRAIN-KND-IND-002": 420,
    "TRAIN-KND-IND-003": 280,
    "TRAIN-IND-KND-001": 890,
    "TRAIN-IND-KND-002": 420,
    "TRAIN-IND-KND-003": 280,
    "FLIGHT-001": 5200,
    "FLIGHT-002": 4800,
    "FLIGHT-003": 3900,
    "HOTEL-001": 4500,
    "HOTEL-002": 2800,
    "HOTEL-003": 1600,
    "PKG-001": 18999,
    "PKG-002": 12999,
    "PLAN-001": 25000,
}


class TravelService:
    def __init__(
        self,
        train_provider: TrainProvider,
        flight_provider: FlightProvider,
        hotel_provider: HotelProvider,
        package_provider: PackageProvider,
        booking_repo: BookingRepository,
        bus_provider: Optional[BusProvider] = None,
    ) -> None:
        self._trains = train_provider
        self._flights = flight_provider
        self._hotels = hotel_provider
        self._packages = package_provider
        self._buses = bus_provider
        self._bookings = booking_repo

    async def search_trains(self, request: TrainSearchRequest) -> SearchResponse:
        from app.services.extras import (
            enrich_train_row,
            sort_trains_by_preference,
            wishlist_list,
        )

        results = await self._trains.search(
            source=request.source,
            destination=request.destination,
            departure_date=request.departure_date,
            passengers=request.passengers,
            travel_class=request.travel_class,
        )
        wish_ids = set()
        if request.session_id:
            wish_ids = {
                str(w.get("id"))
                for w in wishlist_list(request.session_id).get("wishlist", [])
            }
        outbound = []
        for r in results:
            row = enrich_train_row(r.model_dump(by_alias=True))
            row["wishlisted"] = str(row.get("id")) in wish_ids
            row["leg"] = "outbound"
            outbound.append(row)
        if request.preference:
            outbound = sort_trains_by_preference(outbound, request.preference)

        combined = list(outbound)
        trip_type = (request.trip_type or "").lower()
        if request.return_date or trip_type == "round_trip":
            ret_date = request.return_date or request.departure_date
            back = await self._trains.search(
                source=request.destination,
                destination=request.source,
                departure_date=ret_date,
                passengers=request.passengers,
                travel_class=request.travel_class,
            )
            inbound = []
            for r in back:
                row = enrich_train_row(r.model_dump(by_alias=True))
                row["wishlisted"] = str(row.get("id")) in wish_ids
                row["leg"] = "return"
                inbound.append(row)
            if request.preference:
                inbound = sort_trains_by_preference(inbound, request.preference)
            combined.extend(inbound)

        provider_name = "MOCK"
        if results:
            provider_name = str(getattr(results[0], "provider", None) or "MOCK")
        elif combined:
            provider_name = str(combined[0].get("provider") or "MOCK")

        return SearchResponse(
            results=combined,
            provider=provider_name,
            count=len(combined),
        )

    async def search_flights(self, request: FlightSearchRequest) -> SearchResponse:
        results = await self._flights.search(
            source=request.source,
            destination=request.destination,
            departure_date=request.departure_date,
            passengers=request.passengers,
            travel_class=request.travel_class,
            return_date=request.return_date,
        )
        provider_name = "MOCK"
        if results:
            provider_name = str(getattr(results[0], "provider", None) or "MOCK")
        return SearchResponse(
            results=[r.model_dump() for r in results],
            provider=provider_name,
            count=len(results),
        )

    async def search_buses(self, request: BusSearchRequest) -> SearchResponse:
        if self._buses is None:
            return SearchResponse(results=[], provider="MOCK", count=0)
        results = await self._buses.search(
            source=request.source,
            destination=request.destination,
            departure_date=request.departure_date,
            passengers=request.passengers,
            bus_type=request.bus_type,
        )
        return SearchResponse(
            results=[r.model_dump() for r in results],
            provider="MOCK",
            count=len(results),
        )

    async def search_hotels(self, request: HotelSearchRequest) -> SearchResponse:
        results = await self._hotels.search(
            city=request.city,
            check_in=request.check_in,
            check_out=request.check_out,
            guests=request.guests,
            rooms=request.rooms,
            budget_max=request.budget_max,
        )
        return SearchResponse(
            results=[r.model_dump() for r in results],
            provider="MOCK",
            count=len(results),
        )

    async def search_packages(self, request: PackageSearchRequest) -> SearchResponse:
        results = await self._packages.search(
            source=request.source,
            destination=request.destination,
            departure_date=request.departure_date,
            return_date=request.return_date,
            passengers=request.passengers,
            budget_max=request.budget_max,
        )
        return SearchResponse(
            results=[r.model_dump() for r in results],
            provider="MOCK",
            count=len(results),
        )

    async def plan_package(self, request: PackagePlanRequest) -> PackagePlan:
        return await self._packages.plan(
            source=request.source,
            destination=request.destination,
            departure_date=request.departure_date,
            return_date=request.return_date,
            passengers=request.passengers,
            include_hotel=request.include_hotel,
            transport_preference=request.transport_preference,
            budget_max=request.budget_max,
        )

    async def create_booking(self, request: CreateBookingRequest) -> BookingResponse:
        if not request.confirmed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Booking requires explicit confirmation. "
                    "Set confirmed=true only after the user says yes."
                ),
            )
        unit = _MOCK_PRICES.get(request.item_id)
        if unit is None:
            from app.catalog import get_train_catalog
            from app.catalog.buses import get_bus_catalog

            unit = get_train_catalog().price_for(request.item_id)
            if unit is None:
                unit = get_bus_catalog().price_for(request.item_id)
        if unit is None:
            unit = 1000.0
        total = float(unit) * request.passenger_count

        reservation: dict[str, Any] = {}
        if request.item_type == "train":
            from app.catalog import get_train_catalog
            from app.catalog.trains import availability_for_passengers

            catalog = get_train_catalog()
            found = catalog.find_train(request.item_id)
            if found:
                probe = availability_for_passengers(found, request.passenger_count)
                if not probe.get("can_book"):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=probe.get("message") or "Not available",
                    )
                if probe.get("needs_waitlist_confirm") and not request.metadata.get(
                    "accept_waitlist"
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            probe.get("message")
                            + " Ask the user clearly: book on RAC/waiting list? "
                            "Only then set accept_waitlist=true."
                        ),
                    )
            names = list(request.metadata.get("traveler_names") or [])
            if not names and request.passenger_name:
                names = [request.passenger_name]
            reservation = catalog.reserve_seats(
                request.item_id, request.passenger_count, names
            )
            if not reservation.get("ok"):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=reservation.get("message")
                    or "No seats available for this train.",
                )

        record = await self._bookings.create(request, total_price=total)
        meta = dict(record.metadata or {})
        if request.item_type == "train":
            from app.catalog import get_train_catalog
            from app.services.extras import enrich_train_row
            from app.services.live_status import live_status_for_train

            names = list(request.metadata.get("traveler_names") or [])
            if not names and request.passenger_name:
                names = [request.passenger_name]
            catalog = get_train_catalog()
            train_row = catalog.find_train(request.item_id) or {
                "id": request.item_id,
                "name": request.metadata.get("train_name") or request.item_id,
                "source": request.metadata.get("source") or "Origin",
                "destination": request.metadata.get("destination") or "Destination",
                "departure_time": request.metadata.get("departure_time"),
                "arrival_time": request.metadata.get("arrival_time"),
                "duration": request.metadata.get("duration"),
                "class": request.metadata.get("travel_class"),
            }
            inv = reservation.get("inventory") or {}
            train_row = {**train_row, **inv}
            stub = enrich_train_row(
                {
                    **train_row,
                    "source": train_row.get("source")
                    or request.metadata.get("source")
                    or "Origin",
                    "destination": train_row.get("destination")
                    or request.metadata.get("destination")
                    or "Destination",
                }
            )
            live = live_status_for_train(request.item_id) or {}
            seats = reservation.get("seats") or []
            meta.update(
                {
                    "train_id": stub.get("id"),
                    "train_name": stub.get("name"),
                    "source": stub.get("source"),
                    "destination": stub.get("destination"),
                    "departure_date": request.metadata.get("departure_date"),
                    "departure_time": stub.get("departure_time"),
                    "arrival_time": stub.get("arrival_time"),
                    "duration": stub.get("duration"),
                    "travel_class": stub.get("class") or stub.get("travel_class"),
                    "platform": stub.get("platform"),
                    "source_station": stub.get("source_station"),
                    "destination_station": stub.get("destination_station"),
                    "arrival_platform": (stub.get("stops") or [{}])[-1].get("platform"),
                    "stops": stub.get("stops") or [],
                    "connections_note": stub.get("connections_note"),
                    "seats": seats,
                    "booking_quota": reservation.get("booking_quota"),
                    "availability_status": reservation.get("availability_status"),
                    "availability_message": reservation.get("message"),
                    "traveler_names": names,
                    "passenger_count": request.passenger_count,
                    "meal_preference": request.metadata.get("meal_preference"),
                    "allergies": request.metadata.get("allergies"),
                    "total_price": total,
                    "currency": "INR",
                    "live_status": live.get("live_status"),
                    "live_status_message": live.get("status_message"),
                    "delay_minutes": live.get("delay_minutes"),
                    "current_station": live.get("current_station"),
                }
            )
            record = record.model_copy(update={"metadata": meta})
            self._bookings._bookings[record.booking_id] = record  # type: ignore[attr-defined]
            from app.services.seat_queue import register_booking_queue

            register_booking_queue(
                str(request.item_id),
                record.booking_id,
                str(reservation.get("booking_quota") or "GN"),
            )
        return BookingResponse(**record.model_dump())

    async def get_booking(self, booking_id: str) -> BookingResponse:
        record = await self._bookings.get(booking_id)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Booking {booking_id} not found",
            )
        return BookingResponse(**record.model_dump())

    async def cancel_booking(
        self, booking_id: str, request: CancelBookingRequest
    ) -> BookingResponse:
        if not request.confirmed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Cancellation requires explicit confirmation. "
                    "Set confirmed=true only after the user says yes."
                ),
            )
        existing = await self._bookings.get(booking_id)
        record = await self._bookings.cancel(booking_id, reason=request.reason)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Booking {booking_id} not found",
            )
        if existing and existing.item_type == "train":
            from app.services.seat_queue import release_on_cancel

            meta = dict(existing.metadata or {})
            release = release_on_cancel(
                train_id=str(existing.item_id),
                booking_id=booking_id,
                passenger_count=int(existing.passenger_count or 1),
                seats=list(meta.get("seats") or []),
                booking_quota=str(meta.get("booking_quota") or "GN"),
                booking_repo=self._bookings,
            )
            new_meta = dict(record.metadata or {})
            new_meta["seat_release"] = release
            record = record.model_copy(update={"metadata": new_meta})
            self._bookings._bookings[record.booking_id] = record  # type: ignore[attr-defined]
        return BookingResponse(**record.model_dump())
