"""
命令列入口：python -m idea2alpha.cli <指令> [選項]

計算核心（不需要 API 金鑰）
  selftest                         模擬資料跑完整流程，共 9 項檢查（組合語意 4 項、過度擬合、論點檢查、重播防竄改、
                                   翻譯員離線回放、偷看未來）。不需要網路與金鑰，也因此「不會」測到你的模型設定
  fetch   --top 400                抓成交值前 400 名的資料（也可 --stocks 2330,2317 或 --stocks-file x.txt）
  panel   --period swing           組因子面板 → output/panel_swing.parquet
  screen  --strategies G1,G2 --mode all --period swing
  screen  --template 技術共振 --period short
  screen  --json my_rule.json --period swing
  backtest --strategies T5,T6,T7,T8 --mode vote --k 2 --period short --random 200
  arena   --period swing           23 個策略同場回測排行 → output/arena_swing.csv
  stats   --strategies G2 --period swing   訊號後 N 日報酬分布（策略體檢卡）
  overlap --strategies Q2,Q3,G1,T8 --period swing
  gaf     --period swing           訓練 L3 線型影像分類器
  validate --json my_rule.json     檢查條件 JSON 是否都在因子白名單內

生成式 AI 模組（模型設定見 llm.py；每次呼叫都記在 runs/llm_calls.jsonl）
  translate "低價、剛突破月線、放量"                     互動式翻譯（要金鑰，不用面板）
  eval-translator --modes direct,dict,pipeline          30 題測試集、三種做法比較（要金鑰＋output/panel_swing.parquet）
  facts   --stock 2610 --date 2026-07-28                帶日期的事實表（不用金鑰，要面板）
  explain --stock 2610 --date 2026-07-28 --hide-name    解說員（要金鑰＋面板）
  debate  --stock 2610 --date 2026-07-28 --hide-name    多方、空方、裁判（要金鑰＋面板）
  replay  --rule r.json --start 2026-07-01 --end 2026-09-30   先鎖定；再 replay --reveal 鎖定檔 揭露（不用金鑰，揭露要面板）
  figures --fig3 … --fig4 … --fig5 … --fig6 …           從 runs/ 的紀錄檔畫報告圖 3–6（不用金鑰，不用面板）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from . import backtest as B
from . import data as D
from . import engine as E
from . import pipeline as P

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"


def _node(a) -> dict:
    if a.json:
        return json.loads(Path(a.json).read_text(encoding="utf-8"))
    if a.template:
        t = json.loads((ROOT / "templates.json").read_text(encoding="utf-8"))
        return t[a.template]
    ids = [s.strip() for s in a.strategies.split(",") if s.strip()]
    w = [float(x) for x in a.weights.split(",")] if a.weights else None
    return E.combo(ids, a.mode, k=a.k, weights=w, top_n=a.top_n)


def _panel(period) -> pd.DataFrame:
    f = OUT / f"panel_{period}.parquet"
    if not f.exists():
        sys.exit(f"找不到 {f}，先跑：python -m idea2alpha.cli panel --period {period}")
    return pd.read_parquet(f)


def _market() -> pd.DataFrame:
    f = OUT / "market.parquet"
    m = pd.read_parquet(f) if f.exists() else P.build_market()
    m.attrs.setdefault("bench_name", "0050（還原收盤）")
    return m


def _ids(a) -> list[str]:
    if getattr(a, "stocks", None):
        return [s.strip() for s in a.stocks.split(",")]
    f = Path(getattr(a, "stocks_file", None) or OUT / "universe.txt")
    if not f.exists():
        sys.exit("沒有股票清單：用 --stocks、--stocks-file，或先跑 fetch --top 400")
    return [s.strip() for s in f.read_text().split() if s.strip()]


def _fmt(m: dict) -> str:
    pct_keys = ("報酬", "回撤", "年化", "勝率", "比例", "超額", "同期")
    out = []
    for k, v in m.items():
        if isinstance(v, (float, np.floating)):
            out.append(f"{k} {v:.1%}" if any(t in k for t in pct_keys) else f"{k} {v:.2f}")
        else:
            out.append(f"{k} {v}")
    return "、".join(out)


def cmd_fetch(a):
    ids = _ids(a) if (a.stocks or a.stocks_file) else D.candidates_today(a.top)
    OUT.mkdir(exist_ok=True)
    (OUT / "universe.txt").write_text("\n".join(ids))
    print(f"股票 {len(ids)} 檔（清單存在 output/universe.txt），開始抓大盤資料")
    D.fetch_market()
    sets = ["price", "valuation", "chips", "fundamental"] + (["holders"] if a.holders else [])
    D.fetch_stocks(ids, sets)
    print("完成。下一步：python -m idea2alpha.cli panel --period swing")


def cmd_panel(a):
    ids = _ids(a) if (a.stocks or a.stocks_file or not a.daily_all) else []
    mk = P.build_market(refresh=False)
    OUT.mkdir(exist_ok=True)
    mk.to_parquet(OUT / "market.parquet")
    info = D.stock_info()
    daily = D.load_daily_all(a.daily_all) if a.daily_all else None
    evs = None
    if a.events_file:
        evs = pd.read_csv(a.events_file, dtype={"stock_id": str}, parse_dates=["date"], encoding="utf-8-sig")
    if daily is not None and not (a.stocks or a.stocks_file):
        # 有全市場日線時：取「曾經進過成交值前 universe_n×1.5 名」的股票，股票池每天依當時成交值重算（含已下市）
        m = daily[daily["date"] >= pd.Timestamp(a.start)].copy()
        m["t20"] = m.groupby("stock_id")["Trading_money"].transform(lambda x: x.rolling(20).mean())
        m = m[m["stock_id"].str.fullmatch(r"[1-9]\d{3}")]
        m["rk"] = m.groupby("date")["t20"].rank(ascending=False)
        ids = sorted(m.loc[m["rk"] <= 450, "stock_id"].unique())
        print(f"全市場日線：曾進前 450 名的股票 {len(ids)} 檔")
    loader = (lambda s: P.raw_for(s, holders=a.holders, daily_all=daily, events_all=evs))
    for per in a.period.split(","):
        panel = P.build_panel(ids, per, start=a.start, market=mk, raw_loader=loader, info=info)
        panel.to_parquet(OUT / f"panel_{per}.parquet")
        print(f"{per}：{panel.shape[0]:,} 列 × {panel.shape[1]} 個欄位 → output/panel_{per}.parquet")


def cmd_screen(a):
    panel = _panel(a.period)
    ev = E.Evaluator(panel)
    node = _node(a)
    errs = E.validate(node, E.load_factor_dict(), ev.lib)
    if errs:
        print("條件檢查：", *errs, sep="\n  ")
    res = ev.screen(node, a.date)
    d = a.date or panel.index.get_level_values("date").max().date()
    print(f"{d} 符合條件 {len(res)} 檔（股票池內）")
    if "strategy" not in node and node.get("rules"):
        ids = [r["strategy"] for r in node["rules"] if "strategy" in r]
        if ids:
            print("各策略單獨命中：", ev.counts(ids, a.date).to_dict())
    print(res.head(a.show).to_string())
    if len(res) < 3 and node.get("logic") == "all":
        print("提示：全部符合的檔數少於 3，可改用 --mode vote 或放寬條件")
    OUT.mkdir(exist_ok=True)
    res.to_csv(OUT / "screen_latest.csv", encoding="utf-8-sig")


def cmd_backtest(a):
    panel = _panel(a.period)
    mk = _market()
    per = P.load_periods()[a.period]
    ev = E.Evaluator(panel)
    node = _node(a)
    hit, score, _ = ev.eval(node)
    ov = E.uses_overlay(node, ev.lib)
    if ov and not {"inst_short", "retail_bull"} & set(mk.columns):
        print("注意：沒有期貨法人／散戶資料，L4 不會生效（先跑 fetch 抓大盤資料）")
    r = B.run_backtest(panel, hit, score, per, mk, overlay=ov, start=a.start, end=a.end, n_random=a.random,
                       hold_days=a.hold)
    print(f"週期：{per['name']}，換股 {per['rebalance']}，L4 濾網：{'開' if ov else '關'}")
    print(f"  基準：{r['基準名稱']}")
    for k, v in r.items():
        if not k.startswith("_") and isinstance(v, dict):
            print(f"  {k}：{_fmt(v)}")
    print(f"  平均訊號檔數：{r['平均訊號檔數']:.1f}，最短持有 {r['最短持有天數']} 個交易日")
    OUT.mkdir(exist_ok=True)
    n_cond = E.count_conditions(node, ev.lib)
    if n_cond > E.OVERFIT_LIMIT or a.ablation:
        if n_cond > E.OVERFIT_LIMIT:
            print(f"過度擬合警示：共 {n_cond} 個條件（超過 {E.OVERFIT_LIMIT} 個）。逐一拿掉每個條件重跑：")
        ab = B.ablation(ev, node, panel, per, mk, overlay=ov, start=a.start, end=a.end, hold_days=a.hold)
        show = ab.copy()
        for c in ["年化報酬", "年化報酬變化"]:
            show[c] = show[c].map(lambda x: f"{x:+.1%}" if c.endswith("變化") else f"{x:.1%}")
        for c in ["夏普", "夏普變化"]:
            show[c] = show[c].map(lambda x: f"{x:+.2f}" if c.endswith("變化") else f"{x:.2f}")
        print(show.to_string(index=False))
        print("  拿掉後夏普上升或幾乎不變（|變化| < 0.05）的條件，可能只是在湊歷史，考慮刪掉。")
        ab.to_csv(OUT / "backtest_ablation.csv", index=False, encoding="utf-8-sig")
    r["_returns"].to_csv(OUT / "backtest_returns.csv", header=["ret"])
    if r["_trades"] is not None:
        r["_trades"].to_csv(OUT / "backtest_trades.csv", index=False, encoding="utf-8-sig")


def cmd_arena(a):
    panel = _panel(a.period)
    mk = _market()
    per = P.load_periods()[a.period]
    ev = E.Evaluator(panel)
    ids = a.strategies.split(",") if a.strategies else [k for k, v in ev.lib.items() if v.get("role") != "overlay"]
    rows = []
    for sid in ids:
        for ov in ([False, True] if a.l4 else [False]):
            node = {"logic": "all", "rules": [{"strategy": sid}] + ([{"strategy": "L4"}] if ov else [])}
            hit, score, cov = ev.eval(node)
            r = B.run_backtest(panel, hit, score, per, mk, overlay=ov, start=a.start, n_random=a.random)
            row = {"策略": sid, "名稱": ev.lib[sid]["name"], "L4": "開" if ov else "關",
                   "適合週期": "是" if a.period in ev.lib[sid].get("periods", []) else "否",
                   "平均訊號檔數": r["平均訊號檔數"], "資料覆蓋率": float(cov.mean())}
            for part in ["full", "樣本內 2019–2023", "樣本外 2024 起"]:
                for k, v in r.get(part, {}).items():
                    row[f"{part}・{k}"] = v
            row.update({f"交易・{k}": v for k, v in r.get("交易", {}).items()})
            row.update({f"隨機・{k}": v for k, v in r.get("隨機基準", {}).items()})
            rows.append(row)
            print(f"  {sid} L4{'開' if ov else '關'}：{_fmt(r.get('full', {}))}")
    df = pd.DataFrame(rows).sort_values("full・夏普", ascending=False)
    OUT.mkdir(exist_ok=True)
    df.to_csv(OUT / f"arena_{a.period}.csv", index=False, encoding="utf-8-sig")
    print(f"排行存到 output/arena_{a.period}.csv")


def cmd_stats(a):
    panel = _panel(a.period)
    ev = E.Evaluator(panel)
    hit, _, _ = ev.eval(_node(a))
    days = P.load_periods()[a.period]["eval_days"]
    print(B.signal_stats(hit, panel, days).to_string(index=False))


def cmd_overlap(a):
    panel = _panel(a.period)
    ev = E.Evaluator(panel)
    m = ev.overlap(a.strategies.split(","))
    print(m.round(3).to_string())
    low = [(x, y) for x in m.index for y in m.columns if x < y and m.loc[x, y] < 0.05]
    if low:
        print("本質互斥（共同入選率 < 5%）：", low)


def cmd_gaf(a):
    from . import gaf
    gaf.train_and_score(_panel(a.period), a.period, a.train_end)
    print("重新跑 panel 後，L3 就會讀到 gaf_up_pct")


def cmd_validate(a):
    node = json.loads(Path(a.json).read_text(encoding="utf-8"))
    errs = E.validate(node, E.load_factor_dict(), E.load_library())
    print("通過" if not errs else "\n".join(errs))


def cmd_selftest(a):
    from . import synthetic as S
    import time
    t0 = time.time()
    ids = [str(1101 + i) for i in range(30)]
    raws = {s: S.make_raw(s) for s in ids}
    mk = S.make_market(pd.bdate_range("2013-01-01", "2026-10-30"))
    lib = E.load_library()
    ok = True
    for per in ["short", "swing", "long"]:
        panel = P.build_panel(ids, per, start="2016-01-01", universe_n=25, market=mk, raw_loader=lambda s: raws[s])
        ev = E.Evaluator(panel)
        hits = {sid: int(ev.eval({"strategy": sid})[0].sum()) for sid in lib}
        r = B.run_backtest(panel, *ev.eval(E.combo(["T5", "T6", "T7", "T8"], "vote", k=1))[:2],
                           P.load_periods()[per], mk, n_random=5)
        print(f"[{per}] 面板 {panel.shape}，各策略命中筆數 {hits}")
        print(f"   技術共振回測：{_fmt(r['full'])}")
    # 引擎語意
    panel = P.build_panel(ids, "swing", start="2016-01-01", universe_n=25, market=mk, raw_loader=lambda s: raws[s])
    ev = E.Evaluator(panel)
    a1, a2 = ev.eval({"strategy": "T5"})[0], ev.eval({"strategy": "T6"})[0]
    and_ = ev.eval(E.combo(["T5", "T6"], "all"))[0]
    or_ = ev.eval(E.combo(["T5", "T6"], "any"))[0]
    vote = ev.eval(E.combo(["T5", "T6"], "vote", k=2))[0]
    l4 = ev.eval(E.combo(["T5", "L4"], "any"))[0]
    checks = {"AND = 兩者交集": (and_ == (a1 & a2)).all(), "OR = 兩者聯集": (or_ == (a1 | a2)).all(),
              "投票 k=2 = AND": (vote == and_).all(), "L4 不影響選股": (l4 == a1).all()}
    # 過度擬合警示：T2 有 6 個條件 → 觸發；逐一拿掉後應有 6 個版本，且每個版本命中數 ≥ 原版
    n2 = E.count_conditions({"strategy": "T2"}, ev.lib)
    var = E.ablation_variants({"strategy": "T2"}, ev.lib)
    t2 = ev.eval({"strategy": "T2"})[0].sum()
    checks["過度擬合警示（T2 六個條件、六個拿掉版本、拿掉條件不會變少）"] = (
        n2 == 6 and len(var) == 6 and all(ev.eval(v)[0].sum() >= t2 for _, v in var)
        and E.count_conditions(E.combo(["T5", "L4"], "all"), ev.lib) == 3)
    # 論點檢查：四種判定各一例
    from . import claims as CL
    from . import facts as FA
    d0 = panel.index.get_level_values("date").max()
    sid0 = panel.xs(d0, level="date").index[0]
    sh = FA.build(panel, sid0, d0)
    fid = {x["source"]: x for x in sh["facts"]}
    v5 = fid["vol_ratio20"]
    tests = [({"text": f"Volume was {v5['shown']} times its average.", "cites": [v5["id"]]}, "supported"),
             ({"text": f"Volume is sustained at {v5['shown']} times its average.", "cites": [v5["id"]]}, "insufficient"),
             ({"text": "Volume rose to 987.6 times its average.", "cites": [v5["id"]]}, "unsupported"),
             ({"text": "Buy now.", "cites": ["F99"]}, "invalid")]
    checks["論點檢查（supported／insufficient／unsupported／invalid）"] = all(CL.check_claim(c, sh)["verdict"] == v for c, v in tests)
    # 歷史重播：鎖定檔被改過就不能揭露
    import tempfile
    from . import replay as RP
    RP.ROOT = Path(tempfile.mkdtemp())
    lf = RP.lock({"strategy": "T5"}, {"logic": "any", "rules": [{"factor": "above_ma20", "op": "==", "value": 0}], "horizon_days": 5},
                 "2024-01-01", "2024-03-31", title="selftest")
    res = RP.reveal(lf, panel)
    rec = json.loads(lf.read_text(encoding="utf-8"))
    rec["failure_condition"]["horizon_days"] = 10
    lf.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    try:
        RP.reveal(lf, panel)
        tamper = False
    except ValueError:
        tamper = True
    checks["歷史重播（鎖定後揭露、竄改鎖定檔會被拒絕）"] = res["n_signals"] > 0 and tamper and \
        all(c["status"] in ("held", "failed", "unknown") for c in res["cases"])
    # 翻譯員：用錄製回應離線走一遍「提問 → 條件」與「誤譯 → 攔截 → 修正」
    import os
    from . import translate as TR
    from . import llm as LL
    rp = Path(tempfile.mkdtemp()) / "r.jsonl"
    rs = [{"status": "clarify", "questions": [{"id": "q1", "about": "放量", "text": "放量多大？", "options": []}]},
          {"status": "rule", "rule": {"logic": "all", "rules": [{"factor": "cross_up_ma20", "op": "==", "value": 1},
                                                              {"factor": "vol_ratio20", "op": ">=", "value": 1.5}]}},
          {"status": "rule", "rule": {"logic": "all", "rules": [{"factor": "above_ma20", "op": "==", "value": 1}]}},
          {"status": "rule", "rule": {"logic": "all", "rules": [{"factor": "cross_up_ma20", "op": "==", "value": 1}]}},
          # 11-20 真實評估 H02 的錯誤：「至少兩個」寫成 logic "vote2"
          {"status": "rule", "rule": {"logic": "vote2", "rules": [{"strategy": "T6"}, {"strategy": "T7"}, {"strategy": "T8"}]}},
          {"status": "rule", "rule": {"logic": "vote", "k": 2, "rules": [{"strategy": "T6"}, {"strategy": "T7"}, {"strategy": "T8"}]}}]
    rp.write_text("\n".join(json.dumps({"text": json.dumps(r, ensure_ascii=False)}, ensure_ascii=False) for r in rs), encoding="utf-8")
    os.environ.update(IDEA2ALPHA_LLM="replay", IDEA2ALPHA_REPLAY=str(rp))
    LL._replay, LL.RUNS = None, Path(tempfile.mkdtemp())
    l1 = TR.translate("剛突破月線、有放量", "pipeline", lambda q: "放量：1.5 倍")
    l2 = TR.translate("剛站上 20 日均線", "pipeline", lambda q: "")
    l3 = TR.translate("KD 黃金交叉、MACD 翻正、布林突破，三個至少兩個成立", "pipeline", lambda q: "")
    checks["翻譯員（澄清後產生條件；事件誤譯成狀態、logic 寫錯都被攔下並修正）"] = (
        l1["asked"] and not [i for i in l1["issues"] if i["level"] == "error"]
        and any(i["code"] == "EVENT_AS_STATE" for i in l2.get("intercepted", []))
        and not [i for i in l2["issues"] if i["level"] == "error"]
        and any(i["code"] == "LOGIC" for i in l3.get("intercepted", []))
        and not [i for i in l3["issues"] if i["level"] == "error"])
    ab = B.ablation(ev, {"strategy": "T2"}, panel, P.load_periods()["swing"], mk)
    print(f"   T2 逐一拿掉條件：夏普變化 {', '.join(f'{x:+.2f}' for x in ab['夏普變化'][1:])}")
    # 偷看未來：資料截斷在 2021-06-30 重算，之前的因子必須完全相同（價格水準類欄位除外：還原權值會整體縮放）
    cut = pd.Timestamp("2021-06-30")
    trunc = {s: {k: (v[v["date"] <= cut] if isinstance(v, pd.DataFrame) and "date" in v else v) for k, v in r.items()}
             for s, r in raws.items()}
    mk2 = S.make_market(pd.bdate_range("2013-01-01", "2026-10-30"), cut=cut)
    p2 = P.build_panel(ids, "swing", start="2016-01-01", universe_n=25, market=mk2, raw_loader=lambda s: trunc[s])
    level = {"adj_open", "adj_close", "ma_s", "ma_m", "ma_l", "close_p", "ma20", "ma50", "ma150", "ma200", "macd_hist"}
    cols = [c for c in p2.columns if c not in level and pd.api.types.is_numeric_dtype(p2[c])]
    p1 = panel.loc[panel.index.get_level_values("date") <= cut, cols]
    p2c = p2.loc[p1.index, cols]
    diff = ((p1 - p2c).abs() > 1e-6 * (1 + p1.abs())) & ~(p1.isna() & p2c.isna())
    bad = diff.sum()
    bad = bad[bad > 0]
    checks["偷看未來測試（截斷資料重算不變）"] = len(bad) == 0
    for k, v in checks.items():
        print(f"  {'通過' if v else '失敗'}：{k}")
        ok &= bool(v)
    if len(bad):
        print("  有差異的欄位：", bad.to_dict())
    print(f"selftest {'全部通過' if ok else '有失敗項目'}，{time.time() - t0:.0f} 秒")
    if not ok:
        sys.exit(1)


def _panel_dates(period, dates):
    f = OUT / f"panel_{period}.parquet"
    if not f.exists():
        sys.exit(f"找不到 {f}：跟老師拿 output/panel_{period}.parquet 放進 output/，或先跑 fetch 與 panel")
    return pd.read_parquet(f, filters=[("date", "in", [pd.Timestamp(d) for d in dates])]) if dates else pd.read_parquet(f)


def cmd_translate(a):
    from . import translate as TR
    log = TR.interactive(a.idea)
    fin = log["final"]
    print(json.dumps(fin, ensure_ascii=False, indent=1))
    for i in log.get("intercepted", []):
        print(f"（第一次輸出被攔下：[{i['code']}] {i['message']}）")
    for i in log["issues"]:
        print(f"[{i['level']}] {i['code']}：{i['message']}")
    OUT.mkdir(exist_ok=True)
    f = ROOT / "runs" / f"translate_{pd.Timestamp.now():%Y%m%d_%H%M%S}.json"
    f.write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"紀錄：{f}")


def cmd_eval_translator(a):
    from . import eval_translator as ET
    f = OUT / f"panel_{a.period}.parquet"
    if not f.exists():
        sys.exit(f"找不到 {f}：評估要用真實資料的因子面板來比對選股結果。"
                 f"跟老師拿 output/panel_{a.period}.parquet 放進 output/，或先跑 fetch 與 panel。")
    idx = pd.read_parquet(f, columns=["in_universe"]).index.get_level_values("date").unique().sort_values()
    s = pd.Series(idx, index=idx)
    jun = s[s.dt.month == 6]
    dates = list(jun.groupby(jun.dt.year).max())[-6:]
    df, summ = ET.run(_panel_dates(a.period, dates), modes=a.modes.split(","), dates=dates,
                      ids=a.ids.split(",") if a.ids else None)
    print(json.dumps(summ, ensure_ascii=False, indent=1))


def cmd_facts(a):
    from . import facts as FA
    sheet = FA.build(_panel_dates(a.period, [a.date]), a.stock, a.date, hide_name=a.hide_name)
    print(FA.to_text(sheet))
    (ROOT / "runs").mkdir(exist_ok=True)
    (ROOT / "runs" / f"factsheet_{a.stock}_{a.date}.json").write_text(json.dumps(sheet, ensure_ascii=False, indent=1), encoding="utf-8")


def cmd_debate(a):
    from . import debate as DB
    d = pd.Timestamp(a.date)
    p = _panel_dates(a.period, None)
    p = p[p.index.get_level_values("date") <= d]  # 只給到論點日期為止的資料
    out = DB.run(p, a.stock, a.date, a.thesis, hide_name=a.hide_name)
    for side in ("bull", "bear"):
        for c in out[f"{side}_checks"]:
            print(f"{side}  {c['verdict']:12s} {c['text']}  {'；'.join(c['reasons'])}")
    print(json.dumps(out["judge"], ensure_ascii=False, indent=1))
    for role, fm in out.get("format_checks", {}).items():
        state = "通過" if not fm["issues"] else "仍有問題：" + "；".join(i["message"] for i in fm["issues"])
        print(f"格式檢查 {role}：{'退回修正一次後' if fm['repaired'] else ''}{state}")
    print(f"紀錄：{out['_file']}")


def cmd_explain(a):
    from . import debate as DB
    d = pd.Timestamp(a.date)
    p = _panel_dates(a.period, None)
    p = p[p.index.get_level_values("date") <= d]
    rule = json.loads(Path(a.rule).read_text(encoding="utf-8")).get("rule") if a.rule else None
    out = DB.explain(p, a.stock, a.date, rule, hide_name=a.hide_name)
    for c in out["checks"]:
        print(f"{c['verdict']:12s} {c['text']}  {'；'.join(c['reasons'])}")
    print(f"紀錄：{out['_file']}")


def cmd_replay(a):
    from . import replay as RP
    if a.reveal:
        res = RP.reveal(a.reveal, _panel_dates(a.period, None))
        print(f"{res['n_signals']} 個訊號：{res['counts']}；紀錄 {res['_file']}")
        return
    node = json.loads(Path(a.rule).read_text(encoding="utf-8"))
    f = RP.lock(node["rule"], node["failure_condition"], a.start, a.end, title=node.get("title", ""), period=a.period)
    print(f"已鎖定：{f}\n確認內容無誤後再執行：python -m idea2alpha.cli replay --reveal {f}")


def cmd_figures(a):
    from . import claims as C
    from . import figures as FG
    out = ROOT / "figures"
    ill = ROOT / "testset" / "illustrative"
    if a.fig3:
        logs = FG.load_jsonl_logs(a.fig3, {"F01", "C01"})
        # 評估時的「使用者回答」是測試題預設的腳本回答，圖例照實寫
        FG.fig3(logs["F01"], logs["C01"], out, a.idea_en, False, f"Source: {Path(a.fig3).name} (logged model output)",
                answer_note="● = option chosen by the test item's scripted answer")
    else:
        FG.fig3(json.loads((ill / "fig3_ok_log.json").read_text(encoding="utf-8")),
                json.loads((ill / "fig3_bad_log.json").read_text(encoding="utf-8")), out, a.idea_en, True,
                "Source: testset/illustrative/fig3_*_log.json (offline replay of test responses through the real validator).")
    if a.fig4:
        d = json.loads(Path(a.fig4).read_text(encoding="utf-8"))
        FG.fig4(d["fact_sheet"], d["bull_checks"], d["bear_checks"], out, False, f"Source: {Path(a.fig4).name} (logged model output)")
    else:
        t = json.loads((ill / "fig4_test_claims.json").read_text(encoding="utf-8"))
        sheet = json.loads((ROOT / t["fact_sheet"]).read_text(encoding="utf-8"))
        FG.fig4(sheet, C.check_all(t["bull"], sheet), C.check_all(t["bear"], sheet), out, True,
                "Fact sheet: real program output. Claims: team-written checker test inputs.")
    if a.fig5:
        rec = json.loads(Path(a.fig5).read_text(encoding="utf-8"))
        FG.fig5(rec, out)
        if a.bare:
            FG.fig5(rec, out, bare=True)
    if a.fig6:
        res = json.loads(Path(a.fig6).read_text(encoding="utf-8"))
        res["_file"] = a.fig6
        FG.fig6(res, out)
        if a.bare:
            FG.fig6(res, out, bare=True)
    print(f"已輸出到 {out}")


def main():
    # 輸出被導到檔案或管線時，英文版 Windows 用 cp1252 編碼，印中文會整批中斷（例如付費跑到一半的 eval-translator）；
    # 印不出的字改成「?」，結果檔不受影響
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Idea2Alpha 選股策略庫")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def sel(p):
        p.add_argument("--strategies", default="")
        p.add_argument("--mode", default="all", choices=["all", "any", "vote", "weighted", "filter"])
        p.add_argument("--k", type=int, default=2)
        p.add_argument("--weights", default="")
        p.add_argument("--top-n", type=int, default=20)
        p.add_argument("--template", default="")
        p.add_argument("--json", default="")
        p.add_argument("--period", default="swing", choices=["short", "swing", "long"])

    p = sub.add_parser("selftest")
    p = sub.add_parser("fetch")
    p.add_argument("--top", type=int, default=400)
    p.add_argument("--stocks", default="")
    p.add_argument("--stocks-file", default="")
    p.add_argument("--holders", action="store_true", help="一併抓集保股權分散（需 FinMind 付費等級）")
    p = sub.add_parser("panel")
    p.add_argument("--period", default="short,swing,long")
    p.add_argument("--stocks", default="")
    p.add_argument("--stocks-file", default="")
    p.add_argument("--start", default="2017-01-01")
    p.add_argument("--daily-all", default="", help="實驗室全市場日線 parquet 資料夾（動態股票池用）")
    p.add_argument("--events-file", default="", help="實驗室全市場除權息檔 tw_dividend_events.csv")
    p.add_argument("--holders", action="store_true")
    p = sub.add_parser("screen"); sel(p)
    p.add_argument("--date", default=None)
    p.add_argument("--show", type=int, default=30)
    p = sub.add_parser("backtest"); sel(p)
    p.add_argument("--start", default="2019-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--random", type=int, default=200)
    p.add_argument("--hold", type=int, default=None, help="最短持有交易日（預設依週期：5／20／60）")
    p.add_argument("--ablation", action="store_true", help="條件不到 6 個也做「逐一拿掉條件」檢查")
    p = sub.add_parser("arena")
    p.add_argument("--period", default="swing", choices=["short", "swing", "long"])
    p.add_argument("--strategies", default="")
    p.add_argument("--start", default="2019-01-01")
    p.add_argument("--random", type=int, default=50)
    p.add_argument("--l4", action="store_true", help="每個策略加跑 L4 濾網開的版本")
    p = sub.add_parser("stats"); sel(p)
    p = sub.add_parser("overlap")
    p.add_argument("--strategies", required=True)
    p.add_argument("--period", default="swing", choices=["short", "swing", "long"])
    p = sub.add_parser("gaf")
    p.add_argument("--period", default="swing", choices=["short", "swing", "long"])
    p.add_argument("--train-end", default="2023-12-31")
    p = sub.add_parser("validate")
    p.add_argument("--json", required=True)
    p = sub.add_parser("translate")
    p.add_argument("idea")
    p = sub.add_parser("eval-translator")
    p.add_argument("--modes", default="direct,dict,pipeline")
    p.add_argument("--ids", default="")
    p.add_argument("--period", default="swing", choices=["short", "swing", "long"])
    p = sub.add_parser("facts")
    p.add_argument("--stock", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--period", default="swing", choices=["short", "swing", "long"])
    p.add_argument("--hide-name", action="store_true")
    p = sub.add_parser("debate")
    p.add_argument("--stock", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--thesis", default="")
    p.add_argument("--period", default="swing", choices=["short", "swing", "long"])
    p.add_argument("--hide-name", action="store_true")
    p = sub.add_parser("explain")
    p.add_argument("--stock", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--rule", default="", help="JSON：{rule}，列出符合的條件")
    p.add_argument("--period", default="swing", choices=["short", "swing", "long"])
    p.add_argument("--hide-name", action="store_true")
    p = sub.add_parser("replay")
    p.add_argument("--rule", default="", help="JSON：{title, rule, failure_condition}")
    p.add_argument("--start", default="")
    p.add_argument("--end", default="")
    p.add_argument("--reveal", default="", help="鎖定檔路徑；給了就執行揭露")
    p.add_argument("--period", default="swing", choices=["short", "swing", "long"])
    p = sub.add_parser("figures")
    p.add_argument("--fig3", default="", help="runs/translator_eval_*.jsonl（需含 F01、C01 的 pipeline 紀錄）")
    p.add_argument("--fig4", default="", help="runs/debate_*.json")
    p.add_argument("--fig5", default="", help="runs/fig5_run_record.json")
    p.add_argument("--fig6", default="", help="runs/replay_result_*.json")
    p.add_argument("--idea-en", default="Low-priced stocks that just broke above MA20 with a volume surge, but avoid one-day rallies")
    p.add_argument("--bare", action="store_true", help="另外輸出不含圖內標題與註記的圖 5、圖 6（*_bare，放進英文稿用，說明改寫在圖說）")
    a = ap.parse_args()
    globals()[f"cmd_{a.cmd.replace('-', '_')}"](a)


if __name__ == "__main__":
    main()
