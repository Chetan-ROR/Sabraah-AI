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
        super().__init__(base_url="http://travel.test", api_key="test-api-key")
        self.calls: List[Tuple[str, str, Optional[dict]]] = []
        self.available = True
        self.raise_unavailable = False

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        self.calls.append((method, path, json))
        if self.raise_unavailable:
            raise TravelBackendError("down", code="travel_unavailable")
        if path == "/api/v1/trains/search":
            return {
                "results": [
                    {
                        "id": "TRAIN-001",
                        "name": "Rajdhani Express",
                        "source": json["source"],
                        "destination": json["destination"],
                        "departure_time": "16:55",
                        "price": 1850,
                        "currency": "INR",
                    }
                ],
                "provider": "MOCK",
                "count": 1,
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
    stack["llm"].enqueue(
        SimpleNamespace(content="Hi! How can I help you today?", tool_calls=None)
    )
    response = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Hi Sabrah"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["assistant_text"].startswith("Hi!")
    assert data["audio_base64"] == base64.b64encode(b"FAKEMP3").decode("ascii")
    assert stack["tts"].texts


def test_openai_provider_mocked_via_agent(client) -> None:
    test_client, stack = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    stack["llm"].enqueue(SimpleNamespace(content="Understood.", tool_calls=None))
    response = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Hello"},
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
        json={"session_id": session, "message": "Hi"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["assistant_text"] == "Text only reply"
    assert data["audio_base64"] is None
    assert data["tts_error"]


def test_tool_calling_search_trains(client) -> None:
    test_client, stack = client
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    stack["llm"].enqueue(
        SimpleNamespace(
            content=None,
            tool_calls=[
                _tool_call(
                    "search_trains",
                    {
                        "source": "Delhi",
                        "destination": "Mumbai",
                        "departure_date": "2026-08-21",
                    },
                )
            ],
        )
    )
    stack["llm"].enqueue(
        SimpleNamespace(
            content="I found a Rajdhani Express leaving at 4:55 PM.",
            tool_calls=None,
        )
    )
    response = test_client.post(
        "/api/v1/chat/text",
        json={
            "session_id": session,
            "message": "Delhi se Mumbai kal ki train dikhao.",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "search_trains" in data["tools_used"]
    assert stack["travel"].calls
    assert stack["travel"].calls[0][1] == "/api/v1/trains/search"
    assert data["memory"]["source"] == "Delhi"
    assert data["memory"]["destination"] == "Mumbai"


def test_backend_unavailable(client) -> None:
    test_client, stack = client
    stack["travel"].raise_unavailable = True
    session = test_client.post("/api/v1/sessions").json()["session_id"]
    stack["llm"].enqueue(
        SimpleNamespace(
            content=None,
            tool_calls=[
                _tool_call(
                    "search_trains",
                    {
                        "source": "Delhi",
                        "destination": "Mumbai",
                        "departure_date": "2026-08-21",
                    },
                )
            ],
        )
    )
    stack["llm"].enqueue(
        SimpleNamespace(
            content="I cannot reach the travel service right now.",
            tool_calls=None,
        )
    )
    response = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Show trains"},
    )
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
                        "item_type": "train",
                        "item_id": "TRAIN-001",
                        "passenger_name": "Asha",
                        "confirmed": True,
                    },
                )
            ],
        )
    )
    stack["llm"].enqueue(
        SimpleNamespace(
            content="Your booking BK-TEST123 is confirmed.",
            tool_calls=None,
        )
    )
    response = test_client.post(
        "/api/v1/chat/text",
        json={"session_id": session, "message": "Yes, please book it"},
    )
    assert response.status_code == 200
    assert any(path == "/api/v1/bookings" for _, path, _ in stack["travel"].calls)
    assert "BK-TEST123" in response.json()["assistant_text"]


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
    stack["stt"].text = "Hi Sabrah"
    stack["llm"].enqueue(
        SimpleNamespace(content="Hi! How can I help you today?", tool_calls=None)
    )
    response = test_client.post(
        "/api/v1/chat/voice",
        data={"session_id": session},
        files={"audio": ("speech.webm", b"fake-audio-bytes", "audio/webm")},
    )
    assert response.status_code == 200
    assert response.json()["user_text"] == "Hi Sabrah"


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
