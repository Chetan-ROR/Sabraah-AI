"""Prompt helpers."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.models import SessionState

_PROMPT_PATH = Path(__file__).with_name("system_prompt.txt")
_agent_store = None


def set_agent_store(store) -> None:
    """Inject agent store so prompts can resolve per-agent system text."""
    global _agent_store
    _agent_store = store


def load_system_prompt(agent_id: Optional[str] = None) -> str:
    if _agent_store is not None:
        agent = None
        if agent_id:
            agent = _agent_store.get(agent_id)
        if agent is None:
            agent = _agent_store.get_default()
        if agent is not None and agent.system_prompt.strip():
            return agent.system_prompt.strip()
    return _PROMPT_PATH.read_text(encoding="utf-8").strip()


def load_first_message(agent_id: Optional[str] = None) -> str:
    if _agent_store is not None:
        agent = _agent_store.get(agent_id) if agent_id else None
        if agent is None:
            agent = _agent_store.get_default()
        if agent is not None and agent.first_message.strip():
            return agent.first_message.strip()
    return (
        "What would you like to do — book a flight, book a train, book an event, cancel a ticket, or request a refund?"
    )


def _summarize_search_results(results: list) -> list[dict]:
    """Compact last search rows so the model can refer to option ids."""
    summary: list[dict] = []
    for item in results[:5]:
        if not isinstance(item, dict):
            continue
        summary.append(
            {
                "id": item.get("id"),
                "name": item.get("name") or item.get("airline"),
                "source": item.get("source"),
                "destination": item.get("destination") or item.get("city"),
                "departure_time": item.get("departure_time"),
                "arrival_time": item.get("arrival_time"),
                "duration": item.get("duration"),
                "price": item.get("price")
                or item.get("total_price")
                or item.get("estimated_total"),
                "currency": item.get("currency", "INR"),
                "class": item.get("class") or item.get("travel_class"),
                "available_seats": item.get("available_seats"),
                "nights": item.get("nights"),
                "includes": item.get("includes"),
                "summary": item.get("summary"),
            }
        )
    return summary


def build_system_message(session: SessionState) -> str:
    now = datetime.now(timezone.utc).astimezone()
    memory = session.memory.model_dump(exclude_none=True)
    memory["language"] = "en"
    parts = [
        load_system_prompt(session.agent_id),
        "",
        "CRITICAL REMINDER: Reply in English only on this turn, even if the user spoke Hindi.",
        "CRITICAL REMINDER: After search results, do not read lists aloud — point the user to the screen.",
        f"Current local datetime: {now.isoformat()}",
        f"Known trip slots (do not re-ask if already present): {memory}",
    ]
    if session.memory.itinerary_step:
        parts.append(f"Current itinerary step: {session.memory.itinerary_step}")
    if session.itinerary_selections:
        parts.append(
            "Itinerary selections so far: "
            f"{ {k: (v.get('id'), v.get('name')) for k, v in session.itinerary_selections.items() if isinstance(v, dict)} }"
        )
    if session.travelers:
        parts.append(
            "Uploaded/collected travelers: "
            f"{[{'name': t.get('name')} for t in session.travelers[:20]]} "
            f"({len(session.travelers)} of {session.memory.passenger_count or len(session.travelers)})"
        )
        if session.memory.passenger_count and len(session.travelers) < session.memory.passenger_count:
            parts.append(
                "CRITICAL: Still missing passenger names. Ask for the next passenger name now."
            )
    if session.wishlist:
        parts.append(
            "Wishlisted trains: "
            f"{[{'id': w.get('id'), 'name': w.get('name')} for w in session.wishlist[:8]]}"
        )
    if session.memory.train_preference:
        parts.append(f"User train preference: {session.memory.train_preference}")
    if session.memory.meal_preference or session.memory.allergies:
        parts.append(
            "Meal/diet: "
            f"meal={session.memory.meal_preference}, "
            f"allergies={session.memory.allergies}, "
            f"dietary={session.memory.dietary_restrictions}, "
            f"catering_full_train={session.memory.catering_full_train}"
        )
    if session.memory.trip_purpose:
        parts.append(f"Trip purpose / event: {session.memory.trip_purpose}")
    if session.last_offerings:
        parts.append(f"Last offerings on screen (choose by id/option): {session.last_offerings}")
    elif session.last_search_results:
        parts.append(
            "Last search results (use these ids/names when the user picks an option): "
            f"{_summarize_search_results(session.last_search_results)}"
        )
    if session.pending_confirmation:
        parts.append(f"Pending confirmation: {session.pending_confirmation}")
    return "\n".join(parts)
