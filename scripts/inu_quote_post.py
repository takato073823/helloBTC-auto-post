#!/usr/bin/env python3
"""元投稿のネイティブ動画・画像を保ったINU引用投稿を公開する。"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
from pathlib import Path

from inu_post import MAX_WEIGHTED_LENGTH, validate_post, weighted_length
from x_poster import _neutralize_service_domains, post_video_reference_tweet


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / "inu_quote_post_state.json"


def load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {"posted": []}
    state = json.loads(path.read_text(encoding="utf-8"))
    state.setdefault("posted", [])
    return state


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_quote(post_key: str, source_tweet_id: str, text: str, state: dict) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{5,100}", post_key):
        raise ValueError("投稿キーが不正です")
    if not re.fullmatch(r"\d{15,22}", source_tweet_id):
        raise ValueError("引用元の投稿IDが不正です")
    if any(row.get("post_key") == post_key for row in state["posted"]):
        raise RuntimeError("この引用投稿はすでに公開済みです")
    safe_text = _neutralize_service_domains(text.strip())
    if re.search(r"https?://|www\.", safe_text, flags=re.IGNORECASE):
        raise ValueError("本文に外部URLを直書きできません")
    validate_post(safe_text)
    video_url = f"https://x.com/i/status/{source_tweet_id}/video/1"
    if weighted_length(safe_text + "\n\n" + video_url) > MAX_WEIGHTED_LENGTH:
        raise ValueError("動画参照URLを含めるとXの文字数上限を超えます")
    return safe_text


def publish(
    post_key: str,
    source_tweet_id: str,
    text: str,
    *,
    state_path: Path = STATE_PATH,
    poster=post_video_reference_tweet,
) -> str:
    state = load_state(state_path)
    safe_text = validate_quote(post_key, source_tweet_id, text, state)
    tweet_id = poster(safe_text, source_tweet_id)
    if not tweet_id:
        raise RuntimeError("X引用投稿に失敗しました。文字だけの代替投稿は行っていません")
    state["posted"].append(
        {
            "post_key": post_key,
            "source_tweet_id": source_tweet_id,
            "delivery_mode": "x_native_video_reference",
            "tweet_id": str(tweet_id),
            "posted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
    )
    save_state(state, state_path)
    return str(tweet_id)


def main(args: argparse.Namespace) -> int:
    state = load_state()
    safe_text = validate_quote(args.post_key, args.source_tweet_id, args.text, state)
    logger.info("元投稿のネイティブ動画を参照します（動画・画像は再アップロードしません）: %s", args.source_tweet_id)
    logger.info("投稿本文:\n%s", safe_text)
    if not args.live:
        logger.info("ドライランのためXには投稿していません")
        return 0
    if os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
        raise RuntimeError("GitHub Actionsの再実行は重複投稿防止のため禁止しています")
    tweet_id = publish(args.post_key, args.source_tweet_id, args.text)
    tweet_url = f"https://x.com/hellobtc_jp/status/{tweet_id}"
    logger.info("INU引用投稿完了: %s", tweet_url)
    if output_file := os.environ.get("GITHUB_OUTPUT"):
        with Path(output_file).open("a", encoding="utf-8") as handle:
            handle.write(f"tweet_url={tweet_url}\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="INUの動画付き引用投稿")
    parser.add_argument("--post-key", required=True)
    parser.add_argument("--source-tweet-id", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--live", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(main(build_parser().parse_args()))
