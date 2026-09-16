"""Sabrah Travel Backend FastAPI entrypoint."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api import router as api_router
from app.api.admin import router as admin_router
from app.catalog import get_train_catalog
from app.config import get_settings
from app.providers import (
    build_bus_provider,
    build_flight_provider,
    build_hotel_provider,
    build_package_provider,
    build_train_provider,
)
from app.repositories import InMemoryBookingRepository
from app.schemas import HealthResponse
from app.services import TravelService

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    catalog = get_train_catalog()
    app.state.settings = settings
    app.state.train_catalog = catalog
    app.state.travel_service = TravelService(
        train_provider=build_train_provider(settings.train_provider),
        flight_provider=build_flight_provider(settings.flight_provider),
        hotel_provider=build_hotel_provider(settings.hotel_provider),
        package_provider=build_package_provider(settings.package_provider),
        booking_repo=InMemoryBookingRepository(),
        bus_provider=build_bus_provider(getattr(settings, "bus_provider", "mock")),
    )
    logging.getLogger(__name__).info(
        "Started %s env=%s providers=train:%s flight:%s bus:%s hotel:%s package:%s",
        settings.app_name,
        settings.app_env,
        settings.train_provider,
        settings.flight_provider,
        getattr(settings, "bus_provider", "mock"),
        settings.hotel_provider,
        settings.package_provider,
    )
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8000",
            "http://localhost:8000",
            "http://127.0.0.1:8001",
            "http://localhost:8001",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)
    app.include_router(admin_router)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        s = app.state.settings
        return HealthResponse(
            status="ok",
            app=s.app_name,
            env=s.app_env,
            providers={
                "train": s.train_provider,
                "flight": s.flight_provider,
                "bus": getattr(s, "bus_provider", "mock"),
                "hotel": s.hotel_provider,
                "package": s.package_provider,
            },
        )

    if FRONTEND_DIR.exists():
        @app.get("/admin")
        async def admin_page() -> FileResponse:
            return FileResponse(FRONTEND_DIR / "admin.html")

        @app.get("/dashboard")
        async def dashboard_page() -> FileResponse:
            return FileResponse(FRONTEND_DIR / "admin.html")

    return app


app = create_app()
