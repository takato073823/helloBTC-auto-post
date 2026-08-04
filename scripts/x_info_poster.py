#!/usr/bin/env python3
"""INUの独立した有益情報を、画像付きでXへ投稿する。"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from x_poster import _build_hashtags, _neutralize_service_domains, post_info_tweet


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
CATALOG_PATH = SCRIPT_DIR / "x_info_posts.json"
STATE_PATH = SCRIPT_DIR / "x_info_state.json"
JST = ZoneInfo("Asia/Tokyo")

NAVY = "#101820"
BLACK = "#111111"
ORANGE = "#F7931A"
WHITE = "#FFFFFF"
GRAY = "#A8ADB4"

MAX_WEIGHTED_LENGTH = 280
MAX_POSTED_HISTORY = 500
BLOCKING_PATTERNS = (
    "必ず儲かる",
    "絶対に上がる",
    "絶対に下がる",
    "買うべき",
    "売るべき",
    "利益保証",
    "元本保証",
    "価格目標",
    "今すぐ買",
    "今すぐ売",
)


def weighted_length(text: str) -> int:
    """Xの制限に対して安全側となる簡易重み（ASCII=1、その他=2）。"""
    return sum(1 if ord(char) < 128 else 2 for char in text)


def build_info_tweet(item: dict) -> str:
    category = _neutralize_service_domains(item["category"].strip())
    title = _neutralize_service_domains(item["title"].strip())
    bullets = [
        f"・{_neutralize_service_domains(value.strip())}"
        for value in item["bullets"][:3]
    ]
    hashtags = _build_hashtags(item.get("tags", []))
    return f"【{category}】{title}\n\n" + "\n".join(bullets) + f"\n\n{hashtags}"


def validate_item(item: dict) -> None:
    required = {"id", "category", "title", "bullets", "tags", "source"}
    missing = sorted(required - set(item))
    if missing:
        raise ValueError(f"投稿データの必須項目がありません: {item.get('id', '?')} {missing}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", str(item["id"])):
        raise ValueError(f"投稿IDの形式が不正です: {item['id']}")
    if not isinstance(item["bullets"], list) or not 2 <= len(item["bullets"]) <= 3:
        raise ValueError(f"要点は2〜3件必要です: {item['id']}")
    combined = " ".join(
        [item["category"], item["title"], *item["bullets"], *item.get("tags", [])]
    )
    blocked = [pattern for pattern in BLOCKING_PATTERNS if pattern in combined]
    if blocked:
        raise ValueError(f"禁止表現があります: {item['id']} {blocked}")
    if re.search(r"https?://|www\.", combined, flags=re.IGNORECASE):
        raise ValueError(f"独立情報投稿の本文にはURLを含められません: {item['id']}")
    tweet = build_info_tweet(item)
    if weighted_length(tweet) > MAX_WEIGHTED_LENGTH:
        raise ValueError(
            f"Xの文字数上限を超えます: {item['id']} "
            f"({weighted_length(tweet)}/{MAX_WEIGHTED_LENGTH})"
        )


def load_catalog(path: Path = CATALOG_PATH) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    posts = payload.get("posts", [])
    if not posts:
        raise ValueError("情報投稿ライブラリが空です")
    seen: set[str] = set()
    for item in posts:
        validate_item(item)
        if item["id"] in seen:
            raise ValueError(f"投稿IDが重複しています: {item['id']}")
        seen.add(item["id"])
    return posts


def load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {"version": 1, "cursor": 0, "posted_slots": [], "posted_ids": []}
    with path.open(encoding="utf-8") as handle:
        state = json.load(handle)
    state.setdefault("version", 1)
    state.setdefault("cursor", 0)
    state.setdefault("posted_slots", [])
    state.setdefault("posted_ids", [])
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


def select_item(posts: list[dict], state: dict, slot_key: str, post_id: str | None = None) -> dict | None:
    if any(row.get("slot") == slot_key for row in state.get("posted_slots", [])):
        return None
    if post_id:
        match = next((item for item in posts if item["id"] == post_id), None)
        if not match:
            raise ValueError(f"指定した投稿IDがありません: {post_id}")
        return match

    used = set(state.get("posted_ids", []))
    if len(used) >= len(posts):
        used.clear()
    start = int(state.get("cursor", 0)) % len(posts)
    for offset in range(len(posts)):
        item = posts[(start + offset) % len(posts)]
        if item["id"] not in used:
            return item
    return posts[start]


def mark_posted(state: dict, item: dict, slot_key: str, tweet_id: str, posts: list[dict]) -> dict:
    updated = {
        "version": 1,
        "cursor": (posts.index(item) + 1) % len(posts),
        "posted_slots": list(state.get("posted_slots", [])),
        "posted_ids": list(state.get("posted_ids", [])),
    }
    updated["posted_slots"].append(
        {"slot": slot_key, "post_id": item["id"], "tweet_id": str(tweet_id)}
    )
    updated["posted_ids"].append(item["id"])
    updated["posted_slots"] = updated["posted_slots"][-MAX_POSTED_HISTORY:]
    updated["posted_ids"] = updated["posted_ids"][-MAX_POSTED_HISTORY:]
    return updated


def _font_path() -> Path:
    candidates = []
    if os.environ.get("X_INFO_FONT"):
        candidates.append(Path(os.environ["X_INFO_FONT"]))
    candidates.append(Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"))
    spec = importlib.util.find_spec("japanize_matplotlib")
    if spec and spec.submodule_search_locations:
        candidates.append(Path(next(iter(spec.submodule_search_locations))) / "fonts" / "ipaexg.ttf")
    candidates.append(Path("/System/Library/Fonts/Hiragino Sans GB.ttc"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("日本語フォントが見つかりません。X_INFO_FONTを設定してください")


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size, index=0)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def render_card(item: dict, output_path: Path, font_path: Path | None = None) -> Path:
    font_path = font_path or _font_path()
    image = Image.new("RGB", (1600, 900), BLACK)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 18, 900), fill=ORANGE)

    badge_font = _font(font_path, 27)
    title_font = _font(font_path, 67)
    bullet_font = _font(font_path, 34)
    footer_font = _font(font_path, 25)

    draw.rounded_rectangle((72, 56, 315, 108), radius=8, fill=ORANGE)
    draw.text((98, 68), "INU NEWS", font=badge_font, fill=WHITE)
    draw.text((350, 69), item["category"], font=badge_font, fill=GRAY)

    y = 165
    for line in _wrap(item["title"], title_font, 1425, draw)[:2]:
        draw.text((76, y), line, font=title_font, fill=WHITE)
        y += 90

    y = max(y + 48, 415)
    for bullet in item["bullets"][:3]:
        draw.ellipse((82, y + 13, 103, y + 34), fill=ORANGE)
        for line in _wrap(bullet, bullet_font, 1340, draw)[:2]:
            draw.text((128, y), line, font=bullet_font, fill="#E8EAED")
            y += 48
        y += 24

    draw.line((76, 790, 1525, 790), fill="#34383D", width=2)
    source = f"INU / helloBTC   |   参考: {item['source']}"
    draw.text((76, 818), source[:100], font=footer_font, fill=GRAY)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=True)
    return output_path


def _default_slot(now: dt.datetime | None = None) -> str:
    current = (now or dt.datetime.now(dt.timezone.utc)).astimezone(JST)
    return current.strftime("%Y-%m-%d-%H")


def run(args: argparse.Namespace) -> int:
    catalog_path = Path(args.catalog)
    state_path = Path(args.state)
    posts = load_catalog(catalog_path)
    state = load_state(state_path)
    slot_key = args.slot or _default_slot()
    item = select_item(posts, state, slot_key, post_id=args.post_id)
    if not item:
        logger.info("同じ投稿枠は処理済みです: %s", slot_key)
        return 0

    tweet = build_info_tweet(item)
    validate_item(item)
    preview_output = getattr(args, "preview_output", None)
    if not args.live and preview_output:
        card_path = render_card(item, Path(preview_output))
        logger.info("ドライラン（Xへ送信しません）\n%s", tweet)
        logger.info("プレビューを保存: %s", card_path)
        return 0
    with tempfile.TemporaryDirectory(prefix="inu-x-info-") as temp_dir:
        card_path = render_card(item, Path(temp_dir) / f"{item['id']}.png")
        if not args.live:
            logger.info("ドライラン（Xへ送信しません）\n%s", tweet)
            logger.info("カード生成完了: %s", card_path)
            return 0

        tweet_id = post_info_tweet(tweet, card_path)
        if not tweet_id:
            logger.warning("投稿できなかったため履歴は更新しません")
            return 0

    updated = mark_posted(state, item, slot_key, tweet_id, posts)
    save_state(state_path, updated)
    logger.info("情報投稿履歴を更新: %s / %s", slot_key, item["id"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="INUの独立したX情報投稿")
    parser.add_argument("--live", action="store_true", help="Xへ画像付きで投稿する")
    parser.add_argument("--slot", help="二重投稿防止キー。省略時はJSTの年月日・時")
    parser.add_argument("--post-id", help="投稿ライブラリ内のIDを指定")
    parser.add_argument("--preview-output", help="ドライラン画像を指定先へ保存")
    parser.add_argument("--catalog", default=str(CATALOG_PATH))
    parser.add_argument("--state", default=str(STATE_PATH))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        logger.error("情報投稿の検証に失敗: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
