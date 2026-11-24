"""
backtest — 規劃書〈回測共同規則〉的實作

  股票池：每個換股日當下 20 日平均成交值前 300 名（in_universe）
  換股：短線每日、波段每週最後一個交易日、長線每月最後一個交易日；收盤產生訊號、次一交易日開盤成交
  持股：等權重，最多 20 檔；超過時依分數、成交值排序取前 20
  最短持有：買進後至少抱 hold_days 個交易日（預設 = 週期的第一個評估天數：短線 5、波段 20、長線 60），
           期間內即使訊號消失也續抱；突破、黃金交叉這類「只亮一天」的訊號才不會每次換股就被洗掉
  續抱的股票不重新配重，除非偏離目標權重超過 band（預設 20%）
  成本：手續費 0.1425% 雙邊、證交稅 0.3% 賣出、滑價 0.1% 雙邊
  L4：組合裡有勾 L4 時，換股日的持股比例 = l4_scale（1、0.5 或 0.25），其餘放現金
  基準：0050 還原收盤買進持有
  驗證：樣本內 2019–2023、樣本外 2024 起分開報告；多頭／空頭（0050 是否在 200 日線上）分段；
        隨機基準 200 次（同股票池、同持股數、同換股比例、同最短持有）
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FEE, TAX, SLIP = 0.001425, 0.003, 0.001


def rebalance_dates(dates: pd.DatetimeIndex, freq: str) -> pd.DatetimeIndex:
    s = pd.Series(dates, index=dates)
    if freq == "D":
        return dates
    key = dates.to_period("W-FRI") if freq == "W" else dates.to_period("M")
    return pd.DatetimeIndex(s.groupby(key).max().values)


class Book:
    """價格矩陣與模擬器（numpy，讓隨機基準跑得動）"""

    def __init__(self, panel: pd.DataFrame, start=None, end=None):
        o = panel["adj_open"].unstack("stock_id")
        c = panel["adj_close"].unstack("stock_id")
        if start:
            o, c = o[o.index >= pd.Timestamp(start)], c[c.index >= pd.Timestamp(start)]
        if end:
            o, c = o[o.index <= pd.Timestamp(end)], c[c.index <= pd.Timestamp(end)]
        self.dates = c.index
        self.ids = list(c.columns)
        self.col = {s: i for i, s in enumerate(self.ids)}
        cf = c.ffill()
        self.tradable = o.notna().to_numpy() & c.notna().to_numpy()  # 當天有開盤價才可交易
        self.O = o.fillna(cf.shift(1)).fillna(cf).to_numpy()
        self.C = cf.to_numpy()

    def run(self, targets: dict, exposure: dict | None = None, band: float = 0.2, record: bool = True,
            hold_days: int = 0, max_n: int = 20):
        """
        targets：{訊號日: [股票代號, ...]}（已排序、已截到最多檔數）
        exposure：{訊號日: 持股比例}
        """
        T, N = self.C.shape
        sh = np.zeros(N)
        cash = 1.0
        V = np.full(T, np.nan)
        traded = 0.0
        pending = None
        entry = {}  # 股票 → (進場日序, 進場價)
        trades = []
        tdates = set(targets)
        since = {}  # 股票欄位 → 買進的日序（最短持有用）
        for j in range(T):
            if pending is not None:
                names, expo = pending
                px = self.O[j]
                val = cash + np.nansum(sh * np.nan_to_num(px))
                want = np.zeros(N)
                idx = [self.col[s] for s in names if s in self.col and self.tradable[j, self.col[s]]]
                if idx:
                    want[idx] = expo * val / len(idx)
                cur = sh * np.nan_to_num(px)
                # 賣：不在名單，或超出目標太多
                for i in np.nonzero(cur > 0)[0]:
                    if not self.tradable[j, i]:
                        continue  # 停牌賣不掉，留到下次
                    if want[i] == 0 or (cur[i] - want[i]) > band * want[i]:
                        amt = cur[i] - want[i]
                        cash += amt * (1 - FEE - TAX - SLIP)
                        traded += amt
                        sh[i] = want[i] / px[i]
                        if want[i] == 0:
                            since.pop(i, None)
                        if want[i] == 0 and record and i in entry:
                            j0, p0 = entry.pop(i)
                            trades.append((self.dates[j0], self.dates[j], self.ids[i], px[i] / p0 * (1 - FEE - TAX - SLIP) / (1 + FEE + SLIP) - 1, j - j0))
                # 買：新名單，或低於目標太多
                cur = sh * np.nan_to_num(px)
                buys = [(i, want[i] - cur[i]) for i in idx if (cur[i] == 0) or (want[i] - cur[i]) > band * want[i]]
                need = sum(a for _, a in buys) * (1 + FEE + SLIP)
                scale = min(1.0, cash / need) if need > 0 else 0.0
                for i, a in buys:
                    a *= scale
                    if a <= 0:
                        continue
                    cash -= a * (1 + FEE + SLIP)
                    traded += a
                    if sh[i] == 0:
                        since[i] = j
                        if record:
                            entry[i] = (j, px[i])
                    sh[i] += a / px[i]
                pending = None
            V[j] = cash + np.nansum(sh * np.nan_to_num(self.C[j]))
            d = self.dates[j]
            if d in tdates and j + 1 < T:
                names = list(targets[d])
                if hold_days:
                    keep = [self.ids[i] for i, j0 in since.items() if sh[i] > 0 and (j + 1 - j0) < hold_days]
                    names = keep + [x for x in names if x not in keep]
                pending = (names[:max_n], (exposure or {}).get(d, 1.0))
        ret = pd.Series(V, self.dates).pct_change().fillna(0.0)
        tr = pd.DataFrame(trades, columns=["entry", "exit", "stock_id", "ret", "days"]) if record else None
        years = max(len(self.dates) / 252, 1e-9)
        return ret, tr, traded / np.nanmean(V) / years / 2  # 年換手率（單邊）


def pick_targets(hit: pd.Series, score: pd.Series, panel: pd.DataFrame, rdates, max_n=20) -> dict:
    df = pd.DataFrame({"hit": hit, "score": score, "tr": panel["turnover_rank"], "u": panel["in_universe"]})
    df = df[(df["hit"]) & (df["u"] == 1)]
    out = {}
    rset = set(rdates)
    for d, g in df.groupby(level="date"):
        if d in rset:
            g = g.sort_values(["score", "tr"], ascending=[False, True])
            out[d] = list(g.index.get_level_values("stock_id")[:max_n])
    for d in rdates:
        out.setdefault(d, [])
    return out


def metrics(ret: pd.Series, bench: pd.Series | None = None) -> dict:
    ret = ret.dropna()
    if len(ret) < 20:
        return {}
    eq = (1 + ret).cumprod()
    yrs = len(ret) / 252
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    sd = ret.std()
    m = {"年化報酬": cagr, "最大回撤": dd, "夏普": ret.mean() / sd * np.sqrt(252) if sd > 0 else np.nan}
    if bench is not None:
        b = bench.reindex(ret.index).fillna(0)
        beq = (1 + b).cumprod()
        m["基準年化"] = beq.iloc[-1] ** (1 / yrs) - 1  # 基準是 market["bench"]，名稱見 run_backtest 輸出的「基準名稱」
        m["超額年化"] = cagr - m["基準年化"]
    return m


def run_backtest(panel: pd.DataFrame, hit: pd.Series, score: pd.Series, period: dict, market: pd.DataFrame,
                 overlay: bool = False, start="2019-01-01", end=None, max_n=20, n_random=200,
                 split="2024-01-01", seed=7, hold_days: int | None = None) -> dict:
    hold = period["eval_days"][0] if hold_days is None else hold_days
    book = Book(panel, start, end)
    rd = rebalance_dates(book.dates, period["rebalance"])
    tg = pick_targets(hit, score, panel, rd, max_n)
    expo = None
    if overlay and "l4_scale" in market:
        expo = market["l4_scale"].reindex(rd).fillna(1.0).to_dict()
    ret, trades, turnover = book.run(tg, expo, hold_days=hold, max_n=max_n)
    bench = market["bench"].pct_change().reindex(book.dates)
    out = {"基準名稱": market.attrs.get("bench_name", "未標示（請在 market.attrs['bench_name'] 註明）"), "full": metrics(ret, bench)}
    out["樣本內 2019–2023"] = metrics(ret[ret.index < split], bench)
    out["樣本外 2024 起"] = metrics(ret[ret.index >= split], bench)
    reg = market["market_uptrend"].shift(1).reindex(book.dates)
    for name, mask in [("多頭段", reg == 1), ("空頭段", reg == 0)]:
        r = ret[mask]
        out[name] = {"年化（日均×252）": r.mean() * 252 if len(r) else np.nan,
                     "0050 同期": bench[mask].mean() * 252 if len(r) else np.nan, "天數": int(mask.sum())}
    if trades is not None and len(trades):
        out["交易"] = {"筆數": len(trades), "勝率": float((trades["ret"] > 0).mean()),
                     "平均持有天數": float(trades["days"].mean()), "年換手率": float(turnover)}
    out["平均訊號檔數"] = float(np.mean([min(len(v), max_n) for v in tg.values()]))
    out["最短持有天數"] = hold
    # 隨機基準：同股票池、同持股數
    if n_random:
        rng = np.random.default_rng(seed)
        uni = panel["in_universe"].unstack("stock_id").reindex(book.dates)
        pools = {d: list(uni.columns[(uni.loc[d] == 1).to_numpy()]) for d in rd if d in uni.index}
        sharpe = []
        rds = sorted(tg)
        for _ in range(n_random):
            rt, prev_t, prev_r = {}, [], []
            for d in rds:
                cur = tg[d]
                pool = pools.get(d, [])
                k = len(set(cur) & set(prev_t))  # 策略這次續留幾檔 → 隨機組合也續留幾檔
                stay = [x for x in prev_r if x in set(pool)]
                stay = list(rng.choice(stay, size=min(k, len(stay)), replace=False)) if stay and k else []
                cand = [x for x in pool if x not in set(stay)]
                m = min(len(cur) - len(stay), len(cand))
                new = list(rng.choice(cand, size=m, replace=False)) if m > 0 else []
                rt[d] = stay + new
                prev_t, prev_r = cur, rt[d]
            r, _, _ = book.run(rt, expo, record=False, hold_days=hold, max_n=max_n)
            sharpe.append(metrics(r).get("夏普", np.nan))
        s = out["full"].get("夏普", np.nan)
        arr = np.array(sharpe)
        pct = float(np.mean(arr[~np.isnan(arr)] < s)) if np.isfinite(s) else np.nan
        out["_random_sharpes"] = [float(x) for x in arr]
        out["隨機基準"] = {"次數": n_random, "種子": seed, "隨機夏普中位數": float(np.nanmedian(arr)),
                        "贏過隨機比例": pct, "判讀": "與運氣難以區分" if pct < 0.7 else "優於多數隨機組合"}
    out["_returns"] = ret
    out["_trades"] = trades
    out["_targets"] = tg
    return out


def signal_stats(hit: pd.Series, panel: pd.DataFrame, days=(5, 10, 20)) -> pd.DataFrame:
    """
    策略體檢卡用：「過去同類訊號後 N 日的報酬分布」。
    訊號日收盤後，次日開盤買進，持有 N 個交易日到收盤；與同日股票池平均相比。
    """
    o = panel["adj_open"].unstack("stock_id")
    c = panel["adj_close"].unstack("stock_id")
    u = panel["in_universe"].unstack("stock_id") == 1
    h = hit.unstack("stock_id").reindex_like(c).fillna(False).astype(bool) & u
    rows = []
    for n in days:
        fwd = c.shift(-n) / o.shift(-1) - 1
        ex = fwd.sub(fwd.where(u).mean(axis=1), axis=0)
        v, e = fwd.where(h).stack().dropna(), ex.where(h).stack().dropna()
        if not len(v):
            continue
        rows.append({"天數": n, "樣本數": len(v), "平均報酬": v.mean(), "中位數": v.median(), "勝率": (v > 0).mean(),
                     "5% 分位": v.quantile(0.05), "95% 分位": v.quantile(0.95), "對股票池平均的超額": e.mean()})
    return pd.DataFrame(rows)


def ablation(evaluator, node: dict, panel: pd.DataFrame, period: dict, market: pd.DataFrame,
             overlay: bool = False, start="2019-01-01", end=None, hold_days: int | None = None) -> pd.DataFrame:
    """
    過度擬合檢查：逐一拿掉一個條件重跑回測（不跑隨機基準），看績效變多少。
    拿掉某條件後夏普反而上升或幾乎不變，代表那個條件可能只是在湊歷史。
    """
    from .engine import ablation_variants

    def one(n):
        h, s, _ = evaluator.eval(n)
        r = run_backtest(panel, h, s, period, market, overlay=overlay, start=start, end=end,
                         n_random=0, hold_days=hold_days)
        f = r.get("full", {})
        return f.get("夏普", np.nan), f.get("年化報酬", np.nan), r["平均訊號檔數"]

    base = one(node)
    rows = [{"拿掉的條件": "（全部條件）", "夏普": base[0], "年化報酬": base[1], "平均訊號檔數": base[2],
             "夏普變化": 0.0, "年化報酬變化": 0.0}]
    for lab, v in ablation_variants(node, evaluator.lib):
        s, c, n = one(v)
        rows.append({"拿掉的條件": lab, "夏普": s, "年化報酬": c, "平均訊號檔數": n,
                     "夏普變化": s - base[0], "年化報酬變化": c - base[1]})
    return pd.DataFrame(rows)
