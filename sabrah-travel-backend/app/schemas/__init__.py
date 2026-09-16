"""Pydantic request/response schemas."""

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class TripType(str, Enum):
    ONE_WAY = "one_way"
    ROUND_TRIP = "round_trip"


class TransportType(str, Enum):
    TRAIN = "train"
    FLIGHT = "flight"
    BUS = "bus"
    PACKAGE = "package"


class TravelClass(str, Enum):
    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium_economy"
    BUSINESS = "business"
    FIRST = "first"
    SLEEPER = "sleeper"
    AC_3 = "3A"
    AC_2 = "2A"
    AC_1 = "1A"


class BookingStatus(str, Enum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    PENDING = "pending"


class TrainSearchRequest(BaseModel):
    source: str
    destination: str
    departure_date: date = Field(default_factory=date.today)
    return_date: Optional[date] = None
    passengers: int = Field(default=1, ge=1, le=9)
    travel_class: Optional[str] = None
    preference: Optional[str] = Field(
        default=None,
        description="cheapest | fastest | ac | balanced | wishlist",
    )
    trip_type: Optional[str] = Field(
        default=None,
        description="one_way | round_trip | multi_city",
    )
    session_id: Optional[str] = None


class LocalTransportSearchRequest(BaseModel):
    city: str
    pickup: Optional[str] = None
    dropoff: Optional[str] = None
    transport_type: Optional[str] = None


class WishlistAddRequest(BaseModel):
    session_id: str
    train_id: Optional[str] = None
    name: Optional[str] = None
    source: Optional[str] = None
    destination: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CharterRequest(BaseModel):
    source: str
    destination: str
    departure_date: date
    return_date: Optional[date] = None
    passengers: int = Field(default=50, ge=10, le=1200)
    charter_type: str = Field(
        default="full_coach",
        description="full_coach | entire_train",
    )
    event_type: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    traveler_names: Optional[list[str]] = None
    notes: Optional[str] = None


class SupportEscalateRequest(BaseModel):
    reason: str
    booking_id: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    details: Optional[str] = None


class RefundRequest(BaseModel):
    booking_id: str
    reason: Optional[str] = None
    confirmed: bool = False


class FeedbackRequest(BaseModel):
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    comment: Optional[str] = None
    booking_id: Optional[str] = None
    session_id: Optional[str] = None


class FlightSearchRequest(BaseModel):
    source: str
    destination: str
    departure_date: date
    return_date: Optional[date] = None
    passengers: int = Field(default=1, ge=1, le=9)
    travel_class: Optional[str] = "economy"
    trip_type: TripType = TripType.ONE_WAY


class HotelSearchRequest(BaseModel):
    city: str
    check_in: date
    check_out: date
    guests: int = Field(default=2, ge=1, le=10)
    rooms: int = Field(default=1, ge=1, le=5)
    budget_max: Optional[float] = None


class BusSearchRequest(BaseModel):
    source: str
    destination: str
    departure_date: date = Field(default_factory=date.today)
    passengers: int = Field(default=1, ge=1, le=9)
    bus_type: Optional[str] = None


class PackageSearchRequest(BaseModel):
    source: str
    destination: str
    departure_date: date
    return_date: Optional[date] = None
    passengers: int = Field(default=2, ge=1, le=9)
    budget_max: Optional[float] = None


class PackagePlanRequest(BaseModel):
    source: str
    destination: str
    departure_date: date
    return_date: date
    passengers: int = Field(default=2, ge=1, le=9)
    include_hotel: bool = True
    transport_preference: Optional[str] = None
    budget_max: Optional[float] = None


class CreateBookingRequest(BaseModel):
    item_type: str = Field(description="train | flight | hotel | package | bus")
    item_id: str
    passenger_name: str
    passenger_count: int = Field(default=1, ge=1, le=9)
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = Field(
        default=False,
        description="Must be true; caller must have explicit user confirmation.",
    )


class CancelBookingRequest(BaseModel):
    reason: Optional[str] = None
    confirmed: bool = Field(
        default=False,
        description="Must be true; caller must have explicit user confirmation.",
    )


class TrainResult(BaseModel):
    id: str
    name: str
    source: str
    destination: str
    departure_time: str
    arrival_time: str
    duration: str
    travel_class: str = Field(alias="class")
    price: float
    unit_price: Optional[float] = None
    currency: str = "INR"
    available_seats: int
    rac_seats: int = 0
    waiting_list: int = 0
    availability_status: str = "AVAILABLE"
    availability_message: Optional[str] = None
    can_book: bool = True
    needs_waitlist_confirm: bool = False
    provider: str = "MOCK"

    model_config = {"populate_by_name": True}


class FlightResult(BaseModel):
    id: str
    airline: str
    source: str
    destination: str
    departure_time: str
    arrival_time: str
    duration: str
    price: float
    currency: str = "INR"
    travel_class: str = "economy"
    available_seats: int = 20
    provider: str = "MOCK"


class BusResult(BaseModel):
    id: str
    name: str
    operator: str
    source: str
    destination: str
    departure_time: str
    arrival_time: str
    duration: str
    bus_type: str = "AC Seater"
    price: float
    currency: str = "INR"
    available_seats: int = 20
    legs: list[dict[str, Any]] = Field(default_factory=list)
    provider: str = "MOCK"


class HotelResult(BaseModel):
    id: str
    name: str
    city: str
    check_in: date
    check_out: date
    price_per_night: float
    total_price: float
    currency: str = "INR"
    rating: float
    amenities: list[str] = Field(default_factory=list)
    provider: str = "MOCK"


class PackageResult(BaseModel):
    id: str
    name: str
    source: str
    destination: str
    departure_date: date
    return_date: Optional[date] = None
    nights: int
    price: float
    currency: str = "INR"
    includes: list[str] = Field(default_factory=list)
    provider: str = "MOCK"


class PackagePlan(BaseModel):
    id: str
    summary: str
    source: str
    destination: str
    departure_date: date
    return_date: date
    transport: dict[str, Any]
    hotel: Optional[dict[str, Any]] = None
    estimated_total: float
    currency: str = "INR"
    day_plan: list[str] = Field(default_factory=list)
    provider: str = "MOCK"


class SearchResponse(BaseModel):
    results: list[Any]
    provider: str = "MOCK"
    count: int = 0


class BookingResponse(BaseModel):
    booking_id: str
    status: BookingStatus
    item_type: str
    item_id: str
    passenger_name: str
    passenger_count: int
    total_price: float
    currency: str = "INR"
    created_at: datetime
    cancelled_at: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    provider: str = "MOCK"


class HealthResponse(BaseModel):
    status: str
    app: str
    env: str
    providers: dict[str, str]
