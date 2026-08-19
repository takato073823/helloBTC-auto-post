"""INUの自動投稿に共通する編集憲法と公開前の品質ゲート。"""

from __future__ import annotations

import re
from urllib.parse import urlparse


# リサーチ、本文作成、公開のすべてが同じ判断軸を使うための最小の憲法。
# 毎時枠では候補不足を理由に止まらず、一次資料の探索範囲を広げる。
EDITORIAL_CONSTITUTION = """
INUは、今この瞬間に投資家が知る意味のある一次情報だけを扱う。
一つの投稿では、一つの変化を伝える。見出しで変化を明示し、事実では数字・条件・決定を示し、
事実のあとに、その変化が市場・利用者へ与える意味を短く分析する。事実と分析は段落を分け、
分析には一次情報から導けない断定、価格予測、売買判断、曖昧な「注視したい」を加えない。
価格チャート投稿だけは個人見解を加えず、実測値で完結させる。
毎時の定期枠では、新しい事実、読者への具体的な影響、その事実を一目で確認できる画像または動画が
そろうまで、X話題・公式発表・実測データ・企業IRの順に探索範囲を広げる。候補不足を理由に定期投稿を止めない。
暗号資産のシンボルを本文で使う場合は、BTCではなく必ず$BTCの形式に統一する。
""".strip()

AUTO_POST_PLAYBOOK = """
公開前に必ず次を満たす。
1. Why now: 今投稿する根拠が、公式発表時刻・価格節目・制度変更・新しい数値のどれかで明確。
2. What changed: 見出しと事実に、数字・条件・決定・需給・価格反応の少なくとも一つがある。
3. Reader value: 初心者にも分かる言葉で「なぜ重要か」を説明し、読者が次に確認すべき対象を一つだけ具体的に示せる。
4. Visual proof: 画像は本文の根拠を補強する公式資料、データ、相場画面、または出来事を直感的に伝える独自ビジュアル。
5. Follow-through: 続報として追う対象が明確で、単なる「フォローしてください」ではない。
6. Lead: 冒頭に具体的な数値・機関名・規制当局名・結論のいずれかを置き、最初の数行で要点が分かる。
""".strip()

EDUCATIONAL_NEWS_PLAYBOOK = """
次の3系統は、例示された固有ニュースを固定投稿せず、現在時刻から見て新しい一次情報だけを使う。
- prediction_market_shift: Polymarket公式市場で予測確率が短時間に大きく変化した場合。冒頭に対象と
  「変更前→変更後」を置き、変化幅と計測時間を示す。予測市場の参加者が付けた確率であり、確定情報・
  公式予測・価格保証ではないことも初心者向けに一文で明記する。
- institutional_custody: Citiなど金融機関自身の発表で、暗号資産カストディの開始・提供・提携・承認・
  具体的な計画が新しく確認できた場合。冒頭に金融機関名と結論を置き、「カストディとは何か」と、
  利用者や市場インフラにとっての意味をQ&Aまたは「つまり」の一文で説明する。
- regulatory_rule_change: SECなど規制当局自身の発表で、新ルール名と採択・施行・提案・撤回・改正などの
  具体的な変更が確認できた場合。見出し=事実、1文目=変更内容と背景、2文目=投資家・事業者への影響の
  順にし、難しい制度用語を平易に翻訳する。
""".strip()

# 毎時の自動経路で実際に選べる投稿系統。過去のテスト投稿・手動投稿の
# 分類を、将来の自動選定の実績として混ぜないためにも使う。
AUTO_SELECTABLE_TOPIC_TYPES = (
    "breaking_news",
    "developing_story",
    "market_microstructure",
    "etf_flow",
    "prediction_market_shift",
    "institutional_custody",
    "regulatory_rule_change",
    "institutional_flow",
    "onchain",
    "whale_treasury",
    "earnings",
    "supply_event",
    "adoption_kpi",
    "policy_household",
    "macro_event",
)

# 一次資料で事実を固定した後に、編集上の分析を加えられるニュース系統。
# 価格チャートは別経路で生成されるため、この集合には含めない。
EDITORIAL_ANALYSIS_TOPIC_TYPES = frozenset(
    (*AUTO_SELECTABLE_TOPIC_TYPES, "x_reaction", "public_figure_statement")
)
_FORBIDDEN_ANALYSIS_RE = re.compile(
    r"(?:必ず|確実|間違いなく|価格目標|買うべき|売るべき|今すぐ買|今すぐ売|"
    r"上がるはず|下がるはず|爆上げ|爆下げ)"
)
_VAGUE_ANALYSIS_RE = re.compile(
    r"(?:注視したい|追いたい|見ていきたい|確認したい|ポイントです|節目だと見ています)[。！!]*$"
)


def allows_editorial_analysis(topic_type: object) -> bool:
    return str(topic_type or "") in EDITORIAL_ANALYSIS_TOPIC_TYPES


def validate_editorial_analysis(candidate: dict, *, required: bool = True) -> None:
    """ニュースの分析を事実から分離し、予測や定型の感想を公開前に止める。"""
    topic_type = str(candidate.get("topic_type", ""))
    opinion = " ".join(str(candidate.get("opinion", "")).split())
    if not allows_editorial_analysis(topic_type):
        if opinion:
            raise ValueError("価格・データ投稿に個人見解を追加できません")
        return
    if not opinion:
        if required:
            raise ValueError("ニュースの意味を示す分析がありません")
        return
    if not 18 <= len(opinion) <= 95:
        raise ValueError("ニュース分析は18〜95文字の1文にしてください")
    if "\n" in str(candidate.get("opinion", "")) or len(re.findall(r"[。！？!?]", opinion)) > 1:
        raise ValueError("ニュース分析は独立した短い1文にしてください")
    if re.search(r"https?://|www\.|#[^\s#]+", opinion, flags=re.IGNORECASE):
        raise ValueError("ニュース分析にURL・ハッシュタグを入れられません")
    if _FORBIDDEN_ANALYSIS_RE.search(opinion):
        raise ValueError("ニュース分析に予測・売買判断・誇大表現があります")
    if _VAGUE_ANALYSIS_RE.search(opinion):
        raise ValueError("ニュース分析が曖昧な注視宣言で終わっています")
    facts = compact_text(" ".join(str(value) for value in candidate.get("facts", [])))
    compact_opinion = compact_text(opinion)
    if compact_opinion and compact_opinion in facts:
        raise ValueError("ニュース分析が検証済み事実の繰り返しです")

_MATERIAL_CHANGE_RE = re.compile(
    r"(?:承認|却下|可決|否決|開始|終了|停止|禁止|解禁|導入|撤回|引き上げ|引き下げ|"
    r"増額|減額|上方修正|下方修正|流入|流出|買い戻し|売却|購入|発行|償還|最高値|最安値|"
    r"急騰|急落|反転|金利|利回り|入札|ETF|決算|売上|利益|供給|需要|ハッキング|流出|清算|提携|"
    r"採択|施行|発効|提案|改正|公布)"
)
_MATERIAL_NUMBER_RE = re.compile(
    r"(?:[$¥€£]\s?\d|\d[\d,.]*\s?(?:%|％|ドル|円|億|万|兆|BTC|ETH|株|bp|ベーシス))",
    re.IGNORECASE,
)
_GENERIC_WHY_NOW_RE = re.compile(
    r"^(?:公式(?:ページ|サイト|資料)?(?:が|を)?(?:更新|公表|発表|公開)(?:された|した|したため)?|"
    r"新しい(?:情報|資料)(?:が|を)?(?:出た|公開された)|重要(?:な)?(?:情報|ニュース)(?:のため)?)。?$"
)
_PREDICTION_PAIR_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s?(?:%|％).{0,24}?(?:→|から|to).{0,12}?(\d+(?:\.\d+)?)\s?(?:%|％)",
    re.IGNORECASE,
)
_PREDICTION_WINDOW_RE = re.compile(r"(?:直近)?\d{1,2}時間|24時間|当日|本日")
_PREDICTION_CAVEAT_RE = re.compile(
    r"(?:予測市場|参加者).{0,40}(?:確定(?:情報)?ではない|保証ではない|公式予測ではない|結果ではない)"
)
_CUSTODY_ACTION_RE = re.compile(
    r"(?:カストディ|custody).{0,45}(?:開始|提供|参入|提携|承認|拡大|計画|構築)|"
    r"(?:開始|提供|参入|提携|承認|拡大|計画|構築).{0,45}(?:カストディ|custody)",
    re.IGNORECASE,
)
_CUSTODY_EXPLAINER_RE = re.compile(
    r"(?:カストディとは|カストディ[＝=]|カストディは、|つまり).{0,55}(?:保管|管理|預か)"
)
_INSTITUTION_LEAD_RE = re.compile(
    r"(?:[A-Z][A-Za-z0-9.&-]{1,}|[\w一-龥ァ-ヶー]{2,}(?:銀行|証券|信託|フィナンシャル))"
)
_REGULATOR_RE = re.compile(
    r"(?:SEC|米証券取引委員会|CFTC|金融庁|FSA|欧州委員会|ESMA|規制当局)",
    re.IGNORECASE,
)
_RULE_CHANGE_RE = re.compile(
    r"(?:規則|ルール|制度|ガイダンス|命令|法案|基準).{0,45}(?:採択|施行|発効|提案|撤回|改正|承認|公布)|"
    r"(?:採択|施行|発効|提案|撤回|改正|承認|公布).{0,45}(?:規則|ルール|制度|ガイダンス|命令|法案|基準)"
)
_REGULATORY_IMPACT_RE = re.compile(
    r"(?:投資家|個人|事業者|取引所|発行体|金融機関|ETF|開示|審査|保護|取引|申請|資産)"
)
_ROUTINE_DISCLOSURE_RE = re.compile(
    r"(?:毎日|週次|毎週|月次|毎月|定期的|継続的|常時).{0,30}(?:公開|開示|更新|掲載)|"
    r"(?:公開|開示|更新|掲載).{0,30}(?:毎日|週次|毎週|月次|毎月|定期的|継続的|常時)"
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
    validate_editorial_analysis(candidate)

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

    compact_why = compact_text(why_now)
    compact_interest = compact_text(reader_interest)
    compact_follow = compact_text(follow_value)
    if compact_why in compact_interest or compact_interest in compact_why:
        raise ValueError("今投稿する理由と読者価値が同じ内容です")
    if compact_follow and (
        compact_follow in compact_interest or compact_interest in compact_follow
    ):
        raise ValueError("継続フォロー価値が読者価値の言い換えです")

    topic_type = str(candidate.get("topic_type", ""))
    combined = " ".join([hook, *facts, why_now, reader_interest])
    if topic_type == "prediction_market_shift":
        host = (urlparse(str(candidate.get("source_url", ""))).hostname or "").lower()
        if not (host == "polymarket.com" or host.endswith(".polymarket.com")):
            raise ValueError("Polymarket公式市場ページが一次情報になっていません")
        probability_pair = _PREDICTION_PAIR_RE.search(hook)
        if "polymarket" not in hook.lower() or not probability_pair:
            raise ValueError("Polymarketの変更前後の確率と変化幅がありません")
        before, after = (float(value) for value in probability_pair.groups())
        if abs(after - before) < 15 or not _PREDICTION_WINDOW_RE.search(why_now):
            raise ValueError("Polymarketの急変条件（15ポイント以上・計測時間）を満たしません")
        if not _PREDICTION_CAVEAT_RE.search(" ".join(facts)):
            raise ValueError("予測市場の確率が確定情報ではない説明がありません")
    elif topic_type == "institutional_custody":
        if not _INSTITUTION_LEAD_RE.search(hook) or not _CUSTODY_ACTION_RE.search(hook):
            raise ValueError("金融機関のカストディに関する具体的な決定がありません")
        if not _CUSTODY_EXPLAINER_RE.search(" ".join(facts)):
            raise ValueError("初心者向けのカストディ説明がありません")
    elif topic_type == "regulatory_rule_change":
        if not _REGULATOR_RE.search(hook) or not _RULE_CHANGE_RE.search(combined):
            raise ValueError("規制当局名・新ルール名・具体的な変更が不足しています")
        if len(facts) != 2 or not _REGULATORY_IMPACT_RE.search(facts[-1]):
            raise ValueError("規制変更の背景と投資家・事業者への影響が不足しています")
    elif topic_type == "supply_event":
        if not _MATERIAL_NUMBER_RE.search(combined):
            raise ValueError("需給イベントの数量・金額・比率が不足しています")
        if _ROUTINE_DISCLOSURE_RE.search(combined) and not re.search(
            r"(?:前回比|前年比|増加|減少|純流入|純流出|新規|今回|当日)", combined
        ):
            raise ValueError("常設の定期開示ページだけでは新しい需給イベントになりません")
