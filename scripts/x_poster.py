"""
X (Twitter) 自動投稿モジュール
記事公開後に呼び出して、カテゴリ・要点箇条書き・URL・ハッシュタグをツイートする。
X API v2 (tweepy) + OAuth 1.0a

ツイート形式:
  【カテゴリ】タイトル

  ・要点1
  ・要点2
  ・要点3

  ▶ URL

  #タグ1 #タグ2 #仮想通貨
"""
import os
import logging
import re
import io
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import requests
import tweepy

logger = logging.getLogger(__name__)

_REQUIRED_ENV = ("X_API_KEY", "X_API_KEY_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
_MAX_X_IMAGE_BYTES = 5 * 1024 * 1024
_SUPPORTED_X_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
}
_OGP_CHECK_ATTEMPTS = 2
_OGP_CHECK_RETRY_SECONDS = 3
_RELATED_REPLY_RETRY_SECONDS = (5, 15, 45)

# X は ``crypto.com`` のようなサービス名も外部リンクとして解釈することがある。
# 本文中の名称だけを非リンク化し、記事URLはそのままカード表示に使う。
_SERVICE_DOMAIN_RE = re.compile(
    # ``\w`` は日本語も含むため使わない。サービス名の前後に日本語が
    # 続く ``Crypto.comの情報`` も変換しつつ、ASCIIのドメイン境界は守る。
    r"(?<![@A-Za-z0-9_.-])(?P<name>[A-Za-z0-9][A-Za-z0-9-]{1,62})\."
    r"(?P<tld>com|io|net|org|ai|jp|co|app|xyz|exchange|finance|market|global|us|me|tv)"
    # 文末のピリオドやハイフン接続はサービス名として変換する一方、
    # ``crypto.com.example`` のようにドメインが続く場合は触らない。
    r"(?![A-Za-z0-9_]|\.[A-Za-z0-9])",
    re.IGNORECASE,
)


def _neutralize_service_domains(text: str) -> str:
    """サービス名に見えるドメインだけを非リンク表記へ変換する。

    例: ``Crypto.com`` → ``Crypto(.)com``。メールアドレスや実URLは変更しない。
    """
    def replace(match: re.Match) -> str:
        # ``https://crypto.com`` のように、直前がURLスキームなら実URLとして保持する。
        if text[max(0, match.start() - 3):match.start()].lower() == "://":
            return match.group(0)
        return f"{match.group('name')}(.){match.group('tld')}"

    return _SERVICE_DOMAIN_RE.sub(replace, text)


def _secrets_available() -> bool:
    return all(os.environ.get(k) for k in _REQUIRED_ENV)


def _get_client() -> tweepy.Client:
    return tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_KEY_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )


def _get_oauth1_api() -> tweepy.API:
    """画像アップロード用のOAuth 1.0a APIクライアントを返す。"""
    auth = tweepy.OAuth1UserHandler(
        os.environ["X_API_KEY"],
        os.environ["X_API_KEY_SECRET"],
        os.environ["X_ACCESS_TOKEN"],
        os.environ["X_ACCESS_TOKEN_SECRET"],
    )
    return tweepy.API(auth)


def _download_featured_image(image_url: str, article_url: str) -> tuple[bytes, str]:
    """WordPressのアイキャッチをXへ添付できる形式で取得する。

    投稿先記事と同一ホストのHTTPS画像だけを許可し、意図しない外部URL取得と
    Xの静止画上限を超えるファイルを防ぐ。
    """
    image = urlparse((image_url or "").strip())
    article = urlparse((article_url or "").strip())
    if image.scheme != "https" or not image.hostname:
        raise ValueError("アイキャッチURLが有効なHTTPS URLではありません")
    if not article.hostname or image.hostname.lower() != article.hostname.lower():
        raise ValueError("アイキャッチURLが記事と同一ホストではありません")

    response = requests.get(
        image_url,
        headers={"User-Agent": "helloBTC-X-poster/1.0"},
        timeout=30,
    )
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    suffix = _SUPPORTED_X_IMAGE_TYPES.get(content_type)
    if not suffix:
        raise ValueError(f"X未対応の画像形式です: {content_type or 'unknown'}")
    if not response.content:
        raise ValueError("アイキャッチ画像が空です")
    if len(response.content) > _MAX_X_IMAGE_BYTES:
        raise ValueError("アイキャッチ画像がXのアップロード上限を超えています")
    return response.content, f"featured{suffix}"


def _upload_featured_image(
    api: tweepy.API,
    image_url: str,
    article_url: str,
    alt_text: str,
) -> str:
    """アイキャッチをXへ直接アップロードし、media IDを返す。"""
    image_data, filename = _download_featured_image(image_url, article_url)
    media = api.media_upload(filename=filename, file=io.BytesIO(image_data))
    media_id = str(getattr(media, "media_id_string", None) or media.media_id)
    try:
        api.create_media_metadata(media_id, (alt_text or "記事のアイキャッチ画像")[:1000])
    except Exception as e:
        # ALT設定の失敗だけで画像投稿全体を止めない。
        logger.warning("X画像のALT設定に失敗（投稿は続行）: %s", e)
    return media_id


class _OpenGraphImageParser(HTMLParser):
    """公開済み記事のog:imageだけを取り出す最小のHTMLパーサー。"""

    def __init__(self):
        super().__init__()
        self.image_url: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag.lower() != "meta" or self.image_url:
            return
        values = {key.lower(): value for key, value in attrs}
        if values.get("property", "").lower() == "og:image":
            self.image_url = values.get("content")


def _is_og_image_ready(article_url: str) -> bool:
    """記事ページのOGP画像が、公開済みの状態で取得できるか確認する。

    Xのカード表示そのものを予測する処理ではない。OGPが未反映・画像未配信の
    ときだけ、ニュース投稿の直接添付フォールバックを選ぶための自己検査である。
    """
    for attempt in range(_OGP_CHECK_ATTEMPTS):
        try:
            response = requests.get(
                article_url,
                headers={"User-Agent": "Twitterbot/1.0"},
                timeout=30,
            )
            response.raise_for_status()
            parser = _OpenGraphImageParser()
            parser.feed(response.text)
            if parser.image_url:
                _download_featured_image(parser.image_url, article_url)
                return True
            logger.info("Xカード事前確認: og:image が未反映です: %s", article_url)
        except Exception as e:
            logger.info("Xカード事前確認: OGP画像を確認できません: %s", e)

        if attempt < _OGP_CHECK_ATTEMPTS - 1:
            time.sleep(_OGP_CHECK_RETRY_SECONDS)
    return False


def _build_hashtags(tags: list[str]) -> str:
    seen = set()
    result = []
    for tag in tags:
        ht = _neutralize_service_domains(tag.strip()).replace(" ", "").replace("　", "")
        if ht and ht not in seen:
            seen.add(ht)
            result.append(f"#{ht}")
        if len(result) >= 3:
            break
    if not any("仮想通貨" in t for t in result):
        result.append("#仮想通貨")
    return " ".join(result)


def _build_tweet(
    title: str,
    article_url: str,
    article_section: str,
    tweet_bullets: list[str] | None,
    tags: list[str],
) -> str:
    category = article_section or "ニュース"
    # サービス名に含まれるドメインを先に非リンク化してから、長さを調整する。
    safe_title = _neutralize_service_domains(title)
    short_title = safe_title[:45] + "…" if len(safe_title) > 45 else safe_title

    header = f"【{category}】{short_title}"

    if tweet_bullets:
        bullets = "\n".join(
            f"・{_neutralize_service_domains(b)}" for b in tweet_bullets[:3]
        )
        body = f"{header}\n\n{bullets}"
    else:
        body = header

    hashtags = _build_hashtags(tags)
    return f"{body}\n\n▶ {article_url}\n\n{hashtags}"


@dataclass(frozen=True)
class XPostResult:
    """記事投稿と、任意の関連返信の実行結果。"""

    tweet_id: str
    related_reply_posted: bool | None = None


def _x_status_code(error: Exception) -> int | None:
    """Tweepy/requests例外からHTTPステータスを安全に取得する。"""
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _is_retryable_reply_error(error: Exception) -> bool:
    """一時的なX API障害だけを返信単位で再試行する。"""
    status = _x_status_code(error)
    if status is not None:
        return status == 429 or status >= 500
    return isinstance(error, (requests.ConnectionError, requests.Timeout))


def _post_related_reply(client: tweepy.Client, tweet_id: str, text: str) -> bool:
    """主投稿への返信を送る。一時的な障害だけを最大3回まで再試行する。"""
    for attempt, retry_seconds in enumerate(_RELATED_REPLY_RETRY_SECONDS, start=1):
        try:
            client.create_tweet(text=text, in_reply_to_tweet_id=tweet_id)
            logger.info("関連投稿を追加: %s", tweet_id)
            return True
        except Exception as error:
            status = _x_status_code(error)
            retryable = _is_retryable_reply_error(error)
            logger.warning(
                "関連投稿に失敗（tweet_id=%s, 試行=%s/%s, status=%s, retry=%s）: %s",
                tweet_id,
                attempt,
                len(_RELATED_REPLY_RETRY_SECONDS),
                status if status is not None else "unknown",
                retryable,
                error,
            )
            if not retryable or attempt == len(_RELATED_REPLY_RETRY_SECONDS):
                break
            time.sleep(retry_seconds)

    logger.error("関連投稿を送信できませんでした（要再送）: %s", tweet_id)
    return False


def post_related_reply(tweet_id: str, text: str) -> bool:
    """保存済みの主投稿に対する関連投稿を、安全に再送する。"""
    if not _secrets_available():
        logger.error("X APIシークレット未設定のため関連投稿を再送できません")
        return False
    return _post_related_reply(_get_client(), tweet_id, text)


def post_tweet(
    title: str,
    article_url: str,
    tags: list[str] | None = None,
    tweet_bullets: list[str] | None = None,
    article_section: str = "ニュース",
    featured_image_url: str | None = None,
    attach_featured_image_if_og_missing: bool = False,
    related_reply_text: str | None = None,
    return_post_result: bool = False,
) -> str | XPostResult | None:
    """記事をXに投稿する。OGP不備時はアイキャッチ添付へフォールバックする。"""
    if not _secrets_available():
        logger.info("X APIシークレット未設定のためスキップ")
        return None
    try:
        text = _build_tweet(
            title=title,
            article_url=article_url,
            article_section=article_section,
            tweet_bullets=tweet_bullets,
            tags=tags or [],
        )

        media_ids = None
        if attach_featured_image_if_og_missing and not _is_og_image_ready(article_url):
            if not featured_image_url:
                logger.warning(
                    "X投稿をスキップ（OGP画像が未準備かつアイキャッチURLがありません）: %s",
                    article_url,
                )
                return None
            media_id = _upload_featured_image(
                _get_oauth1_api(),
                image_url=featured_image_url,
                article_url=article_url,
                alt_text=title,
            )
            media_ids = [media_id]
            logger.info("XカードのOGP画像未準備のため、アイキャッチを直接添付します")

        client = _get_client()
        request = {"text": text}
        if media_ids:
            request["media_ids"] = media_ids
        response = client.create_tweet(**request)
        tweet_id = response.data["id"]
        logger.info(f"X投稿完了: https://x.com/i/web/status/{tweet_id}")
        related_reply_posted = None
        if related_reply_text:
            related_reply_posted = _post_related_reply(client, tweet_id, related_reply_text)
        result = XPostResult(
            tweet_id=tweet_id,
            related_reply_posted=related_reply_posted,
        )
        return result if return_post_result else tweet_id
    except Exception as e:
        logger.warning(f"X投稿失敗（記事投稿は続行）: {e}")
        return None


def post_info_tweet(text: str, media_path: str | Path) -> str | None:
    """独立した情報投稿を画像付きで送る。

    既存の記事投稿とは呼び出し元を分離する。画像アップロードまたは投稿に
    失敗した場合は文字だけで代替せず、Noneを返して安全にスキップする。
    """
    if not _secrets_available():
        logger.info("X APIシークレット未設定のため情報投稿をスキップ")
        return None

    path = Path(media_path)
    if not path.is_file():
        logger.warning("X情報投稿をスキップ（画像が存在しません）: %s", path)
        return None

    try:
        safe_text = _neutralize_service_domains(text)
        media = _get_oauth1_api().media_upload(filename=str(path))
        media_id = str(getattr(media, "media_id_string", None) or media.media_id)
        response = _get_client().create_tweet(text=safe_text, media_ids=[media_id])
        tweet_id = response.data["id"]
        logger.info("X情報投稿完了: https://x.com/i/web/status/%s", tweet_id)
        return tweet_id
    except Exception as e:
        logger.warning("X情報投稿失敗（既存の記事投稿には影響しません）: %s", e)
        return None
