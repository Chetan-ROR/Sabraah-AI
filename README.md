# Sabrah

Voice-based AI travel assistant MVP.

Sabrah consists of **two independent Python applications**:

| Project | Port | Responsibility |
|---------|------|----------------|
| `sabrah-ai` | 8000 | Voice I/O, speech recognition, LLM conversation, tool calling, TTS |
| `sabrah-travel-backend` | 8001 | Travel search, packages, bookings (mock providers for MVP) |

```text
Browser microphone
        ↓
OpenAI Speech-to-Text          (sabrah-ai)
        ↓
OpenAI GPT + tool calling      (sabrah-ai)
        ↓
HTTP travel APIs               (sabrah-travel-backend)
        ↓
OpenAI GPT final reply         (sabrah-ai)
        ↓
ElevenLabs Text-to-Speech      (sabrah-ai)
        ↓
Browser speaker
```

Travel business logic lives only in `sabrah-travel-backend`. Sabrah AI never invents prices, availability, or booking IDs.

---

## Quick start

### 1. Setup

```bash
cd sabrah
chmod +x setup.sh run-dev.sh
./setup.sh
```

### 2. Configure secrets

Edit both `.env` files (created from `.env.example` by `setup.sh`):

**`sabrah-ai/.env`** — fill at minimum:

```env
OPENAI_API_KEY=sk-...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
TRAVEL_BACKEND_API_KEY=local-development-key
```

**`sabrah-travel-backend/.env`** — ensure:

```env
API_KEY=local-development-key
```

`TRAVEL_BACKEND_API_KEY` in Sabrah AI must match `API_KEY` in Travel Backend.

### 3. Run

Option A — helper script (two processes):

```bash
./run-dev.sh
```

Option B — two terminals:

```bash
# Terminal 1
cd sabrah-travel-backend
source .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8001

# Terminal 2
cd sabrah-ai
source .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open: [http://127.0.0.1:8000](http://127.0.0.1:8000)

---

## External setup you must perform

### OpenAI

1. Create an API key at [https://platform.openai.com/api-keys](https://platform.openai.com/api-keys)
2. Ensure billing/credits are enabled (ChatGPT Plus alone is not enough)
3. Put the key in `sabrah-ai/.env` as `OPENAI_API_KEY`

Defaults (override in `.env` if needed):

- Chat: `gpt-4o-mini`
- STT: `whisper-1`

### ElevenLabs

1. Create an API key in the ElevenLabs dashboard
2. Pick a Voice ID (Voice Library → copy Voice ID)
3. Set in `sabrah-ai/.env`:

```env
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
ELEVENLABS_MODEL_ID=eleven_multilingual_v2
```

`eleven_multilingual_v2` supports English, Hindi, and Hinglish-friendly speech.

---

## Architecture notes

- **Secrets stay server-side** — the browser never receives OpenAI or ElevenLabs keys.
- **Providers are abstract** — STT / LLM / TTS can be swapped later (e.g. OpenAI Realtime).
- **Travel providers are mock** — replace `MockTrainProvider` etc. without changing Sabrah AI.
- **Booking safety** — create/cancel booking tools require explicit user confirmation in the LLM prompt + tool schema.

---

## Tests

```bash
# Travel backend
cd sabrah-travel-backend && source .venv/bin/activate && pytest -q

# Sabrah AI
cd sabrah-ai && source .venv/bin/activate && pytest -q
```

Tests mock OpenAI and ElevenLabs — they do not consume API credits.

---

## Connecting real travel APIs later

In `sabrah-travel-backend`:

1. Implement `RealTrainProvider`, `RealFlightProvider`, etc.
2. Set `TRAIN_PROVIDER=real` (and similar) in `.env`
3. Keep the same HTTP schemas so Sabrah AI tools keep working unchanged

See each project's README for details.
