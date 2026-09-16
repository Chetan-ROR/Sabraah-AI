"""Conversation agent with full OpenAI tool-calling loop."""

from __future__ import annotations

import base64
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
    "Hi, I'm Sabrah. What would you like to do — "
    "book a train, cancel a ticket, or request a refund?"
)

PURPOSE_ASK_LINE = (
    "Why are you traveling? "
    "You can say wedding, baraat, personal work, tourism, conference, or other."
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

    async def handle_voice(
        self,
        session_id: str,
        audio_bytes: bytes,
        filename: str = "audio.webm",
    ) -> ChatResponse:
        session = await self._require_session(session_id)
        user_text = await self._stt.transcribe(audio_bytes, filename=filename)
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
        text = user_text.lower()
        mem = session.memory

        # Soft preference / trip signals (always update when mentioned).
        if "cheapest" in text or "lowest price" in text:
            mem.train_preference = "cheapest"
        elif "fastest" in text or "quickest" in text:
            mem.train_preference = "fastest"
        elif " ac" in f" {text}" or text.startswith("ac ") or "comfort" in text:
            mem.train_preference = "ac"
        elif "wishlist" in text:
            mem.train_preference = "wishlist"

        if "round trip" in text or "round-trip" in text or "return ticket" in text:
            mem.trip_type = "round_trip"
        elif "multi city" in text or "multi-city" in text:
            mem.trip_type = "multi_city"

        for purpose, keys in (
            ("wedding", ("wedding",)),
            ("baraat", ("baraat", "baraat")),
            ("personal_work", ("personal work", "office work", "work trip", "business")),
            ("tourism", ("tourism", "holiday", "vacation", "leisure")),
            ("conference", ("conference", "meeting", "seminar")),
            ("other", ("other reason", "something else")),
        ):
            if any(k in text for k in keys):
                mem.trip_purpose = purpose
                break

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
                            name, args, session_id=session.session_id
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
            if session.memory.booking_mode in {None, "normal"}:
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
            "दूसरा": "2",
            "दूसरे": "2",
            "दूसरी": "2",
            "सेकंड": "2",
            "second": "2",
            "तीसरा": "3",
            "तीसरे": "3",
            "तीसरी": "3",
            "थर्ड": "3",
            "third": "3",
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
            r"\b(?:option|train|bus|hotel|flight)\s*\d+\b",
            raw_l,
        ):
            # Only allow explicit "option 2" / "train 2" style choices here.
            numbered = re.search(
                r"\b(?:option|train|bus|hotel|flight|package)\s*(?:number\s*)?(\d+)\b",
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
            r"(?:option|train|bus|hotel|flight|package|number|no|num)?\s*(\d+)",
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

    @staticmethod
    def _parse_route_from_text(user_text: str) -> tuple[Optional[str], Optional[str]]:
        text = " ".join((user_text or "").strip().split())
        patterns = [
            r"(?:route\s+is|from)\s+([A-Za-z][A-Za-z\s]+?)\s+(?:to|→|->)\s+([A-Za-z][A-Za-z\s]+?)(?:\s+and\b|\s+on\b|\s+travel\b|[.,]|$)",
            r"\b([A-Za-z][A-Za-z\s]{1,30}?)\s+(?:to|→|->)\s+([A-Za-z][A-Za-z\s]{1,30}?)(?:\s+and\b|\s+on\b|\s+travel\b|[.,]|$)",
        ]
        for pat in patterns:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if not m:
                continue
            src = m.group(1).strip(" .,")
            dst = m.group(2).strip(" .,")
            # Drop trailing date words accidentally captured
            dst = re.sub(
                r"\b(and|travel|date|is|on|for|by)\b.*$",
                "",
                dst,
                flags=re.IGNORECASE,
            ).strip(" .,")
            if len(src) >= 2 and len(dst) >= 2:
                return src.title(), dst.title()
        return None, None

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
        # Conversational only — no option cards on screen.
        session.last_offerings.pop("goals", None)
        session.last_offerings.pop("purposes", None)
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

    async def _handle_guided_flow(
        self, session: SessionState, user_text: str, tools_used: list[str]
    ) -> Optional[str]:
        """Simple chat flow: goal → where → why → when → passengers; or PNR cancel/refund."""
        mem = session.memory
        text = (user_text or "").strip().lower()

        # Greeting → ask what they want (no option cards).
        if self._is_greeting(user_text) and not mem.user_goal:
            return self._show_main_menu(session)

        # Capture goal from speech only (no Option 1/2 cards).
        if not mem.user_goal or mem.flow_step == "goal":
            matched_goal = None
            if any(
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
            if matched_goal:
                mem.user_goal = matched_goal
                mem.flow_step = "where" if matched_goal == "book_train" else matched_goal
                if matched_goal == "book_train":
                    mem.booking_mode = mem.booking_mode or "normal"
                    mem.intent = "book"
                    return "Where would you like to go? Say it like Pune to Delhi."
                if matched_goal == "charter":
                    mem.booking_mode = "charter"
                    mem.intent = "charter"
                    return (
                        "Sure — full coach for large groups. "
                        "Tell me the route, date, and how many people."
                    )
                if matched_goal in {"cancel", "refund"}:
                    mem.intent = "support"
                    return (
                        f"Okay, {matched_goal}. "
                        "Please share your PNR or booking ID, like BK-XXXXXXXX."
                    )

        # Still no goal and no route yet → ask again in chat (no cards).
        if not mem.user_goal and not (mem.source and mem.destination):
            if mem.flow_step != "goal":
                return self._show_main_menu(session)
            # Already on goal step; if they said something unclear, nudge once.
            if not self._is_greeting(user_text):
                return (
                    "Please say book a train, cancel a ticket, or request a refund."
                )

        # Cancel / refund by PNR
        if mem.user_goal in {"cancel", "refund"} or mem.intent == "support":
            return await self._handle_pnr_support(session, user_text, tools_used)

        # Book train guided slots: where → why → when → count
        if mem.user_goal in {None, "book_train"} or mem.booking_mode in {
            None,
            "normal",
        }:
            if mem.user_goal is None and (mem.source or mem.destination):
                mem.user_goal = "book_train"
                mem.booking_mode = mem.booking_mode or "normal"

            src, dst = self._parse_route_from_text(user_text)
            if src and dst:
                mem.source = src
                mem.destination = dst
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

            if mem.source and mem.destination and not mem.trip_purpose:
                return self._show_purpose_menu(session)

            if mem.source and mem.destination and mem.trip_purpose and not mem.departure_date:
                mem.flow_step = "when"
                return (
                    f"Noted — {mem.trip_purpose.replace('_', ' ')}. "
                    "When do you want to travel? Say a date like 20 September 2026 or tomorrow."
                )

            if (
                mem.source
                and mem.destination
                and mem.trip_purpose
                and mem.departure_date
                and not mem.passenger_count
            ):
                # Same turn may include the count ("two passengers").
                parsed = self._parse_passenger_count(user_text)
                if parsed:
                    mem.passenger_count = parsed
                else:
                    mem.flow_step = "passengers"
                    session.last_offerings.pop("goals", None)
                    return "How many passengers? You can say 2 or two passengers."

            # Ready to search once — auto normal booking, no mode quiz.
            if (
                mem.source
                and mem.destination
                and mem.trip_purpose
                and mem.departure_date
                and mem.passenger_count
                and not mem.selected_train_id
                and not (session.last_offerings.get("trains") or [])
            ):
                mem.booking_mode = mem.booking_mode or "normal"
                mem.flow_step = "search"
                session.last_offerings.pop("goals", None)
                session.last_offerings.pop("purposes", None)
                args = {
                    "source": mem.source,
                    "destination": mem.destination,
                    "departure_date": mem.departure_date,
                    "passengers": min(int(mem.passenger_count), 9),
                    "preference": mem.train_preference or "balanced",
                }
                result = await self._tools.execute(
                    "search_trains", args, session_id=session.session_id
                )
                tools_used.append("search_trains")
                self._update_memory_from_tool(session, "search_trains", args, result)
                if isinstance(result, dict) and result.get("results"):
                    return (
                        f"Got it — {mem.passenger_count} passenger(s). "
                        f"Trains from {mem.source} to {mem.destination} are on your screen. "
                        "Each option shows per-person price and total. "
                        "Tell me which option you want."
                    )
                return (
                    "I could not find trains for that route. "
                    "Please try another city pair."
                )

        return None

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
        if mem.booking_mode not in {None, "normal"}:
            return None
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
