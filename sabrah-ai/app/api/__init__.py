"""API routes for Sabrah AI."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile, status

from app.agents import ConversationAgent
from app.models import (
    AgentCreate,
    AgentRecord,
    AgentUpdate,
    ChatResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    TextChatRequest,
)
from app.prompts import load_first_message
from app.providers import ProviderError
from app.services.agent_store import AgentStore
from app.services.session_store import SessionStore, append_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


def get_agent(request: Request) -> ConversationAgent:
    return request.app.state.agent


def get_sessions(request: Request) -> SessionStore:
    return request.app.state.session_store


def get_agents(request: Request) -> AgentStore:
    return request.app.state.agent_store


def _session_summary(session) -> dict:
    visible = [
        m for m in session.conversation_history if m.role in {"user", "assistant"}
    ]
    last = visible[-1] if visible else None
    name = (
        session.customer_name
        or session.memory.passenger_name
        or (f"Guest {session.session_id[:6]}" if session.session_id else "Guest")
    )
    phone = session.customer_phone or session.memory.contact_phone
    email = session.customer_email or session.memory.contact_email
    booked = bool(session.memory.last_booking_id or session.booking_details)
    return {
        "session_id": session.session_id,
        "agent_id": session.agent_id,
        "customer_name": name,
        "customer_phone": phone,
        "customer_email": email,
        "source": session.source or "Admin Console",
        "unread": session.unread,
        "booked": booked,
        "updated_at": session.updated_at.isoformat() + "Z",
        "created_at": session.created_at.isoformat() + "Z",
        "snippet": (last.content[:120] if last else "No messages yet"),
        "message_count": len(visible),
        "memory": session.memory.model_dump(),
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "timestamp": m.timestamp.isoformat() + "Z",
            }
            for m in visible
        ],
    }


@router.get("/agents", response_model=list[AgentRecord])
async def list_agents(agents: AgentStore = Depends(get_agents)) -> list[AgentRecord]:
    return agents.list()


@router.get("/agents/{agent_id}", response_model=AgentRecord)
async def get_agent_record(
    agent_id: str,
    agents: AgentStore = Depends(get_agents),
) -> AgentRecord:
    agent = agents.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.post("/agents", response_model=AgentRecord, status_code=201)
async def create_agent(
    body: AgentCreate,
    agents: AgentStore = Depends(get_agents),
) -> AgentRecord:
    return agents.create(body)


@router.patch("/agents/{agent_id}", response_model=AgentRecord)
async def update_agent(
    agent_id: str,
    body: AgentUpdate,
    agents: AgentStore = Depends(get_agents),
) -> AgentRecord:
    updated = agents.update(agent_id, body)
    if updated is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return updated


@router.delete("/agents/{agent_id}")
async def delete_agent(
    agent_id: str,
    agents: AgentStore = Depends(get_agents),
) -> dict:
    ok = agents.delete(agent_id)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete agent (not found or last remaining agent).",
        )
    return {"ok": True, "id": agent_id}


@router.get("/conversations")
async def list_conversations(
    sessions: SessionStore = Depends(get_sessions),
) -> dict:
    items = [_session_summary(s) for s in await sessions.list()]
    return {"count": len(items), "conversations": items}


@router.post("/sessions", response_model=SessionCreateResponse)
async def create_session(
    body: Optional[SessionCreateRequest] = Body(default=None),
    sessions: SessionStore = Depends(get_sessions),
    agents: AgentStore = Depends(get_agents),
) -> SessionCreateResponse:
    payload = body or SessionCreateRequest()
    agent_id = payload.agent_id
    if agent_id:
        if agents.get(agent_id) is None:
            raise HTTPException(status_code=404, detail="Agent not found")
    else:
        agent_id = agents.get_default().id
    source = payload.source or "Voice UI"
    session = await sessions.create(
        agent_id=agent_id,
        customer_name=payload.customer_name,
        customer_phone=payload.customer_phone,
        customer_email=payload.customer_email,
        source=source,
    )
    # Admin testing: seed opening gambit into the transcript.
    if source == "Admin Console":
        first = load_first_message(agent_id)
        if first:
            append_message(session, "assistant", first)
            await sessions.save(session)
    logger.info(
        "session.created session_id=%s agent_id=%s", session.session_id, agent_id
    )
    return SessionCreateResponse(session_id=session.session_id, agent_id=agent_id)


@router.post("/sessions/{session_id}/reset", response_model=SessionCreateResponse)
async def reset_session(
    session_id: str,
    sessions: SessionStore = Depends(get_sessions),
    agents: AgentStore = Depends(get_agents),
) -> SessionCreateResponse:
    session = await sessions.reset(session_id)
    if session is None:
        default_id = agents.get_default().id
        session = await sessions.create(agent_id=default_id, source="Voice UI")
    if session.source == "Admin Console":
        first = load_first_message(session.agent_id)
        if first:
            append_message(session, "assistant", first)
            await sessions.save(session)
    logger.info("session.reset session_id=%s", session.session_id)
    return SessionCreateResponse(
        session_id=session.session_id, agent_id=session.agent_id
    )


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    sessions: SessionStore = Depends(get_sessions),
) -> dict:
    session = await sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_summary(session)


@router.post("/sessions/{session_id}/travelers/upload")
async def upload_travelers(
    session_id: str,
    file: UploadFile = File(...),
    sessions: SessionStore = Depends(get_sessions),
) -> dict:
    from app.services.traveler_upload import parse_traveler_file

    session = await sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file uploaded.")
    try:
        travelers = parse_traveler_file(content, file.filename or "travelers.xlsx")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not travelers:
        raise HTTPException(
            status_code=400,
            detail="No traveler names found. Use columns: name, phone, meal, allergies.",
        )
    session.travelers = travelers
    session.memory.passenger_count = len(travelers)
    if len(travelers) > 9:
        session.memory.booking_mode = "charter"
        session.memory.intent = "charter"
        session.memory.charter_type = session.memory.charter_type or "full_coach"
    if travelers[0].get("name") and not session.memory.passenger_name:
        session.memory.passenger_name = travelers[0]["name"]
    if travelers[0].get("phone") and not session.memory.contact_phone:
        session.memory.contact_phone = travelers[0]["phone"]
    if travelers[0].get("meal") and not session.memory.meal_preference:
        session.memory.meal_preference = travelers[0]["meal"]
    await sessions.save(session)
    session.last_offerings["travelers"] = travelers
    return {
        "ok": True,
        "count": len(travelers),
        "travelers": travelers,
        "message": f"Uploaded {len(travelers)} travelers. You can continue booking.",
    }


@router.post("/chat/text", response_model=ChatResponse)
async def chat_text(
    body: TextChatRequest,
    agent: ConversationAgent = Depends(get_agent),
    sessions: SessionStore = Depends(get_sessions),
) -> ChatResponse:
    if body.agent_id:
        session = await sessions.get(body.session_id)
        if session is not None and session.agent_id != body.agent_id:
            session.agent_id = body.agent_id
            await sessions.save(session)
    try:
        return await agent.handle_text(body.session_id, body.message)
    except ProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": exc.user_message, "code": exc.code},
        ) from exc


@router.post("/chat/voice", response_model=ChatResponse)
async def chat_voice(
    session_id: str = Form(...),
    audio: UploadFile = File(...),
    agent: ConversationAgent = Depends(get_agent),
) -> ChatResponse:
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Empty audio received. Please try speaking again.",
                "code": "empty_audio",
            },
        )
    filename = audio.filename or "audio.webm"
    try:
        return await agent.handle_voice(session_id, audio_bytes, filename=filename)
    except ProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": exc.user_message, "code": exc.code},
        ) from exc
