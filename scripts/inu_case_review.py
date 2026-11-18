"""Offline editorial research. No HTTP clients, model calls, credentials or billing fallback."""
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
STUDY = "https://blog.ethereum.org/2026/02/17/ethereum-protocol-studies-26"
LESSONS = {
    "learn": "学習目標を一つに絞り、具体例の後に自分で確かめられる答えを示す",
    "visual": "初心者が抱く疑問を一つ選び、本文と同じ要点に画像を絞る",
    "next_step": "学べる内容を先に実演し、参加受付前は質問または次の図解の案内を一つだけ添える",
    "quantity": "数字だけを強調せず、対象と比較期間が読み取れる候補を優先する",
    "speaker": "誰が何を発表・発言したかを特定できる候補を優先する",
}


def read(name):
    return json.loads((BASE / name).read_text(encoding="utf-8"))


def research(slot, now, state, academy=None):
    """Scan measured outcomes afresh every slot; never republish the historical news."""
    cases = []
    if academy:
        joins = read("inu_academy_confirmed_joins.json")
        for post in state.get("history", []):
            meta = post.get("academy") or {}
            n = joins.get(meta.get("code"))
            if (meta.get("format") == academy["format"] and type(n) is int and n > 0
                    and str(post.get("tweet_id", "")).isdigit()):
                cases.append((n, {"url": "https://x.com/hellobtc_jp/status/"+str(post["tweet_id"]),
                    "result": f"確認済み参加 {n}人（自己申告帰属）", "observed": meta["code"],
                    "feature": "learn", "limit": "同形式の実参加。投稿単独の因果効果ではない。"}))
        cases.append((0, {"url": STUDY, "result": "主催者が累計数百人の学習参加を報告",
            "observed": "2026-02-17", "feature": "learn",
            "limit": "クリプト教育の類例。個別のX投稿経由の入会数・理解度チェック単独の効果は不明。"}))
        cases.append((0, {"url":"https://business.x.com/ja/success-stories/solution-tree",
            "result":"教育イベント告知キャンペーンで35万表示以上", "observed":"2026-11-18 出典確認",
            "feature":"visual", "limit":"有料施策を含む表示実績。自然投稿の効果や参加完了数ではない。"}))
        cases.append((0, {"url":"https://academy.trezor.io/", "result":"主催者発表：30カ国以上・参加者2,500人以上",
            "observed":"2026-11-18 出典確認", "feature":"next_step",
            "limit":"現地の教育施策全体の実績。Xの誘導文単体の参加効果ではない。"}))
    else:
        # Preserve measured exemplars when the live discovery cache rolls over.
        rows = {row["post_url"]:row for row in read("inu_case_examples.json")}
        rows.update({row["post_url"]:row for row in read("inu_x_research_state.json").get("signals", [])
                     if row.get("post_url")})
        for row in rows.values():
            url, n = row.get("post_url",""), row.get("impression_count")
            if (not re.fullmatch(r"https://x\.com/[\w]+/status/\d+", url)
                    or type(n) is not int or n < 10000 or not row.get("discovered_at")):
                continue
            headline = row.get("headline","")
            if re.search(r"\d", re.sub(r"https?://\S+", "", headline)) and re.search(r"hour|day|week|時間|週間|日間", headline, re.I):
                feature = "quantity"
            elif re.search(r"says|announces|発表|発言", headline, re.I):
                feature = "speaker"
            else:
                continue
            reposts = row.get("repost_count")
            known = type(reposts) is int and 0 <= reposts <= n
            rate = round(1000 * reposts / n, 3) if known else None
            result = f"観測時 {n:,}表示" + (f"・{reposts}リポスト（1,000表示あたり{rate}）" if known else "・再共有数未計測")
            cases.append((rate if known else -1, {"url": url, "result": result, "observed": row["discovered_at"],
                "reposts_per_1000":rate, "feature": feature, "limit": "再共有の観測値。投稿者・題材・観測時刻は揃わず、因果や参加は不明。"}))
    if not cases:
        raise ValueError("出典と成果を確認できる成功事例なし")
    ordered = sorted(cases, key=lambda pair: -pair[0])
    # Keep one actual exemplar per transferable feature, rather than one viral account many times.
    selected = {}
    for _, case in ordered:
        case["application"] = LESSONS[case["feature"]]
        selected.setdefault(case["feature"], case)
    return {"slot": slot, "researched_at": now.isoformat(),
            "goal": "集客" if academy else "トレンド" if slot.endswith(("-13-a","-17-a")) else "通常",
            "compared": len(cases), "references": list(selected.values()), "status": "調査済み"}


def matches(text, feature):
    if feature == "learn":
        return bool(re.search(r"比較|違い|に対し|前日|増減|前週|なぜ|例|比べ", text))
    if feature == "visual":
        return bool(re.search(r"なぜ|どう|違い|？|\?|比べ", text))
    if feature == "quantity":
        return bool(re.search(r"\d", text) and re.search(r"時間|前日|週間|週比|日間|前年比|前月|四半期|hour|day|week", text, re.I))
    if feature == "speaker":
        return bool(re.search(r"発表|公表|声明|発言|明らかに|says|announc", text, re.I))
    return False


def rank(rows, review):
    if not review:
        return rows
    def score(row):
        text = " ".join(str(row.get(k, "")) for k in ("hook","facts","title","summary"))
        matching = [ref for ref in review["references"] if matches(text, ref["feature"])]
        # Repost rate breaks ties between otherwise comparable formats; missing is not zero.
        rates = [ref["reposts_per_1000"] for ref in matching if ref.get("reposts_per_1000") is not None]
        return len(matching), max(rates, default=-1)
    return sorted(rows, key=score, reverse=True)  # stable on ties; never disables fact/quality checks


def application(review, text):
    if review["goal"] == "集客":
        cta = next((line for line in text.splitlines() if line.startswith("開設準備中。")), "")
        return "1テーマの学習解説。本文と画像で要点を示す。" + (f" 公開前の案内：{cta}" if cta else "")
    used = [ref["application"] for ref in review["references"] if matches(text, ref["feature"])]
    return "／".join(used) if used else "事例は候補の比較に使用。最終稿に同じ型は確認できず、一次情報を優先。"


def markdown(state):
    lines = ["# 自動投稿｜成功事例と採用理由", "", "日時は枠の日本時間。歴史的な成功事例はニュースの出典には使いません。",
             "新ルール適用後の記録は公開処理で更新します。事例調査は保存データだけを利用します。",
             "有料API・従量課金サービスや日次上限を追加・増額する場合は、設定前に見積もりと上限を提示し、明示の承認を得てください。", ""]
    for slot, row in sorted(state.get("success_case_audit", {}).items(), reverse=True):
        lines += [f"### {slot} · {row['goal']}", "", f"調査: {row['researched_at']} ／ 対象事例: {row['compared']}件"]
        for ref in row["references"]:
            lines += [f"- [参考事例]({ref['url']})：{ref['result']}（{ref['observed']}）。参考にした点：{ref['application']}。限界：{ref['limit']}"]
        lines += ["", row.get("actual_use", "原稿作成前"), "",
                  f"[公開投稿](https://x.com/hellobtc_jp/status/{row['tweet_id']})" if row.get("tweet_id") else row["status"], ""]
    return "\n".join(lines).rstrip()+"\n"


if __name__ == "__main__":
    (BASE.parent / "docs/inu-success-cases.md").write_text(markdown(read("inu_hourly_state.json")))
