"""
debate — 解說員（M3）與多方、空方、裁判（M4）。三個角色分開呼叫，都只拿到同一份事實表。

  python -m idea2alpha.cli debate --stock 2344 --date 2026-09-30 --period swing --hide-name

輸出 runs/debate_<股票>_<日期>_<時間>.json：事實表、三個角色的原始輸出、每句理由的檢查結果、模型與提示詞版本。
多方、空方的 failure_condition 與裁判的 invalidation 都必須是可執行的條件 JSON：程式用翻譯員同一套 validate 檢查
（因子白名單、運算子、數值範圍），而且只能用事實表上的因子；格式不合格（例如規則寫成文字、句尾多出字元）就把問題
回饋給模型修正一次，第一次被攔下的問題留在紀錄的 format_checks 裡。
注意：三個角色是同一個模型分開生成，不等於取得了三份獨立的外部證據。
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pandas as pd

from . import claims as C
from . import engine as E
from . import facts as FA
from . import llm

ROOT = Path(__file__).resolve().parent.parent
COND_KEY = {"bull_v1": "failure_condition", "bear_v1": "failure_condition", "judge_v1": "invalidation"}
# 句尾（。！？或引用括號）之後又多出 1–3 個字，例如「…[F03, F06]。廠」
TEXT_TAIL = re.compile(r"[。！？\]）)]\s*([^\s。！？\]）)」』，,.、]{1,3})\s*$")


def allowed_factors(panel, stock_id, sheet, fd) -> list[dict]:
    """失效條件可用的因子：事實表上、也在因子字典裡的因子，附字典單位與當天的值（字典單位，不是事實表的顯示單位）"""
    row = panel.loc[(pd.Timestamp(sheet["as_of"]), stock_id)]
    out = []
    for x in sheet["facts"]:
        s = x["source"]
        if s in fd and s in row.index and pd.notna(row[s]) and all(a["factor"] != s for a in out):
            out.append({"factor": s, "name": fd[s]["name"], "unit": fd[s].get("unit", ""), "now": float(row[s])})
    return out


def _allowed_text(allowed: list[dict]) -> str:
    # 不用「- 」條列：模型曾把條列符號抄進 JSON（"- \"rules\": [" 導致無法解析）
    return ("\n\n失效條件可用的因子（條件 JSON 只能用這些名稱，數值用這裡的單位；理由與分歧的文字仍照事實表的數字與單位）：\n"
            + "\n".join(f"{a['factor']}：{a['name']}；單位 {a['unit']}；目前 {a['now']:.4g}" for a in allowed))


def _leaves(node):
    if not isinstance(node, dict):
        return []
    if "factor" in node or "strategy" in node:
        return [node]
    return [x for r in node.get("rules", []) for x in _leaves(r)]


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_role(name: str, out: dict, parsed: bool, allowed: list[dict], fd: dict, lib: dict) -> list[dict]:
    """角色輸出的格式檢查：理由清單、句尾多餘字元、失效條件是不是只用事實表因子的可執行條件 JSON"""
    issues = []
    add = lambda code, msg: issues.append({"level": "error", "code": code, "message": msg})  # noqa: E731
    if not parsed:
        add("SCHEMA", "輸出不是合法 JSON")
        return issues
    if name in ("bull_v1", "bear_v1"):
        claims = out.get("claims")
        if not isinstance(claims, list) or not claims:
            add("SCHEMA", "缺少 claims（理由清單）")
        for i, c in enumerate(claims if isinstance(claims, list) else [], 1):
            t = c.get("text", "") if isinstance(c, dict) else ""
            if not t:
                add("SCHEMA", f"第 {i} 個理由沒有 text")
            m = TEXT_TAIL.search(t)
            if m:
                add("TEXT_TAIL", f"第 {i} 個理由在句尾之後多了「{m.group(1)}」，請刪除")
    else:
        for k in ("disagreements", "insufficient"):
            if not isinstance(out.get(k), list):
                add("SCHEMA", f"缺少 {k}（清單）")
    key = COND_KEY[name]
    fc = out.get(key)
    if not isinstance(fc, dict) or not isinstance(fc.get("rules"), list) or not fc["rules"]:
        add("SCHEMA", f'{key} 必須是條件 JSON：{{"logic": "any", "rules": [...], "horizon_days": 5}}，而且至少有一條規則')
        return issues
    for r in fc["rules"]:
        if not isinstance(r, dict):
            add("SCHEMA", f"{key} 的規則要寫成條件物件，不能寫成文字：「{str(r)[:40]}」")
    if fc.get("logic", "all") not in ("any", "all"):
        add("SCHEMA", f"{key} 的 logic 只能是 any 或 all（目前是 {fc.get('logic')}）")
    hz = fc.get("horizon_days")
    if not (isinstance(hz, int) and not isinstance(hz, bool) and hz > 0):
        add("SCHEMA", f"{key} 缺少 horizon_days（正整數，交易日）")
    node = {"logic": "all", "rules": [r for r in fc["rules"] if isinstance(r, dict)]}
    leaves = _leaves(node)
    for leaf in leaves:
        v = leaf.get("value")
        if v is not None and not (_is_num(v) or (isinstance(v, list) and v and all(_is_num(x) for x in v))):
            add("SCHEMA", f"{key}：{leaf.get('factor')} 的 value 必須是數字（目前是 {v!r}）")
    if not any(i["code"] == "SCHEMA" and "value 必須是數字" in i["message"] for i in issues):
        for msg in E.validate(node, fd, lib):
            add("DICT", f"{key}：{msg}")
    names = {a["factor"] for a in allowed}
    for leaf in leaves:
        if "strategy" in leaf:
            add("SCHEMA", f"{key} 只能用因子條件，不能引用策略 {leaf['strategy']}")
            continue
        for f in (leaf.get("factor"), leaf.get("ref")):
            if f and f in fd and f not in names:
                add("NOT_IN_SHEET", f"{key}：{f} 不在「失效條件可用的因子」裡")
        if "value" not in leaf and "ref" not in leaf:
            add("SCHEMA", f"{key}：{leaf.get('factor')} 缺少 value 或 ref")
    return issues


def _ask(system, msgs, ver, tag):
    rec = llm.chat(system, msgs, ver, tag=tag)
    try:
        out = llm.parse_json(rec["text"])
        if isinstance(out, dict):
            return out, rec, True
        return {"error": "回應不是 JSON 物件", "claims": []}, rec, False
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "claims": []}, rec, False


def _role(name, content, allowed, fd, lib):
    """呼叫一個角色；格式檢查不過就把問題回饋給模型、修正一次（同翻譯員的做法）"""
    system, ver = llm.load_prompt(name)
    msgs = [{"role": "user", "content": content}]
    out, rec, ok = _ask(system, msgs, ver, f"debate:{name}")
    recs = [rec]
    issues = check_role(name, out, ok, allowed, fd, lib)
    fmt = {"repaired": False, "intercepted": [], "issues": issues}
    if issues:
        fmt.update(repaired=True, intercepted=issues, first_output=out)
        msgs += [{"role": "assistant", "content": rec["text"]},
                 {"role": "user", "content": "程式檢查發現以下問題，請修正後重新輸出完整 JSON：\n"
                  + "\n".join(f"- [{i['code']}] {i['message']}" for i in issues)}]
        out, rec, ok = _ask(system, msgs, ver, f"debate:{name}:repair")
        recs.append(rec)
        fmt["issues"] = check_role(name, out, ok, allowed, fd, lib)
    return out, recs, fmt


def run(panel, stock_id, date, thesis: str = "", hide_name=True) -> dict:
    sheet = FA.build(panel, stock_id, date, hide_name=hide_name)
    fd, lib = E.load_factor_dict(), E.load_library()
    allowed = allowed_factors(panel, stock_id, sheet, fd)
    text = FA.to_text(sheet) + (f"\n\n使用者的論點：{thesis}" if thesis else "")
    tail = _allowed_text(allowed)
    bull, rb, fb = _role("bull_v1", text + tail, allowed, fd, lib)
    bear, rr, fr = _role("bear_v1", text + tail, allowed, fd, lib)
    cb, cr = C.check_all(bull.get("claims", []), sheet), C.check_all(bear.get("claims", []), sheet)
    extra = "\n\n多方理由與檢查：\n" + json.dumps(cb, ensure_ascii=False) + "\n\n空方理由與檢查：\n" + json.dumps(cr, ensure_ascii=False)
    judge, rj, fj = _role("judge_v1", text + extra + tail, allowed, fd, lib)
    out = {"stock_id": stock_id if not hide_name else "（已遮蔽）", "as_of": sheet["as_of"], "fact_sheet": sheet,
           "thesis": thesis, "bull": bull, "bear": bear, "judge": judge, "bull_checks": cb, "bear_checks": cr,
           "allowed_factors": allowed, "format_checks": {"bull_v1": fb, "bear_v1": fr, "judge_v1": fj},
           "models": {r["tag"]: {"provider": r["provider"], "model": r["model"], "prompt_version": r["prompt_version"],
                                 "created": r["created"]} for r in rb + rr + rj}}
    (ROOT / "runs").mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    f = ROOT / "runs" / f"debate_{stock_id}_{sheet['as_of']}_{stamp}.json"
    f.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    out["_file"] = str(f)
    return out


def explain(panel, stock_id, date, rule: dict | None = None, hide_name=True) -> dict:
    """解說員：三句白話說明為什麼符合條件，每句都經過論點檢查"""
    sheet = FA.build(panel, stock_id, date, hide_name=hide_name)
    conds = ""
    if rule:
        conds = "\n\n符合的條件：\n" + "\n".join(f"- {r.get('label') or r.get('factor') or r.get('strategy')}"
                                                for r in rule.get("rules", []))
    system, ver = llm.load_prompt("explain_v1")
    rec = llm.chat(system, [{"role": "user", "content": FA.to_text(sheet) + conds}], ver, tag="explain")
    try:
        out = llm.parse_json(rec["text"])
    except Exception as e:  # noqa: BLE001
        out = {"error": str(e), "sentences": []}
    res = {"as_of": sheet["as_of"], "fact_sheet": sheet, "rule": rule, "explain": out,
           "checks": C.check_all(out.get("sentences", []), sheet),
           "model": {"provider": rec["provider"], "model": rec["model"], "prompt_version": ver, "created": rec["created"]}}
    (ROOT / "runs").mkdir(exist_ok=True)
    f = ROOT / "runs" / f"explain_{stock_id}_{sheet['as_of']}_{dt.datetime.now():%Y%m%d_%H%M%S}.json"
    f.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    res["_file"] = str(f)
    return res
