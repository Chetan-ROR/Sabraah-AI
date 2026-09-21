"""OpenAI tool definitions and executors mapped to Travel Backend."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Awaitable, Callable, Dict, Optional, Union

from pydantic import BaseModel, Field, ValidationError

from app.services.events_client import SuperTravelEventsClient
from app.services.travel_client import CABIN_MAP, TravelBackendClient, TravelBackendError

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


class SearchEventsArgs(BaseModel):
    search: Optional[str] = None
    artist: Optional[str] = None
    city: Optional[str] = None
    category_id: Optional[int] = None


class GetEventDetailsArgs(BaseModel):
    event_id: str = Field(min_length=1)


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
    children: int = Field(default=0, ge=0, le=8)
    infants: int = Field(default=0, ge=0, le=8)
    travel_class: Optional[str] = "economy"
    return_date: Optional[str] = None
    trip_type: Optional[str] = None
    direct_only: Optional[bool] = None


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
            "description": (
                "Search live Super Travel flights (api-repository). "
                "Use city names or IATA codes. For round trip pass return_date."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                    "departure_date": {"type": "string"},
                    "return_date": {"type": "string"},
                    "passengers": {"type": "integer"},
                    "children": {"type": "integer"},
                    "infants": {"type": "integer"},
                    "travel_class": {"type": "string"},
                    "direct_only": {"type": "boolean"},
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
            "name": "search_events",
            "description": (
                "Search live Super Travel events for the logged-in user. "
                "Use for concerts, shows, festivals, comedy, or any event booking. "
                "Do not collect guests or take payment — after the user picks an event, "
                "call get_event_details and give them the booking_url."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {
                        "type": "string",
                        "description": "Event name search text",
                    },
                    "artist": {"type": "string"},
                    "city": {"type": "string"},
                    "category_id": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_event_details",
            "description": (
                "Get one event's tickets, schedule, and the booking_url. "
                "Never create the booking or charge the card. The user opens booking_url "
                "in a new tab, fills guest details, and pays there."
            ),
            "parameters": {
                "type": "object",
                "properties": {"event_id": {"type": "string"}},
                "required": ["event_id"],
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

    def __init__(
        self,
        client: TravelBackendClient,
        events_client: Optional[SuperTravelEventsClient] = None,
    ) -> None:
        self._client = client
        self._events = events_client
        self._user_access_token: Optional[str] = None
        self._handlers: dict[str, ToolHandler] = {
            "search_trains": self._search_trains,
            "search_events": self._search_events,
            "get_event_details": self._get_event_details,
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
        self,
        name: str,
        arguments: dict[str, Any],
        session_id: Optional[str] = None,
        *,
        user_access_token: Optional[str] = None,
    ) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if handler is None:
            return {
                "error": "unsupported_tool",
                "message": f"Tool '{name}' is not supported.",
            }
        self._user_access_token = user_access_token
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
        finally:
            self._user_access_token = None

    async def _search_trains(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = SearchTrainsArgs.model_validate(arguments)
        origin = await self._client.resolve_station(args.source, session_id=session_id)
        destination = await self._client.resolve_station(
            args.destination, session_id=session_id
        )
        departure = args.departure_date or date.today().isoformat()
        try:
            day, month, year = (
                departure[8:10],
                departure[5:7],
                departure[0:4],
            )
            irctc_date = f"{day}-{month}-{year}"
        except Exception:  # noqa: BLE001
            irctc_date = date.today().strftime("%d-%m-%Y")
        payload = await self._client.request(
            "GET",
            "/api/v1/trains/train-list/",
            params={
                "origin": origin,
                "destination": destination,
                "date": irctc_date,
                "page": 1,
                "page_size": 8,
            },
            session_id=session_id,
        )
        rows = payload.get("results") or []
        results = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            train_id = str(row.get("train_number") or row.get("id") or "")
            results.append(
                {
                    "id": train_id,
                    "name": row.get("train_name") or train_id,
                    "source": args.source,
                    "destination": args.destination,
                    "origin_code": row.get("origin_code") or origin,
                    "destination_code": row.get("destination_code") or destination,
                    "departure_time": (row.get("departure_time") or "")[:5],
                    "arrival_time": (row.get("arrival_time") or "")[:5],
                    "duration": row.get("running_time"),
                    "provider": "SUPER_TRAVEL",
                }
            )
        return {
            "results": results,
            "count": len(results),
            "provider": "SUPER_TRAVEL",
            "source": args.source,
            "destination": args.destination,
            "departure_date": departure,
            "return_date": args.return_date,
            "passengers": args.passengers,
            "preference": args.preference,
            "trip_type": args.trip_type,
        }

    def _events_client(self) -> SuperTravelEventsClient:
        if self._events is None:
            raise TravelBackendError(
                "Event search is not configured.",
                code="events_unavailable",
            )
        return self._events

    async def _search_events(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = SearchEventsArgs.model_validate(arguments)
        return await self._events_client().search(
            user_access_token=self._user_access_token,
            search=args.search,
            artist=args.artist,
            city=args.city,
            category_id=args.category_id,
            session_id=session_id,
        )

    async def _get_event_details(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = GetEventDetailsArgs.model_validate(arguments)
        return await self._events_client().detail(
            args.event_id,
            user_access_token=self._user_access_token,
            session_id=session_id,
        )

    async def _search_local_transport(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        return await self._unsupported_on_api_repository("Local transport")

    async def _get_train_details(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = GetTrainDetailsArgs.model_validate(arguments)
        return await self._client.request(
            "GET",
            "/api/v1/trains/live/",
            params={"train_number": args.train_id},
            session_id=session_id,
        )

    async def _get_live_train_status(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = LiveTrainStatusArgs.model_validate(arguments)
        train_id = args.train_id
        if not train_id:
            return {
                "error": "invalid_tool_arguments",
                "message": "Please give the train number for live status.",
            }
        return await self._client.request(
            "GET",
            "/api/v1/trains/live/",
            params={"train_number": train_id},
            session_id=session_id,
        )

    async def _add_to_wishlist(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = WishlistAddArgs.model_validate(arguments)
        return await self._client.request(
            "POST",
            "/api/v1/wishlists/",
            json={"name": args.name or args.train_id, "train_id": args.train_id},
            session_id=session_id,
            user_access_token=self._user_access_token,
        )

    async def _get_wishlist(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        WishlistGetArgs.model_validate(arguments or {})
        return await self._client.request(
            "GET",
            "/api/v1/wishlists/",
            session_id=session_id,
            user_access_token=self._user_access_token,
        )

    async def _request_charter(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        return await self._unsupported_on_api_repository("Charter / full coach")

    async def _escalate_to_sales(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        return await self._unsupported_on_api_repository("Sales escalation")

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
            f"/api/v1/flights/manage/{args.booking_id}/cancel",
            json={"reason": args.reason},
            session_id=session_id,
            user_access_token=self._user_access_token,
        )

    async def _submit_feedback(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = FeedbackArgs.model_validate(arguments)
        payload = {
            "rating": args.rating,
            "comment": args.comment,
            "booking": args.booking_id,
        }
        return await self._client.request(
            "POST",
            "/api/v1/bookings/rating/",
            json={k: v for k, v in payload.items() if v is not None},
            session_id=session_id,
            user_access_token=self._user_access_token,
        )

    async def _get_weather_alert(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        return await self._unsupported_on_api_repository("Weather alerts")

    async def _unsupported_on_api_repository(self, product: str) -> dict[str, Any]:
        return {
            "error": "not_on_super_travel",
            "message": (
                f"{product} is not available on the Super Travel API. "
                "I can search flights, trains, hotels, and events."
            ),
            "results": [],
            "count": 0,
        }

    def _map_flight_card(self, row: dict[str, Any], *, search_tui: str = "") -> dict[str, Any]:
        selection = row.get("selection") if isinstance(row.get("selection"), dict) else {}
        flight_id = str(
            selection.get("index") or row.get("index") or row.get("flight_number") or ""
        )
        return {
            "id": flight_id,
            "name": row.get("flight_number") or row.get("airline_name") or flight_id,
            "airline": row.get("airline_name"),
            "airline_code": row.get("airline_code"),
            "departure_time": row.get("departure_time"),
            "arrival_time": row.get("arrival_time"),
            "duration": row.get("duration"),
            "stops": row.get("stops"),
            "price": row.get("price") or row.get("gross_fare"),
            "price_label": row.get("price_label") or row.get("gross_fare_label"),
            "currency": "INR",
            "from_code": row.get("departure_code"),
            "to_code": row.get("arrival_code"),
            "selection": selection,
            "flight_fares": row.get("flight_fares") or [],
            "search_tui": search_tui or selection.get("tui"),
        }

    async def _search_flights(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = SearchFlightsArgs.model_validate(arguments)
        origin = await self._client.resolve_airport(args.source, session_id=session_id)
        dest = await self._client.resolve_airport(args.destination, session_id=session_id)
        trip_type = (args.trip_type or "").strip().lower()
        if args.return_date and trip_type != "oneway":
            trip_type = "round_trip"
        if trip_type not in {"oneway", "round_trip"}:
            trip_type = "oneway"
        cabin = CABIN_MAP.get((args.travel_class or "economy").strip().lower(), "economy")
        payload = {
            "trip_type": trip_type,
            "travellers": {
                "adults": max(1, args.passengers),
                "children": args.children or 0,
                "infants": args.infants or 0,
            },
            "cabin": cabin,
            "from_airport": origin,
            "to_airport": dest,
            "departure_date": args.departure_date,
            "return_date": args.return_date if trip_type == "round_trip" else None,
            "student_fare": False,
            "armed_forces": False,
            "senior_citizen": False,
            "direct_only": bool(args.direct_only),
            "refundable_only": False,
            "nearby_airports": True,
        }
        data = await self._client.request(
            "POST",
            "/api/v1/flights/search",
            json=payload,
            session_id=session_id,
        )
        flights = data.get("flights") or []
        if trip_type == "round_trip":
            groups = data.get("fare_groups") or {}
            rt = groups.get("RT") if isinstance(groups, dict) else None
            if isinstance(rt, dict) and isinstance(rt.get("flights"), list):
                flights = rt["flights"]
        search_tui = str(data.get("tui") or "")
        results = [
            self._map_flight_card(row, search_tui=search_tui)
            for row in flights
            if isinstance(row, dict)
        ][:8]
        continue_url = ""
        if self._client._web_app_base_url:
            continue_url = f"{self._client._web_app_base_url}/preview/flights/search"
        return {
            "results": results,
            "count": len(results),
            "provider": "SUPER_TRAVEL",
            "tui": search_tui,
            "source": args.source,
            "destination": args.destination,
            "from_airport": origin,
            "to_airport": dest,
            "departure_date": args.departure_date,
            "return_date": args.return_date,
            "passengers": args.passengers,
            "trip_type": trip_type,
            "booking_url": continue_url,
        }

    async def _search_buses(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        return await self._unsupported_on_api_repository("Bus search")

    async def _search_hotels(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = SearchHotelsArgs.model_validate(arguments)
        adults = max(1, args.guests)
        occupancy = [
            {
                "room_no": index + 1,
                "adult": max(1, adults if args.rooms == 1 else 1),
                "child": 0,
                "child_age": [],
            }
            for index in range(max(1, args.rooms))
        ]
        data = await self._client.request(
            "POST",
            "/api/v1/hotels/hotel/search/",
            json={
                "city": args.city,
                "checkin": args.check_in,
                "checkout": args.check_out,
                "requiredCurrency": "INR",
                "occupancy": occupancy,
            },
            session_id=session_id,
        )
        rows = data.get("results") or data.get("hotels") or []
        if isinstance(data.get("data"), list):
            rows = data["data"]
        results = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            results.append(
                {
                    "id": str(row.get("id") or row.get("hotel_id") or ""),
                    "name": row.get("name") or "Hotel",
                    "city": args.city,
                    "rating": row.get("rating"),
                    "price": row.get("price") or row.get("total_price") or row.get("price_per_night"),
                    "currency": row.get("currency") or "INR",
                }
            )
        return {
            "results": results[:8],
            "count": len(results[:8]),
            "provider": "SUPER_TRAVEL",
            "city": args.city,
            "check_in": args.check_in,
            "check_out": args.check_out,
        }

    async def _search_packages(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        return await self._unsupported_on_api_repository("Travel packages")

    async def _create_package_plan(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        return await self._unsupported_on_api_repository("Package plans")

    async def _get_booking(
        self, arguments: dict[str, Any], session_id: Optional[str]
    ) -> dict[str, Any]:
        args = GetBookingArgs.model_validate(arguments)
        return await self._client.request(
            "GET",
            f"/api/v1/users/trips/{args.booking_id}",
            session_id=session_id,
            user_access_token=self._user_access_token,
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
        item_type = (args.item_type or "").strip().lower()
        if item_type in {"flight", "flights"}:
            metadata = args.metadata or {}
            selection = metadata.get("selection") if isinstance(metadata, dict) else None
            if not isinstance(selection, dict):
                selection = {
                    "index": args.item_id,
                    "order_id": 1,
                    "amount": str(metadata.get("amount") or ""),
                    "tui": metadata.get("tui") or metadata.get("search_tui") or "",
                }
            fares = metadata.get("flight_fares") if isinstance(metadata, dict) else None
            fare_payload = {
                "trip_type": "ON",
                "include_paid_ssr": True,
                "flight": {
                    "departure_code": metadata.get("from_code") or "",
                    "arrival_code": metadata.get("to_code") or "",
                },
                "fares": (
                    fares
                    if isinstance(fares, list) and fares
                    else [
                        {
                            "selection": selection,
                            "price": metadata.get("price"),
                        }
                    ]
                ),
            }
            priced = await self._client.request(
                "POST",
                "/api/v1/flights/fare-group-details",
                json=fare_payload,
                session_id=session_id,
            )
            web = self._client._web_app_base_url
            booking_url = f"{web}/preview/flights/review" if web else ""
            fare_types = priced.get("fare_types") or []
            first = fare_types[0] if fare_types and isinstance(fare_types[0], dict) else priced
            summary = first.get("fare_summary") if isinstance(first, dict) else {}
            fare_cards: list[dict[str, Any]] = []
            for fare in fare_types:
                if not isinstance(fare, dict):
                    continue
                extras = []
                for opt in fare.get("ssr_options") or []:
                    if not isinstance(opt, dict):
                        continue
                    extras.append(
                        {
                            "category": str(opt.get("category") or ""),
                            "title": str(opt.get("title") or opt.get("code") or "Add-on"),
                            "price": opt.get("price") or "",
                            "price_label": opt.get("price_label") or "",
                            "code": str(opt.get("code") or ""),
                            "id": str(
                                opt.get("id")
                                or (opt.get("selection") or {}).get("id")
                                or ""
                            ),
                        }
                    )
                fare_cards.append(
                    {
                        "name": str(fare.get("fare_name") or fare.get("name") or "Fare"),
                        "total_label": (fare.get("fare_summary") or {}).get("total_label")
                        or fare.get("total_label"),
                        "ssr_options": extras[:16],
                    }
                )
            return {
                "status": "priced",
                "item_type": "flight",
                "item_id": args.item_id,
                "fare_summary": summary,
                "priced_tui": first.get("priced_tui") if isinstance(first, dict) else None,
                "total": (summary or {}).get("total_label") or first.get("total_label"),
                "fare_types": fare_cards,
                "booking_url": booking_url,
                "payment_url": booking_url,
                "message": (
                    "Live fare is locked. Complete extras, passenger details and payment "
                    "on Super Travel — voice cannot charge the card."
                ),
            }
        return {
            "error": "booking_needs_app",
            "message": (
                "Super Travel creates flight bookings only after login and payment "
                "(itinerary → prepare-payment → confirm-payment). "
                "I can search and price the fare here; finish checkout in the app."
            ),
            "item_type": args.item_type,
            "item_id": args.item_id,
        }

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
            f"/api/v1/flights/manage/{args.booking_id}/cancel",
            json={"reason": args.reason},
            session_id=session_id,
            user_access_token=self._user_access_token,
        )


def parse_tool_arguments(raw: Union[str, dict[str, Any]]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
