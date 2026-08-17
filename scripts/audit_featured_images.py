#!/usr/bin/env python3
"""公開中の自動生成アイキャッチをGemini Visionで監査する。"""

from __future__ import annotations

import html
import json
import os
import re

import requests

from generator import _review_generated_image


LEGACY_FEATURED_RE = re.compile(r"/featured-\d+\.(?:jpe?g|png)$", re.IGNORECASE)
REPLACED_FEATURED_RE = re.compile(
    r"/featured-(?:repaired|replaced)-.+-\d+\.(?:jpe?g|png)$",
    re.IGNORECASE,
)


def is_legacy_generated_image(url: str) -> bool:
    """差し替え済み・ガイド用画像を除き、旧自動生成画像だけを対象にする。"""
    return bool(LEGACY_FEATURED_RE.search((url or "").split("?", 1)[0]))


def is_auditable_generated_image(url: str, *, include_replaced: bool = True) -> bool:
    """現在の自動生成画像を対象にし、手作業のガイド画像などは除外する。"""
    clean_url = (url or "").split("?", 1)[0]
    return is_legacy_generated_image(clean_url) or (
        include_replaced and bool(REPLACED_FEATURED_RE.search(clean_url))
    )


def fetch_posts(base_url: str, since: str) -> list[dict]:
    response = requests.get(
        f"{base_url.rstrip('/')}/wp-json/wp/v2/posts",
        params={
            "after": f"{since}T00:00:00",
            "per_page": 100,
            "orderby": "date",
            "order": "desc",
            "_embed": "wp:featuredmedia",
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def featured_image_url(post: dict) -> str:
    media = post.get("_embedded", {}).get("wp:featuredmedia", [])
    return media[0].get("source_url", "") if media else ""


def main() -> None:
    from google import genai

    base_url = os.getenv("WP_URL", "https://hellobtc.jp")
    since = os.getenv("AUDIT_SINCE", "2026-08-01")
    include_replaced = os.getenv("AUDIT_INCLUDE_REPLACED", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    targets = []
    for post in fetch_posts(base_url, since):
        image_url = featured_image_url(post)
        if is_auditable_generated_image(image_url, include_replaced=include_replaced):
            targets.append((post, image_url))

    rejected = 0
    for index, (post, image_url) in enumerate(targets, start=1):
        image_response = requests.get(image_url, timeout=60)
        image_response.raise_for_status()
        title = html.unescape(post.get("title", {}).get("rendered", ""))
        passed, reason = _review_generated_image(
            client,
            image_response.content,
            trusted_brand=None,
            visual_brief=title,
        )
        status = "PASS" if passed else "REJECT"
        rejected += int(not passed)
        print(
            "AUDIT_RESULT\t"
            + json.dumps(
                {
                    "status": status,
                    "index": index,
                    "slug": post.get("slug", ""),
                    "title": title,
                    "image_url": image_url,
                    "reason": reason,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    print(
        f"AUDIT_SUMMARY\tchecked={len(targets)}\trejected={rejected}\tpassed={len(targets) - rejected}",
        flush=True,
    )


if __name__ == "__main__":
    main()
