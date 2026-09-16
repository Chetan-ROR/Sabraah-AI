"""API dependencies and auth."""

from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status

from app.config import Settings, get_settings
from app.services import TravelService


async def require_api_key(
    authorization: Optional[str] = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header. Use Bearer <API_KEY>.",
        )
    token = authorization[len("Bearer ") :].strip()
    if token != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )


def get_travel_service(request: Request) -> TravelService:
    return request.app.state.travel_service
