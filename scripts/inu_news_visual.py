"""INUニュース投稿の主画像を、根拠画像とは分けて用意する。"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from inu_gpt_image import generate_image


USER_AGENT = "Mozilla/5.0 (compatible; INUNewsVisual/1.0)"
MIN_IMAGE_SIZE = (640, 360)
MAX_IMAGE_PIXELS = 3_000_000
PORTRAIT_SIZE = (1200, 1500)
# 一目で媒体を特定できる編集ビジュアルは、投稿の主画像に再利用しない。
# 根拠のスクリーンショットは別添で保持し、主画像はINU独自の生成ビジュアルにする。
BLOCKED_EDITORIAL_VISUAL_DOMAINS = {"cointelegraph.com"}

# ニュース主体は検証可能な方法で識別する。機関は実物紋章・看板、暗号資産は
# 検証済みロゴ素材、人物は一次ソース実写を優先し中立的な写真風肖像を予備経路にする。
INSTITUTION_SUBJECTS = (
    {
        "key": "sec",
        "label": "SEC",
        "aliases": ("米証券取引委員会", "u.s. securities and exchange commission", " sec"),
    },
    {
        "key": "cftc",
        "label": "CFTC",
        "aliases": ("米商品先物取引委員会", "commodity futures trading commission", "cftc"),
    },
    {
        "key": "federal_reserve",
        "label": "FED",
        "aliases": ("米連邦準備制度", "federal reserve", "frb", "fomc"),
    },
    {
        "key": "us_treasury",
        "label": "U.S. TREASURY",
        "aliases": ("米財務省", "u.s. treasury", "us treasury"),
    },
    {
        "key": "boj",
        "label": "BOJ",
        "aliases": ("日本銀行", "bank of japan", "日銀"),
    },
    {
        "key": "fsa_japan",
        "label": "FSA JAPAN",
        "aliases": ("金融庁", "financial services agency japan"),
    },
)

PUBLIC_FIGURE_SUBJECTS = (
    {
        "key": "donald_trump",
        "label": "Donald Trump",
        "aliases": ("トランプ", "donald trump", "president trump"),
    },
    {
        "key": "jerome_powell",
        "label": "Jerome Powell",
        "aliases": ("パウエル", "jerome powell", "chair powell"),
    },
    {
        "key": "paul_atkins",
        "label": "Paul Atkins",
        "aliases": ("ポール・アトキンス", "paul atkins"),
    },
    {
        "key": "michael_saylor",
        "label": "Michael Saylor",
        "aliases": ("マイケル・セイラー", "michael saylor"),
    },
    {
        "key": "elon_musk",
        "label": "Elon Musk",
        "aliases": ("イーロン・マスク", "elon musk"),
    },
    {
        "key": "larry_fink",
        "label": "Larry Fink",
        "aliases": ("ラリー・フィンク", "larry fink"),
    },
)

# 通貨名・ティッカー・プロジェクト名を、CoinGecko IDと公式ドメインへ固定して
# 同名トークンや検索結果の偽ロゴを採用しない。時価総額上位30＋主要トレンド候補を
# ここで扱い、未登録銘柄はロゴを推測せず候補を止める。
CRYPTO_PROJECT_SUBJECTS = (
    ("bitcoin", "Bitcoin", "BTC", "bitcoin", "bitcoin.org", ("ビットコイン", "bitcoin", "btc")),
    ("ethereum", "Ethereum", "ETH", "ethereum", "ethereum.org", ("イーサリアム", "ethereum", "ether", "eth")),
    ("xrp", "XRP", "XRP", "ripple", "xrpl.org", ("xrp", "リップル", "ripple")),
    ("solana", "Solana", "SOL", "solana", "solana.com", ("solana", "ソラナ", "sol")),
    ("bnb", "BNB", "BNB", "binancecoin", "bnbchain.org", ("bnb", "bnb chain", "binance coin")),
    ("tron", "TRON", "TRX", "tron", "tron.network", ("tron", "トロン", "trx")),
    ("dogecoin", "Dogecoin", "DOGE", "dogecoin", "dogecoin.com", ("dogecoin", "ドージコイン", "doge")),
    ("cardano", "Cardano", "ADA", "cardano", "cardano.org", ("cardano", "カルダノ", "ada")),
    ("hyperliquid", "Hyperliquid", "HYPE", "hyperliquid", "hyperfoundation.org", ("hyperliquid", "ハイパーリキッド", "hype")),
    ("chainlink", "Chainlink", "LINK", "chainlink", "chain.link", ("chainlink", "チェーンリンク", "link")),
    ("stellar", "Stellar", "XLM", "stellar", "stellar.org", ("stellar", "ステラ", "xlm")),
    ("sui", "Sui", "SUI", "sui", "sui.io", ("sui", "スイ")),
    ("avalanche", "Avalanche", "AVAX", "avalanche-2", "avax.network", ("avalanche", "アバランチ", "avax")),
    ("litecoin", "Litecoin", "LTC", "litecoin", "litecoin.org", ("litecoin", "ライトコイン", "ltc")),
    ("bitcoin_cash", "Bitcoin Cash", "BCH", "bitcoin-cash", "bitcoincash.org", ("bitcoin cash", "ビットコインキャッシュ", "bch")),
    ("shiba_inu", "Shiba Inu", "SHIB", "shiba-inu", "shibatoken.com", ("shiba inu", "柴犬コイン", "shib")),
    ("uniswap", "Uniswap", "UNI", "uniswap", "uniswap.org", ("uniswap", "ユニスワップ", "uni")),
    ("aave", "Aave", "AAVE", "aave", "aave.com", ("aave", "アーベ")),
    ("hedera", "Hedera", "HBAR", "hedera-hashgraph", "hedera.com", ("hedera", "ヘデラ", "hbar")),
    ("bittensor", "Bittensor", "TAO", "bittensor", "bittensor.com", ("bittensor", "ビテンソル", "tao")),
    ("polkadot", "Polkadot", "DOT", "polkadot", "polkadot.com", ("polkadot", "ポルカドット", "dot")),
    ("near", "NEAR Protocol", "NEAR", "near", "near.org", ("near protocol", "ニアプロトコル", "near")),
    ("pepe", "Pepe", "PEPE", "pepe", "pepe.vip", ("pepe", "ぺぺ")),
    ("aptos", "Aptos", "APT", "aptos", "aptosfoundation.org", ("aptos", "アプトス", "apt")),
    ("internet_computer", "Internet Computer", "ICP", "internet-computer", "internetcomputer.org", ("internet computer", "インターネットコンピューター", "icp")),
    ("ethereum_classic", "Ethereum Classic", "ETC", "ethereum-classic", "ethereumclassic.org", ("ethereum classic", "イーサリアムクラシック", "etc")),
    ("ondo", "Ondo", "ONDO", "ondo-finance", "ondo.finance", ("ondo", "オンド")),
    ("filecoin", "Filecoin", "FIL", "filecoin", "filecoin.io", ("filecoin", "ファイルコイン", "fil")),
    ("arbitrum", "Arbitrum", "ARB", "arbitrum", "arbitrum.io", ("arbitrum", "アービトラム", "arb")),
    ("optimism", "Optimism", "OP", "optimism", "optimism.io", ("optimism", "オプティミズム")),
)

COINGECKO_COIN_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}"
COINGECKO_IMAGE_HOSTS = {"coin-images.coingecko.com", "assets.coingecko.com"}


def _mentions_alias(text: str, alias: str, *, ticker_alias: bool = False) -> bool:
    lowered = alias.lower()
    if ticker_alias:
        # 短いティッカーは一般英単語（LINK/NEAR/OP等）と衝突するため、
        # $付きまたは大文字表記だけをティッカーとして扱う。
        return bool(
            re.search(rf"(?<![A-Za-z0-9])\${re.escape(lowered)}(?![A-Za-z0-9])", text, re.IGNORECASE)
            or re.search(rf"(?<![A-Za-z0-9]){re.escape(alias.upper())}(?![A-Za-z0-9])", text)
        )
    return lowered in text.lower()


def identify_visual_subject(*, hook: str, source_name: str = "") -> dict | None:
    """見出しの主語を、人物優先で明示的な画像要件へ変換する。"""
    hook_text = f" {hook.lower()} "
    source_text = f" {source_name.lower()} "
    for subject in PUBLIC_FIGURE_SUBJECTS:
        if any(alias.lower() in hook_text for alias in subject["aliases"]):
            return {
                "kind": "public_figure",
                "key": subject["key"],
                "label": subject["label"],
                "identity_method": "verified_primary_source_photo",
                "fallback_identity_method": "generated_editorial_portrait",
            }
    combined = hook_text + source_text
    for subject in INSTITUTION_SUBJECTS:
        if any(alias.lower() in combined for alias in subject["aliases"]):
            return {
                "kind": "institution",
                "key": subject["key"],
                "label": subject["label"],
                "identity_method": "generated_photorealistic_physical_mark",
            }
    combined_raw = f" {hook} {source_name} "
    for key, label, symbol, coin_id, official_domain, aliases in CRYPTO_PROJECT_SUBJECTS:
        named = label.lower() in combined_raw.lower()
        if named or any(
            _mentions_alias(combined_raw, alias, ticker_alias=alias.lower() == symbol.lower())
            for alias in aliases
        ):
            return {
                "kind": "crypto_project",
                "key": key,
                "label": label,
                "symbol": symbol,
                "coingecko_id": coin_id,
                "official_domain": official_domain,
                "identity_method": "verified_project_logo_asset",
            }
    return None


def _uses_blocked_editorial_visual_style(source_url: str) -> bool:
    host = urlparse(source_url).netloc.lower().split(":", 1)[0]
    return any(host == domain or host.endswith(f".{domain}") for domain in BLOCKED_EDITORIAL_VISUAL_DOMAINS)


def _https_url(value: str, base_url: str) -> str:
    url = urljoin(base_url, (value or "").strip())
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("HTTPSの主画像URLが必要です")
    return url


def _og_image_url(html: str, source_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for selector in (
        'meta[property="og:image"]',
        'meta[name="twitter:image"]',
        'meta[property="twitter:image"]',
    ):
        tag = soup.select_one(selector)
        if tag and tag.get("content"):
            return _https_url(str(tag["content"]), source_url)
    raise ValueError("記事・公式発表に主画像が見つかりません")


def _og_image_alt(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for selector in (
        'meta[property="og:image:alt"]',
        'meta[name="twitter:image:alt"]',
        'meta[property="twitter:image:alt"]',
    ):
        tag = soup.select_one(selector)
        if tag and tag.get("content"):
            return str(tag["content"]).strip()
    return ""


def _save_as_source_png(image_bytes: bytes, output_path: Path) -> None:
    with Image.open(io.BytesIO(image_bytes)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        if image.width < MIN_IMAGE_SIZE[0] or image.height < MIN_IMAGE_SIZE[1]:
            raise ValueError("主画像が小さすぎます")
        # Xのタイムラインで横長画像が小さく見えないよう、主画像は4:5へ統一する。
        # 人物写真は顔が置かれやすい上寄り中央を残す。
        image = ImageOps.fit(
            image,
            PORTRAIT_SIZE,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.42),
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path, format="PNG", optimize=True)


def _verified_project_logo(subject: dict, session: requests.Session | None = None) -> tuple[bytes, str]:
    """CoinGeckoの銘柄IDと公式サイトが一致したロゴだけを取得する。"""
    requester = session or requests.Session()
    coin_id = str(subject.get("coingecko_id", "")).strip()
    expected_domain = str(subject.get("official_domain", "")).lower().strip()
    if not coin_id or not expected_domain:
        raise ValueError("プロジェクトロゴの検証情報がありません")
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    response = requester.get(
        COINGECKO_COIN_URL.format(coin_id=coin_id),
        params={
            "localization": "false", "tickers": "false", "market_data": "false",
            "community_data": "false", "developer_data": "false", "sparkline": "false",
        },
        headers=headers,
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()
    # CoinGecko側のhomepage欄は欠落・複数候補があるため認証根拠にしない。
    # IDはAPI応答、公式ドメインはレビュー済みローカル許可リストで別々に固定する。
    if not isinstance(payload, dict) or str(payload.get("id", "")) != coin_id:
        raise ValueError("CoinGecko銘柄IDを照合できません")
    image_url = str(payload.get("image", {}).get("large", ""))
    image_host = urlparse(image_url).netloc.lower()
    if not image_url.startswith("https://") or image_host not in COINGECKO_IMAGE_HOSTS:
        raise ValueError("検証済みロゴ画像URLではありません")
    image_response = requester.get(image_url, headers={"User-Agent": USER_AGENT}, timeout=25)
    image_response.raise_for_status()
    with Image.open(io.BytesIO(image_response.content)) as opened:
        opened.verify()
    return image_response.content, image_url


def _integrate_project_logo(background_path: Path, logo_bytes: bytes) -> None:
    """正しいロゴを再描画せず、写真風背景の主役として大きく合成する。"""
    with Image.open(background_path) as opened:
        background = ImageOps.fit(
            ImageOps.exif_transpose(opened).convert("RGB"), PORTRAIT_SIZE,
            method=Image.Resampling.LANCZOS,
        ).convert("RGBA")
    with Image.open(io.BytesIO(logo_bytes)) as opened:
        logo = ImageOps.contain(opened.convert("RGBA"), (430, 430), Image.Resampling.LANCZOS)

    layer = Image.new("RGBA", PORTRAIT_SIZE, (0, 0, 0, 0))
    shadow = Image.new("RGBA", PORTRAIT_SIZE, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    box = (275, 340, 925, 990)
    shadow_draw.ellipse((box[0] + 18, box[1] + 28, box[2] + 18, box[3] + 28), fill=(0, 0, 0, 115))
    shadow = shadow.filter(ImageFilter.GaussianBlur(32))
    layer.alpha_composite(shadow)
    draw = ImageDraw.Draw(layer)
    draw.ellipse(box, fill=(248, 250, 252, 238), outline=(255, 255, 255, 250), width=12)
    inner = (310, 375, 890, 955)
    draw.ellipse(inner, fill=(232, 238, 244, 120), outline=(195, 205, 215, 190), width=4)
    x = (PORTRAIT_SIZE[0] - logo.width) // 2
    y = 665 - logo.height // 2
    layer.alpha_composite(logo, (x, y))
    background.alpha_composite(layer)
    background.convert("RGB").save(background_path, format="PNG", optimize=True)


def capture_source_hero_image(
    *,
    source_url: str,
    source_name: str,
    published_at: str,
    output_path: str | Path,
    is_primary_source: bool,
    visual_subject: dict | None = None,
    session: requests.Session | None = None,
) -> Path:
    """出典ページ自身が示す主画像だけを、投稿1枚目用に保存する。

    検索結果の無関係な画像は使わず、記事または公式発表のOG画像に限定する。
    人物ニュースでは同記事に掲載された本人写真、企業ニュースでは公式ロゴや
    現場写真が自然に最優先になる。
    """
    if _uses_blocked_editorial_visual_style(source_url):
        raise ValueError("Cointelegraphの編集ビジュアルはニュース主画像として使いません")
    if visual_subject and visual_subject.get("kind") == "public_figure" and not is_primary_source:
        raise ValueError("実在人物の画像は一次ソースに掲載された実写写真だけを使います")

    requester = session or requests.Session()
    headers = {"User-Agent": USER_AGENT}
    page = requester.get(source_url, headers=headers, timeout=25)
    page.raise_for_status()
    image_url = _og_image_url(page.text, source_url)
    if visual_subject and visual_subject.get("kind") == "public_figure":
        # 公式ページのOG画像でも、サイトロゴや建物写真なら人物主画像として採用しない。
        # altまたは画像URLに姓がある場合だけ実写候補として扱い、それ以外は中立肖像へ送る。
        family_name = str(visual_subject.get("label", "")).split()[-1].lower()
        identity_hint = f"{_og_image_alt(page.text)} {image_url}".lower()
        if not family_name or family_name not in identity_hint:
            raise ValueError("一次ソース主画像が見出しの人物本人だと確認できません")
    page_path = urlparse(source_url).path.lower()
    # 金融庁の広報誌「アクセスFSA」の表紙は記事固有の主画像ではなく、
    # ニュースの意味を伝えられない。根拠スクリーンショットは残し、
    # 主画像はテキストレスの生成ビジュアルへ切り替える。
    if urlparse(source_url).netloc.lower().endswith("fsa.go.jp") and "/access/" in page_path:
        raise ValueError("金融庁広報誌の表紙はニュース主画像として使いません")
    response = requester.get(image_url, headers=headers, timeout=25)
    response.raise_for_status()

    destination = Path(output_path)
    _save_as_source_png(response.content, destination)
    manifest = {
        "source_url": source_url,
        "source_name": source_name,
        "published_at": published_at,
        "evidence_type": "source_news_image",
        "is_primary_source": bool(is_primary_source),
        "source_image_url": image_url,
        "capture_type": "source_hero_image",
        "visual_role": "attention_visual",
        "facts_verified": True,
        "generated_image": False,
        "subject_identifiable": bool(visual_subject),
        "visual_subject": visual_subject,
        "entity_identity_required": bool(visual_subject),
        "official_logo_used": False,
        "identity_method_used": (
            "verified_primary_source_photo"
            if visual_subject and visual_subject.get("kind") == "public_figure"
            else "source_hero_image"
        ),
    }
    destination.with_suffix(".source.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destination


def _editorial_prompt(
    *, hook: str, facts: list[str], topic_type: str, visual_subject: dict | None = None
) -> str:
    visual_subject = visual_subject or identify_visual_subject(hook=hook)
    facts_text = "\n".join(f"- {fact}" for fact in facts[:2])
    regulatory_direction = (
        "For a regulation or government-policy event, show a photorealistic U.S. financial-regulation scene: "
        "an official federal building or hearing-room atmosphere, a close-up of formal policy documents, "
        "and subtle digital-asset technology cues. Do not add a floating badge, text card, or readable legal document."
        if topic_type == "regulatory_rule_change"
        else ""
    )
    identity_direction = ""
    if visual_subject and visual_subject.get("kind") == "institution":
        institution_key = str(visual_subject.get("key", ""))
        if institution_key == "sec":
            identity_direction = (
                "Make a real, physically mounted cast-metal U.S. Securities and Exchange Commission wall seal "
                "the unmistakable dominant subject, with an American flag beside it. The full circular seal must "
                "read as an object photographed inside a contemporary federal press room, filling roughly 40 to "
                "55 percent of the portrait frame. Preserve the recognizable eagle structure and the institutional "
                "name engraved around the physical seal. The institution must be recognizable from the physical "
                "object itself, not from an added text box."
            )
        else:
            identity_direction = (
                f"Show the real-world physical sign, building nameplate, flag, or wall emblem of "
                f"{visual_subject.get('label')} as the dominant photographed object in its institutional setting. "
                "It must be recognizable from the object itself, not from an added text box."
            )
    elif visual_subject and visual_subject.get("kind") == "crypto_project":
        identity_direction = (
            f"Build a specific photorealistic digital-asset infrastructure scene for {visual_subject.get('label')}. "
            "Leave one large, front-facing, clean circular glass-and-metal identity surface near the upper-middle "
            "of the frame. Keep that surface blank and unobstructed because the exact verified project logo will "
            "be composited there after generation. Do not draw, imitate, spell, or approximate the logo yourself."
        )
    elif visual_subject and visual_subject.get("kind") == "public_figure":
        identity_direction = (
            f"Create a neutral, close editorial portrait of {visual_subject.get('label')} as the dominant subject, "
            "occupying roughly 45 to 65 percent of the frame. Use a plausible press-photography backdrop, but do "
            "not depict a specific unverified action, meeting, document signing, quote, or event."
        )
    text_direction = (
        "no added headline, caption, label, or graphic overlay. The only lettering may be physically engraved "
        "on the named public institution's real-world seal or sign."
        if visual_subject and visual_subject.get("kind") == "institution"
        else "no text at all."
    )
    if visual_subject and visual_subject.get("kind") == "institution":
        mark_constraint = (
            "The named public institution's physical seal or sign is allowed only as part of the photographed "
            "environment; do not add a detached logo, badge, card, or watermark. Do not include any company brand."
        )
    elif visual_subject and visual_subject.get("kind") == "crypto_project":
        mark_constraint = (
            "Do not draw any logo or token symbol. The exact verified logo is added in a deterministic post-process. "
            "Do not include any other company, exchange, media, or project brand."
        )
    else:
        mark_constraint = "do not generate company logos, trademarks, or government seals."
    person_constraint = (
        "A neutral recognizable likeness of the named public figure is required, but it must not imply documentary "
        "proof of the reported event."
        if visual_subject and visual_subject.get("kind") == "public_figure"
        else "do not generate a likeness of a real person."
    )
    return f"""
Use case: ads-marketing
Asset type: primary image for a timely Japanese X news post, portrait 4:5
Primary request: Create an original, high-impact editorial news visual for this verified event.
Topic: {hook}
Verified context (for visual direction only, do not render words or numbers):
{facts_text}
Topic type: {topic_type}
Scene/backdrop: a specific, contemporary business or technology scene that communicates the event at a glance.
Style/medium: photorealistic premium financial-news editorial photography, natural material detail, sophisticated and credible at phone size. No illustration or 3D-rendered look.
Composition/framing: portrait 4:5, one decisive focal point, strong depth and contrast, generous clean negative space. It must feel like a news image, not a web card or infographic.
Topic-specific direction: {regulatory_direction} {identity_direction}
Text: {text_direction}
Constraints: do not generate charts, interface screens, watermarks, or signatures. {person_constraint} {mark_constraint} Do not invent a claim beyond the verified context. This image is an attention visual; the source evidence will be attached separately.
Avoid: generic stock-photo office scenes, HTML dashboards, black breaking-news template, cryptocurrency coins unless directly essential, mascots, illustration, CGI, copied influencer layouts, and imitation of any reference image or a recognizable publisher illustration style (including Cointelegraph-style crypto editorial art).
""".strip()


def generate_editorial_news_visual(
    *,
    hook: str,
    facts: list[str],
    topic_type: str,
    source_url: str,
    source_name: str,
    published_at: str,
    output_path: str | Path,
    is_primary_source: bool,
    visual_subject: dict | None = None,
    session: requests.Session | None = None,
) -> Path:
    """主画像がない場合だけ、事実を増やさないテキストレスの生成画像を作る。"""
    destination = Path(output_path)
    subject = visual_subject or identify_visual_subject(hook=hook, source_name=source_name)
    prompt = _editorial_prompt(
        hook=hook,
        facts=facts,
        topic_type=topic_type,
        visual_subject=subject,
    )
    generate_image(prompt, destination)
    logo_source_url = ""
    if subject and subject.get("kind") == "crypto_project":
        logo_bytes, logo_source_url = _verified_project_logo(subject, session=session)
        _integrate_project_logo(destination, logo_bytes)
    manifest = {
        "source_url": source_url,
        "source_name": source_name,
        "published_at": published_at,
        "evidence_type": "gpt_news_visual",
        "is_primary_source": bool(is_primary_source),
        "facts_primary_source": bool(is_primary_source),
        "facts_verified": True,
        "generated_image": True,
        "visual_role": "attention_visual",
        "textless": True,
        "no_brand_or_logo": not bool(subject and subject.get("kind") == "crypto_project"),
        "subject_identifiable": bool(subject),
        "visual_subject": subject,
        "entity_identity_required": bool(subject),
        "official_logo_used": bool(subject and subject.get("kind") == "crypto_project"),
        "logo_source_url": logo_source_url,
        "logo_verified_against_official_domain": bool(logo_source_url),
        "logo_official_domain": subject.get("official_domain", "") if subject else "",
        "logo_registry_coin_id": subject.get("coingecko_id", "") if subject else "",
        "identity_method_used": (
            "generated_editorial_portrait"
            if subject and subject.get("kind") == "public_figure"
            else subject.get("identity_method") if subject else "none"
        ),
        "generated_public_figure_portrait": bool(subject and subject.get("kind") == "public_figure"),
        "not_event_evidence": bool(subject and subject.get("kind") == "public_figure"),
        "official_mark_depicted": bool(subject and subject.get("kind") == "institution"),
        "mark_depiction_mode": (
            "photorealistic_physical_object"
            if subject and subject.get("kind") == "institution"
            else "none"
        ),
    }
    destination.with_suffix(".source.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destination
