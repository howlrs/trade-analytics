# Trade Analytics プロジェクト

## プロジェクト概要

マーケット分析・投資戦略作成のためのワークスペース。
基本方針は `docs/base.md` を参照。

## コンテキスト優先順位

分析開始時や情報参照時は、以下の順序でスキャンすること:

1. `docs/knowledges/` - 蓄積された知見・API仕様（最優先）
2. `docs/base.md` - 基本方針
3. `src/` - 自作パッケージのコード
4. `scripts/` - データ取得・分析スクリプト

ワークスペース全体を無差別にスキャンしない。

## 命名規則

- ディレクトリ・ファイル: `YYYYMMDD_HHMM_<概要>` 形式（例: `20260301_0930_btc_funding_rate`）
- 分析アイデア: `idea_*.md`
- 分析ノートブック: `analysis_*.py`（marimo形式）

## データ形式

- マーケットデータ: parquet形式で `data/` に保存
- 大規模結合クエリ: DuckDB使用
- 分析成果物: marimo notebook（作成ルールは `docs/knowledges/marimo_notebook.md` 参照）

## GitHub ワークフロー

### 開発フロー

1. **Issue作成** - 作業の目的・スコープを明確化（`gh issue create`）
2. **ブランチ作成** - `feature/<issue番号>-<概要>` or `data/<概要>` 形式
3. **テスト** - `tests/` にテストを書き、`pytest` で検証
4. **コミット** - 変更をコミット（`Closes #<issue番号>` で紐付け）
5. **Push & PR** - リモートへpushし、必要に応じてPR作成

### ブランチ命名規則

- `feature/<issue番号>-<概要>` - 新機能・スクリプト
- `data/<概要>` - データ取得・パイプライン
- `analysis/<概要>` - 分析ノートブック
- `fix/<issue番号>-<概要>` - バグ修正

### コミットメッセージ規約

```
<type>: <description>

Types: feat, fix, data, analysis, docs, refactor, test, chore
```

### セキュリティ

- **APIキー・シークレットは絶対にコミットしない**
- 秘匿情報は `.env` に格納し、`.gitignore` で除外済み
- `credentials.json`, `secrets.json`, `*.pem`, `*.key` も除外済み
- 環境変数経由でAPIキーを参照: `os.environ['BINANCE_API_KEY']`

## 分析ワークフロー

1. アイデアを `idea_*.md` にまとめる
2. 計画を `docs/plans/` に保存（Skillsを使用）
3. データを取得・加工して `data/` に格納
4. marimo notebook で分析実行
5. 結果レビュー
6. 知見を `docs/knowledges/` に記録

## 知見の記録ルール

新しい発見・知見が得られた場合は `docs/knowledges/` に Markdown で記録すること:
- 過去分析からの発見
- データ定義・取得ジョブ情報
- パッケージ・スクリプトの使い方
- 外部API仕様

## 技術スタック

- Python (polars, numpy, marimo)
- データ: parquet, DuckDB
- 指標計算: polars式ベース, ta-lib
- 取引所API: ccxt
- 取引所: Binance, Bybit
- 対象通貨: BTC, ETH（USDT建て先物）
- オンチェーン: CryptoQuant, Glassnode, DefiLlama
- デリバティブデータ: Coinglass
- テスト: pytest
