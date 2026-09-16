"""Persistent train catalog for mock provider + admin dashboard."""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

CITY_ALIASES: dict[str, str] = {
    "nashik": "nasik",
    "nasik road": "nasik",
    "mumbai central": "mumbai",
    "bombay": "mumbai",
    "csmt": "mumbai",
    "new delhi": "delhi",
    "ndls": "delhi",
    "delhi ncr": "delhi",
    "indore city": "indore",
    "khandwa jn": "khandwa",
    "jalgaon jn": "jalgaon",
    "poona": "pune",
    "puné": "pune",
}


DEFAULT_ROUTES: dict[str, list[dict[str, Any]]] = {
    "khandwa|indore": [
        {
            "id": "TRAIN-KND-IND-001",
            "name": "Narmada Express",
            "departure_time": "06:15",
            "arrival_time": "09:40",
            "duration": "3h 25m",
            "class": "3A",
            "unit_price": 890,
            "available_seats": 28,
            "rac_seats": 8,
            "waiting_list": 12,
            "max_waiting_list": 120,
            "availability_status": "AVAILABLE",
        },
    ],
}

DEFAULT_FALLBACK: list[dict[str, Any]] = [
    {
        "id": "TRAIN-001",
        "name": "Rajdhani Express",
        "departure_time": "16:55",
        "arrival_time": "08:35",
        "duration": "15h 40m",
        "class": "3A",
        "unit_price": 1850,
        "available_seats": 18,
        "rac_seats": 6,
        "waiting_list": 10,
        "max_waiting_list": 120,
        "availability_status": "AVAILABLE",
    },
]


def norm_city(value: str) -> str:
    raw = " ".join(value.strip().lower().split())
    return CITY_ALIASES.get(raw, raw)


def route_key(source: str, destination: str) -> str:
    return f"{norm_city(source)}|{norm_city(destination)}"


def _normalize_train_row(train: dict[str, Any]) -> dict[str, Any]:
    seats = int(train.get("available_seats") or 0)
    rac = int(train.get("rac_seats") or 0)
    wl = int(train.get("waiting_list") or 0)
    max_wl = int(train.get("max_waiting_list") or 120)
    if seats > 0:
        status = "AVAILABLE"
    elif rac > 0:
        status = "RAC"
    elif wl < max_wl:
        status = "WL"
    else:
        status = "NOT_AVAILABLE"
    return {
        "id": str(train["id"]).strip(),
        "name": str(train["name"]).strip(),
        "departure_time": str(train["departure_time"]).strip(),
        "arrival_time": str(train["arrival_time"]).strip(),
        "duration": str(train["duration"]).strip(),
        "class": str(train.get("class") or "3A").strip(),
        "unit_price": float(train["unit_price"]),
        "available_seats": seats,
        "rac_seats": rac,
        "waiting_list": wl,
        "max_waiting_list": max_wl,
        "availability_status": status,
    }


def availability_for_passengers(row: dict[str, Any], passengers: int) -> dict[str, Any]:
    """IRCTC-style availability for N passengers."""
    passengers = max(1, int(passengers or 1))
    seats = int(row.get("available_seats") or 0)
    rac = int(row.get("rac_seats") or 0)
    wl = int(row.get("waiting_list") or 0)
    max_wl = int(row.get("max_waiting_list") or 120)
    if seats >= passengers:
        return {
            "availability_status": "AVAILABLE",
            "booking_quota": "GN",
            "can_book": True,
            "needs_waitlist_confirm": False,
            "message": f"AVAILABLE — {seats} confirmed seats left",
            "available_seats": seats,
            "rac_seats": rac,
            "waiting_list": wl,
            "wl_position_if_booked": None,
        }
    if seats + rac >= passengers:
        return {
            "availability_status": "RAC",
            "booking_quota": "RAC",
            "can_book": True,
            "needs_waitlist_confirm": True,
            "message": (
                f"RAC available — only {seats} confirmed + {rac} RAC. "
                "You may get a side berth / shared seat until upgraded."
            ),
            "available_seats": seats,
            "rac_seats": rac,
            "waiting_list": wl,
            "wl_position_if_booked": None,
        }
    remaining_wl = max_wl - wl
    if remaining_wl >= passengers:
        start = wl + 1
        end = wl + passengers
        return {
            "availability_status": "WL",
            "booking_quota": "WL",
            "can_book": True,
            "needs_waitlist_confirm": True,
            "message": (
                f"Waiting List — current WL/{wl}. "
                f"If you book now you get WL/{start}"
                + (f"–WL/{end}" if passengers > 1 else "")
                + ". Confirm only if you accept waitlist risk."
            ),
            "available_seats": seats,
            "rac_seats": rac,
            "waiting_list": wl,
            "wl_position_if_booked": start,
        }
    return {
        "availability_status": "NOT_AVAILABLE",
        "booking_quota": None,
        "can_book": False,
        "needs_waitlist_confirm": False,
        "message": "NOT AVAILABLE — no confirmed, RAC, or waitlist seats left.",
        "available_seats": seats,
        "rac_seats": rac,
        "waiting_list": wl,
        "wl_position_if_booked": None,
    }


class TrainCatalog:
    """JSON-backed train catalog used by mock search + admin UI."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._routes: dict[str, list[dict[str, Any]]] = {}
        self._fallback: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        with self._lock:
            if not self._path.exists():
                self._routes = {
                    k: [_normalize_train_row(r) for r in v]
                    for k, v in deepcopy(DEFAULT_ROUTES).items()
                }
                self._fallback = [
                    _normalize_train_row(r) for r in deepcopy(DEFAULT_FALLBACK)
                ]
                self._write_unlocked()
                return
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._routes = {
                str(k): [_normalize_train_row(row) for row in (v or [])]
                for k, v in (raw.get("routes") or {}).items()
            }
            self._fallback = [
                _normalize_train_row(row) for row in (raw.get("fallback") or [])
            ]
            if not self._fallback:
                self._fallback = [
                    _normalize_train_row(r) for r in deepcopy(DEFAULT_FALLBACK)
                ]

    def reload(self) -> None:
        self.load()

    def _write_unlocked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"routes": self._routes, "fallback": self._fallback}
        self._path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def save(self) -> None:
        with self._lock:
            self._write_unlocked()

    def list_routes(self) -> list[dict[str, Any]]:
        with self._lock:
            rows: list[dict[str, Any]] = []
            for key, trains in sorted(self._routes.items()):
                source, destination = key.split("|", 1)
                rows.append(
                    {
                        "route_key": key,
                        "source": source.title(),
                        "destination": destination.title(),
                        "train_count": len(trains),
                        "trains": deepcopy(trains),
                    }
                )
            return rows

    def list_all_trains(self) -> list[dict[str, Any]]:
        with self._lock:
            out: list[dict[str, Any]] = []
            for key, trains in self._routes.items():
                source, destination = key.split("|", 1)
                for row in trains:
                    item = deepcopy(row)
                    item["source"] = source.title()
                    item["destination"] = destination.title()
                    item["route_key"] = key
                    out.append(item)
            return out

    def get_trains_for_route(
        self,
        source: str,
        destination: str,
        *,
        use_fallback: bool = False,
    ) -> list[dict[str, Any]]:
        key = route_key(source, destination)
        with self._lock:
            if key in self._routes:
                return deepcopy(self._routes[key])
            if use_fallback:
                return deepcopy(self._fallback)
            return []

    def get_fallback(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._fallback)

    def find_train(self, train_id: str) -> Optional[dict[str, Any]]:
        tid = str(train_id).strip()
        with self._lock:
            for key, trains in self._routes.items():
                source, destination = key.split("|", 1)
                for row in trains:
                    if str(row.get("id")) == tid:
                        item = deepcopy(row)
                        item["source"] = source.title()
                        item["destination"] = destination.title()
                        item["route_key"] = key
                        return item
            for row in self._fallback:
                if str(row.get("id")) == tid:
                    item = deepcopy(row)
                    item["source"] = "Origin"
                    item["destination"] = "Destination"
                    item["route_key"] = "fallback"
                    return item
        return None

    def upsert_train(
        self,
        source: str,
        destination: str,
        train: dict[str, Any],
    ) -> dict[str, Any]:
        key = route_key(source, destination)
        row = _normalize_train_row(train)
        with self._lock:
            trains = self._routes.setdefault(key, [])
            replaced = False
            for idx, existing in enumerate(trains):
                if existing.get("id") == row["id"]:
                    trains[idx] = row
                    replaced = True
                    break
            if not replaced:
                trains.append(row)
            self._write_unlocked()
            return deepcopy(row)

    def delete_train(self, source: str, destination: str, train_id: str) -> bool:
        key = route_key(source, destination)
        with self._lock:
            trains = self._routes.get(key)
            if not trains:
                return False
            next_trains = [t for t in trains if t.get("id") != train_id]
            if len(next_trains) == len(trains):
                return False
            if next_trains:
                self._routes[key] = next_trains
            else:
                self._routes.pop(key, None)
            self._write_unlocked()
            return True

    def price_for(self, train_id: str) -> Optional[float]:
        found = self.find_train(train_id)
        if found:
            return float(found["unit_price"])
        return None

    def reserve_seats(
        self,
        train_id: str,
        passengers: int,
        traveler_names: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Decrement inventory and return CNF/RAC/WL allotment (IRCTC-style)."""
        passengers = max(1, int(passengers or 1))
        names = list(traveler_names or [])
        while len(names) < passengers:
            names.append(f"Passenger {len(names) + 1}")
        names = names[:passengers]

        with self._lock:
            location = self._locate_unlocked(train_id)
            if location is None:
                return {
                    "ok": False,
                    "error": "train_not_found",
                    "message": f"Train {train_id} not found in catalog.",
                }
            key, idx, row = location
            avail = availability_for_passengers(row, passengers)
            if not avail["can_book"]:
                return {"ok": False, "error": "not_available", **avail}

            status = avail["availability_status"]
            allotments: list[dict[str, Any]] = []
            if status == "AVAILABLE":
                row["available_seats"] = int(row["available_seats"]) - passengers
                for i, name in enumerate(names):
                    coach = f"B{(i // 6) + 1}"
                    berths = ["LB", "MB", "UB", "LB", "MB", "UB"]
                    berth = berths[i % 6]
                    seat_no = (i % 6) + 1 + ((i // 6) * 6)
                    allotments.append(
                        {
                            "passenger": name,
                            "status": "CNF",
                            "coach": coach,
                            "berth": f"{seat_no}{berth}",
                            "seat_label": f"{coach} / {seat_no}{berth}",
                        }
                    )
            elif status == "RAC":
                use_cnf = min(int(row["available_seats"]), passengers)
                use_rac = passengers - use_cnf
                row["available_seats"] = int(row["available_seats"]) - use_cnf
                row["rac_seats"] = int(row["rac_seats"]) - use_rac
                rac_start = 40 - int(row["rac_seats"])
                for i, name in enumerate(names):
                    if i < use_cnf:
                        allotments.append(
                            {
                                "passenger": name,
                                "status": "CNF",
                                "coach": "B1",
                                "berth": f"{i + 1}LB",
                                "seat_label": f"B1 / {i + 1}LB",
                            }
                        )
                    else:
                        rac_no = rac_start + (i - use_cnf) + 1
                        allotments.append(
                            {
                                "passenger": name,
                                "status": f"RAC{rac_no}",
                                "coach": "B1",
                                "berth": f"RAC{rac_no}",
                                "seat_label": f"RAC {rac_no}",
                            }
                        )
            else:  # WL
                start = int(row["waiting_list"]) + 1
                row["waiting_list"] = int(row["waiting_list"]) + passengers
                for i, name in enumerate(names):
                    wl_no = start + i
                    allotments.append(
                        {
                            "passenger": name,
                            "status": f"WL{wl_no}",
                            "coach": None,
                            "berth": f"WL{wl_no}",
                            "seat_label": f"WL/{wl_no}",
                        }
                    )

            row.update(_normalize_train_row(row))
            if key == "fallback":
                self._fallback[idx] = row
            else:
                self._routes[key][idx] = row
            self._write_unlocked()
            return {
                "ok": True,
                "availability_status": status,
                "booking_quota": avail["booking_quota"],
                "message": avail["message"],
                "seats": allotments,
                "inventory": deepcopy(row),
            }

    def release_hint(self, train_id: str) -> Optional[dict[str, Any]]:
        return self.find_train(train_id)

    def _locate_unlocked(
        self, train_id: str
    ) -> Optional[tuple[str, int, dict[str, Any]]]:
        tid = str(train_id).strip()
        for key, trains in self._routes.items():
            for idx, row in enumerate(trains):
                if str(row.get("id")) == tid:
                    return key, idx, row
        for idx, row in enumerate(self._fallback):
            if str(row.get("id")) == tid:
                return "fallback", idx, row
        return None


_catalog: Optional[TrainCatalog] = None


def get_train_catalog() -> TrainCatalog:
    global _catalog
    if _catalog is None:
        root = Path(__file__).resolve().parents[2]
        _catalog = TrainCatalog(root / "data" / "trains.json")
    return _catalog


def set_train_catalog(catalog: TrainCatalog) -> None:
    global _catalog
    _catalog = catalog
