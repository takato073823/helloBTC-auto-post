"""INUの自動投稿に共通する編集憲法と公開前の品質ゲート。"""

from __future__ import annotations

import re


# リサーチ、本文作成、公開のすべてが同じ判断軸を使うための最小の憲法。
# 成長のために投稿本数を埋めることは目的に含めない。
EDITORIAL_CONSTITUTION = """
INUは、今この瞬間に投資家が知る意味のある一次情報だけを扱う。
一つの投稿では、一つの変化を伝える。見出しで変化を明示し、事実では数字・条件・決定を示し、
最後に僕の見方として「何が変わるか」または「次に何を見るか」を一つだけ加える。
投稿本数、話題の多さ、他媒体の見出しだけでは公開しない。新しい事実、読者への具体的な影響、
その事実を一目で確認できる意味のある画像または動画がそろわなければ見送る。
""".strip()

AUTO_POST_PLAYBOOK = """
公開前に必ず次を満たす。
1. Why now: 今投稿する根拠が、公式発表時刻・価格節目・制度変更・新しい数値のどれかで明確。
2. What changed: 見出しと事実に、数字・条件・決定・需給・価格反応の少なくとも一つがある。
3. Reader value: 読者が次に確認すべき対象を一つだけ具体的に示せる。
4. Visual proof: 画像は本文の根拠を補強する公式資料、データ、相場画面、または出来事を直感的に伝える独自ビジュアル。
5. Follow-through: 続報として追う対象が明確で、単なる「フォローしてください」ではない。
""".strip()

_MATERIAL_CHANGE_RE = re.compile(
    r"(?:承認|却下|可決|否決|開始|終了|停止|禁止|解禁|導入|撤回|引き上げ|引き下げ|"
    r"増額|減額|上方修正|下方修正|流入|流出|買い戻し|売却|購入|発行|償還|最高値|最安値|"
    r"急騰|急落|反転|金利|利回り|入札|ETF|決算|売上|利益|供給|需要|ハッキング|流出|清算|提携)"
)
_MATERIAL_NUMBER_RE = re.compile(
    r"(?:[$¥€£]\s?\d|\d[\d,.]*\s?(?:%|％|ドル|円|億|万|兆|BTC|ETH|株|bp|ベーシス))",
    re.IGNORECASE,
)
_GENERIC_WHY_NOW_RE = re.compile(
    r"^(?:公式(?:ページ|サイト|資料)?(?:が|を)?(?:更新|公表|発表|公開)(?:された|した|したため)?|"
    r"新しい(?:情報|資料)(?:が|を)?(?:出た|公開された)|重要(?:な)?(?:情報|ニュース)(?:のため)?)。?$"
)


def compact_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def has_material_change(value: object) -> bool:
    text = str(value or "")
    return bool(_MATERIAL_CHANGE_RE.search(text) or _MATERIAL_NUMBER_RE.search(text))


def validate_auto_post_quality(candidate: dict) -> None:
    """投稿候補が「今読む価値のある一件」になっているかを機械的に下限確認する。"""
    hook = " ".join(str(candidate.get("hook", "")).split())
    facts = [" ".join(str(value).split()) for value in candidate.get("facts", []) if str(value).strip()]
    why_now = " ".join(str(candidate.get("why_now", "")).split())
    reader_interest = " ".join(str(candidate.get("reader_interest", "")).split())
    follow_value = " ".join(str(candidate.get("follow_value", "")).split())
    opinion = " ".join(str(candidate.get("opinion", "")).split())

    if len(hook) < 12:
        raise ValueError("見出しが短すぎて、何が変化したか伝わりません")
    if not 1 <= len(facts) <= 2 or any(len(fact) < 12 for fact in facts):
        raise ValueError("事実は具体的な1〜2文に絞る必要があります")
    if not has_material_change(" ".join([hook, *facts, str(candidate.get("evidence_anchor", ""))])):
        raise ValueError("数字・条件・決定など、読者が判断できる変化がありません")
    if len(why_now) < 18 or _GENERIC_WHY_NOW_RE.fullmatch(why_now):
        raise ValueError("今投稿する必然性が具体的ではありません")
    if len(reader_interest) < 18:
        raise ValueError("読者が今見る理由が具体的ではありません")
    if len(opinion) < 18 or "僕" not in opinion:
        raise ValueError("僕の見方として次に見る対象が不足しています")

    compact_why = compact_text(why_now)
    compact_interest = compact_text(reader_interest)
    compact_follow = compact_text(follow_value)
    if compact_why in compact_interest or compact_interest in compact_why:
        raise ValueError("今投稿する理由と読者価値が同じ内容です")
    if compact_follow and (
        compact_follow in compact_interest or compact_interest in compact_follow
    ):
        raise ValueError("継続フォロー価値が読者価値の言い換えです")
