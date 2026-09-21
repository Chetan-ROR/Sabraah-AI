"""HTTP client for Super Travel (api-repository) train APIs."""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, Optional

import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

# City / spoken name → primary IRCTC station code (used if station-codes search fails).
CITY_STATION_CODES: dict[str, str] = {
    "indore": "INDB",
    "mumbai": "BCT",
    "bombay": "BCT",
    "mumbai central": "BCT",
    "delhi": "NDLS",
    "new delhi": "NDLS",
    "pune": "PUNE",
    "poona": "PUNE",
    "khandwa": "KNW",
    "burhanpur": "BAU",
    "jalgaon": "JL",
    "nasik": "NK",
    "nashik": "NK",
    "noida": "NDLS",
    "bhopal": "BPL",
    "nagpur": "NGP",
    "ahmedabad": "ADI",
    "surat": "ST",
    "jaipur": "JP",
    "lucknow": "LKO",
    "kanpur": "CNB",
    "hyderabad": "HYB",
    "chennai": "MAS",
    "kolkata": "HWH",
    "bangalore": "SBC",
    "bengaluru": "SBC",
    "goa": "MAO",
    "vadodara": "BRC",
    "rajkot": "RJT",
    "lokmanya tilak": "LTT",
    "ltt": "LTT",
    "cst": "CSTM",
    "csmt": "CSTM",
}


def _base_url() -> str:
    settings = get_settings()
    return (settings.super_travel_api_base_url or "").rstrip("/")


def _timeout() -> float:
    return float(get_settings().super_travel_timeout_seconds)


def _hhmm(value: Optional[str]) -> str:
    if not value:
        return "00:00"
    text = str(value).strip()
    # "16:55:00" → "16:55"
    if re.match(r"^\d{1,2}:\d{2}", text):
        parts = text.split(":")
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    return text[:5]


def estimate_unit_price(distance_km: Optional[float], travel_class: str) -> float:
    """Rough fare when Super Travel list has no price field yet."""
    km = float(distance_km or 300)
    rates = {
        "SL": 0.55,
        "CC": 0.9,
        "3A": 1.15,
        "2A": 1.75,
        "1A": 2.8,
        "EC": 1.4,
        "EA": 2.0,
    }
    rate = rates.get((travel_class or "3A").upper(), 1.15)
    raw = max(250.0, km * rate)
    return float(round(raw / 10) * 10)


def train_number_from_id(train_id: str) -> str:
    text = str(train_id or "").strip()
    digits = re.findall(r"\d{3,5}", text)
    return digits[0] if digits else text


async def resolve_station_code(query: str) -> Optional[str]:
    """Resolve city / station name or code via GET /trains/station-codes/."""
    raw = (query or "").strip()
    if not raw:
        return None
    key = " ".join(raw.lower().split())
    cached = CITY_STATION_CODES.get(key)
    if cached:
        return cached

    upper = raw.upper()
    # IRCTC codes are typically 2–4 letters. 5-letter words like DELHI are cities.
    if re.fullmatch(r"[A-Z]{2,4}", upper):
        return upper

    base = _base_url()
    if not base:
        return cached

    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.get(
                f"{base}/api/v1/trains/station-codes/",
                params={"search": raw, "page": 1, "page_size": 10},
            )
            resp.raise_for_status()
            payload = resp.json()
            rows = payload.get("data") or []
            if not rows:
                return cached
            # Prefer exact name/code match, else first hit.
            for row in rows:
                code = str(row.get("code") or "").upper()
                name = str(row.get("name") or "").lower()
                if code == upper or key in name or name.startswith(key):
                    return code
            return str(rows[0].get("code") or "").upper() or cached
    except Exception as exc:  # noqa: BLE001 — network / API soft-fail
        logger.warning("station-codes lookup failed for %r: %s", raw, exc)
        return cached


async def resolve_station_code_fast(query: str) -> Optional[str]:
    """Prefer known city map; only hit station-codes API when unknown."""
    raw = (query or "").strip()
    if not raw:
        return None
    key = " ".join(raw.lower().split())
    if key in CITY_STATION_CODES:
        return CITY_STATION_CODES[key]
    upper = raw.upper()
    if re.fullmatch(r"[A-Z]{2,4}", upper):
        return upper
    return await resolve_station_code(query)

async def fetch_train_list(
    *,
    origin: str,
    destination: str,
    journey_date: date,
    page_size: int = 50,
) -> list[dict[str, Any]]:
    base = _base_url()
    if not base:
        raise RuntimeError("SUPER_TRAVEL_API_BASE_URL is not set")

    date_str = journey_date.strftime("%d-%m-%Y")
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        resp = await client.get(
            f"{base}/api/v1/trains/train-list/",
            params={
                "origin": origin,
                "destination": destination,
                "date": date_str,
                "page": 1,
                "page_size": page_size,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        return list(payload.get("data") or [])


def fetch_live_status_sync(train_number: str) -> Optional[dict[str, Any]]:
    """Sync live status for dashboard / booking enrichment."""
    base = _base_url()
    number = train_number_from_id(train_number)
    if not base or not number:
        return None
    try:
        with httpx.Client(timeout=_timeout()) as client:
            resp = client.get(
                f"{base}/api/v1/trains/live/",
                params={"train_number": number},
            )
            if resp.status_code >= 400:
                logger.warning(
                    "live status %s → HTTP %s: %s",
                    number,
                    resp.status_code,
                    resp.text[:200],
                )
                return None
            payload = resp.json()
            data = payload.get("data")
            return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("live status failed for %s: %s", number, exc)
        return None


def map_live_to_sabrah(
    data: dict[str, Any],
    *,
    train_id: Optional[str] = None,
) -> dict[str, Any]:
    """Normalize Super Travel live payload to Sabrah live_status shape."""
    status_raw = str(
        data.get("current_status") or data.get("status") or "UNKNOWN"
    ).upper()
    if "ARRIVED" in status_raw or status_raw == "DESTINATION":
        live = "ARRIVED"
    elif "RUNNING" in status_raw or "DEPARTED" in status_raw:
        live = "RUNNING"
    elif "BOARD" in status_raw or "START" in status_raw:
        live = "BOARDING"
    elif "SCHEDULE" in status_raw or "YET" in status_raw:
        live = "SCHEDULED"
    else:
        live = status_raw if status_raw in {
            "BOARDING", "RUNNING", "SCHEDULED", "ARRIVED", "UNKNOWN"
        } else "RUNNING"

    delay = data.get("delay_minutes")
    try:
        delay_i = int(delay) if delay is not None else 0
    except (TypeError, ValueError):
        delay_i = 0

    current = (
        data.get("current_station_name")
        or data.get("current_station_code")
        or "—"
    )
    nxt = data.get("next_stop_name") or data.get("next_stop_code") or "—"
    number = str(data.get("train_number") or train_number_from_id(train_id or ""))
    name = str(data.get("train_name") or number)

    msg_parts = [live.replace("_", " ").title()]
    if delay_i:
        msg_parts.append(f"+{delay_i} min delay")
    if current and current != "—":
        msg_parts.append(f"at {current}")
    if nxt and nxt != "—":
        msg_parts.append(f"next {nxt}")

    return {
        "train_id": train_id or number,
        "id": train_id or number,
        "train_name": name,
        "name": name,
        "source": data.get("source"),
        "destination": data.get("destination"),
        "live_status": live,
        "delay_minutes": delay_i,
        "current_station": current,
        "next_station": nxt,
        "eta_destination": None,
        "status_message": " · ".join(msg_parts),
        "last_updated": data.get("last_updated"),
        "overall_progress_pct": data.get("overall_progress_pct"),
        "provider": "SUPER_TRAVEL",
        "raw": data,
    }


def map_train_row(
    row: dict[str, Any],
    *,
    source_label: str,
    destination_label: str,
    passengers: int,
    travel_class: Optional[str],
) -> dict[str, Any]:
    """Map Super Travel train-list item → catalog-ready dict + display fields."""
    number = str(row.get("train_number") or row.get("id") or "").strip()
    name = str(row.get("train_name") or f"Train {number}").strip()
    cls = (travel_class or "3A").upper()
    if cls in {"ECONOMY", "SLEEPER"}:
        cls = "SL"
    unit = estimate_unit_price(row.get("distance_km"), cls)
    dep = _hhmm(row.get("departure_time"))
    arr = _hhmm(row.get("arrival_time"))
    duration = str(row.get("running_time") or "—")
    train_id = number  # live API uses train_number

    return {
        "id": train_id,
        "name": name,
        "source": source_label.title(),
        "destination": destination_label.title(),
        "departure_time": dep,
        "arrival_time": arr,
        "duration": duration,
        "class": cls,
        "unit_price": unit,
        "available_seats": 24,
        "rac_seats": 8,
        "waiting_list": 0,
        "max_waiting_list": 120,
        "availability_status": "AVAILABLE",
        "train_number": number,
        "distance_km": row.get("distance_km"),
        "origin_code": row.get("origin_code"),
        "destination_code": row.get("destination_code"),
        "provider": "SUPER_TRAVEL",
        "_passengers": passengers,
    }


CITY_AIRPORT_CODES: dict[str, str] = {
    "delhi": "DEL",
    "new delhi": "DEL",
    "mumbai": "BOM",
    "bombay": "BOM",
    "bangalore": "BLR",
    "bengaluru": "BLR",
    "hyderabad": "HYD",
    "chennai": "MAA",
    "kolkata": "CCU",
    "calcutta": "CCU",
    "pune": "PNQ",
    "ahmedabad": "AMD",
    "goa": "GOI",
    "jaipur": "JAI",
    "lucknow": "LKO",
    "kochi": "COK",
    "cochin": "COK",
    "chandigarh": "IXC",
    "indore": "IDR",
    "bhopal": "BHO",
    "nagpur": "NAG",
    "surat": "STV",
    "vadodara": "BDQ",
    "varanasi": "VNS",
    "amritsar": "ATQ",
    "srinagar": "SXR",
    "guwahati": "GAU",
    "trivandrum": "TRV",
    "thiruvananthapuram": "TRV",
    "coimbatore": "CJB",
    "visakhapatnam": "VTZ",
    "patna": "PAT",
    "ranchi": "IXR",
    "raipur": "RPR",
    "dubai": "DXB",
    "singapore": "SIN",
    "london": "LHR",
    "new york": "JFK",
    "bangkok": "BKK",
}


def _flight_timeout() -> float:
    return float(get_settings().super_travel_flight_timeout_seconds)


def parse_money(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^\d.]", "", str(value))
    try:
        return float(text) if text else 0.0
    except ValueError:
        return 0.0


def _cabin_for_api(travel_class: Optional[str]) -> str:
    raw = (travel_class or "economy").strip().lower().replace(" ", "_")
    mapping = {
        "e": "economy",
        "economy": "economy",
        "premium_economy": "premium_economy",
        "premiumeconomy": "premium_economy",
        "business": "business",
        "first": "first",
        "1a": "first",
        "2a": "business",
        "3a": "economy",
    }
    return mapping.get(raw, "economy")


async def resolve_airport_code(query: str) -> Optional[str]:
    """Resolve city / airport name to IATA via GET /flights/airports."""
    raw = (query or "").strip()
    if not raw:
        return None
    key = " ".join(raw.lower().split())
    cached = CITY_AIRPORT_CODES.get(key)
    if cached:
        return cached

    upper = raw.upper()
    if re.fullmatch(r"[A-Z]{3}", upper):
        return upper

    base = _base_url()
    if not base:
        return cached

    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.get(
                f"{base}/api/v1/flights/airports",
                params={"q": raw, "limit": 10},
            )
            resp.raise_for_status()
            payload = resp.json()
            data = payload.get("data") or {}
            rows = data.get("airports") if isinstance(data, dict) else None
            if not rows:
                return cached
            for row in rows:
                code = str(row.get("code") or "").upper()
                city = str(row.get("city") or "").lower()
                name = str(row.get("name") or "").lower()
                if code == upper or key == city or key in city or key in name:
                    return code
            return str(rows[0].get("code") or "").upper() or cached
    except Exception as exc:  # noqa: BLE001
        logger.warning("airport lookup failed for %r: %s", raw, exc)
        return cached


async def fetch_flight_search(
    *,
    origin: str,
    destination: str,
    departure_date: date,
    passengers: int = 1,
    travel_class: Optional[str] = "economy",
    return_date: Optional[date] = None,
) -> list[dict[str, Any]]:
    base = _base_url()
    if not base:
        raise RuntimeError("SUPER_TRAVEL_API_BASE_URL is not set")

    trip_type = "round_trip" if return_date else "oneway"
    payload: dict[str, Any] = {
        "trip_type": trip_type,
        "travellers": {"adults": max(1, int(passengers)), "children": 0, "infants": 0},
        "cabin": _cabin_for_api(travel_class),
        "from_airport": origin,
        "to_airport": destination,
        "departure_date": departure_date.isoformat(),
        "nearby_airports": True,
        "direct_only": False,
        "refundable_only": False,
    }
    if return_date:
        payload["return_date"] = return_date.isoformat()

    async with httpx.AsyncClient(timeout=_flight_timeout()) as client:
        resp = await client.post(f"{base}/api/v1/flights/search", json=payload)
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise RuntimeError("Flight search returned no data")
        flights = data.get("flights") or []
        if not flights:
            raise RuntimeError(
                f"No live flights for {origin}→{destination} on {departure_date}"
            )
        return list(flights)


def map_flight_card(
    row: dict[str, Any],
    *,
    source_label: str,
    destination_label: str,
    passengers: int,
    travel_class: Optional[str],
) -> FlightResult:
    from app.schemas import FlightResult

    number = str(row.get("flight_number") or "").strip()
    index = str(row.get("index") or "").strip()
    flight_id = number or index or f"FLIGHT-{row.get('order_id') or '0'}"
    if number and index:
        flight_id = f"{number} [{index}]"
    airline = (
        str(row.get("airline_name") or "").strip()
        or str(row.get("airline_code") or "").strip()
        or "Airline"
    )
    price = parse_money(row.get("price") or row.get("gross_fare"))
    if price <= 0:
        selection = row.get("selection") or {}
        price = parse_money(selection.get("amount"))
    seats = row.get("seats")
    try:
        seat_count = int(seats) if seats is not None else 9
    except (TypeError, ValueError):
        seat_count = 9
    cabin = str(row.get("cabin_label") or row.get("cabin") or travel_class or "economy")
    return FlightResult(
        id=flight_id,
        airline=airline,
        source=str(row.get("departure_label") or row.get("departure_code") or source_label),
        destination=str(
            row.get("arrival_label") or row.get("arrival_code") or destination_label
        ),
        departure_time=str(row.get("departure_time") or "—"),
        arrival_time=str(row.get("arrival_time") or "—"),
        duration=str(row.get("duration") or "—"),
        price=price,
        currency="INR",
        travel_class=cabin,
        available_seats=max(seat_count, 0),
        provider="SUPER_TRAVEL",
    )
