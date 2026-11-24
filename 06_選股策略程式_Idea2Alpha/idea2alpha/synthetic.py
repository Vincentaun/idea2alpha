"""
synthetic — 產生格式與 FinMind 相同的模擬資料，讓 selftest 不需要網路就能跑完整流程
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def make_raw(sid: str, start="2013-01-01", end="2026-10-30", seed=None) -> dict:
    rng = np.random.default_rng(seed if seed is not None else int(sid))
    dates = pd.bdate_range(start, end)
    n = len(dates)
    mu, sig = rng.uniform(-0.0002, 0.0008), rng.uniform(0.012, 0.03)
    r = rng.normal(mu, sig, n)
    c = 50 * np.exp(np.cumsum(r))
    o = c * np.exp(rng.normal(0, sig / 3, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, sig / 2, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, sig / 2, n)))
    vol = np.exp(rng.normal(np.log(rng.uniform(2e6, 5e7)), 0.5, n))
    price = pd.DataFrame({"date": dates, "stock_id": sid, "Trading_Volume": vol, "Trading_money": vol * c,
                          "open": o, "max": h, "min": l, "close": c})
    # 每年 7 月除息一次
    ex = [d for d in dates if d.month == 7 and d.day <= 3][::3]
    events = pd.DataFrame({"date": ex, "before_price": [c[dates.get_loc(d) - 1] for d in ex]})
    events["after_price"] = events["before_price"] * 0.97
    per = pd.DataFrame({"date": dates, "stock_id": sid,
                        "PER": np.clip(15 + np.cumsum(rng.normal(0, 0.1, n)), 3, 80),
                        "PBR": np.clip(2 + np.cumsum(rng.normal(0, 0.01, n)), 0.3, 15),
                        "dividend_yield": np.clip(4 + np.cumsum(rng.normal(0, 0.02, n)), 0, 12)})
    names = ["Foreign_Investor", "Investment_Trust", "Dealer_self", "Dealer_Hedging", "Foreign_Dealer_Self"]
    inst = pd.concat([pd.DataFrame({"date": dates, "stock_id": sid, "name": nm,
                                    "buy": np.abs(rng.normal(1, 1, n)) * vol * 0.05,
                                    "sell": np.abs(rng.normal(1, 1, n)) * vol * 0.05}) for nm in names])
    margin = pd.DataFrame({"date": dates, "stock_id": sid,
                           "MarginPurchaseTodayBalance": np.abs(10000 + np.cumsum(rng.normal(0, 200, n)))})
    months = pd.period_range(start, end, freq="M")[:-1]
    g = rng.uniform(-0.01, 0.03)
    rev = 1e9 * np.exp(np.cumsum(rng.normal(g, 0.08, len(months))))
    mrev = pd.DataFrame({"date": (months + 1).to_timestamp(), "stock_id": sid, "revenue": rev,
                         "revenue_year": months.year, "revenue_month": months.month,
                         "create_time": ((months + 1).to_timestamp() + pd.Timedelta(days=9)).strftime("%Y-%m-%d")})
    qs = pd.date_range(start, end, freq="QE")
    qs = qs[qs <= pd.Timestamp(end) - pd.Timedelta(days=60)]
    k = len(qs)
    rq = 3e9 * np.exp(np.cumsum(rng.normal(g * 3, 0.1, k)))
    ni = rq * rng.uniform(0.02, 0.2) * np.exp(rng.normal(0, 0.3, k))
    fs_vals = {"Revenue": rq, "GrossProfit": rq * 0.3, "OperatingIncome": ni * 1.2, "IncomeAfterTaxes": ni, "EPS": ni / 1e8}
    fs = pd.concat([pd.DataFrame({"date": qs, "stock_id": sid, "type": t, "value": v}) for t, v in fs_vals.items()])
    ta = rq * 4
    bs_vals = {"TotalAssets": ta, "Liabilities": ta * rng.uniform(0.2, 0.6), "CurrentAssets": ta * 0.4,
               "CurrentLiabilities": ta * 0.4 / rng.uniform(1, 3), "Equity": ta * 0.55,
               "CashAndCashEquivalents": ta * 0.1, "PropertyPlantAndEquipment": ta * 0.3,
               "LongtermBorrowings": ta * 0.05, "OrdinaryShare": np.full(k, 1e9)}
    bs = pd.concat([pd.DataFrame({"date": qs, "stock_id": sid, "type": t, "value": v}) for t, v in bs_vals.items()])
    cfo_q, capex_q = ni * 1.3, -ni * 0.5
    yr = qs.year
    cum = lambda x: pd.Series(x).groupby(yr).cumsum().to_numpy()
    cf = pd.concat([pd.DataFrame({"date": qs, "stock_id": sid, "type": "CashFlowsFromOperatingActivities", "value": cum(cfo_q)}),
                    pd.DataFrame({"date": qs, "stock_id": sid, "type": "PropertyAndPlantAndEquipment", "value": cum(capex_q)})])
    years = range(qs[0].year, qs[-1].year)
    div = pd.DataFrame({"date": [pd.Timestamp(y + 1, 3, 20) for y in years], "stock_id": sid,
                        "year": [f"{y - 1911}年" for y in years],
                        "CashEarningsDistribution": [max(0, rng.normal(2, 0.5)) for _ in years],
                        "CashStatutorySurplus": 0.0,
                        "AnnouncementDate": [f"{y + 1}-03-01" for y in years]})
    return {"price": price, "events": events, "per": per, "inst": inst, "margin": margin, "mrev": mrev,
            "fs": fs, "bs": bs, "cf": cf, "div": div, "holders": None}


def make_market(dates: pd.DatetimeIndex, seed=1, cut=None) -> pd.DataFrame:
    """cut：把輸入資料截斷在這一天（偷看未來測試用）"""
    from .factors import market_frame
    rng = np.random.default_rng(seed)
    b = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.011, len(dates)))), dates)
    inst = pd.DataFrame({"date": np.repeat(dates, 3), "institutional_investors": ["外資", "投信", "自營商"] * len(dates),
                         "long_open_interest_balance_volume": rng.integers(10000, 60000, 3 * len(dates)),
                         "short_open_interest_balance_volume": rng.integers(10000, 60000, 3 * len(dates))})
    if cut is not None:
        b, inst = b[b.index <= cut], inst[inst["date"] <= cut]
    return market_frame(b, inst)
