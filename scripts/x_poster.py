"""
X (Twitter) 自動投稿モジュール
記事公開後に呼び出して、タイトル・URL・ハッシュタグをツイートする。
X API v2 (tweepy) + OAuth 1.0a
"""
import os
import logging
import tweepy

logger = logging.getLogger(__name__)

_REQUIRED_ENV = ("X_API_KEY", "X_API_KEY_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")


def _secrets_available() -> bool:
    return all(os.environ.get(k) for k in _REQUIRED_ENV)


def _get_client() -> tweepy.Client:
    return tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_KEY_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )


def _build_hashtags(tags: list[str]) -> str:
    seen = set()
    result = []
    for tag in tags:
        ht = tag.strip().replace(" ", "").replace("　", "")
        if ht and ht not in seen:
            seen.add(ht)
            result.append(f"#{ht}")
        if len(result) >= 3:
            break
    if not any("仮想通貨" in t for t in result):
        result.append("#仮想通貨")
    return " ".join(result)


def post_tweet(title: str, article_url: str, tags: list[str] | None = None) -> str | None:
    """記事をXに投稿する。失敗時はNoneを返し、記事投稿は続行する。"""
    if not _secrets_available():
        logger.info("X APIシークレット未設定のためスキップ")
        return None
    try:
        title_trimmed = title[:100]
        hashtags = _build_hashtags(tags or [])
        text = f"【新着】{title_trimmed}\n\n▶ {article_url}"
        if hashtags:
            text += f"\n\n{hashtags}"

        client = _get_client()
        response = client.create_tweet(text=text)
        tweet_id = response.data["id"]
        logger.info(f"X投稿完了: https://x.com/i/web/status/{tweet_id}")
        return tweet_id
    except Exception as e:
        logger.warning(f"X投稿失敗（記事投稿は続行）: {e}")
        return None
