"""INUの成長施策用Xリストを安全に同期する小さなクライアント。

投稿やいいね等のエンゲージメント操作は一切行わない。対象リストの読み書きだけを
このモジュールに閉じ込め、``いいね`` リストの重複作成や会員差分の取りこぼしを防ぐ。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from x_poster import _get_client


logger = logging.getLogger(__name__)


def _data(response: Any) -> Any:
    return getattr(response, "data", response)


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code
    return None


class XListClient:
    """OAuth 1.0aのユーザーコンテキストでXリストを扱う。

    一時的な失敗は呼び出し元が状態に残して次回再開する。ここではリスト削除を
    実装しないため、誤ってユーザーのリストを消す経路は存在しない。
    """

    def __init__(self, client: Any | None = None):
        self.client = client or _get_client()

    def owner_id(self) -> str:
        response = self.client.get_me(user_auth=True)
        value = _value(_data(response), "id")
        if not value:
            raise RuntimeError("XアカウントIDを取得できません")
        return str(value)

    def find_owned_list(self, owner_id: str, name: str) -> str | None:
        response = self.client.get_owned_lists(
            owner_id,
            max_results=100,
            list_fields=["name"],
            user_auth=True,
        )
        for item in _data(response) or []:
            if str(_value(item, "name", "")) == name:
                value = _value(item, "id")
                return str(value) if value else None
        return None

    def list_exists(self, list_id: str) -> bool:
        try:
            self.client.get_list(list_id, user_auth=True)
            return True
        except Exception as exc:
            if _status_code(exc) == 404:
                return False
            raise

    def resolve_list(self, state_list_id: str | None, name: str, allow_create: bool) -> str | None:
        """既存ID→同名の所有リスト→作成、の順で一意に解決する。"""
        if state_list_id and self.list_exists(state_list_id):
            return state_list_id
        owner_id = self.owner_id()
        owned_id = self.find_owned_list(owner_id, name)
        if owned_id:
            return owned_id
        if not allow_create:
            return None
        response = self.client.create_list(
            name=name,
            description="INUの投資・暗号資産情報リサーチ対象",
            private=False,
            user_auth=True,
        )
        created = _value(_data(response), "id")
        if not created:
            raise RuntimeError("Xリストを作成できませんでした")
        return str(created)

    def member_ids(self, list_id: str) -> set[str]:
        """ページングして現時点の会員IDを全件取得する。"""
        result: set[str] = set()
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"max_results": 100, "user_auth": True}
            if token:
                kwargs["pagination_token"] = token
            response = self.client.get_list_members(list_id, **kwargs)
            for user in _data(response) or []:
                user_id = _value(user, "id")
                if user_id:
                    result.add(str(user_id))
            meta = getattr(response, "meta", {}) or {}
            token = meta.get("next_token") if isinstance(meta, dict) else _value(meta, "next_token")
            if not token:
                return result

    def recent_tweets(self, list_id: str, max_pages: int = 3) -> list[Any]:
        """リスト全体の新着投稿をまとめて取得する（個別200アカウントを巡回しない）。"""
        tweets: list[Any] = []
        token: str | None = None
        for _ in range(max_pages):
            kwargs: dict[str, Any] = {
                "max_results": 100,
                "tweet_fields": ["author_id", "created_at", "lang", "public_metrics"],
                "user_auth": True,
            }
            if token:
                kwargs["pagination_token"] = token
            response = self.client.get_list_tweets(list_id, **kwargs)
            tweets.extend(_data(response) or [])
            meta = getattr(response, "meta", {}) or {}
            token = meta.get("next_token") if isinstance(meta, dict) else _value(meta, "next_token")
            if not token:
                break
        return tweets

    def recent_tweet_rows(self, list_id: str, max_pages: int = 3) -> list[dict[str, Any]]:
        """統合タイムラインのメディア種別を復元した投稿行を返す。

        海外KOLの動画・画像引用候補では、本文だけでなく実際に添付された
        メディアを確認する必要がある。元投稿のファイルは取得せず、メディア種別と
        元投稿IDだけを返すため、公開時はXネイティブの引用を維持できる。
        """
        rows: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(max_pages):
            kwargs: dict[str, Any] = {
                "max_results": 100,
                "tweet_fields": ["attachments", "author_id", "created_at", "lang", "public_metrics"],
                "expansions": ["attachments.media_keys"],
                "media_fields": ["media_key", "preview_image_url", "type"],
                "user_auth": True,
            }
            if token:
                kwargs["pagination_token"] = token
            response = self.client.get_list_tweets(list_id, **kwargs)
            includes = _value(response, "includes", {}) or {}
            media = _value(includes, "media", []) or []
            media_types = {
                str(_value(item, "media_key")): str(_value(item, "type", ""))
                for item in media
                if _value(item, "media_key") and _value(item, "type")
            }
            for tweet in _data(response) or []:
                attachments = _value(tweet, "attachments", {}) or {}
                keys = _value(attachments, "media_keys", []) or []
                kinds = sorted({media_types.get(str(key), "") for key in keys if media_types.get(str(key), "")})
                metrics = _value(tweet, "public_metrics", {}) or {}
                if not isinstance(metrics, dict):
                    metrics = vars(metrics) if hasattr(metrics, "__dict__") else {}
                rows.append(
                    {
                        "post_id": str(_value(tweet, "id", "")),
                        "author_id": str(_value(tweet, "author_id", "")),
                        "posted_at": str(_value(tweet, "created_at", "")),
                        "text": str(_value(tweet, "text", "")),
                        "lang": str(_value(tweet, "lang", "")),
                        "impression_count": int(metrics.get("impression_count", 0) or 0),
                        "like_count": int(metrics.get("like_count", 0) or 0),
                        "reply_count": int(metrics.get("reply_count", 0) or 0),
                        "repost_count": int(metrics.get("retweet_count", 0) or 0),
                        "quote_count": int(metrics.get("quote_count", 0) or 0),
                        "media_types": kinds,
                        "has_video": any(kind in {"video", "animated_gif"} for kind in kinds),
                        "has_image": any(kind == "photo" for kind in kinds),
                    }
                )
            meta = _value(response, "meta", {}) or {}
            token = meta.get("next_token") if isinstance(meta, dict) else _value(meta, "next_token")
            if not token:
                break
        return rows

    def add_members(self, list_id: str, user_ids: Iterable[str]) -> tuple[list[str], list[str]]:
        """会員追加を個別に隔離し、成功・保留IDを返す。

        X側が書き込み上限を返した場合は、その場で追加を止める。上限中に残り
        全件へ同じリクエストを繰り返すと、次の正規更新までの余力も失われるため、
        保留として状態へ残し、次回の小分け同期で再開する。
        """
        completed: list[str] = []
        pending: list[str] = []
        queue = [str(user_id) for user_id in user_ids]
        for index, user_id in enumerate(queue):
            try:
                self.client.add_list_member(list_id, user_id, user_auth=True)
                completed.append(user_id)
            except Exception as exc:
                # 既存会員は同期済みとして扱う。一時エラー・権限エラーは次回に持ち越す。
                if _status_code(exc) == 409 or "already" in str(exc).lower():
                    completed.append(user_id)
                else:
                    logger.warning("リストへの追加を保留しました: %s / %s", user_id, exc)
                    pending.append(user_id)
                    if _status_code(exc) == 429:
                        pending.extend(queue[index + 1:])
                        logger.info("Xリスト追加の上限に達したため、残り%d件は次回へ繰り越します", len(queue) - index - 1)
                        break
        return completed, pending

    def remove_members(self, list_id: str, user_ids: Iterable[str]) -> tuple[list[str], list[str]]:
        """会員削除を個別に隔離する。リストそのものは決して削除しない。"""
        completed: list[str] = []
        pending: list[str] = []
        for user_id in user_ids:
            try:
                self.client.remove_list_member(list_id, str(user_id), user_auth=True)
                completed.append(str(user_id))
            except Exception as exc:
                if _status_code(exc) == 404 or "not a member" in str(exc).lower():
                    completed.append(str(user_id))
                else:
                    logger.warning("リストからの除外を保留しました: %s / %s", user_id, exc)
                    pending.append(str(user_id))
        return completed, pending
