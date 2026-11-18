"""Generate one finished poster, verify the actual pixels before reservation."""
import base64
import hashlib
from pathlib import Path

from inu_gpt_image import generate_image


def check_pixels(path, approved, example):
    from openai import OpenAI
    data = base64.b64encode(path.read_bytes()).decode()
    result = OpenAI(max_retries=0, timeout=90).responses.create(
        model="gpt-4.1-mini", max_output_tokens=64,
        instructions=("校閲者として画像そのものを読む。見出し、主役、主要事実が本文と一致し、"
                      "数字・左右の対応・通貨・単位・条件が正しく、日本語に誤植・欠落・見切れがなく、"
                      "具体例なら実勢値でないと明記され、スマホで読める完成ポスターのみ PASS。"
                      "数字の捏造や本文と異なる結論・判読不能・判断不能は FAIL。PASS または FAIL だけを返す。"),
        input=[{"role":"user","content":[{"type":"input_text","text":f"仮例={example}。照合用原稿：{approved}"},
            {"type":"input_image","image_url":"data:image/png;base64," + data}]}])
    if result.status != "completed" or result.output_text.strip() != "PASS":
        raise ValueError("集客ポスターの文字・数値・可読性を確認できないため公開しません")


def render(text, path: Path, code, *, example):
    brief = text.removesuffix("\n\nCryptoAcademy｜" + code)
    prompt = ("日本語のX投稿用に、文字も数字も背景も含む完成した縦4:5の金融誌の表紙を1枚生成。"
              "濃紺とコバルトブルー、白の太い見出しと黄色の強調。ブランドは helloBTC ｜ クリプトアカデミー。"
              "教材のテーマから具体物をひとつ選び、ロゴや写真風の被写体で一目で主役がわかるように。"
              "HTMLのカード画面のようにせず、原稿から短い問い・比較・ひとことの答えを大きく配置。"
              "数値を使うときは単位と対応を厳守。余分な数字や推奨、出典を創作しない。"
              + ("『架空の例』と明記。" if example else "一次情報で照合済みの事実だけを図解。実際の発表資料を装わない。")
              + "\n原稿：\n" + brief)
    generate_image(prompt, path, size="1024x1280", quality="medium")
    check_pixels(path, brief, example)
    return {"method":"whole_image_generation", "review":"passed", "code":code,
            "sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
            "caption_sha256":hashlib.sha256(text.encode()).hexdigest()}
