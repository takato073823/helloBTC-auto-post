"""INUのA〜D施策に使う、200件までのXアカウント・ウォッチリスト管理。

この処理がするのは、対象者の発見・適格性評価・Xリスト ``いいね`` への同期だけ。
投稿、返信、いいね、フォロー、DMなどのエンゲージメント操作は実行しない。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import re
from pathlib import Path
from typing import Any

from grok_client import generate_x_json
from inu_auto_hourly import _parse_timestamp
from x_list_client import XListClient


logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / "inu_growth_watchlist_state.json"
DENYLIST_PATH = SCRIPT_DIR / "inu_growth_watchlist_denylist.json"
LIST_NAME = "いいね"
TARGET_SIZE = 200
INITIAL_ADD_LIMIT = 50
STEADY_ADD_LIMIT = 10
MAX_DISCOVERY_CALLS = 12
MAX_CANDIDATE_EVALUATIONS = 60
MIN_FOLLOWERS = 1_000
ADMIT_SCORE = 65.0
RETAIN_SCORE = 50.0
COOLDOWN_DAYS = 30
MIN_TENURE_DAYS = 7
ACTIVE_DAYS = 14

# 情報リサーチに役立つことを確認できる論点のみ。トークン販売・価格当て等は対象にしない。
TOPIC_TERMS = (
    "bitcoin", "btc", "ethereum", "eth", "crypto", "cryptocurrency", "stablecoin", "etf",
    "onchain", "blockchain", "defi", "token", "wallet", "exchange", "web3", "mining",
    "ビットコイン", "仮想通貨", "暗号資産", "ステーブルコイン", "オンチェーン", "取引所",
    "米国株", "日本株", "株式", "決算", "金利", "債券", "インフレ", "金融", "マクロ",
    "半導体", "人工知能", "比特币", "加密", "稳定币", "链上", "交易所", "美股", "宏观",
)
BLOCKED_TERMS = (
    "airdrop", "giveaway", "referral", "invite", "招待", "キャンペーン", "プレゼント",
    "価格予想", "signals", "pump", "copytrade", "copy trade", "dm me", "100x", "稳赚",
)
JAPANESE_LANGUAGE = "ja"
# X APIが返す直近投稿の言語タグで、日本語が過半を占めることを実測する。
# プロフィール文やモデルの自己申告だけでは国・言語を確定しない。
JAPANESE_TIMELINE_SHARE = 0.60
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")

# 初回だけ最大12系統を横断する。満員後は補充数に応じて必要な分だけ実行する。
DISCOVERY_TRACKS = (
    ("ja", "日本語で、一次情報・オンチェーン・ETFフローを継続的に検証する暗号資産の専門家"),
    ("ja", "日本語で、米国株・日本株・金利・為替をデータとともに扱う投資・マクロの専門家"),
    ("ja", "日本語で、AI・半導体・テック企業の業績や投資論点を継続的に扱う分析者"),
    ("ja", "日本の制度・規制・上場企業情報を一次資料で読み解く金融・暗号資産の情報発信者"),
    ("ja", "日本語で、取引所リスク・ウォレット・オンチェーンデータを根拠付きで扱う暗号資産の分析者"),
)

WATCHLIST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "accounts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "handle": {"type": "string"},
                    "language": {"type": "string", "enum": ["ja"]},
                    "role": {"type": "string"},
                    "focus": {"type": "string"},
                    "recent_post_url": {"type": "string"},
                    "why_relevant": {"type": "string"},
                },
                "required": ["handle", "language", "role", "focus", "recent_post_url", "why_relevant"],
            },
        },
    },
    "required": ["accounts"],
}


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _data(response: Any) -> list[Any]:
    value = _value(response, "data", response)
    return value if isinstance(value, list) else []


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return _parse_timestamp(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def is_japanese_timeline(tweets: list[Any]) -> bool:
    """直近の公開投稿が日本語中心かを、X APIの言語タグで判定する。

    一時的な日本語投稿だけで海外アカウントを混ぜないよう、少なくとも2件の
    言語判定済み投稿のうち60%以上が ``ja`` の場合だけ合格とする。
    """
    languages = [
        _text(_value(tweet, "lang")).lower()
        for tweet in tweets
        if _text(_value(tweet, "lang"))
    ]
    if len(languages) < 2:
        return False
    japanese = sum(language == JAPANESE_LANGUAGE for language in languages)
    return japanese >= math.ceil(len(languages) * JAPANESE_TIMELINE_SHARE)


def _contains_term(text: str, terms: tuple[str, ...]) -> bool:
    lower = text.lower()
    for term in terms:
        token = term.lower()
        if token in {"ai", "eth"}:
            if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", lower):
                return True
        elif token in lower:
            return True
    return False


def _metrics(item: Any) -> dict[str, int]:
    raw = _value(item, "public_metrics", {}) or {}
    if not isinstance(raw, dict):
        raw = vars(raw) if hasattr(raw, "__dict__") else {}
    result: dict[str, int] = {}
    for key in ("follower_count", "like_count", "reply_count", "retweet_count", "quote_count", "impression_count"):
        try:
            # User public metricsは followers_count、Tweet public metricsは各 *_count。
            # 内部では扱いやすさのため follower_count に正規化する。
            source_key = "followers_count" if key == "follower_count" and "followers_count" in raw else key
            result[key] = int(raw.get(source_key, 0) or 0)
        except (TypeError, ValueError):
            result[key] = 0
    return result


def default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "list_name": LIST_NAME,
        "list_id": "",
        "managed_initialized": False,
        "members": {},
        "unresolved_member_ids": [],
        "last_skip_reason": "",
    }


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return default_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default_state()
    state = default_state()
    if isinstance(payload, dict):
        state.update(payload)
    if not isinstance(state.get("members"), dict):
        state["members"] = {}
    return state


def save_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    stable = dict(state)
    stable["members"] = {key: state["members"][key] for key in sorted(state["members"])}
    path.write_text(json.dumps(stable, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_denylist(path: Path = DENYLIST_PATH) -> set[str]:
    if not path.exists():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    values = raw.get("handles", raw) if isinstance(raw, dict) else raw
    return {str(value).lstrip("@").lower() for value in values or []}


def build_discovery_prompt(track: tuple[str, str], known_handles: set[str], count: int) -> str:
    language, focus = track
    # 初期充足中でも同じ候補を繰り返さないよう、200件の既知対象を全て渡す。
    # このプロンプトは6時間ごとの候補発見専用で、毎時の投稿生成には使わない。
    known = ", ".join(f"@{handle}" for handle in sorted(known_handles)[:TARGET_SIZE]) or "なし"
    return f"""
INUの成長施策A〜Dのため、X Searchで公開アカウントを探してください。
対象: {focus}
言語: {language}

条件:
- 最大{count}件。実在し、過去14日以内にこの専門領域でオリジナル投稿をしているアカウントだけ。
- フォロワーが十分にあり、広告・紹介・価格煽りよりも、データ・一次資料・具体的な分析を主に扱う。
- プロジェクトの公式告知専用、エアドロップ、コピー取引、相場予想だけのアカウントは除外。
- handleは@なし。recent_post_urlには、X Searchで確認できた直近のstatus URLを入れる。
- 既知の対象 ({known}) は重複させない。
- 確認できない推測のhandleは返さない。候補なしは正常。
""".strip()


def discover_accounts(existing_handles: set[str], needed: int, now: dt.datetime) -> list[dict[str, Any]]:
    """GrokのX Searchで候補を発見する。handleは必ず後段のX APIで実在確認する。"""
    found: dict[str, dict[str, Any]] = {}
    calls = min(MAX_DISCOVERY_CALLS, max(1, math.ceil(max(needed, 20) / 20)))
    for track in DISCOVERY_TRACKS[:calls]:
        try:
            payload, _ = generate_x_json(
                build_discovery_prompt(track, existing_handles | set(found), count=20),
                schema_name="inu_growth_watchlist_accounts",
                schema=WATCHLIST_SCHEMA,
                from_date=now.date() - dt.timedelta(days=14),
                to_date=now.date(),
                max_output_tokens=4200,
            )
        except Exception as exc:
            logger.warning("ウォッチリスト候補探索を見送りします: %s", exc)
            continue
        for row in payload.get("accounts", []):
            if not isinstance(row, dict):
                continue
            handle = _text(row.get("handle")).lstrip("@").lower()
            if HANDLE_RE.fullmatch(handle) and handle not in existing_handles:
                found.setdefault(handle, {**row, "handle": handle})
    return list(found.values())


def _profile_batches(client: Any, handles: list[str]) -> dict[str, Any]:
    profiles: dict[str, Any] = {}
    for start in range(0, len(handles), 100):
        response = client.get_users(
            usernames=handles[start:start + 100],
            user_fields=["created_at", "description", "protected", "public_metrics"],
            user_auth=True,
        )
        for profile in _data(response):
            username = _text(_value(profile, "username")).lower()
            if username:
                profiles[username] = profile
    return profiles


def _profile_batches_by_id(client: Any, user_ids: list[str]) -> dict[str, Any]:
    profiles: dict[str, Any] = {}
    for start in range(0, len(user_ids), 100):
        response = client.get_users(
            ids=user_ids[start:start + 100],
            user_fields=["created_at", "description", "protected", "public_metrics"],
            user_auth=True,
        )
        for profile in _data(response):
            user_id = str(_value(profile, "id") or "")
            if user_id:
                profiles[user_id] = profile
    return profiles


def _recent_user_tweets(client: Any, user_id: str) -> list[Any]:
    response = client.get_users_tweets(
        user_id,
        max_results=10,
        exclude=["retweets", "replies"],
        tweet_fields=["created_at", "lang", "public_metrics"],
        user_auth=True,
    )
    return _data(response)


def score_account(
    profile: Any,
    tweets: list[Any],
    candidate: dict[str, Any],
    now: dt.datetime,
    own_user_id: str,
    denylist: set[str],
) -> tuple[dict[str, Any] | None, str]:
    """公開プロフィールと直近投稿で、A〜D対象としての適格性を判定する。"""
    handle = _text(_value(profile, "username")).lower()
    user_id = str(_value(profile, "id") or "")
    if not HANDLE_RE.fullmatch(handle) or not user_id:
        return None, "invalid_profile"
    if user_id == own_user_id or handle in denylist:
        return None, "self_or_denylisted"
    if bool(_value(profile, "protected", False)):
        return None, "protected"
    bio = _text(_value(profile, "description"))
    if _contains_term(bio, BLOCKED_TERMS):
        return None, "blocked_bio"
    created_at = _parse_iso(str(_value(profile, "created_at", "")))
    if created_at and now - created_at < dt.timedelta(days=180):
        return None, "new_account"
    followers = _metrics(profile)["follower_count"]
    if followers < MIN_FOLLOWERS:
        return None, "followers_below_threshold"

    fresh: list[Any] = []
    topical_count = 0
    blocked_posts = 0
    interactions: list[int] = []
    impressions: list[int] = []
    newest: dt.datetime | None = None
    for tweet in tweets:
        posted_at = _parse_iso(str(_value(tweet, "created_at", "")))
        if not posted_at or now - posted_at > dt.timedelta(days=ACTIVE_DAYS):
            continue
        fresh.append(tweet)
        newest = max(newest, posted_at) if newest else posted_at
        body = _text(_value(tweet, "text"))
        if _contains_term(body, TOPIC_TERMS):
            topical_count += 1
        if _contains_term(body, BLOCKED_TERMS):
            blocked_posts += 1
        metrics = _metrics(tweet)
        interactions.append(metrics["like_count"] + metrics["reply_count"] * 2 + metrics["retweet_count"] * 2 + metrics["quote_count"] * 2)
        impressions.append(metrics["impression_count"])

    if not fresh:
        return None, "inactive"
    if not is_japanese_timeline(fresh):
        return None, "non_japanese_timeline"
    if topical_count == 0 and not _contains_term(bio + " " + _text(candidate.get("focus")), TOPIC_TERMS):
        return None, "not_topical"
    if blocked_posts >= max(2, len(fresh) // 2 + 1):
        return None, "promotion_heavy"

    relevance = min(35.0, 15.0 + topical_count * 10.0)
    follower_score = min(10.0, math.log10(max(followers, 1)) * 2.5)
    cadence = 15.0 if 2 <= len(fresh) <= 10 else 9.0
    median_interactions = sorted(interactions)[len(interactions) // 2] if interactions else 0
    engagement_rate = median_interactions / max(followers, 1)
    engagement = 25.0 if engagement_rate >= 0.01 else 20.0 if engagement_rate >= 0.003 else 14.0 if engagement_rate > 0 else 0.0
    visible_reach = max(impressions, default=0)
    reach_score = 15.0 if visible_reach >= 10_000 else 10.0 if visible_reach >= 1_000 else 5.0
    score = round(relevance + follower_score + cadence + engagement + reach_score, 1)
    return {
        "handle": handle,
        "user_id": user_id,
        "focus": _text(candidate.get("focus"))[:180],
        "role": _text(candidate.get("role"))[:80],
        "language": JAPANESE_LANGUAGE,
        "followers": followers,
        "score": score,
        "last_seen_at": _iso(newest or now),
        "recent_post_url": _text(candidate.get("recent_post_url")),
        "why_relevant": _text(candidate.get("why_relevant"))[:220],
        "observed_interactions": median_interactions,
        "observed_impressions": visible_reach,
        "engagement_component": engagement + reach_score,
        "hard_gate": True,
    }, ""


def select_members(state: dict[str, Any], now: dt.datetime) -> tuple[list[dict[str, Any]], list[str]]:
    """閾値・在籍期間・クールダウンを考慮し、実際にリストへ置く上位200件を返す。"""
    active: list[dict[str, Any]] = []
    removed: list[str] = []
    for handle, item in state["members"].items():
        tier = item.get("tier", "probation")
        if tier == "excluded":
            continue
        if item.get("language") != JAPANESE_LANGUAGE:
            item.update({
                "tier": "excluded",
                "exclusion_reason": "non_japanese_account",
                "cooldown_until": _iso(now + dt.timedelta(days=COOLDOWN_DAYS)),
            })
            removed.append(handle)
            continue
        last_seen = _parse_iso(item.get("last_seen_at"))
        if tier == "member" and last_seen and now - last_seen > dt.timedelta(days=ACTIVE_DAYS):
            item.update({"tier": "excluded", "exclusion_reason": "inactive", "cooldown_until": _iso(now + dt.timedelta(days=COOLDOWN_DAYS))})
            removed.append(handle)
            continue
        score = float(item.get("score", 0) or 0)
        admitted = tier == "member" and score >= RETAIN_SCORE
        if tier == "member" and not admitted:
            added_at = _parse_iso(item.get("added_at"))
            if added_at and now - added_at < dt.timedelta(days=MIN_TENURE_DAYS):
                admitted = True
            else:
                item["low_score_cycles"] = int(item.get("low_score_cycles", 0) or 0) + 1
                admitted = item["low_score_cycles"] < 2
        if tier == "probation" and score >= ADMIT_SCORE:
            cooldown = _parse_iso(item.get("cooldown_until"))
            admitted = not cooldown or cooldown <= now
        if admitted:
            active.append(item)
    active.sort(key=lambda row: (float(row.get("score", 0)), int(row.get("followers", 0))), reverse=True)
    return active[:TARGET_SIZE], removed


def refresh_member_observations(state: dict[str, Any], tweets: list[Any], now: dt.datetime) -> None:
    """リスト統合タイムラインから既存メンバーの活動・反応を再評価する。

    1投稿だけで既存アカウントを誤って除外しないよう、スコアは既存値と観測値を
    合成する。一方、直近観測が広告・紹介だけの場合は即時に適格性を失わせる。
    """
    by_author: dict[str, list[Any]] = {}
    for tweet in tweets:
        author_id = str(_value(tweet, "author_id", ""))
        if author_id:
            by_author.setdefault(author_id, []).append(tweet)
    for item in state["members"].values():
        if item.get("tier") != "member":
            continue
        observed = by_author.get(str(item.get("user_id")), [])
        if not observed:
            continue
        visible = []
        for tweet in observed:
            posted_at = _parse_iso(str(_value(tweet, "created_at", "")))
            if posted_at and now - posted_at <= dt.timedelta(days=ACTIVE_DAYS):
                visible.append(tweet)
        if not visible:
            continue
        newest = max(_parse_iso(str(_value(tweet, "created_at"))) for tweet in visible)
        item["last_seen_at"] = _iso(newest)
        text = " ".join(_text(_value(tweet, "text")) for tweet in visible)
        if len(visible) >= 2 and all(_contains_term(_text(_value(tweet, "text")), BLOCKED_TERMS) for tweet in visible):
            item.update({
                "tier": "excluded",
                "exclusion_reason": "promotion_heavy",
                "cooldown_until": _iso(now + dt.timedelta(days=COOLDOWN_DAYS)),
            })
            continue
        # 直近投稿がテーマに接続している場合だけ、観測指標でスコアを更新する。
        if not _contains_term(text, TOPIC_TERMS):
            continue
        interactions = []
        impressions = []
        for tweet in visible:
            metrics = _metrics(tweet)
            interactions.append(metrics["like_count"] + metrics["reply_count"] * 2 + metrics["retweet_count"] * 2 + metrics["quote_count"] * 2)
            impressions.append(metrics["impression_count"])
        median_interactions = sorted(interactions)[len(interactions) // 2]
        followers = max(int(item.get("followers", 0) or 0), 1)
        rate = median_interactions / followers
        observed_score = 25.0 if rate >= 0.01 else 20.0 if rate >= 0.003 else 14.0 if rate > 0 else 0.0
        reach_score = 15.0 if max(impressions, default=0) >= 10_000 else 10.0 if max(impressions, default=0) >= 1_000 else 5.0
        # 既存の関連性・フォロワー・活動性の点数を維持し、反応部分だけを更新する。
        baseline = max(0.0, float(item.get("score", 0) or 0) - float(item.get("engagement_component", 0) or 0))
        item["engagement_component"] = observed_score + reach_score
        item["score"] = round(min(100.0, baseline + item["engagement_component"]), 1)
        item["observed_interactions"] = median_interactions
        item["observed_impressions"] = max(impressions, default=0)
        item["score_history"] = (item.get("score_history", []) + [item["score"]])[-8:]


def import_existing_list_members(
    state: dict[str, Any], client: Any, actual_ids: set[str], now: dt.datetime,
    own_user_id: str, denylist: set[str],
) -> set[str]:
    """状態が空で既存の同名リストを再利用する場合も、無評価で会員を消さない。

    正常に読み取れた会員だけをその場で評価する。取得不能な会員は次回まで保留し、
    リストから除外しない。
    """
    unresolved: set[str] = set()
    profiles = _profile_batches_by_id(client, sorted(actual_ids))
    for user_id in actual_ids:
        profile = profiles.get(user_id)
        if not profile:
            unresolved.add(user_id)
            continue
        handle = _text(_value(profile, "username")).lower()
        if not handle:
            unresolved.add(user_id)
            continue
        candidate = {
            "handle": handle,
            "language": "", "role": "existing_list_member", "focus": _text(_value(profile, "description")),
            "recent_post_url": "", "why_relevant": "既存の『いいね』リストから適格性を再評価",
        }
        try:
            record, reason = score_account(profile, _recent_user_tweets(client, user_id), candidate, now, own_user_id, denylist)
        except Exception as exc:
            logger.info("既存リスト会員の評価を保留: %s / %s", user_id, exc)
            unresolved.add(user_id)
            continue
        if record:
            record.update({"tier": "member", "added_at": "", "low_score_cycles": 0, "score_history": [record["score"]]})
            state["members"][record["handle"]] = record
        else:
            # 適格性を確認できた不合格だけを、状態に理由付きで残して次の同期で除外する。
            state["members"][handle] = {
                "handle": handle, "user_id": user_id, "tier": "excluded", "score": 0,
                "exclusion_reason": reason, "cooldown_until": _iso(now + dt.timedelta(days=COOLDOWN_DAYS)),
            }
    return unresolved


def refresh_watchlist(
    state: dict[str, Any],
    client: Any,
    now: dt.datetime,
    *,
    allow_create: bool,
    dry_run: bool,
) -> dict[str, Any]:
    """候補発見・適格性確認・リスト差分同期を一回実行する。"""
    list_client = XListClient(client)
    own_user_id = list_client.owner_id()
    denylist = load_denylist()
    list_id = ""
    actual_ids: set[str] = set()
    if not dry_run:
        list_id = list_client.resolve_list(state.get("list_id") or None, LIST_NAME, allow_create) or ""
        if not list_id:
            state["last_skip_reason"] = "list_not_found_or_creation_not_allowed"
            return {"desired": 0, "added": 0, "removed": 0, "list_id": ""}
        state["list_id"] = list_id
        try:
            actual_ids = list_client.member_ids(list_id)
        except Exception as exc:
            state["last_skip_reason"] = f"list_members_unavailable: {exc}"
            return {"desired": 0, "added": 0, "removed": 0, "list_id": list_id}
        # 同名リストを既に持っていた場合、無評価で会員を消さない。まず全員を
        # X APIで確認し、失敗した会員は次回の確認まで現状維持にする。
        if not state.get("managed_initialized", False):
            unresolved = import_existing_list_members(state, client, actual_ids, now, own_user_id, denylist)
            state["unresolved_member_ids"] = sorted(unresolved)
            state["managed_initialized"] = not unresolved
        try:
            refresh_member_observations(state, list_client.recent_tweets(list_id, max_pages=3), now)
        except Exception as exc:
            # 読取不能でも、直近の確認済み状態を壊さず次回へ持ち越す。
            logger.info("既存対象の再評価を見送ります: %s", exc)
    # 除外後30日を過ぎたアカウントは、再び適格になった場合だけ再評価を許可する。
    known = {
        handle for handle, item in state["members"].items()
        if item.get("tier") != "excluded"
        or (_parse_iso(item.get("cooldown_until")) and _parse_iso(item.get("cooldown_until")) > now)
    }
    current_members = [item for item in state["members"].values() if item.get("tier") == "member"]
    needed = max(0, TARGET_SIZE - len(current_members))

    if needed:
        discovered = discover_accounts(known, min(max(needed * 2, 40), 240), now)
        # 1回の同期で数百プロフィールを個別取得しない。初回は最大60件を
        # 評価し、50件ずつリストへ反映することでX APIの読取上限にも収める。
        candidates_to_evaluate = discovered[:MAX_CANDIDATE_EVALUATIONS]
        profiles = _profile_batches(client, [row["handle"] for row in candidates_to_evaluate]) if candidates_to_evaluate else {}
        for candidate in candidates_to_evaluate:
            profile = profiles.get(candidate["handle"])
            if not profile:
                continue
            try:
                tweets = _recent_user_tweets(client, str(_value(profile, "id")))
                record, reason = score_account(profile, tweets, candidate, now, own_user_id, denylist)
            except Exception as exc:
                logger.info("候補の適格性を確認できません: @%s / %s", candidate["handle"], exc)
                continue
            if record:
                previous = state["members"].get(record["handle"], {})
                record.update({
                    "tier": previous.get("tier", "probation"),
                    "added_at": previous.get("added_at", ""),
                    "low_score_cycles": previous.get("low_score_cycles", 0),
                    "score_history": (previous.get("score_history", []) + [record["score"]])[-8:],
                })
                state["members"][record["handle"]] = record
            else:
                logger.info("候補を除外: @%s / %s", candidate["handle"], reason)

    desired, _ = select_members(state, now)
    desired_by_id = {str(row["user_id"]): row for row in desired}
    if dry_run:
        return {"desired": len(desired_by_id), "added": 0, "removed": 0, "list_id": state.get("list_id", "")}

    # リスト外の任意追加は、条件に合う対象だけを残すユーザー要件に従って除外する。
    unresolved_ids = {str(value) for value in state.get("unresolved_member_ids", [])}
    to_remove = sorted(actual_ids - set(desired_by_id) - unresolved_ids)
    removed, pending_remove = list_client.remove_members(list_id, to_remove)
    # 200件に達するまでは、Xの書き込み負荷を抑えつつ50件ずつ補充する。
    # 満員後の入れ替えだけを10件までに抑え、対象の入れ替わりを安定させる。
    add_limit = INITIAL_ADD_LIMIT if len(current_members) < TARGET_SIZE else STEADY_ADD_LIMIT
    to_add = [user_id for user_id in desired_by_id if user_id not in actual_ids][:add_limit]
    added, pending_add = list_client.add_members(list_id, to_add)
    for user_id in added:
        record = desired_by_id[user_id]
        record["tier"] = "member"
        record["added_at"] = record.get("added_at") or _iso(now)
        record["low_score_cycles"] = 0
    for user_id in removed:
        for item in state["members"].values():
            if str(item.get("user_id")) == user_id:
                item.update({"tier": "excluded", "exclusion_reason": "not_selected", "cooldown_until": _iso(now + dt.timedelta(days=COOLDOWN_DAYS))})
    state["last_skip_reason"] = "" if not (pending_add or pending_remove) else "list_sync_pending"
    state["last_refreshed_at"] = _iso(now)
    return {"desired": len(desired_by_id), "added": len(added), "removed": len(removed), "list_id": list_id}


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    state_path = Path(args.state)
    state = load_state(state_path)
    try:
        result = refresh_watchlist(
            state,
            XListClient().client,
            _utcnow(),
            allow_create=bool(args.allow_create),
            dry_run=bool(args.dry_run),
        )
        logger.info("ウォッチリスト同期: 対象=%d / 追加=%d / 除外=%d", result["desired"], result["added"], result["removed"])
    except Exception as exc:
        state["last_skip_reason"] = f"watchlist_unavailable: {exc}"
        logger.warning("ウォッチリスト同期を見送ります: %s", exc)
    save_state(state, state_path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="INU成長施策のXウォッチリスト")
    parser.add_argument("--state", default=str(STATE_PATH))
    parser.add_argument("--allow-create", action="store_true", help="『いいね』リストがない場合にだけ作成する")
    parser.add_argument("--dry-run", action="store_true", help="Xリストを書き換えずに候補を評価する")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
