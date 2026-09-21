# Sabrah AI

Voice-based AI travel assistant frontend + backend.

Responsibilities:

- Browser microphone capture
- OpenAI Speech-to-Text
- OpenAI GPT conversation + tool calling
- Calling Super Travel (`api-repository`) for flights, trains, hotels, events
- ElevenLabs Text-to-Speech
- Serving the voice UI

This project does **not** contain travel business logic or mock travel data.

## Requirements

- Python 3.11+
- OpenAI API key with billing enabled
- ElevenLabs API key + Voice ID
- Running `api-repository` on port 8002

## Installation

```bash
cd sabrah-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Environment

Fill `sabrah-ai/.env`:

```env
OPENAI_API_KEY=sk-...
OPENAI_CHAT_MODEL=gpt-4o-mini
OPENAI_STT_MODEL=whisper-1

ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
ELEVENLABS_MODEL_ID=eleven_multilingual_v2

TRAVEL_BACKEND_BASE_URL=http://127.0.0.1:8002
TRAVEL_BACKEND_API_KEY=
```

Search/list APIs on api-repository are anonymous. Login JWT is only needed for itinerary, payment, cancel, wishlist.

### OpenAI setup

1. Create a key at https://platform.openai.com/api-keys
2. Ensure the project has API credits (ChatGPT subscription is not enough)
3. Defaults: chat `gpt-4o-mini`, STT `whisper-1`

### ElevenLabs setup

1. Create an API key in the ElevenLabs dashboard
2. Open Voice Library → select a voice → copy Voice ID
3. Prefer `eleven_multilingual_v2` for English / Hindi / Hinglish
4. If TTS fails, Sabrah still returns the text reply when possible

## Run

Start **api-repository** on port 8002 first, then:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open: http://127.0.0.1:8000

**How to use (what to say, for which booking, and in what order):** [`../CONVERSATION_FLOW.md`](../CONVERSATION_FLOW.md)

## Testing without a microphone

1. Open the UI
2. Use the text input box (also available for debugging)
3. Or call:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/sessions
# then
curl -X POST http://127.0.0.1:8000/api/v1/chat/text \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"<id>","message":"Hi Sabrah"}'
```

## Microphone testing

1. Allow microphone permission when the browser asks
2. Click **Start Talking**
3. Speak, then click **Stop** (or wait for auto-stop)
4. Sabrah transcribes, thinks, speaks the reply

If permission is denied, the UI shows a clear error.

## Voice pipeline

```text
Browser MediaRecorder
  → POST /api/v1/chat/voice
  → OpenAI STT
  → OpenAI GPT (+ tools → Travel Backend)
  → ElevenLabs TTS
  → audio/mpeg playback in browser
```

## Tests

```bash
pytest -q
```

OpenAI and ElevenLabs are mocked — no API credits are used.

## Future Realtime support

Providers are abstracted (`SpeechToTextProvider`, `LLMProvider`, `TextToSpeechProvider`).
A future OpenAI Realtime speech-to-speech provider can replace STT+TTS without changing Travel Backend tools.
