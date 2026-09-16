"""IRCTC-style seat queue: CNF → RAC → WL, with cancel promotion."""

from __future__ import annotations

import threading
from typing import Any, Optional

from app.catalog import get_train_catalog
from app.catalog.trains import _normalize_train_row

# train_id → ordered booking ids waiting for upgrade
_RAC_QUEUE: dict[str, list[str]] = {}
_WL_QUEUE: dict[str, list[str]] = {}
_LOCK = threading.Lock()


def register_booking_queue(
    train_id: str,
    booking_id: str,
    booking_quota: str,
) -> None:
    """Track RAC/WL bookings so cancel can promote them in FIFO order."""
    tid = str(train_id)
    bid = str(booking_id)
    quota = (booking_quota or "GN").upper()
    with _LOCK:
        if quota == "RAC":
            q = _RAC_QUEUE.setdefault(tid, [])
            if bid not in q:
                q.append(bid)
        elif quota == "WL":
            q = _WL_QUEUE.setdefault(tid, [])
            if bid not in q:
                q.append(bid)


def _pop_first(queue: dict[str, list[str]], train_id: str) -> Optional[str]:
    q = queue.get(train_id) or []
    if not q:
        return None
    bid = q.pop(0)
    if not q:
        queue.pop(train_id, None)
    else:
        queue[train_id] = q
    return bid


def _remove_from_queues(train_id: str, booking_id: str) -> None:
    for queue in (_RAC_QUEUE, _WL_QUEUE):
        if train_id in queue:
            queue[train_id] = [b for b in queue[train_id] if b != booking_id]
            if not queue[train_id]:
                queue.pop(train_id, None)


def release_on_cancel(
    *,
    train_id: str,
    booking_id: str,
    passenger_count: int,
    seats: list[dict[str, Any]],
    booking_quota: Optional[str] = None,
    booking_repo: Any = None,
) -> dict[str, Any]:
    """
    Booking ladder:
      CNF available → book CNF
      CNF full → book RAC (RAC pool shrinks)
      RAC full → book WL (waiting_list count grows; no berth yet)

    Cancel ladder (FIFO):
      Cancel CNF → CNF frees → first RAC upgrades to CNF → that RAC berth frees
                    → first WL upgrades to RAC
      Cancel RAC → RAC frees → first WL upgrades to RAC
      Cancel WL  → waiting_list count decreases only
    """
    catalog = get_train_catalog()
    tid = str(train_id)
    count = max(1, int(passenger_count or 1))
    seats = list(seats or [])

    cnf_released = 0
    rac_released = 0
    wl_released = 0
    for s in seats:
        st = str(s.get("status") or "").upper()
        if st.startswith("CNF") or st in {"CONFIRMED", "GN"}:
            cnf_released += 1
        elif st.startswith("RAC"):
            rac_released += 1
        elif st.startswith("WL"):
            wl_released += 1

    if not seats:
        quota = (booking_quota or "GN").upper()
        if quota == "RAC":
            rac_released = count
        elif quota == "WL":
            wl_released = count
        else:
            cnf_released = count

    promotions: list[dict[str, Any]] = []

    with catalog._lock:  # type: ignore[attr-defined]
        _remove_from_queues(tid, booking_id)

        location = catalog._locate_unlocked(tid)  # type: ignore[attr-defined]
        if location is None:
            return {
                "ok": False,
                "message": f"Train {tid} not found for seat release",
                "promotions": [],
            }
        key, idx, row = location

        # 1) Put cancelled inventory back
        row["available_seats"] = int(row.get("available_seats") or 0) + cnf_released
        row["rac_seats"] = int(row.get("rac_seats") or 0) + rac_released
        row["waiting_list"] = max(
            0, int(row.get("waiting_list") or 0) - wl_released
        )

        # 2) For each freed CNF: RAC → CNF, then WL → RAC
        for _ in range(cnf_released):
            rac_bid = _pop_first(_RAC_QUEUE, tid)
            if not rac_bid:
                break
            # RAC passenger takes the freed CNF berth
            row["available_seats"] = max(0, int(row["available_seats"]) - 1)
            # Their old RAC berth opens
            row["rac_seats"] = int(row["rac_seats"]) + 1
            promotions.append(
                {
                    "booking_id": rac_bid,
                    "from": "RAC",
                    "to": "CNF",
                    "message": f"Booking {rac_bid} upgraded RAC → CNF",
                }
            )
            if booking_repo is not None:
                _upgrade_booking_record(booking_repo, rac_bid, "CNF")

            wl_bid = _pop_first(_WL_QUEUE, tid)
            if wl_bid:
                row["rac_seats"] = max(0, int(row["rac_seats"]) - 1)
                row["waiting_list"] = max(0, int(row["waiting_list"]) - 1)
                _RAC_QUEUE.setdefault(tid, []).append(wl_bid)
                promotions.append(
                    {
                        "booking_id": wl_bid,
                        "from": "WL",
                        "to": "RAC",
                        "message": f"Booking {wl_bid} upgraded WL → RAC",
                    }
                )
                if booking_repo is not None:
                    _upgrade_booking_record(booking_repo, wl_bid, "RAC")

        # 3) For each freed RAC: WL → RAC
        for _ in range(rac_released):
            wl_bid = _pop_first(_WL_QUEUE, tid)
            if not wl_bid:
                break
            row["rac_seats"] = max(0, int(row["rac_seats"]) - 1)
            row["waiting_list"] = max(0, int(row["waiting_list"]) - 1)
            _RAC_QUEUE.setdefault(tid, []).append(wl_bid)
            promotions.append(
                {
                    "booking_id": wl_bid,
                    "from": "WL",
                    "to": "RAC",
                    "message": f"Booking {wl_bid} upgraded WL → RAC",
                }
            )
            if booking_repo is not None:
                _upgrade_booking_record(booking_repo, wl_bid, "RAC")

        row.update(_normalize_train_row(row))
        if key == "fallback":
            catalog._fallback[idx] = row  # type: ignore[attr-defined]
        else:
            catalog._routes[key][idx] = row  # type: ignore[attr-defined]
        catalog._write_unlocked()  # type: ignore[attr-defined]

        return {
            "ok": True,
            "released": {
                "cnf": cnf_released,
                "rac": rac_released,
                "wl": wl_released,
            },
            "inventory": {
                "available_seats": row["available_seats"],
                "rac_seats": row["rac_seats"],
                "waiting_list": row["waiting_list"],
                "availability_status": row["availability_status"],
            },
            "promotions": promotions,
            "message": (
                "Seats released. "
                + (
                    f"{len(promotions)} passenger(s) moved up the queue."
                    if promotions
                    else "No one was waiting to promote."
                )
            ),
        }


def _upgrade_booking_record(booking_repo: Any, booking_id: str, new_quota: str) -> None:
    store = getattr(booking_repo, "_bookings", None)
    if not isinstance(store, dict):
        return
    record = store.get(booking_id)
    if record is None:
        return
    meta = dict(record.metadata or {})
    seats = list(meta.get("seats") or [])
    updated_seats = []
    for i, s in enumerate(seats):
        s = dict(s)
        if new_quota == "CNF":
            berth = f"{i + 1}LB"
            s.update(
                {
                    "status": "CNF",
                    "coach": "B1",
                    "berth": berth,
                    "seat_label": f"B1 / {berth}",
                }
            )
        elif new_quota == "RAC":
            s.update(
                {
                    "status": f"RAC{i + 1}",
                    "coach": "B1",
                    "berth": f"RAC{i + 1}",
                    "seat_label": f"RAC {i + 1}",
                }
            )
        updated_seats.append(s)
    meta["seats"] = updated_seats
    meta["booking_quota"] = new_quota
    meta["availability_status"] = new_quota
    meta["promotion_note"] = f"Auto-upgraded to {new_quota} after a cancellation"
    store[booking_id] = record.model_copy(update={"metadata": meta})


def queue_snapshot(train_id: Optional[str] = None) -> dict[str, Any]:
    with _LOCK:
        if train_id:
            tid = str(train_id)
            return {
                "train_id": tid,
                "rac_queue": list(_RAC_QUEUE.get(tid) or []),
                "wl_queue": list(_WL_QUEUE.get(tid) or []),
            }
        return {
            "rac_queues": {k: list(v) for k, v in _RAC_QUEUE.items()},
            "wl_queues": {k: list(v) for k, v in _WL_QUEUE.items()},
        }
