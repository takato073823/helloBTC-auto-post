"""One read per published experiment post, after 24h. No joins inferred from X."""
import datetime as dt
import json
from pathlib import Path

from inu_academy import START, ROOT, assignment

BASE = ROOT / "scripts"
REPORT = ROOT / "docs/inu-academy-14d.md"
SNAP = BASE / "inu_academy_metrics.json"
JST = dt.timezone(dt.timedelta(hours=9))


def planned():
    for day in range(14):
        for hour in (9, 21):
            slot = f"{START + dt.timedelta(days=day)}-{hour:02}-a"
            yield slot, assignment(slot)


def capture(history, snapshots, now, lookup):
    due = [r for r in history if r.get("academy") and assignment(r["slot"]) == r["academy"]
           and r["tweet_id"] not in snapshots
           and now - dt.datetime.fromisoformat(r["posted_at"]) >= dt.timedelta(hours=24)]
    if not due:
        return
    ids = [r["tweet_id"] for r in due]
    try:
        metrics = lookup(ids)
    except Exception as exc:
        print("X指標未取得:", type(exc).__name__)
        metrics = {}
    for key in ids:
        snapshots[key] = {"observed_at": now.isoformat(), "metrics": metrics.get(key)}


def render(history, snapshots, joins):
    by_slot = {r["slot"]: r for r in history if r.get("academy") and r["academy"] == assignment(r["slot"])}
    lines = ["# CryptoAcademy｜14日間の先行配信", "",
             "11/19〜12/2 JST。興味の指標は公開24時間以降の初回観測。参加は別集計。未計測はゼロではありません。",
             "開設後は、参加完了が確認できた新規会員が任意回答した投稿コードまたは投稿URLで紐付けます（自己申告）。",
             "無回答・複数候補は帰属不明。各人は1回のみ。個人情報はリポジトリに保存しません。", "",
             "|日時（JST）|投稿コード|形式|公開投稿|表示|保存|返信|確認済み参加（自己申告）|",
             "|---|---|---|---|---:|---:|---:|---:|"]
    for slot, meta in planned():
        row = by_slot.get(slot, {})
        tid = row.get("tweet_id")
        m = snapshots.get(tid, {}).get("metrics") or {}
        code = meta["code"]
        n = joins.get(code)
        if n is not None and (not tid or type(n) is not int or n < 0):
            raise ValueError("参加記録は公開済み投稿別の非負の整数のみ")
        cells = [slot[:-2], code, meta["format"], f"[投稿](https://x.com/hellobtc_jp/status/{tid})" if tid else "公開記録なし",
                 *(str(m.get(k, "—")) for k in ("impression_count", "bookmark_count", "reply_count")),
                 str(n) if n is not None else "未計測"]
        lines.append("|" + "|".join(cells) + "|")
    lines.extend(["", "### 形式別の反応", "",
                  "|形式|公開数|表示・保存・返信すべて取得済み|1,000表示あたりの保存|1,000表示あたりの返信|",
                  "|---|---:|---:|---:|---:|"])
    for style in ("図で検証", "初心者Q&A"):
        rows = [r for r in by_slot.values() if r["academy"]["format"] == style]
        stats = [snapshots.get(r["tweet_id"], {}).get("metrics") or {} for r in rows]
        complete = [m for m in stats if all(isinstance(m.get(k), int) for k in
                    ("impression_count", "bookmark_count", "reply_count"))]
        imp = sum(m["impression_count"] for m in complete)
        rates = [f"{1000*sum(m[k] for m in complete)/imp:.1f}" if imp else "—"
                 for k in ("bookmark_count", "reply_count")]
        lines.append(f"|{style}|{len(rows)}|{len(complete)}|{'|'.join(rates)}|")
    if set(joins) - {m["code"] for _, m in planned()}:
        raise ValueError("未知の投稿コード")
    return "\n".join(lines) + "\n"


def lookup(ids):
    from x_poster import _get_client
    result = _get_client().get_tweets(ids=ids, tweet_fields=["public_metrics"], user_auth=True)
    return {str(t.id): t.data["public_metrics"] for t in result.data or [] if "public_metrics" in t.data}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--collect", action="store_true")
    args = parser.parse_args()
    history = json.loads((BASE / "inu_hourly_state.json").read_text())["history"]
    snapshots = json.loads(SNAP.read_text())
    now = dt.datetime.now(JST)
    if args.collect and START <= now.date() <= START + dt.timedelta(days=16):
        capture(history, snapshots, now, lookup)
        SNAP.write_text(json.dumps(snapshots, indent=2, sort_keys=True) + "\n")
    joins = json.loads((BASE / "inu_academy_confirmed_joins.json").read_text())
    REPORT.write_text(render(history, snapshots, joins))


if __name__ == "__main__":
    main()
