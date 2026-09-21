"""Application settings."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="Sabrah AI", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    host: str = Field(default="127.0.0.1", alias="HOST")
    port: int = Field(default=8000, alias="PORT")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_chat_model: str = Field(default="gpt-4o-mini", alias="OPENAI_CHAT_MODEL")
    openai_stt_model: str = Field(default="whisper-1", alias="OPENAI_STT_MODEL")
    openai_stt_language: str = Field(default="en", alias="OPENAI_STT_LANGUAGE")
    openai_tts_model: str = Field(default="gpt-4o-mini-tts", alias="OPENAI_TTS_MODEL")

    elevenlabs_api_key: str = Field(default="", alias="ELEVENLABS_API_KEY")
    elevenlabs_voice_id: str = Field(default="", alias="ELEVENLABS_VOICE_ID")
    elevenlabs_model_id: str = Field(
        default="eleven_flash_v2_5", alias="ELEVENLABS_MODEL_ID"
    )

    travel_backend_base_url: str = Field(
        default="http://127.0.0.1:8002", alias="TRAVEL_BACKEND_BASE_URL"
    )
    travel_backend_api_key: str = Field(default="", alias="TRAVEL_BACKEND_API_KEY")
    travel_backend_timeout_seconds: float = Field(
        default=90.0, alias="TRAVEL_BACKEND_TIMEOUT_SECONDS"
    )
    super_travel_api_base_url: str = Field(
        default="http://127.0.0.1:8002", alias="SUPER_TRAVEL_API_BASE_URL"
    )
    super_travel_timeout_seconds: float = Field(
        default=20.0, alias="SUPER_TRAVEL_TIMEOUT_SECONDS"
    )
    web_app_base_url: str = Field(default="", alias="WEB_APP_BASE_URL")

    session_ttl_minutes: int = Field(default=60, alias="SESSION_TTL_MINUTES")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    tts_provider: str = Field(default="elevenlabs", alias="TTS_PROVIDER")

    @field_validator(
        "travel_backend_base_url", "super_travel_api_base_url", "web_app_base_url"
    )
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    def validate_required(self) -> None:
        missing: list[str] = []
        if not self.openai_api_key.strip():
            missing.append("OPENAI_API_KEY")
        if self.tts_provider == "elevenlabs":
            if not self.elevenlabs_api_key.strip():
                missing.append("ELEVENLABS_API_KEY")
            if not self.elevenlabs_voice_id.strip():
                missing.append("ELEVENLABS_VOICE_ID")
        if missing:
            raise RuntimeError(
                "Missing required environment variables: "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill in the values."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
