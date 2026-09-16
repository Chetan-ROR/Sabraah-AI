"""In-memory booking repository with Redis-ready interface."""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.models import BookingRecord
from app.schemas import BookingStatus, CreateBookingRequest


class BookingRepository(ABC):
    @abstractmethod
    async def create(
        self, request: CreateBookingRequest, total_price: float
    ) -> BookingRecord:
        raise NotImplementedError

    @abstractmethod
    async def get(self, booking_id: str) -> Optional[BookingRecord]:
        raise NotImplementedError

    @abstractmethod
    async def cancel(
        self, booking_id: str, reason: Optional[str] = None
    ) -> Optional[BookingRecord]:
        raise NotImplementedError


class InMemoryBookingRepository(BookingRepository):
    """MVP storage. Swap for RedisBookingRepository later."""

    def __init__(self) -> None:
        self._bookings: dict[str, BookingRecord] = {}

    async def create(
        self, request: CreateBookingRequest, total_price: float
    ) -> BookingRecord:
        booking_id = f"BK-{uuid4().hex[:10].upper()}"
        record = BookingRecord(
            booking_id=booking_id,
            status=BookingStatus.CONFIRMED,
            item_type=request.item_type,
            item_id=request.item_id,
            passenger_name=request.passenger_name,
            passenger_count=request.passenger_count,
            total_price=total_price,
            currency="INR",
            created_at=datetime.now(timezone.utc),
            contact_email=request.contact_email,
            contact_phone=request.contact_phone,
            metadata=request.metadata,
        )
        self._bookings[booking_id] = record
        return record

    async def get(self, booking_id: str) -> Optional[BookingRecord]:
        return self._bookings.get(booking_id)

    async def cancel(
        self, booking_id: str, reason: Optional[str] = None
    ) -> Optional[BookingRecord]:
        record = self._bookings.get(booking_id)
        if record is None:
            return None
        if record.status == BookingStatus.CANCELLED:
            return record
        updated = record.model_copy(
            update={
                "status": BookingStatus.CANCELLED,
                "cancelled_at": datetime.now(timezone.utc),
                "metadata": {
                    **record.metadata,
                    "cancel_reason": reason or "user_requested",
                },
            }
        )
        self._bookings[booking_id] = updated
        return updated
