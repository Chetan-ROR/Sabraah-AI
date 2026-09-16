"""Application configuration."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="Sabrah Travel Backend", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    host: str = Field(default="127.0.0.1", alias="HOST")
    port: int = Field(default=8001, alias="PORT")
    api_key: str = Field(default="local-development-key", alias="API_KEY")
    train_provider: str = Field(default="mock", alias="TRAIN_PROVIDER")
    flight_provider: str = Field(default="mock", alias="FLIGHT_PROVIDER")
    bus_provider: str = Field(default="mock", alias="BUS_PROVIDER")
    hotel_provider: str = Field(default="mock", alias="HOTEL_PROVIDER")
    package_provider: str = Field(default="mock", alias="PACKAGE_PROVIDER")
    # Super Travel (api-repository) — used when TRAIN_PROVIDER=real
    super_travel_api_base_url: str = Field(
        default="http://127.0.0.1:8002",
        alias="SUPER_TRAVEL_API_BASE_URL",
    )
    super_travel_timeout_seconds: float = Field(
        default=8.0,
        alias="SUPER_TRAVEL_TIMEOUT_SECONDS",
    )
    train_provider_fallback_mock: bool = Field(
        default=True,
        alias="TRAIN_PROVIDER_FALLBACK_MOCK",
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    def validate_required(self) -> None:
        if not self.api_key.strip():
            raise RuntimeError(
                "API_KEY is required. Set it in sabrah-travel-backend/.env"
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_required()
    return settings
