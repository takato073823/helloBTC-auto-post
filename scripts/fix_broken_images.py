#!/usr/bin/env python3
"""
既存BingX記事の壊れた画像（地域制限「Access prohibited」/404のライブ撮影）を修正する。

原因: GitHub Actions の米国IPが bingx.com にブロックされ、Playwright が
エラー画面を撮影 → それがアイキャッチ＆本文画像になっていた。

対処:
  1) 本文から「スクショ画像」(<img alt="BingX <key>">、key は imgN 以外) を除去。
  2) アイキャッチがスクショ（ファイル名に -img を含まない）なら、本文中の
     Imagen画像(alt="BingX imgN")のメディアIDに差し替える。
冪等。スクショが無い記事・手動作成記事はスキップ。
"""
import logging
import os
import re

from wp_poster import WordPressAPI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# スクショ画像 = <figure> 有無を問わず alt="BingX <key>"（key は img で始まらない）
_SCREENSHOT_FIGURE = re.compile(
    r'<figure>\s*<img[^>]*\balt="BingX (?!img)[a-z_]+"[^>]*>\s*</figure>', re.IGNORECASE
)
_SCREENSHOT_IMG = re.compile(
    r'<img[^>]*\balt="BingX (?!img)[a-z_]+"[^>]*>', re.IGNORECASE
)
# Imagen画像の src を取り出す（アイキャッチ差し替え用）
_IMAGEN_IMG_SRC = re.compile(
    r'<img[^>]*\balt="BingX img\d+"[^>]*\bsrc="([^"]+)"', re.IGNORECASE
)
_IMG_SRC_ALT = re.compile(r'<img[^>]*\bsrc="([^"]+)"[^>]*\balt="BingX img\d+"', re.IGNORECASE)


def find_media_id_by_url(wp, url: str):
    """画像URLから WordPress メディアIDを逆引きする。"""
    base = url.split("/")[-1].rsplit(".", 1)[0]  # 拡張子なしファイル名
    try:
        items = wp._request("GET", "media", params={"search": base, "per_page": 10, "_fields": "id,source_url"})
        for m in items:
            if m.get("source_url") == url:
                return m["id"]
        if items:
            return items[0]["id"]
    except Exception as e:
        logger.warning(f"  メディアID逆引き失敗 ({base}): {e}")
    return None


def main():
    wp = WordPressAPI(
        os.environ["WP_URL"],
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )

    posts = wp._request(
        "GET", "posts",
        params={"search": "BingX", "per_page": 100, "status": "publish",
                "context": "edit", "_fields": "id,slug,content,featured_media"},
    )
    targets = [p for p in posts if p.get("slug", "").startswith("bingx-")]
    logger.info(f"BingX自動記事: {len(targets)}件をチェック")

    fixed = 0
    for p in targets:
        pid = p["id"]
        raw = p.get("content", {}).get("raw", "")
        if not _SCREENSHOT_IMG.search(raw):
            continue  # スクショ無し → スキップ

        # 1) 本文からスクショ画像を除去
        new_content = _SCREENSHOT_FIGURE.sub("", raw)
        new_content = _SCREENSHOT_IMG.sub("", new_content)

        fields = {"content": new_content}

        # 2) アイキャッチがスクショなら Imagen画像へ差し替え
        feat_id = p.get("featured_media", 0)
        replace_featured = False
        if feat_id:
            try:
                fm = wp._request("GET", f"media/{feat_id}", params={"_fields": "source_url"})
                fn = fm.get("source_url", "").split("/")[-1]
                if "-img" not in fn:  # スクショ or 非Imagen
                    replace_featured = True
            except Exception:
                replace_featured = True

        if replace_featured:
            m = _IMAGEN_IMG_SRC.search(new_content) or _IMG_SRC_ALT.search(new_content)
            if m:
                new_feat = find_media_id_by_url(wp, m.group(1))
                if new_feat:
                    fields["featured_media"] = new_feat

        try:
            wp.update_post(pid, **fields)
            note = f"アイキャッチ→{fields.get('featured_media','据置')}"
            logger.info(f"[ok] {p['slug']} (ID {pid}): スクショ除去・{note}")
            fixed += 1
        except Exception as e:
            logger.error(f"[fail] {p['slug']} (ID {pid}): {e}")

    logger.info(f"完了: {fixed}件修正")


if __name__ == "__main__":
    main()
