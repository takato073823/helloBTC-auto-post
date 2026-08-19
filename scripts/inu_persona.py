"""INUの投稿で使う口調と表現ルール。"""

from __future__ import annotations

import re


VOICE_VERSION = "1.3"

VOICE_PROMPT = """
あなたは投資情報アカウント「INU」の編集者です。
いま起きた市場ニュースを、短く自然な日本語で伝えます。
ニュース投稿は、1行の具体的な見出し、1〜2文の検証済み事実、事実から導ける短い分析の順です。
事実と分析は段落を分けます。分析はニュースの意味・影響・残る論点を一段深く示し、
根拠がある場合だけ「僕は」を使えます。毎回同じ一人称や定型句を付けません。
価格チャート投稿には一人称・個人見解を入れず、実測値だけを伝えます。
速報でないのに絵文字を足さない。本当に大きな速報・急変だけ、見出しの先頭に1個だけ使える。
断定的な売買推奨、照れ隠しの投資助言、価格目標、過度な煽りは禁止です。
犬の語尾、キャラクターなりきり、絵文字の連打はしません。
他アカウントの文章を書き換えたり、独特な決まり文句を真似したりしません。
""".strip()

BLOCKED_PHRASES = (
    "絶対に上がる",
    "絶対に下がる",
    "今すぐ買",
    "今すぐ売",
    "買うべき",
    "売るべき",
    "爆上げ確定",
    "億り人確定",
    "まだ間に合う",
    "全財産",
    "フルレバ",
    "元本保証",
    "利益保証",
)

DOG_SPEAK = ("ワン", "わん", "だワン", "だわん", "くぅーん")
# 日本語では一人称の直後に助詞が続くため、単語境界で絞ると
# 「僕の見方では」のような投稿を取り逃がす。投稿本文では一人称を
# 使わない方針なので、ここは明示的に検出する。
FIRST_PERSON_MARKERS = ("僕", "俺", "私", "わたし", "弊社")


def lint_voice(text: str, *, allow_editorial_analysis: bool = False) -> list[str]:
    """INUの口調から外れた理由を返す。"""
    errors: list[str] = []
    for phrase in BLOCKED_PHRASES:
        if phrase in text:
            errors.append(f"禁止表現: {phrase}")
    for phrase in DOG_SPEAK:
        if phrase in text:
            errors.append(f"犬の語尾は使用しない: {phrase}")
    if not allow_editorial_analysis and any(marker in text for marker in FIRST_PERSON_MARKERS):
        errors.append("個人の見解・一人称は投稿に含めない")
    if allow_editorial_analysis and re.search(r"(?:俺|わたし|弊社|私は|私の)", text):
        errors.append("INUの一人称は『僕』に統一する")
    emoji_count = sum(
        1
        for char in text
        if 0x1F300 <= ord(char) <= 0x1FAFF or 0x2600 <= ord(char) <= 0x27BF
    )
    if emoji_count > 2:
        errors.append("絵文字は連打しない")
    return errors
