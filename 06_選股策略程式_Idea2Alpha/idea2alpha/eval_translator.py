"""
eval_translator — 翻譯員驗收（表 9 第一列、第 3.5 節）

  python -m idea2alpha.cli eval-translator --modes direct,dict,pipeline        （預設 --period swing）

需要：模型設定與 API 金鑰（見 llm.py），以及真實資料的因子面板 output/panel_swing.parquet（跟老師拿，或跑 fetch、panel）。
判定日 = 面板中每年 6 月最後一個交易日，取最近 6 個（事先指定的規則，與評估結果無關）。
輸出：runs/translator_eval_<時間>.jsonl（逐題完整紀錄）、.csv（逐題判定）、_summary.json（各做法的結果分類計數）。

判定方式：
  1. 預期拒答（G 類）     模型 status = refuse，或沒有產生任何條件
  2. 預期無法翻譯（E 類） 沒有產生條件，而且 untranslated 有寫原因
  3. 預期條件             (a) 通過字典檢查；(b) 與標準答案「執行結果相同」：在固定的判定日，兩者選出的股票完全一樣；
                          標準答案在這些日子都選不到股票（例如面板沒有該資料）時，改比對正規化後的條件結構；
                          (c) 有標準失效條件時，失效條件也要相同、天數一致；(d) 有應標未翻譯片語時，untranslated 不可為空
結果分類：
  correct      正確
  blocked      完整流程的檢查沒有放行（安全，但算錯）
  wrong_valid  通過檢查卻與答案不同（最需要注意：程式攔不到的錯）
  invalid      比較組產生了不合法的條件（實際上無法執行）
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

from . import engine as E
from . import translate as TR

ROOT = Path(__file__).resolve().parent.parent
BIN = {"0/1"}


def scripted_answer(item):
    """用測試題預設的「使用者回答」回應澄清問題；沒有對應時請模型自行說明假設"""
    ans = item.get("answers", {})

    def fn(questions):
        out, used = [], set()
        for q in questions:
            blob = f"{q.get('about', '')} {q.get('text', '')}"
            hit = [(k, v) for k, v in ans.items() if k in blob]
            used.update(k for k, _ in hit)
            reply = "，".join(v for _, v in hit) if hit else "沒有特別要求，請用你的建議並寫進 assumptions"
            out.append(f"{q.get('about') or q.get('id') or '問題'}：{reply}")
        # 沒被問到的預設回答也一併附上，避免模型漏問後無從得知（舊版只有這行註解，沒有真的附上）
        out += [f"{k}：{v}" for k, v in ans.items() if k not in used]
        return "；".join(out)
    return fn


def _canon_leaf(x, fd):
    if "strategy" in x:
        return ("S", x["strategy"])
    f, op, v = x.get("factor"), x.get("op"), x.get("value")
    if "ref" in x:
        return ("R", f, op.replace("=", ""), x["ref"])
    if fd.get(f, {}).get("unit") in BIN:
        one = (op in ("==",) and v == 1) or (op in (">=",) and v == 1) or (op == ">" and v == 0) or (op == "!=" and v == 0)
        return ("B", f, 1 if one else 0)
    if op == "between":
        return ("N", f, "between", tuple(float(a) for a in v))
    return ("N", f, op.replace("=", ""), float(v))


def canonical(node, fd):
    if not node:
        return None
    if "strategy" in node or "factor" in node:
        node = {"logic": "all", "rules": [node]}
    leaves = []
    for r in node.get("rules", []):
        if "between" == r.get("op"):
            leaves.append(_canon_leaf(r, fd))
        elif "logic" in r:
            leaves.append(("SUB", canonical(r, fd)))
        else:
            leaves.append(_canon_leaf(r, fd))
    return (node.get("logic", "all"), node.get("k"), tuple(sorted(map(str, leaves))))


def _hits(ev, node, dates):
    h = ev.eval(node)[0]
    u = ev.df["in_universe"] == 1
    h = h & u
    return {d: set(h.xs(d, level="date").pipe(lambda s: s[s].index)) for d in dates}


def same_rule(gold, pred, ev, dates, fd) -> tuple[bool, str]:
    try:
        g = _hits(ev, gold, dates)
    except Exception:  # noqa: BLE001
        g = None
    if g and any(g.values()):
        try:
            p = _hits(ev, pred, dates)
        except Exception as e:  # noqa: BLE001
            return False, f"執行失敗：{e}"
        return all(g[d] == p[d] for d in dates), "執行結果比對"
    return canonical(gold, fd) == canonical(pred, fd), "條件結構比對"


def grade(item, log, ev, dates, fd) -> dict:
    """逐題判定。格式怪異的輸出也要有判定（invalid），不能讓整批評估中斷。"""
    try:
        return _grade(item, log, ev, dates, fd)
    except Exception as e:  # noqa: BLE001
        fin = log.get("final") if isinstance(log.get("final"), dict) else {}
        errs = [i for i in log.get("issues", []) if i.get("level") == "error"]
        return {"id": item["id"], "category": item["category"], "mode": log["mode"], "status": fin.get("status"),
                "asked": log.get("asked", False), "n_errors": len(errs),
                "error_codes": ",".join(sorted({e["code"] for e in errs})), "intercepted_codes": "",
                "outcome": "invalid", "how": f"輸出結構錯誤（{type(e).__name__}）"}


def _grade(item, log, ev, dates, fd) -> dict:
    fin = log.get("final", {}) or {}
    st = fin.get("status")
    leaves = TR._leaves(fin.get("rule"))
    errs = [i for i in log.get("issues", []) if i["level"] == "error"]
    untr = fin.get("untranslated") or []
    exp = item["expect_status"]
    r = {"id": item["id"], "category": item["category"], "mode": log["mode"], "status": st,
         "asked": log.get("asked", False), "n_errors": len(errs), "error_codes": ",".join(sorted({e["code"] for e in errs})),
         "intercepted_codes": ",".join(sorted({e["code"] for e in log.get("intercepted", []) if e["level"] == "error"}))}
    if exp == "refuse":
        ok = st == "refuse" or (st == "rule" and not leaves)
        r.update(outcome="correct" if ok else ("blocked" if errs and log["mode"] == "pipeline" else "wrong_valid"), how="拒答")
        return r
    if exp == "untranslatable":
        ok = not leaves and bool(untr)
        r.update(outcome="correct" if ok else ("blocked" if errs and log["mode"] == "pipeline" else "wrong_valid"), how="無法翻譯")
        return r
    if st != "rule" or not leaves:
        r.update(outcome="blocked" if log["mode"] == "pipeline" else "wrong_valid", how="沒有條件")
        return r
    hard = [e for e in errs if e["code"] in ("SCHEMA", "DICT", "EMPTY_RULE")]
    if log["mode"] == "pipeline" and errs:
        r.update(outcome="blocked", how="檢查未放行")
        return r
    if hard:  # 比較組：因子或格式不合法，實際無法執行
        r.update(outcome="invalid", how="無法執行")
        return r
    ok, how = same_rule(item["gold_rule"], fin["rule"], ev, dates, fd)
    if ok and item.get("gold_failure"):
        pf = fin.get("failure_condition")
        if not pf or pf.get("horizon_days") != item["gold_failure"]["horizon_days"]:
            ok, how = False, how + "；失效條件缺漏或天數不同"
        else:
            g = {k: v for k, v in item["gold_failure"].items() if k != "horizon_days"}
            p = {k: v for k, v in pf.items() if k != "horizon_days"}
            ok2, _ = same_rule(g, p, ev, dates, fd)
            ok, how = ok2, how + ("；失效條件相同" if ok2 else "；失效條件不同")
    if ok and item.get("expect_untranslated") and not untr:
        ok, how = False, how + "；應標示未翻譯片語"
    r.update(outcome="correct" if ok else "wrong_valid", how=how)
    return r


def run(panel: pd.DataFrame, modes=("direct", "dict", "pipeline"), dates=None, ids=None,
        testset=ROOT / "testset" / "translator_30.json") -> tuple[pd.DataFrame, dict]:
    items = json.loads(Path(testset).read_text(encoding="utf-8"))
    if ids:
        items = [i for i in items if i["id"] in ids]
    fd = E.load_factor_dict()
    alld = panel.index.get_level_values("date").unique().sort_values()
    if dates is None:  # 預先指定：資料中每年 6 月最後一個交易日（最多 6 天）
        s = pd.Series(alld, index=alld)
        dates = list(s[s.dt.month == 6].groupby(s[s.dt.month == 6].dt.year).max())[-6:]
    dates = [pd.Timestamp(d) for d in dates]
    ev = E.Evaluator(panel[panel.index.get_level_values("date").isin(dates)])  # 只留判定日，省記憶體
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "runs"
    out_dir.mkdir(exist_ok=True)
    rows = []
    with open(out_dir / f"translator_eval_{stamp}.jsonl", "w", encoding="utf-8") as fo:
        for mode in modes:
            for it in items:
                log = TR.translate(it["idea"], mode, scripted_answer(it), tag=f"eval:{mode}:{it['id']}")
                g = grade(it, log, ev, dates, fd)
                rows.append(g)
                fo.write(json.dumps({"item": it, "log": log, "grade": g}, ensure_ascii=False, default=str) + "\n")
                print(f"  {mode:8s} {it['id']} → {g['outcome']}（{g.get('how', '')}）", flush=True)
    df = pd.DataFrame(rows)
    summ = {"stamp": stamp, "dates": [str(pd.Timestamp(d).date()) for d in dates], "n_items": len(items),
            "model": rows and log.get("model"), "provider": rows and log.get("provider"),
            "by_mode": {m: df[df["mode"] == m]["outcome"].value_counts().to_dict() for m in modes}}
    df.to_csv(out_dir / f"translator_eval_{stamp}.csv", index=False, encoding="utf-8-sig")
    (out_dir / f"translator_eval_{stamp}_summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    return df, summ
