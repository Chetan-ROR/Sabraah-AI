"""Sabrah AI FastAPI entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agents import ConversationAgent
from app.api import router as api_router
from app.config import get_settings
from app.models import HealthResponse
from app.prompts import set_agent_store
from app.providers import (
    build_llm_provider,
    build_openai_client,
    build_stt_provider,
    build_tts_provider,
)
from app.services import AgentStore, InMemorySessionStore, SuperTravelEventsClient, TravelBackendClient
from app.tools import TravelToolExecutor

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

    # Validate required env at startup (tests can inject mocks before create_app).
    if not getattr(app.state, "skip_env_validation", False):
        settings.validate_required()

    openai_client = build_openai_client(settings)
    travel_client = TravelBackendClient(
        base_url=settings.travel_backend_base_url,
        api_key=settings.travel_backend_api_key,
        timeout=settings.travel_backend_timeout_seconds,
        web_app_base_url=settings.web_app_base_url,
    )
    events_client = SuperTravelEventsClient(
        base_url=settings.super_travel_api_base_url,
        timeout=settings.super_travel_timeout_seconds,
        web_app_base_url=settings.web_app_base_url,
    )
    session_store = InMemorySessionStore(ttl_minutes=settings.session_ttl_minutes)
    agent_store = AgentStore()
    set_agent_store(agent_store)
    agent = ConversationAgent(
        session_store=session_store,
        llm=build_llm_provider(settings, openai_client),
        stt=build_stt_provider(settings, openai_client),
        tts=build_tts_provider(settings, openai_client),
        tools=TravelToolExecutor(travel_client, events_client),
    )

    app.state.settings = settings
    app.state.session_store = session_store
    app.state.travel_client = travel_client
    app.state.agent_store = agent_store
    app.state.agent = agent
    logging.getLogger(__name__).info(
        "Started %s env=%s travel_backend=%s",
        settings.app_name,
        settings.app_env,
        settings.travel_backend_base_url,
    )
    yield


def create_app(*, skip_env_validation: bool = False) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.skip_env_validation = skip_env_validation

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8000",
            "http://localhost:8000",
            "http://127.0.0.1:3000",
            "http://localhost:3000",
            "http://192.168.1.6:3000",
        ],
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+)(:\d+)?",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logging.getLogger(__name__).exception(
            "unhandled_error path=%s", request.url.path
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": {
                    "message": "Something went wrong. Please try again.",
                    "code": "internal_error",
                }
            },
        )

    app.include_router(api_router)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        travel_client: TravelBackendClient = app.state.travel_client
        reachable = await travel_client.health()
        return HealthResponse(
            status="ok",
            travel_backend="reachable" if reachable else "unreachable",
            app=app.state.settings.app_name,
        )

    if FRONTEND_DIR.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(FRONTEND_DIR)),
            name="static",
        )

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(FRONTEND_DIR / "index.html")

        @app.get("/admin")
        async def admin_page() -> FileResponse:
            return FileResponse(FRONTEND_DIR / "admin.html")

        @app.get("/payment")
        async def payment_page() -> FileResponse:
            return FileResponse(FRONTEND_DIR / "payment.html")

    return app


app = create_app()
