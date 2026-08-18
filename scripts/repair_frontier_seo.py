"""helloBTC の重要記事を改稿し、競合URLを安全に統合する保守スクリプト。

GitHub Actions から WordPress REST API を使って一度だけ実行する。削除対象は
完全削除せずゴミ箱へ移し、変更前データを JSON に保存する。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from urllib.parse import unquote

from wp_poster import WordPressAPI


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SITE = "https://hellobtc.jp"
CANONICAL_SOLANA_ID = 1998
CANONICAL_SOLANA_SLUG = "solana-sol-blockchain-investment-guide"

# Search Console と WordPress API の全件確認で特定した、同一検索意図の旧記事。
SOLANA_DUPLICATES = [
    (2143, "solana-sol-blockchain-guide-2026-3"),
    (2131, "solana-sol-complete-guide-blockchain"),
    (1986, "solana-sol-guide-2026"),
    (1869, "solana-blockchain-basics-purchase-guide"),
    (1857, "solana-sol-blockchain-guide-2026-2"),
    (1712, "solana-blockchain-investment-guide-2024"),
    (1700, "solana-sol-blockchain-guide-2024-3"),
    (1618, "solana-sol-blockchain-guide-2026"),
    (1606, "solana-sol-beginners-guide-2026"),
    (1547, "solana-blockchain-guide-2024-2"),
    (1535, "solana-sol-blockchain-guide-2024-2"),
    (1490, "solana-blockchain-guide-beginner"),
    (1455, "solana-sol-blockchain-investment-guide-2026"),
    (1443, "solana-sol-blockchain-guide-2024"),
    (1409, "solana-blockchain-guide-2024"),
    (1397, "solana-sol-blockchain-tps-guide"),
    (1350, "solana-sol-guide-blockchain-technology"),
    (1315, "solana-sol-blockchain-2024"),
]

PILLAR_REWRITES = {
    142: {
        "slug": "what-is-cryptocurrency",
        "legacy_slug": "仮想通貨とは何か？わかりやすく解説｜特徴・仕組み・始め方まで徹底ガイド",
        "title": "仮想通貨とは？仕組み・始め方・リスクを初心者向けに解説",
        "excerpt": "仮想通貨（暗号資産）の意味、ブロックチェーンの仕組み、始め方、価格変動・詐欺・秘密鍵などのリスクを初心者向けに整理する。",
    },
    129: {
        "slug": "how-to-earn-crypto-beginners",
        "legacy_slug": "【初心者向け】仮想通貨で稼ぐを叶える「3つのステップ」と安全な始め方",
        "title": "仮想通貨で稼ぐ方法は？初心者向け5つの選択肢と損失リスク",
        "excerpt": "仮想通貨で利益を狙う5つの方法を、難易度・主なリスク・確認事項で比較する。利益を保証せず、初心者が損失を抑える判断基準を解説。",
    },
    430: {
        "slug": "crypto-tax-guide",
        "legacy_slug": "仮想通貨の税金完全ガイド｜初心者が知るべき税務の基礎",
        "title": "仮想通貨の税金はいつかかる？計算・確定申告の基本を解説",
        "excerpt": "暗号資産の売却・決済・交換で生じる所得、計算方法、確定申告の確認点を国税庁資料に沿って整理。20万円基準の誤解にも注意。",
    },
    CANONICAL_SOLANA_ID: {
        "slug": CANONICAL_SOLANA_SLUG,
        "title": "Solana（SOL）とは？仕組み・特徴・買い方・リスクを解説",
        "excerpt": "Solana（SOL）の仕組み、用途、入手前に確認すべき保管・障害・価格変動リスクを、公式資料へのリンク付きで初心者向けに解説する。",
    },
}


def _p(text: str, class_name: str = "") -> str:
    attrs = f' {{"className":"{class_name}"}}' if class_name else ""
    html_class = f' class="{class_name}"' if class_name else ""
    return f'<!-- wp:paragraph{attrs} -->\n<p{html_class}>{text}</p>\n<!-- /wp:paragraph -->'


def _h2(text: str) -> str:
    return f"<!-- wp:heading -->\n<h2>{text}</h2>\n<!-- /wp:heading -->"


def _h3(text: str) -> str:
    return f'<!-- wp:heading {{"level":3}} -->\n<h3>{text}</h3>\n<!-- /wp:heading -->'


def _ul(items: list[str]) -> str:
    return "<!-- wp:list -->\n<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>\n<!-- /wp:list -->"


def _source_box(items: list[tuple[str, str]]) -> str:
    links = "".join(
        f'<li><a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a></li>'
        for label, url in items
    )
    return (
        '<!-- wp:group {"className":"hellobtc-official-sources"} -->\n'
        '<div class="wp-block-group hellobtc-official-sources">'
        '<h2>公式情報・一次資料</h2><p>重要な判断は、次の公式情報を直接確認してください。</p>'
        f"<ul>{links}</ul></div>\n<!-- /wp:group -->"
    )


def _related(items: list[tuple[str, str]]) -> str:
    links = "".join(f'<li><a href="{SITE}/{slug}/">{label}</a></li>' for label, slug in items)
    return (
        '<!-- wp:group {"className":"hellobtc-related-guides"} -->\n'
        '<div class="wp-block-group hellobtc-related-guides"><h2>次に読む関連ガイド</h2>'
        f"<ul>{links}</ul></div>\n<!-- /wp:group -->"
    )


def cryptocurrency_content() -> str:
    return "\n".join([
        _p("<strong>結論：</strong>仮想通貨（法律上の呼称は暗号資産）は、インターネット上で移転でき、法定通貨や資産と交換されるデジタル資産だ。銀行の預金とは性質が異なり、価格変動、事業者、詐欺、秘密鍵の管理といった固有のリスクがある。", "hellobtc-direct-answer"),
        _p("最終確認日：2026年8月19日。制度やサービス条件は変わるため、金融庁と各事業者の最新情報を確認してほしい。", "hellobtc-fact-check-date"),
        _h2("仮想通貨（暗号資産）とは何か"),
        _p("暗号資産は、電子的に記録・移転され、不特定の相手への支払いに利用でき、法定通貨と交換できる財産的価値として制度上整理されている。日本円や米ドルそのものではなく、国が価値を保証するものでもない。ビットコインやイーサリアムは代表例だが、目的と仕組みは銘柄ごとに異なる。"),
        _h3("ブロックチェーンとの関係"),
        _p("多くの暗号資産では、取引記録を複数の参加者で共有・検証するブロックチェーンが使われる。ただし、ブロックチェーンを使うから価格が上がる、改ざんや盗難が一切起きない、という意味ではない。取引所やウォレットなど周辺システムの安全性も別に確認する必要がある。"),
        _h2("何に使われるのか"),
        _ul(["価値の移転や決済", "スマートコントラクトを使うアプリケーション", "デジタル資産やサービス利用権の管理", "投資・取引（大きな価格変動を伴う）"]),
        _p("用途があることと投資価値があることは同じではない。発行主体、供給設計、利用実態、流動性、規制、システム障害を分けて調べるのが基本だ。"),
        _h2("初心者が始める前の5つの確認"),
        _ul(["金融庁の暗号資産交換業者登録一覧で事業者を確認する", "生活費や緊急資金ではなく、損失に耐えられる範囲を決める", "二段階認証を設定し、パスワードを使い回さない", "送金先アドレスとネットワークを少額で確認する", "売却・交換・決済の履歴を残し、税務上の所得を計算できるようにする"]),
        _h2("主なリスク"),
        _h3("価格変動と流動性"),
        _p("短期間に価格が大きく上下し、希望価格で売買できないことがある。レバレッジ取引では証拠金を超える損失リスクも確認が必要だ。"),
        _h3("秘密鍵・送金・詐欺"),
        _p("秘密鍵や復元フレーズを失うと資産を取り戻せない場合がある。誤送金、フィッシング、偽アプリ、SNSの投資勧誘にも注意したい。金融庁は、登録の有無だけでリスクがなくなるわけではないと案内している。"),
        _h2("よくある質問"),
        _h3("仮想通貨と電子マネーは同じ？"),
        _p("同じではない。一般的な電子マネーは円などの法定通貨を基準に発行者が管理する。暗号資産は法定通貨ではなく、市場で価格が変動する。"),
        _h3("少額でも始められる？"),
        _p("多くのサービスでは1単位未満の小口売買ができる。ただし最低注文額、手数料、スプレッドは事業者ごとに違うため、金額だけで選ばないことが重要だ。"),
        _source_box([
            ("金融庁：暗号資産に関する制度・注意喚起", "https://www.fsa.go.jp/policy/virtual_currency02/index.html"),
            ("金融庁：免許・許可・登録等を受けている事業者一覧", "https://www.fsa.go.jp/menkyo/menkyo.html"),
        ]),
        _related([
            ("ビットコインの仕組み", "bitcoin-how-it-works"),
            ("暗号資産取引所の選び方", "crypto-exchange-selection-guide-2026"),
            ("仮想通貨の税金", "crypto-tax-guide"),
            ("仮想通貨で利益を狙う方法とリスク", "how-to-earn-crypto-beginners"),
        ]),
    ])


def earning_content() -> str:
    return "\n".join([
        _p("<strong>結論：</strong>仮想通貨で利益を狙う方法には現物の長期保有、売買、ステーキング等があるが、元本や利益は保証されない。初心者は『期待利益』より、最大損失、換金性、事業者リスク、税務記録を先に比較すべきだ。", "hellobtc-direct-answer"),
        _p("最終確認日：2026年8月19日。本記事は投資助言ではなく、利益を保証しない。", "hellobtc-fact-check-date"),
        _h2("仮想通貨で利益を狙う5つの方法"),
        _h3("1. 現物を長期保有する"),
        _p("暗号資産を購入し、中長期の値上がりを待つ方法だ。仕組みは単純だが、価格下落やプロジェクトの失敗で大きく損をする可能性がある。銘柄を増やすだけでは十分な分散にならない場合もある。"),
        _h3("2. 現物を売買する"),
        _p("値動きを見て売買差益を狙う。取引回数が増えるほど、手数料、スプレッド、判断ミス、税務記録の負担が増える。『高く売り、安く買う』ことを継続できる保証はない。"),
        _h3("3. ステーキング等の報酬を得る"),
        _p("対象ネットワークへの参加やサービス利用により報酬を得る方法だ。表示利率だけでなく、価格下落、ロック期間、解除条件、バリデーターや事業者のリスク、報酬の税務上の扱いを確認する。"),
        _h3("4. 貸暗号資産・DeFiを利用する"),
        _p("貸付や分散型金融で利回りを得る選択肢だが、返済不能、スマートコントラクトの不具合、ブリッジ攻撃、運営権限、ステーブルコインの価格乖離など、現物保有とは異なる損失経路がある。"),
        _h3("5. 学習・仕事の対価として得る"),
        _p("技術開発、翻訳、コミュニティ活動などの対価として暗号資産を受け取る方法もある。投機だけに依存しない一方、受領時と売却時の記録・評価が必要になる。"),
        _h2("初心者が避けたい進め方"),
        _ul(["借入金や生活費を投じる", "『必ず儲かる』『元本保証』という勧誘を信じる", "仕組みを説明できない高利回り商品へ預ける", "レバレッジを損失上限なしで使う", "秘密鍵や二段階認証を軽視する", "売買・交換・報酬の履歴を残さない"]),
        _h2("方法を選ぶ比較軸"),
        _ul(["最悪の場合にいくら失うか", "いつ、どの通貨で換金できるか", "誰が資産を保管し、破綻時にどう扱われるか", "手数料・スプレッド・ガス代を含む実質コスト", "所得計算に必要な履歴を取得できるか"]),
        _p("海外事業者が日本居住者向けに暗号資産交換サービスを行う場合も登録が必要だ。利用前に金融庁の登録一覧、事業者の利用規約、居住地制限を確認したい。登録済みであることも無損失を保証するものではない。"),
        _source_box([
            ("金融庁：暗号資産を利用する際の注意点", "https://www.fsa.go.jp/receipt/soudansitu/advice05.html"),
            ("金融庁：免許・許可・登録等を受けている事業者一覧", "https://www.fsa.go.jp/menkyo/menkyo.html"),
        ]),
        _related([
            ("仮想通貨とは", "what-is-cryptocurrency"),
            ("暗号資産取引所の選び方", "crypto-exchange-selection-guide-2026"),
            ("仮想通貨の税金", "crypto-tax-guide"),
        ]),
    ])


def tax_content() -> str:
    return "\n".join([
        _p("<strong>結論：</strong>暗号資産を売却・決済・別の暗号資産へ交換して利益が確定した場合などは、原則として所得計算が必要だ。給与所得者に関する『20万円』は一定条件下の所得税の確定申告要否であり、20万円まで一律非課税という意味ではない。", "hellobtc-direct-answer"),
        _p("最終確認日：2026年8月19日。個別事情や制度改正で扱いが変わるため、申告時は国税庁の最新資料または税理士へ確認してほしい。", "hellobtc-fact-check-date"),
        _h2("仮想通貨の税金が関係する主な場面"),
        _p("国税庁は、暗号資産取引で生じた利益を、原則として雑所得に区分すると案内している。単に保有しているだけで含み益が出た段階とは区別し、次のような取引を記録する。"),
        _ul(["暗号資産を円などの法定通貨へ売却した", "暗号資産で商品やサービスを購入した", "暗号資産Aを暗号資産Bへ交換した", "マイニング、ステーキング等で暗号資産を取得した"]),
        _h2("所得金額の基本的な考え方"),
        _p("基本は、取引で得た対価の時価から、その暗号資産の取得価額と必要経費を差し引いて計算する。取得価額の計算には総平均法または移動平均法が使われる。多数の取引がある場合は、取引所ごとの履歴、ウォレット間移動、手数料、年末残高を照合する。"),
        _h3("暗号資産同士の交換にも注意"),
        _p("円へ戻していなくても、別の暗号資産へ交換した時点で所得計算が必要になる場合がある。交換前資産の取得価額と、交換で取得した資産の時価を記録しておく。"),
        _h2("『20万円以下なら申告不要』の誤解"),
        _p("年末調整済みで給与の支払いが1か所など、一定の給与所得者は、給与・退職所得以外の所得金額の合計が20万円を超えると所得税の確定申告が必要になる。反対に、医療費控除など別の理由で確定申告をする場合は、20万円以下の所得も申告に含める必要がある。給与の状況や他の所得で条件は変わる。"),
        _p("また、所得税の確定申告が不要でも住民税の申告が必要となる場合がある。居住する自治体の案内を確認すること。"),
        _h2("損失と経費の注意点"),
        _p("暗号資産の雑所得の損失は、給与所得など他区分の所得と自由に相殺できるものではない。翌年以降へ繰り越せると決めつけず、取引の実態と最新制度を専門家へ確認する。経費も、所得を得るために直接必要だった部分を証拠とともに説明できるようにする。"),
        _h2("申告前チェックリスト"),
        _ul(["すべての取引所・ウォレットの年間履歴を保存した", "売却、決済、交換、報酬を漏れなく抽出した", "取得価額の計算方法を一貫して適用した", "手数料や必要経費の証憑を保存した", "不明点は国税庁の相談窓口または税理士へ確認した"]),
        _source_box([
            ("国税庁：暗号資産に関する税務上の取扱い及び計算書", "https://www.nta.go.jp/publication/pamph/shotoku/kakuteishinkokukankei/kasoutuka/index.htm"),
            ("国税庁：給与所得者で確定申告が必要な人", "https://www.nta.go.jp/taxes/shiraberu/taxanswer/shotoku/1900.htm"),
            ("国税庁：給与所得者で確定申告が必要な人（20万円に関する例）", "https://www.nta.go.jp/taxes/shiraberu/taxanswer/shotoku/1906.htm"),
        ]),
        _related([
            ("仮想通貨とは", "what-is-cryptocurrency"),
            ("仮想通貨で利益を狙う方法とリスク", "how-to-earn-crypto-beginners"),
            ("暗号資産取引所の選び方", "crypto-exchange-selection-guide-2026"),
        ]),
    ])


def solana_content() -> str:
    return "\n".join([
        _p("<strong>結論：</strong>Solanaは、取引の実行とスマートコントラクトに対応するブロックチェーンで、SOLは手数料支払いとステーキング等に使われるネイティブ資産だ。処理性能だけで投資判断せず、停止履歴、バリデーター構成、アプリの安全性、SOLの価格変動を分けて確認する必要がある。", "hellobtc-direct-answer"),
        _p("最終確認日：2026年8月19日。ネットワーク仕様と稼働状況はSolana公式ドキュメントとStatusページで確認している。", "hellobtc-fact-check-date"),
        _h2("Solana（SOL）とは"),
        _p("Solanaは、複数の参加者が取引を検証する公開型ブロックチェーンだ。スマートコントラクトを使うアプリ、トークン、決済、NFTなどの基盤として利用される。SOLはネットワーク手数料やステーキングに使われるが、SolanaというネットワークとSOLという資産は同じ意味ではない。"),
        _h2("処理の仕組みと特徴"),
        _h3("Proof of Stakeと時間順序"),
        _p("SolanaはProof of Stakeを基盤とし、Proof of Historyと呼ばれる暗号学的な時間の並びを利用して、ノード間で取引順序を共有しやすくする。単一の時計が全取引を決めるという説明ではなく、合意形成を補助する仕組みとして理解するのが正確だ。"),
        _h3("手数料と実行環境"),
        _p("多数の処理を並行して扱う設計により、アプリ開発者は決済や取引などを実装できる。一方、混雑時の成功率、優先手数料、アプリ固有の処理は利用時点で変わるため、『常に高速・低コスト』と固定的に判断しない。"),
        _h2("SOLを入手・保管する前の確認"),
        _ul(["日本で利用する事業者は金融庁の登録一覧で確認する", "現物、デリバティブ、ステーキングを混同しない", "送金時はSolanaネットワークと受取アドレスを確認する", "自己管理では秘密鍵・復元フレーズをオフラインで保管する", "少額送金後に着金を確認してから本送金する"]),
        _h2("Solana固有の主なリスク"),
        _h3("ネットワーク停止・性能低下"),
        _p("過去にはネットワークが停止または性能低下した事例がある。現在の稼働状況、障害報告、復旧内容は公式Statusページで確認できる。過去の改善は将来の無停止を保証しない。"),
        _h3("アプリとトークンのリスク"),
        _p("Solana上のアプリやトークンが、Solana本体と同じ安全性を持つとは限らない。スマートコントラクト、運営権限、流動性、ブリッジ、偽トークンを個別に調べる。ウォレット接続時の署名内容にも注意が必要だ。"),
        _h3("SOLの価格・ステーキング"),
        _p("SOL価格は需給や市場全体の影響で大きく変動する。ステーキング報酬も価格下落を補う保証はなく、バリデーター、解除待ち、サービス提供者の条件を確認する。"),
        _h2("よくある質問"),
        _h3("SolanaとSOLの違いは？"),
        _p("Solanaはブロックチェーン・ネットワーク、SOLはそのネットワークで使われるネイティブ資産だ。"),
        _h3("Solanaは止まらない？"),
        _p("停止しないとは言い切れない。現在の状態と過去の障害履歴は公式Statusページで確認する。"),
        _source_box([
            ("Solana公式ドキュメント", "https://solana.com/docs"),
            ("Solana Network Health Report", "https://solana.com/news/network-health-report-june-2025"),
            ("Solana Status", "https://status.solana.com/"),
        ]),
        _related([
            ("仮想通貨とは", "what-is-cryptocurrency"),
            ("イーサリアムとは", "what-is-ethereum"),
            ("暗号資産取引所の選び方", "crypto-exchange-selection-guide-2026"),
        ]),
    ])


CONTENT_BUILDERS = {
    142: cryptocurrency_content,
    129: earning_content,
    430: tax_content,
    CANONICAL_SOLANA_ID: solana_content,
}


def _editable_post(wp: WordPressAPI, post_id: int) -> dict:
    return wp._request(
        "GET", f"posts/{post_id}",
        params={
            "context": "edit",
            "_fields": "id,status,slug,link,title,content,excerpt,modified,categories,tags",
        },
    )


def _same_slug(returned: str, requested: str) -> bool:
    return unquote(returned).strip("/") == requested.strip("/")


def record_old_slug(
    wp: WordPressAPI,
    post_id: int,
    legacy_slug: str,
    canonical_slug: str,
    *,
    strict_legacy_match: bool = True,
) -> None:
    """WordPress標準の旧スラッグ記録を使い、旧URLを現URLへ301転送する。"""
    changed = wp.update_post(post_id, slug=legacy_slug)
    # 検証に失敗しても、公開URLが一時スラッグのまま残らないよう先に戻す。
    restored = wp.update_post(post_id, slug=canonical_slug)
    if not _same_slug(restored.get("slug", ""), canonical_slug):
        raise RuntimeError(f"正規スラッグへ戻せません: {canonical_slug} -> {restored.get('slug')}")
    # 日本語タイトル由来の旧URLはWordPressが記号・空白を正規化するため、
    # 空でないことだけを確認し、最終的な301は公開URL検証で判定する。
    if not changed.get("slug"):
        raise RuntimeError(f"旧スラッグを記録できません: {legacy_slug}")
    if strict_legacy_match and not _same_slug(changed.get("slug", ""), legacy_slug):
        raise RuntimeError(f"旧スラッグが競合しています: {legacy_slug} -> {changed.get('slug')}")


def repair(wp: WordPressAPI, backup_path: Path) -> dict:
    ids = list(PILLAR_REWRITES) + [post_id for post_id, _ in SOLANA_DUPLICATES]
    before = {post_id: _editable_post(wp, post_id) for post_id in ids}
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(
        json.dumps({"posts": list(before.values())}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("変更前バックアップを保存: %s", backup_path)

    # 重複記事は完全削除せず、復元可能なゴミ箱へ移す。
    trashed = []
    for post_id, expected_slug in SOLANA_DUPLICATES:
        post = before[post_id]
        if post.get("status") == "trash":
            continue
        if post.get("status") == "publish" and not _same_slug(post.get("slug", ""), expected_slug):
            raise RuntimeError(f"投稿ID {post_id} のスラッグが想定外です: {post.get('slug')}")
        wp.trash_post(post_id)
        trashed.append(post_id)

    # 重要ページを、確認可能な一次資料と内部リンクを持つ内容へ全面更新する。
    for post_id, fields in PILLAR_REWRITES.items():
        payload = {
            "title": fields["title"],
            "excerpt": fields["excerpt"],
            "content": CONTENT_BUILDERS[post_id](),
            "status": "publish",
            "slug": fields["slug"],
        }
        updated = wp.update_post(post_id, **payload)
        if not _same_slug(updated.get("slug", ""), fields["slug"]):
            raise RuntimeError(f"投稿ID {post_id} の正規URLを更新できません")

    # 18本の旧Solana URLを、評価を集約する1本へ転送する。
    for _, legacy_slug in SOLANA_DUPLICATES:
        record_old_slug(wp, CANONICAL_SOLANA_ID, legacy_slug, CANONICAL_SOLANA_SLUG)

    # Search Consoleに表示が残っている日本語旧URLの404も修復する。
    for post_id, fields in PILLAR_REWRITES.items():
        legacy_slug = fields.get("legacy_slug")
        if legacy_slug:
            record_old_slug(
                wp, post_id, legacy_slug, fields["slug"], strict_legacy_match=False
            )

    result = {
        "updated": list(PILLAR_REWRITES),
        "trashed": trashed,
        "redirects_recorded": len(SOLANA_DUPLICATES) + 3,
    }
    logger.info("SEO修復完了: %s", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", type=Path, default=Path("frontier-seo-backup.json"))
    args = parser.parse_args()
    wp = WordPressAPI(
        os.environ["WP_URL"],
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )
    repair(wp, args.backup)


if __name__ == "__main__":
    main()
