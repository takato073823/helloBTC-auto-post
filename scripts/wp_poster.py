"""
WordPress REST API を使って記事を投稿する
"""
import requests
import base64
import logging
from datetime import datetime, timedelta, timezone

from price_formatting import format_usd_prices

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

    def get_posts_by_slugs(self, slugs, status="publish"):
        """指定スラッグの記事を一括取得して返す（編集用に raw content 込み）。

        ``status="any"`` を指定すれば下書きも含めて確認できる。新規投稿前の
        スラッグ重複防止と、公開済み記事の内部リンク同期に使う。
        """
        if not slugs:
            return []
        try:
            # 注意: params={"slug": [...]} だと slug=a&slug=b となり PHP が
            # 最後の1件に潰してしまう。WP の wp_parse_slug_list が解釈できる
            # カンマ区切り文字列で渡すことで全スラッグを配列として検索する。
            return self._request(
                "GET", "posts",
                params={
                    "slug": ",".join(slugs),
                    "per_page": 100,
                    "status": status,
                    "context": "edit",  # content.raw を取得するため
                    "_fields": "id,slug,link,title,content,excerpt",
                },
            )
        except Exception as e:
            logger.warning(f"記事の一括取得に失敗: {e}")
            return []

    def update_post_content(self, post_id, content):
        """既存記事の本文を更新する（内部リンククラスターの差し込み・同期用）。"""
        return self._request("POST", f"posts/{post_id}", json={"content": content})

    def update_post(self, post_id, **fields):
        """記事の任意フィールド（slug など）を更新する。
        slug を変更すると WordPress が旧スラッグを _wp_old_slug に記録し、
        旧URL → 新URL の301リダイレクトを自動で行う。"""
        return self._request("POST", f"posts/{post_id}", json=fields)

    def get_published_titles(self, per_page=100):
        """公開済み記事のタイトルを取得する（類似テーマの重複投稿防止用）。"""
        posts = []
        page = 1
        page_size = max(1, min(per_page, 100))
        while True:
            try:
                batch = self._request(
                    "GET", "posts",
                    params={
                        "per_page": page_size,
                        "page": page,
                        "status": "publish",
                        "_fields": "title",
                    },
                )
            except Exception as e:
                if page == 1:
                    logger.warning(f"公開済みタイトルの取得に失敗: {e}")
                break
            posts.extend(batch)
            if len(batch) < page_size:
                break
            page += 1
        return [p.get("title", {}).get("rendered", "") for p in posts]

    def get_recent_published_titles(self, days=30, per_page=100):
        """類似ニュース抑止用に、直近の公開タイトルだけを取得する。"""
        after = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        posts = []
        page = 1
        page_size = max(1, min(per_page, 100))
        while True:
            try:
                batch = self._request(
                    "GET", "posts",
                    params={
                        "after": after,
                        "per_page": page_size,
                        "page": page,
                        "status": "publish",
                        "orderby": "date",
                        "order": "desc",
                        "_fields": "title",
                    },
                )
            except Exception as e:
                if page == 1:
                    logger.warning(f"直近の公開タイトル取得に失敗: {e}")
                break
            posts.extend(batch)
            if len(batch) < page_size:
                break
            page += 1
        return [post.get("title", {}).get("rendered", "") for post in posts]

    def upsert_page(self, slug, title, content, excerpt=""):
        """固定ページをスラッグで作成または更新する。"""
        pages = self._request(
            "GET", "pages",
            params={"slug": slug, "context": "edit", "_fields": "id,slug,link"},
        )
        payload = {
            "slug": slug,
            "title": title,
            "content": content,
            "excerpt": excerpt,
            "status": "publish",
        }
        if pages:
            return self._request("POST", f"pages/{pages[0]['id']}", json=payload)
        return self._request("POST", "pages", json=payload)

    def update_current_user_profile(self, **fields):
        """投稿者情報をGoogle Newsの透明性要件に合わせて更新する。"""
        user = self._request("GET", "users/me", params={"context": "edit"})
        return self._request("POST", f"users/{user['id']}", json=fields)

    def get_published_posts_with_content(self):
        """公開済み記事をraw本文付きで全件取得する（保守作業専用）。"""
        posts = []
        page = 1
        while True:
            try:
                batch = self._request(
                    "GET", "posts",
                    params={
                        "per_page": 100,
                        "page": page,
                        "status": "publish",
                        "context": "edit",
                        "_fields": "id,slug,title,content",
                    },
                )
            except Exception as e:
                # 最終ページはWordPressが400を返す。1ページ目の失敗だけはログに残す。
                if page == 1:
                    logger.warning(f"公開済み本文の取得に失敗: {e}")
                break
            posts.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return posts

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

    def post_article(self, title, content, excerpt, tags=None, category_id=None,
                     featured_media_id=None, status="publish", slug=None,
                     featured_image_url=None, article_section="ニュース",
                     meta_description=None):
        """WordPress に記事を投稿。status は 'publish' または 'draft'"""
        # サイト共通の価格表記: タイトルは「6万4,600ドル」、本文は「64,600ドル」。
        title = format_usd_prices(title, for_title=True)
        content = format_usd_prices(content, for_title=False)
        excerpt = format_usd_prices(excerpt, for_title=False)
        meta_description = format_usd_prices(meta_description, for_title=False)
        tag_ids = []
        if tags:
            for tag_name in tags[:8]:
                tag_id = self.get_or_create_tag(tag_name)
                if tag_id:
                    tag_ids.append(tag_id)

        # スラッグに説明書きが混じった場合も、URLに使う先頭部分だけに絞る。
        if slug:
            clean_slug = slug.split(" ")[0].strip().lower()  # 説明書き除去

        post_data = {
            "title": title,
            "content": content,
            # SEO SIMPLE PACK 連携プラグインがこの抜粋を meta description に使う。
            "excerpt": meta_description or excerpt,
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
