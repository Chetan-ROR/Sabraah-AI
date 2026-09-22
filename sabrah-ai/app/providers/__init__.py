"""Provider interfaces and implementations for STT, LLM, and TTS."""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Optional, Union

import httpx
from openai import AsyncOpenAI, AuthenticationError, RateLimitError

from app.config import Settings

logger = logging.getLogger(__name__)

_STT_PROMPT = (
    "Hey Sabraah. Book a train. Book a flight. Book a hotel. Book an event. "
    "Thank you. Please. Bye. Pune to Delhi. Mumbai. Indore. Jaipur."
)

_STT_PROMPT_LEAKS = (
    "cities, dates, trains, hotels, passengers, options",
    "english travel booking conversation",
    "cities dates trains hotels passengers options",
)

_STT_LEAK_WORDS = {
    "cities",
    "dates",
    "trains",
    "hotels",
    "passengers",
    "options",
    "english",
    "travel",
    "booking",
    "conversation",
}

_WAKE_ONLY_RE = re.compile(
    r"^(?:hey|hi|hello|ok(?:ay)?|oye|yo)?\s*(?:sabrah+|sabraah+|sahara|saber+|sabre)$",
    re.IGNORECASE,
)


def _normalize_stt_text(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (text or "").lower()).split())


def is_wake_only_transcript(text: str) -> bool:
    cleaned = _normalize_stt_text(text)
    return bool(cleaned and _WAKE_ONLY_RE.match(cleaned))


def is_unusable_transcript(text: str) -> bool:
    """Drop Whisper prompt leaks. Wake-only audio is a greeting, not ignored."""
    cleaned = _normalize_stt_text(text)
    if not cleaned:
        return True
    if is_wake_only_transcript(text):
        return False
    for leak in _STT_PROMPT_LEAKS:
        leak_n = _normalize_stt_text(leak)
        if leak_n and (leak_n in cleaned or cleaned in leak_n):
            return True
    words = set(cleaned.split())
    if words and words <= _STT_LEAK_WORDS:
        return True
    return False


class ProviderError(Exception):
    """User-safe provider failure."""

    def __init__(self, message: str, *, code: str = "provider_error") -> None:
        super().__init__(message)
        self.code = code
        self.user_message = message


class SpeechToTextProvider(ABC):
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, filename: str = "audio.webm") -> str:
        raise NotImplementedError


class LLMProvider(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Union[str, dict[str, Any]] = "auto",
    ) -> Any:
        """Return a provider-native chat completion message/response object."""
        raise NotImplementedError


class TextToSpeechProvider(ABC):
    @abstractmethod
    async def synthesize(self, text: str) -> bytes:
        raise NotImplementedError


class OpenAISpeechToTextProvider(SpeechToTextProvider):
    def __init__(
        self, client: AsyncOpenAI, model: str, *, language: str = "en"
    ) -> None:
        self._client = client
        self._model = model
        self._language = language

    async def transcribe(self, audio_bytes: bytes, filename: str = "audio.webm") -> str:
        if not audio_bytes:
            raise ProviderError(
                "I could not hear anything. Please try speaking again.",
                code="empty_audio",
            )
        try:
            transcript = await self._client.audio.transcriptions.create(
                model=self._model,
                file=(filename, audio_bytes),
                language=self._language,
                prompt=_STT_PROMPT,
                temperature=0,
            )
        except AuthenticationError as exc:
            raise ProviderError(
                "Speech recognition is temporarily unavailable (invalid OpenAI key).",
                code="openai_auth",
            ) from exc
        except RateLimitError as exc:
            raise ProviderError(
                "Speech recognition is temporarily unavailable due to quota limits.",
                code="openai_quota",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("STT failed")
            raise ProviderError(
                "I could not understand the audio. Please try again.",
                code="stt_failed",
            ) from exc

        text = (getattr(transcript, "text", None) or "").strip()
        if not text:
            raise ProviderError(
                "I could not catch that. Could you please repeat?",
                code="speech_not_recognized",
            )
        if is_unusable_transcript(text) and not is_wake_only_transcript(text):
            raise ProviderError(
                "I could not catch that. Could you please repeat?",
                code="speech_not_recognized",
            )
        return text


class OpenAILLMProvider(LLMProvider):
    def __init__(self, client: AsyncOpenAI, model: str) -> None:
        self._client = client
        self._model = model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Union[str, dict[str, Any]] = "auto",
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.4,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        try:
            response = await self._client.chat.completions.create(**kwargs)
        except AuthenticationError as exc:
            raise ProviderError(
                "Conversation is temporarily unavailable (invalid OpenAI key).",
                code="openai_auth",
            ) from exc
        except RateLimitError as exc:
            raise ProviderError(
                "Conversation is temporarily unavailable due to OpenAI quota limits.",
                code="openai_quota",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("LLM chat failed")
            raise ProviderError(
                "I had trouble thinking just now. Please try again in a moment.",
                code="llm_failed",
            ) from exc
        return response.choices[0].message


class ElevenLabsTTSProvider(TextToSpeechProvider):
    def __init__(self, api_key: str, voice_id: str, model_id: str) -> None:
        self._api_key = api_key
        self._voice_id = voice_id
        self._model_id = model_id

    async def synthesize(self, text: str) -> bytes:
        if not text.strip():
            raise ProviderError("Nothing to speak.", code="empty_tts_text")
        url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/{self._voice_id}"
            "?output_format=mp3_44100_128"
        )
        headers = {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": text,
            "model_id": self._model_id,
            "voice_settings": {"stability": 0.4, "similarity_boost": 0.75},
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(
                "Voice playback is temporarily unavailable.",
                code="elevenlabs_network",
            ) from exc

        if response.status_code == 401:
            raise ProviderError(
                "Voice playback is unavailable (invalid ElevenLabs key).",
                code="elevenlabs_auth",
            )
        if response.status_code == 429:
            raise ProviderError(
                "Voice playback is unavailable due to ElevenLabs quota limits.",
                code="elevenlabs_quota",
            )
        if response.status_code >= 400:
            logger.error(
                "ElevenLabs TTS failed status=%s body=%s",
                response.status_code,
                response.text[:200],
            )
            raise ProviderError(
                "Voice playback failed. The text reply is still available.",
                code="elevenlabs_failed",
            )
        return response.content


class OpenAITTSProvider(TextToSpeechProvider):
    """Optional fallback TTS provider."""

    def __init__(self, client: AsyncOpenAI, model: str, voice: str = "alloy") -> None:
        self._client = client
        self._model = model
        self._voice = voice

    async def synthesize(self, text: str) -> bytes:
        try:
            response = await self._client.audio.speech.create(
                model=self._model,
                voice=self._voice,
                input=text,
            )
            return response.content
        except Exception as exc:  # noqa: BLE001
            logger.exception("OpenAI TTS failed")
            raise ProviderError(
                "Voice playback failed. The text reply is still available.",
                code="openai_tts_failed",
            ) from exc


def build_openai_client(settings: Settings) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key)


def build_stt_provider(settings: Settings, client: AsyncOpenAI) -> SpeechToTextProvider:
    return OpenAISpeechToTextProvider(
        client=client,
        model=settings.openai_stt_model,
        language=settings.openai_stt_language,
    )


def build_llm_provider(settings: Settings, client: AsyncOpenAI) -> LLMProvider:
    return OpenAILLMProvider(client=client, model=settings.openai_chat_model)


def build_tts_provider(
    settings: Settings, client: AsyncOpenAI
) -> TextToSpeechProvider:
    if settings.tts_provider == "openai":
        return OpenAITTSProvider(client=client, model=settings.openai_tts_model)
    return ElevenLabsTTSProvider(
        api_key=settings.elevenlabs_api_key,
        voice_id=settings.elevenlabs_voice_id,
        model_id=settings.elevenlabs_model_id,
    )
