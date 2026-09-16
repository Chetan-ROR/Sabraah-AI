"""Persistent store for configurable AI agents and prompts."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from app.models import AgentCreate, AgentRecord, AgentUpdate

_DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "system_prompt.txt"
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_AGENTS_FILE = _DATA_DIR / "agents.json"


def _default_system_prompt() -> str:
    if _DEFAULT_PROMPT_PATH.exists():
        return _DEFAULT_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return "You are Sabrah, a friendly AI travel assistant. Reply in English only."


def _seed_agents() -> list[AgentRecord]:
    now = datetime.utcnow()
    sabrah = AgentRecord(
        id="agent-sabrah",
        name="Sabrah Travel Agent",
        description="Voice travel assistant for train booking, cancel, and refund.",
        first_message="What would you like to do — book a train, cancel a ticket, or request a refund?",
        system_prompt=_default_system_prompt(),
        enabled=True,
        created_at=now,
        updated_at=now,
    )
    inbound = AgentRecord(
        id="agent-inbound",
        name="Inbound Receptionist Agent",
        description="Handles inbound travel inquiries and routes to booking flow.",
        first_message="Thank you for calling Sabrah. How can I help with your travel plans today?",
        system_prompt=(
            "Persona: You are Sabrah's inbound receptionist for travel bookings.\n"
            "Tone: professional, warm, concise (1–2 short sentences for voice).\n\n"
            "GOALS:\n"
            "1. Identify caller intent: book, cancel, or refund.\n"
            "2. Gather route, date, and passenger count when booking.\n"
            "3. Never invent prices or PNRs — use tools only.\n\n"
            "CONSTRAINTS:\n"
            "- English only.\n"
            "- Do not read long train lists aloud; point users to the screen."
        ),
        enabled=True,
        created_at=now,
        updated_at=now,
    )
    sales = AgentRecord(
        id="agent-sales",
        name="Sales Agent",
        description="Upsells packages and group / charter bookings.",
        first_message="Looking for a group trip or full-coach charter? I can help.",
        system_prompt=(
            "Persona: You are Sabrah Sales, focused on group travel and packages.\n"
            "GOALS: Qualify group size, trip purpose, and dates; offer charter when >9 passengers.\n"
            "CONSTRAINTS: English only; never invent fares; use tools for search/booking."
        ),
        enabled=True,
        created_at=now,
        updated_at=now,
    )
    return [sabrah, inbound, sales]


class AgentStore:
    """Thread-safe JSON-backed agent registry."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _AGENTS_FILE
        self._lock = threading.Lock()
        self._agents: dict[str, AgentRecord] = {}
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        with self._lock:
            if self._path.exists():
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._agents = {
                    item["id"]: AgentRecord.model_validate(item) for item in raw
                }
            else:
                seeded = _seed_agents()
                self._agents = {a.id: a for a in seeded}
                self._persist_unlocked()

    def _persist_unlocked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [a.model_dump(mode="json") for a in self._agents.values()]
        self._path.write_text(
            json.dumps(payload, indent=2, default=str),
            encoding="utf-8",
        )

    def list(self) -> list[AgentRecord]:
        with self._lock:
            return sorted(self._agents.values(), key=lambda a: a.created_at)

    def get(self, agent_id: str) -> Optional[AgentRecord]:
        with self._lock:
            return self._agents.get(agent_id)

    def get_default(self) -> AgentRecord:
        agents = self.list()
        for agent in agents:
            if agent.enabled:
                return agent
        if agents:
            return agents[0]
        seeded = _seed_agents()[0]
        with self._lock:
            self._agents[seeded.id] = seeded
            self._persist_unlocked()
        return seeded

    def create(self, body: AgentCreate) -> AgentRecord:
        now = datetime.utcnow()
        agent = AgentRecord(
            id=f"agent-{uuid4().hex[:10]}",
            name=body.name.strip(),
            description=(body.description or "").strip(),
            first_message=(body.first_message or "").strip(),
            system_prompt=(body.system_prompt or _default_system_prompt()).strip(),
            enabled=body.enabled,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._agents[agent.id] = agent
            self._persist_unlocked()
        return agent

    def update(self, agent_id: str, body: AgentUpdate) -> Optional[AgentRecord]:
        with self._lock:
            existing = self._agents.get(agent_id)
            if existing is None:
                return None
            data = existing.model_dump()
            patch = body.model_dump(exclude_unset=True)
            for key, value in patch.items():
                if isinstance(value, str):
                    data[key] = value.strip()
                else:
                    data[key] = value
            data["updated_at"] = datetime.utcnow()
            updated = AgentRecord.model_validate(data)
            self._agents[agent_id] = updated
            self._persist_unlocked()
            return updated

    def delete(self, agent_id: str) -> bool:
        with self._lock:
            if agent_id not in self._agents:
                return False
            if len(self._agents) <= 1:
                return False
            del self._agents[agent_id]
            self._persist_unlocked()
            return True
