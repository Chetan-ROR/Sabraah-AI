"""Persistent bus catalog for mock provider + itinerary suggestions."""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

from app.catalog.trains import norm_city, route_key

DEFAULT_ROUTES: dict[str, list[dict[str, Any]]] = {
    "khandwa|indore": [
        {
            "id": "BUS-KND-IND-001",
            "name": "Indore Express AC",
            "operator": "Sabrah Travels",
            "departure_time": "07:30",
            "arrival_time": "11:15",
            "duration": "3h 45m",
            "bus_type": "AC Seater",
            "unit_price": 350,
            "available_seats": 18,
            "legs": [
                {
                    "from": "Khandwa",
                    "to": "Indore",
                    "departure_time": "07:30",
                    "arrival_time": "11:15",
                    "bus_name": "Indore Express AC",
                }
            ],
        },
        {
            "id": "BUS-KND-IND-002",
            "name": "Night Rider Non-AC",
            "operator": "Central Roadways",
            "departure_time": "22:00",
            "arrival_time": "02:10",
            "duration": "4h 10m",
            "bus_type": "Non-AC Seater",
            "unit_price": 220,
            "available_seats": 26,
            "legs": [
                {
                    "from": "Khandwa",
                    "to": "Indore",
                    "departure_time": "22:00",
                    "arrival_time": "02:10",
                    "bus_name": "Night Rider Non-AC",
                }
            ],
        },
    ],
    "indore|khandwa": [
        {
            "id": "BUS-IND-KND-001",
            "name": "Khandwa Link AC",
            "operator": "Sabrah Travels",
            "departure_time": "15:00",
            "arrival_time": "18:40",
            "duration": "3h 40m",
            "bus_type": "AC Seater",
            "unit_price": 350,
            "available_seats": 14,
            "legs": [
                {
                    "from": "Indore",
                    "to": "Khandwa",
                    "departure_time": "15:00",
                    "arrival_time": "18:40",
                    "bus_name": "Khandwa Link AC",
                }
            ],
        }
    ],
    # Connecting buses example: via Bhopal
    "delhi|indore": [
        {
            "id": "BUS-DEL-IDR-CONN-001",
            "name": "Delhi–Indore via Bhopal",
            "operator": "Intercity Connect",
            "departure_time": "18:00",
            "arrival_time": "12:30",
            "duration": "18h 30m",
            "bus_type": "AC Sleeper + AC Seater",
            "unit_price": 1850,
            "available_seats": 8,
            "legs": [
                {
                    "from": "Delhi",
                    "to": "Bhopal",
                    "departure_time": "18:00",
                    "arrival_time": "06:00",
                    "bus_name": "Capital Sleeper",
                },
                {
                    "from": "Bhopal",
                    "to": "Indore",
                    "departure_time": "07:30",
                    "arrival_time": "12:30",
                    "bus_name": "Malwa Shuttle",
                },
            ],
        },
        {
            "id": "BUS-DEL-IDR-002",
            "name": "Direct Volvo AC",
            "operator": "North South Travels",
            "departure_time": "20:30",
            "arrival_time": "11:45",
            "duration": "15h 15m",
            "bus_type": "Volvo AC",
            "unit_price": 2100,
            "available_seats": 12,
            "legs": [
                {
                    "from": "Delhi",
                    "to": "Indore",
                    "departure_time": "20:30",
                    "arrival_time": "11:45",
                    "bus_name": "Direct Volvo AC",
                }
            ],
        },
    ],
    "mumbai|indore": [
        {
            "id": "BUS-BOM-IDR-001",
            "name": "Western AC Express",
            "operator": "Coastal Travels",
            "departure_time": "21:00",
            "arrival_time": "09:30",
            "duration": "12h 30m",
            "bus_type": "AC Sleeper",
            "unit_price": 1200,
            "available_seats": 10,
            "legs": [
                {
                    "from": "Mumbai",
                    "to": "Indore",
                    "departure_time": "21:00",
                    "arrival_time": "09:30",
                    "bus_name": "Western AC Express",
                }
            ],
        },
        {
            "id": "BUS-BOM-IDR-CONN-001",
            "name": "Mumbai–Indore via Surat",
            "operator": "Link Connect",
            "departure_time": "16:00",
            "arrival_time": "08:00",
            "duration": "16h",
            "bus_type": "AC Seater + AC Seater",
            "unit_price": 980,
            "available_seats": 16,
            "legs": [
                {
                    "from": "Mumbai",
                    "to": "Surat",
                    "departure_time": "16:00",
                    "arrival_time": "20:30",
                    "bus_name": "Coastal Seater",
                },
                {
                    "from": "Surat",
                    "to": "Indore",
                    "departure_time": "22:00",
                    "arrival_time": "08:00",
                    "bus_name": "Malwa Night Bus",
                },
            ],
        },
    ],
}

DEFAULT_FALLBACK: list[dict[str, Any]] = [
    {
        "id": "BUS-001",
        "name": "Intercity AC Seater",
        "operator": "Demo Bus Co",
        "departure_time": "08:00",
        "arrival_time": "14:00",
        "duration": "6h",
        "bus_type": "AC Seater",
        "unit_price": 650,
        "available_seats": 20,
        "legs": [],
    },
    {
        "id": "BUS-002",
        "name": "Night AC Sleeper",
        "operator": "Demo Bus Co",
        "departure_time": "21:30",
        "arrival_time": "05:45",
        "duration": "8h 15m",
        "bus_type": "AC Sleeper",
        "unit_price": 900,
        "available_seats": 12,
        "legs": [],
    },
]


class BusCatalog:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._routes: dict[str, list[dict[str, Any]]] = {}
        self._fallback: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        with self._lock:
            if not self._path.exists():
                self._routes = deepcopy(DEFAULT_ROUTES)
                self._fallback = deepcopy(DEFAULT_FALLBACK)
                self._write_unlocked()
                return
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._routes = {
                str(k): [dict(row) for row in (v or [])]
                for k, v in (raw.get("routes") or {}).items()
            }
            self._fallback = [dict(row) for row in (raw.get("fallback") or [])]
            if not self._fallback:
                self._fallback = deepcopy(DEFAULT_FALLBACK)

    def _write_unlocked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"routes": self._routes, "fallback": self._fallback}
        self._path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def get_buses_for_route(self, source: str, destination: str) -> list[dict[str, Any]]:
        key = route_key(source, destination)
        with self._lock:
            if key in self._routes:
                return deepcopy(self._routes[key])
            return deepcopy(self._fallback)

    def price_for(self, bus_id: str) -> Optional[float]:
        with self._lock:
            for buses in self._routes.values():
                for row in buses:
                    if row.get("id") == bus_id:
                        return float(row["unit_price"])
            for row in self._fallback:
                if row.get("id") == bus_id:
                    return float(row["unit_price"])
        return None


_catalog: Optional[BusCatalog] = None


def get_bus_catalog() -> BusCatalog:
    global _catalog
    if _catalog is None:
        root = Path(__file__).resolve().parents[2]
        _catalog = BusCatalog(root / "data" / "buses.json")
    return _catalog
