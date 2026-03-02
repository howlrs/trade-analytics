# sui-auto-lp: TypeScript → Rust 移行検討

## 1. 背景と目的

現在運用中のTypeScript（Node.jsベース）システムから、Google Cloud Platform Compute Engine (GCE) におけるリソース効率最大化（メモリ・CPU最適化・コスト削減）を目的とし、Rust言語への移行を検討する。

## 2. モジュール・パッケージの移行構成案

現在の `package.json` で稼働している主要機能を網羅するため、以下のRustクレートへの代替を提案する。

| 機能                    | 現在のTSパッケージ                         | Rustクレート                                                                                                        | 成熟度   | 備考                                                                  |
| :---------------------- | :----------------------------------------- | :------------------------------------------------------------------------------------------------------------------ | :------- | :-------------------------------------------------------------------- |
| **Suiネットワーク接続** | `@mysten/sui` ^1.21.1                      | [`sui-sdk`](https://github.com/MystenLabs/sui/tree/main/crates/sui-sdk) (monorepo版)                                | ◎        | MystenLabs公式。TX構築・署名・RPC完備。ノードリリースとバージョン連動 |
|                         |                                            | [`sui-rust-sdk`](https://github.com/MystenLabs/sui-rust-sdk) (軽量版)                                               | △ v0.1.x | 新しいモジュラー設計。WASM対応。まだpre-1.0                           |
| **Cetusプロトコル連携** | `@cetusprotocol/cetus-sui-clmm-sdk` ^5.4.0 | **公式SDK なし — 自前PTB構築が必要**                                                                                | ✗        | **最大のブロッカー**。詳細は§3参照                                    |
| **Cetus Aggregator**    | `@cetusprotocol/aggregator-sdk` ^1.4.5     | **なし — Cetus REST API で代替**                                                                                    | —        | `reqwest` 経由でルーティングAPI呼び出し                               |
| **非同期ランタイム**    | Node.js Event Loop                         | [`tokio`](https://crates.io/crates/tokio)                                                                           | ◎        | デファクト。`features = ["full"]`                                     |
| **スケジューラ**        | `node-cron` ^3.0.3                         | `tokio::time::interval` (標準)                                                                                      | ◎        | 固定間隔なら追加クレート不要                                          |
|                         |                                            | [`tokio-cron-scheduler`](https://crates.io/crates/tokio-cron-scheduler)                                             | ○        | cron式が必要な場合のみ。月156kダウンロード                            |
| **環境変数管理**        | `dotenv` ^16.4.7                           | [`dotenvy`](https://crates.io/crates/dotenvy) v0.15                                                                 | ◎        | `dotenv`の後継メンテナンス版                                          |
|                         |                                            | [`envy`](https://crates.io/crates/envy)                                                                             | ○        | serde連携で型安全なConfig構造体へ直接デシリアライズ                   |
| **ロギング**            | `winston` ^3.17.0                          | [`tracing`](https://crates.io/crates/tracing) + [`tracing-subscriber`](https://crates.io/crates/tracing-subscriber) | ◎        | Tokio公式。`features = ["json", "env-filter"]` で構造化JSON出力       |
| **型・スキーマ検証**    | `zod` ^3.24.1                              | [`serde`](https://crates.io/crates/serde) + `serde_json`                                                            | ◎        | Rustの型システム自体がzodの役割を大部分カバー                         |
| **外部通信**            | Node.js fetch                              | [`reqwest`](https://crates.io/crates/reqwest)                                                                       | ◎        | デファクト。`features = ["json", "rustls-tls"]`                       |
| **大整数演算**          | BN.js (SDK内部)                            | [`ruint`](https://crates.io/crates/ruint) v1.17                                                                     | ◎        | `U256`等。sqrtPriceX64のQ64.64演算に必須                              |
| **精密小数**            | —                                          | [`bigdecimal`](https://crates.io/crates/bigdecimal) v0.4                                                            | ○        | USD表示・収益率計算用                                                 |
| **CLMM数学**            | Cetus SDK内部                              | 後述（§3-2）                                                                                                        | —        | tick_math / sqrt_price_math / liquidity_math                          |
| **テスト**              | `vitest` ^4.0.18                           | Rust標準 `#[test]` + `cargo test`                                                                                   | ◎        | 追加クレート不要                                                      |

### 参考 Cargo.toml

```toml
[dependencies]
tokio = { version = "1", features = ["full"] }
reqwest = { version = "0.12", features = ["json", "rustls-tls"], default-features = false }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["json", "env-filter"] }
dotenvy = "0.15"
envy = "0.4"
ruint = { version = "1", features = ["num-traits"] }
bigdecimal = "0.4"
sui-sdk = "1"  # Suiノードバージョンに合わせてピン留め
```

## 3. 実現可能性 (Feasibility)

**結論：移行の実現は【可能（中〜高難易度）】**

最大の課題は、**Cetus CLMMの公式Rust SDKが存在しないこと**である。（※2026年2月現在、CetusProtocol公式リポジトリ、GitHub全体、および `crates.io` を網羅的に調査した結果、公式・非公式を問わず、現行のSui CLMMに対応し保守されているRust SDKは**一切存在しない**ことを確認済みです。）

### 3-1. Cetus連携の解決アプローチ

RustからCetusを操作するには、以下3つのアプローチがある。

#### A. PTB直接構築（推奨・完全な代替実装）

**「公式SDKがない＝実装できない」ではありません。** 現在のTypeScript SDK (`cetus-sui-clmm-sdk`) は、結局のところ**「Suiネットワーク上のMove関数を呼び出すための手順書（PTB: Programmable Transaction Block）」を組み立てるための薄いラッパー**に過ぎません。

TypeScriptでもRustでも、最終的にSuiネットワークに送信する命令（PTBデータ）は全く同じです。
したがって、**Sui公式の `sui-sdk` (Rust) を用いて、TS SDKが裏側で行っているのと同じPTB組み立てロジックを自前で書く** ことで、100%同等の機能（完全な代替）が実装可能です。

```rust
use sui_sdk::types::programmable_transaction_builder::ProgrammableTransactionBuilder;

let mut ptb = ProgrammableTransactionBuilder::new();
// TS SDKの addLiquidity() 内部で行われているのと同じMove関数呼び出しを直接記述する
ptb.programmable_move_call(
    cetus_package_id,                    // Cetus CLMMパッケージID
    Identifier::new("pool")?,           // モジュール名
    Identifier::new("open_position")?,  // 関数名
    vec![type_arg_a, type_arg_b],       // CoinType型引数
    vec![pool_obj, tick_lower, tick_upper],
);
```

**再実装が必要な操作一覧:**

| 操作           | 現TS SDK メソッド                         | Move関数                                      |
| :------------- | :---------------------------------------- | :-------------------------------------------- |
| ポジション開設 | `createAddLiquidityTransactionPayload`    | `pool::open_position` + `pool::add_liquidity` |
| 流動性追加     | 同上                                      | `pool::add_liquidity_fix_coin`                |
| 流動性削除     | `createRemoveLiquidityTransactionPayload` | `pool::remove_liquidity`                      |
| ポジション閉鎖 | `closePositionTransactionPayload`         | `pool::close_position`                        |
| 手数料回収     | `collectFeesTransactionPayload`           | `pool::collect_fee`                           |
| リワード回収   | `collectRewarderTransactionPayload`       | `pool::collect_reward`                        |
| スワップ       | `createSwapTransactionPayload`            | `pool::swap`                                  |

参照: [cetus-clmm-interface](https://github.com/CetusProtocol/cetus-clmm-interface) （Move関数シグネチャ）

**工数見積: 2〜4週間**

#### B. CLMM数学ライブラリの移植

オフチェーンでのtick計算・価格見積もりに必要。以下の選択肢がある:

| ライブラリ                                                                                             | ソース           | 成熟度         | 備考                                                                                                           |
| :----------------------------------------------------------------------------------------------------- | :--------------- | :------------- | :------------------------------------------------------------------------------------------------------------- |
| [Raydium CLMM math](https://github.com/raydium-io/raydium-clmm/tree/master/programs/amm/src/libraries) | Solana (Raydium) | **◎ 本番実績** | `tick_math.rs`, `sqrt_price_math.rs`, `liquidity_math.rs` が純Rust。Solana依存なし。Uniswap V3同等の数学モデル |
| [`clmm-swap-math`](https://github.com/aleexeyy/rust-uniswap-v3)                                        | crates.io        | △ 早期段階     | v0.1.0, 2 stars, 2025/11作成。本番利用にはリスクあり                                                           |
| 自前実装                                                                                               | Cetus TS SDK     | —              | TS SDKソースコードからQ64.64演算を移植                                                                         |

**推奨: Raydium CLMMの数学モジュールを抽出・適合**（Cetusと同じUniswap V3数学モデルのため互換性あり）

#### C. Cetus REST API併用（ハイブリッド方式）

価格見積もり・ルーティング等の複雑な計算をCetus APIに委譲し、TX構築・署名・送信のみRustで行う。移行難易度を大幅に下げられるが、API依存が増える。

### 3-2. Sui Rust SDK の現状

2つのSDKが並存している:

| SDK                    | リポジトリ                                                                                  | 状態           | 用途                                     |
| :--------------------- | :------------------------------------------------------------------------------------------ | :------------- | :--------------------------------------- |
| `sui-sdk` (monorepo版) | [MystenLabs/sui/crates/sui-sdk](https://github.com/MystenLabs/sui/tree/main/crates/sui-sdk) | 本番利用可     | PTB構築、TX署名・送信、RPCクライアント   |
| `sui-rust-sdk` (新版)  | [MystenLabs/sui-rust-sdk](https://github.com/MystenLabs/sui-rust-sdk)                       | v0.1.x pre-1.0 | モジュラー設計、WASM対応。将来の主流候補 |

本移行には **monorepo版 `sui-sdk`** を推奨（本番実績あり）。

### 3-3. 参考実装

| リポジトリ                                                                                  | Stars | 言語     | 参考価値                                                        |
| :------------------------------------------------------------------------------------------ | :---- | :------- | :-------------------------------------------------------------- |
| [fuzzland/sui-mev](https://github.com/fuzzland/sui-mev)                                     | 745   | **Rust** | Sui上のMEV/アービトラージBot。PTB構築パターンの最良リファレンス |
| [MystenLabs/capybot](https://github.com/MystenLabs/capybot)                                 | —     | TS       | Mysten公式リファレンスBot。Cetus/Turbos対応。戦略設計の参考     |
| [CetusProtocol/cetus-clmm-interface](https://github.com/CetusProtocol/cetus-clmm-interface) | —     | Move     | Cetus Move関数のインターフェース定義。PTB構築時の必須参照       |

## 4. Rust移行による期待効果

### ① GCEリソース最適化

| 指標            | Node.js (現状)           | Rust (推定)               | 改善率   |
| :-------------- | :----------------------- | :------------------------ | :------- |
| メモリ使用量    | 80〜150 MB               | 5〜15 MB                  | **90%↓** |
| バイナリ + 依存 | ~200 MB (node_modules含) | 10〜30 MB (static linked) | **85%↓** |
| CPU (idle時)    | 1〜3% (V8 GC含む)        | ~0.01%                    | **99%↓** |
| 起動時間        | 2〜5秒                   | ~10 ms                    | **99%↓** |

現在 e2-micro (1GB RAM) で運用中。Rust化により f1-micro (0.6GB) への縮小、または同インスタンスで複数プール並行監視が可能になる。

> **注意:** 既にGCE最小tierで運用中のため、**月額コスト削減効果は限定的**（数ドル程度）。効果の本質はリソース余裕の確保とスケーラビリティにある。

### ② デプロイと運用保守の簡略化

- Node.jsランタイム不要、`npm install` / `node_modules` が消滅
- **単一バイナリ** を `scp` + `systemd` で配置するだけで完了
- Dockerコンテナ化する場合も `FROM scratch` で極小イメージ（~10 MB）

### ③ セキュリティと安全性の向上

- Rustの所有権モデルにより `undefined` / `null` 起因のクラッシュを根絶
- 資金を扱うBotの長期無人運用において「コンパイルが通れば安全に動く」安心感
- メモリリーク・GCストップ・ザ・ワールドのない安定した長期稼働

### ④ 実行パフォーマンスの向上

- TX署名・BCSシリアライズ・暗号演算で TS を凌駕
- ただし本Botの律速はネットワーク I/O（RPC応答30〜300ms）であり、**計算速度の差が運用上の優位に直結する場面は限定的**

## 5. リスクと課題

| リスク                          | 影響度 | 対策                                                                         |
| :------------------------------ | :----- | :--------------------------------------------------------------------------- |
| Cetus TS SDK のアップデート追従 | **高** | Cetus がコントラクトを更新するたびにRust側のPTB構築コードも手動修正が必要    |
| Sui Rust SDK の破壊的変更       | 中     | monorepo版はノードリリースと連動。バージョンをピン留めし慎重にアップグレード |
| CLMM数学の精度検証              | 中     | Raydium数学モジュール採用時、Cetusのtick spacing との互換性テスト必須        |
| 開発速度の低下                  | 中     | TS → Rust の学習コスト。TS比でイテレーション速度が遅い                       |
| デバッグ難度の上昇              | 低〜中 | TS比でon-chain TXのデバッグが困難（SDK の抽象化レイヤーがないため）          |

## 6. ROI 総合評価

| 観点               | 評価                                                   |
| :----------------- | :----------------------------------------------------- |
| 実現可能性         | **可能だが高コスト**（Cetus SDK手動再実装 2〜4週間）   |
| GCEコスト削減      | **限定的**（既にe2-micro。月額差は数ドル）             |
| 運用安定性向上     | **◎ 高い**（GCなし、メモリリークなし、長期無停止運用） |
| スケーラビリティ   | **◎**（同インスタンスで複数プール並行監視可能）        |
| メンテナンスコスト | **△ 増加**（Cetus SDK更新の手動追従）                  |
| 開発速度           | **TS >> Rust**（特にSDK不在のDeFi領域）                |

## 7. 推奨戦略と次のステップ

### 推奨: 段階的移行（PoCファースト）

全面書き換えは ROI が現時点では低いため、**段階的アプローチ**を推奨する。

#### Phase 0: PoC（1〜2週間）

1. 現TSコードの Cetus SDK 呼び出し箇所を分析し、PTB構造を文書化
2. Rust `sui-sdk` で **Cetus Pool の状態読み取り** (read-only) を実装
3. GCE上でメモリ使用量を計測し、TS版と定量比較

#### Phase 1: Read-only機能の移行（2〜3週間）

- プール監視・価格取得・ボラティリティ計算をRustで実装
- 既存TSシステムと並行稼働し、出力を突合検証

#### Phase 2: 書き込み機能の移行（3〜4週間）

- PTB構築によるリバランス・コンパウンド・ハーベスト実装
- testnetで十分なテスト後、mainnet切り替え

### 代替案

即座の移行が見合わない場合の選択肢:

| 代替案                       | 効果                                                                                  | 工数 |
| :--------------------------- | :------------------------------------------------------------------------------------ | :--- |
| **TSのままメモリ最適化**     | `--max-old-space-size=128` でV8ヒープ制限、不要依存削除                               | 小   |
| **CLMM数学のみRust(WASM)化** | 計算集約部をWASMモジュールとしてTSから呼び出し                                        | 中   |
| **Cetus公式Rust SDK待ち**    | Suiエコシステムの Rust 化進行中（`sui-rust-sdk` 整備中）。DEX SDKが追随する可能性あり | —    |
