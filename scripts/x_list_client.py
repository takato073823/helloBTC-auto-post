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

    def add_members(self, list_id: str, user_ids: Iterable[str]) -> tuple[list[str], list[str]]:
        """会員追加を個別に隔離し、成功・保留IDを返す。"""
        completed: list[str] = []
        pending: list[str] = []
        for user_id in user_ids:
            try:
                self.client.add_list_member(list_id, str(user_id), user_auth=True)
                completed.append(str(user_id))
            except Exception as exc:
                # 既存会員は同期済みとして扱う。一時エラー・権限エラーは次回に持ち越す。
                if _status_code(exc) == 409 or "already" in str(exc).lower():
                    completed.append(str(user_id))
                else:
                    logger.warning("リストへの追加を保留しました: %s / %s", user_id, exc)
                    pending.append(str(user_id))
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
