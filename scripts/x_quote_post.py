#!/usr/bin/env python3
"""INUの意見を付けて、指定したX投稿を引用投稿する。"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import tempfile
from pathlib import Path

from x_info_poster import BLOCKING_PATTERNS, MAX_WEIGHTED_LENGTH, weighted_length
from x_poster import _get_client, _neutralize_service_domains, _secrets_available


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / "x_quote_state.json"
DEFAULT_SOURCE_ID = "2084214417856889071"
DEFAULT_KEY = "sou_usdjpy_polymarket_quote_v1"

COMMENTARY = """この45%は「確定的な予測」ではなく、Polymarket参加者の売買がつけた、この時点の市場価格です。

僕が見ているのは150円という数字より、介入後も円買いが続くかどうか。材料ひとつで確率は動くので、数字だけで方向を決めつけない方がいいと思います。"""


def validate_quote(text: str, source_id: str, key: str) -> str:
    safe_text = _neutralize_service_domains(text.strip())
    if not re.fullmatch(r"[0-9]{1,19}", source_id):
        raise ValueError("引用元の投稿IDが不正です")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", key):
        raise ValueError("二重投稿防止キーの形式が不正です")
    if re.search(r"https?://|www\.", safe_text, flags=re.IGNORECASE):
        raise ValueError("引用投稿の本文にはURLを含めません")
    blocked = [pattern for pattern in BLOCKING_PATTERNS if pattern in safe_text]
    if blocked:
        raise ValueError(f"禁止表現があります: {blocked}")
    length = weighted_length(safe_text)
    if length > MAX_WEIGHTED_LENGTH:
        raise ValueError(f"Xの文字数上限を超えます: {length}/{MAX_WEIGHTED_LENGTH}")
    if "僕" not in safe_text:
        raise ValueError("INUの意見には一人称『僕』が必要です")
    if re.search(r"(?:^|[。！？\s])INUは", safe_text):
        raise ValueError("一人称に『INUは』を使用できません")
    return safe_text


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "posts": []}
    with path.open(encoding="utf-8") as handle:
        state = json.load(handle)
    state.setdefault("version", 1)
    state.setdefault("posts", [])
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def post_quote(text: str, source_id: str) -> str | None:
    if not _secrets_available():
        logger.error("X APIシークレット未設定のため引用投稿を中止します")
        return None
    try:
        response = _get_client().create_tweet(text=text, quote_tweet_id=source_id)
        tweet_id = str(response.data["id"])
        logger.info("X引用投稿完了: https://x.com/i/web/status/%s", tweet_id)
        return tweet_id
    except Exception as exc:
        # 引用が拒否された場合も通常投稿へ切り替えず、重複を防ぐため停止する。
        logger.error("X引用投稿に失敗しました（代替投稿は行いません）: %s", exc)
        return None


def run(args: argparse.Namespace) -> int:
    text = validate_quote(args.text, args.source_id, args.key)
    state_path = Path(args.state)
    state = load_state(state_path)
    if any(row.get("key") == args.key for row in state["posts"]):
        logger.info("この引用投稿は実行済みです: %s", args.key)
        return 0

    logger.info("引用投稿プレビュー（引用元ID: %s）\n%s", args.source_id, text)
    if not args.live:
        return 0

    tweet_id = post_quote(text, args.source_id)
    if not tweet_id:
        return 1

    state["posts"].append(
        {
            "key": args.key,
            "source_tweet_id": args.source_id,
            "tweet_id": tweet_id,
            "posted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
    )
    save_state(state_path, state)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="INUのX引用投稿")
    parser.add_argument("--live", action="store_true", help="Xへ実際に引用投稿する")
    parser.add_argument("--source-id", default=DEFAULT_SOURCE_ID)
    parser.add_argument("--key", default=DEFAULT_KEY)
    parser.add_argument("--state", default=str(STATE_PATH))
    parser.add_argument("--text", default=COMMENTARY)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        return run(build_parser().parse_args(argv))
    except (ValueError, json.JSONDecodeError) as exc:
        logger.error("引用投稿を中止: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
