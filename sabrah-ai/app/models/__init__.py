"""Conversation and API models."""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class TravelSlotMemory(BaseModel):
    language: Optional[str] = "en"
    intent: Optional[str] = None
    # normal | itinerary | journey | charter | support
    booking_mode: Optional[str] = None
    # flights | trains | buses | local | hotels | meals | summary
    itinerary_step: Optional[str] = None
    source: Optional[str] = None
    destination: Optional[str] = None
    via_city: Optional[str] = None
    multi_city_stops: Optional[list[str]] = None
    departure_date: Optional[str] = None
    return_date: Optional[str] = None
    # one_way | round_trip | multi_city
    trip_type: Optional[str] = None
    transport_type: Optional[str] = None
    # leisure | business | honeymoon | family | adventure | religious | medical |
    # shopping | wedding | baraat | conference | weekend | other
    trip_purpose: Optional[str] = None
    # solo | couple | family | group | business
    party_type: Optional[str] = None
    # price | time | comfort | luxury | convenience | experience | balanced
    value_priority: Optional[str] = None
    date_flexible: Optional[bool] = None
    trip_nights: Optional[int] = None
    infant_count: Optional[int] = None
    direct_only: Optional[bool] = None
    avoid_early_departure: Optional[bool] = None
    travel_pace: Optional[str] = None
    accessibility_needed: Optional[bool] = None
    extra_meal: Optional[str] = None
    extra_baggage: Optional[str] = None
    extra_checkin: Optional[str] = None
    flight_fare_name: Optional[str] = None
    hotel_area: Optional[str] = None
    # book_train | book_flight | book_event | cancel | refund | charter | (legacy support)
    user_goal: Optional[str] = None
    # welcome | goal | where | why | when | passengers | search | ...
    flow_step: Optional[str] = None
    pnr: Optional[str] = None
    event_name: Optional[str] = None
    passenger_count: Optional[int] = None
    adult_count: Optional[int] = None
    child_count: Optional[int] = None
    travel_class: Optional[str] = None
    # cheapest | fastest | ac | balanced | wishlist
    train_preference: Optional[str] = None
    budget: Optional[float] = None
    hotel_required: Optional[bool] = None
    flight_required: Optional[bool] = None
    event_required: Optional[bool] = None
    local_transport_required: Optional[bool] = None
    selected_option: Optional[str] = None
    selected_train_id: Optional[str] = None
    selected_return_train_id: Optional[str] = None
    selected_flight_id: Optional[str] = None
    selected_bus_id: Optional[str] = None
    selected_hotel_id: Optional[str] = None
    selected_local_id: Optional[str] = None
    skip_bus: Optional[bool] = None
    skip_flight: Optional[bool] = None
    skip_local: Optional[bool] = None
    # veg | non_veg | jain | none
    meal_preference: Optional[str] = None
    meal_asked: Optional[bool] = None
    dietary_restrictions: Optional[str] = None
    allergies: Optional[str] = None
    catering_full_train: Optional[bool] = None
    book_meal_with_ticket: Optional[bool] = None
    charter_type: Optional[str] = None
    passenger_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    booking_confirmation: Optional[bool] = None
    last_booking_id: Optional[str] = None
    selected_event_id: Optional[str] = None
    feedback_requested: Optional[bool] = None
    weather_alert_sent: Optional[bool] = None
    pre_booking_step: Optional[str] = None  # train_pick | meals | passengers | phone | confirm
    passenger_collect_index: Optional[int] = None  # 0-based next passenger to collect
    accept_waitlist: Optional[bool] = None  # user accepted RAC/WL booking
    support_ticket_id: Optional[str] = None


class SessionState(BaseModel):
    session_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    agent_id: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None
    source: Optional[str] = "Admin Console"
    unread: bool = False
    memory: TravelSlotMemory = Field(default_factory=TravelSlotMemory)
    conversation_history: list[ConversationMessage] = Field(default_factory=list)
    pending_confirmation: Optional[dict[str, Any]] = None
    last_search_results: list[dict[str, Any]] = Field(default_factory=list)
    last_offerings: dict[str, Any] = Field(default_factory=dict)
    itinerary_cache: dict[str, Any] = Field(default_factory=dict)
    itinerary_selections: dict[str, Any] = Field(default_factory=dict)
    wishlist: list[dict[str, Any]] = Field(default_factory=list)
    travelers: list[dict[str, Any]] = Field(default_factory=list)
    booking_details: dict[str, Any] = Field(default_factory=dict)
    user_access_token: Optional[str] = None


class SessionCreateResponse(BaseModel):
    session_id: str
    agent_id: Optional[str] = None


class SessionCreateRequest(BaseModel):
    agent_id: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None
    source: Optional[str] = None
    user_access_token: Optional[str] = None


class TextChatRequest(BaseModel):
    session_id: str
    message: str = Field(min_length=1)
    agent_id: Optional[str] = None
    user_access_token: Optional[str] = None


class AgentRecord(BaseModel):
    id: str
    name: str
    description: str = ""
    first_message: str = ""
    system_prompt: str
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    first_message: str = ""
    system_prompt: str = ""
    enabled: bool = True


class AgentUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = None
    first_message: Optional[str] = None
    system_prompt: Optional[str] = None
    enabled: Optional[bool] = None


class ChatResponse(BaseModel):
    session_id: str
    user_text: str
    assistant_text: str
    audio_base64: Optional[str] = None
    audio_mime_type: Optional[str] = "audio/mpeg"
    tools_used: list[str] = Field(default_factory=list)
    tts_error: Optional[str] = None
    memory: TravelSlotMemory
    booking_id: Optional[str] = None
    payment_url: Optional[str] = None
    booking_url: Optional[str] = None
    open_booking: bool = False
    payment_amount: Optional[float] = None
    payment_currency: Optional[str] = None
    offerings: dict[str, Any] = Field(default_factory=dict)
    booking_details: dict[str, Any] = Field(default_factory=dict)
    ignored: bool = False


class HealthResponse(BaseModel):
    status: str
    travel_backend: str
    app: str
