# INU X運用会社

## 目的

Xの発見、一次情報確認、文章・画像作成、品質審査、公開、障害復旧を分離し、
「GitHub Actionsが成功したのに投稿されていない」状態を成功として扱わない。

## 部門

| 部門 | Hermes profile | 責任 | X書込権限 |
|---|---|---|---|
| 経営・統括 | `inuceo` | 2時間SLA、依存関係、停止判断 | なし |
| リサーチ | `inuresearch` | X発見、一次資料への回帰、引用付き候補 | なし |
| 編集 | `inueditor` | 検証済み事実から本文と画像briefを作る | なし |
| 品質管理 | `inuquality` | 鮮度、重複、画像一致、禁止表現を審査 | なし |
| 配信 | `inupublisher` | 承認済みOutboxを一度だけ公開 | 既存X APIのみ |
| 信頼性 | `inusre` | 枠欠落、予約lease、障害段階を監査 | なし |

## 実行経路

1. `inu_company_orchestrator.py --prepare` が既存の探索・本文・画像処理を呼ぶ。
2. 品質部が本文、一次資料、画像manifestを独立して再検証する。
3. 承認済み候補を30分の予約lease付きOutboxとして保存する。
4. `--publish` だけが既存のOAuth 1.0a投稿モジュールへ渡す。
5. X投稿失敗時は予約を解放して失敗段階を保存し、成功扱いにしない。
6. 同一本文、同一URL、URL違いの近似事件を公開前に拒否する。

ブラウザやChromeの自動操作による投稿は行わない。Hermesは読み取り専用X探索に限定し、
最終投稿は既存のX公式API経路だけを使用する。

## Hermes連携

- Hermes Agent: `v0.20.4`（導入時commit `5dd15872a6878a19b9b5478b6968b38f48dd311f`）
- 推論: `openai-codex` / `gpt-5.5`
- Codex MCP server名: `codex`
- Durable Kanban board: `inu-x-company`
- X発見packet: `scripts/inu_hermes_research_packet.json`
- 有効化フラグ: `INU_HERMES_RESEARCH_ENABLED=true`

Hermes X検索は、`degraded=false`、引用1件以上、投稿時刻24時間以内を満たす結果だけを
発見シグナルに入れる。X投稿は最終出典にせず、後段が公式発表・IR・規制当局・公式データへ戻る。

## 常時稼働

Xの公開と2時間枠はGitHub Actionsで動くため、Macが閉じていても継続する。
HermesのProfiles・Kanban・CronをMac停止中も動かす場合は、常時稼働VPSまたはコンテナが必要。
ローカルgatewayだけを常時稼働と見なさない。

## 外部障害の扱い

- X API / xAI / OpenAIのクレジット不足、403、認証・権限不足は`blocked_external`として記録する。
- 文字だけの代替投稿、同じ枠での無制限再試行、古い情報での穴埋めは行わない。
- GitHub Actionsの成功ではなく、`tweet_id`を含むDeliveryReceiptを公開成功とする。
