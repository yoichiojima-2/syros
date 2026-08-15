# レビューノート: 初版ペルソナ/機能推奨ドキュメントの批判的レビュー

初版の `personas.html` / `featurerecommendations.html`(2026-08、UX Design 作)を
syros コードベース(v0.2.0)と照合した結果と、改訂版での修正方針の記録。

## 総評

初版はコードベースを確認せずに書かれており、汎用的な「AI エージェント SaaS コンソール」
のテンプレートに近い。3 つの系統的な誤りがある:

1. **偽のギャップ** — 「ADD」17 件のうち少なくとも 5 件は実装済み。
2. **現状の誤記** — 「MODIFY」のうち 2 件は、現状の挙動をそのまま「提案」として記述。
3. **方針との矛盾** — 複数の項目が README「Out of scope」と
   「1 GCP プロジェクト = 1 信頼境界」の設計方針に反する。

## プロダクトの実像(照合の基準)

- `claude_agent_sdk` エージェントを GCP Cloud Run Jobs のサンドボックスで実行する
  ミニマルなインフラ/SDK プロダクト。Python パッケージ(`src/syros/`)+ Terraform
  (`infra/`)のみ。REST API なし、常時稼働コストなし。
- Firestore = セッション状態・ジャーナル・承認キュー / GCS = ワークスペース・成果物 /
  IAM = 認証 / Vertex AI = モデル。
- コンソールは静的 Next.js エクスポート + stdlib の HTTP サーバで、ポーリングベース。
- README「Out of scope」(REST API、バージョン付きレジストリ、vault/egress proxy、
  マルチテナンシー)が設計の境界線。

## 検証済みの個別指摘

### 実装済みなのに「ADD」とされた項目
| 初版の項目 | 実装箇所 |
|---|---|
| フリート全体ダッシュボード | `/`, `/dashboard`(`console/src/app/dashboard/page.tsx`)|
| 改竄不能なアクション台帳 | `journal.py`, `gate.py` — `PreToolUse` が実行前にコミット。`analytics.py` で BigQuery へ |
| mid-run 介入 | inbox 経由の追いクエリ、実行中のコンポーザー |
| セッション再開/継続 | `resume=`、ジャーナルツリー + `rewind` 分岐 |
| jump-to-error 相当 | 型付きジャーナル + `state-badge` で部分カバー |

### 現状を誤記した「MODIFY」
- **状態モデル「二値→詳細化」**: 現状は `running|starting|stalled|queued|idle|terminated|unknown`
  + 直交する `RunOutcome`(`console/src/lib/types.ts`, `console/api.py` の `derived_state()`)。
  要求された "possibly-stalled" はリース失効ベースの `stalled` として実装済み。
- **承認「リアルタイム割り込み→非同期キュー」**: 既に Firestore の非同期キュー、
  監査付き、300 秒タイムアウト拒否(`gate.py`)。新規なのはリスク階層化のみ。

### 方針と矛盾する項目(取り下げ/再定義)
- アプリ内監査ロール → IAM viewer / IAP 招待(`infra/main.tf` の `console_iap`)。
- 共有セッションリンク → artifact space + `storage.objectViewer` + IAP の文脈で再定義。
- エンジニア別予算 → セッションに所有者概念がない(`trigger`/`agent` のみ)。実装不能。

### 本物のギャップ(改訂版で優先度アップ)
1. **通知の欠如(最大)** — プッシュ機構ゼロ。承認は放置で 300 秒後に*拒否*。
2. **帰属バグ** — `_decided_by()`(`src/syros/console/api.py:163`)が IAP 身元でなく
   `getpass.getuser()` / `"console"` を記録。監査証跡の主張を空洞化させる。
   確認済み: 承認決定(`api.py:302`)、セッション作成(:239)、deployment 作成(:447)等で使用。
3. **検索の欠如** — UI は状態フィルタのみ。BigQuery エクスポートはスナップショット。
4. **フリート予算上限の欠如** — クエリ単位 `max_budget_usd` のみ(README:371 が自認)。

### ペルソナの修正
- **Devon(Engineer)** → 第一ペルソナに昇格。実ユーザー像(SDK ファースト)に最も近い。
  共有 JTBD を IAM/artifact space の語彙に書き直し。
- **Maria(Manager)** → 「フリート運用者(プロジェクトオーナー)」に再スコープ。
  エンジニア別予算関連(初版の約4割)を削除。
- **Priya(Security)** → ジャーナル/BigQuery/IAM に接地。帰属バグを最大ペインに。
  SOC2 型組織ワークフロー前提を除去。
- 出典のない定量値はすべて「検証すべき仮説」とラベル付け。

## 成果物

- `docs/design/personas.md` — 改訂版ペルソナ(日本語)
- `docs/design/feature-recommendations.md` — 修正版ギャップ分析(日本語)
- 本ファイル — 修正根拠の記録
