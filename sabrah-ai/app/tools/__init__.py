"""OpenAI tool definitions and executors mapped to Travel Backend."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Awaitable, Callable, Dict, Optional, Union

from pydantic import BaseModel, Field, ValidationError

from app.services.travel_client import TravelBackendClient, TravelBackendError

logger = logging.getLogger(__name__)

ToolHandler = Callable[[Dict[str, Any], Optional[str]], Awaitable[Dict[str, Any]]]


class SearchTrainsArgs(BaseModel):
    source: str
    destination: str
    departure_date: Optional[str] = None
    return_date: Optional[str] = None
    passengers: int = Field(default=1, ge=1, le=9)
    travel_class: Optional[str] = None
    preference: Optional[str] = None
    trip_type: Optional[str] = None


class SearchLocalTransportArgs(BaseModel):
    city: str
    pickup: Optional[str] = None
    dropoff: Optional[str] = None
    transport_type: Optional[str] = None


class GetTrainDetailsArgs(BaseModel):
    train_id: str


class LiveTrainStatusArgs(BaseModel):
    train_id: Optional[str] = None
    source: Optional[str] = None
    destination: Optional[str] = None


class WishlistAddArgs(BaseModel):
    train_id: str
    name: Optional[str] = None
    source: Optional[str] = None
    destination: Optional[str] = None


class WishlistGetArgs(BaseModel):
    pass


class CharterRequestArgs(BaseModel):
    source: str
    destination: str
    departure_date: str
    return_date: Optional[str] = None
    passengers: int = Field(default=50, ge=10, le=1200)
    charter_type: str = "full_coach"
    event_type: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    notes: Optional[str] = None
    traveler_names: Optional[list[str]] = None


class EscalateArgs(BaseModel):
    reason: str
    booking_id: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    details: Optional[str] = None


class RefundArgs(BaseModel):
    booking_id: str
    reason: Optional[str] = None
    confirmed: bool = False


class FeedbackArgs(BaseModel):
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    comment: Optional[str] = None
    booking_id: Optional[str] = None


class WeatherArgs(BaseModel):
    city: str


class SearchFlightsArgs(BaseModel):
    source: str
    destination: str
    departure_date: str
    passengers: int = Field(default=1, ge=1, le=9)
    travel_class: Optional[str] = "economy"
    return_date: Optional[str] = None


class SearchHotelsArgs(BaseModel):
    city: str
    check_in: str
    check_out: str
    guests: int = Field(default=2, ge=1, le=10)
    rooms: int = Field(default=1, ge=1, le=5)
    budget_max: Optional[float] = None


class SearchBusesArgs(BaseModel):
    source: str
    destination: str
    departure_date: Optional[str] = None
    passengers: int = Field(default=1, ge=1, le=9)
    bus_type: Optional[str] = None


class SearchPackagesArgs(BaseModel):
    source: str
    destination: str
    departure_date: str
    return_date: Optional[str] = None
    passengers: int = Field(default=2, ge=1, le=9)
    budget_max: Optional[float] = None


class CreatePackagePlanArgs(BaseModel):
    source: str
    destination: str
    departure_date: str
    return_date: str
    passengers: int = Field(default=2, ge=1, le=9)
    include_hotel: bool = True
    transport_preference: Optional[str] = None
    budget_max: Optional[float] = None


class GetBookingArgs(BaseModel):
    booking_id: str


class CreateBookingArgs(BaseModel):
    item_type: str
    item_id: str
    passenger_name: str
    passenger_count: int = Field(default=1, ge=1, le=9)
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    confirmed: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class CancelBookingArgs(BaseModel):
    booking_id: str
    reason: Optional[str] = None
    confirmed: bool = False


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_trains",
            "description": (
                "Search trains between two cities for NORMAL ticket booking "
                "(max 9 passengers). If the group is larger than 9, do NOT call this "
                "with high passenger counts — use request_charter for full coach "
                "(72 seats) instead. Supports preference ranking, round_trip, "
                "stations and platform hints."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Origin city name, e.g. Khandwa",
                    },
                    "destination": {
                        "type": "string",
                        "description": "Destination city name, e.g. Indore",
                    },
                    "departure_date": {
                        "type": "string",
                        "description": "ISO date YYYY-MM-DD. Omit to search for today.",
                    },
                    "return_date": {
                        "type": "string",
                        "description": "ISO date for round-trip return leg.",
                    },
                    "passengers": {"type": "integer", "minimum": 1, "maximum": 9},
                    "travel_class": {"type": "string"},
                    "preference": {
                        "type": "string",
                        "description": "cheapest | fastest | ac | balanced | wishlist",
                    },
                    "trip_type": {
                        "type": "string",
                        "description": "one_way | round_trip | multi_city",
                    },
                },
                "required": ["source", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_train_details",
            "description": (
                "Get stations, platforms, stops, availability (CNF/RAC/WL), "
                "and connection notes for a train id."
            ),
            "parameters": {
                "type": "object",
                "properties": {"train_id": {"type": "string"}},
                "required": ["train_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_live_train_status",
            "description": (
                "Get mock live running status for trains (delay, current station, ETA). "
                "Optionally filter by source, destination, or train_id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "train_id": {"type": "string"},
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_local_transport",
            "description": (
                "Search local cabs or car rentals in a city (e.g. station to hotel/event)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "pickup": {"type": "string"},
                    "dropoff": {"type": "string"},
                    "transport_type": {
                        "type": "string",
                        "description": "cab | car_rental",
                    },
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_wishlist",
            "description": "Save a train to the user's wishlist for this session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "train_id": {"type": "string"},
                    "name": {"type": "string"},
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                },
                "required": ["train_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_wishlist",
            "description": "List wishlisted trains for this session.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_charter",
            "description": (
                "Request full coach (72 seats) or entire train for groups larger than 9 "
                "(wedding/baraat/group tourism). Use this instead of splitting into "
                "multiple 9-passenger ticket bookings. Creates a SabRaah sales request."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                    "departure_date": {"type": "string"},
                    "return_date": {"type": "string"},
                    "passengers": {"type": "integer"},
                    "charter_type": {
                        "type": "string",
                        "enum": ["full_coach", "entire_train"],
                    },
                    "event_type": {"type": "string"},
                    "contact_name": {"type": "string"},
                    "contact_phone": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["source", "destination", "departure_date", "passengers"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_sales",
            "description": (
                "Hand off to a SabRaah sales executive when AI cannot resolve the issue."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "booking_id": {"type": "string"},
                    "contact_name": {"type": "string"},
                    "contact_phone": {"type": "string"},
                    "details": {"type": "string"},
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_refund",
            "description": (
                "Start a refund request. Set confirmed=true only after explicit user yes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "confirmed": {"type": "boolean"},
                },
                "required": ["booking_id", "confirmed"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_feedback",
            "description": "Save client feedback / review after trip or booking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rating": {"type": "integer", "minimum": 1, "maximum": 5},
                    "comment": {"type": "string"},
                    "booking_id": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather_alert",
            "description": (
                "Get weather / travel tip for a city (sunny, rain, flood warning, etc.)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_flights",
            "description": "Search available flights between two cities on a date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                    "departure_date": {"type": "string"},
                    "return_date": {"type": "string"},
                    "passengers": {"type": "integer"},
                    "travel_class": {"type": "string"},
                },
                "required": ["source", "destination", "departure_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_hotels",
            "description": "Search hotels in a city for given dates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "check_in": {"type": "string"},
                    "check_out": {"type": "string"},
                    "guests": {"type": "integer"},
                    "rooms": {"type": "integer"},
                    "budget_max": {"type": "number"},
                },
                "required": ["city", "check_in", "check_out"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_buses",
            "description": (
                "Search buses between two cities, including connecting bus options "
                "when a direct bus is not ideal. Use in itinerary mode with trains/hotels."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                    "departure_date": {
                        "type": "string",
                        "description": "ISO date YYYY-MM-DD. Omit for today.",
                    },
                    "passengers": {"type": "integer"},
                    "bus_type": {"type": "string"},
                },
                "required": ["source", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_packages",
            "description": "Search travel packages for a destination.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                    "departure_date": {"type": "string"},
                    "return_date": {"type": "string"},
                    "passengers": {"type": "integer"},
                    "budget_max": {"type": "number"},
                },
                "required": ["source", "destination", "departure_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_package_plan",
            "description": "Create a suggested multi-day package plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                    "departure_date": {"type": "string"},
                    "return_date": {"type": "string"},
                    "passengers": {"type": "integer"},
                    "include_hotel": {"type": "boolean"},
                    "transport_preference": {"type": "string"},
                    "budget_max": {"type": "number"},
                },
                "required": [
                    "source",
                    "destination",
                    "departure_date",
                    "return_date",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_booking",
            "description": "Look up an existing booking by booking ID.",
            "parameters": {
                "type": "object",
                "properties": {"booking_id": {"type": "string"}},
                "required": ["booking_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_booking",
            "description": (
                "Create a booking ONLY after the user explicitly confirms. "
                "Set confirmed=true only when the user clearly said yes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item_type": {
                        "type": "string",
                        "enum": [
                            "train",
                            "flight",
                            "hotel",
                            "package",
                            "bus",
                            "local_transport",
                        ],
                    },
                    "item_id": {"type": "string"},
                    "passenger_name": {"type": "string"},
                    "passenger_count": {"type": "integer"},
                    "contact_email": {"type": "string"},
                    "contact_phone": {"type": "string"},
                    "confirmed": {"type": "boolean"},
                },
                "required": [
                    "item_type",
                    "item_id",
                    "passenger_name",
                    "confirmed",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_booking",
            "description": (
                "Cancel a booking ONLY after the user explicitly confirms. "
                "Set confirmed=true only when the user clearly said yes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "confirmed": {"type": "boolean"},
                },
                "required": ["booking_id", "confirmed"],
            },
        },
    },
]


class TravelToolExecutor:
    """Maps predefined tool names to Travel Backend HTTP calls only."""

    def __init__(self, client: TravelBackendClient) -> None:
        self._client = client
        self._handlers: dict[str, ToolHandler] = {
            "search_trains": self._search_trains,
            "search_flights": self._search_flights,
            "search_buses": self._search_buses,
            "search_hotels": self._search_hotels,
            "search_packages": self._search_packages,
            "create_package_plan": self._create_package_plan,
            "search_local_transport": self._search_local_transport,
            "get_train_details": self._get_train_details,
            "get_live_train_status": self._get_live_train_status,
            "add_to_wishlist": self._add_to_wishlist,
            "get_wishlist": self._get_wishlist,
            "request_charter": self._request_charter,
            "escalate_to_sales": self._escalate_to_sales,
            "request_refund": self._request_refund,
            "submit_feedback": self._submit_feedback,
            "get_weather_alert": self._get_weather_alert,
            "get_booking": self._get_booking,
            "create_booking": self._create_booking,
            "cancel_booking": self._cancel_booking,
        }

    @property
    def tool_names(self) -> set[str]:
        return set(self._handlers)

    async def execute(
        self, name: str, arguments: dict[str, Any], session_id: Optional[str] = None
    ) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if handler is None:
            return {
                "error": "unsupported_tool",
                "message": f"Tool '{name}' is not supported.",
            }
        try:
            return await handler(arguments, session_id)
        except ValidationError as exc:
            return {
                "error": "invalid_tool_arguments",
                "message": "Some required travel details are missing or invalid.",
                "details": exc.errors(),
            }
        except TravelBackendError as exc:
            return {"error": exc.code, "message": exc.user_message}

    async def _search_trains(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = SearchTrainsArgs.model_validate(arguments)
        payload = args.model_dump(exclude_none=True)
        if not payload.get("departure_date"):
            payload["departure_date"] = date.today().isoformat()
        if session_id:
            payload["session_id"] = session_id
        result = await self._client.request(
            "POST",
            "/api/v1/trains/search",
            json=payload,
            session_id=session_id,
        )
        if isinstance(result, dict):
            result = {
                **result,
                "source": payload["source"],
                "destination": payload["destination"],
                "departure_date": payload["departure_date"],
                "return_date": payload.get("return_date"),
                "passengers": payload.get("passengers", 1),
                "preference": payload.get("preference"),
                "trip_type": payload.get("trip_type"),
            }
        return result

    async def _search_local_transport(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = SearchLocalTransportArgs.model_validate(arguments)
        return await self._client.request(
            "POST",
            "/api/v1/local-transport/search",
            json=args.model_dump(exclude_none=True),
            session_id=session_id,
        )

    async def _get_train_details(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = GetTrainDetailsArgs.model_validate(arguments)
        return await self._client.request(
            "GET",
            f"/api/v1/trains/{args.train_id}/details",
            session_id=session_id,
        )

    async def _get_live_train_status(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = LiveTrainStatusArgs.model_validate(arguments)
        if args.train_id:
            return await self._client.request(
                "GET",
                f"/api/v1/trains/{args.train_id}/live-status",
                session_id=session_id,
            )
        params: dict[str, str] = {}
        if args.source:
            params["source"] = args.source
        if args.destination:
            params["destination"] = args.destination
        return await self._client.request(
            "GET",
            "/api/v1/trains/live-status",
            params=params or None,
            session_id=session_id,
        )

    async def _add_to_wishlist(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = WishlistAddArgs.model_validate(arguments)
        payload = args.model_dump(exclude_none=True)
        payload["session_id"] = session_id or "anon"
        return await self._client.request(
            "POST",
            "/api/v1/wishlist",
            json=payload,
            session_id=session_id,
        )

    async def _get_wishlist(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        WishlistGetArgs.model_validate(arguments or {})
        sid = session_id or "anon"
        return await self._client.request(
            "GET",
            f"/api/v1/wishlist?session_id={sid}",
            session_id=session_id,
        )

    async def _request_charter(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = CharterRequestArgs.model_validate(arguments)
        return await self._client.request(
            "POST",
            "/api/v1/charter/request",
            json=args.model_dump(exclude_none=True),
            session_id=session_id,
        )

    async def _escalate_to_sales(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = EscalateArgs.model_validate(arguments)
        return await self._client.request(
            "POST",
            "/api/v1/support/escalate",
            json=args.model_dump(exclude_none=True),
            session_id=session_id,
        )

    async def _request_refund(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = RefundArgs.model_validate(arguments)
        if not args.confirmed:
            return {
                "error": "confirmation_required",
                "message": (
                    "Refund was not submitted because the user has not confirmed yet."
                ),
            }
        return await self._client.request(
            "POST",
            "/api/v1/support/refund",
            json=args.model_dump(exclude_none=True),
            session_id=session_id,
        )

    async def _submit_feedback(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = FeedbackArgs.model_validate(arguments)
        payload = args.model_dump(exclude_none=True)
        if session_id:
            payload["session_id"] = session_id
        return await self._client.request(
            "POST",
            "/api/v1/feedback",
            json=payload,
            session_id=session_id,
        )

    async def _get_weather_alert(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = WeatherArgs.model_validate(arguments)
        city = args.city.strip().strip("/")
        return await self._client.request(
            "GET",
            f"/api/v1/weather/{city}",
            session_id=session_id,
        )

    async def _search_flights(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = SearchFlightsArgs.model_validate(arguments)
        return await self._client.request(
            "POST",
            "/api/v1/flights/search",
            json=args.model_dump(exclude_none=True),
            session_id=session_id,
        )

    async def _search_buses(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = SearchBusesArgs.model_validate(arguments)
        payload = args.model_dump(exclude_none=True)
        if not payload.get("departure_date"):
            payload["departure_date"] = date.today().isoformat()
        result = await self._client.request(
            "POST",
            "/api/v1/buses/search",
            json=payload,
            session_id=session_id,
        )
        if isinstance(result, dict):
            result = {
                **result,
                "source": payload["source"],
                "destination": payload["destination"],
                "departure_date": payload["departure_date"],
                "passengers": payload.get("passengers", 1),
            }
        return result

    async def _search_hotels(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = SearchHotelsArgs.model_validate(arguments)
        return await self._client.request(
            "POST",
            "/api/v1/hotels/search",
            json=args.model_dump(exclude_none=True),
            session_id=session_id,
        )

    async def _search_packages(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = SearchPackagesArgs.model_validate(arguments)
        return await self._client.request(
            "POST",
            "/api/v1/packages/search",
            json=args.model_dump(exclude_none=True),
            session_id=session_id,
        )

    async def _create_package_plan(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = CreatePackagePlanArgs.model_validate(arguments)
        return await self._client.request(
            "POST",
            "/api/v1/packages/plan",
            json=args.model_dump(exclude_none=True),
            session_id=session_id,
        )

    async def _get_booking(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = GetBookingArgs.model_validate(arguments)
        return await self._client.request(
            "GET",
            f"/api/v1/bookings/{args.booking_id}",
            session_id=session_id,
        )

    async def _create_booking(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = CreateBookingArgs.model_validate(arguments)
        if not args.confirmed:
            return {
                "error": "confirmation_required",
                "message": (
                    "Booking was not created because the user has not confirmed yet. "
                    "Ask for explicit confirmation first."
                ),
            }
        return await self._client.request(
            "POST",
            "/api/v1/bookings",
            json=args.model_dump(exclude_none=True),
            session_id=session_id,
        )

    async def _cancel_booking(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = CancelBookingArgs.model_validate(arguments)
        if not args.confirmed:
            return {
                "error": "confirmation_required",
                "message": (
                    "Cancellation was not performed because the user has not confirmed yet."
                ),
            }
        return await self._client.request(
            "POST",
            f"/api/v1/bookings/{args.booking_id}/cancel",
            json={"reason": args.reason, "confirmed": True},
            session_id=session_id,
        )


def parse_tool_arguments(raw: Union[str, dict[str, Any]]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
