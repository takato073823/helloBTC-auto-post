"""Finite CryptoAcademy pre-launch series. All slot decisions use Japan time."""
import datetime as dt
import hashlib
import html
import json
import re
from pathlib import Path

START = dt.date(2026, 11, 19)
ROOT = Path(__file__).resolve().parent.parent


def assignment(slot):
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})-(09|21)-a", slot)
    if not match:
        return None
    day = (dt.date.fromisoformat(match[1]) - START).days
    if not 0 <= day < 14:
        return None
    evening = int(match[2] == "21")
    return {"code": f"CA{1 + day*2 + evening:02}", "day": day,
            "format": ("図で検証", "初心者Q&A")[(day + evening) % 2]}


# Independent lessons, not live quotes or claims of reader submissions.
# Each example explicitly labels illustrative inputs; both the caption and card
# are rendered from the same reviewed figures.
EXAMPLES = [
    ("価格と騰落率", "100円 → 90円", "翌日90円 → 100円", "10％下落の後、元の価格に戻るには約11.1％の上昇が必要。分母が違います。"),
    ("ETFの純流入", "流入 120億円", "流出 100億円", "差額は20億円の純流入。売買代金が20億円だった、という意味ではありません。"),
    ("出来高と売買代金", "10枚 × 100円", "5枚 × 200円", "枚数は半分でも、売買代金はどちらも1,000円。比較するときは単位から。"),
    ("円建ての値動き", "1ドル＝150円", "1ドル＝145円", "ドル価格が100ドルのままでも、円換算は15,000円から14,500円に変わります。"),
    ("時価総額", "価格 200円", "流通量 100万枚", "掛け算すると時価総額は2億円。実際に2億円が流入したわけではありません。"),
    ("取引板の読み方", "最良買い気配 99円", "最良売り気配 101円", "気配の差は2円。直近の約定価格だけでは、この差は分かりません。"),
    ("取引の平均単価", "1枚を100円で購入", "2枚を130円で購入", "平均は115円ではなく120円。合計360円を合計3枚で割ります。"),
    ("手数料の比べ方", "定額手数料 100円", "送金額 1,000円", "負担率は10％。同じ手数料でも送金額が1万円なら1％です。"),
    ("予測市場の変化幅", "確率表示 50％", "更新後 65％", "差は15ポイント。参加者がつけた価格の変化で、結果が確定したわけではありません。"),
    ("単純平均の落とし穴", "銘柄A：100円分が倍", "銘柄B：900円分が横ばい", "合計は1,000円から1,100円へ。2銘柄の騰落率の単純平均とは違います。"),
    ("供給量と持ち分", "保有量 10枚／全体100枚", "発行後 10枚／全体125枚", "保有枚数は同じでも、全体に占める割合は10％から8％に変わります。"),
    ("価格レンジの読み方", "安値80円・高値120円", "終値100円", "終値はレンジの中央。高値と安値のどちらが先だったかは、この数字だけでは分かりません。"),
    ("オンチェーン送金", "アドレスAから10枚送金", "アドレスBが10枚受領", "移動の記録だけでは売却の証拠になりません。同じ所有者のウォレット間でも移動できます。"),
    ("前日比の見方", "前日の終値100円", "今日の終値105円", "終値同士では5％高。日中もずっと上昇していたことまでは示しません。"),
]
QUESTIONS = [
    ("半値になった後、50％戻れば元通り？", "100円 → 50円", "50円 × 1.5 ＝ 75円", "半値から元に戻るには100％の上昇が必要。上げ下げの率は足し算できません。"),
    ("ETFの残高が増えたら全額が新規資金？", "保有量 10枚で変化なし", "1枚100円 → 110円", "評価額は1,000円から1,100円へ。残高の増加だけでは純流入を判定できません。"),
    ("取引回数が多い方が売買代金も大きい？", "100円の取引 × 10回", "2,000円の取引 × 1回", "回数が少ない後者の売買代金は前者の2倍。何を数えた数字か確認しましょう。"),
    ("ドル建てで上昇しても円建てで下がる？", "100ドル × 150円", "102ドル × 145円", "円換算は15,000円から14,790円へ。通貨が違うチャートは結果も異なります。"),
    ("1枚が安い通貨の方が規模も小さい？", "10円 × 流通100万枚", "100円 × 流通1万枚", "時価総額は前者が1,000万円、後者が100万円。単価だけでは規模は分かりません。"),
    ("板に100円とあれば全部100円で買える？", "100円の売り注文 1枚", "101円の売り注文 2枚", "板が変わらず3枚を成行で買う例では総額302円。最良気配には数量の限りがあります。"),
    ("同額ずつ買えば単価の平均は単純平均？", "100円で200円分＝2枚", "200円で200円分＝1枚", "合計400円で3枚。手数料を除いた平均取得単価は約133円で、150円ではありません。"),
    ("手数料0.1％は100円？", "取引額 1万円", "取引額 10万円", "同じ0.1％でも手数料は10円と100円。率には、何に掛けるかという条件があります。"),
    ("60％から75％は何％の増加？", "差 75−60＝15ポイント", "比 15÷60＝25％", "差と相対変化は別の数字。予測市場の表示は結果を保証しません。"),
    ("2日間それぞれ10％上がると合計20％？", "100円 → 110円", "110円 → 121円", "2日目の基準は110円なので、最初からの上昇率は21％です。"),
    ("供給上限と流通量は同じ？", "発行上限 1,000枚", "現在の流通量 400枚", "1枚100円なら、流通分の評価は4万円。上限すべてを掛けた10万円とは別です。"),
    ("ローソク足のヒゲは終値？", "期間中の高値 120円", "期間の終値 105円", "上ヒゲの先は高値。期間の最後に120円で取引された、という意味ではありません。"),
    ("アドレスが2つなら利用者も2人？", "1人がアドレスを2つ使用", "どちらも同じ日に送金", "アクティブアドレス数と人数は一致しません。利用状況を比べるときは指標の定義から。"),
    ("24時間騰落率と前日終値比は同じ？", "24時間前 100円／今110円", "前日終値 105円", "24時間比は10％、前日終値比は約4.8％。基準時刻が違えば、数字も変わります。"),
]


async def lesson(meta, slot, directory):
    from playwright.async_api import async_playwright
    from inu_live_post import validate_test_item
    qa = meta["format"] == "初心者Q&A"
    rows = QUESTIONS if qa else EXAMPLES
    title, left, right, answer = rows[(meta["day"] + (7 if qa else 0)) % len(rows)]
    code = meta["code"]
    hook = ("💬 " if qa else "🔎 ") + title
    text = f"{hook}\n\n仮の例：{left}、{right}。\n{answer}\n\nCryptoAcademy｜{code}"
    directory.mkdir(parents=True, exist_ok=True)
    image = directory / f"{slot}-{code}.png"
    parts = [html.escape(v) for v in (meta["format"], title, left, right, answer, code)]
    markup = "<meta charset=utf-8><style>*{box-sizing:border-box}body{margin:0;background:#f0f6fa;color:#142c40;font:30px sans-serif;padding:56px}small{color:#256763}h1{font-size:46px;margin:32px 0}section{background:white;border-left:10px solid #238779;padding:18px;margin:12px 0}p{line-height:1.5;font-size:32px}</style>" + ("<small>CryptoAcademy ｜ {} ｜ 仮の例</small><h1>{}</h1><section>{}</section><section>{}</section><p>{}</p><small>{}</small>".format(*parts))
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1000, "height": 680})
        await page.set_content(markup)
        await page.screenshot(path=str(image), full_page=True)
        await browser.close()
    source = image.with_suffix(".json")
    source.write_text(json.dumps({"evidence_type": "academy_worked_example", "example_code": code,
        "sha256": hashlib.sha256(image.read_bytes()).hexdigest()}))
    item = dict(id=f"academy_{code}", text=text, topic_type="academy_lesson", visual_route="academy_worked_example",
                media_path=str(image.relative_to(ROOT)), source_manifest=str(source.relative_to(ROOT)))
    validate_test_item(item)
    return item, dict(topic_type="academy_lesson", hook=hook, source_url="", published_at="", why_now="14日間の学習企画（仮例・ニュースではない）", follow_value=answer)
