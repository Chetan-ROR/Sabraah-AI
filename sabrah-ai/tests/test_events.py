"""Event search mapping and login-required behaviour."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.services.events_client import (
    SuperTravelEventsClient,
    map_event_card,
    map_event_details,
)
from app.services.travel_client import TravelBackendClient
from app.tools import TravelToolExecutor


def test_map_event_card_builds_booking_link() -> None:
    card = map_event_card(
        {
            "id": 12,
            "name": "Sunburn Arena",
            "artist_name": "DJ Snake",
            "venue_name": "Jio Garden",
            "start_date_time": "2026-10-12 19:00:00",
            "start_price": "2500.00",
        },
        web_app_base_url="http://127.0.0.1:3000",
    )
    assert card["id"] == "12"
    assert card["booking_url"] == "http://127.0.0.1:3000/event-booking-detail?event_id=12"
    assert card["detail_url"] == "http://127.0.0.1:3000/event-detail/12"
    assert "INR 2500.00" in card["hint"]


def test_map_event_details_keeps_tickets_and_link() -> None:
    details = map_event_details(
        {
            "id": 7,
            "name": "Comedy Night",
            "tickets": [{"id": 3, "name": "Gold", "price": "999", "available_seats": 40}],
            "schedules": [{"id": 9, "event_date": "2026-11-01", "start_time": "20:00:00"}],
        }
    )
    assert details["booking_url"] == "/event-booking-detail?event_id=7"
    assert details["tickets"][0]["name"] == "Gold"
    assert details["schedules"][0]["event_date"] == "2026-11-01"


def test_search_events_requires_login() -> None:
    client = SuperTravelEventsClient("http://127.0.0.1:8002")
    executor = TravelToolExecutor(TravelBackendClient("http://x", "k"), client)
    result = asyncio.run(executor.execute("search_events", {}, session_id="s1"))
    assert result["error"] == "login_required"


def test_search_events_maps_api_payload() -> None:
    events = SuperTravelEventsClient(
        "http://127.0.0.1:8002", web_app_base_url="http://127.0.0.1:3000"
    )
    events._request = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "data": [
                {
                    "id": 4,
                    "name": "Jazz Fest",
                    "venue_name": "NCPA",
                    "venue_address": "Nariman Point, Mumbai",
                    "start_price": "1200",
                }
            ]
        }
    )
    executor = TravelToolExecutor(TravelBackendClient("http://x", "k"), events)
    result = asyncio.run(
        executor.execute(
            "search_events",
            {"city": "Mumbai"},
            session_id="s1",
            user_access_token="user-jwt",
        )
    )
    assert result["provider"] == "SUPER_TRAVEL"
    assert result["results"][0]["name"] == "Jazz Fest"
    assert result["results"][0]["booking_url"].endswith(
        "/event-booking-detail?event_id=4"
    )


def test_generic_event_question_has_no_search_filter() -> None:
    from app.agents import ConversationAgent

    assert ConversationAgent._event_search_args(
        "Can you tell me about events? What do you have?"
    ) == {}
    args = ConversationAgent._event_search_args("zakir khan events in jaipur")
    assert args["city"] == "Jaipur"
    assert args["search"] == "zakir khan"


def test_event_guest_names_ignore_ask_request() -> None:
    from app.agents import ConversationAgent

    assert ConversationAgent._parse_event_guest_names(
        "Yeah, can you book it for me and ask the users name?",
    ) == []


def test_event_guest_names_parse_two_people() -> None:
    from app.agents import ConversationAgent

    names = ConversationAgent._parse_event_guest_names(
        "two guests, Rahul Sharma and Priya Verma",
    )
    assert names == ["Rahul Sharma", "Priya Verma"]


def test_event_guest_names_parse_first_second_is() -> None:
    from app.agents import ConversationAgent

    names = ConversationAgent._parse_event_guest_names(
        "Two guys, first one is Chetan Shentge and second one is Pawan Shentge.",
    )
    assert names == ["Chetan Shentge", "Pawan Shentge"]
