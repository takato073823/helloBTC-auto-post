"""INUの投稿で使う口調と表現ルール。"""

from __future__ import annotations

import re


VOICE_VERSION = "1.0"
FIRST_PERSON = "僕"

VOICE_PROMPT = """
あなたは投資情報アカウント「INU」の編集者です。
一人称は必ず「僕」を使います。友人に相場を説明するように、短く、自然な日本語で話します。
記者のように事実を並べるだけでなく、数字の意味とINUの見方を1つ加えます。
見解は「僕は、〜と見ています。」「個人的には、〜がポイントだと思います。」
「ここで注目したいのは、〜です。」のいずれかの温度感で表現します。
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
OTHER_FIRST_PERSON = re.compile(r"(?<![一-鿿])(?:俺|私|わたし|弊社)(?![一-鿿])")


def lint_voice(text: str, *, require_opinion: bool = True) -> list[str]:
    """INUの口調から外れた理由を返す。"""
    errors: list[str] = []
    for phrase in BLOCKED_PHRASES:
        if phrase in text:
            errors.append(f"禁止表現: {phrase}")
    for phrase in DOG_SPEAK:
        if phrase in text:
            errors.append(f"犬の語尾は使用しない: {phrase}")
    if OTHER_FIRST_PERSON.search(text):
        errors.append("一人称は「僕」に統一する")
    if require_opinion and "僕は" not in text and "個人的には" not in text:
        errors.append("INUの見解がない")
    emoji_count = sum(
        1
        for char in text
        if 0x1F300 <= ord(char) <= 0x1FAFF or 0x2600 <= ord(char) <= 0x27BF
    )
    if emoji_count > 2:
        errors.append("絵文字は連打しない")
    return errors
