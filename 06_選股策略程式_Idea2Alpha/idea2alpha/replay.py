"""
replay — 先鎖定、再揭露的歷史重播（圖 6、時光機模式）

  1. lock：把條件、失效條件、觀察天數、期間、選案方式寫進鎖定檔，算 SHA-256；這一步不看任何結果。
  2. reveal：讀鎖定檔、驗證雜湊沒被改過，才去看訊號日之後的資料，逐日判定：
       failed   觀察期內失效條件成立（記下第幾天）
       held     觀察期每一天都有資料，且失效條件從未成立
       unknown  觀察期資料不足（停牌、資料尚未更新）
     每個訊號只用訊號日以前的資料產生；選案方式在鎖定檔裡事先寫好，不從結果挑。

這是「回顧式」重播：資料是歷史資料，只是在程式裡截斷。真正的前瞻追蹤是預言卡（鎖定後等真實時間經過）。
若重播時另外請 AI 解說，請用 facts.build(..., hide_name=True) 遮蔽代號，降低模型記得歷史走勢的影響。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import engine as E

ROOT = Path(__file__).resolve().parent.parent


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def lock(rule: dict, failure: dict, start: str, end: str, title: str = "", selection: str | None = None,
         detail_cases: str | None = None, period: str = "swing") -> Path:
    body = {
        "title": title, "period": period, "rule": rule, "failure_condition": failure,
        "window": {"start": start, "end": end},
        "selection": selection or "期間內每一個交易日、股票池（當日成交值前 300 名）內所有符合條件的股票，全部列入",
        "detail_cases": detail_cases or "圖中詳列的兩例：依訊號日先後，第一個 held 與第一個 failed；同日以成交值排名較前者優先",
        "data_rule": "每個訊號只用訊號日收盤以前的資料；觀察期從訊號日的下一個交易日開始",
        "declared_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    rec = {**body, "sha256": _sha(body)}
    (ROOT / "runs").mkdir(exist_ok=True)
    f = ROOT / "runs" / f"replay_lock_{rec['sha256'][:8]}.json"
    if f.exists():
        raise FileExistsError(f"同樣內容的鎖定檔已存在：{f}")
    f.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return f


def reveal(lock_file, panel: pd.DataFrame) -> dict:
    rec = json.loads(Path(lock_file).read_text(encoding="utf-8"))
    body = {k: v for k, v in rec.items() if k != "sha256"}
    if _sha(body) != rec["sha256"]:
        raise ValueError("鎖定檔內容和雜湊不符：鎖定後被修改過，不能揭露")
    ev = E.Evaluator(panel)
    hit, _, _ = ev.eval(rec["rule"])
    fl = {k: v for k, v in rec["failure_condition"].items() if k != "horizon_days"}
    H = int(rec["failure_condition"]["horizon_days"])
    fhit, _, _ = ev.eval(fl)
    u = panel["in_universe"] == 1
    dates = panel.index.get_level_values("date").unique().sort_values()
    pos = {d: i for i, d in enumerate(dates)}
    w0, w1 = pd.Timestamp(rec["window"]["start"]), pd.Timestamp(rec["window"]["end"])
    sig = hit[hit & u]
    sig = sig[(sig.index.get_level_values("date") >= w0) & (sig.index.get_level_values("date") <= w1)]
    cases = []
    for (d, sid) in sig.index:
        i0 = pos[d]
        base = panel.loc[(d, sid)]
        obs, status, fail_day = [], None, None
        for k in range(1, H + 1):
            if i0 + k >= len(dates):
                status = "unknown"
                break
            dk = dates[i0 + k]
            if (dk, sid) not in panel.index or pd.isna(panel.loc[(dk, sid), "adj_close"]):
                status = "unknown"
                break
            row = panel.loc[(dk, sid)]
            f_true = bool(fhit.loc[(dk, sid)])
            obs.append({"day": k, "date": str(dk.date()), "close": float(row["close_raw"]),
                        "ret_vs_signal_pct": float((row["adj_close"] / base["adj_close"] - 1) * 100),
                        "pct_vs_ma20": float((row["adj_close"] / row["ma20"] - 1) * 100) if "ma20" in row else np.nan,
                        "failure_true": f_true})
            if f_true:
                status, fail_day = "failed", k
                break
        status = status or "held"
        cases.append({"signal_date": str(d.date()), "stock_id": sid, "turnover_rank": float(base.get("turnover_rank", np.nan)),
                      "signal_close": float(base["close_raw"]), "signal_vol_ratio20": float(base.get("vol_ratio20", np.nan)),
                      "signal_pct_vs_ma20": float((base["adj_close"] / base["ma20"] - 1) * 100) if "ma20" in base else np.nan,
                      "status": status, "fail_day": fail_day, "observations": obs})
    cases.sort(key=lambda c: (c["signal_date"], c["turnover_rank"]))
    pick = {}
    for st in ("held", "failed"):
        x = [c for c in cases if c["status"] == st]
        if x:
            pick[st] = x[0]
    counts = pd.Series([c["status"] for c in cases]).value_counts().to_dict() if cases else {}
    res = {"lock_file": str(Path(lock_file).name), "lock_sha256": rec["sha256"], "declared_at": rec["declared_at"],
           "revealed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "lock": body, "n_signals": len(cases), "counts": counts, "detail": pick, "cases": cases}
    f = ROOT / "runs" / f"replay_result_{rec['sha256'][:8]}.json"
    f.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    res["_file"] = str(f)
    return res
