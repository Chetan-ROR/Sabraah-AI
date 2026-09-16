"""Pytest configuration for Sabrah AI."""

import os

# Ensure required env vars exist before app imports.
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("ELEVENLABS_API_KEY", "test-elevenlabs-key")
os.environ.setdefault("ELEVENLABS_VOICE_ID", "test-voice")
os.environ.setdefault("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
os.environ.setdefault("TRAVEL_BACKEND_API_KEY", "test-api-key")
os.environ.setdefault("TRAVEL_BACKEND_BASE_URL", "http://127.0.0.1:8001")
