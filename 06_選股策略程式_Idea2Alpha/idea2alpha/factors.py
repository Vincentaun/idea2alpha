"""
factors — 由原始資料算出每天、每檔股票的因子

原則（偷看未來檢查）：
  1. 第 t 天的因子只用第 t 天收盤後已公布的資料。
  2. 月營收以公告日（create_time，沒有就用次月 10 日）起才可用。
  3. 季報依法定期限才可用：Q1→5/15、Q2→8/14、Q3→11/14、Q4→隔年 3/31。
  4. 股利以董事會公告日（AnnouncementDate）起才可用。
  5. 價格用還原權值（除權息、分割）；指標都是比值，向後還原不影響過去的訊號。
  6. 週K 指標只在該週最後一個交易日更新，週間沿用上一根完成的週K。
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 基本工具
# ---------------------------------------------------------------------------


def _streak(cond: pd.Series) -> pd.Series:
    """連續成立天數"""
    c = cond.fillna(False).astype(int)
    grp = (c == 0).cumsum()
    return c.groupby(grp).cumsum()


def _rsi(c: pd.Series, n: int) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _kd(h, l, c, n=9):
    """台灣常用 KD：K = 2/3 K昨 + 1/3 RSV，D = 2/3 D昨 + 1/3 K，起始 50"""
    rsv = (c - l.rolling(n).min()) / (h.rolling(n).max() - l.rolling(n).min()).replace(0, np.nan) * 100
    k = np.full(len(c), np.nan)
    d = np.full(len(c), np.nan)
    pk = pd_ = 50.0
    for i, r in enumerate(rsv.to_numpy()):
        if np.isnan(r):
            continue
        pk = 2 / 3 * pk + r / 3
        pd_ = 2 / 3 * pd_ + pk / 3
        k[i], d[i] = pk, pd_
    return pd.Series(k, c.index), pd.Series(d, c.index)


def adjust_prices(px: pd.DataFrame, events: pd.DataFrame | None) -> pd.DataFrame:
    """
    px：TaiwanStockPrice（date, open, max, min, close, Trading_Volume, Trading_money）
    events：除權息結果＋分割（date, before_price, after_price）
    回傳加上 adj_open/adj_high/adj_low/adj_close 的表（向後還原：最新價格不變）
    """
    px = px.sort_values("date").drop_duplicates("date").set_index("date")
    px = px[(px["close"] > 0) & (px["open"] > 0)]
    f = pd.Series(1.0, index=px.index)
    if events is not None and len(events):
        ev = events.dropna(subset=["before_price", "after_price"])
        ev = ev[(ev["before_price"] > 0) & (ev["after_price"] > 0)]
        for _, e in ev.iterrows():
            r = e["after_price"] / e["before_price"]
            f[f.index < e["date"]] *= r
    out = px.copy()
    for a, b in [("open", "adj_open"), ("max", "adj_high"), ("min", "adj_low"), ("close", "adj_close")]:
        out[b] = px[a] * f
    out["volume"] = px["Trading_Volume"] / 1000.0  # 張
    out["money"] = px["Trading_money"]
    return out


# ---------------------------------------------------------------------------
# 技術面（依週期設定）
# ---------------------------------------------------------------------------


def _weekly(d: pd.DataFrame) -> pd.DataFrame:
    wk = d.index.to_period("W-FRI")
    g = d.groupby(wk)
    w = pd.DataFrame({
        "adj_open": g["adj_open"].first(), "adj_high": g["adj_high"].max(),
        "adj_low": g["adj_low"].min(), "adj_close": g["adj_close"].last(), "volume": g["volume"].sum(),
    })
    w.index = g.apply(lambda x: x.index.max())  # 用該週最後一個交易日當索引
    return w


def technical(d: pd.DataFrame, p: dict) -> pd.DataFrame:
    """p：periods.json 裡的一組週期設定"""
    bars = _weekly(d) if p["bar"] == "W" else d
    h, l, c, v = bars["adj_high"], bars["adj_low"], bars["adj_close"], bars["volume"]
    s, m, L = p["ma"]
    rise = 4 if p["bar"] == "W" else 20
    t = pd.DataFrame(index=bars.index)
    t["ma_s"], t["ma_m"], t["ma_l"] = c.rolling(s).mean(), c.rolling(m).mean(), c.rolling(L).mean()
    t["ma_l_rising"] = (t["ma_l"] > t["ma_l"].shift(rise)).astype(float).where(t["ma_l"].shift(rise).notna())
    t["close_p"] = c  # 與 ma_* 比較用（週K 時是週收盤）
    k, dd = _kd(h, l, c, p["kd"])
    t["k"], t["d"] = k, dd
    t["kd_cross_up_low"] = ((k > dd) & (k.shift(1) <= dd.shift(1)) & (k.shift(1) < 20)).astype(float).where(k.shift(1).notna())
    t["rsi"] = _rsi(c, p["rsi"])
    n = p["willr"]
    hh, ll = h.rolling(n).max(), l.rolling(n).min()
    wr = (hh - c) / (hh - ll).replace(0, np.nan) * -100
    t["willr"] = wr
    t["willr_cross_up_80"] = ((wr.shift(1) < -80) & (wr >= -80)).astype(float).where(wr.shift(1).notna())
    f, sl, sg = p["macd"]
    dif = c.ewm(span=f, adjust=False).mean() - c.ewm(span=sl, adjust=False).mean()
    dea = dif.ewm(span=sg, adjust=False).mean()
    hist = dif - dea
    t["macd_hist"] = hist
    t["macd_turn_pos"] = ((hist > 0) & (hist.shift(1) <= 0)).astype(float)
    t["dif_abs_pct"] = (dif.abs() / c * 100)
    bn, bk = p["bb"]
    mid, sd = c.rolling(bn).mean(), c.rolling(bn).std()
    bw = 2 * bk * sd / mid
    t["bb_break_up"] = (c > mid + bk * sd).astype(float).where(sd.notna())
    t["bb_width_rising"] = (bw > bw.shift(1)).astype(float).where(bw.shift(1).notna())
    t["bb_squeeze_recent"] = (bw.rolling(20).min() <= bw.rolling(120, min_periods=60).quantile(0.2)).astype(float).where(bw.notna())
    # 跳過暖機期不足的列
    warm = max(L, sl + sg, 60)
    t.iloc[:warm] = np.nan
    if p["bar"] == "W":
        t = t.reindex(d.index).ffill()
    return t


def fixed_technical(d: pd.DataFrame) -> pd.DataFrame:
    """不隨週期變動的價量因子（日K、以交易日計）"""
    h, l, c, v = d["adj_high"], d["adj_low"], d["adj_close"], d["volume"]
    r = c.pct_change()
    t = pd.DataFrame(index=d.index)
    t["close"] = d["close"]
    t["ma50"], t["ma150"], t["ma200"] = c.rolling(50).mean(), c.rolling(150).mean(), c.rolling(200).mean()
    t["adj_close"] = c
    t["ma200_rising"] = (t["ma200"] > t["ma200"].shift(20)).astype(float).where(t["ma200"].shift(20).notna())
    hi252 = h.rolling(252, min_periods=240).max()
    t["high_52w_dist"] = (c / hi252 - 1) * 100
    t["vol_ratio20"] = v / v.rolling(20).mean().shift(1)
    t["mom_12_1"] = (c.shift(21) / c.shift(252) - 1) * 100
    t["rs_raw"] = 0.4 * c.pct_change(63) + 0.2 * c.pct_change(126) + 0.2 * c.pct_change(189) + 0.2 * c.pct_change(252)
    t["vol_1y"] = r.rolling(250, min_periods=200).std() * np.sqrt(250) * 100
    t["var5"] = r.rolling(250, min_periods=200).quantile(0.05) * 100
    t["above_low20"] = (c >= l.rolling(20).min().shift(1)).astype(float)
    # VCP：最近 60 日切三段，振幅逐段收斂且最後一段 < 10%
    def seg(a, b):
        hh = h.shift(a).rolling(b - a).max()
        ll = l.shift(a).rolling(b - a).min()
        return (hh / ll - 1) * 100
    r1, r2, r3 = seg(40, 60), seg(20, 40), seg(0, 20)
    t["range_last20"] = r3
    t["vcp_contracting"] = ((r1 > r2) & (r2 > r3) & (r3 < 10)).astype(float).where(r1.notna())
    # 達瓦斯箱型：前 20 日箱頂接近 52 週高點、箱高 ≤ 15%，今天收盤突破箱頂
    top = h.rolling(20).max().shift(1)
    bot = l.rolling(20).min().shift(1)
    prior_hi = h.shift(1).rolling(252, min_periods=240).max()
    t["box_height"] = (top / bot - 1) * 100
    t["darvas_break"] = ((c > top) & (top >= 0.97 * prior_hi) & (t["box_height"] <= 15)).astype(float).where(prior_hi.notna())
    t["turnover20"] = d["money"].rolling(20).mean()
    t = t.join(event_factors(c, v))
    return t


def event_factors(c: pd.Series, v: pd.Series) -> pd.DataFrame:
    """
    「事件」與「狀態」要分開（翻譯員最常混淆的地方）：
      狀態：收盤在 MA20 之上（above_ma20）——今天成立，昨天可能也成立
      事件：剛突破 MA20（cross_up_ma20）——昨天收盤 ≤ 昨天 MA20，且今天收盤 > 今天 MA20
    """
    t = pd.DataFrame(index=c.index)
    ma20 = c.rolling(20).mean()
    t["ma20"] = ma20
    above = c > ma20
    t["above_ma20"] = above.astype(float).where(ma20.notna())
    prev_ok = ma20.shift(1).notna()
    t["cross_up_ma20"] = (above & (c.shift(1) <= ma20.shift(1))).astype(float).where(prev_ok)
    t["cross_down_ma20"] = ((c < ma20) & (c.shift(1) >= ma20.shift(1))).astype(float).where(prev_ok)
    t["days_above_ma20"] = _streak(above).astype(float).where(ma20.notna())
    t["ret_1d"] = c.pct_change() * 100
    t["ret_5d"] = c.pct_change(5) * 100
    t["vol5_ratio20"] = v.rolling(5).mean() / v.rolling(20).mean().shift(5)
    return t


# ---------------------------------------------------------------------------
# 籌碼面
# ---------------------------------------------------------------------------


def chips(idx: pd.DatetimeIndex, inst: pd.DataFrame | None, margin: pd.DataFrame | None, volume: pd.Series) -> pd.DataFrame:
    t = pd.DataFrame(index=idx)
    if inst is not None and len(inst):
        x = inst.assign(net=(inst["buy"] - inst["sell"]) / 1000.0)
        pv = x.pivot_table(index="date", columns="name", values="net", aggfunc="sum").reindex(idx)
        g = lambda *ks: sum(pv[k].fillna(0) for k in ks if k in pv) if any(k in pv for k in ks) else pd.Series(np.nan, idx)
        fn = g("Foreign_Investor", "Foreign_Dealer_Self")
        tn = g("Investment_Trust")
        has = pv.notna().any(axis=1)
        fn, tn = fn.where(has), tn.where(has)
        t["foreign_net"], t["trust_net"] = fn, tn
        fs, ts = _streak(fn > 0), _streak(tn > 0)
        t["foreign_buy_days"], t["trust_buy_days"] = fs.where(has), ts.where(has)
        t["inst_net_buy_days"] = np.maximum(fs, ts).where(has)
        t["trust_buy_ratio_3d"] = tn.rolling(3).sum() / volume.rolling(3).sum() * 100
        turn = lambda s: (s > 0) & (s.shift(1).rolling(5).sum() <= 0)
        t["inst_turn_buy"] = (turn(fn) | turn(tn)).astype(float).where(has)
        t["inst_net_20d"] = (fn + tn).rolling(20).sum()
    if margin is not None and len(margin):
        mb = margin.set_index("date")["MarginPurchaseTodayBalance"].reindex(idx)
        t["margin_bal"] = mb
        t["margin_chg_10d"] = (mb / mb.shift(10) - 1) * 100
    return t


def holders(idx: pd.DatetimeIndex, h: pd.DataFrame | None) -> pd.DataFrame:
    """集保股權分散（付費資料）：400 張以上大戶比例、股東人數；資料日當天收盤後可用"""
    t = pd.DataFrame(index=idx)
    if h is None or not len(h):
        return t
    lv = h["HoldingSharesLevel"].astype(str)
    lo = lv.str.replace(",", "").str.extract(r"^(\d+)")[0].astype(float)
    big = h[lo >= 400001].groupby("date")["percent"].sum()
    is_tot = lv.str.contains("total|合計", case=False)
    tot = h[is_tot].groupby("date")["people"].sum() if is_tot.any() else h[lo.notna()].groupby("date")["people"].sum()
    w = pd.DataFrame({"big_pct": big, "holders": tot}).sort_index()
    w["big_pct_up_2w"] = ((w["big_pct"] > w["big_pct"].shift(1)) & (w["big_pct"].shift(1) > w["big_pct"].shift(2))).astype(float)
    w["holders_down"] = (w["holders"] < w["holders"].shift(1)).astype(float)
    return w.reindex(idx, method="ffill")


# ---------------------------------------------------------------------------
# 基本面
# ---------------------------------------------------------------------------


def _q_avail(q: pd.Timestamp) -> pd.Timestamp:
    y, m = q.year, q.month
    return {3: pd.Timestamp(y, 5, 15), 6: pd.Timestamp(y, 8, 14), 9: pd.Timestamp(y, 11, 14),
            12: pd.Timestamp(y + 1, 3, 31)}.get(m, q + pd.Timedelta(days=75))


def _asof(idx: pd.DatetimeIndex, ev: pd.DataFrame, cols) -> pd.DataFrame:
    """ev 需要 avail 欄；把每個事件在 avail 當天起的值鋪到每日"""
    ev = ev.dropna(subset=["avail"]).sort_values("avail").drop_duplicates("avail", keep="last")
    left = pd.DataFrame({"date": idx})
    m = pd.merge_asof(left, ev[["avail"] + cols].rename(columns={"avail": "date"}), on="date")
    return m.set_index("date")[cols]


def month_revenue(idx, mr: pd.DataFrame | None) -> pd.DataFrame:
    cols = ["rev_m", "rev_yoy_m", "rev_yoy_3m", "rev_new_high_12m"]
    if mr is None or not len(mr):
        return pd.DataFrame(index=idx, columns=cols, dtype=float)
    x = mr.copy()
    x["ym"] = pd.PeriodIndex.from_fields(year=x["revenue_year"].astype(int), month=x["revenue_month"].astype(int), freq="M")
    x = x.sort_values("ym").drop_duplicates("ym", keep="last").set_index("ym")
    full = pd.period_range(x.index.min(), x.index.max(), freq="M")
    rev = x["revenue"].reindex(full)
    q = pd.DataFrame(index=full)
    q["rev_m"] = rev
    q["rev_yoy_m"] = (rev / rev.shift(12) - 1) * 100
    s3 = rev.rolling(3).sum()
    q["rev_yoy_3m"] = (s3 / s3.shift(12) - 1) * 100
    q["rev_new_high_12m"] = (rev >= rev.rolling(12).max()).astype(float).where(rev.rolling(12).count() == 12)
    ct = pd.to_datetime(x["create_time"], errors="coerce") if "create_time" in x else pd.Series(pd.NaT, x.index)
    default = (x.index + 1).to_timestamp() + pd.Timedelta(days=9)  # 次月 10 日
    av = ct.fillna(pd.Series(default, index=x.index)).reindex(full)
    q["avail"] = av.fillna(pd.Series((full + 1).to_timestamp() + pd.Timedelta(days=9), index=full))
    return _asof(idx, q.reset_index(drop=True), cols)


def _pivot(df, types):
    if df is None or not len(df):
        return pd.DataFrame(columns=types)
    p = df[df["type"].isin(types)].pivot_table(index="date", columns="type", values="value", aggfunc="last")
    return p.reindex(columns=types)


def quarterly(fs, bs, cf) -> pd.DataFrame:
    """回傳每季一列的財務指標（含 avail 可用日）"""
    a = _pivot(fs, ["EPS", "IncomeAfterTaxes", "Revenue", "OperatingIncome", "GrossProfit"])
    b = _pivot(bs, ["TotalAssets", "Liabilities", "CurrentAssets", "CurrentLiabilities", "Equity",
                    "CashAndCashEquivalents", "PropertyPlantAndEquipment", "LongtermBorrowings", "BondsPayable",
                    "ShorttermBorrowings", "OrdinaryShare"])
    c = _pivot(cf, ["CashFlowsFromOperatingActivities", "PropertyAndPlantAndEquipment"])
    if not len(a):
        return pd.DataFrame()
    q = a.join(b, how="outer").join(c, how="outer").sort_index()
    q = q[q.index.month.isin([3, 6, 9, 12])]
    # 現金流量表是年初累計 → 拆成單季
    for col, new in [("CashFlowsFromOperatingActivities", "cfo_q"), ("PropertyAndPlantAndEquipment", "capex_q")]:
        s = q[col]
        prev = s.groupby(q.index.year).shift(1)
        q[new] = np.where(q.index.month == 3, s, s - prev)
    out = pd.DataFrame(index=q.index)
    out["eps_q"] = q["EPS"]
    ttm = lambda s: s.rolling(4, min_periods=4).sum()
    out["eps_ttm"] = ttm(q["EPS"])
    ni, rev, oi, gp = ttm(q["IncomeAfterTaxes"]), ttm(q["Revenue"]), ttm(q["OperatingIncome"]), ttm(q["GrossProfit"])
    cfo, capex = ttm(q["cfo_q"]), ttm(q["capex_q"])
    ta, eq = q["TotalAssets"], q["Equity"]
    out["roe_ttm"] = ni / ((eq + eq.shift(4)) / 2) * 100
    out["roa_ttm"] = ni / ((ta + ta.shift(4)) / 2) * 100
    base = q["EPS"].shift(4)
    out["eps_yoy_q"] = ((q["EPS"] - base) / base.abs() * 100).where(base > 0)
    e0, e3 = out["eps_ttm"], out["eps_ttm"].shift(12)
    out["eps_cagr_3y"] = (((e0 / e3) ** (1 / 3) - 1) * 100).where((e0 > 0) & (e3 > 0))
    out["current_ratio"] = q["CurrentAssets"] / q["CurrentLiabilities"]
    out["debt_ratio"] = q["Liabilities"] / ta * 100
    out["fcf_ttm"] = cfo + capex
    # 年度（Q4 列）→ 近 5 年最小值
    q4 = out.index.month == 12
    roe_y = (ni / eq * 100).where(q4)
    eps_y = out["eps_ttm"].where(q4)
    out["roe_min_5y"] = roe_y.dropna().rolling(5).min().reindex(out.index).ffill()
    out["eps_min_5y"] = eps_y.dropna().rolling(5).min().reindex(out.index).ffill()
    out["eps_annual"] = eps_y.ffill()
    out["eps_year"] = pd.Series(np.where(q4, out.index.year, np.nan), out.index).ffill()
    # Piotroski F-Score（TTM 對一年前）
    ltd = q[["LongtermBorrowings", "BondsPayable"]].fillna(0).sum(axis=1)
    lev = ltd / ta
    cr = out["current_ratio"]
    shares = q["OrdinaryShare"] / 10.0
    gm, at = gp / rev, rev / ta
    roa = out["roa_ttm"]
    items = [roa > 0, cfo > 0, roa > roa.shift(4), cfo > ni, lev <= lev.shift(4), cr > cr.shift(4),
             shares <= shares.shift(4) * 1.001, gm > gm.shift(4), at > at.shift(4)]
    ok = roa.shift(4).notna() & cfo.notna() & gm.shift(4).notna()
    out["fscore"] = sum(i.astype(float) for i in items).where(ok)
    # 神奇公式用
    out["ebit_ttm"] = oi
    nwc = (q["CurrentAssets"] - q["CashAndCashEquivalents"].fillna(0) - q["CurrentLiabilities"]).clip(lower=0)
    out["roc"] = oi / (nwc + q["PropertyPlantAndEquipment"].fillna(0)).replace(0, np.nan)
    out["net_debt"] = ltd + q["ShorttermBorrowings"].fillna(0) - q["CashAndCashEquivalents"].fillna(0)
    out["shares"] = shares
    out["avail"] = [_q_avail(d) for d in out.index]
    return out


QCOLS = ["eps_q", "eps_ttm", "roe_ttm", "roa_ttm", "eps_yoy_q", "eps_cagr_3y", "current_ratio", "debt_ratio",
         "fcf_ttm", "roe_min_5y", "eps_min_5y", "fscore", "ebit_ttm", "roc", "net_debt", "shares"]


def dividends(idx, dv: pd.DataFrame | None, qt: pd.DataFrame) -> pd.DataFrame:
    """連續配息年數、近 5 年配發率變異係數"""
    cols = ["dividend_years", "payout_cv_5y"]
    if dv is None or not len(dv):
        return pd.DataFrame(index=idx, columns=cols, dtype=float)
    x = dv.copy()
    x["fy"] = x["year"].astype(str).str.extract(r"(\d+)\s*年")[0].astype(float) + 1911
    x["cash"] = x[["CashEarningsDistribution", "CashStatutorySurplus"]].fillna(0).sum(axis=1)
    x["avail"] = pd.to_datetime(x.get("AnnouncementDate"), errors="coerce").fillna(x["date"])
    x = x.dropna(subset=["fy"]).sort_values("avail")
    # 年度 EPS 與其可用日
    eps_y = {}
    if len(qt):
        for d, r in qt[qt.index.month == 12].iterrows():
            eps_y[d.year] = (r["eps_ttm"], _q_avail(d))
    rows = []
    for av in x["avail"].drop_duplicates():
        known = x[x["avail"] <= av]
        by = known.groupby("fy")["cash"].sum()
        by = by[by > 0]
        if not len(by):
            rows.append((av, 0.0, np.nan))
            continue
        last = int(by.index.max())
        n = 0
        while (last - n) in by.index:
            n += 1
        pr = []
        for y in range(last - 4, last + 1):
            if y in by.index and y in eps_y and eps_y[y][1] <= av and eps_y[y][0] > 0:
                pr.append(by[y] / eps_y[y][0])
        cv = np.std(pr) / np.mean(pr) if len(pr) >= 3 and np.mean(pr) > 0 else np.nan
        rows.append((av, float(n), cv))
    ev = pd.DataFrame(rows, columns=["avail"] + cols)
    return _asof(idx, ev, cols)


# ---------------------------------------------------------------------------
# 組合：單一股票的完整因子表
# ---------------------------------------------------------------------------


def stock_frame(raw: dict, p: dict, financial: bool = False) -> pd.DataFrame:
    """
    raw：{"price","events","per","inst","margin","mrev","fs","bs","cf","div","holders"} 各為 DataFrame 或 None
    p：週期設定
    """
    d = adjust_prices(raw["price"], raw.get("events"))
    idx = d.index
    parts = [d[["open", "close", "adj_open", "adj_close", "volume", "money"]].rename(columns={"close": "close_raw"}),
             fixed_technical(d).drop(columns=["close", "adj_close"]), technical(d, p)]
    per = raw.get("per")
    if per is not None and len(per):
        v = per.set_index("date")[["PER", "PBR", "dividend_yield"]].reindex(idx)
        v = v.mask(v <= 0)  # 虧損時 PER 為 0 → 視為缺值
        v["dividend_yield"] = per.set_index("date")["dividend_yield"].reindex(idx)
        v["per_x_pbr"] = v["PER"] * v["PBR"]
        parts.append(v)
    parts.append(chips(idx, raw.get("inst"), raw.get("margin"), d["volume"]))
    parts.append(month_revenue(idx, raw.get("mrev")))
    qt = quarterly(raw.get("fs"), raw.get("bs"), raw.get("cf"))
    if len(qt):
        qd = _asof(idx, qt.reset_index(drop=True), QCOLS)
        parts.append(qd)
    parts.append(dividends(idx, raw.get("div"), qt if len(qt) else pd.DataFrame()))
    parts.append(holders(idx, raw.get("holders")))
    f = pd.concat(parts, axis=1)
    f = f.loc[:, ~f.columns.duplicated()]
    if "shares" in f:
        f["mktcap"] = f["close_raw"] * f["shares"]
        f["peg"] = (f["PER"] / f["eps_cagr_3y"]).where(f["eps_cagr_3y"] > 0) if "PER" in f else np.nan
        ev = f["mktcap"] + f["net_debt"]
        f["earnings_yield"] = (f["ebit_ttm"] / ev).where(ev > 0)
        f["profitable_5y"] = (f["eps_min_5y"] > 0).astype(float).where(f["eps_min_5y"].notna())
    f["is_financial"] = float(financial)
    return f


# ---------------------------------------------------------------------------
# 橫斷面因子（每天在股票池內排名）
# ---------------------------------------------------------------------------


def cross_section(panel: pd.DataFrame, universe_n: int = 300) -> pd.DataFrame:
    """panel：MultiIndex (date, stock_id)。加上排名類因子與 in_universe"""
    g = panel.groupby(level="date")
    panel["turnover_rank"] = g["turnover20"].rank(ascending=False, method="first")
    panel["in_universe"] = (panel["turnover_rank"] <= universe_n).astype(float)
    u = panel[panel["in_universe"] == 1]
    gu = u.groupby(level="date")
    pct = lambda col, asc=True: gu[col].rank(pct=True, ascending=asc)
    new = pd.DataFrame(index=u.index)
    new["mom_12_1_pct"] = pct("mom_12_1")
    new["rs_pct"] = pct("rs_raw")
    new["vol_1y_pct"] = pct("vol_1y")
    new["var5_rank"] = pct("var5", asc=False)  # 1 = 下檔風險最高
    if "PBR" in u:
        new["pbr_pct"] = pct("PBR")
    if "earnings_yield" in u and "roc" in u:
        ok = (u["is_financial"] == 0) & (u["ebit_ttm"] > 0) & u["earnings_yield"].notna() & u["roc"].notna()
        v = u[ok]
        gv = v.groupby(level="date")
        s = gv["earnings_yield"].rank(ascending=False) + gv["roc"].rank(ascending=False)
        new["magic_rank"] = s.groupby(level="date").rank(method="first")
    panel = panel.join(new)
    panel["risk_light"] = np.select([panel["var5_rank"] < 0.6, panel["var5_rank"] < 0.8, panel["var5_rank"] >= 0.8],
                                    ["綠", "黃", "紅"], default="")
    return panel


# ---------------------------------------------------------------------------
# 大盤層（L4 濾網、多空頭分段）
# ---------------------------------------------------------------------------


def market_frame(bench_adj_close: pd.Series, tx_inst: pd.DataFrame | None = None,
                 mtx_daily: pd.DataFrame | None = None, mtx_inst: pd.DataFrame | None = None,
                 tmf_daily: pd.DataFrame | None = None, tmf_inst: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    bench_adj_close：0050 還原收盤
    L4 沿用 txswing 的兩個閥門：
      inst_short   三大法人合計大台淨未平倉 5 日減少
      retail_bull  小台、微台散戶多空比各半合成 > 0（散戶 = 全市場未平倉扣掉三大法人；微台上市前只用小台）
    """
    idx = bench_adj_close.index
    m = pd.DataFrame(index=idx)
    m["bench"] = bench_adj_close
    m["market_uptrend"] = (bench_adj_close > bench_adj_close.rolling(200).mean()).astype(float)
    if tx_inst is not None and len(tx_inst):
        a = tx_inst.groupby("date")[["long_open_interest_balance_volume", "short_open_interest_balance_volume"]].sum().reindex(idx)
        anet = a.iloc[:, 0] - a.iloc[:, 1]
        m["inst_short"] = (anet.diff(5) < 0).astype(float).where(anet.diff(5).notna())

    def retail(daily, inst):
        if daily is None or inst is None or not len(daily) or not len(inst):
            return None
        dd = daily[(daily["trading_session"].astype(str) == "position") & ~daily["contract_date"].astype(str).str.contains("/")]
        oi = dd.groupby("date")["open_interest"].sum()
        g = inst.groupby("date")[["long_open_interest_balance_volume", "short_open_interest_balance_volume"]].sum()
        x = pd.concat([oi.rename("oi"), g], axis=1).dropna()
        x = x[x["oi"] > 0]
        return ((x.iloc[:, 2] - x.iloc[:, 1]) / x["oi"]).reindex(idx)  # (法人空 − 法人多) ÷ 未平倉 = 散戶淨多比

    rm, rt = retail(mtx_daily, mtx_inst), retail(tmf_daily, tmf_inst)
    if rm is not None:
        r = rm if rt is None else pd.Series(np.where(rt.isna(), rm, 0.5 * rm + 0.5 * rt), idx)
        m["retail_ratio"] = r
        m["retail_bull"] = (r > 0).astype(float).where(r.notna())
    m["l4_scale"] = 1.0
    if "inst_short" in m:
        m["l4_scale"] *= np.where(m["inst_short"] == 1, 0.5, 1.0)
    if "retail_bull" in m:
        m["l4_scale"] *= np.where(m["retail_bull"] == 1, 0.5, 1.0)
    return m


_ROC = re.compile(r"(\d+)年")
