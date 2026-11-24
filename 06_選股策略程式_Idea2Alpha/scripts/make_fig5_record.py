"""
產生 runs/fig5_run_record.json（報告圖 5 與表中數字的唯一來源）

  python scripts/make_fig5_record.py --panel output/panel_swing.parquet

基準有兩個，分開記錄、分開畫，不要混用：
  0050                    0050 還原收盤（含除息與 2025-06-18 的 1 拆 4），由 FinMind 抓取；回測的「基準年化」就是它
  equal_weight_universe   每天股票池（成交值前 300 名）等權平均報酬，衡量「選股」是否勝過「同一池子平均」
2026-11-17 的舊紀錄把等權股票池放在回測的基準欄位，欄名卻是「0050 年化」（24.1%），造成「0050 有兩個數字」的誤會；
舊檔保留在 runs/superseded/。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG))

import pandas as pd  # noqa: E402

from idea2alpha import backtest as B  # noqa: E402
from idea2alpha import data as D  # noqa: E402
from idea2alpha import engine as E  # noqa: E402
from idea2alpha import factors as F  # noqa: E402
from idea2alpha import pipeline as P  # noqa: E402

CODE = ["idea2alpha/backtest.py", "idea2alpha/engine.py", "idea2alpha/factors.py", "idea2alpha/pipeline.py",
        "scripts/make_fig5_record.py"]


def ann(r: pd.Series, dates) -> float:
    r = r.reindex(dates).fillna(0.0)
    return float((1 + r).prod() ** (252 / len(dates)) - 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=str(PKG / "output" / "panel_swing.parquet"))
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--random", type=int, default=200)
    ap.add_argument("--out", default=str(PKG / "runs" / "fig5_run_record.json"))
    a = ap.parse_args()
    code_hash = hashlib.sha256(b"".join((PKG / f).read_bytes() for f in CODE)
                               + b"".join(f.read_bytes() for f in sorted((PKG / "strategies").glob("*.json")))).hexdigest()
    p = pd.read_parquet(a.panel)
    # 0050（還原收盤）
    px = D.load("TaiwanStockPrice", "0050", start="2018-01-01")
    ev = [x for x in (D.load("TaiwanStockDividendResult", "0050", start="2013-01-01"),
                      D.load("TaiwanStockSplitPrice", "0050", start="2013-01-01")) if len(x)]
    b0050 = F.adjust_prices(px, pd.concat(ev) if ev else None)["adj_close"]
    mk = F.market_frame(b0050)
    mk.attrs["bench_name"] = "0050（還原收盤）"
    # 等權股票池
    c = p["adj_close"].unstack()
    u = p["in_universe"].unstack() == 1
    ew_ret = c.pct_change().where(u.shift(1).fillna(False)).mean(axis=1).fillna(0.0)
    p = p.join(mk.drop(columns=["bench"]), on="date")
    dates = p.index.get_level_values("date").unique().sort_values()
    dates = dates[dates >= pd.Timestamp(a.start)]
    ev_ = E.Evaluator(p)
    per = P.load_periods()["swing"]
    rec = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "code_files": CODE,
           "code_sha256": code_hash,
           "data": "IDeS Lab full-market daily file (TaiwanStockPrice format, incl. delisted); ex-dividend/split adjustment "
                   "from tw_dividend_events.csv; 0050 from FinMind (TaiwanStockPrice, DividendResult, SplitPrice)",
           "universe": "each day: top 300 by 20-day average traded value among common stocks that ranked in the top 450 at any time since 2018",
           "period": "swing", "start": str(dates[0].date()), "end": str(dates[-1].date()), "n_days": len(dates),
           "rebalance": "last trading day of each week; signal at close, trade at next open",
           "max_holdings": 20, "weighting": "equal at entry; rebalanced only when >20% off target",
           "min_holding_days": per["eval_days"][0],
           "costs": {"commission_each_side": B.FEE, "tax_sell": B.TAX, "slippage_each_side": B.SLIP},
           "random_baseline": {"R": a.random, "seed": 7, "rule": "same universe, same number of names, same retention count per rebalance, same minimum holding"},
           "benchmarks": {
               "0050": {"annualized": ann(b0050.pct_change(), dates), "label": "0050 buy-and-hold (adjusted close)"},
               "equal_weight_universe": {"annualized": ann(ew_ret, dates), "label": "Equal-weighted daily top-300 universe"}},
           "strategies": {}}
    for name, node in [("T1 12-1 momentum", {"strategy": "T1"}), ("T3 52-week high", {"strategy": "T3"}),
                       ("Q3 Low volatility", {"strategy": "Q3"}),
                       ("Technical resonance (>=2 of T5-T8)", E.combo(["T5", "T6", "T7", "T8"], "vote", k=2))]:
        h, s, _ = ev_.eval(node)
        r = B.run_backtest(p, h, s, per, mk, start=a.start, n_random=a.random)
        eq = (1 + r["_returns"]).cumprod()
        rec["strategies"][name] = {
            "node": node, "benchmark": r["基準名稱"], "full": r["full"], "in_sample_2019_2023": r["樣本內 2019–2023"],
            "out_of_sample_2024_on": r["樣本外 2024 起"], "bull_regime": r["多頭段"], "bear_regime": r["空頭段"],
            "trades": r.get("交易"), "avg_signals": r["平均訊號檔數"], "random": r["隨機基準"],
            "random_sharpes": r["_random_sharpes"],
            "equity_weekly": {str(k.date()): float(v) for k, v in eq.resample("W-FRI").last().items()}}
        print(name, round(r["full"]["年化報酬"], 4), round(r["full"]["夏普"], 3), r["隨機基準"]["贏過隨機比例"], flush=True)
    Path(a.out).write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("基準：", {k: round(v["annualized"], 4) for k, v in rec["benchmarks"].items()})
    print("已寫入", a.out)


if __name__ == "__main__":
    main()
