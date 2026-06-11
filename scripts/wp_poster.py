"""
WordPress REST API を使って記事を投稿する
"""
import requests
import base64
import logging

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
        """画像を WordPress メディアライブラリにアップロードして ID を返す"""
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
        media_id = response.json()["id"]
        logger.info(f"画像アップロード完了 (ID: {media_id})")
        return media_id

    def post_article(self, title, content, excerpt, tags=None, category_id=None, featured_media_id=None):
        """WordPress に記事を投稿"""
        tag_ids = []
        if tags:
            for tag_name in tags[:8]:
                tag_id = self.get_or_create_tag(tag_name)
                if tag_id:
                    tag_ids.append(tag_id)

        post_data = {
            "title": title,
            "content": content,
            "excerpt": excerpt,
            "status": "publish",
            "tags": tag_ids,
        }

        if category_id:
            post_data["categories"] = [category_id]

        if featured_media_id:
            post_data["featured_media"] = featured_media_id

        result = self._request("POST", "posts", json=post_data)
        logger.info(f"投稿完了: {result.get('link', '')}")
        return result
