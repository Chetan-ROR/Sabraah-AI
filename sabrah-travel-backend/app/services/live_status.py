"""Mock live train running status for dashboard + AI."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.catalog import get_train_catalog
from app.catalog.trains import availability_for_passengers


IST = timezone(timedelta(hours=5, minutes=30))


def _parse_hhmm(value: str) -> Optional[tuple[int, int]]:
    try:
        parts = (value or "").strip().split(":")
        return int(parts[0]), int(parts[1])
    except (TypeError, ValueError, IndexError):
        return None


def _duration_minutes(value: str) -> int:
    text = (value or "").lower().replace(" ", "")
    hours = 0
    mins = 0
    try:
        if "h" in text:
            hours = int(text.split("h")[0] or 0)
            rest = text.split("h")[-1]
            if "m" in rest:
                mins = int(rest.replace("m", "") or 0)
        elif "m" in text:
            mins = int(text.replace("m", "") or 0)
    except ValueError:
        return 180
    return max(30, hours * 60 + mins)


def compute_live_status(
    row: dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Simulate live running status from scheduled times (demo)."""
    now = now or datetime.now(IST)
    dep = _parse_hhmm(str(row.get("departure_time") or ""))
    arr = _parse_hhmm(str(row.get("arrival_time") or ""))
    dur_m = _duration_minutes(str(row.get("duration") or ""))
    seed = hashlib.md5(str(row.get("id") or "").encode()).hexdigest()
    delay = int(seed[0:2], 16) % 35  # 0–34 min mock delay
    on_time = delay < 5

    source = str(row.get("source") or "Origin")
    dest = str(row.get("destination") or "Destination")
    mid = f"{source[:3].upper()}-HALT"

    if not dep:
        return {
            **row,
            "live_status": "UNKNOWN",
            "delay_minutes": 0,
            "current_station": source,
            "next_station": dest,
            "eta_destination": row.get("arrival_time"),
            "last_updated": now.isoformat(),
            "status_message": "Schedule unavailable",
        }

    dep_dt = now.replace(hour=dep[0], minute=dep[1], second=0, microsecond=0)
    # Overnight trains: if arrival clock < departure clock, arrival is next day.
    if arr:
        arr_dt = now.replace(hour=arr[0], minute=arr[1], second=0, microsecond=0)
        if arr_dt <= dep_dt:
            arr_dt = arr_dt + timedelta(days=1)
    else:
        arr_dt = dep_dt + timedelta(minutes=dur_m)

    dep_actual = dep_dt + timedelta(minutes=delay)
    arr_actual = arr_dt + timedelta(minutes=delay)

    # If we're before a morning departure that already "passed" yesterday wrap —
    # keep simple same-day window: if now is way after arrival, treat as arrived today.
    if now < dep_actual - timedelta(hours=2):
        live = "SCHEDULED"
        current = f"{source} Junction (yet to depart)"
        nxt = mid
        msg = f"Scheduled to depart at {row.get('departure_time')}"
        if delay and not on_time:
            msg += f" · expected delay {delay} min"
    elif now < dep_actual:
        live = "BOARDING"
        current = f"{source} Junction"
        nxt = mid
        mins = int((dep_actual - now).total_seconds() // 60)
        msg = f"Boarding · departs in ~{max(0, mins)} min · Platform mock"
    elif now >= arr_actual:
        live = "ARRIVED"
        current = f"{dest} Central"
        nxt = None
        msg = f"Arrived at {dest}" + (f" · delayed {delay} min" if delay else " · on time")
    else:
        live = "RUNNING"
        progress = (now - dep_actual).total_seconds() / max(
            1.0, (arr_actual - dep_actual).total_seconds()
        )
        if progress < 0.35:
            current = f"{source} Junction"
            nxt = mid
        elif progress < 0.7:
            current = f"{mid} Station"
            nxt = f"{dest} Central"
        else:
            current = f"Approaching {dest}"
            nxt = f"{dest} Central"
        eta_mins = int((arr_actual - now).total_seconds() // 60)
        msg = (
            f"Running · next {nxt} · ETA {dest} in ~{max(0, eta_mins)} min"
            + (f" · delayed {delay} min" if delay else " · on time")
        )

    avail = availability_for_passengers(row, 1)
    return {
        "train_id": row.get("id"),
        "train_name": row.get("name"),
        "source": source,
        "destination": dest,
        "class": row.get("class"),
        "departure_time": row.get("departure_time"),
        "arrival_time": row.get("arrival_time"),
        "duration": row.get("duration"),
        "live_status": live,
        "delay_minutes": 0 if on_time and live == "SCHEDULED" else delay,
        "on_time": on_time and live in {"SCHEDULED", "BOARDING"},
        "current_station": current,
        "next_station": nxt,
        "eta_destination": arr_actual.strftime("%H:%M"),
        "availability_status": avail["availability_status"],
        "available_seats": avail["available_seats"],
        "rac_seats": avail["rac_seats"],
        "waiting_list": avail["waiting_list"],
        "availability_message": avail["message"],
        "status_message": msg,
        "last_updated": now.isoformat(),
        "provider": "MOCK",
    }


def list_live_status(
    *,
    source: Optional[str] = None,
    destination: Optional[str] = None,
    train_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    catalog = get_train_catalog()
    rows = catalog.list_all_trains()
    out: list[dict[str, Any]] = []
    for row in rows:
        if train_id and str(row.get("id")) != str(train_id):
            continue
        if source and str(row.get("source") or "").lower() != source.strip().lower():
            # allow alias-normalized compare via catalog route_key pieces
            from app.catalog.trains import norm_city

            if norm_city(str(row.get("source") or "")) != norm_city(source):
                continue
        if destination:
            from app.catalog.trains import norm_city

            if norm_city(str(row.get("destination") or "")) != norm_city(destination):
                continue
        out.append(compute_live_status(row))
    # Running / boarding first
    order = {"BOARDING": 0, "RUNNING": 1, "SCHEDULED": 2, "ARRIVED": 3, "UNKNOWN": 4}
    out.sort(key=lambda r: (order.get(str(r.get("live_status")), 9), r.get("train_name")))
    return out


def live_status_for_train(train_id: str) -> Optional[dict[str, Any]]:
    """Prefer Super Travel live API when TRAIN_PROVIDER=real; else mock schedule."""
    from app.config.settings import get_settings

    settings = get_settings()
    if (settings.train_provider or "").lower() == "real":
        from app.providers.super_travel import (
            fetch_live_status_sync,
            map_live_to_sabrah,
        )

        live = fetch_live_status_sync(train_id)
        if live:
            mapped = map_live_to_sabrah(live, train_id=train_id)
            # Merge catalog schedule fields when available.
            catalog = get_train_catalog()
            found = catalog.find_train(train_id)
            if found:
                mapped = {
                    **found,
                    **mapped,
                    "name": mapped.get("train_name") or found.get("name"),
                    "train_name": mapped.get("train_name") or found.get("name"),
                }
                avail = availability_for_passengers(found, 1)
                mapped.update(
                    {
                        "available_seats": avail["available_seats"],
                        "rac_seats": avail["rac_seats"],
                        "waiting_list": avail["waiting_list"],
                        "availability_message": avail["message"],
                    }
                )
            return mapped

    rows = list_live_status(train_id=train_id)
    return rows[0] if rows else None
