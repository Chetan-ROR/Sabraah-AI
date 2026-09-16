"""Domain models for in-memory storage."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.schemas import BookingStatus


class BookingRecord(BaseModel):
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
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
