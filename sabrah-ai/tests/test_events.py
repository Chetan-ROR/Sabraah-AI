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
    assert args["artist"] == "Zakir Khan"
    assert "search" not in args
    jaipur_open = ConversationAgent._event_search_args(
        "I want to go to Jaipur for some event. So do you have any idea? "
        "Is there any event ongoing?"
    )
    assert jaipur_open == {"city": "Jaipur"}


def test_search_events_falls_back_to_nearby_city() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from app.services.events_client import SuperTravelEventsClient

    events = SuperTravelEventsClient("http://127.0.0.1:8002")
    events._request = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "data": [
                {
                    "id": 4,
                    "name": "Ajmer Night Live",
                    "venue_name": "Ajmer Club",
                    "locations": {"name": "Ajmer"},
                    "start_price": "900",
                }
            ]
        }
    )
    result = asyncio.run(
        events.search(user_access_token="jwt", city="Jaipur", session_id="s1")
    )
    assert result["nearby"] is True
    assert result["results"][0]["name"] == "Ajmer Night Live"


def test_event_artist_from_spoken_name() -> None:
    from app.agents import ConversationAgent

    text = "I want an event for Jakhir Khan. So, do you have any events in the list?"
    assert ConversationAgent._is_event_intent(text)
    assert ConversationAgent._parse_event_artist(text) == "Zakir Khan"
    args = ConversationAgent._event_search_args(text)
    assert args["artist"] == "Zakir Khan"
    assert "search" not in args
    assert "city" not in args


def test_event_artist_filters_current_list() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from app.agents import ConversationAgent
    from app.models import SessionState

    agent = ConversationAgent.__new__(ConversationAgent)
    agent._tools = type("T", (), {"execute": AsyncMock()})()
    session = SessionState(session_id="s1")
    session.memory.user_goal = "book_event"
    session.memory.booking_mode = "event"
    session.memory.flow_step = "event_pick"
    session.last_offerings["events"] = [
        {"id": "1", "name": "Sunburn Arena", "artist_name": "DJ Snake"},
        {"id": "2", "name": "Ha Ha Ha Tour", "artist_name": "Zakir Khan"},
    ]
    spoken = asyncio.run(
        agent._handle_event_flow(
            session,
            "I want an event for Jakhir Khan. So, do you have any events in the list?",
            [],
        )
    )
    assert "zakir khan" in spoken.lower()
    assert "ha ha ha" in spoken.lower()
    assert "sunburn" not in spoken.lower()
    agent._tools.execute.assert_not_awaited()


def test_event_artist_searches_without_city() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from app.agents import ConversationAgent
    from app.models import SessionState

    agent = ConversationAgent.__new__(ConversationAgent)
    agent._tools = type(
        "T",
        (),
        {
            "execute": AsyncMock(
                return_value={
                    "results": [
                        {
                            "id": "8",
                            "name": "Ha Ha Ha Tour",
                            "artist_name": "Zakir Khan",
                            "city": "Jaipur",
                        }
                    ]
                }
            )
        },
    )()
    agent._update_memory_from_tool = lambda *args, **kwargs: None
    agent._attach_trains_to_event_city = AsyncMock(return_value="")
    session = SessionState(session_id="s1")
    spoken = asyncio.run(
        agent._handle_event_flow(
            session,
            "I want an event for Jakhir Khan. So, do you have any events in the list?",
            [],
        )
    )
    assert session.memory.event_artist == "Zakir Khan"
    assert "ha ha ha" in spoken.lower()
    agent._tools.execute.assert_awaited()
    called = agent._tools.execute.await_args
    assert called.args[0] == "search_events"
    assert called.args[1].get("artist") == "Zakir Khan"
    assert not called.args[1].get("city")


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


def test_event_flow_asks_city_when_missing() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from app.agents import ConversationAgent
    from app.models import SessionState

    agent = ConversationAgent.__new__(ConversationAgent)
    agent._tools = type(
        "T",
        (),
        {
            "execute": AsyncMock(
                return_value={"results": [{"id": "1", "name": "Jazz Fest", "city": "Mumbai"}]}
            )
        },
    )()
    session = SessionState(session_id="s1")
    session.memory.user_goal = "book_event"
    session.memory.booking_mode = "event"
    session.memory.flow_step = "event_search"
    reply = asyncio.run(agent._handle_event_flow(session, "Book an event", []))
    assert "city" in reply.lower()
    agent._tools.execute.assert_not_awaited()
    reply = asyncio.run(agent._handle_event_flow(session, "Mumbai", []))
    assert agent._tools.execute.await_args.args[0] == "search_events"
    assert agent._tools.execute.await_args.args[1]["city"] == "Mumbai"


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
    assert ConversationAgent._wants_venue_list_read("Can you read out, please?")
    assert ConversationAgent._wants_venue_list_read("tumhare pass kya list of venues btao")
    assert ConversationAgent._title_city("nodia") == "Noida"


def test_one_guest_is_a_count() -> None:
    from app.agents import ConversationAgent

    assert ConversationAgent._parse_passenger_count("One guest.") == 1
    assert ConversationAgent._parse_event_guest_names("One guest.") == []
    assert ConversationAgent._parse_passenger_count("Around 200.") == 200
    assert ConversationAgent._parse_passenger_count("Two hundreds.") == 200
    assert ConversationAgent._parse_passenger_count("Two hundred gates.") == 200
    assert ConversationAgent._parse_passenger_count("two hundred guests") == 200


def test_guest_details_are_not_treated_as_names() -> None:
    from app.agents import ConversationAgent

    text = (
        "Mobile number is 8458916116. Gender is male. "
        "And date of birth is 31 August 1999."
    )
    assert ConversationAgent._parse_event_guest_names(text) == []
    assert ConversationAgent._parse_event_guest_names(
        "Rahul Sharma, rahul@gmail.com, 9876543210, male, 12 January 1990"
    ) == ["Rahul Sharma"]
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


def test_flight_booking_url_is_passenger_page() -> None:
    from app.agents import ConversationAgent
    from app.models import SessionState

    agent = ConversationAgent.__new__(ConversationAgent)
    session = SessionState(session_id="s1")
    session.last_offerings["flight_checkout"] = {
        "booking_url": "https://app.sabraah.com/preview/flights/review"
    }
    session.travelers = [
        {
            "name": "Arjit Kumar",
            "first_name": "Arjit",
            "last_name": "Kumar",
            "email": "arjit@gmail.com",
            "phone": "8458916116",
            "gender": "male",
            "dob": "1999-08-31",
        }
    ]
    url = agent._flight_passenger_booking_url(session)
    assert "/flight-details" in url
    assert "/preview/flights/review" not in url
    assert "guest_data=" in url


def test_flight_checkout_asks_passenger_details_first() -> None:
    from app.agents import ConversationAgent
    from app.models import SessionState

    agent = ConversationAgent.__new__(ConversationAgent)
    session = SessionState(session_id="s1")
    session.memory.passenger_count = 1
    session.memory.selected_flight_id = "1"
    session.last_offerings["flight_checkout"] = {
        "booking_url": "https://app.sabraah.com/preview/flights/review"
    }
    reply = agent._open_flight_checkout(session)
    assert session.memory.flow_step == "flight_guests"
    assert "name" in reply.lower()
    assert "opening the passenger details page" not in reply.lower()


def test_concert_query_keeps_type_in_search() -> None:
    from app.agents import ConversationAgent

    args = ConversationAgent._event_search_args("concerts in mumbai")
    assert args["city"] == "Mumbai"
    assert "concert" in args.get("search", "")
    assert ConversationAgent._is_event_intent("any theme park in Jaipur")


def test_event_list_asks_to_add_to_existing_trip() -> None:
    from app.agents import ConversationAgent
    from app.models import SessionState

    agent = ConversationAgent.__new__(ConversationAgent)
    session = SessionState(session_id="s1")
    session.memory.source = "Pune"
    session.memory.destination = "Jaipur"
    session.last_offerings["trains"] = [{"id": "1", "name": "Train"}]
    speech = agent._speak_event_names(
        [{"id": "12", "name": "Sunburn Arena"}],
        session,
    )
    assert "sunburn arena" in speech.lower()
    assert "add this event to your existing trip" in speech.lower()
    assert session.memory.flow_step == "event_add_trip"


def test_profile_email_phone_are_not_reasked() -> None:
    from app.agents import ConversationAgent
    from app.models import SessionState

    agent = ConversationAgent.__new__(ConversationAgent)
    session = SessionState(
        session_id="s1",
        customer_email="rahul@gmail.com",
        customer_phone="9876543210",
    )
    session.memory.flow_step = "event_guests"
    session.memory.selected_event_id = "12"
    reply = agent._collect_event_guests(session, "Rahul Sharma")
    guest = session.travelers[0]
    assert guest["email"] == "rahul@gmail.com"
    assert guest["phone"] == "9876543210"
    lowered = reply.lower()
    assert "gender" in lowered
    assert "email" not in lowered
    assert "phone" not in lowered


def test_flight_spoken_option_includes_cabin() -> None:
    from app.agents import ConversationAgent

    spoken = ConversationAgent._format_spoken_option(
        1,
        {
            "name": "6E 123",
            "airline": "IndiGo",
            "cabin": "economy",
            "departure_time": "06:10",
            "arrival_time": "08:05",
            "duration": "1h 55m",
            "price_label": "INR 4500",
        },
    )
    assert "economy class" in spoken.lower()
    assert spoken.startswith("Option 1.")


def test_medical_need_sets_assistance_and_direct() -> None:
    from app.agents import ConversationAgent
    from app.models import SessionState

    session = SessionState(session_id="s1")
    ConversationAgent._ingest_trip_signals(
        session, "Wheelchair assistance, I need a direct flight."
    )
    assert session.memory.accessibility_needed is True
    assert session.memory.direct_only is True
    assert session.memory.medical_notes


def test_emergency_purpose_sorts_earliest_first() -> None:
    from app.agents import ConversationAgent
    from app.models import SessionState

    session = SessionState(session_id="s1")
    ConversationAgent._ingest_trip_signals(session, "This is an emergency.")
    assert session.memory.trip_purpose == "emergency"
    assert session.memory.train_preference == "fastest"
    assert session.memory.value_priority == "time"

    agent = ConversationAgent.__new__(ConversationAgent)
    rows = [
        {"name": "Late", "departure_time": "18:00", "duration": "2h"},
        {"name": "Early", "departure_time": "06:10", "duration": "2h 10m"},
        {"name": "Mid", "departure_time": "09:00", "duration": "1h"},
    ]
    ordered = agent._order_results(session, rows)
    assert ordered[0]["name"] == "Early"
    option_n, picked, _ = agent._recommend_option(rows, session.memory)
    assert picked["name"] == "Early"
    assert option_n == 2


def test_medical_purpose_asks_what_happened() -> None:
    from app.agents import ConversationAgent
    from app.models import SessionState

    session = SessionState(session_id="s1")
    ConversationAgent._ingest_trip_signals(session, "Medical.")
    assert session.memory.trip_purpose == "medical"
    assert session.memory.medical_notes is None
    agent = ConversationAgent.__new__(ConversationAgent)
    assert agent._needs_medical_followup(session.memory)
    ask = agent._ask_medical_followup(session)
    assert "what happened" in ask.lower()
    pending = agent._capture_medical_followup(
        session, "Surgery for my father. No wheelchair."
    )
    assert pending is None
    assert "surgery" in (session.memory.medical_notes or "").lower()


def test_medical_followup_emergency_switches_to_fastest() -> None:
    from app.agents import ConversationAgent
    from app.models import SessionState

    agent = ConversationAgent.__new__(ConversationAgent)
    session = SessionState(session_id="s1")
    session.memory.trip_purpose = "medical"
    session.memory.flow_step = "medical_reason"
    pending = agent._capture_medical_followup(session, "It's an emergency, heart surgery.")
    assert pending is None
    assert session.memory.trip_purpose == "emergency"
    assert session.memory.train_preference == "fastest"


def test_venue_intent_is_not_event_or_hotel() -> None:
    from app.agents import ConversationAgent

    assert ConversationAgent._is_venue_intent("I want to book an event venue")
    assert ConversationAgent._is_venue_intent("Wedding venue in Jaipur")
    assert not ConversationAgent._is_event_intent("I want to book an event venue")
    assert not ConversationAgent._is_hotel_intent("Wedding venue in a hotel banquet")
    assert ConversationAgent._is_event_intent("concerts in Mumbai")
    assert ConversationAgent._is_hotel_intent("Book a hotel in Goa")


def test_search_venues_builds_rfp_urls() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from app.services.travel_client import TravelBackendClient
    from app.tools import TravelToolExecutor

    client = TravelBackendClient(
        "http://travel.test", "k", web_app_base_url="http://127.0.0.1:3000"
    )
    client.request = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "results": [
                {
                    "id": 21,
                    "name": "Royal Banquet Jaipur",
                    "type": "banquet",
                    "category_name": "Banquet",
                    "capacity": 300,
                    "price": "45000",
                }
            ]
        }
    )
    executor = TravelToolExecutor(client)
    result = asyncio.run(
        executor.execute(
            "search_venues",
            {
                "city": "Jaipur",
                "event_type": "wedding",
                "venue_kind": "banquet",
                "guests": 200,
                "budget": 500000,
                "services": "food_decoration",
            },
            session_id="s1",
        )
    )
    assert result["count"] >= 1
    card = result["results"][0]
    assert "Royal Banquet Jaipur" in card["name"]
    assert "/venue-booking-detail/21" in card["booking_url"]
    assert "guests=200" in card["booking_url"]
    assert "/venue-list?" in result["booking_url"]
    assert "location=Jaipur" in result["booking_url"]
    params = client.request.await_args.kwargs["params"]
    assert params["search"] == "Jaipur"
    assert "banquet" not in params["search"].lower()
    assert "wedding" not in params["search"].lower()
    assert "min_price" not in params
    assert result.get("nearby") is False


def test_search_venues_uses_catalog_when_city_search_empty() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from app.services.travel_client import TravelBackendClient
    from app.tools import TravelToolExecutor

    client = TravelBackendClient(
        "http://travel.test", "k", web_app_base_url="http://127.0.0.1:3000"
    )

    async def fake_request(method, path, **kwargs):
        search = (kwargs.get("params") or {}).get("search")
        if search:
            return {"results": []}
        return {
            "results": [
                {
                    "id": 11,
                    "name": "hmashu 123 new",
                    "category_name": "Banquet Hall",
                    "address_summary": "noida 125, Noida",
                    "capacity": 500,
                }
            ]
        }

    client.request = AsyncMock(side_effect=fake_request)  # type: ignore[method-assign]
    executor = TravelToolExecutor(client)
    result = asyncio.run(
        executor.execute("search_venues", {"city": "Noida", "venue_kind": "banquet"}, session_id="s1")
    )
    assert result["count"] == 1
    assert "hmashu" in result["results"][0]["name"].lower()


def test_venue_flow_reads_list_when_asked() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from app.agents import ConversationAgent
    from app.models import SessionState

    agent = ConversationAgent.__new__(ConversationAgent)
    agent._tools = type("T", (), {"execute": AsyncMock()})()
    session = SessionState(session_id="s1")
    session.memory.user_goal = "book_venue"
    session.memory.booking_mode = "venue"
    session.memory.venue_event_type = "wedding"
    session.memory.passenger_count = 200
    session.memory.destination = "Noida"
    session.memory.date_flexible = True
    session.memory.venue_kind = "banquet"
    session.memory.venue_services = "food"
    session.memory.budget = 0
    session.memory.flow_step = "venue_open"
    session.last_offerings["venues"] = [
        {"id": "11", "name": "hmashu 123 new", "hint": "Noida", "city": "Noida"},
        {"id": "12", "name": "the weeding party", "hint": "Noida", "city": "Noida"},
    ]
    spoken = asyncio.run(agent._handle_venue_flow(session, "Can you read out, please?", []))
    assert "hmashu" in spoken.lower()
    assert "weeding" in spoken.lower()
    assert "noida" in spoken.lower()
    agent._tools.execute.assert_not_awaited()


def test_tell_me_the_list_at_start_includes_location() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from app.agents import ConversationAgent
    from app.models import SessionState

    assert ConversationAgent._wants_venue_list_read("Can you tell me the list?")
    agent = ConversationAgent.__new__(ConversationAgent)
    agent._tools = type(
        "T",
        (),
        {
            "execute": AsyncMock(
                return_value={
                    "results": [
                        {
                            "id": "11",
                            "name": "hmashu 123 new",
                            "city": "Noida",
                        }
                    ]
                }
            )
        },
    )()
    agent._update_memory_from_tool = lambda *args, **kwargs: None
    session = SessionState(session_id="s1")
    spoken = asyncio.run(
        agent._handle_venue_flow(session, "Can you tell me the list?", [])
    )
    assert "hmashu" in spoken.lower()
    assert "noida" in spoken.lower()
    assert session.memory.flow_step == "venue_pick"
    assert session.last_offerings.get("venue_checkout") in (None, {})


def test_search_venues_falls_back_nearby() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from app.services.travel_client import TravelBackendClient
    from app.tools import TravelToolExecutor

    client = TravelBackendClient(
        "http://travel.test", "k", web_app_base_url="http://127.0.0.1:3000"
    )
    client.request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {"results": []},
            {
                "results": [
                    {
                        "id": 9,
                        "name": "Ajmer Lawn",
                        "type": "banquet",
                        "address": "Civil Lines, Ajmer",
                        "capacity": 250,
                    }
                ]
            },
        ]
    )
    executor = TravelToolExecutor(client)
    result = asyncio.run(
        executor.execute(
            "search_venues",
            {"city": "Jaipur", "event_type": "wedding", "venue_kind": "banquet"},
            session_id="s1",
        )
    )
    assert result["nearby"] is True
    assert result["count"] == 1
    assert result["results"][0]["city"] == "Ajmer"
    assert client.request.await_count >= 2


def test_venue_short_answers_food_and_skip() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from app.agents import ConversationAgent
    from app.models import SessionState

    agent = ConversationAgent.__new__(ConversationAgent)
    agent._tools = type(
        "T",
        (),
        {"execute": AsyncMock(return_value={"results": [], "booking_url": "/venue-list"})},
    )()
    session = SessionState(session_id="s1")
    ConversationAgent._maybe_set_booking_mode(session, "I want to book an event venue")
    asyncio.run(agent._handle_venue_flow(session, "I want to book an event venue", []))
    asyncio.run(agent._handle_venue_flow(session, "Wedding", []))
    asyncio.run(agent._handle_venue_flow(session, "Around 200.", []))
    asyncio.run(agent._handle_venue_flow(session, "Jaipur", []))
    asyncio.run(agent._handle_venue_flow(session, "Skip.", []))
    sixth = asyncio.run(agent._handle_venue_flow(session, "Banquet.", []))
    assert "food" in sixth.lower()
    seventh = asyncio.run(agent._handle_venue_flow(session, "Food.", []))
    assert "budget" in seventh.lower()
    assert session.memory.venue_services == "food"
    assert session.memory.passenger_count == 200
    eighth = asyncio.run(agent._handle_venue_flow(session, "Skip.", []))
    assert session.memory.budget == 0
    agent._tools.execute.assert_awaited()


def test_parse_venue_services_food_period() -> None:
    from app.agents import ConversationAgent

    assert ConversationAgent._parse_venue_services("Food.") == "food"
    assert ConversationAgent._parse_venue_services("Yes.") is None
    assert ConversationAgent._parse_venue_kind("Banquet.") == "banquet"


def test_venue_flow_asks_in_order_then_searches() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from app.agents import ConversationAgent
    from app.models import SessionState

    agent = ConversationAgent.__new__(ConversationAgent)
    agent._tools = type(
        "T",
        (),
        {
            "execute": AsyncMock(
                return_value={
                    "results": [
                        {
                            "id": "21",
                            "name": "Royal Banquet Jaipur",
                            "city": "Jaipur",
                            "hint": "banquet",
                            "booking_url": "http://127.0.0.1:3000/venue-booking-detail/21",
                        }
                    ],
                    "booking_url": "http://127.0.0.1:3000/venue-list?location=Jaipur",
                    "rfp_url": "http://127.0.0.1:3000/venue-booking-confirm",
                }
            )
        },
    )()
    session = SessionState(session_id="s1")
    ConversationAgent._maybe_set_booking_mode(session, "I want to book an event venue")
    first = asyncio.run(agent._handle_venue_flow(session, "I want to book an event venue", []))
    assert "event" in first.lower()
    second = asyncio.run(agent._handle_venue_flow(session, "Wedding", []))
    assert "guest" in second.lower()
    third = asyncio.run(agent._handle_venue_flow(session, "Around 200.", []))
    assert "city" in third.lower() or "location" in third.lower()
    assert session.memory.passenger_count == 200
    fourth = asyncio.run(agent._handle_venue_flow(session, "Jaipur", []))
    assert "date" in fourth.lower()
    fifth = asyncio.run(agent._handle_venue_flow(session, "skip", []))
    assert "hotel" in fifth.lower() or "banquet" in fifth.lower()
    sixth = asyncio.run(agent._handle_venue_flow(session, "Banquet", []))
    assert "food" in sixth.lower() or "decoration" in sixth.lower()
    seventh = asyncio.run(agent._handle_venue_flow(session, "Food and decoration", []))
    assert "budget" in seventh.lower()
    eighth = asyncio.run(agent._handle_venue_flow(session, "Budget 5 lakh", []))
    assert session.memory.venue_event_type == "wedding"
    assert session.memory.passenger_count == 200
    assert session.memory.destination == "Jaipur"
    assert session.memory.venue_kind == "banquet"
    assert session.memory.venue_services == "food_decoration"
    assert session.memory.budget == 500000
    assert session.memory.flow_step == "venue_pick"
    assert "royal banquet" in eighth.lower()
    assert "jaipur" in eighth.lower()
    assert "opening the venue list" not in eighth.lower()
    agent._tools.execute.assert_awaited()


def test_venue_something_else_asks_what_then_events_api() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from app.agents import ConversationAgent
    from app.models import SessionState

    agent = ConversationAgent.__new__(ConversationAgent)
    agent._tools = type("T", (), {"execute": AsyncMock()})()
    session = SessionState(session_id="s1")
    first = asyncio.run(agent._handle_venue_flow(session, "I want to book an event venue", []))
    assert "something else" in first.lower()
    second = asyncio.run(agent._handle_venue_flow(session, "Something else.", []))
    assert "what is the event" in second.lower()
    assert "concert" not in second.lower()
    assert "comedy" not in second.lower()
    assert session.memory.user_goal == "book_venue"
    third = asyncio.run(agent._handle_venue_flow(session, "Comedy", []))
    assert session.memory.user_goal == "book_event"
    assert session.memory.event_category == "comedy"
    assert "city" in third.lower()
    assert "comedy" in third.lower()
    agent._tools.execute.assert_not_awaited()


def test_venue_something_else_comedy_searches_events_when_city_known() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from app.agents import ConversationAgent
    from app.models import SessionState

    agent = ConversationAgent.__new__(ConversationAgent)
    agent._tools = type(
        "T",
        (),
        {
            "execute": AsyncMock(
                return_value={
                    "results": [
                        {
                            "id": "9",
                            "name": "Late Night Laughs",
                            "city": "Mumbai",
                            "hint": "comedy",
                        }
                    ]
                }
            )
        },
    )()
    agent._update_memory_from_tool = lambda *args, **kwargs: None
    agent._attach_trains_to_event_city = AsyncMock(return_value="")
    agent._speak_event_names = lambda rows, session: "Here are events. Late Night Laughs."
    session = SessionState(session_id="s1")
    session.memory.destination = "Mumbai"
    asyncio.run(agent._handle_venue_flow(session, "I want to book an event venue", []))
    asyncio.run(agent._handle_venue_flow(session, "Something else.", []))
    spoken = asyncio.run(agent._handle_venue_flow(session, "Comedy", []))
    assert session.memory.user_goal == "book_event"
    assert "late night laughs" in spoken.lower()
    agent._tools.execute.assert_awaited()
    called = agent._tools.execute.await_args
    assert called.args[0] == "search_events"
    assert called.args[1].get("city") == "Mumbai"
    assert "comedy" in str(called.args[1].get("search") or "").lower()


def test_venue_something_else_baby_shower_stays_on_venues() -> None:
    import asyncio

    from app.agents import ConversationAgent
    from app.models import SessionState

    agent = ConversationAgent.__new__(ConversationAgent)
    session = SessionState(session_id="s1")
    asyncio.run(agent._handle_venue_flow(session, "I want to book an event venue", []))
    asyncio.run(agent._handle_venue_flow(session, "Something else.", []))
    spoken = asyncio.run(agent._handle_venue_flow(session, "Baby shower", []))
    assert session.memory.user_goal == "book_venue"
    assert session.memory.venue_event_type == "baby shower"
    assert "guest" in spoken.lower()


def test_venue_concert_answer_uses_events_api() -> None:
    import asyncio

    from app.agents import ConversationAgent
    from app.models import SessionState

    agent = ConversationAgent.__new__(ConversationAgent)
    session = SessionState(session_id="s1")
    asyncio.run(agent._handle_venue_flow(session, "I want to book an event venue", []))
    spoken = asyncio.run(agent._handle_venue_flow(session, "Concert", []))
    assert session.memory.user_goal == "book_event"
    assert session.memory.event_category == "concert"
    assert "city" in spoken.lower()


def test_venue_one_shot_does_not_become_charter() -> None:
    from app.agents import ConversationAgent
    from app.models import SessionState

    agent = ConversationAgent.__new__(ConversationAgent)
    session = SessionState(session_id="s1")
    line = (
        "Wedding venue in Jaipur for 200 guests, banquet, "
        "food and decoration, budget 5 lakh"
    )
    ConversationAgent._maybe_set_booking_mode(session, line)
    assert session.memory.user_goal == "book_venue"
    assert session.memory.passenger_count == 200
    assert session.memory.venue_kind == "banquet"
    assert session.memory.budget == 500000
    hint = agent._handle_large_group_hint(session, line)
    assert hint is None
    assert session.memory.booking_mode == "venue"


def test_venue_pick_opens_rfp_url() -> None:
    from app.agents import ConversationAgent
    from app.models import SessionState

    session = SessionState(session_id="s1")
    session.memory.user_goal = "book_venue"
    session.memory.selected_venue_id = "21"
    session.last_offerings["venues"] = [
        {
            "id": "21",
            "name": "Royal Banquet Jaipur",
            "booking_url": "http://127.0.0.1:3000/venue-booking-detail/21?guests=200",
        }
    ]
    url = ConversationAgent._selected_venue_booking_url(session)
    assert "/venue-booking-detail/21" in url
    assert "guests=200" in url
