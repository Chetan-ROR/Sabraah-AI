"""HTTP client for Super Travel customer event APIs (user JWT)."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional
from urllib.parse import urljoin

import httpx

from app.services.travel_client import TravelBackendError

logger = logging.getLogger(__name__)

LOGIN_REQUIRED = TravelBackendError(
    "Please log in to Super Travel first, then I can show live events.",
    code="login_required",
)


def event_booking_paths(event_id: Any) -> dict[str, str]:
    eid = str(event_id or "").strip()
    return {
        "booking_path": f"/event-booking-detail?event_id={eid}",
        "detail_path": f"/event-detail/{eid}",
    }


def absolute_web_url(path: str, web_app_base_url: str = "") -> str:
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    base = (web_app_base_url or "").rstrip("/")
    if not base:
        return path if path.startswith("/") else f"/{path}"
    return urljoin(base + "/", path.lstrip("/"))


def map_event_card(
    item: dict[str, Any], *, web_app_base_url: str = ""
) -> dict[str, Any]:
    event_id = item.get("id") or item.get("event_id")
    paths = event_booking_paths(event_id)
    price = item.get("start_price")
    hint_bits = [
        bit
        for bit in (
            item.get("venue_name"),
            item.get("artist_name"),
            item.get("start_date_time"),
            f"from INR {price}" if price not in (None, "") else "",
        )
        if bit
    ]
    return {
        "id": str(event_id) if event_id is not None else "",
        "name": item.get("name") or "Event",
        "artist_name": item.get("artist_name"),
        "category_name": item.get("category_name"),
        "venue_name": item.get("venue_name"),
        "venue_address": item.get("venue_address"),
        "start_date_time": item.get("start_date_time"),
        "start_price": price,
        "brief_desc": item.get("brief_desc"),
        "hint": " · ".join(str(bit) for bit in hint_bits),
        "booking_url": absolute_web_url(paths["booking_path"], web_app_base_url),
        "detail_url": absolute_web_url(paths["detail_path"], web_app_base_url),
    }


def map_event_details(
    item: dict[str, Any], *, web_app_base_url: str = ""
) -> dict[str, Any]:
    card = map_event_card(item, web_app_base_url=web_app_base_url)
    venues = item.get("event_venues") or []
    first_venue = venues[0] if isinstance(venues, list) and venues else {}
    tickets = []
    for ticket in item.get("tickets") or []:
        if not isinstance(ticket, dict):
            continue
        tickets.append(
            {
                "id": ticket.get("id"),
                "name": ticket.get("name"),
                "ticket_type": ticket.get("ticket_type"),
                "price": ticket.get("price"),
                "available_seats": ticket.get("available_seats"),
                "max_per_booking": ticket.get("max_per_booking"),
            }
        )
    schedules = []
    for schedule in item.get("schedules") or []:
        if not isinstance(schedule, dict):
            continue
        schedules.append(
            {
                "id": schedule.get("id"),
                "event_date": schedule.get("event_date"),
                "start_time": schedule.get("start_time"),
                "end_time": schedule.get("end_time"),
            }
        )
    category = item.get("category")
    category_name = (
        category.get("name")
        if isinstance(category, dict)
        else item.get("category_name")
    )
    return {
        **card,
        "category_name": category_name or card.get("category_name"),
        "venue_name": card.get("venue_name") or first_venue.get("venue_name"),
        "minimum_age": item.get("minimum_age"),
        "tickets": tickets,
        "schedules": schedules,
        "brief_desc": item.get("brief_desc") or item.get("detailed_desc"),
    }


def _city_matches(item: dict[str, Any], city: Optional[str]) -> bool:
    if not city:
        return True
    needle = city.strip().lower()
    if not needle:
        return True
    hay = " ".join(
        str(item.get(key) or "")
        for key in ("venue_name", "venue_address", "name", "brief_desc")
    ).lower()
    return needle in hay


class SuperTravelEventsClient:
    """Calls api-repository customer_events with the logged-in user's JWT."""

    def __init__(
        self,
        base_url: str,
        timeout: float = 20.0,
        web_app_base_url: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._web_app_base_url = web_app_base_url.rstrip("/")

    def _headers(self, user_access_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {user_access_token}",
            "Accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        user_access_token: Optional[str],
        params: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> dict[str, Any]:
        token = (user_access_token or "").strip()
        if not token:
            raise LOGIN_REQUIRED

        url = f"{self._base_url}{path}"
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method,
                    url,
                    headers=self._headers(token),
                    params=params,
                )
        except httpx.TimeoutException as exc:
            logger.error(
                "events_api.timeout session_id=%s path=%s", session_id, path
            )
            raise TravelBackendError(
                "Event listings timed out. Please try again shortly.",
                code="events_timeout",
            ) from exc
        except httpx.HTTPError as exc:
            logger.error(
                "events_api.unavailable session_id=%s path=%s error=%s",
                session_id,
                path,
                type(exc).__name__,
            )
            raise TravelBackendError(
                "I cannot reach Super Travel events right now. Please try again later.",
                code="events_unavailable",
            ) from exc

        latency_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "events_api.request session_id=%s method=%s path=%s status=%s latency_ms=%.1f",
            session_id,
            method,
            path,
            response.status_code,
            latency_ms,
        )

        if response.status_code in {401, 403}:
            raise LOGIN_REQUIRED
        if response.status_code == 404:
            raise TravelBackendError(
                "I could not find that event.",
                code="event_not_found",
            )
        if response.status_code >= 400:
            raise TravelBackendError(
                "Event listings are unavailable right now. Please try again.",
                code="events_http_error",
            )

        try:
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            raise TravelBackendError(
                "Event listings returned an unexpected response.",
                code="events_malformed",
            ) from exc
        if not isinstance(data, dict):
            raise TravelBackendError(
                "Event listings returned an unexpected response.",
                code="events_malformed",
            )
        return data

    async def search(
        self,
        *,
        user_access_token: Optional[str],
        search: Optional[str] = None,
        artist: Optional[str] = None,
        city: Optional[str] = None,
        category_id: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": 1, "page_size": 20}
        if search:
            params["search"] = search
        if artist:
            params["artist"] = artist
        if category_id:
            params["category"] = category_id

        payload = await self._request(
            "GET",
            "/api/v1/customer_events/",
            user_access_token=user_access_token,
            params=params,
            session_id=session_id,
        )
        rows = payload.get("data") or []
        if not isinstance(rows, list):
            rows = []
        cards = [
            map_event_card(item, web_app_base_url=self._web_app_base_url)
            for item in rows
            if isinstance(item, dict) and _city_matches(item, city)
        ]
        return {
            "results": cards[:8],
            "count": len(cards),
            "provider": "SUPER_TRAVEL",
        }

    async def detail(
        self,
        event_id: str,
        *,
        user_access_token: Optional[str],
        session_id: Optional[str] = None,
    ) -> dict[str, Any]:
        payload = await self._request(
            "GET",
            f"/api/v1/customer_events/{event_id}/",
            user_access_token=user_access_token,
            session_id=session_id,
        )
        item = payload.get("data")
        if not isinstance(item, dict):
            raise TravelBackendError(
                "I could not find that event.",
                code="event_not_found",
            )
        details = map_event_details(item, web_app_base_url=self._web_app_base_url)
        return {"event": details, **details}
