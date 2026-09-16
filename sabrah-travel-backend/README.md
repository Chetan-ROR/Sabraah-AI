# Sabrah Travel Backend

FastAPI service for travel search, package planning, and bookings.

Sabrah AI calls this service over HTTP. It does **not** call OpenAI or ElevenLabs.

## Requirements

- Python 3.11+
- Local API key shared with Sabrah AI (`API_KEY`)

## Installation

```bash
cd sabrah-travel-backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Configuration

Edit `.env`:

```env
API_KEY=local-development-key
TRAIN_PROVIDER=mock
FLIGHT_PROVIDER=mock
HOTEL_PROVIDER=mock
PACKAGE_PROVIDER=mock
```

`API_KEY` must match `TRAVEL_BACKEND_API_KEY` in Sabrah AI.

## Run

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

Health check: [http://127.0.0.1:8001/health](http://127.0.0.1:8001/health)

## Train admin dashboard

Add/edit mock trains in the browser:

[http://127.0.0.1:8001/admin](http://127.0.0.1:8001/admin)

Use the same `API_KEY` from `.env` (default `local-development-key`). Data is saved to `data/trains.json` and used by train search immediately.

## Auth

All `/api/v1/*` routes require:

```http
Authorization: Bearer <API_KEY>
```

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness |
| POST | `/api/v1/trains/search` | Train search |
| POST | `/api/v1/flights/search` | Flight search |
| POST | `/api/v1/hotels/search` | Hotel search |
| POST | `/api/v1/packages/search` | Package search |
| POST | `/api/v1/packages/plan` | Package plan |
| GET | `/api/v1/bookings/{id}` | Booking lookup |
| POST | `/api/v1/bookings` | Create booking |
| POST | `/api/v1/bookings/{id}/cancel` | Cancel booking |

## Train providers

| `TRAIN_PROVIDER` | Behaviour |
|------------------|-----------|
| `mock` | Local `data/trains.json` catalog |
| `real` | Live list from Super Travel `api-repository` (`/api/v1/trains/train-list/`), station resolve via `/station-codes/`, live status via `/live/`. Booking / CNF-RAC-WL seats stay local mock. If the API is down, falls back to mock when `TRAIN_PROVIDER_FALLBACK_MOCK=true`. |

```env
TRAIN_PROVIDER=real
SUPER_TRAVEL_API_BASE_URL=http://127.0.0.1:8002
```

Run `api-repository` on port **8002** (Sabrah AI uses 8000, this service uses 8001).

Sabrah AI does not need changes.

## Tests

```bash
pytest -q
```

## Connecting real travel APIs later

Keep request/response schemas stable. Point provider implementations at your IRCTC / airline / hotel APIs. Sabrah AI tool definitions already call these HTTP paths.
