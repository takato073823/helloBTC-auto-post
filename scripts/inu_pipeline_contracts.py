#!/usr/bin/env python3
"""INU社内パイプラインの共通契約、指紋、期限付き予約。"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


RESERVATION_LEASE_MINUTES = 30
MAX_FAILURE_HISTORY = 200


def parse_timestamp(value: object) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("日時にはタイムゾーンが必要です")
    return parsed.astimezone(dt.timezone.utc)


def _compact(value: object) -> str:
    return re.sub(r"[^0-9a-z一-龥ぁ-んァ-ヶー$]+", "", str(value or "").lower())


def normalize_source_url(value: object) -> str:
    parsed = urlsplit(str(value or "").strip())
    query = urlencode(
        sorted(
            (key, val)
            for key, val in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in {"ref", "source", "fbclid", "gclid"}
        )
    )
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), query, ""))


def content_fingerprint(text: object) -> str:
    return hashlib.sha256(_compact(text).encode("utf-8")).hexdigest()


def event_fingerprint(candidate: dict) -> str:
    identity = "|".join(
        (
            str(candidate.get("topic_type", "")),
            normalize_source_url(candidate.get("source_url", "")),
            _compact(candidate.get("market_key", "")),
            _compact(candidate.get("hook", "")),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _character_ngrams(value: object, size: int = 3) -> set[str]:
    compact = _compact(value)
    if len(compact) < size:
        return {compact} if compact else set()
    return {compact[index : index + size] for index in range(len(compact) - size + 1)}


def is_semantic_event_duplicate(candidate: dict, existing: dict) -> bool:
    """URL違いの同一事件を、見出しの文字n-gramで保守的に検出する。"""
    if str(candidate.get("topic_type", "")) != str(existing.get("topic_type", "")):
        return False
    candidate_market = _compact(candidate.get("market_key", ""))
    existing_market = _compact(existing.get("market_key", ""))
    if candidate_market or existing_market:
        return bool(candidate_market and candidate_market == existing_market)
    left = _character_ngrams(candidate.get("hook", ""))
    right = _character_ngrams(existing.get("hook", ""))
    if not left or not right:
        return False
    similarity = len(left & right) / len(left | right)
    return similarity >= 0.55


def reservation_expiry(now: dt.datetime, *, minutes: int = RESERVATION_LEASE_MINUTES) -> str:
    return (now.astimezone(dt.timezone.utc) + dt.timedelta(minutes=minutes)).isoformat()


def is_active_reservation(row: dict, now: dt.datetime) -> bool:
    if not isinstance(row, dict):
        return False
    try:
        expires_at = parse_timestamp(row.get("lease_expires_at"))
    except (TypeError, ValueError):
        try:
            reserved_at = parse_timestamp(row.get("reserved_at"))
        except (TypeError, ValueError):
            return False
        expires_at = reserved_at + dt.timedelta(minutes=RESERVATION_LEASE_MINUTES)
    return expires_at > now.astimezone(dt.timezone.utc)


def prune_stale_reservations(state: dict, now: dt.datetime) -> tuple[dict, list[dict]]:
    updated = dict(state)
    active: list[dict] = []
    expired: list[dict] = []
    for row in state.get("reservations", []):
        (active if is_active_reservation(row, now) else expired).append(row)
    updated["reservations"] = active
    if expired:
        failures = list(state.get("delivery_failures", []))
        failures.extend(
            {
                "slot": str(row.get("slot", "")),
                "post_id": str(row.get("post_id", "")),
                "failed_at": now.astimezone(dt.timezone.utc).isoformat(),
                "stage": "reservation",
                "reason": "reservation_lease_expired",
            }
            for row in expired
        )
        updated["delivery_failures"] = failures[-MAX_FAILURE_HISTORY:]
    return updated, expired


def release_reservation(
    state: dict,
    *,
    slot: str,
    post_id: str,
    now: dt.datetime,
    reason: str,
) -> dict:
    updated = dict(state)
    updated["reservations"] = [
        row
        for row in state.get("reservations", [])
        if not (row.get("slot") == slot and row.get("post_id") == post_id)
    ]
    failures = list(state.get("delivery_failures", []))
    failures.append(
        {
            "slot": slot,
            "post_id": post_id,
            "failed_at": now.astimezone(dt.timezone.utc).isoformat(),
            "stage": "publish",
            "reason": str(reason)[:300],
        }
    )
    updated["delivery_failures"] = failures[-MAX_FAILURE_HISTORY:]
    return updated
