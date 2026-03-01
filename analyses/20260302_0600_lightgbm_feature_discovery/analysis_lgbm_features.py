import marimo

__generated_with = "0.20.2"
app = marimo.App(width="medium")


# ============================================================
# Cell 1: setup — imports, 定数, フォント設定
# ============================================================
@app.cell
def setup():
    import sys
    from pathlib import Path

    import lightgbm as lgb
    import marimo as mo
    import matplotlib as mpl
    import matplotlib.font_manager as fm
    import matplotlib.pyplot as plt
    import numpy as np
    import polars as pl
    import shap
    from sklearn.metrics import accuracy_score, roc_auc_score

    fm.fontManager.addfont("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    mpl.rcParams["font.family"] = "Noto Sans CJK JP"
    mpl.rcParams["axes.unicode_minus"] = False

    # src/ をimportパスに追加
    _project_root = Path(__file__).parent.parent.parent
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))

    DATA_DIR = _project_root / "data"
    TOKENS = ["BTC", "ETH", "SOL", "SUI"]
    COLORS = {"BTC": "tab:orange", "ETH": "tab:blue", "SOL": "tab:purple", "SUI": "tab:cyan"}
    TRAIN_END = "2025-09-01"

    return (
        COLORS,
        DATA_DIR,
        TOKENS,
        TRAIN_END,
        accuracy_score,
        lgb,
        mo,
        np,
        pl,
        plt,
        roc_auc_score,
        shap,
    )


# ============================================================
# Cell 2: data_load — フィーチャー構築 + Train/Test分割
# ============================================================
@app.cell
def data_load(DATA_DIR, TOKENS, TRAIN_END, mo, np, pl):
    from src.features import build_all_features, get_feature_cols

    all_features = build_all_features(DATA_DIR, tokens=TOKENS)

    # Train/Test split
    feature_data = {}
    for _sym in TOKENS:
        _df = all_features[_sym]
        _df = _df.with_columns(
            pl.when(
                pl.col("timestamp").dt.replace_time_zone(None)
                < pl.lit(TRAIN_END).str.to_datetime()
            )
            .then(pl.lit("train"))
            .otherwise(pl.lit("test"))
            .alias("split"),
        )
        feature_data[_sym] = _df

    # サマリー
    _rows = []
    for _sym in TOKENS:
        _df = feature_data[_sym]
        _train = _df.filter(pl.col("split") == "train")
        _test = _df.filter(pl.col("split") == "test")
        _feat_cols = get_feature_cols(_sym)
        _null_pcts = []
        for _c in _feat_cols:
            _null_pcts.append(_df[_c].null_count() / _df.height * 100)
        _avg_null = np.mean(_null_pcts) if _null_pcts else 0
        _rows.append({
            "通貨": _sym,
            "全データ": _df.height,
            "Train": _train.height,
            "Test": _test.height,
            "特徴量数": len(_feat_cols),
            "平均欠損率(%)": round(_avg_null, 1),
        })
    _summary_df = pl.DataFrame(_rows)

    mo.vstack([
        mo.md("# LightGBM 非線形フィーチャー探索"),
        mo.md(f"""
**目的**: 複数特徴量の非線形交互作用をLightGBMで探索的に分析。
SHAP値で「どの特徴量の組み合わせにエッジがあるか」の仮説を生成。

- **ターゲット**: 8h後リターンの符号 (+1/-1) → 二値分類
- **特徴量**: OHLCV, デリバティブ, ベーシス, センチメント, マクロ, 時刻
- **Train**: < {TRAIN_END} / **Test**: >= {TRAIN_END}
"""),
        mo.md("### データ概要"),
        mo.ui.table(_summary_df.to_pandas()),
    ])
    return (feature_data, get_feature_cols)


# ============================================================
# Cell 3: baseline_model — LightGBM 二値分類
# ============================================================
@app.cell
def baseline_model(COLORS, TOKENS, accuracy_score, feature_data, get_feature_cols, lgb, mo, np, pl, plt, roc_auc_score):
    LGB_PARAMS = {
        "n_estimators": 200,
        "max_depth": 4,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "random_state": 42,
        "verbosity": -1,
        "n_jobs": -1,
    }

    models = {}
    _result_rows = []

    for _sym in TOKENS:
        _df = feature_data[_sym]
        _feat_cols = get_feature_cols(_sym)
        _train = _df.filter(pl.col("split") == "train")
        _test = _df.filter(pl.col("split") == "test")

        _X_train = _train.select(_feat_cols).to_pandas().values
        _y_train = _train["target"].to_numpy()
        _X_test = _test.select(_feat_cols).to_pandas().values
        _y_test = _test["target"].to_numpy()

        # NaN行を除外
        _train_mask = np.all(np.isfinite(_X_train), axis=1)
        _test_mask = np.all(np.isfinite(_X_test), axis=1)
        _X_train, _y_train = _X_train[_train_mask], _y_train[_train_mask]
        _X_test, _y_test = _X_test[_test_mask], _y_test[_test_mask]

        # ラベルを0/1に変換 (LightGBM binary)
        _y_train_01 = ((_y_train + 1) / 2).astype(int)
        _y_test_01 = ((_y_test + 1) / 2).astype(int)

        _model = lgb.LGBMClassifier(**LGB_PARAMS)
        _model.fit(_X_train, _y_train_01)
        models[_sym] = _model

        # Train metrics
        _pred_train = _model.predict(_X_train)
        _prob_train = _model.predict_proba(_X_train)[:, 1]
        _acc_train = accuracy_score(_y_train_01, _pred_train)
        _auc_train = roc_auc_score(_y_train_01, _prob_train)

        # Test metrics
        _pred_test = _model.predict(_X_test)
        _prob_test = _model.predict_proba(_X_test)[:, 1]
        _acc_test = accuracy_score(_y_test_01, _pred_test)
        _auc_test = roc_auc_score(_y_test_01, _prob_test)

        # ランダムベースライン
        _base_acc = max(np.mean(_y_test_01), 1 - np.mean(_y_test_01))

        _result_rows.append({
            "通貨": _sym,
            "Train Acc": round(_acc_train, 4),
            "Train AUC": round(_auc_train, 4),
            "Test Acc": round(_acc_test, 4),
            "Test AUC": round(_auc_test, 4),
            "Baseline Acc": round(_base_acc, 4),
            "Acc改善(pp)": round((_acc_test - _base_acc) * 100, 2),
        })

    _results_df = pl.DataFrame(_result_rows)

    # Accuracy 比較プロット
    _fig, _ax = plt.subplots(figsize=(10, 5))
    _x = np.arange(len(TOKENS))
    _w = 0.25
    _train_accs = [r["Train Acc"] for r in _result_rows]
    _test_accs = [r["Test Acc"] for r in _result_rows]
    _base_accs = [r["Baseline Acc"] for r in _result_rows]
    _ax.bar(_x - _w, _train_accs, _w, label="Train Acc", alpha=0.8)
    _ax.bar(_x, _test_accs, _w, label="Test Acc", alpha=0.8)
    _ax.bar(_x + _w, _base_accs, _w, label="Baseline", alpha=0.5, color="gray")
    _ax.set_xticks(_x)
    _ax.set_xticklabels(TOKENS)
    _ax.set_ylabel("Accuracy")
    _ax.set_title("LightGBM 二値分類: Train / Test / Baseline")
    _ax.legend()
    _ax.grid(True, alpha=0.3)
    _ax.axhline(0.5, color="red", linestyle="--", alpha=0.3)
    _fig.tight_layout()

    mo.vstack([
        mo.md("## 1. ベースラインモデル"),
        mo.md(f"""
LightGBM 二値分類 (8h後リターンの方向予測)。

**パラメータ**: `n_estimators=200, max_depth=4, lr=0.05, subsample=0.8`
- 浅い木 (depth=4) で過学習を抑制
- Baseline = テストデータの多数クラス比率
"""),
        _fig,
        mo.md("### 分類結果"),
        mo.ui.table(_results_df.to_pandas()),
    ])
    return (LGB_PARAMS, models)


# ============================================================
# Cell 4: feature_importance — 特徴量重要度 + SHAP
# ============================================================
@app.cell
def feature_importance(COLORS, TOKENS, feature_data, get_feature_cols, models, mo, np, pl, plt, shap):
    # Feature importance (gain)
    _fig_imp, _axes_imp = plt.subplots(2, 2, figsize=(16, 14))
    _axes_imp = _axes_imp.flatten()
    _all_importance_rows = []

    for _ti, _sym in enumerate(TOKENS):
        _model = models[_sym]
        _feat_cols = get_feature_cols(_sym)
        _importances = _model.feature_importances_
        _sorted_idx = np.argsort(_importances)[::-1]

        _top_n = min(15, len(_feat_cols))
        _top_idx = _sorted_idx[:_top_n]
        _ax = _axes_imp[_ti]
        _ax.barh(
            [_feat_cols[i] for i in _top_idx][::-1],
            _importances[_top_idx][::-1],
            color=COLORS[_sym], alpha=0.7,
        )
        _ax.set_title(f"{_sym}: Feature Importance (Gain)")
        _ax.set_xlabel("Importance")
        _ax.grid(True, alpha=0.3)

        for _i, _idx in enumerate(_sorted_idx):
            _all_importance_rows.append({
                "通貨": _sym,
                "特徴量": _feat_cols[_idx],
                "Importance": round(float(_importances[_idx]), 2),
                "Rank": _i + 1,
            })

    _fig_imp.suptitle("LightGBM Feature Importance (Gain)", fontsize=14)
    _fig_imp.tight_layout()

    # SHAP summary plot (1 token ずつ)
    _shap_figs = []
    shap_values_dict = {}
    for _sym in TOKENS:
        _model = models[_sym]
        _feat_cols = get_feature_cols(_sym)
        _df = feature_data[_sym]
        _test = _df.filter(pl.col("split") == "test")
        _X_test = _test.select(_feat_cols).to_pandas()
        _X_test = _X_test.dropna()

        _explainer = shap.TreeExplainer(_model)
        _shap_vals = _explainer.shap_values(_X_test)
        # binary分類: shap_valuesはクラスごとのリスト or 2d array
        if isinstance(_shap_vals, list):
            _sv = _shap_vals[1]  # クラス1 (positive direction)
        else:
            _sv = _shap_vals
        shap_values_dict[_sym] = (_sv, _X_test)

        _fig_shap, _ax_shap = plt.subplots(figsize=(10, 8))
        shap.summary_plot(_sv, _X_test, show=False, max_display=15)
        plt.title(f"{_sym}: SHAP Summary (Test)")
        plt.tight_layout()
        _shap_figs.append(plt.gcf())

    _imp_df = pl.DataFrame(_all_importance_rows)

    _elements = [
        mo.md("## 2. 特徴量重要度分析"),
        mo.md("""
- **Gain**: 特徴量がモデルの分割で獲得した情報量の合計
- **SHAP**: 各特徴量が個別予測に与える影響の方向と大きさ
"""),
        _fig_imp,
    ]
    for _i, _sym in enumerate(TOKENS):
        _elements.append(mo.md(f"### {_sym} SHAP Summary"))
        _elements.append(_shap_figs[_i])
    _elements.extend([
        mo.md("### 全トークン重要度テーブル (Top 10)"),
        mo.ui.table(
            _imp_df.filter(pl.col("Rank") <= 10).sort(["通貨", "Rank"]).to_pandas()
        ),
    ])

    mo.vstack(_elements)
    return (shap_values_dict,)


# ============================================================
# Cell 5: interaction_analysis — SHAP交互作用分析
# ============================================================
@app.cell
def interaction_analysis(TOKENS, COLORS, feature_data, get_feature_cols, models, shap_values_dict, mo, np, pl, plt, shap):
    _elements = [
        mo.md("## 3. 交互作用分析"),
        mo.md("""
SHAP interaction valuesで特徴量ペアの交互作用を定量化。
上位ペアの dependence plot で非線形関係を可視化。

**注**: interaction values の計算は O(n*m^2) のため、サンプルを制限。
"""),
    ]

    interaction_top_pairs = {}
    for _sym in TOKENS:
        _model = models[_sym]
        _feat_cols = get_feature_cols(_sym)
        _sv, _X_test = shap_values_dict[_sym]

        # 特徴量ペアの相互作用強度を SHAP 値の相関で近似
        _n_feats = len(_feat_cols)
        _pair_scores = []
        for _i in range(_n_feats):
            for _j in range(_i + 1, _n_feats):
                # SHAP値とfeature値の交差相関
                _xi = _X_test.iloc[:, _i].values.astype(float)
                _xj = _X_test.iloc[:, _j].values.astype(float)
                _si = _sv[:, _i]
                _mask = np.isfinite(_xi) & np.isfinite(_xj) & np.isfinite(_si)
                if _mask.sum() < 50:
                    continue
                # feature_j が feature_i の SHAP値にどう影響するか
                _corr = np.abs(np.corrcoef(_xj[_mask], _si[_mask])[0, 1])
                if np.isfinite(_corr):
                    _pair_scores.append((_feat_cols[_i], _feat_cols[_j], _corr))

        _pair_scores.sort(key=lambda x: x[2], reverse=True)
        _top3 = _pair_scores[:3]
        interaction_top_pairs[_sym] = _top3

        # Dependence plot for top 3 pairs
        _fig_dep, _axes_dep = plt.subplots(1, 3, figsize=(18, 5))
        for _pi, (_f1, _f2, _score) in enumerate(_top3):
            _idx1 = _feat_cols.index(_f1)
            shap.dependence_plot(
                _idx1, _sv, _X_test,
                interaction_index=_feat_cols.index(_f2),
                ax=_axes_dep[_pi], show=False,
            )
            _axes_dep[_pi].set_title(f"{_f1} × {_f2}\n(interaction={_score:.3f})")
        _fig_dep.suptitle(f"{_sym}: 上位3交互作用ペア", fontsize=13)
        _fig_dep.tight_layout()
        _elements.append(mo.md(f"### {_sym}"))
        _elements.append(_fig_dep)

    # 全トークン横断の交互作用サマリー
    _pair_rows = []
    for _sym in TOKENS:
        for _rank, (_f1, _f2, _score) in enumerate(interaction_top_pairs[_sym], 1):
            _pair_rows.append({
                "通貨": _sym, "Rank": _rank,
                "Feature 1": _f1, "Feature 2": _f2,
                "Interaction Score": round(_score, 4),
            })
    _pair_df = pl.DataFrame(_pair_rows)
    _elements.extend([
        mo.md("### 交互作用ペア サマリー"),
        mo.ui.table(_pair_df.to_pandas()),
    ])

    mo.vstack(_elements)
    return (interaction_top_pairs,)


# ============================================================
# Cell 6: purged_walk_forward — Purged Walk-Forward CV
# ============================================================
@app.cell
def purged_walk_forward(TOKENS, COLORS, accuracy_score, feature_data, get_feature_cols, lgb, LGB_PARAMS, mo, np, pl, plt, roc_auc_score):
    N_FOLDS = 5
    GAP_HOURS = 24  # purge gap

    _elements = [
        mo.md("## 4. Purged Walk-Forward CV"),
        mo.md(f"""
時系列分割で過学習を評価。各foldでTrain→ギャップ({GAP_HOURS}h)→Testの構造。

- **{N_FOLDS} folds**: 時系列順に等分割
- **Purge gap**: {GAP_HOURS}h のギャップでリーク防止
- 各foldの Test Accuracy / AUC を算出
"""),
    ]

    wf_results = {}
    _all_fold_rows = []

    for _sym in TOKENS:
        _df = feature_data[_sym].sort("timestamp")
        _feat_cols = get_feature_cols(_sym)
        _X = _df.select(_feat_cols).to_pandas().values
        _y = ((_df["target"].to_numpy() + 1) / 2).astype(int)
        _n = len(_y)

        # 有効行マスク
        _valid = np.all(np.isfinite(_X), axis=1)

        # 時系列分割: 各foldは累積的に train を拡大
        _fold_size = _n // (N_FOLDS + 1)
        _fold_accs = []
        _fold_aucs = []

        for _fold in range(N_FOLDS):
            _train_end_idx = _fold_size * (_fold + 1)
            _test_start_idx = _train_end_idx + GAP_HOURS
            _test_end_idx = min(_test_start_idx + _fold_size, _n)

            if _test_start_idx >= _n or _test_end_idx <= _test_start_idx:
                continue

            _train_mask = _valid.copy()
            _train_mask[_train_end_idx:] = False
            _test_mask = _valid.copy()
            _test_mask[:_test_start_idx] = False
            _test_mask[_test_end_idx:] = False

            if _train_mask.sum() < 100 or _test_mask.sum() < 50:
                continue

            _X_tr, _y_tr = _X[_train_mask], _y[_train_mask]
            _X_te, _y_te = _X[_test_mask], _y[_test_mask]

            _model = lgb.LGBMClassifier(**LGB_PARAMS)
            _model.fit(_X_tr, _y_tr)

            _pred = _model.predict(_X_te)
            _prob = _model.predict_proba(_X_te)[:, 1]
            _acc = accuracy_score(_y_te, _pred)
            _auc = roc_auc_score(_y_te, _prob)
            _fold_accs.append(_acc)
            _fold_aucs.append(_auc)

            _all_fold_rows.append({
                "通貨": _sym, "Fold": _fold + 1,
                "Train N": int(_train_mask.sum()),
                "Test N": int(_test_mask.sum()),
                "Accuracy": round(_acc, 4),
                "AUC": round(_auc, 4),
            })

        wf_results[_sym] = {"accs": _fold_accs, "aucs": _fold_aucs}

    # Boxplot
    _fig, (_ax1, _ax2) = plt.subplots(1, 2, figsize=(14, 5))
    _acc_data = [wf_results[s]["accs"] for s in TOKENS]
    _auc_data = [wf_results[s]["aucs"] for s in TOKENS]
    _bp1 = _ax1.boxplot(_acc_data, labels=TOKENS, patch_artist=True)
    for _patch, _sym in zip(_bp1["boxes"], TOKENS):
        _patch.set_facecolor(COLORS[_sym])
        _patch.set_alpha(0.5)
    _ax1.axhline(0.5, color="red", linestyle="--", alpha=0.5, label="Random")
    _ax1.set_ylabel("Accuracy")
    _ax1.set_title("Walk-Forward Accuracy")
    _ax1.legend()
    _ax1.grid(True, alpha=0.3)

    _bp2 = _ax2.boxplot(_auc_data, labels=TOKENS, patch_artist=True)
    for _patch, _sym in zip(_bp2["boxes"], TOKENS):
        _patch.set_facecolor(COLORS[_sym])
        _patch.set_alpha(0.5)
    _ax2.axhline(0.5, color="red", linestyle="--", alpha=0.5, label="Random")
    _ax2.set_ylabel("AUC")
    _ax2.set_title("Walk-Forward AUC")
    _ax2.legend()
    _ax2.grid(True, alpha=0.3)
    _fig.suptitle(f"Purged Walk-Forward CV ({N_FOLDS} folds, gap={GAP_HOURS}h)", fontsize=14)
    _fig.tight_layout()

    _fold_df = pl.DataFrame(_all_fold_rows)

    # サマリー行
    _summary_rows = []
    for _sym in TOKENS:
        _accs = wf_results[_sym]["accs"]
        _aucs = wf_results[_sym]["aucs"]
        if _accs:
            _summary_rows.append({
                "通貨": _sym,
                "Acc Mean": round(np.mean(_accs), 4),
                "Acc Std": round(np.std(_accs), 4),
                "AUC Mean": round(np.mean(_aucs), 4),
                "AUC Std": round(np.std(_aucs), 4),
                "Folds": len(_accs),
            })
    _summary_df = pl.DataFrame(_summary_rows)

    _elements.extend([
        _fig,
        mo.md("### Fold別結果"),
        mo.ui.table(_fold_df.to_pandas()),
        mo.md("### Walk-Forward サマリー"),
        mo.ui.table(_summary_df.to_pandas()),
    ])

    mo.vstack(_elements)
    return (wf_results,)


# ============================================================
# Cell 7: edge_analysis — 予測確率の極端値エッジ分析
# ============================================================
@app.cell
def edge_analysis(TOKENS, COLORS, feature_data, get_feature_cols, models, mo, np, pl, plt):
    _elements = [
        mo.md("## 5. エッジ分析 (高確信度予測)"),
        mo.md("""
予測確率の上位/下位10%のみでポジションを取った場合のリターン。
「自信度の高い予測のみ取引」戦略のシミュレーション。
"""),
    ]

    _edge_rows = []
    _fig, _axes = plt.subplots(2, 2, figsize=(16, 10))
    _axes = _axes.flatten()

    for _ti, _sym in enumerate(TOKENS):
        _model = models[_sym]
        _feat_cols = get_feature_cols(_sym)

        for _split_name in ["train", "test"]:
            _df = feature_data[_sym].filter(pl.col("split") == _split_name)
            _X = _df.select(_feat_cols).to_pandas().values
            _fwd = _df["fwd_8h"].to_numpy()

            _valid = np.all(np.isfinite(_X), axis=1) & np.isfinite(_fwd)
            _X_v, _fwd_v = _X[_valid], _fwd[_valid]

            if len(_X_v) < 50:
                continue

            _prob = _model.predict_proba(_X_v)[:, 1]

            # 確率の閾値
            _p10 = np.percentile(_prob, 10)
            _p90 = np.percentile(_prob, 90)

            # 下位10% → ショート (-1)
            _short_mask = _prob <= _p10
            _short_rets = -_fwd_v[_short_mask]

            # 上位10% → ロング (+1)
            _long_mask = _prob >= _p90
            _long_rets = _fwd_v[_long_mask]

            # 全体 (ランダム)
            _all_rets = _fwd_v

            _edge_rows.append({
                "通貨": _sym, "Split": _split_name,
                "Long Top10% Mean(bps)": round(np.mean(_long_rets) * 10000, 2) if len(_long_rets) > 0 else 0,
                "Short Bot10% Mean(bps)": round(np.mean(_short_rets) * 10000, 2) if len(_short_rets) > 0 else 0,
                "All Mean(bps)": round(np.mean(_all_rets) * 10000, 2),
                "Long N": int(_long_mask.sum()),
                "Short N": int(_short_mask.sum()),
                "Long WinRate": round(np.mean(_long_rets > 0), 3) if len(_long_rets) > 0 else 0,
                "Short WinRate": round(np.mean(_short_rets > 0), 3) if len(_short_rets) > 0 else 0,
            })

        # Test equity curve (高確信度のみ)
        _df_test = feature_data[_sym].filter(pl.col("split") == "test").sort("timestamp")
        _X_test = _df_test.select(_feat_cols).to_pandas().values
        _fwd_test = _df_test["fwd_8h"].to_numpy()
        _ts_test = _df_test["timestamp"].to_list()
        _valid_test = np.all(np.isfinite(_X_test), axis=1) & np.isfinite(_fwd_test)

        if _valid_test.sum() > 50:
            _X_tv = _X_test[_valid_test]
            _fwd_tv = _fwd_test[_valid_test]
            _ts_tv = [t for t, v in zip(_ts_test, _valid_test) if v]
            _prob_tv = models[_sym].predict_proba(_X_tv)[:, 1]

            _p10 = np.percentile(_prob_tv, 10)
            _p90 = np.percentile(_prob_tv, 90)

            _positions = np.zeros(len(_prob_tv))
            _positions[_prob_tv >= _p90] = 1.0  # Long
            _positions[_prob_tv <= _p10] = -1.0  # Short

            _pnl = _positions * _fwd_tv
            _cum = np.cumsum(_pnl)

            _ax = _axes[_ti]
            _ax.plot(_ts_tv, _cum * 100, color=COLORS[_sym], linewidth=1)
            _ax.set_title(f"{_sym}: 高確信度予測のみ (Test)")
            _ax.set_ylabel("累積リターン (%)")
            _ax.grid(True, alpha=0.3)
            _ax.axhline(0, color="black", linewidth=0.5)

            _active = np.sum(np.abs(_positions) > 0)
            _ax.annotate(
                f"取引={_active}/{len(_positions)}",
                xy=(0.05, 0.95), xycoords="axes fraction", fontsize=9, va="top",
                bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.5),
            )

    _fig.suptitle("高確信度予測 (上位/下位10%) エクイティカーブ (Test)", fontsize=14)
    _fig.tight_layout()

    _edge_df = pl.DataFrame(_edge_rows)

    _elements.extend([
        _fig,
        mo.md("### エッジ分析テーブル"),
        mo.ui.table(_edge_df.to_pandas()),
    ])

    mo.vstack(_elements)
    return


# ============================================================
# Cell 8: summary — 総合まとめ
# ============================================================
@app.cell
def summary(TOKENS, feature_data, get_feature_cols, interaction_top_pairs, models, wf_results, mo, np, pl):
    # 全トークン横断の特徴量重要度ランキング
    _importance_agg = {}
    for _sym in TOKENS:
        _model = models[_sym]
        _feat_cols = get_feature_cols(_sym)
        _importances = _model.feature_importances_
        for _i, _col in enumerate(_feat_cols):
            if _col not in _importance_agg:
                _importance_agg[_col] = []
            _importance_agg[_col].append(_importances[_i])

    _rank_rows = []
    for _col, _vals in _importance_agg.items():
        _rank_rows.append({
            "特徴量": _col,
            "平均Importance": round(np.mean(_vals), 2),
            "トークン数": len(_vals),
        })
    _rank_rows.sort(key=lambda x: x["平均Importance"], reverse=True)
    _rank_df = pl.DataFrame(_rank_rows[:15])

    # 交互作用パターン集約
    _pair_counts = {}
    for _sym in TOKENS:
        for _f1, _f2, _score in interaction_top_pairs[_sym]:
            _key = tuple(sorted([_f1, _f2]))
            if _key not in _pair_counts:
                _pair_counts[_key] = {"count": 0, "tokens": [], "scores": []}
            _pair_counts[_key]["count"] += 1
            _pair_counts[_key]["tokens"].append(_sym)
            _pair_counts[_key]["scores"].append(_score)

    _pair_summary = []
    for (_f1, _f2), _info in sorted(_pair_counts.items(), key=lambda x: -x[1]["count"]):
        _pair_summary.append({
            "Feature 1": _f1,
            "Feature 2": _f2,
            "出現トークン数": _info["count"],
            "トークン": ", ".join(_info["tokens"]),
            "平均Score": round(np.mean(_info["scores"]), 4),
        })
    _pair_summary_df = pl.DataFrame(_pair_summary[:10]) if _pair_summary else pl.DataFrame()

    # WF安定性評価
    _wf_summary = []
    for _sym in TOKENS:
        _accs = wf_results[_sym]["accs"]
        _aucs = wf_results[_sym]["aucs"]
        if _accs:
            _wf_summary.append(f"- **{_sym}**: Acc={np.mean(_accs):.3f}±{np.std(_accs):.3f}, AUC={np.mean(_aucs):.3f}±{np.std(_aucs):.3f}")
    _wf_text = "\n".join(_wf_summary) if _wf_summary else "データなし"

    mo.vstack([
        mo.md("## 6. 総合まとめ"),
        mo.md(f"""
### Walk-Forward 安定性
{_wf_text}

**判定基準**:
- Acc > 0.51 が安定して持続 → 微弱だがエッジの可能性
- AUC > 0.52 → ランキング力あり
- Std < 0.02 → 安定性良好

### 次ステップ
1. **有望交互作用のルール化**: SHAP dependence plotから単純ルールを抽出
2. **閾値ベース検証**: LightGBMの発見をif-thenルールに落とし込みバックテスト
3. **特徴量選択**: 上位5-10特徴量のみでモデル再構築（過学習低減）
4. **ターゲット変更**: 8h → 4h / 24h の比較検証
"""),
        mo.md("### 特徴量重要度ランキング (全トークン横断 Top 15)"),
        mo.ui.table(_rank_df.to_pandas()),
        mo.md("### 有望な交互作用パターン"),
        mo.ui.table(_pair_summary_df.to_pandas()) if not _pair_summary_df.is_empty() else mo.md("*該当なし*"),
    ])
    return


if __name__ == "__main__":
    app.run()
