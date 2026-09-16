"""Travel provider interfaces and mock implementations."""

from abc import ABC, abstractmethod
from datetime import date, timedelta
from typing import Any, Optional

from app.schemas import (
    BusResult,
    FlightResult,
    HotelResult,
    PackagePlan,
    PackageResult,
    TrainResult,
)


class TrainProvider(ABC):
    @abstractmethod
    async def search(
        self,
        source: str,
        destination: str,
        departure_date: date,
        passengers: int = 1,
        travel_class: Optional[str] = None,
    ) -> list[TrainResult]:
        raise NotImplementedError


class FlightProvider(ABC):
    @abstractmethod
    async def search(
        self,
        source: str,
        destination: str,
        departure_date: date,
        passengers: int = 1,
        travel_class: Optional[str] = "economy",
    ) -> list[FlightResult]:
        raise NotImplementedError


class BusProvider(ABC):
    @abstractmethod
    async def search(
        self,
        source: str,
        destination: str,
        departure_date: date,
        passengers: int = 1,
        bus_type: Optional[str] = None,
    ) -> list[BusResult]:
        raise NotImplementedError


class HotelProvider(ABC):
    @abstractmethod
    async def search(
        self,
        city: str,
        check_in: date,
        check_out: date,
        guests: int = 2,
        rooms: int = 1,
        budget_max: Optional[float] = None,
    ) -> list[HotelResult]:
        raise NotImplementedError


class PackageProvider(ABC):
    @abstractmethod
    async def search(
        self,
        source: str,
        destination: str,
        departure_date: date,
        return_date: Optional[date] = None,
        passengers: int = 2,
        budget_max: Optional[float] = None,
    ) -> list[PackageResult]:
        raise NotImplementedError

    @abstractmethod
    async def plan(
        self,
        source: str,
        destination: str,
        departure_date: date,
        return_date: date,
        passengers: int = 2,
        include_hotel: bool = True,
        transport_preference: Optional[str] = None,
        budget_max: Optional[float] = None,
    ) -> PackagePlan:
        raise NotImplementedError


class MockTrainProvider(TrainProvider):
    """MOCK provider — reads routes from TrainCatalog (admin-editable)."""

    def __init__(self, catalog: Optional[Any] = None) -> None:
        from app.catalog import get_train_catalog

        self._catalog = catalog or get_train_catalog()

    async def search(
        self,
        source: str,
        destination: str,
        departure_date: date,
        passengers: int = 1,
        travel_class: Optional[str] = None,
    ) -> list[TrainResult]:
        rows = self._catalog.get_trains_for_route(source, destination)
        results: list[TrainResult] = []
        for row in rows:
            cls = travel_class or row["class"]
            from app.catalog.trains import availability_for_passengers

            avail = availability_for_passengers(row, passengers)
            results.append(
                TrainResult.model_validate(
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "source": source.title(),
                        "destination": destination.title(),
                        "departure_time": row["departure_time"],
                        "arrival_time": row["arrival_time"],
                        "duration": row["duration"],
                        "class": cls,
                        "price": float(row["unit_price"]) * passengers,
                        "unit_price": float(row["unit_price"]),
                        "currency": "INR",
                        "available_seats": int(avail["available_seats"]),
                        "rac_seats": int(avail["rac_seats"]),
                        "waiting_list": int(avail["waiting_list"]),
                        "availability_status": avail["availability_status"],
                        "availability_message": avail["message"],
                        "can_book": avail["can_book"],
                        "needs_waitlist_confirm": avail["needs_waitlist_confirm"],
                        "provider": "MOCK",
                    }
                )
            )
        return results


class MockBusProvider(BusProvider):
    """MOCK bus provider — supports direct and connecting legs from catalog."""

    def __init__(self, catalog: Optional[Any] = None) -> None:
        from app.catalog.buses import get_bus_catalog

        self._catalog = catalog or get_bus_catalog()

    async def search(
        self,
        source: str,
        destination: str,
        departure_date: date,
        passengers: int = 1,
        bus_type: Optional[str] = None,
    ) -> list[BusResult]:
        rows = self._catalog.get_buses_for_route(source, destination)
        results: list[BusResult] = []
        for row in rows:
            if bus_type and bus_type.lower() not in str(row.get("bus_type", "")).lower():
                continue
            legs = row.get("legs") or [
                {
                    "from": source.title(),
                    "to": destination.title(),
                    "departure_time": row["departure_time"],
                    "arrival_time": row["arrival_time"],
                    "bus_name": row["name"],
                }
            ]
            results.append(
                BusResult(
                    id=row["id"],
                    name=row["name"],
                    operator=row.get("operator") or "Demo Bus Co",
                    source=source.title(),
                    destination=destination.title(),
                    departure_time=row["departure_time"],
                    arrival_time=row["arrival_time"],
                    duration=row["duration"],
                    bus_type=row.get("bus_type") or "AC Seater",
                    price=float(row["unit_price"]) * passengers,
                    currency="INR",
                    available_seats=int(row["available_seats"]),
                    legs=legs,
                    provider="MOCK",
                )
            )
        return results


class MockFlightProvider(FlightProvider):
    """MOCK provider — replace with RealFlightProvider later."""

    async def search(
        self,
        source: str,
        destination: str,
        departure_date: date,
        passengers: int = 1,
        travel_class: Optional[str] = "economy",
    ) -> list[FlightResult]:
        return [
            FlightResult(
                id="FLIGHT-001",
                airline="Demo Airways",
                source=source.title(),
                destination=destination.title(),
                departure_time="10:30",
                arrival_time="12:45",
                duration="2h 15m",
                price=5200 * passengers,
                currency="INR",
                travel_class=travel_class or "economy",
                available_seats=24,
                provider="MOCK",
            ),
            FlightResult(
                id="FLIGHT-002",
                airline="SkyDemo Airlines",
                source=source.title(),
                destination=destination.title(),
                departure_time="18:15",
                arrival_time="20:40",
                duration="2h 25m",
                price=4800 * passengers,
                currency="INR",
                travel_class=travel_class or "economy",
                available_seats=31,
                provider="MOCK",
            ),
            FlightResult(
                id="FLIGHT-003",
                airline="IndiGo Demo",
                source=source.title(),
                destination=destination.title(),
                departure_time="07:05",
                arrival_time="09:20",
                duration="2h 15m",
                price=3900 * passengers,
                currency="INR",
                travel_class=travel_class or "economy",
                available_seats=15,
                provider="MOCK",
            ),
        ]


class MockHotelProvider(HotelProvider):
    """MOCK provider — replace with RealHotelProvider later."""

    async def search(
        self,
        city: str,
        check_in: date,
        check_out: date,
        guests: int = 2,
        rooms: int = 1,
        budget_max: Optional[float] = None,
    ) -> list[HotelResult]:
        nights = max((check_out - check_in).days, 1)
        hotels = [
            HotelResult(
                id="HOTEL-001",
                name="Sabrah Grand Hotel",
                city=city.title(),
                check_in=check_in,
                check_out=check_out,
                price_per_night=4500,
                total_price=4500 * nights * rooms,
                currency="INR",
                rating=4.5,
                amenities=["WiFi", "Breakfast", "Pool"],
                provider="MOCK",
            ),
            HotelResult(
                id="HOTEL-002",
                name="City Comfort Inn",
                city=city.title(),
                check_in=check_in,
                check_out=check_out,
                price_per_night=2800,
                total_price=2800 * nights * rooms,
                currency="INR",
                rating=4.1,
                amenities=["WiFi", "Breakfast"],
                provider="MOCK",
            ),
            HotelResult(
                id="HOTEL-003",
                name="Budget Stay Lodge",
                city=city.title(),
                check_in=check_in,
                check_out=check_out,
                price_per_night=1600,
                total_price=1600 * nights * rooms,
                currency="INR",
                rating=3.8,
                amenities=["WiFi"],
                provider="MOCK",
            ),
        ]
        if budget_max is not None:
            hotels = [h for h in hotels if h.total_price <= budget_max]
        return hotels


class MockPackageProvider(PackageProvider):
    """MOCK provider — replace with RealPackageProvider later."""

    async def search(
        self,
        source: str,
        destination: str,
        departure_date: date,
        return_date: Optional[date] = None,
        passengers: int = 2,
        budget_max: Optional[float] = None,
    ) -> list[PackageResult]:
        end = return_date or (departure_date + timedelta(days=3))
        nights = max((end - departure_date).days, 1)
        packages = [
            PackageResult(
                id="PKG-001",
                name=f"{destination.title()} Explorer",
                source=source.title(),
                destination=destination.title(),
                departure_date=departure_date,
                return_date=end,
                nights=nights,
                price=18999 * passengers,
                currency="INR",
                includes=["Flight", "Hotel", "Breakfast", "Airport transfer"],
                provider="MOCK",
            ),
            PackageResult(
                id="PKG-002",
                name=f"{destination.title()} Getaway",
                source=source.title(),
                destination=destination.title(),
                departure_date=departure_date,
                return_date=end,
                nights=nights,
                price=12999 * passengers,
                currency="INR",
                includes=["Train", "Hotel", "Breakfast"],
                provider="MOCK",
            ),
        ]
        if budget_max is not None:
            packages = [p for p in packages if p.price <= budget_max]
        return packages

    async def plan(
        self,
        source: str,
        destination: str,
        departure_date: date,
        return_date: date,
        passengers: int = 2,
        include_hotel: bool = True,
        transport_preference: Optional[str] = None,
        budget_max: Optional[float] = None,
    ) -> PackagePlan:
        transport_mode = (transport_preference or "flight").lower()
        transport: dict[str, Any] = {
            "mode": transport_mode,
            "option_id": "FLIGHT-001" if transport_mode == "flight" else "TRAIN-001",
            "estimated_price": 5200 * passengers
            if transport_mode == "flight"
            else 1850 * passengers,
        }
        hotel: Optional[dict[str, Any]] = None
        hotel_cost = 0.0
        if include_hotel:
            nights = max((return_date - departure_date).days, 1)
            hotel_cost = 4500 * nights
            hotel = {
                "option_id": "HOTEL-001",
                "name": "Sabrah Grand Hotel",
                "nights": nights,
                "estimated_price": hotel_cost,
            }
        total = transport["estimated_price"] + hotel_cost
        if budget_max is not None and total > budget_max:
            total = budget_max
        return PackagePlan(
            id="PLAN-001",
            summary=(
                f"MOCK plan from {source.title()} to {destination.title()} "
                f"with {transport_mode} transport"
                + (" and hotel" if include_hotel else "")
            ),
            source=source.title(),
            destination=destination.title(),
            departure_date=departure_date,
            return_date=return_date,
            transport=transport,
            hotel=hotel,
            estimated_total=total,
            currency="INR",
            day_plan=[
                f"Day 1: Arrive in {destination.title()}, check in",
                f"Day 2: Explore popular sights in {destination.title()}",
                f"Day 3: Local experiences and free time",
                f"Final day: Checkout and return to {source.title()}",
            ],
            provider="MOCK",
        )


class RealTrainProvider(TrainProvider):
    """Live trains from Super Travel (api-repository); seats/booking stay local mock."""

    def __init__(self, *, fallback_to_mock: bool = True) -> None:
        self._fallback_to_mock = fallback_to_mock
        self._mock = MockTrainProvider()

    async def search(
        self,
        source: str,
        destination: str,
        departure_date: date,
        passengers: int = 1,
        travel_class: Optional[str] = None,
    ) -> list[TrainResult]:
        from app.catalog import get_train_catalog
        from app.catalog.trains import availability_for_passengers
        from app.providers.super_travel import (
            fetch_train_list,
            map_train_row,
            resolve_station_code_fast,
        )

        try:
            origin = await resolve_station_code_fast(source)
            dest = await resolve_station_code_fast(destination)
            if not origin or not dest:
                raise RuntimeError(
                    f"Could not resolve stations for {source!r} → {destination!r}"
                )
            raw_rows = await fetch_train_list(
                origin=origin,
                destination=dest,
                journey_date=departure_date,
            )
            if not raw_rows:
                raise RuntimeError(
                    f"No live trains for {origin}→{dest} on {departure_date}"
                )

            catalog = get_train_catalog()
            results: list[TrainResult] = []
            for raw in raw_rows:
                mapped = map_train_row(
                    raw,
                    source_label=source,
                    destination_label=destination,
                    passengers=passengers,
                    travel_class=travel_class,
                )
                existing = catalog.find_train(mapped["id"])
                if existing:
                    # Keep seat inventory from prior bookings; refresh schedule/price.
                    mapped["available_seats"] = int(existing.get("available_seats") or 0)
                    mapped["rac_seats"] = int(existing.get("rac_seats") or 0)
                    mapped["waiting_list"] = int(existing.get("waiting_list") or 0)
                    mapped["max_waiting_list"] = int(
                        existing.get("max_waiting_list") or 120
                    )
                catalog.upsert_train(source, destination, mapped)
                avail = availability_for_passengers(mapped, passengers)
                results.append(
                    TrainResult.model_validate(
                        {
                            "id": mapped["id"],
                            "name": mapped["name"],
                            "source": mapped["source"],
                            "destination": mapped["destination"],
                            "departure_time": mapped["departure_time"],
                            "arrival_time": mapped["arrival_time"],
                            "duration": mapped["duration"],
                            "class": mapped["class"],
                            "price": float(mapped["unit_price"]) * passengers,
                            "unit_price": float(mapped["unit_price"]),
                            "currency": "INR",
                            "available_seats": int(avail["available_seats"]),
                            "rac_seats": int(avail["rac_seats"]),
                            "waiting_list": int(avail["waiting_list"]),
                            "availability_status": avail["availability_status"],
                            "availability_message": avail["message"],
                            "can_book": avail["can_book"],
                            "needs_waitlist_confirm": avail["needs_waitlist_confirm"],
                            "provider": "SUPER_TRAVEL",
                        }
                    )
                )
            return results
        except Exception as exc:  # noqa: BLE001 — soft-fail to mock catalog
            import logging

            logging.getLogger(__name__).warning(
                "RealTrainProvider falling back to mock: %s", exc
            )
            if not self._fallback_to_mock:
                raise
            return await self._mock.search(
                source=source,
                destination=destination,
                departure_date=departure_date,
                passengers=passengers,
                travel_class=travel_class,
            )


class RealFlightProvider(FlightProvider):
    async def search(
        self,
        source: str,
        destination: str,
        departure_date: date,
        passengers: int = 1,
        travel_class: Optional[str] = "economy",
    ) -> list[FlightResult]:
        raise NotImplementedError(
            "RealFlightProvider is not configured yet. Set FLIGHT_PROVIDER=mock."
        )


class RealHotelProvider(HotelProvider):
    async def search(
        self,
        city: str,
        check_in: date,
        check_out: date,
        guests: int = 2,
        rooms: int = 1,
        budget_max: Optional[float] = None,
    ) -> list[HotelResult]:
        raise NotImplementedError(
            "RealHotelProvider is not configured yet. Set HOTEL_PROVIDER=mock."
        )


class RealPackageProvider(PackageProvider):
    async def search(
        self,
        source: str,
        destination: str,
        departure_date: date,
        return_date: Optional[date] = None,
        passengers: int = 2,
        budget_max: Optional[float] = None,
    ) -> list[PackageResult]:
        raise NotImplementedError(
            "RealPackageProvider is not configured yet. Set PACKAGE_PROVIDER=mock."
        )

    async def plan(
        self,
        source: str,
        destination: str,
        departure_date: date,
        return_date: date,
        passengers: int = 2,
        include_hotel: bool = True,
        transport_preference: Optional[str] = None,
        budget_max: Optional[float] = None,
    ) -> PackagePlan:
        raise NotImplementedError(
            "RealPackageProvider is not configured yet. Set PACKAGE_PROVIDER=mock."
        )


def build_train_provider(name: str) -> TrainProvider:
    if name == "mock":
        return MockTrainProvider()
    if name == "real":
        from app.config.settings import get_settings

        return RealTrainProvider(
            fallback_to_mock=get_settings().train_provider_fallback_mock
        )
    raise ValueError(f"Unknown TRAIN_PROVIDER: {name}")


def build_flight_provider(name: str) -> FlightProvider:
    if name == "mock":
        return MockFlightProvider()
    if name == "real":
        return RealFlightProvider()
    raise ValueError(f"Unknown FLIGHT_PROVIDER: {name}")


def build_bus_provider(name: str) -> BusProvider:
    if name == "mock":
        return MockBusProvider()
    if name == "real":
        raise ValueError("RealBusProvider is not configured yet. Set BUS_PROVIDER=mock.")
    raise ValueError(f"Unknown BUS_PROVIDER: {name}")


def build_hotel_provider(name: str) -> HotelProvider:
    if name == "mock":
        return MockHotelProvider()
    if name == "real":
        return RealHotelProvider()
    raise ValueError(f"Unknown HOTEL_PROVIDER: {name}")


def build_package_provider(name: str) -> PackageProvider:
    if name == "mock":
        return MockPackageProvider()
    if name == "real":
        return RealPackageProvider()
    raise ValueError(f"Unknown PACKAGE_PROVIDER: {name}")
