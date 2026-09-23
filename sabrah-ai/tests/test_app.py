"""Sabrah AI pytest suite with mocked OpenAI / ElevenLabs / Travel Backend."""

from __future__ import annotations

import base64
import json
import os
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple, Union
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

# Dummy env before settings import/cache.
os.environ["OPENAI_API_KEY"] = "test-openai-key"
os.environ["ELEVENLABS_API_KEY"] = "test-elevenlabs-key"
os.environ["ELEVENLABS_VOICE_ID"] = "test-voice"
os.environ["ELEVENLABS_MODEL_ID"] = "eleven_multilingual_v2"
os.environ["TRAVEL_BACKEND_API_KEY"] = "test-api-key"
os.environ["TRAVEL_BACKEND_BASE_URL"] = "http://127.0.0.1:8001"
os.environ["APP_ENV"] = "test"

from app.config.settings import get_settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.agents import ConversationAgent  # noqa: E402
from app.prompts import set_agent_store  # noqa: E402
from app.providers import ProviderError  # noqa: E402
from app.services.agent_store import AgentStore  # noqa: E402
from app.services.session_store import InMemorySessionStore  # noqa: E402
from app.services.travel_client import TravelBackendClient, TravelBackendError  # noqa: E402
from app.tools import TravelToolExecutor  # noqa: E402

get_settings.cache_clear()


class FakeLLM:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._queue: List[Any] = []

    def enqueue(self, message: Any) -> None:
        self._queue.append(message)

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Union[str, Dict[str, Any]] = "auto",
    ) -> Any:
        self.calls.append({"messages": messages, "tools": tools})
        if not self._queue:
            return SimpleNamespace(content="How can I help you today?", tool_calls=None)
        return self._queue.pop(0)


class FakeSTT:
    def __init__(self, text: str = "Hi Sabrah") -> None:
        self.text = text

    async def transcribe(self, audio_bytes: bytes, filename: str = "audio.webm") -> str:
        if not audio_bytes:
            raise ProviderError("empty", code="empty_audio")
        return self.text


class FakeTTS:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.texts: List[str] = []

    async def synthesize(self, text: str) -> bytes:
        self.texts.append(text)
        if self.fail:
            raise ProviderError("TTS down", code="elevenlabs_failed")
        return b"FAKEMP3"


class FakeTravelClient(TravelBackendClient):
    def __init__(self) -> None:
        super().__init__(
            base_url="http://travel.test",
            api_key="test-api-key",
            web_app_base_url="http://127.0.0.1:3000",
        )
        self.calls: List[Tuple[str, str, Optional[dict]]] = []
        self.available = True
        self.raise_unavailable = False
        self.generic_error = False

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        session_id: Optional[str] = None,
        user_access_token: Optional[str] = None,
        unwrap: bool = True,
    ) -> dict:
        self.calls.append((method, path, json or params))
        if self.raise_unavailable:
            raise TravelBackendError("down", code="travel_unavailable")
        if self.generic_error:
            raise TravelBackendError(
                "An unexpected server error occurred.",
                code="travel_http_error",
            )
        if path in {"/api/v1/trains/search", "/api/v1/trains/train-list/"}:
            origin = (json or params or {}).get("source") or (params or {}).get("origin")
            dest = (json or params or {}).get("destination") or (params or {}).get(
                "destination"
            )
            return {
                "results": [
                    {
                        "id": "51683",
                        "name": "KNW-BIR PASSENGER",
                        "train_name": "KNW-BIR PASSENGER",
                        "train_number": "51683",
                        "source": origin or "Delhi",
                        "destination": dest or "Mumbai",
                        "departure_time": "08:10:00",
                        "arrival_time": "09:10:00",
                        "running_time": "1h 0m",
                        "price": 1850,
                        "currency": "INR",
                    }
                ],
                "provider": "MOCK",
                "count": 1,
            }
        if path == "/api/v1/flights/search":
            return {
                "tui": "tui-test",
                "flights": [
                    {
                        "index": "6E|2",
                        "airline_name": "IndiGo",
                        "flight_number": "6E 2345",
                        "departure_time": "06:10",
                        "arrival_time": "08:25",
                        "duration": "02h 15m",
                        "price": "5732.0",
                        "price_label": "₹5,732",
                        "selection": {
                            "index": "6E|2",
                            "order_id": 1,
                            "amount": "5632.0",
                            "tui": "tui-test",
                        },
                    }
                ],
            }
        if path == "/api/v1/flights/fare-group-details":
            ssr_options = [
                {
                    "category": "meal",
                    "title": "Veg Meal (For Retail Fare)",
                    "price_label": "₹400",
                    "code": "VGML",
                },
                {
                    "category": "meal",
                    "title": "Paneer Tikka Sandwich Combo",
                    "price_label": "₹500",
                    "code": "PTSC",
                },
                {
                    "category": "priority_checkin",
                    "title": "Priority Check-In",
                    "price_label": "₹430",
                    "code": "PRIO",
                },
                {
                    "category": "baggage",
                    "title": "Prepaid Excess Baggage – 3 Kg",
                    "price_label": "₹2,247",
                    "code": "XB3",
                },
                {
                    "category": "baggage",
                    "title": "Prepaid Excess Baggage – 5 Kg",
                    "price_label": "₹3,478",
                    "code": "XB5",
                },
            ]
            return {
                "fare_types": [
                    {
                        "priced_tui": "priced-test",
                        "fare_name": "SAVER",
                        "fare_summary": {
                            "total": "5898.0",
                            "total_label": "₹5,898",
                        },
                        "ssr_options": ssr_options,
                    },
                    {
                        "priced_tui": "priced-flexi",
                        "fare_name": "FLEXI PLUS FARE",
                        "fare_summary": {
                            "total": "6394.0",
                            "total_label": "₹6,394",
                        },
                        "ssr_options": ssr_options,
                    },
                    {
                        "priced_tui": "priced-super",
                        "fare_name": "SUPER FARE",
                        "fare_summary": {
                            "total": "7548.0",
                            "total_label": "₹7,548",
                        },
                        "ssr_options": ssr_options,
                    },
                ]
            }
        if path == "/api/v1/bookings" and method == "POST":
            if not (json or {}).get("confirmed"):
                raise TravelBackendError("need confirmation", code="travel_http_error")
            return {
                "booking_id": "BK-TEST123",
                "status": "confirmed",
                "item_type": json["item_type"],
                "item_id": json["item_id"],
                "passenger_name": json["passenger_name"],
                "passenger_count": json.get("passenger_count", 1),
                "total_price": 1850,
                "currency": "INR",
                "created_at": "2026-08-20T10:00:00Z",
            }
        if path.endswith("/cancel"):
            if not (json or {}).get("confirmed"):
                raise TravelBackendError("need confirmation", code="travel_http_error")
            return {
                "booking_id": "BK-TEST123",
                "status": "cancelled",
                "item_type": "train",
                "item_id": "TRAIN-001",
                "passenger_name": "Asha",
                "passenger_count": 1,
                "total_price": 1850,
                "currency": "INR",
                "created_at": "2026-08-20T10:00:00Z",
                "cancelled_at": "2026-08-20T11:00:00Z",
            }
        if path.startswith("/api/v1/bookings/"):
            return {
                "booking_id": path.rsplit("/", 1)[-1],
                "status": "confirmed",
                "item_type": "train",
                "item_id": "TRAIN-001",
                "passenger_name": "Asha",
                "passenger_count": 1,
                "total_price": 1850,
                "currency": "INR",
                "created_at": "2026-08-20T10:00:00Z",
            }
        return {"results": [], "count": 0, "provider": "MOCK"}

    async def health(self) -> bool:
        return self.available


def _tool_call(name: str, arguments: Dict[str, Any], call_id: str = "call_1") -> Any:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


@pytest.fixture
def fake_stack() -> Dict[str, Any]:
    store = InMemorySessionStore(ttl_minutes=60)
    llm = FakeLLM()
    stt = FakeSTT()
    tts = FakeTTS()
    travel = FakeTravelClient()
    tools = TravelToolExecutor(travel)
    agent = ConversationAgent(
        session_store=store,
        llm=llm,
        stt=stt,
        tts=tts,
        tools=tools,
    )
    return {
        "store": store,
        "llm": llm,
        "stt": stt,
        "tts": tts,
        "travel": travel,
        "tools": tools,
        "agent": agent,
    }


@pytest.fixture
def client(fake_stack: Dict[str, Any], tmp_path):
    app = create_app(skip_env_validation=True)
    agent_store = AgentStore(tmp_path / "agents.json")
    with TestClient(app) as test_client:
        app.state.agent = fake_stack["agent"]
        app.state.session_store = fake_stack["store"]
        app.state.travel_client = fake_stack["travel"]
        app.state.agent_store = agent_store
        set_agent_store(agent_store)
        yield test_client, fake_stack


def test_health(client) -> None:
    test_client, stack = client
    response = test_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["travel_backend"] == "reachable"


def test_health_backend_unreachable(client) -> None:
    test_client, stack = client
    stack["travel"].available = False
    response = test_client.get("/health")
    assert response.json()["travel_backend"] == "unreachable"


def test_session_creation(client) -> None:
    test_client, _ = client
    response = test_client.post("/api/v1/sessions")
    assert response.status_code == 200
    assert "session_id" in response.json()
    assert response.json().get("agent_id")


def test_agents_crud(client) -> None:
    test_client, _ = client
    listed = test_client.get("/api/v1/agents")
    assert listed.status_code == 200
    assert len(listed.json()) >= 1
    created = test_client.post(
        "/api/v1/agents",
        json={
            "name": "Test Agent",
            "description": "For unit tests",
            "first_message": "Hello from test agent",
            "system_prompt": "You are a test agent. Reply briefly.",
        },
    )
    assert created.status_code == 201
    agent_id = created.json()["id"]
    patched = test_client.patch(
        f"/api/v1/agents/{agent_id}",
        json={"first_message": "Updated greeting"},
    )
    assert patched.status_code == 200
    assert patched.json()["first_message"] == "Updated greeting"
    session = test_client.post(
        "/api/v1/sessions",
        json={
            "agent_id": agent_id,
            "source": "Admin Console",
            "customer_name": "Admin Tester",
        },
    )
    assert session.status_code == 200
    detail = test_client.get(f"/api/v1/sessions/{session.json()['session_id']}")
    assert detail.status_code == 200
    assert detail.json()["customer_name"] == "Admin Tester"
    assert detail.json()["messages"]
    assert detail.json()["messages"][0]["content"] == "Updated greeting"
    conv = test_client.get("/api/v1/conversations")
    assert conv.status_code == 200
    assert conv.json()["count"] >= 1


def test_admin_page(client) -> None:
    test_client, _ = client
    response = test_client.get("/admin")
    assert response.status_code == 200
    assert "Admin Console" in response.text


def test_conversation_memory(client) -> None:
    import asyncio

    test_client, stack = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    stack["llm"].enqueue(
        SimpleNamespace(content="Where will you be travelling from?", tool_calls=None)
    )
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "I want to go to Mumbai."},
    )
    stored = asyncio.get_event_loop().run_until_complete(stack["store"].get(session))
    assert stored is not None
    assert len(stored.conversation_history) >= 2


def test_text_chat_basic(client) -> None:
    test_client, stack = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    response = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Hi Sabrah"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "Tell me the trip" in data["assistant_text"] or "flight" in data["assistant_text"].lower()
    assert data["audio_base64"] == base64.b64encode(b"FAKEMP3").decode("ascii")
    assert stack["tts"].texts


def test_general_chat_reaches_llm(client) -> None:
    test_client, stack = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Hi Sabrah"},
    )
    stack["llm"].enqueue(
        SimpleNamespace(
            content="I'm doing well. I can help with trains, events, cancel, or refunds.",
            tool_calls=None,
        )
    )
    response = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "How are you today?"},
    )
    assert response.status_code == 200
    assert "doing well" in response.json()["assistant_text"]
    assert stack["llm"].calls


def test_openai_provider_mocked_via_agent(client) -> None:
    test_client, stack = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    stack["llm"].enqueue(SimpleNamespace(content="Understood.", tool_calls=None))
    response = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "How are you today?"},
    )
    assert response.status_code == 200
    assert len(stack["llm"].calls) == 1


def test_elevenlabs_failure_still_returns_text(client) -> None:
    test_client, stack = client
    stack["tts"].fail = True
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    stack["llm"].enqueue(SimpleNamespace(content="Text only reply", tool_calls=None))
    response = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "How are you today?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["assistant_text"] == "Text only reply"
    assert data["audio_base64"] is None
    assert data["tts_error"]


def _book_train_until_search(test_client, session: str, route: str = "Delhi to Mumbai"):
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Hi Sabrah"},
    )
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Book a train"},
    )
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": route},
    )
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Tomorrow."},
    )
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "One passenger."},
    )
    return test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Other."},
    )


def test_tool_calling_search_trains(client) -> None:
    test_client, stack = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    response = _book_train_until_search(test_client, session)
    assert response.status_code == 200
    data = response.json()
    assert "search_trains" in data["tools_used"]
    assert stack["travel"].calls
    assert any(path == "/api/v1/trains/train-list/" for _, path, _ in stack["travel"].calls)
    assert data["memory"]["source"] == "Delhi"
    assert data["memory"]["destination"] == "Mumbai"
    text = data["assistant_text"].lower()
    assert "recommend" in text
    assert "read them out" in text or "pick from the screen" in text


def test_backend_unavailable(client) -> None:
    test_client, stack = client
    stack["travel"].raise_unavailable = True
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    response = _book_train_until_search(test_client, session)
    assert response.status_code == 200
    assert "cannot reach" in response.json()["assistant_text"].lower()


def test_booking_confirmation_required(client) -> None:
    test_client, stack = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    stack["llm"].enqueue(
        SimpleNamespace(
            content=None,
            tool_calls=[
                _tool_call(
                    "create_booking",
                    {
                        "item_type": "train",
                        "item_id": "TRAIN-001",
                        "passenger_name": "Asha",
                        "confirmed": False,
                    },
                )
            ],
        )
    )
    stack["llm"].enqueue(
        SimpleNamespace(
            content="Shall I continue with booking the first option?",
            tool_calls=None,
        )
    )
    response = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Book the first one"},
    )
    assert response.status_code == 200
    assert not any(path == "/api/v1/bookings" for _, path, _ in stack["travel"].calls)


def test_booking_with_confirmation(client) -> None:
    test_client, stack = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    stack["llm"].enqueue(
        SimpleNamespace(
            content=None,
            tool_calls=[
                _tool_call(
                    "create_booking",
                    {
                        "item_type": "flight",
                        "item_id": "6E|2",
                        "passenger_name": "Asha",
                        "confirmed": True,
                        "metadata": {
                            "from_code": "DEL",
                            "to_code": "BOM",
                            "selection": {
                                "index": "6E|2",
                                "order_id": 1,
                                "amount": "5632.0",
                                "tui": "tui-test",
                            },
                        },
                    },
                )
            ],
        )
    )
    stack["llm"].enqueue(
        SimpleNamespace(
            content="Your live fare is locked. Complete payment on Super Travel.",
            tool_calls=None,
        )
    )
    response = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Yes, please book it"},
    )
    assert response.status_code == 200
    assert any(path == "/api/v1/flights/fare-group-details" for _, path, _ in stack["travel"].calls)
    assert "locked" in response.json()["assistant_text"].lower()


def test_cancellation_confirmation(client) -> None:
    test_client, stack = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    stack["llm"].enqueue(
        SimpleNamespace(
            content=None,
            tool_calls=[
                _tool_call(
                    "cancel_booking",
                    {"booking_id": "BK-TEST123", "confirmed": False},
                )
            ],
        )
    )
    stack["llm"].enqueue(
        SimpleNamespace(
            content="Just to confirm, should I cancel booking BK-TEST123?",
            tool_calls=None,
        )
    )
    response = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Cancel my booking"},
    )
    assert response.status_code == 200
    assert not any(path.endswith("/cancel") for _, path, _ in stack["travel"].calls)


def test_voice_endpoint(client) -> None:
    test_client, stack = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    stack["stt"].text = "I want to go to Mumbai"
    stack["llm"].enqueue(
        SimpleNamespace(content="Hi! How can I help you today?", tool_calls=None)
    )
    response = test_client.post(
        "/api/v1/chat/voice",
        data={"session_id": session},
        files={"audio": ("speech.webm", b"fake-audio-bytes", "audio/webm")},
    )
    assert response.status_code == 200
    assert response.json()["user_text"] == "I want to go to Mumbai"


def test_voice_empty_transcript_is_ignored(client) -> None:
    test_client, stack = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]

    async def transcribe(_audio_bytes, filename="audio.webm"):
        raise ProviderError("I could not catch that.", code="speech_not_recognized")

    stack["stt"].transcribe = transcribe
    response = test_client.post(
        "/api/v1/chat/voice",
        data={"session_id": session},
        files={"audio": ("speech.webm", b"fake-audio-bytes", "audio/webm")},
    )
    assert response.status_code == 200
    assert response.json()["ignored"] is True


def test_voice_stt_prompt_leak_is_ignored(client) -> None:
    test_client, stack = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    stack["stt"].text = "Cities, dates, trains, hotels, passengers, options."
    response = test_client.post(
        "/api/v1/chat/voice",
        data={"session_id": session},
        files={"audio": ("speech.webm", b"fake-audio-bytes", "audio/webm")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ignored"] is True
    assert data["user_text"] == ""
    assert data["assistant_text"] == ""


def test_voice_wake_phrase_greets(client) -> None:
    test_client, stack = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    stack["stt"].text = "Hey Sabraah"
    response = test_client.post(
        "/api/v1/chat/voice",
        data={"session_id": session},
        files={"audio": ("speech.webm", b"fake-audio-bytes", "audio/webm")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data.get("ignored") is not True
    assert "sabrah" in data["assistant_text"].lower()
    assert "flight" in data["assistant_text"].lower()


def test_guided_flight_search_not_trains(client) -> None:
    test_client, stack = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    greet = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Hi Sabrah"},
    )
    assert "flight" in greet.json()["assistant_text"].lower()
    where = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "I want to book a flight."},
    )
    assert "where" in where.json()["assistant_text"].lower()
    when = test_client.post(
        "/api/v1/chat/text",
        json={
            "session_id": session,
            "message": "I want to go from Indore to Delhi via flight.",
        },
    )
    assert "Indore" in when.json()["assistant_text"]
    assert "Delhi" in when.json()["assistant_text"]
    assert "Via Flight" not in when.json()["assistant_text"]
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Tomorrow."},
    )
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "One passenger."},
    )
    found = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Other."},
    )
    data = found.json()
    assert found.status_code == 200
    assert "flight" in data["assistant_text"].lower()
    assert "train" not in data["assistant_text"].lower()
    assert any(path == "/api/v1/flights/search" for _, path, _ in stack["travel"].calls)
    assert not any(
        path in {"/api/v1/trains/search", "/api/v1/trains/train-list/"}
        for _, path, _ in stack["travel"].calls
    )
    assert data["offerings"].get("flights")


def test_book_a_train_uses_guided_flow(client) -> None:
    test_client, _ = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Hi Sabrah"},
    )
    reply = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Book a train"},
    )
    data = reply.json()
    text = data["assistant_text"].lower()
    assert "train" in text
    assert "pune to delhi" in text
    assert data["memory"]["user_goal"] == "book_train"
    assert "date" not in text


def test_stt_book_a_drain_becomes_train(client) -> None:
    test_client, _ = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    reply = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Book a drain."},
    )
    data = reply.json()
    assert "train" in data["user_text"].lower()
    assert data["memory"]["user_goal"] == "book_train"
    assert "train" in data["assistant_text"].lower()


def test_stt_peace_becomes_thank_you(client) -> None:
    test_client, _ = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    reply = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Peace."},
    )
    data = reply.json()
    assert "thank you" in data["user_text"].lower()


def test_guided_flight_asks_fare_and_extras(client) -> None:
    test_client, stack = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Hi Sabrah"},
    )
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "I want to book a flight."},
    )
    test_client.post(
        "/api/v1/chat/text",
        json={
            "session_id": session,
            "message": "I want to go from Indore to Delhi via flight.",
        },
    )
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Tomorrow."},
    )
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "One passenger."},
    )
    found = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Other."},
    )
    assert found.json()["offerings"].get("flights")

    pick = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Option 1"},
    )
    pick_data = pick.json()
    pick_text = pick_data["assistant_text"].lower()
    assert "fare" in pick_text
    assert "opening" not in pick_text
    assert pick_data.get("open_booking") is not True
    assert pick_data["offerings"].get("fares")
    assert any(path == "/api/v1/flights/fare-group-details" for _, path, _ in stack["travel"].calls)

    fare = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Saver"},
    )
    fare_text = fare.json()["assistant_text"].lower()
    assert "meal" in fare_text
    assert fare.json()["offerings"].get("meals")
    assert fare.json().get("open_booking") is not True

    meal = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "no meal"},
    )
    meal_text = meal.json()["assistant_text"].lower()
    assert "baggage" in meal_text
    assert meal.json()["offerings"].get("baggage")

    bag = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "5 kg"},
    )
    bag_text = bag.json()["assistant_text"].lower()
    assert "check" in bag_text
    assert bag.json()["offerings"].get("addons")

    done = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "skip"},
    )
    done_data = done.json()
    done_text = done_data["assistant_text"].lower()
    assert "opening" in done_text or "checkout" in done_text
    assert done_data.get("open_booking") is True
    extras = (done_data.get("booking_details") or {}).get("flight_extras") or {}
    assert extras.get("fare") == "SAVER"
    assert extras.get("meal") == "skip"
    assert "5" in str(extras.get("baggage") or "")
    assert extras.get("priority_checkin") == "skip"


def test_frontend_loads(client) -> None:
    test_client, _ = client
    response = test_client.get("/")
    assert response.status_code == 200
    assert "Sabrah" in response.text


def test_session_store_unit() -> None:
    import asyncio

    store = InMemorySessionStore(ttl_minutes=60)

    async def _run() -> None:
        session = await store.create()
        session.memory.source = "Delhi"
        session.memory.destination = "Mumbai"
        await store.save(session)
        loaded = await store.get(session.session_id)
        assert loaded is not None
        assert loaded.memory.source == "Delhi"
        reset = await store.reset(session.session_id)
        assert reset is not None
        assert reset.memory.source is None

    asyncio.run(_run())


def _book_flight_until_options(test_client, session: str):
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Hi Sabrah"},
    )
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "I want to book a flight."},
    )
    test_client.post(
        "/api/v1/chat/text",
        json={
            "session_id": session,
            "message": "I want to go from Indore to Delhi via flight.",
        },
    )
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Tomorrow."},
    )
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "One passenger."},
    )
    return test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Other."},
    )


def test_options_ask_read_or_screen(client) -> None:
    test_client, _ = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    found = _book_flight_until_options(test_client, session)
    text = found.json()["assistant_text"].lower()
    assert "recommend" in text
    assert "read them out" in text
    assert "pick from the screen" in text


def test_read_options_speaks_list_and_recommends(client) -> None:
    test_client, _ = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    _book_flight_until_options(test_client, session)
    reply = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Read them"},
    )
    data = reply.json()
    text = data["assistant_text"].lower()
    assert "here they are" in text
    assert "option 1" in text
    assert "6:10 AM" in data["assistant_text"] or "6:10 am" in text
    assert "recommend" in text
    assert "can't read" not in text


def test_screen_pick_keeps_recommendation(client) -> None:
    test_client, _ = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    _book_flight_until_options(test_client, session)
    reply = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "I'll pick from the screen"},
    )
    text = reply.json()["assistant_text"].lower()
    assert "pick from the screen" in text
    assert "recommend" in text
    assert "here they are" not in text


def test_gopal_station_asks_clarification(client) -> None:
    test_client, stack = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Hi Sabrah"},
    )
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Book a train"},
    )
    reply = test_client.post(
        "/api/v1/chat/text",
        json={
            "session_id": session,
            "message": "Okay. So, can you book a train for me from Khandwa to Gopal?",
        },
    )
    data = reply.json()
    text = data["assistant_text"].lower()
    assert reply.status_code == 200
    assert "unexpected server" not in text
    assert "gopal" in text
    assert "bhopal" in text
    assert "gopalganj" in text
    assert data["memory"]["source"] == "Khandwa"
    assert data["memory"]["destination"] is None
    assert not any(
        path == "/api/v1/trains/train-list/" for _, path, _ in stack["travel"].calls
    )


def test_train_search_hides_generic_server_error(client) -> None:
    test_client, stack = client
    stack["travel"].generic_error = True
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Hi Sabrah"},
    )
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Book a train"},
    )
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Khandwa to Bhopal"},
    )
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Tomorrow."},
    )
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "One passenger."},
    )
    found = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Other."},
    )
    text = found.json()["assistant_text"].lower()
    assert found.status_code == 200
    assert "unexpected server" not in text
    assert "khandwa" in text
    assert "bhopal" in text


def test_read_trains_speaks_number_and_time(client) -> None:
    test_client, _ = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    _book_train_until_search(test_client, session, "Khandwa to Bhopal")
    reply = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Read them"},
    )
    text = reply.json()["assistant_text"]
    lowered = text.lower()
    assert "here they are" in lowered
    assert "51683" in text.replace(" ", "") or "5 1 6 8 3" in text
    assert "8:10 AM" in text
    assert "9:10 AM" in text
    assert "knw-bir passenger" in lowered
    assert "recommend" in lowered


def test_train_details_phrase_reads_options(client) -> None:
    test_client, _ = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    _book_train_until_search(test_client, session, "Khandwa to Bhopal")
    reply = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Train number and time"},
    )
    text = reply.json()["assistant_text"]
    assert "8:10 AM" in text
    assert "5 1 6 8 3" in text or "51683" in text.replace(" ", "")


def test_mumbai_after_train_options_changes_destination(client) -> None:
    test_client, stack = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    _book_train_until_search(test_client, session, "Khandwa to Bhopal")
    reply = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Mumbai."},
    )
    data = reply.json()
    text = data["assistant_text"].lower()
    assert "confirm" not in text
    assert data["memory"]["destination"] == "Mumbai"
    assert data["memory"]["source"] == "Khandwa"
    assert "mumbai" in text
    assert any(path == "/api/v1/trains/train-list/" for _, path, _ in stack["travel"].calls)


def test_oneshot_train_uses_stated_name_and_for_me(client) -> None:
    test_client, stack = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Hi Sabrah"},
    )
    reply = test_client.post(
        "/api/v1/chat/text",
        json={
            "session_id": session,
            "message": (
                "I am Chetan. I want to go from Khandwa to Bhopal on 26th September. "
                "Can you book a train for me?"
            ),
        },
    )
    data = reply.json()
    text = data["assistant_text"].lower()
    assert reply.status_code == 200
    assert "who is travelling" not in text
    assert "just me" not in text
    assert data["memory"]["passenger_name"] == "Chetan"
    assert data["memory"]["passenger_count"] == 1
    assert data["memory"]["source"] == "Khandwa"
    assert data["memory"]["destination"] == "Bhopal"
    assert "search_trains" in data["tools_used"]
    assert data["offerings"].get("trains")
    assert any(path == "/api/v1/trains/train-list/" for _, path, _ in stack["travel"].calls)


def test_i_am_going_is_not_a_passenger_name() -> None:
    from app.agents import ConversationAgent

    assert ConversationAgent._extract_stated_passenger_names(
        "I am going from Khandwa to Bhopal."
    ) == []
    assert ConversationAgent._extract_stated_passenger_names(
        "I am Chetan. I want to go from Khandwa to Bhopal."
    ) == ["Chetan"]
