"""INUの一次データを、Webリサーチの補助なしで監視する。

ここで扱うのは発表主体・公式データ提供元から直接取得でき、数値または状態の
大きな変化を機械的に確認できるものだけ。通常のWebリサーチの代用品ではなく、
オンチェーンと取引所ステータスを取り逃さないための低コストな入口に限定する。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import requests


MEMPOOL_DASHBOARD_URL = "https://mempool.space/"
MEMPOOL_STATS_URL = "https://mempool.space/api/mempool"
MEMPOOL_FEES_URL = "https://mempool.space/api/v1/fees/recommended"
COINBASE_STATUS_API_URL = "https://status.coinbase.com/api/v2/incidents/unresolved.json"
COINBASE_STATUS_URL = "https://status.coinbase.com/incidents/{incident_id}"
USER_AGENT = "INU official-source monitor/1.0"
JST = dt.timezone(dt.timedelta(hours=9))


def _json(url: str) -> Any:
    response = requests.get(url, timeout=15, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.json()


def _parse_timestamp(value: object) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("公式データの更新日時にタイムゾーンがありません")
    return parsed.astimezone(dt.timezone.utc)


def _age_hours(now: dt.datetime, value: object) -> float:
    return (now.astimezone(dt.timezone.utc) - _parse_timestamp(value)).total_seconds() / 3600


def _mempool_candidate(now: dt.datetime, state: dict) -> tuple[dict | None, dict]:
    """ビットコインのオンチェーン混雑が急変した時だけ候補を作る。"""
    stats = _json(MEMPOOL_STATS_URL)
    fees = _json(MEMPOOL_FEES_URL)
    count = int(stats["count"])
    fastest_fee = int(fees["fastestFee"])
    current = {
        "checked_at": now.astimezone(dt.timezone.utc).isoformat(),
        "count": count,
        "fastest_fee": fastest_fee,
    }
    snapshots = state.setdefault("direct_source_metrics", {})
    previous = snapshots.get("mempool") if isinstance(snapshots, dict) else None
    if isinstance(snapshots, dict):
        snapshots["mempool"] = current

    if not isinstance(previous, dict):
        return None, current
    try:
        previous_fee = int(previous["fastest_fee"])
        previous_count = int(previous["count"])
    except (KeyError, TypeError, ValueError):
        return None, current
    if previous_fee <= 0 or previous_count <= 0:
        return None, current

    fee_change = (fastest_fee / previous_fee - 1) * 100
    backlog_change = (count / previous_count - 1) * 100
    fee_surge = fastest_fee >= 10 and fee_change >= 75
    backlog_surge = count >= 150_000 and backlog_change >= 60
    if not fee_surge and not backlog_surge:
        return None, current

    trigger = "優先手数料" if fee_surge else "未承認取引"
    hook = (
        "⚠️ ビットコイン送金手数料が急上昇"
        if fee_surge
        else "⚠️ ビットコインの未承認取引が急増"
    )
    return {
        "has_candidate": True,
        "skip_reason": "",
        "topic_type": "onchain",
        "hook": hook,
        "facts": [
            f"mempool.spaceで優先手数料は{fastest_fee} sat/vB。前回確認値{previous_fee} sat/vBから{fee_change:+.0f}％。",
            f"未承認取引は{count:,}件で、前回比{backlog_change:+.0f}％。",
        ],
        "opinion": "送金需要が急に集中しており、急がない送金は手数料が落ち着くまで待つ余地があります。",
        "source_name": "mempool.space",
        "source_url": MEMPOOL_DASHBOARD_URL,
        "published_at": now.astimezone(dt.timezone.utc).isoformat(),
        "evidence_anchor": "mempool - Bitcoin Explorer",
        "evidence_as_primary": True,
        "visual_route": "official_data_crop",
        "tags": ["ビットコイン", "オンチェーン"],
        "why_now": f"Bitcoinネットワークの{trigger}が前回確認値から大きく変化したためです。",
        "reader_interest": "送金コストとネットワーク混雑の急変を、公式オンチェーンデータで確認できるためです。",
        "follow_value": "送金手数料、未承認取引、ブロックスペース需要の変化を継続して追えるためです。",
        "is_primary_source": True,
        "focus_signal_url": "",
    }, current


def _coinbase_status_candidates(now: dt.datetime) -> list[dict]:
    """Coinbase公式ステータスの新しい未解決インシデントを投稿候補にする。"""
    payload = _json(COINBASE_STATUS_API_URL)
    candidates: list[dict] = []
    status_labels = {
        "investigating": "調査中",
        "identified": "原因を特定",
        "monitoring": "監視中",
    }
    for incident in payload.get("incidents", []):
        if not isinstance(incident, dict):
            continue
        status = str(incident.get("status", "")).lower()
        if status not in status_labels:
            continue
        updated_at = incident.get("updated_at") or incident.get("created_at")
        try:
            age = _age_hours(now, updated_at)
        except (TypeError, ValueError):
            continue
        if age < -0.25 or age > 4:
            continue
        incident_id = str(incident.get("id", "")).strip()
        name = " ".join(str(incident.get("name", "")).split())
        if not incident_id or len(name) < 6:
            continue
        components = [
            str(component.get("name", "")).strip()
            for component in incident.get("components", [])
            if isinstance(component, dict) and str(component.get("name", "")).strip()
        ]
        facts = [f"Coinbaseは「{name}」を公式ステータスで「{status_labels[status]}」と表示。"]
        if components:
            facts.append("対象: " + "・".join(components[:3]))
        else:
            facts.append(
                f"最終更新: {_parse_timestamp(updated_at).astimezone(JST).strftime('%m/%d %H:%M JST')}"
            )
        candidates.append(
            {
                "has_candidate": True,
                "skip_reason": "",
                "topic_type": "developing_story",
                "hook": f"⚠️ Coinbase、{status_labels[status]}を表示",
                "facts": facts,
                "opinion": "影響範囲がまだ確定していないため、復旧表示までは入出金状況の確認が必要です。",
                "source_name": "Coinbase Status",
                "source_url": COINBASE_STATUS_URL.format(incident_id=incident_id),
                "published_at": _parse_timestamp(updated_at).isoformat(),
                "evidence_anchor": name,
                "evidence_as_primary": True,
                "visual_route": "official_text_crop",
                "tags": ["仮想通貨", "Coinbase"],
                "why_now": "取引所の公式ステータスが直近4時間以内に更新されたためです。",
                "reader_interest": "売買・入出金・ネットワーク対応への影響を、公式ステータスで即時に確認できるためです。",
                "follow_value": "取引所の障害・復旧状況と、利用者への影響を継続して追えるためです。",
                "is_primary_source": True,
                "focus_signal_url": "",
            }
        )
    return candidates


def collect_direct_source_candidates(now: dt.datetime, state: dict) -> tuple[list[dict], list[dict[str, str]]]:
    """公開可能な直接一次情報候補を返す。取得失敗は他カテゴリーを止めない。"""
    candidates: list[dict] = []
    sources: list[dict[str, str]] = []

    try:
        candidate, _ = _mempool_candidate(now, state)
        if candidate:
            candidates.append(candidate)
            sources.append({"url": candidate["source_url"], "title": "mempool.space live dashboard"})
    except Exception:
        pass

    try:
        for candidate in _coinbase_status_candidates(now):
            candidates.append(candidate)
            sources.append({"url": candidate["source_url"], "title": candidate["source_name"]})
    except Exception:
        pass

    return candidates, sources
