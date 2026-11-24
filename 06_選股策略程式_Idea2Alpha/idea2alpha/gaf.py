"""
gaf — L3 線型影像辨識（實驗性），沿用實驗室 CVGIP 論文的 Gramian Angular Field 做法

  python -m idea2alpha.cli gaf --period swing

步驟：
  1. 每檔股票、每週最後一個交易日，取近 60 根還原收盤 → 縮放到 [-1, 1] → GASF 60×60 影像
  2. 以 PAA 縮成 20×20，取上三角 210 維當特徵（論文用 CNN；這裡用邏輯迴歸當起點，學生可換成 CNN）
  3. 標籤：次日開盤買進持有 20 日的報酬是否高於同日股票池中位數
  4. 只用 train_end 以前（再往前留 30 個交易日空窗，避免標籤用到 train_end 之後的價格）訓練
  5. 對所有日期算上升機率，再在股票池內排百分位 → gaf_up_pct（L3 取 ≥ 0.8）

train_end 之前的分數是樣本內，回測時只能看 train_end 之後那段。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def gasf(x: np.ndarray, size: int = 20) -> np.ndarray:
    lo, hi = x.min(), x.max()
    if hi - lo <= 0:
        return np.zeros(size * (size + 1) // 2)
    s = (x - lo) / (hi - lo) * 2 - 1
    k = len(s) // size
    s = s[: k * size].reshape(size, k).mean(axis=1)  # PAA
    phi = np.arccos(np.clip(s, -1, 1))
    g = np.cos(phi[:, None] + phi[None, :])
    return g[np.triu_indices(size)]


def build_dataset(panel: pd.DataFrame, window=60, horizon=20):
    c = panel["adj_close"].unstack("stock_id")
    o = panel["adj_open"].unstack("stock_id")
    u = panel["in_universe"].unstack("stock_id") == 1
    fwd = c.shift(-horizon) / o.shift(-1) - 1
    rel = fwd.sub(fwd.where(u).median(axis=1), axis=0)
    wk = pd.Series(c.index, index=c.index).groupby(c.index.to_period("W-FRI")).max().values
    X, y, keys = [], [], []
    arr = c.to_numpy()
    pos = {d: i for i, d in enumerate(c.index)}
    for d in wk:
        i = pos[d]
        if i < window:
            continue
        for j, sid in enumerate(c.columns):
            if not u.iat[i, j]:
                continue
            w = arr[i - window + 1: i + 1, j]
            if np.isnan(w).any():
                continue
            X.append(gasf(w))
            y.append(rel.iat[i, j])
            keys.append((d, sid))
    return np.array(X), np.array(y, dtype=float), pd.MultiIndex.from_tuples(keys, names=["date", "stock_id"])


def train_and_score(panel: pd.DataFrame, period: str, train_end="2023-12-31"):
    from sklearn.linear_model import LogisticRegression

    X, y, keys = build_dataset(panel)
    dates = keys.get_level_values("date")
    alld = panel.index.get_level_values("date").unique().sort_values()
    cut = alld[max(0, alld.searchsorted(pd.Timestamp(train_end)) - 30)]
    tr = (dates <= cut) & ~np.isnan(y)
    if tr.sum() < 200:
        raise RuntimeError("訓練樣本太少（需要更長的歷史或更多股票）")
    m = LogisticRegression(max_iter=2000, C=0.1)
    m.fit(X[tr], (y[tr] > 0).astype(int))
    p = pd.Series(m.predict_proba(X)[:, 1], index=keys, name="gaf_prob")
    df = p.to_frame()
    df["gaf_up_pct"] = df.groupby(level="date")["gaf_prob"].rank(pct=True)
    df["in_sample"] = dates <= pd.Timestamp(train_end)
    # 週資料鋪到每日（只往後填，週間沿用上一週的分數）
    full = panel.index
    out = df[["gaf_up_pct", "in_sample"]].reindex(full)
    out = out.groupby(level="stock_id").ffill()
    f = ROOT / "output" / f"gaf_scores_{period}.parquet"
    f.parent.mkdir(exist_ok=True)
    out.to_parquet(f)
    print(f"已寫入 {f}：訓練 {int(tr.sum())} 筆，全部 {len(y)} 筆")
    return out
