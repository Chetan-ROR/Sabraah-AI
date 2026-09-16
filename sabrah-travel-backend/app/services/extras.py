"""Mock extras: stations, local transport, weather, wishlist, charter, support."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any, Optional

# In-memory stores for MVP demos.
_WISHLIST: dict[str, list[dict[str, Any]]] = {}
_CHARTER: list[dict[str, Any]] = []
_ESCALATIONS: list[dict[str, Any]] = []
_REFUNDS: list[dict[str, Any]] = []
_FEEDBACK: list[dict[str, Any]] = []


def enrich_train_row(row: dict[str, Any]) -> dict[str, Any]:
    """Add station / platform / stops metadata for AI explanations."""
    source = str(row.get("source") or "Origin")
    dest = str(row.get("destination") or "Destination")
    seed = hashlib.md5(str(row.get("id") or row.get("name") or "").encode()).hexdigest()
    platform = str((int(seed[:2], 16) % 6) + 1)
    mid = f"{source[:3].upper()}-HALT"
    stops = [
        {
            "station": f"{source} Junction",
            "code": source[:3].upper(),
            "arrival": None,
            "departure": row.get("departure_time"),
            "platform": platform,
        },
        {
            "station": f"{mid} Station",
            "code": mid[:3],
            "arrival": "—",
            "departure": "—",
            "platform": str((int(seed[2:4], 16) % 4) + 1),
        },
        {
            "station": f"{dest} Central",
            "code": dest[:3].upper(),
            "arrival": row.get("arrival_time"),
            "departure": None,
            "platform": str((int(seed[4:6], 16) % 5) + 1),
        },
    ]
    return {
        **row,
        "source_station": f"{source} Junction",
        "destination_station": f"{dest} Central",
        "platform": platform,
        "stops": stops,
        "connections_note": (
            f"Arrive {dest} Central platform {stops[-1]['platform']}. "
            "Local cabs and metro connections are available outside the station."
        ),
    }


def sort_trains_by_preference(
    rows: list[dict[str, Any]], preference: Optional[str]
) -> list[dict[str, Any]]:
    pref = (preference or "balanced").lower().strip()

    def duration_minutes(value: str) -> int:
        text = (value or "").lower().replace(" ", "")
        hours = 0
        mins = 0
        if "h" in text:
            try:
                hours = int(text.split("h")[0] or 0)
            except ValueError:
                hours = 0
            rest = text.split("h")[-1]
            if "m" in rest:
                try:
                    mins = int(rest.replace("m", "") or 0)
                except ValueError:
                    mins = 0
        elif "m" in text:
            try:
                mins = int(text.replace("m", "") or 0)
            except ValueError:
                mins = 0
        return hours * 60 + mins

    scored: list[tuple[float, dict[str, Any], str]] = []
    for row in rows:
        price = float(row.get("price") or 0)
        dur = duration_minutes(str(row.get("duration") or ""))
        seats = int(row.get("available_seats") or 0)
        cls = str(row.get("class") or row.get("travel_class") or "").upper()
        ac_bonus = 1.0 if cls in {"1A", "2A", "3A", "CC", "EC"} else 0.0
        if pref in {"cheapest", "budget", "low price"}:
            score = price
            reason = "Best match for lowest price"
        elif pref in {"fastest", "quick", "shortest"}:
            score = float(dur)
            reason = "Best match for shortest duration"
        elif pref in {"ac", "comfort", "comfortable"}:
            score = -ac_bonus * 1000 + price
            reason = "Best match for AC / comfort preference"
        elif pref in {"wishlist", "saved"}:
            score = 0 if row.get("wishlisted") else 1
            reason = "Prioritised from your wishlist" if row.get("wishlisted") else "Available option"
        else:
            # balanced: prefer AC + reasonable price + duration
            score = price * 0.4 + dur * 2 - ac_bonus * 200 - seats
            reason = "Balanced pick for price, duration, and comfort"
        scored.append((score, {**row, "recommendation_reason": reason}, reason))

    scored.sort(key=lambda x: x[0])
    out: list[dict[str, Any]] = []
    for idx, (_, row, reason) in enumerate(scored):
        item = dict(row)
        item["rank"] = idx + 1
        item["recommendation_reason"] = reason
        if idx == 0:
            item["recommended"] = True
        out.append(item)
    return out


def search_local_transport(
    *,
    city: str,
    pickup: Optional[str] = None,
    dropoff: Optional[str] = None,
    transport_type: Optional[str] = None,
) -> list[dict[str, Any]]:
    city_t = city.title()
    pickup_t = (pickup or f"{city_t} Station").title()
    drop_t = (dropoff or f"{city_t} Hotel area").title()
    options = [
        {
            "id": f"CAB-{city_t[:3].upper()}-001",
            "name": "Sabrah Cab — Sedan",
            "type": "cab",
            "city": city_t,
            "pickup": pickup_t,
            "dropoff": drop_t,
            "duration": "35–50 min",
            "price": 450,
            "currency": "INR",
            "provider": "MOCK",
        },
        {
            "id": f"CAB-{city_t[:3].upper()}-002",
            "name": "Sabrah Cab — SUV",
            "type": "cab",
            "city": city_t,
            "pickup": pickup_t,
            "dropoff": drop_t,
            "duration": "35–50 min",
            "price": 750,
            "currency": "INR",
            "provider": "MOCK",
        },
        {
            "id": f"CAR-{city_t[:3].upper()}-001",
            "name": "Self-drive rental — Hatchback",
            "type": "car_rental",
            "city": city_t,
            "pickup": pickup_t,
            "dropoff": drop_t,
            "duration": "1 day",
            "price": 1800,
            "currency": "INR",
            "provider": "MOCK",
        },
    ]
    if transport_type:
        t = transport_type.lower()
        filtered = [o for o in options if t in o["type"] or t in o["name"].lower()]
        if filtered:
            return filtered
    return options


def weather_for_city(city: str) -> dict[str, Any]:
    c = city.strip().title() or "Your city"
    seed = int(hashlib.md5(c.encode()).hexdigest()[:4], 16)
    conditions = [
        ("sunny", "Sunny and pleasant. Carry a hat and sunglasses if you go out."),
        ("cloudy", "Cloudy skies. A light jacket is enough."),
        ("rain", "Rain likely. Carry an umbrella and waterproof footwear."),
        ("flood_warning", "Flood warning in low-lying areas. Avoid unnecessary travel near rivers."),
        ("heat", "Hot weather. Stay hydrated and avoid peak afternoon sun."),
    ]
    key, tip = conditions[seed % len(conditions)]
    temp = 22 + (seed % 14)
    return {
        "city": c,
        "condition": key,
        "temperature_c": temp,
        "alert": key == "flood_warning",
        "summary": f"{c}: {temp}°C, {key.replace('_', ' ')}.",
        "tip": tip,
        "provider": "MOCK",
    }


def wishlist_add(session_id: str, train: dict[str, Any]) -> dict[str, Any]:
    rows = _WISHLIST.setdefault(session_id or "anon", [])
    tid = str(train.get("id") or "")
    if tid and any(str(r.get("id")) == tid for r in rows):
        return {"ok": True, "wishlist": rows, "message": "Already on wishlist."}
    item = {
        "id": tid or f"WISH-{len(rows)+1}",
        "name": train.get("name"),
        "source": train.get("source"),
        "destination": train.get("destination"),
        "added_at": datetime.utcnow().isoformat(),
        **{k: v for k, v in train.items() if k not in {"added_at"}},
    }
    rows.append(item)
    return {"ok": True, "wishlist": rows, "message": "Saved to wishlist."}


def wishlist_list(session_id: str) -> dict[str, Any]:
    rows = list(_WISHLIST.get(session_id or "anon", []))
    return {"wishlist": rows, "count": len(rows)}


FULL_COACH_SEATS = 72
ENTIRE_TRAIN_COACHES = 22


def assign_mock_seats(
    train_id: str, passenger_count: int, passenger_names: Optional[list[str]] = None
) -> list[dict[str, Any]]:
    """Mock IRCTC-style seat assignment after booking."""
    names = passenger_names or []
    seats: list[dict[str, Any]] = []
    coach = "S3"
    for i in range(max(1, passenger_count)):
        berth_num = (i % 6) + 1
        berth_type = ["LB", "MB", "UB", "SL", "SU", "LB"][i % 6]
        label = f"{coach} / {berth_num}{berth_type}"
        seats.append(
            {
                "passenger": names[i] if i < len(names) else f"Passenger {i + 1}",
                "coach": coach,
                "berth": f"{berth_num}{berth_type}",
                "seat_label": label,
            }
        )
    return seats


def create_charter_request(payload: dict[str, Any]) -> dict[str, Any]:
    charter_type = str(payload.get("charter_type") or "full_coach")
    seats_per_coach = FULL_COACH_SEATS
    total_coaches = ENTIRE_TRAIN_COACHES if charter_type == "entire_train" else 1
    passengers = int(payload.get("passengers") or 0)
    traveler_names = payload.get("traveler_names") or []
    item = {
        "request_id": f"CHARTER-{uuid.uuid4().hex[:8].upper()}",
        "status": "received",
        "created_at": datetime.utcnow().isoformat(),
        "seats_per_coach": seats_per_coach,
        "total_coaches": total_coaches,
        "total_seats_available": seats_per_coach * total_coaches,
        "traveler_names": traveler_names,
        "message": (
            f"Your {charter_type.replace('_', ' ')} request is logged "
            f"({seats_per_coach} seats per coach). "
            "A SabRaah sales executive will contact you to confirm availability."
        ),
        **payload,
    }
    _CHARTER.append(item)
    return item


def escalate_to_sales(payload: dict[str, Any]) -> dict[str, Any]:
    item = {
        "ticket_id": f"SALES-{uuid.uuid4().hex[:8].upper()}",
        "status": "queued",
        "created_at": datetime.utcnow().isoformat(),
        "message": (
            "Connecting you to a SabRaah sales executive. "
            "They will follow up on this chat shortly."
        ),
        **payload,
    }
    _ESCALATIONS.append(item)
    return item


def request_refund(payload: dict[str, Any]) -> dict[str, Any]:
    item = {
        "refund_id": f"REF-{uuid.uuid4().hex[:8].upper()}",
        "status": "under_review",
        "created_at": datetime.utcnow().isoformat(),
        "message": (
            "Refund request received. If AI cannot complete it automatically, "
            "a SabRaah executive will take over."
        ),
        **payload,
    }
    _REFUNDS.append(item)
    return item


def submit_feedback(payload: dict[str, Any]) -> dict[str, Any]:
    item = {
        "feedback_id": f"FB-{uuid.uuid4().hex[:8].upper()}",
        "status": "saved",
        "created_at": datetime.utcnow().isoformat(),
        "message": "Thank you for your feedback. Wishing you a wonderful trip!",
        **payload,
    }
    _FEEDBACK.append(item)
    return item
