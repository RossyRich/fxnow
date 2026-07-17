# FXnow — リアルタイムFX情報

FXの売買目線とニュースを約5分ごとに自動収集して表示するサイト。

**公開URL**: https://rossyrich.github.io/fxnow/

## 構成

- `index.html` — 閲覧画面（PC/スマホ対応、単体でfile://でも開ける）
- `data.js` / `data.json` — 収集データ（GitHub Actionsが自動更新）
- `collect/collect.py` — 収集スクリプト（Python標準ライブラリのみ、依存なし）
- `.github/workflows/collect.yml` — 5分ごとの自動収集ワークフロー（自己連鎖方式）

## データ源

| 内容 | 源 |
|---|---|
| ポジション比率（買い%） | FXSSI current-ratios API（海外ブローカー10社の加重平均） |
| Xポスト（売買目線判定） | Yahoo!リアルタイム検索（ログイン不要でXポストが読める） |
| 為替ニュース（日本語） | みんかぶFX |
| 英語ニュース | ForexLive RSS |
| 経済指標 | ForexFactory 週間カレンダーJSON（直近48時間・中重要度以上） |
| 現在レート・前日比 | GMOコイン 外国為替FX公開API |
| Reddit r/Forex | ベストエフォート（datacenter IPからは403が多く通常スキップ） |

Xポストの買い/売り判定は `collect.py` のキーワード判定（BUY_WORDS/SELL_WORDS）。
宣伝スパムは SPAM_WORDS / OFFTOPIC_WORDS で除外。

## 注意

- 対象4通貨ペア: USDJPY / EURUSD / GBPUSD / AUDUSD
- X APIは有料のためYahoo!リアルタイム検索を経由している（kabu-scoutで実証済みの方式）
- 投資助言ではない。フッターに免責記載
