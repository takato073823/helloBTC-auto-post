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
from collections.abc import Sequence
from pathlib import Path
import tweepy

logger = logging.getLogger(__name__)

_REQUIRED_ENV = ("X_API_KEY", "X_API_KEY_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")

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


def post_tweet(
    title: str,
    article_url: str,
    tags: list[str] | None = None,
    tweet_bullets: list[str] | None = None,
    article_section: str = "ニュース",
) -> str | None:
    """記事をXに投稿する。失敗時はNoneを返し、記事投稿は続行する。"""
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
        client = _get_client()
        response = client.create_tweet(text=text)
        tweet_id = response.data["id"]
        logger.info(f"X投稿完了: https://x.com/i/web/status/{tweet_id}")
        return tweet_id
    except Exception as e:
        logger.warning(f"X投稿失敗（記事投稿は続行）: {e}")
        return None


def post_info_tweet(text: str, media_path: str | Path | Sequence[str | Path]) -> str | None:
    """独立した情報投稿を画像付きで送る。

    既存の記事投稿とは呼び出し元を分離する。画像アップロードまたは投稿に
    失敗した場合は文字だけで代替せず、Noneを返して安全にスキップする。
    """
    if not _secrets_available():
        logger.info("X APIシークレット未設定のため情報投稿をスキップ")
        return None

    raw_paths = (
        list(media_path)
        if isinstance(media_path, Sequence) and not isinstance(media_path, (str, Path))
        else [media_path]
    )
    paths = [Path(path) for path in raw_paths]
    if not paths or len(paths) > 4 or any(not path.is_file() for path in paths):
        logger.warning("X情報投稿をスキップ（画像が不正です）: %s", paths)
        return None

    try:
        safe_text = _neutralize_service_domains(text)
        uploader = _get_oauth1_api()
        media_ids = []
        for path in paths:
            media = uploader.media_upload(filename=str(path))
            media_ids.append(str(getattr(media, "media_id_string", None) or media.media_id))
        response = _get_client().create_tweet(text=safe_text, media_ids=media_ids)
        tweet_id = response.data["id"]
        logger.info("X情報投稿完了: https://x.com/i/web/status/%s", tweet_id)
        return tweet_id
    except Exception as e:
        logger.warning("X情報投稿失敗（既存の記事投稿には影響しません）: %s", e)
        return None
