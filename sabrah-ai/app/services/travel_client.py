"""HTTP client for api-repository (Super Travel Django API)."""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

CITY_AIRPORTS: dict[str, str] = {
    "delhi": "DEL",
    "new delhi": "DEL",
    "mumbai": "BOM",
    "bombay": "BOM",
    "bangalore": "BLR",
    "bengaluru": "BLR",
    "chennai": "MAA",
    "madras": "MAA",
    "kolkata": "CCU",
    "calcutta": "CCU",
    "hyderabad": "HYD",
    "pune": "PNQ",
    "goa": "GOI",
    "ahmedabad": "AMD",
    "jaipur": "JAI",
    "kochi": "COK",
    "cochin": "COK",
    "lucknow": "LKO",
    "indore": "IDR",
    "bhopal": "BHO",
    "nagpur": "NAG",
    "surat": "STV",
    "vadodara": "BDQ",
    "chandigarh": "IXC",
    "amritsar": "ATQ",
    "varanasi": "VNS",
    "patna": "PAT",
    "guwahati": "GAU",
    "trivandrum": "TRV",
    "thiruvananthapuram": "TRV",
}

CITY_STATIONS: dict[str, str] = {
    "indore": "INDB",
    "mumbai": "BCT",
    "bombay": "BCT",
    "delhi": "NDLS",
    "new delhi": "NDLS",
    "pune": "PUNE",
    "khandwa": "KNW",
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
}

CABIN_MAP = {
    "economy": "economy",
    "e": "economy",
    "premium_economy": "premium_economy",
    "premium economy": "premium_economy",
    "pe": "premium_economy",
    "business": "business",
    "b": "business",
    "first": "first",
    "f": "first",
}


class TravelBackendError(Exception):
    def __init__(self, message: str, *, code: str = "travel_backend_error") -> None:
        super().__init__(message)
        self.code = code
        self.user_message = message


class TravelBackendClient:
    """Calls api-repository REST APIs. Search is anonymous; booking needs JWT."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout: float = 90.0,
        web_app_base_url: str = "",
        device_id: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._web_app_base_url = (web_app_base_url or "").rstrip("/")
        self._device_id = device_id or f"sabrah-ai-{uuid.uuid4()}"

    def _headers(self, user_access_token: Optional[str] = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Device-ID": self._device_id,
            "X-Language": "en",
            "X-Timezone": "Asia/Kolkata",
        }
        token = (user_access_token or self._api_key or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _unwrap(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TravelBackendError(
                "The travel service returned an unexpected response.",
                code="travel_malformed",
            )
        status = str(payload.get("status") or "").lower()
        if status == "error":
            message = (
                payload.get("message")
                or payload.get("error")
                or "Travel service returned an error."
            )
            raise TravelBackendError(str(message), code="travel_http_error")
        data = payload.get("data", payload)
        if isinstance(data, list):
            return {
                "results": data,
                "count": payload.get("count", len(data)),
            }
        if isinstance(data, dict):
            return data
        raise TravelBackendError(
            "The travel service returned an unexpected response.",
            code="travel_malformed",
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
        user_access_token: Optional[str] = None,
        unwrap: bool = True,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method,
                    url,
                    headers=self._headers(user_access_token),
                    json=json,
                    params=params,
                )
        except httpx.TimeoutException as exc:
            logger.error(
                "api_repository.timeout session_id=%s path=%s", session_id, path
            )
            raise TravelBackendError(
                "The travel service timed out. Please try again shortly.",
                code="travel_timeout",
            ) from exc
        except httpx.HTTPError as exc:
            logger.error(
                "api_repository.unavailable session_id=%s path=%s error=%s",
                session_id,
                path,
                type(exc).__name__,
            )
            raise TravelBackendError(
                "I cannot reach Super Travel right now. Please try again later.",
                code="travel_unavailable",
            ) from exc

        latency_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "api_repository.request session_id=%s method=%s path=%s status=%s latency_ms=%.1f",
            session_id,
            method,
            path,
            response.status_code,
            latency_ms,
        )

        if response.status_code in {401, 403}:
            raise TravelBackendError(
                "Please log in to Super Travel first, then I can continue this booking.",
                code="login_required",
            )
        if response.status_code >= 400:
            detail = "Travel service returned an error."
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    detail = str(
                        payload.get("message")
                        or payload.get("detail")
                        or payload.get("error")
                        or detail
                    )
            except Exception:  # noqa: BLE001
                pass
            raise TravelBackendError(detail, code="travel_http_error")

        try:
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            raise TravelBackendError(
                "The travel service returned an unexpected response.",
                code="travel_malformed",
            ) from exc
        if unwrap:
            return self._unwrap(data)
        if not isinstance(data, dict):
            raise TravelBackendError(
                "The travel service returned an unexpected response.",
                code="travel_malformed",
            )
        return data

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{self._base_url}/api/v1/flights/airports",
                    params={"q": "DEL"},
                    headers=self._headers(),
                )
            return response.status_code < 500
        except httpx.HTTPError:
            return False

    async def resolve_airport(
        self, query: str, *, session_id: Optional[str] = None
    ) -> str:
        raw = (query or "").strip()
        if not raw:
            raise TravelBackendError("Please tell me the city or airport.", code="invalid_airport")
        key = " ".join(raw.lower().split())
        if key in CITY_AIRPORTS:
            return CITY_AIRPORTS[key]
        if re.fullmatch(r"[A-Za-z]{3}", raw):
            return raw.upper()
        payload = await self.request(
            "GET",
            "/api/v1/flights/airports",
            params={"q": raw},
            session_id=session_id,
        )
        rows = payload.get("airports") or payload.get("results") or []
        if isinstance(payload.get("data"), list):
            rows = payload["data"]
        if not isinstance(rows, list) or not rows:
            raise TravelBackendError(
                f"I could not find an airport for {raw}. Please say the city or IATA code.",
                code="airport_not_found",
            )
        first = rows[0] if isinstance(rows[0], dict) else {}
        code = str(first.get("code") or "").upper()
        if not code:
            raise TravelBackendError(
                f"I could not find an airport for {raw}.",
                code="airport_not_found",
            )
        return code

    async def resolve_station(
        self, query: str, *, session_id: Optional[str] = None
    ) -> str:
        raw = (query or "").strip()
        if not raw:
            raise TravelBackendError("Please tell me the station or city.", code="invalid_station")
        key = " ".join(raw.lower().split())
        if key in CITY_STATIONS:
            return CITY_STATIONS[key]
        upper = raw.upper()
        if re.fullmatch(r"[A-Z]{2,4}", upper):
            return upper
        payload = await self.request(
            "GET",
            "/api/v1/trains/station-codes/",
            params={"search": raw, "page": 1, "page_size": 10},
            session_id=session_id,
        )
        rows = payload.get("results") or payload.get("data") or []
        if not isinstance(rows, list) or not rows:
            mapped = CITY_STATIONS.get(key)
            if mapped:
                return mapped
            raise TravelBackendError(
                f"I could not find a station for {raw}.",
                code="station_not_found",
            )
        first = rows[0] if isinstance(rows[0], dict) else {}
        return str(first.get("code") or first.get("station_code") or "").upper() or upper
