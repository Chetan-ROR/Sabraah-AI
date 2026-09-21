"""Travel Backend pytest suite."""

import os

import pytest
from fastapi.testclient import TestClient

# Ensure settings load without a real .env during tests.
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("TRAIN_PROVIDER", "mock")
os.environ.setdefault("FLIGHT_PROVIDER", "mock")
os.environ.setdefault("HOTEL_PROVIDER", "mock")
os.environ.setdefault("PACKAGE_PROVIDER", "mock")

from app.main import create_app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-api-key"}


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["providers"]["train"] == "mock"


def test_invalid_authentication(client: TestClient) -> None:
    response = client.post(
        "/api/v1/trains/search",
        json={
            "source": "Delhi",
            "destination": "Mumbai",
            "departure_date": "2026-08-21",
        },
    )
    assert response.status_code == 401

    response = client.post(
        "/api/v1/trains/search",
        headers={"Authorization": "Bearer wrong"},
        json={
            "source": "Delhi",
            "destination": "Mumbai",
            "departure_date": "2026-08-21",
        },
    )
    assert response.status_code == 401


def test_train_search(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/trains/search",
        headers=auth_headers,
        json={
            "source": "Delhi",
            "destination": "Mumbai",
            "departure_date": "2026-08-21",
            "passengers": 1,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["count"] >= 1
    assert data["provider"] == "MOCK"
    assert data["results"][0]["id"]
    assert data["results"][0]["source"] == "Delhi"


def test_train_search_khandwa_indore(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/trains/search",
        headers=auth_headers,
        json={
            "source": "Khandwa",
            "destination": "Indore",
            "passengers": 1,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 3
    assert data["results"][0]["id"] == "TRAIN-KND-IND-001"
    assert data["results"][0]["name"] == "Narmada Express"
    assert data["results"][0]["source"] == "Khandwa"
    assert data["results"][0]["destination"] == "Indore"


def test_admin_upsert_train(client: TestClient, auth_headers: dict[str, str]) -> None:
    create = client.post(
        "/api/v1/admin/trains",
        headers=auth_headers,
        json={
            "source": "Bhopal",
            "destination": "Indore",
            "id": "TRAIN-BPL-IDR-001",
            "name": "Demo Express",
            "departure_time": "09:00",
            "arrival_time": "12:30",
            "duration": "3h 30m",
            "class": "CC",
            "unit_price": 550,
            "available_seats": 40,
        },
    )
    assert create.status_code == 200
    assert create.json()["train"]["id"] == "TRAIN-BPL-IDR-001"

    search = client.post(
        "/api/v1/trains/search",
        headers=auth_headers,
        json={
            "source": "Bhopal",
            "destination": "Indore",
            "departure_date": "2026-08-21",
        },
    )
    assert search.status_code == 200
    ids = [row["id"] for row in search.json()["results"]]
    assert "TRAIN-BPL-IDR-001" in ids


def test_flight_search(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/flights/search",
        headers=auth_headers,
        json={
            "source": "Delhi",
            "destination": "Mumbai",
            "departure_date": "2026-08-21",
        },
    )
    assert response.status_code == 200
    assert response.json()["results"][0]["id"] == "FLIGHT-001"


def test_bus_search_khandwa_indore(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/buses/search",
        headers=auth_headers,
        json={
            "source": "Khandwa",
            "destination": "Indore",
            "passengers": 1,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["count"] >= 1
    assert data["results"][0]["id"].startswith("BUS-")
    assert data["results"][0]["source"] == "Khandwa"


def test_bus_search_connecting_delhi_indore(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/v1/buses/search",
        headers=auth_headers,
        json={
            "source": "Delhi",
            "destination": "Indore",
            "departure_date": "2026-08-21",
        },
    )
    assert response.status_code == 200
    data = response.json()
    connecting = [row for row in data["results"] if len(row.get("legs") or []) > 1]
    assert connecting, "Expected at least one connecting bus option"


def test_hotel_search(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/hotels/search",
        headers=auth_headers,
        json={
            "city": "Mumbai",
            "check_in": "2026-08-21",
            "check_out": "2026-08-23",
            "guests": 2,
        },
    )
    assert response.status_code == 200
    assert response.json()["results"][0]["id"] == "HOTEL-001"


def test_package_search(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/packages/search",
        headers=auth_headers,
        json={
            "source": "Delhi",
            "destination": "Goa",
            "departure_date": "2026-09-01",
            "return_date": "2026-09-04",
        },
    )
    assert response.status_code == 200
    assert response.json()["results"][0]["provider"] == "MOCK"


def test_package_plan(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/packages/plan",
        headers=auth_headers,
        json={
            "source": "Delhi",
            "destination": "Goa",
            "departure_date": "2026-09-01",
            "return_date": "2026-09-04",
            "include_hotel": True,
            "transport_preference": "flight",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "PLAN-001"
    assert data["hotel"] is not None


def test_booking_flow(client: TestClient, auth_headers: dict[str, str]) -> None:
    # Without confirmation → reject
    denied = client.post(
        "/api/v1/bookings",
        headers=auth_headers,
        json={
            "item_type": "train",
            "item_id": "TRAIN-001",
            "passenger_name": "Asha",
            "passenger_count": 1,
            "confirmed": False,
        },
    )
    assert denied.status_code == 400

    created = client.post(
        "/api/v1/bookings",
        headers=auth_headers,
        json={
            "item_type": "train",
            "item_id": "TRAIN-001",
            "passenger_name": "Asha",
            "passenger_count": 1,
            "confirmed": True,
        },
    )
    assert created.status_code == 200
    booking_id = created.json()["booking_id"]
    assert booking_id.startswith("BK-")

    lookup = client.get(f"/api/v1/bookings/{booking_id}", headers=auth_headers)
    assert lookup.status_code == 200
    assert lookup.json()["status"] == "confirmed"

    cancel_denied = client.post(
        f"/api/v1/bookings/{booking_id}/cancel",
        headers=auth_headers,
        json={"confirmed": False},
    )
    assert cancel_denied.status_code == 400

    cancelled = client.post(
        f"/api/v1/bookings/{booking_id}/cancel",
        headers=auth_headers,
        json={"confirmed": True, "reason": "plans changed"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_map_super_travel_flight_card() -> None:
    from app.providers.super_travel import map_flight_card, parse_money

    assert parse_money("₹4,299") == 4299.0
    mapped = map_flight_card(
        {
            "flight_number": "6E 2134",
            "index": "6E|2",
            "airline_name": "IndiGo",
            "departure_time": "06:00",
            "arrival_time": "08:10",
            "duration": "2h 10m",
            "departure_code": "DEL",
            "arrival_code": "BOM",
            "price": "4299",
            "seats": 5,
            "cabin_label": "Economy",
        },
        source_label="Delhi",
        destination_label="Mumbai",
        passengers=1,
        travel_class="economy",
    )
    assert mapped.provider == "SUPER_TRAVEL"
    assert mapped.airline == "IndiGo"
    assert mapped.price == 4299
    assert mapped.id == "6E 2134 [6E|2]"


@pytest.mark.asyncio
async def test_real_flight_provider_maps_live_rows(monkeypatch) -> None:
    from datetime import date

    from app.providers import RealFlightProvider

    async def fake_resolve(query: str):
        return "DEL" if "del" in query.lower() else "BOM"

    async def fake_fetch(**_kwargs):
        return [
            {
                "flight_number": "AI 101",
                "index": "AI|1",
                "airline_name": "Air India",
                "departure_time": "09:00",
                "arrival_time": "11:15",
                "duration": "2h 15m",
                "price": "5100",
                "seats": 8,
            }
        ]

    monkeypatch.setattr(
        "app.providers.super_travel.resolve_airport_code", fake_resolve
    )
    monkeypatch.setattr(
        "app.providers.super_travel.fetch_flight_search", fake_fetch
    )

    provider = RealFlightProvider(fallback_to_mock=False)
    rows = await provider.search("Delhi", "Mumbai", date(2026, 9, 20))
    assert len(rows) == 1
    assert rows[0].provider == "SUPER_TRAVEL"
    assert rows[0].airline == "Air India"
    assert rows[0].price == 5100
