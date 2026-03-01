# marimo ノートブック作成ルール

## セル出力の仕組み

marimo ではセルの**最後の式**が表示出力になる。`return` は変数エクスポート用。

### 正しいパターン

```python
# 表示のみ（変数エクスポートなし）
@app.cell
def my_cell(mo):
    mo.md("## タイトル")
    return

# 変数エクスポートあり
@app.cell
def setup():
    import polars as pl
    x = 42
    return (pl, x)

# 表示 + エクスポート
@app.cell
def my_cell(mo, pl):
    df = pl.DataFrame({"a": [1, 2, 3]})
    mo.ui.table(df.to_pandas())
    return (df,)
```

### 間違いパターン（何も表示されない）

```python
# NG: return で表示オブジェクトを返すと変数エクスポートとして扱われる
@app.cell
def my_cell(mo):
    return mo.vstack([mo.md("hello")])  # 表示されない
```

## 日本語表示設定

matplotlib で日本語を使う場合、セットアップセルに以下を追加。
**注意**: Noto CJK フォントは `.ttc` 形式のため matplotlib の自動検出に載らない。`addfont()` で明示登録が必須。

```python
import matplotlib as mpl
import matplotlib.font_manager as fm

fm.fontManager.addfont("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
mpl.rcParams["font.family"] = "Noto Sans CJK JP"
mpl.rcParams["axes.unicode_minus"] = False
```

利用可能フォント: `Noto Sans CJK JP`, `Noto Serif CJK JP`

## テンプレート構成

```python
import marimo
__generated_with = "0.20.2"
app = marimo.App(width="medium")

@app.cell
def setup():
    from pathlib import Path
    import marimo as mo
    import matplotlib as mpl
    import matplotlib.pyplot as plt
    import polars as pl

    mpl.rcParams["font.family"] = "Noto Sans CJK JP"
    mpl.rcParams["axes.unicode_minus"] = False

    DATA_DIR = Path(__file__).parent.parent.parent / "data"
    # ... データ読み込み ...
    return (DATA_DIR, mo, mpl, plt, pl, ...)

@app.cell
def analysis(mo, pl, plt, ...):
    # 分析ロジック
    mo.vstack([
        mo.md("## セクションタイトル"),
        mo.md("説明テキスト"),
        fig,  # matplotlib figure
        mo.ui.table(df.to_pandas()),
    ])
    return

if __name__ == "__main__":
    app.run()
```

## 表示コンポーネント

- `mo.md()` - Markdownテキスト
- `mo.ui.table(df.to_pandas())` - インタラクティブテーブル（polars→pandas変換必要）
- `mo.vstack([...])` - 縦方向レイアウト
- `mo.hstack([...])` - 横方向レイアウト
- matplotlib の `fig` オブジェクト - そのまま渡せる

## データパス

ノートブックからの相対パス:
```python
DATA_DIR = Path(__file__).parent.parent.parent / "data"
```
（`analyses/<日付ディレクトリ>/analysis_*.py` → `data/` への参照）
