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
