#!/usr/bin/env python3
"""重要ニュースのRSS見出しを軽量監視し、速報候補だけを後段へ渡す。"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
import os
import re
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from inu_hourly_dispatcher import load_state
from scraper import fetch_from_rss


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / "inu_hourly_state.json"
MAX_AGE = dt.timedelta(minutes=35)
BREAKING_COOLDOWN = dt.timedelta(minutes=40)

# 見出しだけで重要度を判定できる、誤検知の少ない語に限定する。
HIGH_IMPACT_PATTERNS = (
    r"\b(hack(?:ed)?|exploit(?:ed)?|breach|stolen|drain(?:ed)?|outage)\b",
    r"\b(approve[sd]?|reject(?:ed|s)?|ban(?:ned|s)?|suspend(?:ed|s)?)\b.*\b(etf|exchange|crypto|bitcoin|ethereum)\b",
    r"\b(sec|cftc|fed|federal reserve|bank of japan|boj)\b.*\b(approve[sd]?|reject(?:ed|s)?|sue[sd]?|ban(?:ned|s)?|rate cut|rate hike|emergency)\b",
    r"\b(bankrupt(?:cy)?|insolven(?:t|cy)|liquidat(?:ed|ion)|withdrawals? halted|files? for chapter 11)\b",
    r"\b(bitcoin|btc|ether|ethereum|eth)\b.*\b(record high|all-time high|new high|flash crash|liquidations? exceed)\b",
    r"\b(launch(?:es|ed)?|adds?|adopts?|buys?|sells?)\b.*\b(bitcoin|crypto|tokenized|stablecoin)\b.*\b(billion|bn|million|institutional|custody)\b",
)
BLOCK_PATTERNS = (
    r"what happened.*today",
    r"price prediction",
    r"could (?:rise|fall|hit|reach)",
    r"analyst says",
    r"opinion",
    r"sponsored",
)
TRACKING_KEYS = {"gclid", "fbclid", "ref", "source"}
TRUSTED_MEDIA_HOSTS = {
    "coindesk.com",
    "cointelegraph.com",
    "decrypt.co",
    "theblock.co",
    "bitcoinmagazine.com",
}


def normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    query = urlencode(
        [
            (key, val)
            for key, val in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS
        ]
    )
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), query, ""))


def _emit_output(name: str, value: str) -> None:
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with Path(output_file).open("a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def _parse_published(value: str) -> dt.datetime | None:
    try:
        return parsedate_to_datetime(value).astimezone(dt.timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def is_high_impact_title(title: str) -> bool:
    normalized = " ".join(title.lower().split())
    if any(re.search(pattern, normalized) for pattern in BLOCK_PATTERNS):
        return False
    return any(re.search(pattern, normalized) for pattern in HIGH_IMPACT_PATTERNS)


def _recent_breaking(state: dict, now: dt.datetime) -> bool:
    for row in reversed(state.get("history", [])):
        if row.get("priority") != "breaking":
            continue
        try:
            posted = dt.datetime.fromisoformat(str(row["posted_at"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            continue
        if now - posted.astimezone(dt.timezone.utc) < BREAKING_COOLDOWN:
            return True
        break
    return False


def select_signal(signals: list[dict], state: dict, now: dt.datetime) -> dict | None:
    if _recent_breaking(state, now):
        logger.info("直近に速報を投稿したためニュース速報の連投を抑止します")
        return None
    used_urls = {
        normalize_url(str(row.get("source_url", "")))
        for row in list(state.get("history", [])) + list(state.get("reservations", []))
        if row.get("source_url")
    }
    eligible: list[tuple[dt.datetime, dict]] = []
    for signal in signals:
        title = str(signal.get("title", "")).strip()
        url = str(signal.get("url", "")).strip()
        published = _parse_published(str(signal.get("published", "")))
        host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
        trusted_host = any(
            host == allowed or host.endswith(f".{allowed}")
            for allowed in TRUSTED_MEDIA_HOSTS
        )
        if not title or not url or not published or not trusted_host or not is_high_impact_title(title):
            continue
        age = now - published
        if age < dt.timedelta(minutes=-5) or age > MAX_AGE:
            continue
        if normalize_url(url) in used_urls:
            continue
        eligible.append((published, signal))
    return max(eligible, key=lambda row: row[0])[1] if eligible else None


def run(args: argparse.Namespace) -> int:
    now = dt.datetime.now(dt.timezone.utc)
    state = load_state(Path(args.state))
    signal = select_signal(fetch_from_rss(max_per_feed=4), state, now)
    if not signal:
        logger.info("即時投稿が必要な重要ニュースはありません")
        _emit_output("ready", "false")
        return 0
    normalized_url = normalize_url(signal["url"])
    digest = hashlib.sha256(normalized_url.encode()).hexdigest()[:16]
    event_key = f"breaking_news_{digest}"
    logger.info("重要ニュースを検知: %s / %s", signal["source"], signal["title"])
    _emit_output("ready", "true")
    _emit_output("event_key", event_key)
    _emit_output("source_url", normalized_url)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="INU重要ニュースの軽量監視")
    parser.add_argument("--state", default=str(STATE_PATH))
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
