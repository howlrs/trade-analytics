# Sui Auto LP - Logic Flow

## System Overview

```mermaid
graph TB
    Start[index.ts 起動] --> LoadConfig[loadConfig - dotenv + Zod]
    LoadConfig --> InitLogger[initLogger]
    InitLogger --> LoadWallet[loadKeypair]
    LoadWallet --> InitSui[initSuiClient]
    InitSui --> InitCetus[initCetusSdk]
    InitCetus --> StartScheduler[startScheduler]

    StartScheduler --> RebalanceTimer["setInterval<br/>checkIntervalSec (30s)"]
    StartScheduler --> HarvestTimer["setInterval<br/>harvestIntervalSec (7200s)"]

    RebalanceTimer --> RebalanceCheck[runRebalanceCheck]
    HarvestTimer --> HarvestCheck[runHarvestCheck]

    RebalanceCheck -->|各プール| FetchPool["getPool → getPositions"]
    FetchPool --> FeeFetch{"120s経過?"}
    FeeFetch -->|Yes| FetchFees["fetchPositionFees<br/>→ feeTracker.record"]
    FeeFetch -->|No| SkipFetch["前回のfee dataを使用"]
    FetchFees --> PosLoop
    SkipFetch --> PosLoop
    PosLoop["各ポジション"] -->|feeContext付き| Rebalance[checkAndRebalance]

    HarvestCheck -->|各プール| PoolLoop2["getPool → getPositions"]
    PoolLoop2 -->|各ポジション| Harvest[checkAndHarvest]

    SIGINT[SIGINT / SIGTERM] --> Stop[clearInterval → exit]
```

## Rebalance Trigger Flow

```mermaid
flowchart TD
    Start([evaluateRebalanceTrigger]) --> Direction{"range-out<br/>方向判定"}

    Direction --> CooldownCheck{クールダウン<br/>経過?}

    CooldownCheck -->|"上昇 or 通常:<br/>1800s未満"| CooldownSkip(["スキップ: クールダウン中"])
    CooldownCheck -->|"下落:<br/>3600s未満"| CooldownDown(["スキップ: 下落クールダウン中<br/>(反発待機 60分)"])

    CooldownCheck -->|"経過済み or 初回"| DailyLimit{"日次上限<br/>チェック"}

    DailyLimit -->|"上限到達 &<br/>range内"| SoftBlock(["ソフトブロック:<br/>threshold/time-based 停止"])
    DailyLimit -->|"上限到達 &<br/>range外"| RangeOut
    DailyLimit -->|"上限未到達"| RangeOut

    RangeOut{現在価格が<br/>LP範囲外?}

    RangeOut -->|"Yes (下落)"| DownGate["下落 range-out<br/>ポジション = 100% SUI"]
    RangeOut -->|"Yes (上昇)"| UpGate["上昇 range-out<br/>ポジション = 100% USDC"]

    DownGate --> ProfitGate{"収益性ゲート<br/>breakeven ≤ 12h?"}
    UpGate --> ProfitGate

    ProfitGate -->|"実測データあり"| CalcReal["breakeven =<br/>swapCost / observedHourlyFeeUsd"]
    ProfitGate -->|"データ不足"| CalcFallback["breakeven =<br/>推定モデル (フォールバック)"]

    CalcReal --> BECheck{breakeven<br/>≤ 48h?}
    CalcFallback --> BECheck

    BECheck -->|No| ProfitSkip(["スキップ: 赤字リバランス回避"])
    BECheck -->|Yes| Triggered[shouldRebalance = true<br/>trigger: range-out]

    RangeOut -->|No| ClearDir["方向トラッキング<br/>クリア (範囲内復帰)"]
    ClearDir --> Threshold{範囲端から<br/>threshold(10%) 以内?}

    Threshold -->|Yes| Triggered2[shouldRebalance = true<br/>trigger: threshold]
    Threshold -->|No| TimeBased{前回リバランスから<br/>interval 経過?}

    TimeBased -->|Yes| Triggered3[shouldRebalance = true<br/>trigger: time-based]
    TimeBased -->|No| Skip([スキップ])

    style SoftBlock fill:#888,color:#fff
    style CooldownSkip fill:#888,color:#fff
    style CooldownDown fill:#f80,color:#fff
    style ProfitSkip fill:#f80,color:#fff
    style Skip fill:#888,color:#fff
    style DownGate fill:#d32,color:#fff
    style UpGate fill:#07a,color:#fff
```

## Rebalance Execution Flow

```mermaid
flowchart TD
    Triggered([トリガー発火]) --> CalcRange[calculateOptimalRange<br/>戦略に基づく新範囲計算]

    CalcRange --> StrategySelect{strategy}
    StrategySelect -->|narrow| Narrow["±narrowRangePct (3%)"]
    StrategySelect -->|wide| Wide["±wideRangePct (8%)"]
    StrategySelect -->|dynamic| Dynamic["±(narrow+wide)/2 = ±5.5%"]

    Narrow --> AlignTick
    Wide --> AlignTick
    Dynamic --> AlignTick

    AlignTick[alignTickToSpacing<br/>tickSpacing に合わせて丸め]

    AlignTick --> PreFlight{"ウォレットSUI ≥<br/>0.15 SUI?"}
    PreFlight -->|No| GasError(["エラー: ガス不足"])
    PreFlight -->|Yes| SnapPre["preClose スナップショット<br/>ウォレット残高記録"]

    SnapPre --> Close["Step 1: closePosition<br/>(collect_fee=true)"]
    Close --> CloseOk{close 成功?}

    CloseOk -->|No| ErrorClose([エラー返却])
    CloseOk -->|Yes| Delta["ポジション資金 =<br/>postClose - preClose (delta)"]

    Delta --> DeltaCheck{delta > 0?}
    DeltaCheck -->|"No (live)"| Abort(["ABORT: delta ≤ 0<br/>安全停止"])
    DeltaCheck -->|"No (dry-run)"| Estimate["推定値使用<br/>estimatePositionAmounts"]
    DeltaCheck -->|Yes| CalcSwap

    Estimate --> SwapFreeCheck{swapFreeRebalance?}

    SwapFreeCheck -->|Yes| SkipSwap["Step 2: Swap-free mode<br/>スワップスキップ<br/>close時の比率をそのまま使用"]
    SwapFreeCheck -->|No| CalcSwap["Step 2: calculateSwapPlan<br/>最適比率算出"]

    SkipSwap --> Open

    CalcSwap --> NeedSwap{スワップ<br/>必要?}

    NeedSwap -->|No| Open
    NeedSwap -->|Yes| ExecSwap["executeSwap<br/>(Cetus Router)"]
    ExecSwap --> SwapOk{swap 成功?}

    SwapOk -->|No| SwapFail(["CRITICAL: close済み<br/>swap失敗 → 資金wallet内"])
    SwapOk -->|Yes| ReQuery["残高再取得 (delta方式)"]
    ReQuery --> Open

    Open["Step 3: openPosition<br/>新範囲 + ポジション資金投入<br/>(fix_amount_a 2パス)"]
    Open --> OpenOk{open 成功?}

    OpenOk -->|No| CriticalFail(["CRITICAL: close+swap済み<br/>open失敗 → 資金wallet内"])
    OpenOk -->|Yes| PostRebalance["lastRebalanceTime 更新<br/>feeTracker.handleRebalance"]
    PostRebalance --> Done([完了 - digest 返却])

    style CriticalFail fill:#d32,color:#fff
    style SwapFail fill:#d32,color:#fff
    style ErrorClose fill:#d32,color:#fff
    style GasError fill:#d32,color:#fff
    style Abort fill:#d32,color:#fff
```

## Fee Tracking Flow

```mermaid
flowchart TD
    Cycle["チェックサイクル (30s)"] --> Elapsed{"前回fee取得から<br/>120s 経過?"}

    Elapsed -->|No| UseCache["キャッシュ済みrateを使用"]
    Elapsed -->|Yes| Fetch["fetchPositionFees<br/>(RPC呼び出し)"]

    Fetch --> Record["feeTracker.record<br/>(posId, feeA, feeB)"]

    Record --> Reset{feeA or feeB が<br/>前回より減少?}
    Reset -->|Yes| NewWindow["fee リセット検知<br/>(harvest/close後)<br/>観測窓リスタート"]
    Reset -->|No| Update["latest スナップショット更新"]

    NewWindow --> GetRate
    Update --> GetRate
    UseCache --> GetRate

    GetRate["feeTracker.getHourlyRate"]
    GetRate --> Enough{"観測時間<br/>≥ 5分?"}

    Enough -->|No| NoData["null → フォールバック推定"]
    Enough -->|Yes| CalcRate["hourlyRate =<br/>(latest - first) / elapsed<br/>→ USD換算"]

    CalcRate --> FeeContext["FeeContext {<br/>observedHourlyFeeUsd,<br/>positionValueUsd<br/>}"]

    NoData --> FeeContext
    FeeContext --> Trigger["evaluateRebalanceTrigger<br/>に渡す"]

    style NoData fill:#888,color:#fff
```

## Harvest Flow

```mermaid
flowchart TD
    Start([checkAndHarvest]) --> FetchFees[fetchPositionFees<br/>batchFetchPositionFees]

    FetchFees --> HasFees{feeA > 0 or<br/>feeB > 0?}
    HasFees -->|No| SkipNoFee([スキップ: 手数料なし])

    HasFees -->|Yes| EstGas[estimatedGasCost = 5M MIST]
    EstGas --> Profitable{totalFee ≥<br/>gas × minGasProfitRatio?}

    Profitable -->|No| SkipUnprofitable(["スキップ: ガス代 > 利益"])
    Profitable -->|Yes| AddLiq[addLiquidity<br/>collectFee: true]

    AddLiq --> DryRun{dry-run 成功?}
    DryRun -->|No| Error([エラー返却])
    DryRun -->|Yes| DryOnly{dryRun モード?}

    DryOnly -->|Yes| DryResult([dry-run 結果返却])
    DryOnly -->|No| Execute[トランザクション実行<br/>手数料回収 + 流動性追加]

    Execute --> ExecOk{成功?}
    ExecOk -->|No| Error
    ExecOk -->|Yes| Done([完了 - digest 返却])

    style SkipNoFee fill:#888,color:#fff
    style SkipUnprofitable fill:#888,color:#fff
    style Error fill:#d32,color:#fff
```

## Transaction Safety Flow

```mermaid
flowchart TD
    Payload[SDK payload 生成] --> Build["payload.build(client)"]
    Build --> DryRun["dryRunTransactionBlock<br/>(シミュレーション)"]

    DryRun --> Status{status =<br/>success?}
    Status -->|No| Abort([中止 - エラー返却])
    Status -->|Yes| CalcGas["gasCost =<br/>computation + storage - rebate"]

    CalcGas --> Mode{config.dryRun?}
    Mode -->|true| ReturnDry([dry-run 結果のみ返却<br/>実行しない])
    Mode -->|false| Send["fullClient.sendTransaction<br/>(keypair, payload)"]

    Send --> Response{response<br/>存在?}
    Response -->|No| SendError([送信エラー])
    Response -->|Yes| CheckEffects{effects.status =<br/>success?}

    CheckEffects -->|No| TxError([トランザクション失敗])
    CheckEffects -->|Yes| Success([成功 - digest + gasCost 返却])

    style Abort fill:#d32,color:#fff
    style SendError fill:#d32,color:#fff
    style TxError fill:#d32,color:#fff
    style ReturnDry fill:#07a,color:#fff
```

## Scheduler Safety

```mermaid
flowchart TD
    Timer["setInterval (30s)"] --> Running{前回チェック<br/>実行中?}
    Running -->|Yes| SkipConcurrent([スキップ: 重複防止])
    Running -->|No| Lock["running = true"]

    Lock --> FetchPool["getPool → getPositions"]
    FetchPool --> FeeFetch{"120s経過?"}
    FeeFetch -->|Yes| FetchFees["fetchPositionFees<br/>→ feeTracker.record"]
    FeeFetch -->|No| SkipFee["feeフェッチ省略"]

    FetchFees --> PosLoop
    SkipFee --> PosLoop

    PosLoop["各ポジション<br/>+ FeeContext構築"] --> Rebalance["checkAndRebalance<br/>(cooldown + 収益性ゲート内蔵)"]

    Rebalance --> Result{リバランス結果}
    Result -->|成功 or スキップ| Continue[次のポジションへ]
    Result -->|失敗| Halt["スケジューラ停止<br/>clearInterval"]

    Continue --> MorePositions{残りある?}
    MorePositions -->|Yes| PosLoop
    MorePositions -->|No| Unlock["running = false"]

    Halt --> Alert(["HALT: 安全停止<br/>手動確認が必要"])

    style Alert fill:#d32,color:#fff
    style SkipConcurrent fill:#888,color:#fff
```

## Protection Layers

```mermaid
flowchart LR
    Check["30sチェック"] --> L1["① running guard<br/>同時実行防止"]
    L1 --> L1b["① -b 日次上限 (soft)<br/>≤ 3回/日<br/>range-outは通過"]
    L1b --> L2["② クールダウン<br/>上昇1800s / 下落3600s"]
    L2 --> L2b["② -b rangeout待機<br/>1800s"]
    L2b --> L3["③ 収益性ゲート<br/>breakeven ≤ 48h"]
    L3 --> L3b["③ -b minTimeInRange<br/>threshold: 2h"]
    L3b --> L4["④ ガス残高チェック<br/>≥ 0.15 SUI"]
    L4 --> L5["⑤ delta検証<br/>資金隔離"]
    L5 --> L5b["⑤ -b GAS_RESERVE<br/>1.0 SUI 予約"]
    L5b --> L6["⑥ dry-run<br/>TX事前検証"]
    L6 --> Exec["実行"]

    L1 -.->|"ブロック"| Skip1(["スキップ"])
    L2 -.->|"ブロック"| Skip2(["スキップ"])
    L3 -.->|"ブロック"| Skip3(["スキップ"])
    L4 -.->|"ブロック"| Skip4(["エラー"])
    L5 -.->|"ブロック"| Skip5(["ABORT"])
    L6 -.->|"ブロック"| Skip6(["エラー"])

    style Skip4 fill:#d32,color:#fff
    style Skip5 fill:#d32,color:#fff
    style Skip6 fill:#d32,color:#fff
    style Skip1 fill:#888,color:#fff
    style Skip2 fill:#888,color:#fff
    style Skip3 fill:#f80,color:#fff
```

## Module Dependency

```mermaid
graph LR
    index[index.ts] --> config[config/]
    index --> logger[utils/logger]
    index --> wallet[utils/wallet]
    index --> sui[utils/sui]
    index --> pool[core/pool]
    index --> scheduler[scheduler]

    scheduler --> pool
    scheduler --> position[core/position]
    scheduler --> rebalance[core/rebalance]
    scheduler --> harvest[core/compound]
    scheduler --> feeTracker[utils/fee-tracker]
    scheduler --> price[core/price]

    rebalance --> trigger[strategy/trigger]
    rebalance --> range[strategy/range]
    rebalance --> position
    rebalance --> swap[core/swap]
    rebalance --> feeTracker

    swap --> pool
    swap --> price
    harvest --> position

    trigger --> price
    range --> price
    position --> pool
    position --> price

    config --> types[types/config]
    pool --> types2[types/index]

    style index fill:#07a,color:#fff
    style scheduler fill:#07a,color:#fff
    style swap fill:#f80,color:#fff
    style feeTracker fill:#0a6,color:#fff
```

## Swap & Optimal Ratio Flow

```mermaid
flowchart TD
    Start([calculateSwapPlan]) --> GetRatio["ClmmPoolUtil<br/>.calculateDepositRatioFixTokenA<br/>(tickLower, tickUpper, sqrtPrice)"]

    GetRatio --> Ratio["ratioA : ratioB<br/>例: 0.48 : 0.52"]

    Ratio --> CalcValue["現在残高のUSD評価<br/>valueA = balanceA (USDC)<br/>valueB = balanceB × SUI価格"]

    CalcValue --> CalcTarget["目標配分<br/>targetA = total × ratioA<br/>targetB = total × ratioB"]

    CalcTarget --> Diff["差分 = targetA - valueA"]

    Diff --> Check{差分 > $1?}
    Check -->|No| NoSwap([needSwap: false<br/>差分小さいためスキップ])

    Check -->|Yes| Direction{targetA > valueA?}

    Direction -->|Yes, USDC不足| SwapB2A["SUI → USDC スワップ<br/>a2b: false<br/>amount: 差分相当のSUI"]

    Direction -->|No, SUI不足| SwapA2B["USDC → SUI スワップ<br/>a2b: true<br/>amount: 差分相当のUSDC"]

    SwapB2A --> PreSwap
    SwapA2B --> PreSwap

    PreSwap["sdk.Swap.preswap()<br/>→ estimatedAmountOut<br/>→ isExceed check"]

    PreSwap --> Exceed{isExceed?}
    Exceed -->|Yes| Error([エラー: 流動性不足])
    Exceed -->|No| CalcLimit["amountLimit =<br/>estimatedOut × (1 - slippage)"]

    CalcLimit --> CreateTx["sdk.Swap.createSwapTransactionPayload"]
    CreateTx --> DryRun[dry-run]
    DryRun --> Execute[実行 or dry-run結果返却]

    style NoSwap fill:#888,color:#fff
    style Error fill:#d32,color:#fff
```

## Out-of-Range Behavior (Asymmetric)

```mermaid
flowchart TD
    RangeOut["レンジアウト検知"]

    RangeOut --> Direction{SUI価格の方向}

    Direction -->|"上昇 (price > upper)"| UpPath["ポジション = 100% USDC<br/>クールダウン: 30分"]
    Direction -->|"下落 (price < lower)"| DownPath["ポジション = 100% SUI<br/>クールダウン: 60分<br/>(反発待機)"]

    UpPath --> RangeWait1{"30分待機<br/>(waitAfterRangeout)"}
    RangeWait1 -->|No| UpWait(["待機: 自己修復を待つ"])
    RangeWait1 -->|Yes| ProfitGate

    DownPath --> RangeWait2{"30分待機<br/>(waitAfterRangeout)"}
    RangeWait2 -->|No| DownWait(["待機: 反発の可能性<br/>SUIを底値で売らない"])
    RangeWait2 -->|"Yes (反発なし)"| DownCheck{"価格が範囲内に<br/>戻った?"}

    DownCheck -->|Yes| Recovered(["リバランス不要!<br/>swap費用+底値売り回避"])
    DownCheck -->|No| ProfitGate

    ProfitGate{"収益性ゲート<br/>breakeven ≤ 48h?"}
    ProfitGate -->|No| WaitProfit(["待機: 収益性改善まで"])

    ProfitGate -->|Yes| Close["closePosition<br/>→ 資金回収"]
    Close --> SwapCheck{swapFreeRebalance?}
    SwapCheck -->|Yes| NoSwap["Swap-free<br/>close時比率をそのまま使用<br/>(0.25%手数料回避)"]
    SwapCheck -->|No| Swap["Swap ~50%<br/>最適比率に調整"]
    NoSwap --> NewRange
    Swap --> NewRange["新レンジ計算<br/>現在価格 ±8~15%"]
    NewRange --> Reopen["openPosition"]
    Reopen --> ResetTracker["feeTracker リセット<br/>lastRebalanceTime 更新"]
    ResetTracker --> Earning(["手数料収益再開"])

    style Earning fill:#0a6,color:#fff
    style UpWait fill:#888,color:#fff
    style DownWait fill:#f80,color:#fff
    style WaitProfit fill:#f80,color:#fff
    style Recovered fill:#0a6,color:#fff
```

## Default Parameters (Current)

| パラメータ | 値 | 説明 |
|---|---|---|
| `checkIntervalSec` | 30s | プール状態チェック間隔 |
| `harvestIntervalSec` | 7200s (2h) | ハーベスト（手数料claim）チェック間隔 |
| `narrowRangePct` | ±8% | ナローレンジ幅 |
| `wideRangePct` | ±15% | ワイドレンジ幅 |
| `rebalanceThreshold` | 3% (推奨: 10%) | 範囲端からのリバランス閾値 |
| `COOLDOWN_UP_SEC` | 1800s (30min) | 上昇時のリバランスクールダウン |
| `COOLDOWN_DOWN_SEC` | 3600s (60min) | 下落時のリバランスクールダウン（反発待機） |
| `waitAfterRangeoutSec` | 1800s (30min) | レンジアウト検出後の待機時間 |
| `maxRebalancesPerDay` | 3 | 1日あたり最大リバランス回数 |
| `minTimeInRangeSec` | 7200s (2h) | 新ポジション開設後の最低レンジ内時間（threshold用） |
| `FEE_FETCH_INTERVAL_SEC` | 120s (2min) | fee RPC呼び出し間隔 |
| `maxBreakevenHours` | 48h | 収益性ゲート閾値 |
| `slippageTolerance` | 1% | スリッページ上限 |
| `MIN_SUI_FOR_GAS` | 0.15 SUI | ガス残高最低要件（プリフライトチェック） |
| `GAS_RESERVE` | 1.0 SUI | ポジション資金から予約（ウォレット残高を1 SUI以上に維持） |
| `swapFreeRebalance` | true | リバランス時のスワップスキップ（0.25%手数料回避） |
| `swapFreeMaxRatioSwap` | 10% (range-out: 50%) | swap-free 時の ratio-correction スワップ上限 |
| `maxIdleSwapRatio` | 20% | idle deploy 時のスワップ上限（超過分は部分投入） |
| `volTickWidthMin/Max` | 480/1200 | ボラティリティベースのtick幅下限/上限 |
