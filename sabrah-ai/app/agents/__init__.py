"""Conversation agent with full OpenAI tool-calling loop."""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from typing import Any, Optional

from app.models import ChatResponse, SessionState
from app.prompts import build_system_message
from app.providers import (
    LLMProvider,
    ProviderError,
    SpeechToTextProvider,
    TextToSpeechProvider,
    is_unusable_transcript,
    is_wake_only_transcript,
)
from app.services.session_store import SessionStore, append_message, update_memory_from_slots
from app.tools import TOOL_DEFINITIONS, TravelToolExecutor, parse_tool_arguments

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 6

MEMORY_HINT_TOOLS = {
    "search_trains",
    "search_flights",
    "search_buses",
    "search_hotels",
    "search_packages",
    "search_local_transport",
    "search_events",
    "get_event_details",
    "create_package_plan",
    "create_booking",
    "add_to_wishlist",
    "get_wishlist",
    "request_charter",
    "escalate_to_sales",
    "request_refund",
    "submit_feedback",
    "get_weather_alert",
    "get_train_details",
    "get_live_train_status",
}


SCREEN_OPTIONS_LINE = (
    "The following details are shown on your screen. "
    "Please read them and tell me which option you want."
)

MAIN_MENU_LINE = (
    "Hi, I'm Sabrah. Tell me the trip — where, when, and who is travelling — "
    "or say book a flight, train, hotel, or event. I can also cancel or refund."
)

PURPOSE_ASK_LINE = (
    "What's the trip for — leisure, family, business, wedding, or something else?"
)

ENGLISH_ONLY_REPROMPT = (
    "Please speak in English for now. Multi-language support is coming soon."
)

_NON_LATIN_SCRIPT = re.compile(r"[^\x00-\x7F]")

STEP_SCREEN_LINES = {
    "trains": (
        "Train options are shown on your screen. "
        "Please read them and tell me which train you want."
    ),
    "buses": (
        "Bus options are shown on your screen. "
        "Please pick a bus, or say skip bus."
    ),
    "hotels": (
        "Hotel options are shown on your screen. "
        "Please read them and tell me which hotel you want."
    ),
    "flights": (
        "Flight options are on your screen. "
        "Pick a flight, or say skip flight."
    ),
    "local": (
        "Local transport options are on your screen. "
        "Pick a cab or car rental, or say skip local."
    ),
}

FULL_COACH_SEATS = 72


class ConversationAgent:
    def __init__(
        self,
        *,
        session_store: SessionStore,
        llm: LLMProvider,
        stt: SpeechToTextProvider,
        tts: TextToSpeechProvider,
        tools: TravelToolExecutor,
    ) -> None:
        self._sessions = session_store
        self._llm = llm
        self._stt = stt
        self._tts = tts
        self._tools = tools

    async def handle_text(self, session_id: str, message: str) -> ChatResponse:
        session = await self._require_session(session_id)
        return await self._run_turn(session, message.strip())

    def _ignored_voice(self, session: SessionState) -> ChatResponse:
        return ChatResponse(
            session_id=session.session_id,
            user_text="",
            assistant_text="",
            memory=session.memory,
            ignored=True,
        )

    async def handle_voice(
        self,
        session_id: str,
        audio_bytes: bytes,
        filename: str = "audio.webm",
    ) -> ChatResponse:
        session = await self._require_session(session_id)
        if not audio_bytes:
            return self._ignored_voice(session)
        try:
            user_text = await self._stt.transcribe(audio_bytes, filename=filename)
        except ProviderError as exc:
            if exc.code in {"empty_audio", "speech_not_recognized"}:
                return self._ignored_voice(session)
            raise
        if not (user_text or "").strip():
            return self._ignored_voice(session)
        if is_wake_only_transcript(user_text):
            return await self._run_turn(session, "Hey Sabrah")
        if is_unusable_transcript(user_text):
            return self._ignored_voice(session)
        return await self._run_turn(session, user_text)

    async def _require_session(self, session_id: str) -> SessionState:
        session = await self._sessions.get(session_id)
        if session is None:
            raise ProviderError(
                "Your conversation session expired. Please start a new conversation.",
                code="session_not_found",
            )
        return session

    @staticmethod
    def _maybe_set_booking_mode(session: SessionState, user_text: str) -> None:
        ConversationAgent._ingest_trip_signals(session, user_text)
        text = user_text.lower()
        mem = session.memory

        if "jain" in text:
            mem.meal_preference = "jain"
            mem.meal_asked = True
        elif "non veg" in text or "non-veg" in text or "nonveg" in text:
            mem.meal_preference = "non_veg"
            mem.meal_asked = True
        elif "veg meal" in text or "vegetarian" in text or text.strip() in {"veg", "veg."}:
            mem.meal_preference = "veg"
            mem.meal_asked = True
        elif "no meal" in text or "without meal" in text or "skip meal" in text:
            mem.meal_preference = "none"
            mem.book_meal_with_ticket = False
            mem.meal_asked = True
        if "allerg" in text:
            mem.allergies = user_text.strip()[:200]
        if "catering" in text and ("full" in text or "entire" in text or "whole" in text):
            mem.catering_full_train = True

        if ConversationAgent._is_event_intent(text):
            mem.user_goal = "book_event"
            mem.booking_mode = "event"
            mem.intent = "book"
            mem.flow_step = "event_search"
        elif ConversationAgent._is_flight_intent(text) and mem.user_goal in {
            None,
            "book_train",
        }:
            mem.user_goal = "book_flight"
            mem.transport_type = "flight"
            mem.flight_required = True
            if mem.booking_mode in {None, "normal"}:
                mem.booking_mode = "flight"
        elif ConversationAgent._is_hotel_intent(text) and mem.user_goal in {None, "book_train"}:
            mem.user_goal = "book_hotel"
            mem.hotel_required = True
            mem.booking_mode = "hotel"

        # Support / charter can interrupt anytime.
        if any(
            h in text
            for h in (
                "refund",
                "cancel booking",
                "cancel ticket",
                "cancellation",
                "customer support",
                "talk to human",
                "sales executive",
                "complaint",
            )
        ):
            mem.intent = "support"
            if "refund" in text:
                mem.user_goal = "refund"
            elif "cancel" in text:
                mem.user_goal = "cancel"
        if any(
            h in text
            for h in (
                "full coach",
                "entire train",
                "book the whole train",
                "charter",
                "group tour coach",
                "group booking",
                "group of",
            )
        ):
            mem.booking_mode = "charter"
            mem.intent = "charter"
            mem.user_goal = "charter"

        # Capture group size; >9 passengers → coach/charter (not split tickets).
        count = ConversationAgent._parse_passenger_count(user_text)
        if count:
            mem.passenger_count = count
            if count > 9:
                mem.booking_mode = "charter"
                mem.intent = "charter"
                mem.user_goal = "charter"
                if not mem.charter_type:
                    mem.charter_type = "full_coach"

        if mem.booking_mode:
            return

        # Do NOT force journey mode for wedding/conference — those are trip purpose only.
        journey_hints = (
            "multi-leg",
            "multileg",
            "multi leg",
            "complete journey",
            "full journey",
            "flight then train",
            "from abroad",
            "international flight",
        )
        itinerary_hints = (
            "itinerary",
            "package",
            "full plan",
            "full itinerary",
            "trip plan",
            "holiday package",
            "with hotel",
            "train and hotel",
        )
        normal_hints = (
            "normal booking",
            "normal",
            "just train",
            "train only",
            "only train",
            "simple booking",
            "book a train",
            "book train",
        )
        if any(h in text for h in journey_hints):
            mem.booking_mode = "journey"
        elif any(h in text for h in itinerary_hints):
            mem.booking_mode = "itinerary"
        elif any(h in text for h in normal_hints):
            mem.booking_mode = "normal"

    @staticmethod
    def _uses_non_english_script(user_text: str) -> bool:
        return bool(_NON_LATIN_SCRIPT.search(user_text or ""))

    async def _run_turn(self, session: SessionState, user_text: str) -> ChatResponse:
        started = time.perf_counter()
        if self._uses_non_english_script(user_text):
            in_event = (
                session.memory.user_goal == "book_event"
                or session.memory.booking_mode == "event"
            )
            if not in_event:
                append_message(session, "user", user_text)
                return await self._finalize_turn(
                    session=session,
                    user_text=user_text,
                    assistant_text=ENGLISH_ONLY_REPROMPT,
                    tools_used=[],
                    offerings_updated=False,
                    booking_id=None,
                    payment_amount=None,
                    payment_currency=None,
                    booking_snapshot={},
                    started=started,
                )

        self._maybe_set_booking_mode(session, user_text)
        append_message(session, "user", user_text)

        tools_used: list[str] = []
        offerings_updated = False
        assistant_text = ""
        booking_id: Optional[str] = None
        payment_amount: Optional[float] = None
        payment_currency: Optional[str] = None
        booking_snapshot: dict[str, Any] = {}
        session.memory.language = "en"

        # Large groups: offer full coach instead of "max 9 / split bookings".
        early_text = self._handle_large_group_hint(session, user_text)
        # Main menu + where → why → when (simple guided booking).
        if early_text is None:
            early_text = await self._handle_guided_flow(
                session, user_text, tools_used
            )
        if early_text is None:
            early_text = await self._handle_event_flow(
                session, user_text, tools_used
            )
        # Correct / change a passenger name (must run before option matching).
        if early_text is None:
            early_text = self._handle_name_correction(session, user_text)
        # Mid-flow meal / phone edits (confirm step included).
        if early_text is None:
            early_text = self._handle_mid_booking_edits(session, user_text)
        # User asks to repeat names / phone / meal → answer from memory.
        if early_text is None and self._wants_booking_review(user_text):
            if session.travelers or session.memory.contact_phone:
                early_text = self._format_booking_review(session)
        if early_text is None:
            early_text = self._handle_passenger_details(session, user_text)
        if early_text is None:
            early_text = await self._handle_sequential_choice(
                session, user_text, tools_used
            )
        if early_text is None:
            early_text = await self._handle_normal_booking_flow(
                session, user_text, tools_used
            )
        # Explicit yes → call Travel Backend create_booking / request_charter.
        if early_text is None:
            confirmed = await self._handle_booking_confirm(
                session, user_text, tools_used
            )
            if confirmed is not None:
                assistant_text, booking_meta = confirmed
                offerings_updated = bool(session.last_offerings)
                return await self._finalize_turn(
                    session=session,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    tools_used=tools_used,
                    offerings_updated=offerings_updated,
                    booking_id=booking_meta.get("booking_id"),
                    payment_amount=booking_meta.get("payment_amount"),
                    payment_currency=booking_meta.get("payment_currency"),
                    booking_snapshot=booking_meta.get("booking_snapshot") or {},
                    started=started,
                )
        if early_text is not None:
            assistant_text = early_text
            offerings_updated = bool(session.last_offerings)
            return await self._finalize_turn(
                session=session,
                user_text=user_text,
                assistant_text=assistant_text,
                tools_used=tools_used,
                offerings_updated=offerings_updated,
                booking_id=booking_id,
                payment_amount=payment_amount,
                payment_currency=payment_currency,
                booking_snapshot=booking_snapshot,
                started=started,
            )

        messages = self._build_llm_messages(session)
        for _ in range(MAX_TOOL_ITERATIONS):
            message = await self._llm.chat(messages, tools=TOOL_DEFINITIONS)
            tool_calls = getattr(message, "tool_calls", None) or []

            if tool_calls:
                messages.append(self._assistant_tool_call_message(message))
                for call in tool_calls:
                    name = call.function.name
                    args = parse_tool_arguments(call.function.arguments)
                    if name == "create_booking":
                        meta = dict(args.get("metadata") or {})
                        mem = session.memory
                        meta.update(
                            {
                                "meal_preference": mem.meal_preference,
                                "allergies": mem.allergies,
                                "dietary_restrictions": mem.dietary_restrictions,
                                "source": mem.source,
                                "destination": mem.destination,
                                "traveler_names": [
                                    t.get("name")
                                    for t in session.travelers
                                    if t.get("name")
                                ],
                            }
                        )
                        args["metadata"] = meta
                        if session.travelers and not args.get("passenger_count"):
                            args["passenger_count"] = len(session.travelers)
                    if name == "request_charter":
                        if session.travelers and not args.get("traveler_names"):
                            args["traveler_names"] = [
                                t.get("name") for t in session.travelers if t.get("name")
                            ]
                    if name == "search_trains":
                        if session.memory.train_preference and not args.get("preference"):
                            args["preference"] = session.memory.train_preference
                        if session.memory.trip_type and not args.get("trip_type"):
                            args["trip_type"] = session.memory.trip_type
                        if session.memory.return_date and not args.get("return_date"):
                            args["return_date"] = session.memory.return_date
                        if session.memory.passenger_count and "passengers" not in args:
                            args["passengers"] = session.memory.passenger_count
                    logger.info(
                        "tool.selected session_id=%s tool=%s",
                        session.session_id,
                        name,
                    )
                    if name not in self._tools.tool_names:
                        result = {
                            "error": "unsupported_intent",
                            "message": f"Unsupported tool: {name}",
                        }
                    else:
                        result = await self._tools.execute(
                            name,
                            args,
                            session_id=session.session_id,
                            user_access_token=session.user_access_token,
                        )
                        tools_used.append(name)
                        before = dict(session.last_offerings)
                        self._update_memory_from_tool(session, name, args, result)
                        if session.last_offerings != before:
                            offerings_updated = True
                        if (
                            name == "create_booking"
                            and isinstance(result, dict)
                            and result.get("booking_id")
                            and not result.get("error")
                        ):
                            booking_id = str(result["booking_id"])
                            if result.get("total_price") is not None:
                                payment_amount = float(result["total_price"])
                            payment_currency = str(result.get("currency") or "INR")
                            booking_snapshot = {
                                "passenger_name": result.get("passenger_name")
                                or args.get("passenger_name"),
                                "phone": args.get("contact_phone")
                                or result.get("contact_phone"),
                                "train_id": args.get("item_id")
                                or result.get("item_id"),
                            }
                            meta = result.get("metadata") or {}
                            session.booking_details = meta
                            if meta:
                                session.last_offerings["booking"] = {
                                    "booking_id": booking_id,
                                    **meta,
                                }

                    tool_content = _safe_json(result)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": tool_content,
                        }
                    )
                    append_message(
                        session,
                        "tool",
                        tool_content,
                        tool_call_id=call.id,
                        name=name,
                    )
                continue

            assistant_text = (message.content or "").strip()
            break

        if not assistant_text:
            assistant_text = (
                "I found some information but could not form a clear reply. "
                "Could you please rephrase your request?"
            )

        # Keep sequential UI to ONE step at a time.
        seq_text = self._normalize_sequential_offerings(session)
        if seq_text:
            assistant_text = seq_text
            offerings_updated = True
        elif offerings_updated and session.last_offerings:
            assistant_text = SCREEN_OPTIONS_LINE

        return await self._finalize_turn(
            session=session,
            user_text=user_text,
            assistant_text=assistant_text,
            tools_used=tools_used,
            offerings_updated=offerings_updated,
            booking_id=booking_id,
            payment_amount=payment_amount,
            payment_currency=payment_currency,
            booking_snapshot=booking_snapshot,
            started=started,
        )

    async def _finalize_turn(
        self,
        *,
        session: SessionState,
        user_text: str,
        assistant_text: str,
        tools_used: list[str],
        offerings_updated: bool,
        booking_id: Optional[str],
        payment_amount: Optional[float],
        payment_currency: Optional[str],
        booking_snapshot: dict[str, Any],
        started: float,
    ) -> ChatResponse:
        tts_text = assistant_text
        if offerings_updated and session.last_offerings and not assistant_text:
            assistant_text = SCREEN_OPTIONS_LINE
            tts_text = SCREEN_OPTIONS_LINE

        booking_details = dict(session.booking_details or {})
        if booking_id and booking_details:
            platform = booking_details.get("platform")
            seats = booking_details.get("seats") or []
            seat_labels = [
                str(s.get("seat_label") or s.get("berth") or "")
                for s in seats
                if isinstance(s, dict)
            ]
            seat_labels = [s for s in seat_labels if s]
            extra = ""
            if platform:
                extra += f" Platform {platform}."
            if seat_labels:
                extra += f" Seats: {', '.join(seat_labels)}."
            if extra and extra not in assistant_text:
                assistant_text += extra
                tts_text = assistant_text

        if booking_id and session.memory.destination:
            weather = await self._tools.execute(
                "get_weather_alert",
                {"city": session.memory.destination},
                session_id=session.session_id,
            )
            if isinstance(weather, dict) and not weather.get("error"):
                session.last_offerings["weather"] = weather
                tip = weather.get("tip") or weather.get("summary")
                if tip and tip not in assistant_text:
                    assistant_text += f" {tip}"
                    tts_text = assistant_text
                session.memory.weather_alert_sent = True
            if not session.memory.feedback_requested:
                assistant_text += (
                    " Wishing you a safe and happy trip! "
                    "We would love your feedback after your journey."
                )
                tts_text = assistant_text
                session.memory.feedback_requested = True

        append_message(session, "assistant", assistant_text)
        await self._sessions.save(session)

        audio_b64 = None  # type: Optional[str]
        tts_error = None  # type: Optional[str]
        try:
            audio_bytes = await self._tts.synthesize(tts_text)
            audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
        except ProviderError as exc:
            tts_error = exc.user_message
            logger.warning(
                "tts.failed session_id=%s code=%s",
                session.session_id,
                exc.code,
            )

        logger.info(
            "turn.complete session_id=%s tools=%s latency_ms=%.1f",
            session.session_id,
            tools_used,
            (time.perf_counter() - started) * 1000,
        )

        payment_url = None
        open_booking = False
        booking_url = None
        if session.memory.user_goal == "book_event" and session.memory.flow_step == "event_open":
            booking_url = self._selected_event_booking_url(session)
            open_booking = bool(booking_url)
        if session.memory.user_goal == "book_flight" and session.memory.flow_step == "flight_open":
            checkout = session.last_offerings.get("flight_checkout") or {}
            if isinstance(checkout, dict) and checkout.get("booking_url"):
                booking_url = checkout["booking_url"]
                open_booking = True
        if booking_id:
            from urllib.parse import urlencode

            mem = session.memory
            selected_id = str(
                booking_snapshot.get("train_id")
                or mem.selected_train_id
                or mem.selected_option
                or ""
            )
            train = session.itinerary_selections.get("train") or {}
            if not train:
                train = next(
                    (
                        row
                        for row in session.last_search_results
                        if isinstance(row, dict) and row.get("id") == selected_id
                    ),
                    {},
                )

            passenger_name = str(
                booking_snapshot.get("passenger_name") or mem.passenger_name or ""
            )
            contact_phone = str(
                booking_snapshot.get("phone") or mem.contact_phone or ""
            )
            bus = session.itinerary_selections.get("bus") or {}
            hotel = session.itinerary_selections.get("hotel") or {}
            bd = booking_details or booking_snapshot or {}
            seats = bd.get("seats") or booking_snapshot.get("seats") or []
            seat_summary = "; ".join(
                f"{s.get('passenger')}:{s.get('seat_label') or s.get('berth')}"
                for s in seats
                if isinstance(s, dict)
            )
            traveler_names = bd.get("traveler_names") or booking_snapshot.get(
                "traveler_names"
            )
            if not traveler_names and session.travelers:
                traveler_names = [
                    t.get("name") for t in session.travelers if t.get("name")
                ]
            passengers_csv = ", ".join(str(n) for n in (traveler_names or []) if n)

            query = urlencode(
                {
                    "booking_id": booking_id,
                    "amount": payment_amount if payment_amount is not None else "",
                    "currency": payment_currency or "INR",
                    "session_id": session.session_id,
                    "passenger_name": passenger_name,
                    "passengers": passengers_csv,
                    "phone": contact_phone,
                    "source": mem.source
                    or bd.get("source")
                    or train.get("source")
                    or "",
                    "destination": mem.destination
                    or bd.get("destination")
                    or train.get("destination")
                    or "",
                    "date": mem.departure_date or bd.get("departure_date") or "",
                    "train_id": selected_id
                    or bd.get("train_id")
                    or train.get("id")
                    or "",
                    "train_name": bd.get("train_name")
                    or booking_snapshot.get("train_name")
                    or train.get("name")
                    or "",
                    "depart": bd.get("departure_time")
                    or booking_snapshot.get("departure_time")
                    or train.get("departure_time")
                    or "",
                    "arrive": bd.get("arrival_time")
                    or booking_snapshot.get("arrival_time")
                    or train.get("arrival_time")
                    or "",
                    "duration": bd.get("duration")
                    or booking_snapshot.get("duration")
                    or train.get("duration")
                    or "",
                    "travel_class": bd.get("travel_class")
                    or booking_snapshot.get("travel_class")
                    or train.get("class")
                    or train.get("travel_class")
                    or mem.travel_class
                    or "",
                    "platform": bd.get("platform")
                    or booking_snapshot.get("platform")
                    or "",
                    "source_station": bd.get("source_station")
                    or booking_snapshot.get("source_station")
                    or "",
                    "destination_station": bd.get("destination_station")
                    or booking_snapshot.get("destination_station")
                    or "",
                    "seats": seat_summary,
                    "meal": bd.get("meal_preference")
                    or booking_snapshot.get("meal_preference")
                    or mem.meal_preference
                    or "",
                    "allergies": bd.get("allergies") or mem.allergies or "",
                    "passenger_count": bd.get("passenger_count")
                    or len(traveler_names or [])
                    or mem.passenger_count
                    or "",
                    "bus_name": bus.get("name") or "",
                    "hotel_name": hotel.get("name") or "",
                }
            )
            payment_url = f"/payment?{query}"

        return ChatResponse(
            session_id=session.session_id,
            user_text=user_text,
            assistant_text=assistant_text,
            audio_base64=audio_b64,
            audio_mime_type="audio/mpeg" if audio_b64 else None,
            tools_used=tools_used,
            tts_error=tts_error,
            memory=session.memory,
            booking_id=booking_id,
            payment_url=payment_url,
            booking_url=booking_url,
            open_booking=open_booking,
            payment_amount=payment_amount,
            payment_currency=payment_currency,
            offerings=session.last_offerings or {},
            booking_details=booking_details,
        )

    def _build_llm_messages(self, session: SessionState) -> list[dict[str, Any]]:
        """Build LLM context from user/assistant turns only.

        Tool-call round-trips are handled inside the current turn loop and are
        not replayed from history (avoids orphaned tool messages).
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_message(session)}
        ]
        for item in session.conversation_history:
            if item.role in {"user", "assistant"} and item.content:
                messages.append({"role": item.role, "content": item.content})
        return messages

    @staticmethod
    def _assistant_tool_call_message(message: Any) -> dict[str, Any]:
        tool_calls = []
        for call in message.tool_calls or []:
            tool_calls.append(
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
            )
        return {
            "role": "assistant",
            "content": message.content,
            "tool_calls": tool_calls,
        }

    def _update_memory_from_tool(
        self,
        session: SessionState,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        if tool_name not in MEMORY_HINT_TOOLS:
            return
        slots: dict[str, Any] = {}
        mapping = {
            "source": "source",
            "destination": "destination",
            "departure_date": "departure_date",
            "return_date": "return_date",
            "travel_class": "travel_class",
            "city": "destination",
        }
        if tool_name not in {"search_events", "get_event_details"}:
            for arg_key, mem_key in mapping.items():
                if args.get(arg_key):
                    slots[mem_key] = args[arg_key]
            if "passengers" in args:
                slots["passenger_count"] = args["passengers"]
            # Prefer effective values returned by the tool (e.g. defaulted dates).
            for key in ("source", "destination", "departure_date", "return_date"):
                if result.get(key):
                    slots[key] = result[key]
            if result.get("passengers"):
                slots["passenger_count"] = result["passengers"]
        if tool_name == "search_trains":
            slots["transport_type"] = "train"
            slots["intent"] = "search_trains"
            if args.get("preference"):
                slots["train_preference"] = args["preference"]
            if args.get("trip_type"):
                slots["trip_type"] = args["trip_type"]
            if not session.memory.booking_mode:
                slots["booking_mode"] = "normal"
        elif tool_name == "search_flights":
            slots["transport_type"] = "flight"
            slots["intent"] = "search_flights"
            slots["flight_required"] = True
            if session.memory.user_goal == "book_flight" or session.memory.booking_mode == "flight":
                slots["booking_mode"] = "flight"
                slots["user_goal"] = "book_flight"
            elif session.memory.booking_mode in {None, "normal"}:
                slots["booking_mode"] = "journey"
        elif tool_name == "search_buses":
            slots["transport_type"] = "bus"
            slots["intent"] = "search_buses"
            slots["booking_mode"] = session.memory.booking_mode or "itinerary"
        elif tool_name == "search_local_transport":
            slots["intent"] = "local_transport"
            slots["local_transport_required"] = True
            slots["booking_mode"] = session.memory.booking_mode or "journey"
        elif tool_name == "search_hotels":
            slots["hotel_required"] = True
            slots["intent"] = "search_hotels"
            if not session.memory.booking_mode:
                slots["booking_mode"] = "itinerary"
        elif tool_name.startswith("search_packages") or tool_name == "create_package_plan":
            slots["intent"] = "packages"
            slots["booking_mode"] = "itinerary"
        elif tool_name in {"search_events", "get_event_details"}:
            slots["booking_mode"] = "event"
            slots["user_goal"] = "book_event"
            slots["intent"] = "book"
            if result.get("name"):
                slots["event_name"] = result["name"]
            event_id = result.get("id") or args.get("event_id")
            if event_id:
                slots["selected_event_id"] = str(event_id)
        elif tool_name == "request_charter":
            slots["booking_mode"] = "charter"
            slots["intent"] = "charter"
            slots["charter_type"] = args.get("charter_type")
            if args.get("event_type"):
                slots["trip_purpose"] = args["event_type"]
            if args.get("passengers"):
                slots["passenger_count"] = args["passengers"]
        elif tool_name == "escalate_to_sales":
            slots["intent"] = "support"
            if isinstance(result, dict) and result.get("ticket_id"):
                slots["support_ticket_id"] = result["ticket_id"]
        elif tool_name == "request_refund":
            slots["intent"] = "support"
        elif tool_name == "submit_feedback":
            slots["feedback_requested"] = True
        elif tool_name == "get_weather_alert":
            slots["weather_alert_sent"] = True
        elif tool_name == "create_booking":
            slots["selected_option"] = args.get("item_id")
            slots["booking_confirmation"] = bool(args.get("confirmed"))
            slots["intent"] = "booking"
            if args.get("passenger_name"):
                slots["passenger_name"] = args["passenger_name"]
            if args.get("contact_phone"):
                slots["contact_phone"] = args["contact_phone"]
            if args.get("contact_email"):
                slots["contact_email"] = args["contact_email"]
            if isinstance(result, dict) and result.get("booking_id"):
                slots["last_booking_id"] = result["booking_id"]
            if args.get("confirmed"):
                session.pending_confirmation = None
            else:
                session.pending_confirmation = {
                    "action": "create_booking",
                    "item_type": args.get("item_type"),
                    "item_id": args.get("item_id"),
                    "passenger_name": args.get("passenger_name"),
                }

        session.memory = update_memory_from_slots(session.memory, slots)
        if isinstance(result.get("results"), list):
            session.last_search_results = result["results"]
            if tool_name == "search_trains":
                session.last_offerings["trains"] = result["results"]
                session.itinerary_cache["trains"] = result["results"]
            elif tool_name == "search_flights":
                session.last_offerings["flights"] = result["results"]
                session.itinerary_cache["flights"] = result["results"]
            elif tool_name == "search_buses":
                session.last_offerings["buses"] = result["results"]
                session.itinerary_cache["buses"] = result["results"]
            elif tool_name == "search_local_transport":
                session.last_offerings["local"] = result["results"]
                session.itinerary_cache["local"] = result["results"]
            elif tool_name == "search_hotels":
                session.last_offerings["hotels"] = result["results"]
                session.itinerary_cache["hotels"] = result["results"]
            elif tool_name == "search_packages":
                session.last_offerings["packages"] = result["results"]
            elif tool_name == "search_events":
                session.last_offerings["events"] = result["results"]
        if tool_name == "get_event_details" and isinstance(result, dict) and not result.get(
            "error"
        ):
            event = result.get("event") if isinstance(result.get("event"), dict) else result
            session.last_offerings["event_details"] = event
            if isinstance(event, dict) and event.get("id"):
                session.memory.selected_event_id = str(event.get("id"))
                session.last_offerings["events"] = [event]
        if tool_name == "get_train_details" and isinstance(result, dict) and not result.get(
            "error"
        ):
            session.last_offerings["train_details"] = result
        if tool_name == "get_live_train_status" and isinstance(result, dict) and not result.get(
            "error"
        ):
            trains = result.get("trains")
            if isinstance(trains, list):
                session.last_offerings["live_status"] = trains
            else:
                session.last_offerings["live_status"] = [result]
        if tool_name in {"get_wishlist", "add_to_wishlist"} and isinstance(result, dict):
            session.wishlist = list(result.get("wishlist") or [])
            session.last_offerings["wishlist"] = session.wishlist
        if tool_name == "get_weather_alert" and isinstance(result, dict) and not result.get(
            "error"
        ):
            session.last_offerings["weather"] = result
        if tool_name == "request_charter" and isinstance(result, dict) and not result.get(
            "error"
        ):
            session.last_offerings["charter"] = result
        if tool_name in {"escalate_to_sales", "request_refund"} and isinstance(
            result, dict
        ) and not result.get("error"):
            session.last_offerings["support"] = result
        if tool_name == "submit_feedback" and isinstance(result, dict) and not result.get(
            "error"
        ):
            session.last_offerings["feedback"] = result
        if tool_name == "create_package_plan" and isinstance(result, dict):
            if not result.get("error"):
                session.last_offerings["plan"] = result
                session.itinerary_cache["plan"] = result
                session.last_search_results = [
                    {
                        "id": result.get("id") or "PLAN-001",
                        "name": result.get("summary") or "Package plan",
                        "price": result.get("estimated_total"),
                        "currency": result.get("currency", "INR"),
                        **{
                            k: result.get(k)
                            for k in (
                                "source",
                                "destination",
                                "departure_date",
                                "return_date",
                                "transport",
                                "hotel",
                                "day_plan",
                            )
                            if result.get(k) is not None
                        },
                    }
                ]

    @staticmethod
    def _wants_skip_bus(user_text: str) -> bool:
        text = user_text.lower()
        return any(
            p in text
            for p in (
                "skip bus",
                "no bus",
                "without bus",
                "don't need bus",
                "do not need bus",
                "skip the bus",
                "बस नहीं",
                "बस मत",
                "बस स्किप",
                "बिना बस",
            )
        )

    @staticmethod
    def _normalize_choice_text(user_text: str) -> str:
        """Normalize Hindi/English choice phrases for option matching."""
        import re
        import unicodedata

        text = unicodedata.normalize("NFKC", user_text or "").lower().strip()
        text = text.translate(str.maketrans("०१२३४५६७८९", "0123456789"))

        replacements = {
            "होटेल": "hotel",
            "होटल": "hotel",
            "ट्रेन": "train",
            "रेल": "train",
            "बस": "bus",
            "फ्लाइट": "flight",
            "उड़ान": "flight",
            "पैकेज": "package",
            "ऑप्शन": "option",
            "आपशन": "option",
            "विकल्प": "option",
            "नंबर": "number",
            "नम्बर": "number",
            "पहला": "1",
            "पहले": "1",
            "पहली": "1",
            "फर्स्ट": "1",
            "first": "1",
            "pehle": "1",
            "pehla": "1",
            "pehli": "1",
            "दूसरा": "2",
            "दूसरे": "2",
            "दूसरी": "2",
            "सेकंड": "2",
            "second": "2",
            "dusra": "2",
            "doosra": "2",
            "तीसरा": "3",
            "तीसरे": "3",
            "तीसरी": "3",
            "थर्ड": "3",
            "third": "3",
            "teesra": "3",
            "tisra": "3",
            "चौथा": "4",
            "fourth": "4",
            "पाँचवा": "5",
            "पांचवा": "5",
            "fifth": "5",
        }
        for src, dst in replacements.items():
            text = text.replace(src, f" {dst} ")
        text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _match_option(
        user_text: str, rows: list[dict[str, Any]]
    ) -> Optional[dict[str, Any]]:
        if not rows:
            return None
        import re

        text = ConversationAgent._normalize_choice_text(user_text)
        if not text:
            return None

        # "second person's name is wrong" must NOT become option 2.
        raw_l = (user_text or "").lower()
        if any(
            p in raw_l
            for p in (
                "name",
                "person",
                "passenger",
                "wrong",
                "correct",
                "change",
                "phone",
                "mobile",
                "number is",
            )
        ) and not re.search(
            r"\b(?:option|train|bus|hotel|flight|event)\s*\d+\b",
            raw_l,
        ):
            # Only allow explicit "option 2" / "train 2" style choices here.
            numbered = re.search(
                r"\b(?:option|train|bus|hotel|flight|package|event)\s*(?:number\s*)?(\d+)\b",
                text,
            )
            if numbered:
                idx = int(numbered.group(1)) - 1
                if 0 <= idx < len(rows):
                    return rows[idx]
            # Fall through to name/id matching only (no bare digit from "second")
            stop = {
                "the",
                "a",
                "an",
                "hotel",
                "train",
                "bus",
                "flight",
                "option",
                "number",
                "want",
                "please",
                "i",
                "and",
                "inn",
                "lodge",
                "stay",
                "express",
                "name",
                "person",
                "passenger",
                "wrong",
                "second",
                "first",
                "third",
                "no",
                "you",
                "is",
                "are",
            }
            tokens = [t for t in text.split() if t not in stop and len(t) > 2]
            for row in rows:
                name = str(row.get("name") or row.get("airline") or "").lower().strip()
                rid = str(row.get("id") or "").lower().strip()
                if name and name in text:
                    return row
                if rid and rid in text.replace(" ", ""):
                    return row
                name_tokens = [t for t in re.split(r"\W+", name) if len(t) > 2]
                if name_tokens and tokens and all(
                    any(nt in tok or tok in nt for tok in tokens) for nt in name_tokens[:2]
                ):
                    # Require at least one distinctive token (not only "express")
                    distinctive = [t for t in name_tokens if t not in stop]
                    if distinctive and any(d in text for d in distinctive):
                        return row
            return None

        numbered = re.search(
            r"(?:option|train|bus|hotel|flight|package|event|number|no|num)?\s*(\d+)",
            text,
        )
        if not numbered:
            numbered = re.search(r"(?<!\d)(\d+)(?!\d)", text)
        if numbered:
            idx = int(numbered.group(1)) - 1
            if 0 <= idx < len(rows):
                return rows[idx]

        stop = {
            "the",
            "a",
            "an",
            "hotel",
            "train",
            "bus",
            "flight",
            "option",
            "number",
            "want",
            "please",
            "i",
            "and",
            "inn",
            "lodge",
            "stay",
            "express",
        }
        for row in rows:
            name = str(row.get("name") or row.get("airline") or "").lower().strip()
            rid = str(row.get("id") or "").lower().strip()
            if name and name in text:
                return row
            if rid and rid in text:
                return row
            tokens = [
                t
                for t in re.findall(r"[a-z0-9]+", name)
                if len(t) > 2 and t not in stop
            ]
            if tokens:
                hits = sum(1 for t in tokens if t in text)
                need = 1 if len(tokens) == 1 else min(2, len(tokens))
                if hits >= need:
                    return row
                # Unique distinctive word, e.g. "comfort" → City Comfort Inn
                for token in tokens:
                    if len(token) >= 3 and token in text:
                        owners = [
                            r
                            for r in rows
                            if token in str(r.get("name") or r.get("airline") or "").lower()
                        ]
                        if len(owners) == 1:
                            return owners[0]
        return None

    async def _ensure_step_cached(
        self, session: SessionState, step: str, tools_used: list[str]
    ) -> None:
        mem = session.memory
        if step in session.itinerary_cache and session.itinerary_cache[step]:
            return
        if step == "trains":
            result = await self._tools.execute(
                "search_trains",
                {
                    "source": mem.source,
                    "destination": mem.destination,
                    "departure_date": mem.departure_date,
                    "passengers": mem.passenger_count or 1,
                },
                session_id=session.session_id,
            )
            tools_used.append("search_trains")
            self._update_memory_from_tool(session, "search_trains", {}, result)
        elif step == "buses":
            result = await self._tools.execute(
                "search_buses",
                {
                    "source": mem.source,
                    "destination": mem.destination,
                    "departure_date": mem.departure_date,
                    "passengers": mem.passenger_count or 1,
                },
                session_id=session.session_id,
            )
            tools_used.append("search_buses")
            self._update_memory_from_tool(session, "search_buses", {}, result)
        elif step == "hotels":
            result = await self._tools.execute(
                "search_hotels",
                {
                    "city": mem.destination,
                    "check_in": mem.departure_date,
                    "check_out": mem.return_date or mem.departure_date,
                    "guests": mem.passenger_count or 2,
                },
                session_id=session.session_id,
            )
            tools_used.append("search_hotels")
            self._update_memory_from_tool(session, "search_hotels", {}, result)
        elif step == "flights":
            hub = mem.via_city or mem.destination
            if mem.source and hub:
                result = await self._tools.execute(
                    "search_flights",
                    {
                        "source": mem.source,
                        "destination": hub,
                        "departure_date": mem.departure_date or "",
                        "passengers": mem.passenger_count or 1,
                    },
                    session_id=session.session_id,
                )
                tools_used.append("search_flights")
                self._update_memory_from_tool(session, "search_flights", {}, result)
        elif step == "local":
            city = mem.destination or mem.source or "City"
            train = session.itinerary_selections.get("train") or {}
            pickup = train.get("destination_station") or f"{city} Station"
            result = await self._tools.execute(
                "search_local_transport",
                {
                    "city": city,
                    "pickup": pickup,
                    "dropoff": f"{city} Hotel area",
                },
                session_id=session.session_id,
            )
            tools_used.append("search_local_transport")
            self._update_memory_from_tool(session, "search_local_transport", {}, result)

    @staticmethod
    def _item_price(item: Optional[dict]) -> float:
        if not item:
            return 0.0
        price = item.get("price") or item.get("total_price") or 0
        try:
            return float(price)
        except (TypeError, ValueError):
            return 0.0

    def _build_itinerary_summary(self, session: SessionState) -> dict[str, Any]:
        train = session.itinerary_selections.get("train") or {}
        bus = session.itinerary_selections.get("bus")
        hotel = session.itinerary_selections.get("hotel") or {}
        total = (
            self._item_price(train)
            + self._item_price(bus)
            + self._item_price(hotel)
        )
        train_name = train.get("name") or train.get("id") or "selected train"
        hotel_name = hotel.get("name") or hotel.get("id") or "selected hotel"
        bus_name = (bus or {}).get("name") or (bus or {}).get("id") if bus else None
        includes = [
            {
                "type": "train",
                "label": "Train",
                "name": train_name,
                "detail": " · ".join(
                    p
                    for p in [
                        f"{train.get('departure_time')} → {train.get('arrival_time')}"
                        if train.get("departure_time") and train.get("arrival_time")
                        else None,
                        train.get("class") or train.get("travel_class"),
                        f"INR {int(self._item_price(train))}"
                        if self._item_price(train)
                        else None,
                    ]
                    if p
                ),
            }
        ]
        if bus:
            includes.append(
                {
                    "type": "bus",
                    "label": "Bus",
                    "name": bus_name,
                    "detail": " · ".join(
                        p
                        for p in [
                            bus.get("operator"),
                            f"{bus.get('departure_time')} → {bus.get('arrival_time')}"
                            if bus.get("departure_time") and bus.get("arrival_time")
                            else None,
                            f"INR {int(self._item_price(bus))}"
                            if self._item_price(bus)
                            else None,
                        ]
                        if p
                    ),
                }
            )
        else:
            includes.append(
                {
                    "type": "bus",
                    "label": "Bus",
                    "name": "Skipped",
                    "detail": "No bus in this package",
                }
            )
        includes.append(
            {
                "type": "hotel",
                "label": "Hotel",
                "name": hotel_name,
                "detail": " · ".join(
                    p
                    for p in [
                        hotel.get("city"),
                        f"{hotel.get('rating')}★" if hotel.get("rating") is not None else None,
                        f"INR {int(self._item_price(hotel))}"
                        if self._item_price(hotel)
                        else None,
                    ]
                    if p
                ),
            }
        )
        day_plan = [
            f"Train: {train_name}"
            + (
                f" ({train.get('departure_time')} → {train.get('arrival_time')})"
                if train.get("departure_time") and train.get("arrival_time")
                else ""
            ),
            f"Bus: {bus_name}" if bus else "Bus: skipped",
            f"Hotel: {hotel_name}"
            + (
                f" until {session.memory.return_date}"
                if session.memory.return_date
                else ""
            ),
            f"Estimated total: INR {int(total)}" if total else "Estimated total: see screen",
        ]
        return {
            "id": "ITIN-BUILD-001",
            "summary": (
                f"Your package: {session.memory.source} → "
                f"{session.memory.destination}"
            ),
            "source": session.memory.source,
            "destination": session.memory.destination,
            "departure_date": session.memory.departure_date,
            "return_date": session.memory.return_date,
            "train": train,
            "bus": bus,
            "hotel": hotel,
            "includes": includes,
            "estimated_total": total,
            "currency": "INR",
            "day_plan": day_plan,
        }

    def _spoken_package_summary(self, session: SessionState, plan: dict[str, Any]) -> str:
        train = plan.get("train") or {}
        bus = plan.get("bus")
        hotel = plan.get("hotel") or {}
        train_name = train.get("name") or "your train"
        hotel_name = hotel.get("name") or "your hotel"
        bus_bit = (
            f", bus {bus.get('name')}"
            if isinstance(bus, dict) and bus.get("name")
            else ", no bus"
        )
        total = plan.get("estimated_total") or 0
        try:
            total_bit = f" Estimated total about INR {int(float(total))}."
        except (TypeError, ValueError):
            total_bit = ""
        dates = ""
        if plan.get("departure_date") and plan.get("return_date"):
            dates = f" from {plan['departure_date']} to {plan['return_date']}"
        elif plan.get("departure_date"):
            dates = f" on {plan['departure_date']}"
        return (
            f"You chose the package{dates}: train {train_name}{bus_bit}, "
            f"and hotel {hotel_name}.{total_bit} "
            "Full details are on your screen. "
            "Say yes to continue with passenger details."
        )

    _TRANSPORT_TAIL_RE = re.compile(
        r"\s+(?:via|by|through)\s+(?:a\s+)?(?:flights?|trains?|buses?|air|planes?|aeroplanes?)\b.*$",
        re.IGNORECASE,
    )

    @staticmethod
    def _clean_place_name(place: str) -> str:
        cleaned = ConversationAgent._TRANSPORT_TAIL_RE.sub("", place or "")
        cleaned = re.sub(
            r"\s+\b(?:flights?|trains?|buses?)\b.*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        return cleaned.strip(" .,")

    @staticmethod
    def _parse_route_from_text(user_text: str) -> tuple[Optional[str], Optional[str]]:
        text = " ".join((user_text or "").strip().split())
        patterns = [
            r"(?:route\s+is|from)\s+([A-Za-z][A-Za-z\s]+?)\s+(?:to|→|->)\s+([A-Za-z][A-Za-z\s]+?)(?:\s+(?:via|by|and|on|travel)\b|[.,]|$)",
            r"\b([A-Za-z][A-Za-z\s]{1,30}?)\s+(?:to|→|->)\s+([A-Za-z][A-Za-z\s]{1,30}?)(?:\s+(?:via|by|and|on|travel)\b|[.,]|$)",
        ]
        for pat in patterns:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if not m:
                continue
            src = ConversationAgent._clean_place_name(m.group(1))
            dst = ConversationAgent._clean_place_name(m.group(2))
            dst = re.sub(
                r"\b(and|travel|date|is|on|for|by)\b.*$",
                "",
                dst,
                flags=re.IGNORECASE,
            ).strip(" .,")
            src = ConversationAgent._clean_place_name(src)
            dst = ConversationAgent._clean_place_name(dst)
            if ConversationAgent._is_plausible_place(src) and ConversationAgent._is_plausible_place(dst):
                return src.title(), dst.title()
        return None, None

    _PLACE_NOISE = {
        "i", "we", "you", "me", "my", "a", "an", "the", "to", "from", "and",
        "want", "like", "need", "going", "go", "book", "booking", "please",
        "would", "could", "should", "take", "get", "make", "do", "flight",
        "flights", "train", "trains", "hotel", "hotels", "ticket", "tickets",
    }

    @staticmethod
    def _is_plausible_place(name: str) -> bool:
        tokens = [t for t in (name or "").lower().split() if t]
        if not tokens or len(" ".join(tokens)) < 2:
            return False
        if tokens[0] in ConversationAgent._PLACE_NOISE:
            return False
        if all(t in ConversationAgent._PLACE_NOISE for t in tokens):
            return False
        return True

    @staticmethod
    def _parse_date_from_text(user_text: str) -> Optional[str]:
        """Return ISO date YYYY-MM-DD when possible."""
        from datetime import datetime, timedelta

        text = (user_text or "").lower()
        today = datetime.now().astimezone().date()
        if "day after tomorrow" in text:
            return (today + timedelta(days=2)).isoformat()
        if "tomorrow" in text:
            return (today + timedelta(days=1)).isoformat()
        if re.search(r"\btoday\b", text):
            return today.isoformat()

        iso = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", text)
        if iso:
            y, m, d = int(iso.group(1)), int(iso.group(2)), int(iso.group(3))
            try:
                return datetime(y, m, d).date().isoformat()
            except ValueError:
                pass

        months = {
            "january": 1,
            "jan": 1,
            "february": 2,
            "feb": 2,
            "march": 3,
            "mar": 3,
            "april": 4,
            "apr": 4,
            "may": 5,
            "june": 6,
            "jun": 6,
            "july": 7,
            "jul": 7,
            "august": 8,
            "aug": 8,
            "september": 9,
            "sep": 9,
            "sept": 9,
            "october": 10,
            "oct": 10,
            "november": 11,
            "nov": 11,
            "december": 12,
            "dec": 12,
        }
        # 6th September / September 6 / 6 September 2026
        m = re.search(
            r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
            r"(january|jan|february|feb|march|mar|april|apr|may|june|jun|"
            r"july|jul|august|aug|september|sep|sept|october|oct|november|nov|december|dec)"
            r"(?:\s+(\d{4}))?\b",
            text,
        )
        if not m:
            m = re.search(
                r"\b(january|jan|february|feb|march|mar|april|apr|may|june|jun|"
                r"july|jul|august|aug|september|sep|sept|october|oct|november|nov|december|dec)"
                r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?\b",
                text,
            )
            if m:
                month = months[m.group(1)]
                day = int(m.group(2))
                year = int(m.group(3)) if m.group(3) else today.year
                if not m.group(3) and (month, day) < (today.month, today.day):
                    year += 1
                try:
                    return datetime(year, month, day).date().isoformat()
                except ValueError:
                    return None
        else:
            day = int(m.group(1))
            month = months[m.group(2)]
            year = int(m.group(3)) if m.group(3) else today.year
            if not m.group(3) and (month, day) < (today.month, today.day):
                year += 1
            try:
                return datetime(year, month, day).date().isoformat()
            except ValueError:
                return None
        return None

    @staticmethod
    def _parse_passenger_count(user_text: str) -> Optional[int]:
        """Parse '5 passengers', 'five passengers', 'we are 2', 'One.', etc."""
        text = (user_text or "").strip().lower()
        if not text:
            return None
        # Voice/STT often adds punctuation: "One." / "two!" / "3,"
        text = re.sub(r"[^\w\s]", " ", text)
        text = " ".join(text.split())
        if not text:
            return None
        words = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
            "eleven": 11,
            "twelve": 12,
            "fifteen": 15,
            "twenty": 20,
            "fifty": 50,
            "hundred": 100,
            "ek": 1,
            "do": 2,
            "teen": 3,
            "char": 4,
            "paanch": 5,
            "single": 1,
            "alone": 1,
            "just me": 1,
            "only me": 1,
        }
        # Multi-word phrases first.
        for phrase in ("just me", "only me"):
            if phrase in text:
                return 1
        # Normalize word numbers before digit regex.
        for word, num in words.items():
            if " " in word:
                continue
            text = re.sub(rf"\b{re.escape(word)}\b", str(num), text)

        patterns = (
            r"\b(\d{1,4})\s*(?:passengers?|people|persons?|pax|members?|travelers?|travellers?)\b",
            r"\b(?:passengers?|people|persons?|pax|members?)\s*(?:are\s*|is\s*|:\s*)?(\d{1,4})\b",
            r"\b(?:group of|party of|team of|we are|there are|total|only)\s*(\d{1,4})\b",
            r"^\s*(\d{1,4})\s*$",
        )
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                try:
                    count = int(m.group(1))
                except ValueError:
                    continue
                if 1 <= count <= 1200:
                    return count
        return None

    @staticmethod
    def _is_greeting(user_text: str) -> bool:
        text = (user_text or "").strip().lower()
        return bool(
            re.match(
                r"^(hey|hi|hello|yo)?\s*(sabrah|sabraah)?[!?.]*$",
                text,
            )
            or text in {"hey sabrah", "hi sabrah", "hello sabrah", "hey", "hi", "hello"}
        )

    @staticmethod
    def _extract_pnr(user_text: str) -> Optional[str]:
        text = (user_text or "").upper()
        m = re.search(r"\b(BK-[A-Z0-9]{6,})\b", text)
        if m:
            return m.group(1)
        m = re.search(r"\bPNR[:\s#-]*([A-Z0-9]{6,})\b", text)
        if m:
            return m.group(1)
        # bare token that looks like booking id
        m = re.search(r"\b([A-Z]{2,3}-?[A-Z0-9]{6,})\b", text)
        if m and "TRAIN" not in m.group(1):
            return m.group(1)
        return None

    def _show_main_menu(self, session: SessionState) -> str:
        # Conversational only — no leftover event/train cards on screen.
        session.last_offerings.clear()
        session.memory.flow_step = "goal"
        return MAIN_MENU_LINE

    def _show_purpose_menu(self, session: SessionState) -> str:
        session.last_offerings.pop("purposes", None)
        session.last_offerings.pop("goals", None)
        session.memory.flow_step = "why"
        route = ""
        if session.memory.source and session.memory.destination:
            route = f" from {session.memory.source} to {session.memory.destination}"
        return f"Got the route{route}. {PURPOSE_ASK_LINE}"

    @staticmethod
    def _is_event_intent(text: str) -> bool:
        lowered = (text or "").lower()
        return bool(
            re.search(
                r"\b(events|concerts?|festivals?|comedy(?:\s+shows?)?|"
                r"book (?:an |a )?(?:show|event)|live show|"
                r"what(?:'s| is| are)?(?: the)? events|"
                r"any events)\b",
                lowered,
            )
        )

    @staticmethod
    def _is_flight_intent(text: str) -> bool:
        lowered = (text or "").lower()
        return bool(
            re.search(
                r"\b(flights?|plane|airfare|air ticket|by air|via flight|fly(?:ing)?)\b",
                lowered,
            )
        )

    @staticmethod
    def _is_train_intent(text: str) -> bool:
        lowered = (text or "").lower()
        return bool(re.search(r"\b(trains?|irctc|railway|rail)\b", lowered))

    @staticmethod
    def _is_hotel_intent(text: str) -> bool:
        lowered = (text or "").lower()
        return bool(
            re.search(
                r"\b(hotels?|stay|resort|villa|need a room|book (?:a )?stay)\b",
                lowered,
            )
        )

    @staticmethod
    def _later_module_reply(user_text: str) -> Optional[str]:
        text = (user_text or "").lower()
        mapping = (
            (r"\bcruises?\b", "Cruises"),
            (r"\bvisas?\b", "Visa"),
            (r"\binsurance\b", "Travel insurance"),
            (r"\b(car rental|self drive|chauffeur)\b", "Car rental"),
            (r"\bbuses?\b", "Buses"),
        )
        for pattern, label in mapping:
            if re.search(pattern, text):
                return (
                    f"{label} is not live in this assistant yet. "
                    "I can book flights, trains, hotels, and events now — "
                    "tell me the cities, dates, and who is travelling."
                )
        return None

    _WORD_NUMBERS = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12, "single": 1,
    }

    @staticmethod
    def _words_to_nums(text: str) -> str:
        out = (text or "").lower()
        for word, num in ConversationAgent._WORD_NUMBERS.items():
            out = re.sub(rf"\b{re.escape(word)}\b", str(num), out)
        return out

    @staticmethod
    def _ingest_trip_signals(session: SessionState, user_text: str) -> None:
        """Fill WHO / WHERE / WHEN / WHY / HOW / VALUE from natural language. Never overwrite blindly."""
        mem = session.memory
        text = (user_text or "").strip().lower()
        if not text:
            return

        src, dst = ConversationAgent._parse_route_from_text(user_text)
        if src and dst:
            mem.source = src
            mem.destination = dst
        date = ConversationAgent._parse_date_from_text(user_text)
        if date:
            mem.departure_date = date

        if re.search(r"\b(round trip|return ticket|and back)\b", text):
            mem.trip_type = "round_trip"
        elif re.search(r"\bmulti[-\s]?city\b", text):
            mem.trip_type = "multi_city"
        elif re.search(r"\bone[-\s]?way\b", text):
            mem.trip_type = "one_way"

        if re.search(r"\b(flexible|around that date|plus or minus|nearby dates)\b", text):
            mem.date_flexible = True

        nights = re.search(r"\b(\d{1,2})\s*(?:nights?|days?)\b", ConversationAgent._words_to_nums(text))
        if nights:
            mem.trip_nights = int(nights.group(1))

        if re.search(r"\b(direct|non[-\s]?stop|no (?:layover|connection))\b", text):
            mem.direct_only = True
            mem.value_priority = mem.value_priority or "convenience"

        if re.search(r"\b(4\s*a\.?m|too early|not early|don'?t want to wake|early morning)\b", text):
            mem.avoid_early_departure = True

        if re.search(r"\b(wheelchair|accessible|accessibility|special assistance|elderly)\b", text):
            mem.accessibility_needed = True

        if re.search(r"\b(relaxed|not (?:a )?crazy itinerary|slow pace|with kids so)\b", text):
            mem.travel_pace = "relaxed"
        elif re.search(r"\b(packed|maximise|maximize|see everything)\b", text):
            mem.travel_pace = "packed"

        if re.search(r"\b(premium economy)\b", text):
            mem.travel_class = "premium_economy"
        elif re.search(r"\b(business class|in business)\b", text):
            mem.travel_class = "business"
        elif re.search(r"\bfirst class\b", text):
            mem.travel_class = "first"
        elif re.search(r"\beconomy\b", text):
            mem.travel_class = "economy"

        if re.search(r"\b(cheap(?:est)? but not uncomfortable|best value|balance|balanced)\b", text):
            mem.value_priority = "balanced"
            mem.train_preference = "balanced"
        elif re.search(r"\b(cheapest|lowest price|budget|save money|inexpensive)\b", text):
            mem.value_priority = "price"
            mem.train_preference = "cheapest"
        elif re.search(r"\b(fastest|shortest|save time|quickest)\b", text):
            mem.value_priority = "time"
            mem.train_preference = "fastest"
        elif re.search(r"\b(luxury|5\s*star|first class|premium)\b", text):
            mem.value_priority = "luxury"
        elif re.search(r"\b(comfort|comfortable|extra legroom|not uncomfortable)\b", text):
            mem.value_priority = "comfort"
            mem.train_preference = mem.train_preference or "ac"
        elif re.search(r"\b(convenient|hassle[-\s]?free)\b", text):
            mem.value_priority = "convenience"
        elif re.search(r"\b(experience|sightseeing|memorable)\b", text):
            mem.value_priority = "experience"

        budget = re.search(
            r"(?:₹|rs\.?|inr|budget(?:\s+of)?|under|around)\s*(\d+(?:\.\d+)?)\s*(lakh|lakhs|k|thousand)?",
            text,
        )
        if budget:
            amount = float(budget.group(1))
            unit = (budget.group(2) or "").lower()
            if unit in {"lakh", "lakhs"}:
                amount *= 100000
            elif unit in {"k", "thousand"}:
                amount *= 1000
            mem.budget = amount

        adults, children, infants, total = ConversationAgent._parse_party_counts(user_text)
        if adults is not None:
            mem.adult_count = adults
        if children is not None:
            mem.child_count = children
        if infants is not None:
            mem.infant_count = infants
        if total:
            mem.passenger_count = total
        elif not mem.passenger_count:
            parsed = ConversationAgent._parse_passenger_count(user_text)
            if parsed:
                mem.passenger_count = parsed
                mem.adult_count = mem.adult_count or parsed

        if re.search(r"\b(just me|only me|solo|myself|alone)\b", text):
            mem.party_type = "solo"
            mem.passenger_count = mem.passenger_count or 1
            mem.adult_count = mem.adult_count or 1
        elif re.search(r"\b(honeymoon|with my (?:wife|husband|partner)|couple|two of us)\b", text):
            mem.party_type = "couple"
            mem.passenger_count = mem.passenger_count or 2
            mem.adult_count = mem.adult_count or 2
        elif re.search(r"\b(family|kids?|children|with my (?:son|daughter))\b", text):
            mem.party_type = "family"
            mem.travel_pace = mem.travel_pace or "relaxed"
        elif re.search(r"\b(group|friends|colleagues)\b", text):
            mem.party_type = "group"
        elif re.search(r"\bbusiness (?:trip|travel)\b", text):
            mem.party_type = "business"

        if ConversationAgent._is_hotel_intent(text):
            mem.hotel_required = True

        for purpose, keys in (
            ("honeymoon", ("honeymoon",)),
            ("wedding", ("wedding",)),
            ("baraat", ("baraat",)),
            ("family", ("family trip", "family holiday", "with family")),
            ("business", ("business", "office work", "work trip", "for work")),
            ("conference", ("conference", "meeting", "seminar")),
            ("adventure", ("adventure", "trek", "hiking")),
            ("religious", ("religious", "pilgrimage", "temple trip", "umrah", "hajj")),
            ("medical", ("medical", "treatment", "hospital")),
            ("shopping", ("shopping trip", "shopping")),
            ("weekend", ("weekend getaway", "weekend trip")),
            ("leisure", ("leisure", "holiday", "vacation", "tourism")),
            ("other", ("something else",)),
        ):
            if any(k in text for k in keys):
                mem.trip_purpose = purpose
                break
        if re.fullmatch(r"other[.!]?", text):
            mem.trip_purpose = "other"

    @staticmethod
    def _parse_party_counts(
        user_text: str,
    ) -> tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
        text = ConversationAgent._words_to_nums(user_text or "")
        text = re.sub(r"[^\w\s]", " ", text)
        adults = children = infants = None
        m = re.search(r"\b(\d+)\s*adults?\b", text)
        if m:
            adults = int(m.group(1))
        m = re.search(r"\b(\d+)\s*(?:child|children|kids?)\b", text)
        if m:
            children = int(m.group(1))
        m = re.search(r"\b(\d+)\s*infants?\b", text)
        if m:
            infants = int(m.group(1))
        total = None
        if adults is not None or children is not None or infants is not None:
            total = (adults or 0) + (children or 0) + (infants or 0)
            if total <= 0:
                total = None
            if adults is None and total:
                adults = max(1, total - (children or 0) - (infants or 0))
        return adults, children, infants, total

    @staticmethod
    def _duration_minutes(raw: Any) -> Optional[int]:
        text = str(raw or "")
        hours = re.search(r"(\d+)\s*h", text, re.I)
        mins = re.search(r"(\d+)\s*m", text, re.I)
        if not hours and not mins:
            try:
                return int(float(text))
            except (TypeError, ValueError):
                return None
        return int(hours.group(1) if hours else 0) * 60 + int(mins.group(1) if mins else 0)

    @staticmethod
    def _item_price_value(item: dict[str, Any]) -> float:
        raw = item.get("price") or item.get("total_price") or item.get("gross_fare") or 0
        try:
            return float(re.sub(r"[^\d.]", "", str(raw)) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _recommend_option(
        self, rows: list[dict[str, Any]], mem: Any
    ) -> tuple[int, dict[str, Any], str]:
        scored: list[tuple[int, dict[str, Any]]] = []
        for index, row in enumerate(rows):
            if isinstance(row, dict):
                scored.append((index, row))
        if not scored:
            return 1, {}, "balanced"
        priority = mem.value_priority or "balanced"

        def duration(row: dict[str, Any]) -> int:
            return self._duration_minutes(row.get("duration")) or 10_000

        def stops(row: dict[str, Any]) -> int:
            try:
                return int(row.get("stops") or 0)
            except (TypeError, ValueError):
                return 0

        if priority == "price":
            index, row = min(scored, key=lambda item: self._item_price_value(item[1]))
        elif priority == "time":
            index, row = min(scored, key=lambda item: duration(item[1]))
        elif priority in {"convenience", "comfort"}:
            index, row = min(scored, key=lambda item: (stops(item[1]), duration(item[1])))
        elif priority == "luxury":
            index, row = max(scored, key=lambda item: self._item_price_value(item[1]))
        else:
            cheapest = min(scored, key=lambda item: self._item_price_value(item[1]))
            fastest = min(scored, key=lambda item: duration(item[1]))
            index, row = fastest if fastest[0] != cheapest[0] else cheapest
            if len(scored) >= 2 and fastest[0] == cheapest[0]:
                index, row = scored[min(1, len(scored) - 1)]
        return index + 1, row, priority

    def _speak_options(
        self, session: SessionState, rows: list[dict[str, Any]], kind: str
    ) -> str:
        mem = session.memory
        option_n, picked, priority = self._recommend_option(rows, mem)
        label = picked.get("name") or picked.get("airline") or f"Option {option_n}"
        price = picked.get("price_label") or ""
        why = {
            "price": "lowest fare",
            "time": "shortest travel time",
            "comfort": "fewer stops and a more comfortable journey",
            "convenience": "more convenient routing",
            "luxury": "a more premium option",
            "experience": "a better overall experience",
            "balanced": "a balance of price and time",
        }.get(priority, "a solid overall fit")
        extra = f" {price}" if price else ""
        route = f"from {mem.source} to {mem.destination}" if kind != "hotels" else f"in {mem.destination or mem.source}"
        return (
            f"{kind.title()} {route} are on your screen. "
            f"I recommend Option {option_n} ({label}{extra}) for {why}. "
            "Tell me which option you want."
        )

    _EVENT_SEARCH_STOP = {
        "a", "about", "all", "an", "and", "any", "anything", "are", "around",
        "at", "available", "be", "book", "can", "coming", "comedy", "concert",
        "concerts", "could", "currently", "do", "event", "events", "everything",
        "festival", "festivals", "for", "from", "get", "give", "going", "got",
        "have", "happening", "hello", "here", "hey", "hi", "how", "i", "im",
        "in", "is", "just", "kind", "kinda", "know", "let", "like", "list",
        "live", "looking", "me", "much", "many", "my", "near", "nearby", "need",
        "next", "now", "number", "of", "ok", "okay", "on", "open", "option",
        "or", "please", "running", "sabraah", "sabrah", "see", "should", "show",
        "shows", "some", "something", "stuff", "tell", "thank", "thanks", "the",
        "there", "these", "they", "thing", "things", "this", "those", "ticket",
        "tickets", "to", "today", "tonight", "up", "us", "want", "wanna",
        "was", "we", "weekend", "were", "what", "whats", "when", "where",
        "which", "who", "whom", "whose", "why", "with", "would", "you",
        "your", "youre",
    }

    @staticmethod
    def _event_search_args(user_text: str) -> dict[str, Any]:
        text = (user_text or "").lower()
        if re.search(r"https?://|www\.|\.(?:com|org|net|in)\b", text):
            return {}
        cities = (
            "delhi",
            "mumbai",
            "bangalore",
            "bengaluru",
            "pune",
            "hyderabad",
            "chennai",
            "kolkata",
            "jaipur",
            "goa",
            "ahmedabad",
            "kochi",
            "lucknow",
            "noida",
            "gurgaon",
            "gurugram",
        )
        city = next((name for name in cities if name in text), None)
        leftover = [
            tok
            for tok in re.findall(r"[a-z0-9]+", text)
            if tok not in ConversationAgent._EVENT_SEARCH_STOP
            and tok != (city or "")
        ]
        args: dict[str, Any] = {}
        if city:
            args["city"] = "Bengaluru" if city == "bengaluru" else city.title()
        if leftover and len(leftover) <= 5:
            args["search"] = " ".join(leftover)
        return args

    def _event_choice_speech(self, event: dict[str, Any]) -> str:
        name = event.get("name") or "This event"
        venue = str(event.get("venue_name") or "").strip()
        when = self._speak_event_when(event)
        tickets = event.get("tickets") if isinstance(event.get("tickets"), list) else []
        ticket_bits: list[str] = []
        for ticket in tickets[:4]:
            if not isinstance(ticket, dict):
                continue
            label = str(ticket.get("name") or "Ticket").strip()
            price = self._speak_rupees(ticket.get("price"))
            ticket_bits.append(f"{label} {price}".strip() if price else label)
        if not ticket_bits:
            start = self._speak_rupees(event.get("start_price"))
            if start:
                ticket_bits.append(f"from {start}")
        place = f" at {venue}" if venue else ""
        time_bit = f" on {when}" if when else ""
        tickets_bit = f" Tickets: {', '.join(ticket_bits)}." if ticket_bits else ""
        return (
            f"{name}{place}{time_bit}.{tickets_bit} "
            "Say yes to book, then the guest names."
        )

    @staticmethod
    def _speak_rupees(value: Any) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return ""
        return f"{int(round(number))} rupees"

    @staticmethod
    def _speak_event_when(event: dict[str, Any]) -> str:
        from datetime import datetime

        raw = str(event.get("start_date_time") or "").strip()
        if not raw:
            schedules = event.get("schedules") if isinstance(event.get("schedules"), list) else []
            first = schedules[0] if schedules and isinstance(schedules[0], dict) else {}
            date_part = str(first.get("event_date") or "").strip()
            time_part = str(first.get("start_time") or "").strip()
            raw = f"{date_part} {time_part}".strip()
        if not raw:
            return ""
        stamp = raw.replace("T", " ")
        parsed = None
        used_time = False
        for fmt, width in (
            ("%Y-%m-%d %H:%M:%S", 19),
            ("%Y-%m-%d %H:%M", 16),
            ("%Y-%m-%d", 10),
        ):
            try:
                parsed = datetime.strptime(stamp[:width], fmt)
                used_time = width > 10
                break
            except ValueError:
                continue
        if parsed is None:
            return raw
        date_label = parsed.strftime("%d %B")
        if not used_time:
            return date_label
        hour = parsed.hour
        period = "AM" if hour < 12 else "PM"
        display = ((hour + 11) % 12) + 1
        return f"{date_label} at {display}:{parsed.strftime('%M')} {period}"

    def _speak_event_names(self, rows: list[dict[str, Any]]) -> str:
        lines = []
        for index, row in enumerate(rows, 1):
            name = str(row.get("name") or "Event").strip()
            lines.append(f"Option {index}, {name}")
        spoken = ". ".join(lines)
        return (
            f"Here are the events. {spoken}. "
            "If you want to know about one, say tell me about, then the event name."
        )

    @staticmethod
    def _wants_event_info(user_text: str) -> bool:
        text = (user_text or "").lower()
        if any(
            skip in text
            for skip in ("yourself", "your name", "who you", "who are you")
        ):
            return False
        return any(
            hint in text
            for hint in (
                "about",
                "detail",
                "tell me",
                "batao",
                "bataao",
                "bare",
                "baare",
                "baaray",
                "info",
                "information",
                "बताओ",
                "बारे",
            )
        )

    def _is_event_followup(self, user_text: str, events: list[dict[str, Any]]) -> bool:
        text = (user_text or "").strip().lower()
        if self._is_event_intent(text):
            return True
        args = self._event_search_args(user_text)
        if args.get("city") or args.get("search"):
            return True
        if self._user_said_yes(user_text) or self._user_said_no(user_text):
            return True
        if self._match_event_mention(user_text, events) is not None:
            return True
        if re.search(r"\b(?:option|event|number|no)\s*\d+\b", text):
            return True
        if events and self._wants_event_info(user_text):
            return True
        return False

    @staticmethod
    def _match_event_mention(
        user_text: str, rows: list[dict[str, Any]]
    ) -> Optional[dict[str, Any]]:
        if not rows:
            return None
        text = (user_text or "").lower()
        best = None
        best_len = 0
        for row in rows:
            name = str(row.get("name") or "").strip().lower()
            if len(name) >= 3 and name in text and len(name) > best_len:
                best = row
                best_len = len(name)
        if best is not None:
            return best

        stop = {
            "the", "a", "an", "and", "please", "want", "tell", "me", "about",
            "event", "events", "option", "number", "this", "that", "is", "ke",
            "ka", "ki", "ko", "mein", "me", "mai", "bare", "baare", "baaray",
            "batao", "bataao", "detail", "details", "info", "information",
            "book", "booking", "for", "of", "to", "know", "janna", "janana",
        }
        tokens = [
            tok
            for tok in re.findall(r"[a-z0-9]+", text)
            if tok not in stop and len(tok) > 2
        ]
        scored: list[tuple[int, int, dict[str, Any]]] = []
        for row in rows:
            name = str(row.get("name") or "").lower()
            name_tokens = [
                tok
                for tok in re.findall(r"[a-z0-9]+", name)
                if len(tok) > 2 and tok not in stop
            ]
            if not name_tokens or not tokens:
                continue
            hits = sum(
                1
                for nt in name_tokens
                if any(nt in tok or tok in nt for tok in tokens)
            )
            needed = 1 if len(name_tokens) == 1 else min(2, len(name_tokens))
            if hits >= needed:
                scored.append((hits, len(name), row))
        if scored:
            scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return scored[0][2]

        numbered = re.search(
            r"\b(?:option|event|number|no)\s*(?:number\s*)?(\d+)\b",
            ConversationAgent._normalize_choice_text(user_text),
        )
        if numbered:
            idx = int(numbered.group(1)) - 1
            if 0 <= idx < len(rows):
                return rows[idx]
        return ConversationAgent._match_option(user_text, rows)

    @staticmethod
    def _wants_event_book(user_text: str) -> bool:
        text = (user_text or "").strip().lower()
        return bool(
            re.search(
                r"\b(book(?:\s+it|\s+this|\s+now|\s+for me)?|yes|yeah|yep|yup|sure)\b",
                text,
            )
        ) or ConversationAgent._user_said_yes(user_text)

    _EVENT_GUEST_SKIP = {
        "a", "an", "and", "ask", "at", "book", "booking", "can", "collect",
        "dot", "eight", "event", "events", "female", "first", "five", "for",
        "four", "fourth", "gmail", "guest", "guests", "guy", "guys", "in",
        "is", "it", "male", "me", "music", "my", "name", "names", "nine",
        "of", "one", "other", "people", "person", "please", "second", "seven",
        "six", "such", "take", "ten", "the", "this", "that", "third", "three",
        "two", "uh", "um", "user", "users", "want", "yeah", "yes", "you",
    }

    @staticmethod
    def _wants_event_correction(user_text: str) -> bool:
        text = (user_text or "").lower()
        return bool(
            re.search(
                r"\b(wrong|incorrect|mistake|change|correct|fix|update|not (?:right|correct))\b",
                text,
            )
        )

    @staticmethod
    def _split_full_name(name: str) -> tuple[str, str]:
        parts = [part for part in str(name or "").split() if part]
        if not parts:
            return "", ""
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], " ".join(parts[1:])

    @staticmethod
    def _parse_event_gender(user_text: str) -> Optional[str]:
        text = (user_text or "").lower()
        if re.search(r"\b(female|woman|women|girl|lady|ladies)\b", text):
            return "female"
        if re.search(r"\b(male|man|men|boy|gentleman)\b", text):
            return "male"
        if re.search(r"\b(other|non[- ]?binary)\b", text):
            return "other"
        return None

    @staticmethod
    def _parse_event_phone(user_text: str) -> Optional[str]:
        stripped = re.sub(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", " ", user_text or "")
        stripped = re.sub(r"\b(?:19|20)\d{2}\b", " ", stripped)
        match = re.search(r"(?:\+91[\s-]*)?(\d{10})\b", stripped)
        return match.group(1) if match else None

    @staticmethod
    def _parse_event_dob(user_text: str) -> Optional[str]:
        from datetime import datetime

        text = (user_text or "").strip().lower()
        today = datetime.now().date()
        slash = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", text)
        if slash:
            first, second, year = int(slash.group(1)), int(slash.group(2)), int(slash.group(3))
            day, month = (second, first) if first > 12 else (first, second)
            try:
                stamp = datetime(year, month, day).date()
            except ValueError:
                stamp = None
            if stamp and stamp < today:
                return stamp.isoformat()
        parsed = ConversationAgent._parse_date_from_text(user_text)
        if not parsed:
            return None
        try:
            stamp = datetime.strptime(parsed, "%Y-%m-%d").date()
        except ValueError:
            return None
        if stamp >= today:
            return None
        return parsed

    @staticmethod
    def _parse_event_guest_names(user_text: str) -> list[str]:
        text = user_text or ""
        lowered = text.lower()
        asking_only = re.search(
            r"\b(ask|collect|take|need|want)\b.*\b(name|names)\b",
            lowered,
        ) and not re.search(
            r"\b(?:my name is|names? are|guest(?:s)?\s+(?:is|are)|i am|first one is|second one is)\b",
            lowered,
        )
        if asking_only:
            return []
        cleaned = re.sub(
            r"\b(?:first|second|third|fourth|1st|2nd|3rd|4th)\s*(?:one|guest|person|guy)?\s*(?:is|:)\s*",
            ", ",
            text,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b(?:two|three|four|five|six)?\s*(?:guys|guests|people|persons)\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        names: list[str] = []
        for name in ConversationAgent._parse_passenger_names(cleaned):
            parts = [
                part
                for part in name.split()
                if part.lower() not in ConversationAgent._EVENT_GUEST_SKIP
            ]
            if len(parts) < 2 or len(parts) > 4:
                continue
            names.append(" ".join(parts))
        return names

    @staticmethod
    def _guest_missing_fields(guest: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        first, last = ConversationAgent._split_full_name(str(guest.get("name") or ""))
        first = str(guest.get("first_name") or first)
        last = str(guest.get("last_name") or last)
        if not first or not last:
            missing.append("full name")
        if not guest.get("email"):
            missing.append("email")
        if not guest.get("phone"):
            missing.append("10-digit phone")
        if not guest.get("gender"):
            missing.append("gender")
        if not guest.get("dob"):
            missing.append("date of birth")
        return missing

    def _apply_event_guest_details(self, guest: dict[str, Any], user_text: str) -> None:
        first, last = self._split_full_name(str(guest.get("name") or ""))
        guest["first_name"] = guest.get("first_name") or first
        guest["last_name"] = guest.get("last_name") or last
        email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", user_text or "")
        if email:
            guest["email"] = email.group(0)
        phone = self._parse_event_phone(user_text)
        if phone:
            guest["phone"] = phone
        gender = self._parse_event_gender(user_text)
        if gender:
            guest["gender"] = gender
        dob = self._parse_event_dob(user_text)
        if dob:
            guest["dob"] = dob

    def _ask_event_guest_details(self, guest: dict[str, Any], index: int) -> str:
        missing = self._guest_missing_fields(guest)
        name = str(guest.get("name") or f"guest {index + 1}")
        if missing == ["full name"] or "full name" in missing and len(missing) == 1:
            return f"Please say the first and last name for guest {index + 1}."
        return (
            f"For {name}, please say {', '.join(missing)}. "
            "For example: name@gmail.com, 9876543210, male, 12 January 1990."
        )

    def _collect_event_guests(self, session: SessionState, user_text: str) -> str:
        mem = session.memory
        text = (user_text or "").strip().lower()
        count = self._parse_passenger_count(user_text)
        if count:
            mem.passenger_count = max(1, min(int(count), 10))

        have = len([item for item in session.travelers if item.get("name")])
        need = mem.passenger_count or 0
        collecting_names = have == 0 or (need and have < need)
        if collecting_names or self._wants_event_correction(user_text):
            parsed_names = self._parse_event_guest_names(user_text)
            if collecting_names:
                for name in parsed_names:
                    if name.lower() in {
                        str(item.get("name") or "").lower() for item in session.travelers
                    }:
                        continue
                    first, last = self._split_full_name(name)
                    session.travelers.append(
                        {"name": name, "first_name": first, "last_name": last}
                    )
            elif parsed_names:
                target = session.travelers[0] if session.travelers else None
                spoken = user_text.lower()
                for item in session.travelers:
                    item_name = str(item.get("name") or "").lower()
                    if item_name and item_name.split()[0] in spoken:
                        target = item
                        break
                if target is not None:
                    name = parsed_names[0]
                    first, last = self._split_full_name(name)
                    target["name"] = name
                    target["first_name"] = first
                    target["last_name"] = last

        have = len([item for item in session.travelers if item.get("name")])
        need = mem.passenger_count or have
        skip_names = bool(re.search(r"\b(skip|later|open(?: the)?(?: page| booking)?)\b", text))
        if have == 0 and skip_names:
            mem.flow_step = "event_open"
            return "Opening the booking page. You can fill guest details there."
        if have == 0:
            mem.flow_step = "event_guests"
            return (
                "How many guests, and their full names? "
                "For example: two guests, Rahul Sharma and Priya Verma."
            )
        if need and have < need:
            mem.flow_step = "event_guests"
            return f"Got {have} of {need}. Please say the next guest's full name."

        mem.passenger_count = have
        current = None
        current_index = 0
        for index, guest in enumerate(session.travelers):
            if self._guest_missing_fields(guest):
                current = guest
                current_index = index
                break
        if current is not None:
            self._apply_event_guest_details(current, user_text)
            if self._guest_missing_fields(current):
                mem.flow_step = "event_guests"
                return self._ask_event_guest_details(current, current_index)
            nxt = None
            nxt_index = current_index
            for index, guest in enumerate(session.travelers[current_index + 1 :], start=current_index + 1):
                if self._guest_missing_fields(guest):
                    nxt = guest
                    nxt_index = index
                    break
            if nxt is not None:
                mem.flow_step = "event_guests"
                return self._ask_event_guest_details(nxt, nxt_index)

        mem.flow_step = "event_open"
        named = ", ".join(
            str(item.get("name")) for item in session.travelers if item.get("name")
        )
        return (
            f"Opening booking for {named}. "
            "Guest details are filled. Click Book or Pay on that page."
        )

    @staticmethod
    def _with_query(url: str, extra: dict[str, Any]) -> str:
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        for key, value in extra.items():
            if value in (None, "", []):
                continue
            query[key] = str(value)
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )

    @staticmethod
    def _selected_event_booking_url(session: SessionState) -> Optional[str]:
        events = session.last_offerings.get("events") or []
        if not isinstance(events, list):
            return None
        selected_id = str(session.memory.selected_event_id or "")
        picked = None
        if selected_id:
            picked = next(
                (
                    item
                    for item in events
                    if isinstance(item, dict) and str(item.get("id") or "") == selected_id
                ),
                None,
            )
        if picked is None and len(events) == 1 and isinstance(events[0], dict):
            picked = events[0]
        if not isinstance(picked, dict):
            return None
        url = picked.get("booking_url") or None
        if not url:
            return None
        names = [
            str(item.get("name")).strip()
            for item in session.travelers
            if str(item.get("name") or "").strip()
        ]
        extra: dict[str, Any] = {}
        payload = []
        for item in session.travelers:
            first, last = ConversationAgent._split_full_name(str(item.get("name") or ""))
            first = str(item.get("first_name") or first)
            last = str(item.get("last_name") or last)
            if not first:
                continue
            payload.append(
                {
                    "first_name": first,
                    "last_name": last,
                    "email": item.get("email") or session.memory.contact_email or "",
                    "phone": item.get("phone") or session.memory.contact_phone or "",
                    "gender": item.get("gender") or "",
                    "dob": item.get("dob") or "",
                }
            )
        if payload:
            extra["guest_data"] = json.dumps(payload, separators=(",", ":"))
            extra["qty"] = str(session.memory.passenger_count or len(payload))
        elif names:
            extra["guests"] = ",".join(names)
            extra["qty"] = str(session.memory.passenger_count or len(names))
        if session.memory.contact_phone:
            extra["phone"] = session.memory.contact_phone
        if session.memory.contact_email:
            extra["email"] = session.memory.contact_email
        return ConversationAgent._with_query(url, extra) if extra else url

    async def _load_event_details(
        self,
        session: SessionState,
        event_id: str,
        tools_used: list[str],
        fallback: Optional[dict[str, Any]] = None,
    ) -> str:
        result = await self._tools.execute(
            "get_event_details",
            {"event_id": event_id},
            session_id=session.session_id,
            user_access_token=session.user_access_token,
        )
        tools_used.append("get_event_details")
        self._update_memory_from_tool(
            session, "get_event_details", {"event_id": event_id}, result
        )
        if isinstance(result, dict) and result.get("error"):
            session.memory.flow_step = "event_pick"
            return result.get("message") or "I could not open that event. Please pick another option."
        event = result.get("event") if isinstance(result.get("event"), dict) else result
        if not isinstance(event, dict):
            event = fallback or {}
        session.memory.selected_event_id = str(event.get("id") or event_id)
        session.memory.event_name = event.get("name")
        session.memory.flow_step = "event_confirm"
        return self._event_choice_speech(event)

    async def _handle_event_flow(
        self, session: SessionState, user_text: str, tools_used: list[str]
    ) -> Optional[str]:
        mem = session.memory
        text = (user_text or "").strip().lower()
        if mem.user_goal != "book_event" and mem.booking_mode != "event":
            if not self._is_event_intent(text):
                return None
            mem.user_goal = "book_event"
            mem.booking_mode = "event"
            mem.intent = "book"
            mem.flow_step = "event_search"

        events = [
            item
            for item in (session.last_offerings.get("events") or [])
            if isinstance(item, dict)
        ]

        if mem.flow_step == "event_guests" and mem.selected_event_id:
            return self._collect_event_guests(session, user_text)

        if mem.flow_step == "event_open" and mem.selected_event_id:
            if self._wants_event_correction(user_text):
                mem.flow_step = "event_guests"
                return self._collect_event_guests(session, user_text)
            return (
                "The booking page is open with the guest details filled. "
                "Click Book or Pay there. If a detail is wrong, say what to change."
            )

        if mem.flow_step == "event_confirm" and mem.selected_event_id:
            if self._wants_event_book(user_text):
                session.travelers = []
                mem.passenger_count = self._parse_passenger_count(user_text)
                return self._collect_event_guests(session, user_text)
            if self._user_said_no(user_text):
                mem.flow_step = "event_pick"
                mem.selected_event_id = None
                return "Okay. Tell me about another event by name."
            matched = self._match_event_mention(user_text, events)
            if matched is not None:
                return await self._load_event_details(
                    session, str(matched.get("id") or ""), tools_used, matched
                )
            if self._wants_event_info(user_text):
                return "Which event? Please say the event name."
            if self._is_event_followup(user_text, events):
                return "Please say yes to book, then the guest names."
            return None

        is_followup = self._is_event_followup(user_text, events)

        if events:
            if self._wants_event_book(user_text):
                chosen = self._match_event_mention(user_text, events)
                if chosen is None and len(events) == 1:
                    chosen = events[0]
                if chosen is None:
                    return "Which event should I book? Say the event name."
                await self._load_event_details(
                    session, str(chosen.get("id") or ""), tools_used, chosen
                )
                session.travelers = []
                mem.passenger_count = self._parse_passenger_count(user_text)
                return self._collect_event_guests(session, user_text)
            if (
                mem.flow_step == "event_pick"
                and len(events) == 1
                and self._wants_event_info(user_text)
            ):
                return await self._load_event_details(
                    session, str(events[0].get("id") or ""), tools_used, events[0]
                )
            matched = self._match_event_mention(user_text, events)
            if matched is not None:
                return await self._load_event_details(
                    session, str(matched.get("id") or ""), tools_used, matched
                )
            if mem.flow_step == "event_pick":
                if self._wants_event_info(user_text):
                    return "Which event? Please say the event name, like tell me about, then the name."
                if is_followup:
                    return "Say tell me about, then the event name."
                return None

        if not is_followup and mem.flow_step != "event_search":
            return None

        should_search = not events or any(
            key in text
            for key in (
                "search",
                "find",
                "show me",
                "another",
                "other event",
            )
        ) or bool(self._event_search_args(user_text).get("city") or self._event_search_args(user_text).get("search"))
        if not should_search:
            return None

        args = self._event_search_args(user_text)
        result = await self._tools.execute(
            "search_events",
            args,
            session_id=session.session_id,
            user_access_token=session.user_access_token,
        )
        tools_used.append("search_events")
        self._update_memory_from_tool(session, "search_events", args, result)
        if isinstance(result, dict) and result.get("error"):
            return result.get("message") or "I could not load events right now."
        rows = result.get("results") if isinstance(result, dict) else None
        mem.flow_step = "event_pick"
        if not rows:
            return "I did not find matching events. Try another city, artist, or event name."
        return self._speak_event_names(rows)

    @staticmethod
    def _user_said_no(user_text: str) -> bool:
        text = (user_text or "").strip().lower()
        return bool(re.search(r"\b(no|nope|nah|not now|don't|do not)\b", text))

    async def _handle_guided_flow(
        self, session: SessionState, user_text: str, tools_used: list[str]
    ) -> Optional[str]:
        """Simple chat flow: goal → where → why → when → passengers; or PNR cancel/refund."""
        mem = session.memory
        text = (user_text or "").strip().lower()

        # Greeting → ask what they want (no option cards).
        if self._is_greeting(user_text) and not mem.user_goal:
            return self._show_main_menu(session)

        later = self._later_module_reply(user_text)
        if later and not mem.user_goal:
            return later

        # Capture goal from speech only (no Option 1/2 cards).
        if not mem.user_goal or mem.flow_step == "goal":
            matched_goal = None
            if self._is_event_intent(text):
                matched_goal = "book_event"
            elif self._is_flight_intent(text):
                matched_goal = "book_flight"
            elif self._is_hotel_intent(text):
                matched_goal = "book_hotel"
            elif any(
                k in text
                for k in (
                    "book a train",
                    "book train",
                    "train ticket",
                    "train booking",
                    "i want to book",
                    "book ticket",
                )
            ):
                matched_goal = "book_train"
            elif "cancel" in text:
                matched_goal = "cancel"
            elif "refund" in text:
                matched_goal = "refund"
            elif any(
                k in text
                for k in ("full coach", "large group", "charter", "entire train")
            ):
                matched_goal = "charter"
            elif mem.source and mem.destination:
                matched_goal = "book_flight" if self._looks_international(mem.destination) else None
                if matched_goal is None and (self._is_flight_intent(text) or self._is_train_intent(text)):
                    matched_goal = "book_flight" if self._is_flight_intent(text) else "book_train"
            if matched_goal:
                mem.user_goal = matched_goal
                mem.flow_step = (
                    "where"
                    if matched_goal in {"book_train", "book_flight", "book_hotel"}
                    else matched_goal
                )
                if matched_goal == "book_event":
                    mem.booking_mode = "event"
                    mem.intent = "book"
                    mem.flow_step = "event_search"
                    return None
                if matched_goal == "book_hotel":
                    mem.booking_mode = "hotel"
                    mem.hotel_required = True
                    mem.intent = "book"
                    if not (mem.destination or mem.source):
                        return "Which city should I search hotels in?"
                elif matched_goal == "book_flight":
                    mem.booking_mode = "flight"
                    mem.transport_type = "flight"
                    mem.flight_required = True
                    mem.intent = "book"
                    if not (mem.source and mem.destination):
                        return "Where would you like to go? You can say Pune to Delhi, and who is travelling."
                elif matched_goal == "book_train":
                    mem.booking_mode = mem.booking_mode or "normal"
                    mem.intent = "book"
                    if not (mem.source and mem.destination):
                        return "Where would you like to go? Say it like Pune to Delhi."
                elif matched_goal == "charter":
                    mem.booking_mode = "charter"
                    mem.intent = "charter"
                    return (
                        "Sure — full coach for large groups. "
                        "Tell me the route, date, and how many people."
                    )
                elif matched_goal in {"cancel", "refund"}:
                    mem.intent = "support"
                    return (
                        f"Okay, {matched_goal}. "
                        "Please share your PNR or booking ID, like BK-XXXXXXXX."
                    )

        if mem.user_goal == "book_event" or mem.booking_mode == "event":
            return None

        if mem.user_goal == "book_hotel" or mem.booking_mode == "hotel":
            return await self._handle_hotel_flow(session, user_text, tools_used)

        # Trip discovery: route known, mode not chosen yet.
        if not mem.user_goal and mem.source and mem.destination:
            if self._looks_international(mem.destination):
                mem.user_goal = "book_flight"
                mem.booking_mode = "flight"
                mem.transport_type = "flight"
                mem.flight_required = True
            else:
                mem.flow_step = "how"
                return (
                    f"Got {mem.source} to {mem.destination}. "
                    "Would you like to fly, take a train, or book a hotel?"
                )

        # No booking intent yet — let the LLM handle general conversation.
        if not mem.user_goal and not (mem.source and mem.destination):
            return None

        # Cancel / refund by PNR
        if mem.user_goal in {"cancel", "refund"} or mem.intent == "support":
            return await self._handle_pnr_support(session, user_text, tools_used)

        # Book train / flight guided slots: where → why → when → count
        if mem.user_goal in {None, "book_train", "book_flight"} or mem.booking_mode in {
            None,
            "normal",
            "flight",
        }:
            if self._is_flight_intent(text) and mem.user_goal in {None, "book_train"}:
                if not mem.selected_train_id:
                    mem.user_goal = "book_flight"
                    mem.transport_type = "flight"
                    mem.flight_required = True
                    if mem.booking_mode in {None, "normal"}:
                        mem.booking_mode = "flight"
            if mem.user_goal is None and (mem.source or mem.destination):
                mem.user_goal = "book_train"
                mem.booking_mode = mem.booking_mode or "normal"

            src, dst = self._parse_route_from_text(user_text)
            if src and dst:
                mem.source = src
                mem.destination = dst
            if mem.source:
                mem.source = self._clean_place_name(mem.source).title()
            if mem.destination:
                mem.destination = self._clean_place_name(mem.destination).title()
            date = self._parse_date_from_text(user_text)
            if date:
                mem.departure_date = date

            # Purpose from speech only
            if not mem.trip_purpose:
                for purpose, keys in (
                    ("wedding", ("wedding",)),
                    ("baraat", ("baraat",)),
                    ("personal_work", ("personal work", "office", "work", "business")),
                    ("tourism", ("tourism", "holiday", "vacation")),
                    ("conference", ("conference", "meeting")),
                    ("other", ("other",)),
                ):
                    if any(k in text for k in keys):
                        mem.trip_purpose = purpose
                        break

            if mem.source and mem.destination and not mem.departure_date:
                mem.flow_step = "when"
                return (
                    f"Got {mem.source} to {mem.destination}. "
                    "When do you want to travel? You can say tomorrow, or 20 September 2026."
                )

            if (
                mem.source
                and mem.destination
                and mem.departure_date
                and not mem.passenger_count
            ):
                parsed = self._parse_passenger_count(user_text)
                if parsed:
                    mem.passenger_count = parsed
                    mem.adult_count = mem.adult_count or parsed
                else:
                    mem.flow_step = "passengers"
                    session.last_offerings.pop("goals", None)
                    who = "who is travelling — for example 2 adults, or just me?"
                    if mem.party_type == "family":
                        who = "how many adults and children?"
                    return f"And {who}"

            if mem.source and mem.destination and mem.departure_date and mem.passenger_count and not mem.trip_purpose:
                if mem.party_type in {"family", "business"}:
                    mem.trip_purpose = mem.party_type
                else:
                    return self._show_purpose_menu(session)

            wants_flight = (
                mem.user_goal == "book_flight"
                or mem.transport_type == "flight"
                or mem.booking_mode == "flight"
            )
            already_searched = bool(
                (session.last_offerings.get("flights") or [])
                if wants_flight
                else (session.last_offerings.get("trains") or [])
            )
            already_picked = bool(
                mem.selected_flight_id if wants_flight else mem.selected_train_id
            )

            # Ready to search once — auto normal booking, no mode quiz.
            if (
                mem.source
                and mem.destination
                and mem.trip_purpose
                and mem.departure_date
                and mem.passenger_count
                and not already_picked
                and not already_searched
            ):
                mem.booking_mode = "flight" if wants_flight else (mem.booking_mode or "normal")
                mem.flow_step = "search"
                session.last_offerings.pop("goals", None)
                session.last_offerings.pop("purposes", None)
                args: dict[str, Any] = {
                    "source": mem.source,
                    "destination": mem.destination,
                    "departure_date": mem.departure_date,
                    "passengers": min(int(mem.adult_count or mem.passenger_count or 1), 9),
                }
                if wants_flight:
                    args["travel_class"] = mem.travel_class or "economy"
                    args["children"] = mem.child_count or 0
                    args["infants"] = mem.infant_count or 0
                    args["direct_only"] = bool(mem.direct_only)
                    if mem.return_date:
                        args["return_date"] = mem.return_date
                        args["trip_type"] = mem.trip_type or "round_trip"
                    tool_name = "search_flights"
                    kind = "flights"
                else:
                    args["passengers"] = min(int(mem.passenger_count), 9)
                    args["preference"] = mem.train_preference or mem.value_priority or "balanced"
                    if args["preference"] not in {"cheapest", "fastest", "ac", "balanced", "wishlist"}:
                        args["preference"] = "balanced"
                    tool_name = "search_trains"
                    kind = "trains"
                result = await self._tools.execute(
                    tool_name, args, session_id=session.session_id
                )
                tools_used.append(tool_name)
                self._update_memory_from_tool(session, tool_name, args, result)
                rows = result.get("results") if isinstance(result, dict) else None
                if rows:
                    return self._speak_options(session, rows, kind)
                fail = ""
                if isinstance(result, dict):
                    fail = str(result.get("message") or "")
                return (
                    fail
                    or f"I could not find {kind} for that route. Please try another city pair."
                )

        return None

    _INTERNATIONAL_PLACES = {
        "europe", "dubai", "uae", "switzerland", "paris", "london", "singapore",
        "bali", "thailand", "bangkok", "maldives", "new york", "usa", "uk",
        "france", "italy", "spain", "germany", "tokyo", "japan", "sydney",
        "australia", "qatar", "doha", "malaysia", "kuala lumpur", "zurich",
        "geneva", "amsterdam", "rome", "barcelona",
    }

    @staticmethod
    def _looks_international(place: str) -> bool:
        key = " ".join((place or "").strip().lower().split())
        return any(token in key for token in ConversationAgent._INTERNATIONAL_PLACES)

    async def _handle_hotel_flow(
        self, session: SessionState, user_text: str, tools_used: list[str]
    ) -> Optional[str]:
        mem = session.memory
        city = mem.hotel_area or mem.destination or mem.source
        if not city:
            return "Which city should I search hotels in?"
        mem.destination = mem.destination or city
        if not mem.departure_date:
            return f"Hotels in {city}. What check-in date? Say tomorrow or 20 September 2026."
        nights = mem.trip_nights or 1
        check_out = mem.return_date
        if not check_out:
            from datetime import datetime, timedelta

            try:
                check_out = (
                    datetime.fromisoformat(mem.departure_date) + timedelta(days=max(1, nights))
                ).date().isoformat()
            except ValueError:
                check_out = mem.departure_date
            mem.return_date = check_out
        guests = mem.passenger_count or mem.adult_count or 2
        if not mem.passenger_count:
            parsed = self._parse_passenger_count(user_text)
            if parsed:
                mem.passenger_count = parsed
                guests = parsed
            elif not re.search(r"\b(hotel|stay|room|search)\b", (user_text or "").lower()):
                return "How many guests, and how many nights?"
        already = session.last_offerings.get("hotels") or []
        if already:
            matched = self._match_option(user_text, already)
            if matched is None:
                return None
            mem.selected_hotel_id = str(matched.get("id") or "")
            session.itinerary_selections["hotel"] = matched
            name = matched.get("name") or "that hotel"
            return (
                f"Got {name}. Hotel checkout still finishes in the Super Travel app. "
                "Say another city or date if you want a new search."
            )
        result = await self._tools.execute(
            "search_hotels",
            {
                "city": city,
                "check_in": mem.departure_date,
                "check_out": check_out,
                "guests": guests,
                "rooms": 1,
                "budget_max": mem.budget,
            },
            session_id=session.session_id,
        )
        tools_used.append("search_hotels")
        self._update_memory_from_tool(session, "search_hotels", {"city": city}, result)
        rows = result.get("results") if isinstance(result, dict) else None
        if rows:
            mem.source = mem.source or city
            mem.destination = city
            return self._speak_options(session, rows, "hotels")
        return (
            (result.get("message") if isinstance(result, dict) else None)
            or f"I could not find hotels in {city} for those dates."
        )

    async def _handle_pnr_support(
        self, session: SessionState, user_text: str, tools_used: list[str]
    ) -> Optional[str]:
        mem = session.memory
        goal = mem.user_goal or ("refund" if "refund" in (user_text or "").lower() else "cancel")
        pnr = self._extract_pnr(user_text) or mem.pnr or mem.last_booking_id
        if pnr:
            mem.pnr = pnr
            mem.last_booking_id = mem.last_booking_id or pnr

        if not mem.pnr:
            session.last_offerings["support"] = {
                "action": goal,
                "need": "PNR or booking ID",
            }
            return f"Please share your PNR or booking ID to {goal}."

        if not self._user_said_yes(user_text) and "confirm" not in (user_text or "").lower():
            # If user only pasted PNR this turn, ask confirm.
            if self._extract_pnr(user_text) or not any(
                w in (user_text or "").lower() for w in ("yes", "confirm", "go ahead", "proceed")
            ):
                session.last_offerings["support"] = {
                    "action": goal,
                    "pnr": mem.pnr,
                }
                return (
                    f"PNR {mem.pnr} noted. Say yes to confirm {goal}, "
                    "or share a different PNR."
                )

        if goal == "refund":
            result = await self._tools.execute(
                "request_refund",
                {
                    "booking_id": mem.pnr,
                    "reason": "user_requested",
                    "confirmed": True,
                },
                session_id=session.session_id,
            )
            tools_used.append("request_refund")
            self._update_memory_from_tool(
                session, "request_refund", {"booking_id": mem.pnr}, result
            )
            if isinstance(result, dict) and not result.get("error"):
                session.last_offerings["support"] = result
                mem.flow_step = "done"
                return (
                    f"Refund request submitted for PNR {mem.pnr}. "
                    "Our team will review it shortly."
                )
            return (
                (result or {}).get("message")
                if isinstance(result, dict)
                else None
            ) or f"I could not start a refund for {mem.pnr}. Please check the PNR."

        # cancel
        result = await self._tools.execute(
            "cancel_booking",
            {
                "booking_id": mem.pnr,
                "reason": "user_requested",
                "confirmed": True,
            },
            session_id=session.session_id,
        )
        tools_used.append("cancel_booking")
        self._update_memory_from_tool(
            session, "cancel_booking", {"booking_id": mem.pnr}, result
        )
        if isinstance(result, dict) and not result.get("error"):
            session.last_offerings["support"] = result
            mem.flow_step = "done"
            return f"Booking {mem.pnr} is cancelled. Seats will return to the waitlist queue if needed."
        return (
            (result or {}).get("message")
            if isinstance(result, dict)
            else None
        ) or f"I could not cancel {mem.pnr}. Please check the PNR."

    def _ingest_charter_slots(self, session: SessionState, user_text: str) -> None:
        mem = session.memory
        src, dst = self._parse_route_from_text(user_text)
        if src and dst:
            mem.source = src
            mem.destination = dst
        date = self._parse_date_from_text(user_text)
        if date:
            mem.departure_date = date
        text = user_text.lower()
        for purpose in ("wedding", "conference", "tourism", "baraat", "group tour"):
            if purpose in text:
                mem.trip_purpose = purpose
                break
        if "entire train" in text:
            mem.charter_type = "entire_train"
        elif "full coach" in text or "coach" in text:
            mem.charter_type = mem.charter_type or "full_coach"

    def _handle_large_group_hint(
        self, session: SessionState, user_text: str
    ) -> Optional[str]:
        """When user states >9 people, steer to full coach (72 seats), not split tickets."""
        mem = session.memory
        if mem.user_goal == "book_event" or mem.booking_mode == "event":
            return None
        count = mem.passenger_count or 0
        if count <= 9 and mem.booking_mode != "charter":
            return None

        text = user_text.lower()
        just_stated_count = self._parse_passenger_count(user_text) is not None
        if mem.booking_mode != "charter" and not just_stated_count:
            return None

        if mem.booking_mode != "charter":
            mem.booking_mode = "charter"
            mem.intent = "charter"
            mem.charter_type = mem.charter_type or "full_coach"

        # Always absorb route/date/purpose from this turn.
        self._ingest_charter_slots(session, user_text)

        # Enough details → let LLM / charter tools continue (or ask next step once).
        if mem.source and mem.destination and mem.departure_date:
            if not mem.trip_purpose:
                return (
                    f"Got it — full coach for {count or 'your'} people from "
                    f"{mem.source} to {mem.destination} on {mem.departure_date}. "
                    "What is the trip for — wedding, baraat, personal work, tourism, conference, or other?"
                )
            if session.travelers:
                return None  # LLM should call request_charter
            wants_submit = any(
                w in text
                for w in ("yes", "submit", "confirm", "go ahead", "book coach", "proceed")
            )
            if wants_submit and len(session.travelers) >= (count or 1):
                return None
            # Start / continue collecting passenger names
            mem.pre_booking_step = "passengers"
            if len(session.travelers) < (count or 1):
                return (
                    f"Ready for full coach ({FULL_COACH_SEATS} seats): "
                    f"{mem.source} → {mem.destination} on {mem.departure_date}, "
                    f"{count} people"
                    + (f", {mem.trip_purpose}" if mem.trip_purpose else "")
                    + ". "
                    + self._ask_next_passenger(session)
                )
            return None

        missing = []
        if not mem.source or not mem.destination:
            missing.append("route (from–to)")
        if not mem.departure_date:
            missing.append("travel date")
        seats = FULL_COACH_SEATS
        ask = (
            f"For {count} people we book a full coach ({seats} seats), "
            "not separate tickets of 9. "
        )
        ask += "Please share: " + ", ".join(missing) + "."
        return ask

    @staticmethod
    def _needed_passenger_count(session: SessionState) -> int:
        mem = session.memory
        if session.travelers:
            return max(len(session.travelers), mem.passenger_count or 0)
        return max(1, mem.passenger_count or 1)

    @staticmethod
    def _parse_passenger_names(user_text: str) -> list[str]:
        text = (user_text or "").strip()
        if not text:
            return []
        lower = text.lower()
        # Ignore pure confirmation / meal answers
        if lower in {"yes", "no", "ok", "okay", "skip"}:
            return []
        cleaned = re.sub(
            r"^(?:my name is|name is|i am|i'm|passenger\s*\d+\s*(?:is|:)?)\s*",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        # Split on commas / " and "
        parts = re.split(r"\s*(?:,|/|;|\band\b)\s*", cleaned, flags=re.IGNORECASE)
        names: list[str] = []
        for part in parts:
            name = re.sub(r"[^A-Za-z .'-]", "", part).strip(" .")
            if len(name) < 2:
                continue
            # Skip if it looks like a phone or date-ish leftover
            if re.fullmatch(r"\d+", name):
                continue
            if name.lower() in {
                "veg",
                "non veg",
                "non-veg",
                "jain",
                "no meal",
                "tomorrow",
                "today",
            }:
                continue
            names.append(" ".join(w.capitalize() for w in name.split()))
        return names

    def _ask_next_passenger(self, session: SessionState) -> str:
        mem = session.memory
        total = self._needed_passenger_count(session)
        have = len(session.travelers)
        idx = have + 1
        mem.pre_booking_step = "passengers"
        mem.passenger_collect_index = have
        if total == 1:
            return "Please tell me the passenger full name."
        return (
            f"Please tell me passenger {idx} of {total} full name. "
            "You can also say all names together, like Rahul, Priya, Amit."
        )

    @staticmethod
    def _wants_name_correction(user_text: str) -> bool:
        text = (user_text or "").strip().lower()
        return any(
            p in text
            for p in (
                "name is wrong",
                "names are wrong",
                "wrong name",
                "incorrect name",
                "change the name",
                "change name",
                "correct the name",
                "correct name",
                "person's name",
                "persons name",
                "passenger name is",
                "passenger's name",
                "edit name",
                "update name",
                "update the name",
                "update names",
                "fix name",
                "name update",
                "name galat",
                "naam galat",
                "second person",
                "first person",
                "third person",
                "2nd person",
                "1st person",
                "passenger 2",
                "passenger 1",
            )
        )

    @staticmethod
    def _parse_meal_choice(user_text: str) -> Optional[str]:
        text = (user_text or "").strip().lower()
        text = re.sub(r"[^\w\s-]", " ", text)
        text = " ".join(text.split())
        if not text:
            return None
        if "jain" in text:
            return "jain"
        if "non veg" in text or "non-veg" in text or "nonveg" in text:
            return "non_veg"
        if any(
            p in text
            for p in ("no meal", "without meal", "skip meal", "no food", "without food")
        ):
            return "none"
        if "vegetarian" in text or re.search(r"\bveg\b", text):
            return "veg"
        return None

    @staticmethod
    def _wants_meal_update(user_text: str) -> bool:
        text = (user_text or "").strip().lower()
        # STT often hears "meal" as "mail".
        phrases = (
            "update meal",
            "change meal",
            "edit meal",
            "fix meal",
            "meal update",
            "update the meal",
            "change the meal",
            "different meal",
            "another meal",
            "wrong meal",
            "update mail",
            "change mail",
            "edit mail",
            "update the mail",
            "change the mail",
            "mail update",
            "change food",
            "update food",
        )
        if any(p in text for p in phrases):
            return True
        if re.search(r"\b(meal|mail|food)\b", text) and any(
            w in text for w in ("update", "change", "edit", "fix", "wrong")
        ):
            return True
        return False

    @staticmethod
    def _wants_phone_update(user_text: str) -> bool:
        text = (user_text or "").strip().lower()
        return any(
            p in text
            for p in (
                "update phone",
                "change phone",
                "edit phone",
                "wrong phone",
                "update number",
                "change number",
                "update mobile",
                "change mobile",
                "update the phone",
                "change the phone",
            )
        )

    def _handle_mid_booking_edits(
        self, session: SessionState, user_text: str
    ) -> Optional[str]:
        """Allow meal / phone changes after train pick, including confirm step."""
        mem = session.memory
        if not mem.selected_train_id:
            return None
        if mem.pre_booking_step not in {
            "meals",
            "passengers",
            "phone",
            "confirm",
            "fix_name",
            "await_meal_update",
            "await_phone_update",
            "done",
        }:
            return None

        text_l = (user_text or "").strip().lower()

        # Waiting for new meal after "update meal".
        if mem.pre_booking_step == "await_meal_update":
            choice = self._parse_meal_choice(user_text)
            if not choice:
                return "What meal preference? Say veg, non-veg, jain, or no meal."
            mem.meal_preference = choice
            mem.meal_asked = True
            return self._resume_after_edit(
                session, f"Meal updated to {choice.replace('_', ' ')}."
            )

        # Waiting for new phone after "update phone".
        if mem.pre_booking_step == "await_phone_update":
            phone = self._extract_phone(user_text)
            if not phone:
                return "Please say the 10-digit mobile number."
            mem.contact_phone = phone
            session.customer_phone = phone
            return self._resume_after_edit(session, f"Phone updated to {phone}.")

        if self._wants_meal_update(user_text):
            choice = self._parse_meal_choice(user_text)
            if choice:
                mem.meal_preference = choice
                mem.meal_asked = True
                return self._resume_after_edit(
                    session, f"Meal updated to {choice.replace('_', ' ')}."
                )
            mem.pre_booking_step = "await_meal_update"
            current = (mem.meal_preference or "not set").replace("_", " ")
            return (
                f"Sure — current meal is {current}. "
                "What should I change it to? veg, non-veg, jain, or no meal?"
            )

        if self._wants_phone_update(user_text):
            phone = self._extract_phone(user_text)
            if phone:
                mem.contact_phone = phone
                session.customer_phone = phone
                return self._resume_after_edit(session, f"Phone updated to {phone}.")
            mem.pre_booking_step = "await_phone_update"
            return "Sure — what is the new 10-digit mobile number?"

        # At confirm, also accept a direct meal phrase like "make it veg".
        if mem.pre_booking_step == "confirm" and (
            "meal" in text_l or "mail" in text_l or "food" in text_l
        ):
            choice = self._parse_meal_choice(user_text)
            if choice:
                mem.meal_preference = choice
                mem.meal_asked = True
                return self._resume_after_edit(
                    session, f"Meal updated to {choice.replace('_', ' ')}."
                )

        return None

    def _resume_after_edit(self, session: SessionState, prefix: str) -> str:
        mem = session.memory
        total = self._needed_passenger_count(session)
        names = [
            str(t.get("name")).strip()
            for t in session.travelers
            if t.get("name")
        ]
        if len(session.travelers) < total:
            mem.pre_booking_step = "passengers"
            return f"{prefix} Please continue with passenger names."
        if not mem.contact_phone:
            mem.pre_booking_step = "phone"
            return f"{prefix} What is the contact mobile number?"
        mem.pre_booking_step = "confirm"
        named = ", ".join(names[:8]) if names else "—"
        meal = (mem.meal_preference or "none").replace("_", " ")
        return (
            f"{prefix} Meal: {meal}. Names: {named}. "
            "Say yes to confirm booking, or tell me another change "
            "(update meal / update name / update phone)."
        )

    @staticmethod
    def _extract_phone(user_text: str) -> Optional[str]:
        digits = re.sub(r"\D", "", user_text or "")
        if len(digits) >= 10:
            return digits[-10:]
        return None

    def _handle_name_correction(
        self, session: SessionState, user_text: str
    ) -> Optional[str]:
        """Fix passenger names without re-triggering train option matching."""
        mem = session.memory
        text = (user_text or "").strip()
        text_l = text.lower()

        # Waiting for the corrected name after we asked for it.
        if mem.pre_booking_step == "fix_name":
            idx = mem.passenger_collect_index
            if idx is None or idx < 0:
                idx = 0
            names = self._parse_passenger_names(text)
            # Ignore leftover correction phrases with no real name
            if not names:
                return (
                    f"Please say the correct full name for passenger {idx + 1}."
                )
            new_name = names[0]
            while len(session.travelers) <= idx:
                session.travelers.append({"name": ""})
            session.travelers[idx] = {
                **session.travelers[idx],
                "name": new_name,
            }
            if idx == 0:
                mem.passenger_name = new_name
            session.last_offerings["travelers"] = list(session.travelers)
            # Resume wherever we were (usually phone / confirm).
            if not mem.contact_phone:
                mem.pre_booking_step = "phone"
                named = ", ".join(
                    str(t.get("name")) for t in session.travelers if t.get("name")
                )
                return (
                    f"Updated passenger {idx + 1} to {new_name}. "
                    f"Names now: {named}. What is the contact mobile number?"
                )
            mem.pre_booking_step = "confirm"
            named = ", ".join(
                str(t.get("name")) for t in session.travelers if t.get("name")
            )
            return (
                f"Updated passenger {idx + 1} to {new_name}. "
                f"Names now: {named}. Say yes to confirm booking."
            )

        if not session.travelers and not self._wants_name_correction(text_l):
            return None
        if not self._wants_name_correction(text_l):
            # Also catch "second name is X" style while on phone/confirm
            if mem.pre_booking_step not in {"phone", "confirm", "passengers", "fix_name"}:
                return None
            return None

        # Which passenger index?
        idx = 0
        m = re.search(
            r"(?:passenger|person|pax|traveler|traveller)\s*(?:number\s*)?(\d+)",
            text_l,
        )
        if m:
            idx = max(0, int(m.group(1)) - 1)
        elif any(w in text_l for w in ("second", "2nd", "दोसरा", "दूसरा")):
            idx = 1
        elif any(w in text_l for w in ("third", "3rd", "तीसरा")):
            idx = 2
        elif any(w in text_l for w in ("first", "1st", "पहला")):
            idx = 0
        elif "last" in text_l and session.travelers:
            idx = len(session.travelers) - 1

        if not session.travelers:
            mem.pre_booking_step = "passengers"
            return self._ask_next_passenger(session)

        if idx >= len(session.travelers):
            idx = len(session.travelers) - 1

        # If user already included the correct name: "second name is Priya Sharma"
        inline = re.search(
            r"(?:name\s*(?:is|=|:)|correct(?:\s+name)?\s*(?:is|=|:)|change(?:\s+(?:it|name))?\s+to)\s+([a-zA-Z][a-zA-Z\s.'-]{1,60})$",
            text,
            flags=re.IGNORECASE,
        )
        bad_names = {
            "wrong",
            "incorrect",
            "invalid",
            "missing",
            "false",
            "galat",
            "wrong name",
            "not correct",
        }
        if inline:
            new_name = " ".join(inline.group(1).split()).strip(" .")
            if new_name.lower() in bad_names or new_name.lower().startswith("wrong"):
                inline = None
        if inline:
            new_name = " ".join(inline.group(1).split()).strip(" .")
            if new_name and len(new_name) > 1:
                session.travelers[idx] = {
                    **session.travelers[idx],
                    "name": new_name,
                }
                if idx == 0:
                    mem.passenger_name = new_name
                session.last_offerings["travelers"] = list(session.travelers)
                if not mem.contact_phone:
                    mem.pre_booking_step = "phone"
                else:
                    mem.pre_booking_step = "confirm"
                named = ", ".join(
                    str(t.get("name")) for t in session.travelers if t.get("name")
                )
                return (
                    f"Updated passenger {idx + 1} to {new_name}. "
                    f"Names now: {named}. "
                    + (
                        "What is the contact mobile number?"
                        if not mem.contact_phone
                        else "Say yes to confirm booking."
                    )
                )

        old = session.travelers[idx].get("name") or f"passenger {idx + 1}"
        mem.pre_booking_step = "fix_name"
        mem.passenger_collect_index = idx
        return (
            f"Okay — passenger {idx + 1} is currently {old}. "
            f"Please say the correct full name for passenger {idx + 1}."
        )

    def _handle_passenger_details(
        self, session: SessionState, user_text: str
    ) -> Optional[str]:
        """Collect one name per passenger (or batch / XLSX)."""
        mem = session.memory
        total = self._needed_passenger_count(session)

        # Charter: once route/date known, collect names if incomplete.
        if mem.booking_mode == "charter":
            if not (mem.source and mem.destination and mem.departure_date):
                return None
            if len(session.travelers) >= total > 0:
                # Still collect phone if missing
                if mem.pre_booking_step == "phone" and not mem.contact_phone:
                    digits = re.sub(r"\D", "", user_text)
                    if len(digits) >= 10:
                        mem.contact_phone = digits[-10:]
                        mem.pre_booking_step = "confirm"
                        return (
                            "Thanks. Say yes to submit the full coach request to SabRaah sales."
                        )
                    return "Please share a 10-digit contact mobile number."
                return None
            mem.pre_booking_step = "passengers"
        elif mem.pre_booking_step != "passengers":
            return None

        if total > 20 and len(session.travelers) == 0:
            return (
                f"For {total} travelers, please upload an XLSX/CSV on screen "
                "(columns: name, phone, meal, allergies), "
                "or say names in batches."
            )

        names = self._parse_passenger_names(user_text)
        if names:
            for name in names:
                if len(session.travelers) >= total:
                    break
                session.travelers.append({"name": name})
            if session.travelers and not mem.passenger_name:
                mem.passenger_name = session.travelers[0]["name"]
            session.last_offerings["travelers"] = list(session.travelers)

        have = len(session.travelers)
        if have < total:
            return self._ask_next_passenger(session)

        mem.passenger_name = session.travelers[0]["name"]
        mem.passenger_count = total
        mem.pre_booking_step = "phone"
        named = ", ".join(t["name"] for t in session.travelers[:8])
        extra = f" (+{have - 8} more)" if have > 8 else ""
        if mem.booking_mode == "charter":
            return (
                f"Got all {have} names: {named}{extra}. "
                "What is the contact phone number? Then say yes to submit the coach request."
            )
        if not mem.contact_phone:
            return (
                f"Got all {have} passenger names: {named}{extra}. "
                "What is the contact mobile number?"
            )
        return (
            f"Got all {have} passengers: {named}{extra}. "
            "Shall I continue to payment?"
        )

    @staticmethod
    def _user_said_yes(user_text: str) -> bool:
        text = (user_text or "").strip().lower()
        # Don't treat "I want to know the names" as a yes.
        if any(
            p in text
            for p in (
                "name",
                "names",
                "passenger",
                "passengers",
                "repeat",
                "what did",
                "tell me",
                "list",
                "which",
                "phone",
                "mobile",
                "number",
                "meal",
                "details",
                "summary",
            )
        ) and not re.search(
            r"\b(?:yes|yeah|yep|confirm|book it|book now|proceed|go ahead|submit)\b",
            text,
        ):
            return False
        return any(
            p in text
            for p in (
                "yes",
                "yeah",
                "yep",
                "confirm",
                "confirmed",
                "go ahead",
                "proceed",
                "continue to payment",
                "book it",
                "book now",
                "sure",
                "ok book",
                "okay book",
                "submit",
            )
        )

    @staticmethod
    def _wants_booking_review(user_text: str) -> bool:
        text = (user_text or "").strip().lower()
        return any(
            p in text
            for p in (
                "passenger name",
                "passengers name",
                "passenger's name",
                "passengers' name",
                "name that i",
                "names that i",
                "names i told",
                "name i told",
                "told you",
                "repeat",
                "what are the name",
                "what are the passenger",
                "list the name",
                "list passenger",
                "tell me the name",
                "tell me passenger",
                "show me the name",
                "show passenger",
                "which passenger",
                "who are the passenger",
                "mobile number",
                "phone number",
                "contact number",
                "what meal",
                "meal preference",
                "booking detail",
                "trip detail",
                "summary",
                "recap",
                "put the name",
                "say the name",
                "read the name",
                "again the name",
            )
        )

    def _format_booking_review(self, session: SessionState) -> str:
        mem = session.memory
        names = [
            str(t.get("name")).strip()
            for t in session.travelers
            if t.get("name")
        ]
        if names:
            numbered = ", ".join(f"{i}. {n}" for i, n in enumerate(names, 1))
            names_bit = f"Passengers ({len(names)}): {numbered}."
            session.last_offerings["travelers"] = [
                {"name": n, "index": i} for i, n in enumerate(names, 1)
            ]
        else:
            names_bit = "No passenger names saved yet."
        phone_bit = (
            f" Contact mobile: {mem.contact_phone}."
            if mem.contact_phone
            else " Contact mobile not set yet."
        )
        meal_bit = (
            f" Meal: {mem.meal_preference}."
            if mem.meal_preference
            else ""
        )
        allergy_bit = f" Allergies: {mem.allergies}." if mem.allergies else ""
        train = session.itinerary_selections.get("train") or {}
        train_bit = ""
        if train.get("name") or mem.selected_train_id:
            train_bit = (
                f" Train: {train.get('name') or mem.selected_train_id}"
                f"{(' · ' + str(train.get('departure_time'))) if train.get('departure_time') else ''}"
                f"{(' → ' + str(train.get('arrival_time'))) if train.get('arrival_time') else ''}."
            )
        return (
            f"{names_bit}{phone_bit}{meal_bit}{allergy_bit}{train_bit} "
            "Say yes when you want me to confirm booking and open payment."
        )

    async def _handle_booking_confirm(
        self, session: SessionState, user_text: str, tools_used: list[str]
    ) -> Optional[tuple[str, dict[str, Any]]]:
        """On clear yes, create booking via Travel Backend API."""
        if not self._user_said_yes(user_text):
            return None
        mem = session.memory

        # Full coach / charter → request_charter API
        if mem.booking_mode == "charter":
            if not (mem.source and mem.destination and mem.departure_date):
                return None
            if len(session.travelers) < self._needed_passenger_count(session):
                return (
                    self._ask_next_passenger(session),
                    {},
                )
            args = {
                "source": mem.source,
                "destination": mem.destination,
                "departure_date": mem.departure_date,
                "passengers": self._needed_passenger_count(session),
                "charter_type": mem.charter_type or "full_coach",
                "event_type": mem.trip_purpose,
                "contact_name": mem.passenger_name
                or (session.travelers[0]["name"] if session.travelers else None),
                "contact_phone": mem.contact_phone,
                "traveler_names": [t.get("name") for t in session.travelers if t.get("name")],
                "notes": f"meal={mem.meal_preference}; catering_full={mem.catering_full_train}",
            }
            result = await self._tools.execute(
                "request_charter", args, session_id=session.session_id
            )
            tools_used.append("request_charter")
            self._update_memory_from_tool(session, "request_charter", args, result)
            if not isinstance(result, dict) or result.get("error"):
                return (
                    "I could not submit the coach request. Please try again.",
                    {},
                )
            mem.booking_confirmation = True
            mem.pre_booking_step = "done"
            rid = result.get("request_id") or "CHARTER"
            seats = result.get("seats_per_coach") or FULL_COACH_SEATS
            return (
                f"Coach request {rid} is confirmed with SabRaah sales "
                f"({seats} seats per coach). They will contact you shortly.",
                {},
            )

        # Normal / itinerary / journey train booking
        item_id = (
            mem.selected_train_id
            or mem.selected_option
            or (session.itinerary_selections.get("train") or {}).get("id")
        )
        if not item_id:
            return None
        total = self._needed_passenger_count(session)
        if len(session.travelers) < total:
            mem.pre_booking_step = "passengers"
            return (self._ask_next_passenger(session), {})
        if not mem.contact_phone:
            mem.pre_booking_step = "phone"
            return ("What is the contact mobile number?", {})
        if not mem.passenger_name and session.travelers:
            mem.passenger_name = session.travelers[0].get("name")
        if not mem.passenger_name:
            return ("I need at least one passenger name before booking.", {})

        # Avoid double-booking same turn/session
        if mem.last_booking_id and mem.booking_confirmation:
            return (
                f"Booking {mem.last_booking_id} is already confirmed. Opening payment.",
                {
                    "booking_id": mem.last_booking_id,
                    "payment_amount": None,
                    "payment_currency": "INR",
                    "booking_snapshot": {
                        "passenger_name": mem.passenger_name,
                        "phone": mem.contact_phone,
                        "train_id": str(item_id),
                    },
                },
            )

        train_sel = session.itinerary_selections.get("train") or {}
        if not train_sel:
            train_sel = next(
                (
                    row
                    for row in session.last_search_results
                    if isinstance(row, dict) and str(row.get("id")) == str(item_id)
                ),
                {},
            )
        avail = str(
            train_sel.get("availability_status")
            or (session.last_offerings.get("train_details") or {}).get(
                "availability_status"
            )
            or "AVAILABLE"
        ).upper()
        if avail == "NOT_AVAILABLE":
            return (
                "That train has no seats left. Please choose another option.",
                {},
            )
        if avail in {"RAC", "WL"} and not mem.accept_waitlist:
            if mem.pre_booking_step == "waitlist_confirm":
                # Second yes = accept RAC/WL
                mem.accept_waitlist = True
            else:
                mem.pre_booking_step = "waitlist_confirm"
                return (
                    (
                        f"This train is {avail} only. "
                        f"{train_sel.get('availability_message') or ''} "
                        "Say yes if you want to book on RAC/waiting list anyway."
                    ),
                    {},
                )
        args = {
            "item_type": "train",
            "item_id": str(item_id),
            "passenger_name": mem.passenger_name,
            "passenger_count": total,
            "contact_phone": mem.contact_phone,
            "confirmed": True,
            "metadata": {
                "source": mem.source or train_sel.get("source"),
                "destination": mem.destination or train_sel.get("destination"),
                "departure_date": mem.departure_date,
                "train_name": train_sel.get("name"),
                "departure_time": train_sel.get("departure_time"),
                "arrival_time": train_sel.get("arrival_time"),
                "duration": train_sel.get("duration"),
                "travel_class": train_sel.get("class")
                or train_sel.get("travel_class")
                or mem.travel_class,
                "meal_preference": mem.meal_preference,
                "allergies": mem.allergies,
                "traveler_names": [
                    t.get("name") for t in session.travelers if t.get("name")
                ],
                "booking_mode": mem.booking_mode or "normal",
                "accept_waitlist": bool(mem.accept_waitlist) or avail in {"RAC", "WL"},
            },
        }
        result = await self._tools.execute(
            "create_booking", args, session_id=session.session_id
        )
        tools_used.append("create_booking")
        self._update_memory_from_tool(session, "create_booking", args, result)

        if not isinstance(result, dict) or result.get("error") or not result.get(
            "booking_id"
        ):
            msg = (
                (result or {}).get("message")
                if isinstance(result, dict)
                else None
            ) or "Booking could not be confirmed on the server. Please try again."
            return (msg, {})

        booking_id = str(result["booking_id"])
        mem.last_booking_id = booking_id
        mem.pnr = booking_id
        mem.booking_confirmation = True
        mem.pre_booking_step = "done"
        meta = result.get("metadata") or {}
        meta = {**meta, "pnr": booking_id}
        session.booking_details = meta
        session.last_offerings["booking"] = {"booking_id": booking_id, "pnr": booking_id, **meta}

        platform = meta.get("platform") or "—"
        train_name = meta.get("train_name") or train_sel.get("name") or item_id
        dep = meta.get("departure_time") or train_sel.get("departure_time") or ""
        arr = meta.get("arrival_time") or train_sel.get("arrival_time") or ""
        travel_class = (
            meta.get("travel_class")
            or train_sel.get("class")
            or mem.travel_class
            or ""
        )
        seats = meta.get("seats") or []
        seat_bit = ""
        if seats:
            labels = [
                f"{s.get('passenger')}: {s.get('seat_label') or s.get('berth')}"
                for s in seats
                if isinstance(s, dict)
            ]
            seat_bit = " Seats: " + "; ".join(labels[:8]) + "."
        names = meta.get("traveler_names") or [
            t.get("name") for t in session.travelers if t.get("name")
        ]
        pax_bit = f" Passengers: {', '.join(str(n) for n in names)}." if names else ""
        meal_bit = (
            f" Meal: {mem.meal_preference}." if mem.meal_preference else ""
        )
        amount = result.get("total_price")
        amount_bit = f" Amount INR {int(float(amount))}." if amount is not None else ""
        schedule = ""
        if dep or arr:
            schedule = f" Departs {dep}" + (f", arrives {arr}." if arr else ".")
        text = (
            f"Booking {booking_id} confirmed for {train_name} "
            f"({mem.source or ''} to {mem.destination or ''})"
            f"{(' on ' + mem.departure_date) if mem.departure_date else ''}. "
            f"Class {travel_class or '—'}. Platform {platform}.{schedule}"
            f"{seat_bit}{pax_bit}{meal_bit}{amount_bit} "
            "Opening the payment screen with full train details."
        )
        return (
            text,
            {
                "booking_id": booking_id,
                "payment_amount": float(amount) if amount is not None else None,
                "payment_currency": str(result.get("currency") or "INR"),
                "booking_snapshot": {
                    "passenger_name": result.get("passenger_name") or mem.passenger_name,
                    "phone": mem.contact_phone,
                    "train_id": str(item_id),
                    "train_name": train_name,
                    "traveler_names": names,
                    "platform": platform,
                    "seats": seats,
                    "departure_time": dep,
                    "arrival_time": arr,
                    "duration": meta.get("duration") or train_sel.get("duration"),
                    "travel_class": travel_class,
                    "source_station": meta.get("source_station"),
                    "destination_station": meta.get("destination_station"),
                    "meal_preference": mem.meal_preference,
                },
            },
        )

    async def _handle_flight_pick(
        self, session: SessionState, user_text: str, tools_used: list[str]
    ) -> Optional[str]:
        mem = session.memory
        flights = session.last_offerings.get("flights") or []
        if not flights or mem.selected_flight_id:
            return None
        matched = self._match_option(user_text, flights)
        if matched is None:
            return None
        flight_id = str(matched.get("id") or "")
        mem.selected_flight_id = flight_id
        mem.selected_option = flight_id
        session.itinerary_selections["flight"] = matched
        selection = matched.get("selection") if isinstance(matched.get("selection"), dict) else {}
        result = await self._tools.execute(
            "create_booking",
            {
                "item_type": "flight",
                "item_id": flight_id,
                "confirmed": True,
                "passenger_name": mem.passenger_name
                or next(
                    (str(t.get("name")) for t in session.travelers if t.get("name")),
                    "Guest",
                ),
                "passenger_count": mem.passenger_count or 1,
                "metadata": {
                    "from_code": matched.get("from_code") or "",
                    "to_code": matched.get("to_code") or "",
                    "selection": selection
                    or {
                        "index": flight_id,
                        "order_id": 1,
                        "amount": str(matched.get("price") or ""),
                        "tui": matched.get("search_tui") or "",
                    },
                    "flight_fares": matched.get("flight_fares") or [],
                    "tui": matched.get("search_tui"),
                    "price": matched.get("price"),
                },
            },
            session_id=session.session_id,
        )
        tools_used.append("create_booking")
        self._update_memory_from_tool(
            session,
            "create_booking",
            {"item_type": "flight", "item_id": flight_id, "confirmed": True},
            result if isinstance(result, dict) else {},
        )
        url = result.get("booking_url") if isinstance(result, dict) else None
        label = matched.get("name") or "that flight"
        price = matched.get("price_label") or ""
        price_bit = f" {price}." if price else "."
        if url:
            session.last_offerings["flight_checkout"] = {"booking_url": url}
            mem.flow_step = "flight_open"
            return (
                f"Got {label}.{price_bit} "
                "I'm opening the flight checkout page so you can finish passenger details and payment."
            )
        return (
            (result.get("message") if isinstance(result, dict) else None)
            or f"Got {label}.{price_bit} Finish checkout on the Super Travel flights page."
        )

    async def _handle_sequential_choice(
        self, session: SessionState, user_text: str, tools_used: list[str]
    ) -> Optional[str]:
        mem = session.memory
        if mem.booking_mode == "journey":
            return await self._handle_journey_choice(session, user_text, tools_used)
        if mem.booking_mode == "itinerary":
            return await self._handle_itinerary_choice(session, user_text, tools_used)
        return None

    async def _handle_normal_booking_flow(
        self, session: SessionState, user_text: str, tools_used: list[str]
    ) -> Optional[str]:
        mem = session.memory
        if mem.booking_mode not in {None, "normal", "flight"}:
            return None
        if (
            mem.user_goal == "book_flight"
            or mem.transport_type == "flight"
            or mem.booking_mode == "flight"
        ):
            picked = await self._handle_flight_pick(session, user_text, tools_used)
            if picked is not None:
                return picked
            return await self._handle_flight_extras(session, user_text)
        total = self._needed_passenger_count(session)
        # Ready for confirm → leave to _handle_booking_confirm on "yes"
        if (
            mem.selected_train_id
            and (mem.meal_preference or mem.meal_asked)
            and len(session.travelers) >= total
            and mem.contact_phone
            and mem.pre_booking_step in {"confirm", "done", "phone"}
        ):
            if mem.pre_booking_step == "phone":
                mem.pre_booking_step = "confirm"
            if not self._user_said_yes(user_text) and mem.pre_booking_step == "confirm":
                if self._wants_booking_review(user_text):
                    return self._format_booking_review(session)
                names = [
                    str(t.get("name")).strip()
                    for t in session.travelers
                    if t.get("name")
                ]
                preview = ""
                if names:
                    preview = (
                        " Names on file: "
                        + ", ".join(names[:8])
                        + ("…" if len(names) > 8 else "")
                        + "."
                    )
                    session.last_offerings["travelers"] = [
                        {"name": n, "index": i}
                        for i, n in enumerate(names, 1)
                    ]
                return (
                    f"{total} passenger(s) ready.{preview} "
                    "Say yes to confirm booking on SabRaah backend and continue to payment. "
                    "Or say update meal, update name, or update phone."
                )
            return None

        trains = session.last_offerings.get("trains") or []
        # Never re-bind a train while collecting/fixing passenger details or phone.
        if (
            trains
            and not mem.selected_train_id
            and mem.pre_booking_step
            not in {
                "passengers",
                "phone",
                "confirm",
                "fix_name",
                "meals",
                "await_meal_update",
                "await_phone_update",
                "done",
            }
        ):
            matched = self._match_option(user_text, trains)
            if matched is None:
                return None
            session.itinerary_selections["train"] = matched
            mem.selected_train_id = str(matched.get("id") or "")
            mem.selected_option = mem.selected_train_id
            mem.accept_waitlist = False
            platform = matched.get("platform") or "—"
            station = matched.get("source_station") or mem.source or "the station"
            session.last_offerings["train_details"] = matched
            avail = str(matched.get("availability_status") or "AVAILABLE").upper()
            avail_msg = matched.get("availability_message") or ""
            seat_note = ""
            if avail == "AVAILABLE":
                seat_note = f" Availability: confirmed seats ({matched.get('available_seats', '—')} CNF)."
            elif avail == "RAC":
                seat_note = (
                    f" Only RAC is open ({matched.get('rac_seats', '—')} RAC). "
                    "I will ask again before waitlist/RAC booking."
                )
            elif avail == "WL":
                seat_note = (
                    f" Only waiting list is open (WL/{matched.get('waiting_list', '—')}). "
                    "I will ask before booking on waitlist."
                )
            elif avail == "NOT_AVAILABLE":
                return (
                    f"{matched.get('name') or 'That train'} has no seats left "
                    "(not available). Please pick another option on screen."
                )
            mem.pre_booking_step = "meals"
            if not mem.meal_preference and not mem.meal_asked:
                mem.meal_asked = True
                return (
                    f"Got it — {matched.get('name') or 'your train'} "
                    f"from {station}, platform {platform}.{seat_note} "
                    "Meal once — veg, non-veg, jain, or no meal? Any allergies?"
                )
            mem.pre_booking_step = "passengers"
            if not mem.meal_preference:
                mem.meal_preference = "none"
            return seat_note.strip() + " " + self._ask_next_passenger(session)

        if (
            mem.selected_train_id
            and not mem.meal_preference
            and mem.pre_booking_step == "meals"
        ):
            text = user_text.lower()
            if any(w in text for w in ("veg", "meal", "jain", "allerg", "no meal", "non", "skip")):
                if "jain" in text:
                    mem.meal_preference = "jain"
                elif "non" in text:
                    mem.meal_preference = "non_veg"
                elif "no meal" in text or "skip" in text:
                    mem.meal_preference = "none"
                else:
                    mem.meal_preference = "veg"
                if "allerg" in text:
                    mem.allergies = user_text.strip()[:200]
                mem.meal_asked = True
                mem.pre_booking_step = "passengers"
                return self._ask_next_passenger(session)
            # Already asked once — don't loop; default no meal and continue.
            if mem.meal_asked:
                mem.meal_preference = "none"
                mem.pre_booking_step = "passengers"
                return self._ask_next_passenger(session)

        # Phone after all passenger names
        if (
            mem.selected_train_id
            and (mem.meal_preference or mem.meal_asked)
            and len(session.travelers) >= total
            and mem.pre_booking_step == "phone"
            and not mem.contact_phone
        ):
            digits = re.sub(r"\D", "", user_text)
            if len(digits) >= 10:
                mem.contact_phone = digits[-10:]
                mem.pre_booking_step = "confirm"
                return (
                    f"Thanks. {total} passenger(s) ready. "
                    "Shall I continue to payment?"
                )
            return "Please share a 10-digit mobile number."
        return None

    @staticmethod
    def _wants_skip_flight(user_text: str) -> bool:
        text = user_text.lower()
        return any(
            p in text
            for p in ("skip flight", "no flight", "without flight", "train only leg")
        )

    @staticmethod
    def _wants_skip_local(user_text: str) -> bool:
        text = user_text.lower()
        return any(
            p in text
            for p in ("skip local", "no cab", "no car", "skip cab", "skip transport")
        )

    async def _handle_journey_choice(
        self, session: SessionState, user_text: str, tools_used: list[str]
    ) -> Optional[str]:
        mem = session.memory
        step = mem.itinerary_step
        if not step or step == "summary":
            return None

        if step == "flights" and self._wants_skip_flight(user_text):
            mem.skip_flight = True
            session.itinerary_selections.pop("flight", None)
            mem.selected_flight_id = None
            mem.itinerary_step = "trains"
            await self._ensure_step_cached(session, "trains", tools_used)
            trains = session.itinerary_cache.get("trains") or []
            session.last_offerings = {"trains": trains}
            session.last_search_results = trains
            return STEP_SCREEN_LINES["trains"]

        if step == "local" and self._wants_skip_local(user_text):
            mem.skip_local = True
            session.itinerary_selections.pop("local", None)
            mem.selected_local_id = None
            mem.itinerary_step = "hotels"
            await self._ensure_step_cached(session, "hotels", tools_used)
            hotels = session.itinerary_cache.get("hotels") or []
            session.last_offerings = {"hotels": hotels}
            session.last_search_results = hotels
            return STEP_SCREEN_LINES["hotels"]

        rows = (
            (session.last_offerings or {}).get(step)
            or session.itinerary_cache.get(step)
            or []
        )
        matched = self._match_option(user_text, rows)
        if matched is None:
            return None

        if step == "flights":
            session.itinerary_selections["flight"] = matched
            mem.selected_flight_id = str(matched.get("id") or "")
            mem.skip_flight = False
            mem.itinerary_step = "trains"
            await self._ensure_step_cached(session, "trains", tools_used)
            trains = session.itinerary_cache.get("trains") or []
            session.last_offerings = {"trains": trains}
            session.last_search_results = trains
            return STEP_SCREEN_LINES["trains"]

        if step == "trains":
            session.itinerary_selections["train"] = matched
            mem.selected_train_id = str(matched.get("id") or "")
            platform = matched.get("platform") or "—"
            mem.itinerary_step = "local"
            await self._ensure_step_cached(session, "local", tools_used)
            local = session.itinerary_cache.get("local") or []
            session.last_offerings = {"trains": [matched], "local": local}
            session.last_search_results = local
            return (
                f"Train {matched.get('name')} is on platform {platform}. "
                + STEP_SCREEN_LINES["local"]
            )

        if step == "local":
            session.itinerary_selections["local"] = matched
            mem.selected_local_id = str(matched.get("id") or "")
            mem.skip_local = False
            mem.itinerary_step = "hotels"
            await self._ensure_step_cached(session, "hotels", tools_used)
            hotels = session.itinerary_cache.get("hotels") or []
            session.last_offerings = {"hotels": hotels}
            session.last_search_results = hotels
            return STEP_SCREEN_LINES["hotels"]

        if step == "hotels":
            session.itinerary_selections["hotel"] = matched
            mem.selected_hotel_id = str(matched.get("id") or "")
            mem.itinerary_step = "summary"
            plan = self._build_journey_summary(session)
            session.itinerary_cache["plan"] = plan
            session.last_offerings = {"plan": plan}
            session.last_search_results = [plan]
            return self._spoken_journey_summary(session, plan)
        return None

    def _build_journey_summary(self, session: SessionState) -> dict[str, Any]:
        flight = session.itinerary_selections.get("flight")
        train = session.itinerary_selections.get("train") or {}
        local = session.itinerary_selections.get("local")
        hotel = session.itinerary_selections.get("hotel") or {}
        mem = session.memory
        total = sum(
            self._item_price(x)
            for x in (flight, train, local, hotel)
            if isinstance(x, dict)
        )
        includes = []
        if flight:
            includes.append(
                {
                    "type": "flight",
                    "label": "Flight",
                    "name": flight.get("airline") or flight.get("name"),
                    "detail": f"{flight.get('departure_time')} → {flight.get('arrival_time')}",
                }
            )
        else:
            includes.append({"type": "flight", "label": "Flight", "name": "Skipped", "detail": ""})
        includes.append(
            {
                "type": "train",
                "label": "Train",
                "name": train.get("name"),
                "detail": " · ".join(
                    p
                    for p in [
                        f"Platform {train.get('platform')}" if train.get("platform") else None,
                        f"{train.get('departure_time')} → {train.get('arrival_time')}",
                    ]
                    if p
                ),
            }
        )
        if local:
            includes.append(
                {
                    "type": "local",
                    "label": "Local",
                    "name": local.get("name"),
                    "detail": local.get("type") or "cab",
                }
            )
        else:
            includes.append({"type": "local", "label": "Local", "name": "Skipped", "detail": ""})
        includes.append(
            {
                "type": "hotel",
                "label": "Hotel",
                "name": hotel.get("name"),
                "detail": hotel.get("city") or "",
            }
        )
        trip_label = "Round trip" if mem.trip_type == "round_trip" else "One-way journey"
        if mem.trip_purpose:
            trip_label += f" · {mem.trip_purpose}"
        return {
            "id": "JOURNEY-001",
            "summary": f"{trip_label}: {mem.source} → {mem.destination}",
            "source": mem.source,
            "destination": mem.destination,
            "departure_date": mem.departure_date,
            "return_date": mem.return_date,
            "flight": flight,
            "train": train,
            "local": local,
            "hotel": hotel,
            "includes": includes,
            "estimated_total": total,
            "currency": "INR",
        }

    def _spoken_journey_summary(self, session: SessionState, plan: dict[str, Any]) -> str:
        parts = []
        if plan.get("flight") and isinstance(plan["flight"], dict):
            parts.append(f"flight {plan['flight'].get('airline') or plan['flight'].get('name')}")
        train = plan.get("train") or {}
        if train.get("name"):
            parts.append(f"train {train['name']} platform {train.get('platform') or '—'}")
        if plan.get("local") and isinstance(plan["local"], dict):
            parts.append(f"local {plan['local'].get('name')}")
        hotel = plan.get("hotel") or {}
        if hotel.get("name"):
            parts.append(f"hotel {hotel['name']}")
        joined = ", ".join(parts) if parts else "your selections"
        rt = " Round trip." if session.memory.trip_type == "round_trip" else ""
        return (
            f"Your journey is ready: {joined}.{rt} "
            "Full details are on screen. Say yes for passenger details and booking."
        )

    async def _handle_itinerary_choice(
        self, session: SessionState, user_text: str, tools_used: list[str]
    ) -> Optional[str]:
        mem = session.memory
        if mem.booking_mode != "itinerary":
            return None
        step = mem.itinerary_step
        if not step or step == "summary":
            return None

        if step == "buses" and self._wants_skip_bus(user_text):
            mem.skip_bus = True
            session.itinerary_selections.pop("bus", None)
            mem.selected_bus_id = None
            mem.itinerary_step = "hotels"
            await self._ensure_step_cached(session, "hotels", tools_used)
            hotels = session.itinerary_cache.get("hotels") or []
            session.last_offerings = {"hotels": hotels}
            session.last_search_results = hotels
            return STEP_SCREEN_LINES["hotels"]

        rows = (
            (session.last_offerings or {}).get(step)
            or session.itinerary_cache.get(step)
            or []
        )
        matched = self._match_option(user_text, rows)
        if matched is None:
            return None

        if step == "trains":
            session.itinerary_selections["train"] = matched
            mem.selected_train_id = str(matched.get("id") or "")
            mem.selected_option = mem.selected_train_id
            mem.itinerary_step = "buses"
            await self._ensure_step_cached(session, "buses", tools_used)
            buses = session.itinerary_cache.get("buses") or []
            session.last_offerings = {"buses": buses}
            session.last_search_results = buses
            return STEP_SCREEN_LINES["buses"]

        if step == "buses":
            session.itinerary_selections["bus"] = matched
            mem.selected_bus_id = str(matched.get("id") or "")
            mem.skip_bus = False
            mem.itinerary_step = "hotels"
            await self._ensure_step_cached(session, "hotels", tools_used)
            hotels = session.itinerary_cache.get("hotels") or []
            session.last_offerings = {"hotels": hotels}
            session.last_search_results = hotels
            return STEP_SCREEN_LINES["hotels"]

        if step == "hotels":
            session.itinerary_selections["hotel"] = matched
            mem.selected_hotel_id = str(matched.get("id") or "")
            mem.itinerary_step = "summary"
            plan = self._build_itinerary_summary(session)
            session.itinerary_cache["plan"] = plan
            session.last_offerings = {"plan": plan}
            session.last_search_results = [plan]
            return self._spoken_package_summary(session, plan)
        return None

    def _normalize_sequential_offerings(self, session: SessionState) -> Optional[str]:
        mem = session.memory
        if mem.booking_mode == "journey":
            return self._normalize_journey_offerings(session)
        if mem.booking_mode == "itinerary":
            return self._normalize_itinerary_offerings(session)
        return None

    def _normalize_journey_offerings(self, session: SessionState) -> Optional[str]:
        mem = session.memory
        for key in ("flights", "trains", "local", "hotels"):
            if key in session.last_offerings and session.last_offerings[key]:
                session.itinerary_cache[key] = session.last_offerings[key]

        if mem.itinerary_step == "summary":
            plan = session.itinerary_cache.get("plan") or self._build_journey_summary(
                session
            )
            session.last_offerings = {"plan": plan}
            return None

        if not mem.itinerary_step:
            if session.itinerary_cache.get("flights") or session.last_offerings.get(
                "flights"
            ):
                mem.itinerary_step = "flights"
            elif session.itinerary_cache.get("trains") or session.last_offerings.get(
                "trains"
            ):
                mem.itinerary_step = "trains"
            else:
                return None

        step = mem.itinerary_step or "flights"
        if step not in {"flights", "trains", "local", "hotels"}:
            return None

        rows = session.itinerary_cache.get(step) or session.last_offerings.get(step)
        if not rows:
            return None

        session.last_offerings = {step: rows}
        session.last_search_results = rows
        return STEP_SCREEN_LINES.get(step, SCREEN_OPTIONS_LINE)

    def _normalize_itinerary_offerings(self, session: SessionState) -> Optional[str]:
        """After LLM tools, keep only the current itinerary step visible."""
        mem = session.memory
        if mem.booking_mode != "itinerary":
            return None

        for key in ("trains", "buses", "hotels"):
            if key in session.last_offerings and session.last_offerings[key]:
                session.itinerary_cache[key] = session.last_offerings[key]

        if mem.itinerary_step == "summary":
            plan = session.itinerary_cache.get("plan") or self._build_itinerary_summary(
                session
            )
            session.last_offerings = {"plan": plan}
            return None

        # Start sequential flow when train results exist.
        if not mem.itinerary_step:
            if session.itinerary_cache.get("trains") or session.last_offerings.get(
                "trains"
            ):
                mem.itinerary_step = "trains"
            else:
                return None

        step = mem.itinerary_step or "trains"
        if step not in {"trains", "buses", "hotels"}:
            return None

        rows = session.itinerary_cache.get(step) or session.last_offerings.get(step)
        if not rows:
            # If LLM fetched everything, prefer trains first.
            if step == "trains" and session.itinerary_cache.get("trains"):
                rows = session.itinerary_cache["trains"]
            else:
                return None

        session.last_offerings = {step: rows}
        session.last_search_results = rows
        return STEP_SCREEN_LINES.get(step, SCREEN_OPTIONS_LINE)


def _safe_json(payload: Any) -> str:
    import json

    try:
        return json.dumps(payload, default=str)
    except TypeError:
        return str(payload)
