"""
translate — 翻譯員（M1）：口語選股想法 → 澄清 → 條件 JSON → 檢查

三種模式（第 3.5 節的比較用同一個模型、同一批題目）：
  direct    直接翻譯：不給因子字典、不可提問、不檢查
  dict      只給字典：可用因子名稱，但不可提問、不檢查
  pipeline  完整流程：字典＋澄清（最多 2 輪）＋檢查，檢查不過時把錯誤回饋給模型修一次

檢查分兩層：
  語法層  JSON 格式、因子白名單、運算子、合理範圍（engine.validate）
  語意層  程式能判斷的幾種「語法對但意思錯」：
          EVENT_AS_STATE   原句講「剛突破／跌破月線」，條件卻寫成「在月線之上」
          FUTURE_AS_FILTER 原句講未來結果（一日行情、會漲），卻沒有失效條件也沒標為未翻譯
          VAGUE_NO_ASK     原句有沒給數字的模糊詞，模型沒問就自己定門檻
          ADVICE           使用者要推薦或預測，模型卻產生了條件
          UNIT_YI          原句用「億」，金額因子的數值卻沒換算成元
          PCT_SCALE        百分位因子（0～1）被填成百分比數字
          EMPTY_RULE       status 是 rule 但沒有任何條件
程式抓不到的錯誤（例如門檻選錯但都合法）要靠測試集的人工答案判定。
"""
from __future__ import annotations

import json
import re

from . import engine as E
from . import llm

CROSS_WORDS = re.compile(r"剛突破|剛站上|突破|站上.*(?:當天|今天)|穿越|跌破|黃金交叉|死亡交叉|cross|break(?:s|ing)? above")
MA20_WORDS = re.compile(r"月線|MA\s*20|20\s*日(?:均)?線|20\s*日均線", re.I)
FUTURE_WORDS = re.compile(r"一日行情|會漲|會跌|之後|隔天|明天|下個月|未來|繼續漲|跌回去|撐得住|will|next")
ADVICE_WORDS = re.compile(r"推薦|該買|買哪|目標價|會漲嗎|明天會|哪幾?檔會|報明牌")
VAGUE = {"低價": "close_raw", "便宜": None, "放量": "vol_ratio20", "大量": "vol_ratio20", "強勢": None, "成長很快": None,
         "高殖利率": "dividend_yield", "大型股": "mktcap", "小型股": "mktcap", "體質好": None, "熱絡": None, "跌深": None}
EVENT_FACTORS = {"cross_up_ma20", "cross_down_ma20", "kd_cross_up_low", "macd_turn_pos", "willr_cross_up_80",
                 "bb_break_up", "darvas_break", "inst_turn_buy"}
STATE_MA_FACTORS = {"above_ma20", "close_p", "adj_close", "ma20", "ma_s", "ma_m", "ma_l", "days_above_ma20"}
MONEY_FACTORS = {"turnover20", "mktcap", "rev_m", "fcf_ttm"}
PCT_FACTORS = {k for k in ["mom_12_1_pct", "rs_pct", "vol_1y_pct", "var5_rank", "pbr_pct", "gaf_up_pct"]}


def factor_table(fd: dict) -> str:
    rows = []
    for k, v in fd.items():
        lo, hi = v.get("range", [None, None])
        rows.append(f"{k}｜{v['name']}｜{v['unit']}｜[{'' if lo is None else lo}, {'' if hi is None else hi}]")
    return "\n".join(rows)


def strategy_table(lib: dict) -> str:
    return "\n".join(f"{k}｜{v['name']}｜{v.get('source', '')}" for k, v in lib.items())


def system_prompt(mode: str) -> tuple[str, str]:
    name = {"pipeline": "translator_v1", "dict": "translator_dict_v1", "direct": "translator_direct_v1"}[mode]
    text, ver = llm.load_prompt(name)
    text = text.replace("{FACTORS}", factor_table(E.load_factor_dict())).replace("{STRATEGIES}", strategy_table(E.load_library()))
    return text, ver


# ---------------------------------------------------------------------------
# 檢查
# ---------------------------------------------------------------------------


def _leaves(node):
    if not node or not isinstance(node, dict):  # 規則寫成文字等格式錯誤：沒有可執行的條件
        return []
    if "factor" in node or "strategy" in node:
        return [node]
    out = []
    kids = [k for key in ("rules", "filters") for k in (node.get(key) if isinstance(node.get(key), list) else [])]
    for k in kids + ([node["main"]] if "main" in node else []):
        out += _leaves(k)
    return out


LOGICS = {"all", "any", "vote", "weighted", "filter"}


def _logic_issues(node, where="") -> list[str]:
    """組合節點的結構檢查。engine.validate 只看葉節點（因子、運算子、數值），不看 logic；
    11-20 評估裡 H02 的 "vote2" 因此通過檢查、到執行才失敗。檢查放在這裡而不是 engine.py，
    因為 engine.py 在圖 5 的程式雜湊裡。"""
    if not isinstance(node, dict) or "factor" in node or "strategy" in node:
        return []
    msgs, logic = [], node.get("logic", "all")
    if logic not in LOGICS:
        hint = "；「至少 k 個成立」請寫 \"logic\": \"vote\" 加 \"k\": k" if str(logic).startswith("vote") else ""
        msgs.append(f"{where}logic 只能是 all / any / vote / weighted / filter，收到 {logic!r}{hint}")
    if logic == "filter":
        if not isinstance(node.get("main"), dict):
            msgs.append(f"{where}logic 是 filter 時要有 main（條件物件）")
        kids = (node.get("filters") if isinstance(node.get("filters"), list) else []) + \
               ([node["main"]] if isinstance(node.get("main"), dict) else [])
    else:
        kids = node.get("rules") if isinstance(node.get("rules"), list) else []
        if "rules" in node and not isinstance(node.get("rules"), list):
            msgs.append(f"{where}rules 必須是清單")
    if logic == "vote":
        k = node.get("k")
        if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= max(len(kids), 1):
            msgs.append(f"{where}logic 是 vote 時，k 必須是 1 到 {len(kids)} 的整數，收到 {k!r}")
    for kid in kids:
        msgs += _logic_issues(kid, where)
    return msgs


def check(out: dict, idea: str, asked: bool, fd: dict | None = None, lib: dict | None = None) -> list[dict]:
    """檢查翻譯員輸出。格式怪異的輸出（例如規則寫成文字）也要變成檢查結果，不能讓整批評估中斷。"""
    try:
        return _check(out, idea, asked, fd, lib)
    except Exception as e:  # noqa: BLE001
        return [{"level": "error", "code": "SCHEMA", "message": f"輸出結構不符合格式（{type(e).__name__}：{e}）"}]


def _check(out: dict, idea: str, asked: bool, fd: dict | None = None, lib: dict | None = None) -> list[dict]:
    fd = fd or E.load_factor_dict()
    lib = lib or E.load_library()
    issues = []
    add = lambda lvl, code, msg: issues.append({"level": lvl, "code": code, "message": msg})
    st = out.get("status")
    if st not in ("clarify", "rule", "refuse"):
        add("error", "SCHEMA", f"status 不合法：{st}")
        return issues
    rule, fail = out.get("rule"), out.get("failure_condition")
    raw_untr = out.get("untranslated") or []
    raw_untr = raw_untr if isinstance(raw_untr, list) else [raw_untr]
    if any(not isinstance(u, dict) for u in raw_untr):  # 比較組的提示詞沒有規定項目格式；只記錄，不算錯
        add("warning", "UNTR_FORMAT", "untranslated 的項目應該是 {phrase, reason} 物件；文字項目已當作片語")
    untr = " ".join(u.get("phrase", "") if isinstance(u, dict) else str(u) for u in raw_untr)
    if ADVICE_WORDS.search(idea) and st == "rule" and _leaves(rule):
        add("error", "ADVICE", "使用者要求推薦或預測，模型卻產生了選股條件")
    if st != "rule":
        return issues
    if rule is not None and not isinstance(rule, dict):
        add("error", "SCHEMA", "status 是 rule，但 rule 不是條件 JSON 物件")
        return issues
    if fail and not isinstance(fail, dict):
        add("error", "SCHEMA", "failure_condition 必須是條件 JSON 物件或 null（不能寫成文字）")
        fail = None
    leaves = _leaves(rule)
    if not leaves and not untr:
        add("error", "EMPTY_RULE", "status 是 rule 但沒有任何條件")
    for msg in E.validate(rule or {"rules": []}, fd, lib):
        add("error", "DICT", msg)
    for msg in _logic_issues(rule):
        add("error", "LOGIC", msg)
    if fail:
        for msg in E.validate(fail, fd, lib):
            add("error", "DICT", f"失效條件：{msg}")
        for msg in _logic_issues(fail, "失效條件："):
            add("error", "LOGIC", msg)
        if not isinstance(fail.get("horizon_days"), int) or fail.get("horizon_days", 0) <= 0:
            add("error", "SCHEMA", "失效條件缺少 horizon_days（正整數）")
    facs = {x.get("factor") for x in leaves} | {x.get("ref") for x in leaves}
    if CROSS_WORDS.search(idea) and MA20_WORDS.search(idea) and not (facs & EVENT_FACTORS) and "月線" not in untr:
        if facs & STATE_MA_FACTORS:
            add("error", "EVENT_AS_STATE", "原句是「剛突破／跌破月線」（事件），條件卻寫成「在月線之上」（狀態）；應使用 cross_up_ma20 或 cross_down_ma20")
    if FUTURE_WORDS.search(idea) and not fail and not untr:
        add("error", "FUTURE_AS_FILTER", "原句提到未來結果，但沒有寫成失效條件，也沒有標為未翻譯")
    if not asked:
        for w in VAGUE:
            if w in idea and not re.search(r"\d", idea):
                add("warning", "VAGUE_NO_ASK", f"「{w}」沒有數字，模型沒有先問就自己定了門檻")
    if "億" in idea:
        for x in leaves:
            if x.get("factor") in MONEY_FACTORS and isinstance(x.get("value"), (int, float)) and abs(x["value"]) < 1e7:
                add("error", "UNIT_YI", f"{x['factor']} 的數值 {x['value']} 看起來沒有把「億」換算成元")
    for x in leaves:
        v = x.get("value")
        if x.get("factor") in PCT_FACTORS and isinstance(v, (int, float)) and v > 1:
            add("error", "PCT_SCALE", f"{x['factor']} 是 0～1 的百分位，卻填了 {v}")
    return issues


# ---------------------------------------------------------------------------
# 執行
# ---------------------------------------------------------------------------


def translate(idea: str, mode: str = "pipeline", answer_fn=None, max_rounds: int = 2, tag: str = "") -> dict:
    """
    answer_fn(questions) -> str：回答澄清問題（互動時問使用者；評估時用測試題預設的回答）
    回傳完整紀錄：每一輪的模型輸出、使用者回答、檢查結果、最終條件
    """
    system, ver = system_prompt(mode)
    msgs = [{"role": "user", "content": f"選股想法：{idea}"}]
    log = {"idea": idea, "mode": mode, "prompt_version": ver, "turns": [], "asked": False}
    out = None
    for _ in range(max_rounds + 2):
        rec = llm.chat(system, msgs, ver, tag=tag or f"translate:{mode}")
        try:
            out = llm.parse_json(rec["text"])
            if not isinstance(out, dict):
                out = {"status": "invalid_json", "error": "回應不是 JSON 物件"}
        except Exception as e:  # noqa: BLE001
            out = {"status": "invalid_json", "error": str(e)}
        log["turns"].append({"model_output": out, "raw": rec["text"], "model": rec["model"],
                             "provider": rec["provider"], "created": rec["created"]})
        log["model"], log["provider"] = rec["model"], rec["provider"]
        msgs.append({"role": "assistant", "content": rec["text"]})
        if out.get("status") == "clarify" and mode == "pipeline" and answer_fn and len(log["turns"]) <= max_rounds:
            log["asked"] = True
            ans = answer_fn(out.get("questions", []))
            log["turns"][-1]["user_answer"] = ans
            msgs.append({"role": "user", "content": f"我的回答：{ans}\n請輸出最終條件（status = rule）。"})
            continue
        break
    if out.get("status") == "invalid_json":
        log["final"] = out
        log["issues"] = [{"level": "error", "code": "SCHEMA", "message": "模型輸出不是合法 JSON"}]
        return log
    issues = check(out, idea, log["asked"]) if mode == "pipeline" else []
    if mode == "pipeline" and any(i["level"] == "error" for i in issues):
        # 把檢查結果回饋給模型修一次；第一次被攔下的錯誤保留在紀錄裡（圖 3 的「攔截」例子）
        log["intercepted"] = issues
        fb = "程式檢查發現以下問題，請修正後重新輸出完整 JSON：\n" + "\n".join(f"- [{i['code']}] {i['message']}" for i in issues)
        msgs.append({"role": "user", "content": fb})
        rec = llm.chat(system, msgs, ver, tag=tag or "translate:repair")
        try:
            out = llm.parse_json(rec["text"])
            if not isinstance(out, dict):
                out = {"status": "invalid_json", "error": "回應不是 JSON 物件"}
        except Exception as e:  # noqa: BLE001
            out = {"status": "invalid_json", "error": str(e)}
        log["turns"].append({"model_output": out, "raw": rec["text"], "repair": True, "model": rec["model"],
                             "provider": rec["provider"], "created": rec["created"]})
        issues = check(out, idea, log["asked"]) if out.get("status") != "invalid_json" else [
            {"level": "error", "code": "SCHEMA", "message": "修正後仍不是合法 JSON"}]
    elif mode != "pipeline":
        # 比較組也用同一套檢查「記錄」問題，但不回饋、不攔截
        issues = check(out, idea, False)
    log["final"] = out
    log["issues"] = issues
    return log


def interactive(idea: str) -> dict:
    """命令列互動：模型提問時在終端機回答"""
    def ask(questions):
        lines = []
        for q in questions:
            print(f"\n{q.get('text')}")
            for o in q.get("options", []):
                print(f"  ({o.get('id')}) {o.get('label')}")
            lines.append(f"{q.get('about', q.get('id'))}：{input('你的選擇或說明 > ').strip()}")
        return "；".join(lines)
    return translate(idea, "pipeline", ask)
