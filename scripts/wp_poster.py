"""
WordPress REST API を使って記事を投稿する
"""
import json
import requests
import base64
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class WordPressAPI:
    def __init__(self, url, username, app_password):
        self.base_url = url.rstrip("/")
        credentials = f"{username}:{app_password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        self.headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/json",
        }

    def _request(self, method, endpoint, **kwargs):
        url = f"{self.base_url}/wp-json/wp/v2/{endpoint}"
        response = requests.request(method, url, headers=self.headers, timeout=30, **kwargs)
        response.raise_for_status()
        return response.json()

    def get_or_create_tag(self, tag_name):
        """タグを検索または作成してIDを返す"""
        try:
            tags = self._request("GET", "tags", params={"search": tag_name, "per_page": 5})
            for tag in tags:
                if tag["name"].lower() == tag_name.lower():
                    return tag["id"]

            # 新規作成
            new_tag = self._request("POST", "tags", json={"name": tag_name})
            return new_tag["id"]
        except Exception as e:
            logger.warning(f"タグ '{tag_name}' の処理に失敗: {e}")
            return None

    def get_or_create_category(self, name):
        """カテゴリを取得または作成して ID を返す"""
        try:
            cats = self._request("GET", "categories", params={"search": name, "per_page": 10})
            for cat in cats:
                if cat["name"] == name:
                    return cat["id"]
            new_cat = self._request("POST", "categories", json={"name": name})
            return new_cat["id"]
        except Exception as e:
            logger.warning(f"カテゴリ '{name}' の処理失敗: {e}")
            return None

    def upload_media(self, image_data, filename="featured.jpg"):
        """画像を WordPress メディアライブラリにアップロードして (ID, URL) を返す"""
        upload_headers = {
            "Authorization": self.headers["Authorization"],
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "image/jpeg",
        }
        response = requests.post(
            f"{self.base_url}/wp-json/wp/v2/media",
            headers=upload_headers,
            data=image_data,
            timeout=60,
        )
        response.raise_for_status()
        resp_data = response.json()
        media_id = resp_data["id"]
        media_url = resp_data.get("source_url", "")
        logger.info(f"画像アップロード完了 (ID: {media_id})")
        return media_id, media_url

    def _build_news_schema(self, title, excerpt, article_url, image_url=None, section="ニュース", tags=None):
        """Google News 対応 NewsArticle JSON-LD スキーマを生成して script タグで返す"""
        jst = timezone(timedelta(hours=9))
        now_iso = datetime.now(jst).isoformat()

        schema = {
            "@context": "https://schema.org",
            "@type": "NewsArticle",
            "headline": title[:110],
            "description": excerpt,
            "url": article_url,
            "datePublished": now_iso,
            "dateModified": now_iso,
            "inLanguage": "ja-JP",
            "isAccessibleForFree": True,
            "articleSection": section,
            "author": [{
                "@type": "Person",
                "name": "helloBTC編集部",
                "url": self.base_url,
            }],
            "publisher": {
                "@type": "Organization",
                "name": "helloBTC",
                "url": self.base_url,
                "logo": {
                    "@type": "ImageObject",
                    "url": f"{self.base_url}/wp-content/uploads/hellobtc-logo.png",
                    "width": 200,
                    "height": 60,
                },
            },
        }

        if tags:
            schema["keywords"] = ",".join(tags[:10])

        if image_url:
            schema["image"] = {
                "@type": "ImageObject",
                "url": image_url,
                "width": 1200,
                "height": 630,
            }

        schema_json = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        return f'<script type="application/ld+json">{schema_json}</script>\n'

    def post_article(self, title, content, excerpt, tags=None, category_id=None,
                     featured_media_id=None, status="publish", slug=None,
                     featured_image_url=None, article_section="ニュース"):
        """WordPress に記事を投稿。status は 'publish' または 'draft'"""
        tag_ids = []
        if tags:
            for tag_name in tags[:8]:
                tag_id = self.get_or_create_tag(tag_name)
                if tag_id:
                    tag_ids.append(tag_id)

        # スラッグから記事 URL を構築（schema に埋め込む）
        if slug:
            clean_slug = slug.split(" ")[0].strip().lower()  # 説明書き除去
            article_url = f"{self.base_url}/{clean_slug}/"
        else:
            article_url = self.base_url

        # NewsArticle スキーマをコンテンツ先頭に追加
        schema_html = self._build_news_schema(title, excerpt, article_url,
                                              featured_image_url, article_section, tags)
        content_with_schema = schema_html + content

        post_data = {
            "title": title,
            "content": content_with_schema,
            "excerpt": excerpt,
            "status": status,
            "tags": tag_ids,
        }

        if slug:
            post_data["slug"] = clean_slug

        if category_id:
            post_data["categories"] = [category_id]

        if featured_media_id:
            post_data["featured_media"] = featured_media_id

        result = self._request("POST", "posts", json=post_data)
        logger.info(f"投稿完了: {result.get('link', '')}")
        return result
