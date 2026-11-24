"""
pipeline — 把快取的原始資料組成「因子面板」（date × stock_id）

  panel = build_panel(ids, period="swing")
  panel.to_parquet("output/panel_swing.parquet")

面板之後給 engine（篩選）與 backtest（回測）共用；前端要的每日因子快照也從這裡切出來。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import data as D
from . import factors as F

ROOT = Path(__file__).resolve().parent.parent


def load_periods() -> dict:
    return json.loads((ROOT / "periods.json").read_text(encoding="utf-8"))


def raw_for(sid: str, refresh=False, holders=False, daily_all: pd.DataFrame | None = None,
            events_all: pd.DataFrame | None = None) -> dict:
    ld = lambda ds: D.load(ds, sid, refresh=refresh)
    if daily_all is not None:
        price = daily_all[daily_all["stock_id"] == sid]
    else:
        price = ld("TaiwanStockPrice")
    ev = [x for x in (ld("TaiwanStockDividendResult"), ld("TaiwanStockSplitPrice")) if len(x)]
    if events_all is not None:  # 實驗室的全市場除權息檔（tw_dividend_events.csv）
        ev.append(events_all[events_all["stock_id"] == sid])
        ev = [pd.concat(ev).drop_duplicates("date", keep="last")]
    raw = {
        "price": price,
        "events": pd.concat(ev) if ev else None,
        "per": ld("TaiwanStockPER"),
        "inst": ld("TaiwanStockInstitutionalInvestorsBuySell"),
        "margin": ld("TaiwanStockMarginPurchaseShortSale"),
        "mrev": ld("TaiwanStockMonthRevenue"),
        "fs": ld("TaiwanStockFinancialStatements"),
        "bs": ld("TaiwanStockBalanceSheet"),
        "cf": ld("TaiwanStockCashFlowsStatement"),
        "div": ld("TaiwanStockDividend"),
    }
    if holders:
        try:
            raw["holders"] = ld("TaiwanStockHoldingSharesPer")
        except PermissionError:
            raw["holders"] = None
    return raw


def build_market(refresh=False) -> pd.DataFrame:
    px = D.load("TaiwanStockPrice", "0050", refresh=refresh)
    ev = [x for x in (D.load("TaiwanStockDividendResult", "0050", refresh=refresh),
                      D.load("TaiwanStockSplitPrice", "0050", refresh=refresh)) if len(x)]
    b = F.adjust_prices(px, pd.concat(ev) if ev else None)["adj_close"]
    fut = lambda ds, fid: (pd.read_csv(D.CACHE / ds / f"{fid}.csv.gz", parse_dates=["date"])
                           if (D.CACHE / ds / f"{fid}.csv.gz").exists() else None)
    m = F.market_frame(b, fut("TaiwanFuturesInstitutionalInvestors", "TX"),
                          fut("TaiwanFuturesDaily", "MTX"), fut("TaiwanFuturesInstitutionalInvestors", "MTX"),
                          fut("TaiwanFuturesDaily", "TMF"), fut("TaiwanFuturesInstitutionalInvestors", "TMF"))
    m.attrs["bench_name"] = "0050（還原收盤）"
    return m


def build_panel(ids: list[str], period: str = "swing", start="2015-01-01", universe_n=300,
                market: pd.DataFrame | None = None, raw_loader=None, info: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    ids：股票代號；period：short / swing / long
    raw_loader：測試用，可換成產生模擬資料的函式（sid → raw dict）
    """
    p = load_periods()[period]
    raw_loader = raw_loader or raw_for
    fin = set()
    if info is not None:
        fin = set(info.loc[info["industry_category"].astype(str).str.contains("金融"), "stock_id"])
    frames = []
    for n, sid in enumerate(ids, 1):
        raw = raw_loader(sid)
        if raw["price"] is None or len(raw["price"]) < 260:
            continue
        f = F.stock_frame(raw, p, financial=sid in fin)
        f = f[f.index >= pd.Timestamp(start)]
        f["stock_id"] = sid
        frames.append(f.reset_index().rename(columns={"index": "date"}))
        if n % 50 == 0:
            print(f"  因子 {n}/{len(ids)}")
    panel = pd.concat(frames, ignore_index=True).set_index(["date", "stock_id"]).sort_index()
    panel = F.cross_section(panel, universe_n)
    if market is not None:
        mk = market.drop(columns=["bench"], errors="ignore")
        panel = panel.join(mk, on="date")
    gaf = ROOT / "output" / f"gaf_scores_{period}.parquet"
    if gaf.exists():  # L3：gaf.py 產生的分數
        panel = panel.join(pd.read_parquet(gaf)[["gaf_up_pct"]])
    return panel


def snapshot(panel: pd.DataFrame, date=None) -> pd.DataFrame:
    """給前端的最新一天因子快照（只留股票池內）"""
    d = panel.index.get_level_values("date").max() if date is None else pd.Timestamp(date)
    x = panel.xs(d, level="date")
    return x[x["in_universe"] == 1]
