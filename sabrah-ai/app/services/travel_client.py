"""HTTP client for Sabrah Travel Backend."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional, Union

import httpx

logger = logging.getLogger(__name__)


class TravelBackendError(Exception):
    def __init__(self, message: str, *, code: str = "travel_backend_error") -> None:
        super().__init__(message)
        self.code = code
        self.user_message = message


class TravelBackendClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=json,
                    params=params,
                )
        except httpx.TimeoutException as exc:
            logger.error(
                "travel_backend.timeout session_id=%s path=%s",
                session_id,
                path,
            )
            raise TravelBackendError(
                "The travel service timed out. Please try again shortly.",
                code="travel_timeout",
            ) from exc
        except httpx.HTTPError as exc:
            logger.error(
                "travel_backend.unavailable session_id=%s path=%s error=%s",
                session_id,
                path,
                type(exc).__name__,
            )
            raise TravelBackendError(
                "I cannot reach the travel service right now. Please try again later.",
                code="travel_unavailable",
            ) from exc

        latency_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "travel_backend.request session_id=%s method=%s path=%s status=%s latency_ms=%.1f",
            session_id,
            method,
            path,
            response.status_code,
            latency_ms,
        )

        if response.status_code >= 400:
            detail = "Travel service returned an error."
            try:
                payload = response.json()
                if isinstance(payload, dict) and payload.get("detail"):
                    detail = str(payload["detail"])
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
        if not isinstance(data, dict):
            raise TravelBackendError(
                "The travel service returned an unexpected response.",
                code="travel_malformed",
            )
        return data

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base_url}/health")
            return response.status_code == 200
        except httpx.HTTPError:
            return False
