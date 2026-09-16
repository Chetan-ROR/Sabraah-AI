"""Session memory abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

from app.models import ConversationMessage, SessionState, TravelSlotMemory


class SessionStore(ABC):
    @abstractmethod
    async def create(self, **kwargs) -> SessionState:
        raise NotImplementedError

    @abstractmethod
    async def get(self, session_id: str) -> Optional[SessionState]:
        raise NotImplementedError

    @abstractmethod
    async def list(self) -> list[SessionState]:
        raise NotImplementedError

    @abstractmethod
    async def save(self, session: SessionState) -> SessionState:
        raise NotImplementedError

    @abstractmethod
    async def reset(self, session_id: str) -> Optional[SessionState]:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, session_id: str) -> None:
        raise NotImplementedError


class InMemorySessionStore(SessionStore):
    """MVP in-memory store. Replace with RedisSessionStore later."""

    def __init__(self, ttl_minutes: int = 60) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._ttl = timedelta(minutes=ttl_minutes)

    def _purge_expired(self) -> None:
        now = datetime.utcnow()
        expired = [
            sid
            for sid, session in self._sessions.items()
            if now - session.updated_at > self._ttl
        ]
        for sid in expired:
            self._sessions.pop(sid, None)

    async def create(self, **kwargs) -> SessionState:
        self._purge_expired()
        session = SessionState(session_id=str(uuid4()), **kwargs)
        self._sessions[session.session_id] = session
        return session

    async def get(self, session_id: str) -> Optional[SessionState]:
        self._purge_expired()
        return self._sessions.get(session_id)

    async def list(self) -> list[SessionState]:
        self._purge_expired()
        return sorted(
            self._sessions.values(),
            key=lambda s: s.updated_at,
            reverse=True,
        )

    async def save(self, session: SessionState) -> SessionState:
        session.updated_at = datetime.utcnow()
        self._sessions[session.session_id] = session
        return session

    async def reset(self, session_id: str) -> Optional[SessionState]:
        existing = await self.get(session_id)
        if existing is None:
            return None
        reset = SessionState(
            session_id=session_id,
            agent_id=existing.agent_id,
            customer_name=existing.customer_name,
            customer_phone=existing.customer_phone,
            customer_email=existing.customer_email,
            source=existing.source,
        )
        return await self.save(reset)

    async def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


def update_memory_from_slots(
    memory: TravelSlotMemory, slots: dict
) -> TravelSlotMemory:
    """Merge known slots into memory without wiping unspecified fields."""
    data = memory.model_dump()
    for key, value in slots.items():
        if key in data and value is not None:
            data[key] = value
    return TravelSlotMemory(**data)


def append_message(
    session: SessionState,
    role: str,
    content: str,
    *,
    tool_call_id: Optional[str] = None,
    name: Optional[str] = None,
) -> None:
    session.conversation_history.append(
        ConversationMessage(
            role=role,  # type: ignore[arg-type]
            content=content,
            tool_call_id=tool_call_id,
            name=name,
        )
    )
