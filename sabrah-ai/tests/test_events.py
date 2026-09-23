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
            "locations": {"id": 9, "name": "Mumbai"},
            "start_date_time": "2026-10-12 19:00:00",
            "start_price": "2500.00",
        },
        web_app_base_url="http://127.0.0.1:3000",
    )
    assert card["id"] == "12"
    assert card["city"] == "Mumbai"
    assert card["booking_url"] == "http://127.0.0.1:3000/event-booking-detail?event_id=12"
    assert card["detail_url"] == "http://127.0.0.1:3000/event-detail/12"
    assert "INR 2500.00" in card["hint"]


def test_map_event_details_keeps_tickets_and_link() -> None:
    details = map_event_details(
        {
            "id": 7,
            "name": "Comedy Night",
            "event_venues": [
                {"venue_name": "Phoenix Arena", "city": {"id": 12, "name": "Bhopal"}}
            ],
            "tickets": [{"id": 3, "name": "Gold", "price": "999", "available_seats": 40}],
            "schedules": [{"id": 9, "event_date": "2026-11-01", "start_time": "20:00:00"}],
        }
    )
    assert details["booking_url"] == "/event-booking-detail?event_id=7"
    assert details["city"] == "Bhopal"
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


def test_go_to_city_date_asks_about_events() -> None:
    from app.agents import ConversationAgent

    text = "I want to go to Jaipur on 19 September, is there any event?"
    assert ConversationAgent._is_event_intent(text)
    assert ConversationAgent._parse_destination_mention(text) == "Jaipur"
    assert ConversationAgent._event_search_args(text) == {"city": "Jaipur"}
    date = ConversationAgent._parse_date_from_text(text)
    assert date is not None and date.endswith("-09-19")


def test_event_origin_city_from_speech() -> None:
    from app.agents import ConversationAgent

    assert ConversationAgent._parse_origin_mention("from Pune", "Jaipur") == "Pune"
    assert ConversationAgent._parse_origin_mention(
        "Pune", "Jaipur", allow_bare=True
    ) == "Pune"
    assert ConversationAgent._parse_origin_mention("Jaipur", "Jaipur") is None
    src, dst = ConversationAgent._parse_route_from_text(
        "Pune to Jaipur, is there any event?"
    )
    assert src == "Pune"
    assert dst == "Jaipur"


def test_read_event_names_is_not_a_new_search() -> None:
    from app.agents import ConversationAgent

    text = "Can you read the event's name, please?"
    assert ConversationAgent._wants_event_list_read(text)
    assert ConversationAgent._event_search_args(text) == {}


def test_one_guest_is_a_count() -> None:
    from app.agents import ConversationAgent

    assert ConversationAgent._parse_passenger_count("One guest.") == 1
    assert ConversationAgent._parse_event_guest_names("One guest.") == []


def test_guest_details_are_not_treated_as_names() -> None:
    from app.agents import ConversationAgent

    text = (
        "Mobile number is 8458916116. Gender is male. "
        "And date of birth is 31 August 1999."
    )
    assert ConversationAgent._parse_event_guest_names(text) == []
    assert ConversationAgent._parse_event_phone(text) == "8458916116"
    assert ConversationAgent._parse_event_gender(text) == "male"
    assert ConversationAgent._parse_event_dob(text) == "1999-08-31"


def test_single_first_name_is_kept() -> None:
    from app.agents import ConversationAgent

    assert ConversationAgent._parse_event_guest_names("Arjit") == ["Arjit"]
    assert ConversationAgent._parse_event_guest_names("Mumbai") == []


def test_collect_one_guest_asks_for_name() -> None:
    from app.agents import ConversationAgent
    from app.models import SessionState

    agent = ConversationAgent.__new__(ConversationAgent)
    session = SessionState(session_id="s1")
    session.memory.flow_step = "event_guests"
    session.memory.selected_event_id = "12"
    reply = agent._collect_event_guests(session, "One guest.")
    assert session.memory.passenger_count == 1
    assert "last name" in reply.lower()
    assert "how many guests" not in reply.lower()


def test_collect_saves_phone_gender_dob() -> None:
    from app.agents import ConversationAgent
    from app.models import SessionState

    agent = ConversationAgent.__new__(ConversationAgent)
    session = SessionState(session_id="s1")
    session.memory.flow_step = "event_guests"
    session.travelers = [
        {"name": "Arjit", "first_name": "Arjit", "last_name": "Kumar"}
    ]
    reply = agent._collect_event_guests(
        session,
        "Mobile number is 8458916116. Gender is male. And date of birth is 31 August 1999.",
    )
    guest = session.travelers[0]
    assert guest["phone"] == "8458916116"
    assert guest["gender"] == "male"
    assert guest["dob"] == "1999-08-31"
    assert "email" in reply.lower()
    assert "mumbai" not in reply.lower()
