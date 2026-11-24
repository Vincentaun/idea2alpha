"""
figures — 報告圖 3–6，全部從紀錄檔產生（數字與文字不手打）

  python -m idea2alpha.cli figures --fig3 runs/translator_eval_XXXX.jsonl --fig4 runs/debate_XXXX.json \
         --fig5 runs/fig5_run_record.json --fig6 runs/replay_result_XXXX.json

沒有提供實際模型紀錄時，圖 3、圖 4 用 testset/illustrative/ 的示例紀錄，圖上自動加註
「Illustrative」；換成真實紀錄後重跑即可。視覺規格沿用 Visual Package：白底、深藍文字、
藍綠 = AI、赭色 = 檢查／待核實；字型 Times New Roman（沒有時用度量相容的 Liberation Serif）。

真實紀錄的內容不固定：模型可能沒有追問、C01 可能第一次就答對、理由可能是中文。圖會照實標示
（例如「這次沒有被攔下的解讀」），不會留白，也不會把通過檢查的條件畫成被攔下。
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NAVY, TEAL, AMBER, GRAY, LIGHT, LINE, RED = "#18334B", "#087E8B", "#AF6D13", "#5E6C78", "#F0F5F8", "#D4DEE5", "#A23B2A"
# 英文用 Times New Roman（或度量相容的 Liberation Serif），中文依序找 Linux 的 Noto CJK、新版 Noto Serif TC、
# Windows 內建的微軟正黑體與細明體。font.family 要給「已安裝的字型清單」，matplotlib 才會逐字改用下一個字型；
# 只給 "serif" 時它只挑一個字型，Windows 上會挑到 Times New Roman，中文全部變成方框。
_FONTS = ["Times New Roman", "Liberation Serif", "Noto Serif CJK TC", "Noto Sans CJK TC", "Noto Serif TC",
          "Microsoft JhengHei", "PMingLiU", "MingLiU", "DejaVu Serif"]
_installed = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
plt.rcParams.update({"font.family": [f for f in _FONTS if f in _installed] or ["serif"],
                     "font.size": 10.5, "svg.fonttype": "none", "pdf.fonttype": 42, "axes.unicode_minus": False})

EN = {"close_raw": "Close (NT$)", "cross_up_ma20": "Crossed above MA20 today", "cross_down_ma20": "Crossed below MA20 today",
      "above_ma20": "Close above MA20", "vol_ratio20": "Volume / prior 20-day avg", "days_above_ma20": "Days above MA20",
      "ret_1d": "1-day return (%)", "high_52w_dist": "Distance from 52-week high (%)", "rsi": "RSI", "PER": "P/E",
      "dividend_yield": "Dividend yield (%)", "mom_12_1_pct": "12-1 momentum percentile", "turnover20": "20-day avg traded value (NT$)"}
OPS = {">=": "≥", "<=": "≤", "==": "=", "!=": "≠", ">": ">", "<": "<"}


def _num(v) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)


def _items(node) -> list:
    """條件節點 → 要逐行列出的子條件。all（或只有一條的 any／vote）逐條列；其他組合整個節點寫成一行"""
    if not node:
        return []
    if "factor" in node or "strategy" in node:
        return [node]
    rules = node.get("rules", [])
    if node.get("logic", "all") == "all" or len(rules) == 1:
        return rules
    return [node]


def _rule_text(r: dict) -> str:
    if r.get("logic") == "filter":
        return f"{_rule_text(r.get('main', {}))}, excluding: " + "; ".join(_rule_text(x) for x in r.get("filters", []))
    if "logic" in r or "rules" in r:  # 巢狀組合（真實模型可能輸出 any／vote）
        head = {"all": "All of", "any": "Any of", "vote": f"At least {r.get('k', '?')} of",
                "weighted": "Weighted"}.get(r.get("logic", "all"), str(r.get("logic")))
        return f"{head}: (" + "; ".join(_rule_text(x) for x in r.get("rules", [])) + ")"
    if "strategy" in r:
        return f"Strategy {r['strategy']}"
    f, op, v = r.get("factor", "?"), r.get("op", "?"), r.get("value")
    name = EN.get(f, f)
    if op == "between" and isinstance(v, (list, tuple)) and len(v) == 2:
        return f"{name} between {_num(v[0])} and {_num(v[1])}"
    if f == "above_ma20" and op == "==" and v == 0:
        return "Close back below MA20"
    if v in (0, 1) and f in ("cross_up_ma20", "cross_down_ma20", "above_ma20"):
        return name if (op == "==" and v == 1) else f"Not: {name.lower()}"
    if "ref" in r:
        return f"{name} {OPS.get(op, op)} {EN.get(r['ref'], r['ref'])}"
    return f"{name} {OPS.get(op, op)} {_num(v)}"


def _canvas(w, h):
    f, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")
    f.subplots_adjust(0.01, 0.01, 0.99, 0.99)
    return f, ax


def _box(ax, x, y, w, h, label, title, color, edge=LINE):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.006,rounding_size=1.5", facecolor=LIGHT, edgecolor=edge, lw=1.1))
    ax.text(x + 2, y + h - 2.5, label, fontsize=11, color=color, fontweight="bold", va="top")
    ax.text(x + 6, y + h - 2.5, title, fontsize=11, color=color, fontweight="bold", va="top")


_NO_LINE_START = set("，。、；：！？）」』》〉】,.;:!?)")


def _cw(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def _lines(s, n: int) -> list[str]:
    """依顯示寬度換行：全形中文字算 2、英文算 1。英文在空白處斷行，中文可在字與字之間斷行，句讀不放行首。
    （textwrap 只數字元數，30 個中文字的寬度等於 60 個英文字母，中文會超出框外。）"""
    out = []
    for para in str(s).split("\n"):
        toks, buf = [], ""
        for ch in para:
            if ch.isspace() or _cw(ch) == 2:
                if buf:
                    toks.append(buf)
                    buf = ""
                toks.append(" " if ch.isspace() else ch)
            else:
                buf += ch
        if buf:
            toks.append(buf)
        line, w = "", 0
        for t in toks:
            if t == " ":
                if line:
                    line, w = line + " ", w + 1
                continue
            tw = sum(_cw(c) for c in t)
            if line.strip() and w + tw > n and t not in _NO_LINE_START:
                out.append(line.rstrip())
                line, w = t, tw
            else:
                line, w = line + t, w + tw
        out.append(line.rstrip())
    return out


def _wrap(s, n):
    return "\n".join(_lines(s, n))


def _first(s, n):
    """只留第一行；有截斷就加「…」，不悄悄丟字"""
    ls = _lines(s, n)
    return ls[0] + ("…" if len(ls) > 1 else "")


def _flow(ax, f, x, top, bottom, items, min_scale=0.7):
    """由上往下排文字，items = [(縮排, 文字, 字級, 顏色, 粗體, 下方留白)]。
    總高度超過框高就整體等比例縮小（字級最低 70%），行距跟著字級走，文字不會互相重疊。"""
    ppu = f.get_figheight() * 72 * 0.98 / 100  # 1 個座標單位 = 幾點（_canvas 的座標軸占圖高 98%）

    def height(it, s):
        return (it[1].count("\n") + 1) * it[2] * s * 1.25 / ppu + it[5] * s

    s = max(min_scale, _fit(sum(height(it, 1) for it in items), top - bottom))
    y = top
    for it in items:
        dx, text, size, color, bold, _ = it
        ax.text(x + dx, y, text, fontsize=size * s, color=color, fontweight="bold" if bold else "normal", va="top")
        y -= height(it, s)
    return y


def _stamp(f, illustrative: bool, source: str, top_right: bool = False):
    if illustrative:
        x, y, ha, va = (0.99, 0.985, "right", "top") if top_right else (0.03, 0.12, "left", "bottom")
        f.text(x, y, "ILLUSTRATIVE — test responses,\nnot logged model output", ha=ha, va=va, fontsize=9.5,
               color=RED, fontweight="bold")
    f.text(0.01, 0.003, source, fontsize=7.2, color=GRAY, va="bottom")


def _fit(need: float, room: float) -> float:
    """內容超出框高時的縮放比例（1 = 不縮）；字級最多縮到 80%，避免看不清楚"""
    return 1.0 if need <= room else room / need


def _save(f, out: Path, name: str):
    out.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf", "svg"):
        f.savefig(out / f"{name}.{ext}", dpi=300, facecolor="white")
    plt.close(f)


# ---------------------------------------------------------------------------
# 圖 3：想法 → 澄清 → 確認條件 → 被攔下的錯誤解讀
# ---------------------------------------------------------------------------


# 語意檢查代碼的英文說明（對應 translate.check 的判定，圖上依實際被攔下的代碼顯示，不寫死）
EXPLAIN = {
    "EVENT_AS_STATE": "A crossing event (“just crossed”) was written as a state (“is above”).",
    "FUTURE_AS_FILTER": "The idea mentions a future outcome, but it was neither written as a failure condition "
                        "nor marked as untranslated.",
    "ADVICE": "The user asked for a recommendation or forecast, but the model produced a screening rule.",
    "UNIT_YI": "An amount in 億 (100 million NT$) was not converted to NT$.",
    "PCT_SCALE": "A 0–1 percentile was given a value above 1.",
    "EMPTY_RULE": "The output was a rule with no conditions.",
    "DICT": "A factor, operator or value failed the factor-dictionary check.",
    "SCHEMA": "The output did not follow the required JSON format.",
}


def fig3(ok_log: dict, bad_log: dict, out: Path, idea_en: str, illustrative: bool, source: str,
         answer_note: str = "● = option the user confirmed"):
    f, ax = _canvas(8.2, 6.4)
    ax.text(1, 98.5, "Translating an ambiguous idea into user-confirmed conditions", fontsize=14, color=NAVY, fontweight="bold", va="top")
    # A
    _box(ax, 1, 74, 47, 19, "A", "User's idea", NAVY)
    ax.text(3, 85, _wrap(f"“{ok_log['idea']}”", 46), fontsize=10.5, color=NAVY, va="top")
    ax.text(3, 80, _wrap(f"“{idea_en}”", 52), fontsize=9.5, color=GRAY, va="top", style="italic")
    # B
    _box(ax, 52, 37, 47, 56, "B", "AI asks before choosing thresholds", TEAL)
    turns = ok_log.get("turns") or [{}]
    t0 = turns[0].get("model_output") or {}
    ans = turns[0].get("user_answer", "")
    qs = t0.get("questions", [])[:3] if t0.get("status") == "clarify" else []
    if not qs:  # 真實模型可能沒有追問就直接給條件：照實寫，不留白
        warn = sorted({i["code"] for i in ok_log.get("issues", []) if i["level"] == "warning"})
        ax.text(54, 87, _wrap("The model did not ask a clarifying question in this run; it chose the thresholds itself."
                              + (f" Checker warning: {', '.join(warn)}." if warn else ""), 56), fontsize=9, color=NAVY, va="top")
    # 「選了哪個選項」：選項裡的數字要和回答裡的數字完全相同才算。只比子集合會誤判，例如回答「15 到 30 元」
    # 會把選項「低於 30 元」標成已選。沒有選項符合時（或開放題），照實列出回答。
    nums = lambda t: set(re.findall(r"\d+(?:\.\d+)?", t))  # noqa: E731
    plan = []
    for q in qs:
        mine = [a_ for a_ in ans.split("；") if q.get("about", "") in a_]
        opts = q.get("options", [])[:3]
        flags = [bool(nums(o.get("label", ""))) and any(nums(o.get("label", "")) == nums(a_) for a_ in mine) for o in opts]
        own = mine[0].split("：", 1)[-1] if mine and not any(flags) else ""
        plan.append((q, opts, flags, own))
    # 真實模型可能給 3 題 × 3 個選項或較長的題目，先算總高度，超出 B 框就等比例縮小
    s = _fit(sum(4.2 * len(_lines(q.get("text", ""), 56)) + 3.7 * (max(1, len(opts)) + (1 if own and opts else 0)) + 1.8
                 for q, opts, flags, own in plan), 87 - 41)
    fs = max(s, 0.8)
    y = 87
    for q, opts, flags, own in plan:
        ax.text(54, y, _wrap(q.get("text", ""), 56), fontsize=9 * fs, color=NAVY, va="top")
        y -= 4.2 * s * len(_lines(q.get("text", ""), 56))
        for o, chosen in zip(opts, flags):
            ax.text(56, y, _first(("●  " if chosen else "○  ") + o.get("label", ""), 52), fontsize=8.8 * fs,
                    color=TEAL if chosen else GRAY, va="top", fontweight="bold" if chosen else "normal")
            y -= 3.7 * s
        if own:  # 開放題，或回答不是任何一個選項：列出回答
            ax.text(56, y, _first(("→  Answer: " if opts else "→  ") + own, 52), fontsize=8.8 * fs, color=TEAL,
                    va="top", fontweight="bold" if opts else "normal")
            y -= 3.7 * s
        y -= 1.8 * s
    if qs:
        ax.text(54, 39, answer_note, fontsize=8, color=GRAY, va="bottom")
    # C
    fin = ok_log.get("final") or {}
    _box(ax, 1, 25, 47, 45, "C", "Confirmed rule (executed by the engine)", NAVY)
    errs = [i for i in ok_log.get("issues", []) if i["level"] == "error"]
    rules = _items(fin.get("rule")) if fin.get("status") == "rule" else []
    items = []
    if rules and not errs:
        items += [(0, "+  " + _wrap(_rule_text(r), 50), 9.8, NAVY, False, 3.85) for r in rules]
        fc = fin.get("failure_condition")
        if fc:
            items.append((0, "Failure condition", 9.8, AMBER, True, 2.4))
            items.append((0, _wrap(f"Within {fc.get('horizon_days', '?')} trading days: "
                                   + "; ".join(_rule_text(r) for r in _items(fc)), 52), 9.5, NAVY, False, 0))
    else:  # 完整流程沒有放行：照實寫，不畫成已確認
        why = ", ".join(sorted({i["code"] for i in errs})) or str(fin.get("status", "no output"))
        items.append((0, _wrap(f"No rule passed the checks in this run ({why}).", 50), 9.8, RED, False, 0))
    meta = f"Model {ok_log.get('model', '?')} · prompt {ok_log.get('prompt_version', '?')}"
    if (ok_log.get("_grade") or {}).get("outcome"):
        meta += f" · graded: {ok_log['_grade']['outcome']}"
    _flow(ax, f, 3, 62, 28.5 + 2.6 * len(_lines(meta, 60)), items)  # 底下留給模型與提示詞版本那一兩行
    ax.text(3, 27, _wrap(meta, 60), fontsize=8, color=GRAY)
    # D：只有真的被攔下（有 intercepted 錯誤且有修正輪）才畫「×」；被攔下的是修正輪前一輪的輸出
    _box(ax, 52, 3, 47, 31, "D", "Rejected interpretation", AMBER, edge=AMBER)
    bturns = bad_log.get("turns") or []
    rep_i = next((i for i, t in enumerate(bturns) if t.get("repair")), None)
    codes = [i for i in bad_log.get("intercepted", []) if i["level"] == "error"]
    items = [(0, _wrap(f"Idea: “{bad_log['idea']}”", 50), 9.5, NAVY, False, 3.0)]
    if rep_i and codes:
        rejected = bturns[rep_i - 1].get("model_output") or {}
        items += [(0, "×  " + _wrap(_rule_text(r), 52), 9.8, RED, False, 2.4) for r in _items(rejected.get("rule"))[:3]]
        items.append((0, "Validator: " + ", ".join(dict.fromkeys(i["code"] for i in codes)), 9.5, AMBER, True, 1.5))
        items.append((0, _wrap(EXPLAIN.get(codes[0]["code"], "The output failed the program's checks.")
                               + " The error is returned to the model.", 58), 8.8, NAVY, False, 2.0))
        repaired = bturns[rep_i].get("model_output") or {}
        passed = not [i for i in bad_log.get("issues", []) if i["level"] == "error"]
        if repaired.get("rule"):
            items.append((0, _wrap(("Repaired:  " if passed else "Still rejected after repair:  ")
                                   + "; ".join(_rule_text(r) for r in _items(repaired["rule"])), 56), 9.5,
                          TEAL if passed else RED, False, 0))
    else:
        items.append((0, _wrap("Nothing was rejected in this run: the model's first rule passed the checks, "
                               "so there is no repair to show.", 58), 8.8, NAVY, False, 2.0))
        bfin = bad_log.get("final") or {}
        if bfin.get("rule"):
            items.append((0, _wrap("Accepted:  " + "; ".join(_rule_text(r) for r in _items(bfin["rule"])), 56), 9.5, TEAL, False, 0))
    _flow(ax, f, 54, 27, 4, items)
    _stamp(f, illustrative, source)
    _save(f, out, "Fig03_Translation")


# ---------------------------------------------------------------------------
# 圖 4：事實表 → 論點 → 檢查結果
# ---------------------------------------------------------------------------

VCOL = {"supported": TEAL, "insufficient": AMBER, "unsupported": RED, "invalid": RED}


def fig4(sheet: dict, bull: list, bear: list, out: Path, illustrative: bool, source: str, keep_ids=None):
    f, ax = _canvas(8.2, 6.0)
    ax.text(1, 98.5, "Tracing generated claims to one dated fact sheet", fontsize=14, color=NAVY, fontweight="bold", va="top")
    _box(ax, 1, 3, 36, 89, "", f"Fact sheet · {sheet['as_of']}", NAVY)
    facts = [x for x in sheet["facts"] if not keep_ids or x["id"] in keep_ids]
    total = len(facts)
    if keep_ids is None and total > 15:
        # 有財報、籌碼資料時事實表有 25 條，全列會擠成一團：只列論點引用到的，並在框底註明
        cited = {c for cl in (bull, bear) for x in cl for c in x.get("cites", [])}
        facts = [x for x in facts if x["id"] in cited] or facts[:15]
    # 行高 = 3.2 + 3.2 × 標籤行數（13 列的實驗室事實表與原版排法相同）；放不下就一起縮，行距縮到 60% 以下才縮字
    rows = [(x, _lines(x["label_en"], 30)) for x in facts]
    s = min(1.0, (84 - 10) / max(1.0, sum(3.2 + 3.2 * len(ls) for _, ls in rows)))
    fs = min(1.0, s / 0.6)
    y = 84
    for x, ls in rows:
        val = x["shown"] if x["unit"] != "是/否" else ("yes" if x["value"] == 1 else "no")
        unit = {"元": "NT$", "倍": "×", "日": "day" if x["value"] == 1 else "days", "名": "", "百分位": "pctl", "是/否": "", "%": "%"}.get(x["unit"], x["unit"])
        ax.text(3, y, x["id"], fontsize=9.3 * fs, color=TEAL, fontweight="bold", va="top")
        ax.text(9, y, "\n".join(ls), fontsize=9 * fs, color=NAVY, va="top")
        ax.text(35.5, y, f"{val} {unit}".strip(), fontsize=9 * fs, color=NAVY, va="top", ha="right")
        y -= (3.2 + 3.2 * len(ls)) * s
    if len(facts) < total:
        ax.text(3, 7.6, f"Cited facts only ({len(facts)} of {total}); full sheet in the log.", fontsize=7.6, color=GRAY)
    ax.text(3, 5, "Computed by the program; the AI only quotes it.", fontsize=8, color=GRAY)
    _box(ax, 39, 3, 60, 89, "", "Claims and automatic checks", NAVY)

    def verdict_text(c):
        rs = c.get("reasons_en") or c.get("reasons") or []
        return f"{c['verdict'].upper()}" + (f" — {rs[0]}" if rs else "")

    # 真實模型的論點可能比測試輸入長（而且多半是中文），先算總高度，超出右框就等比例縮小
    need = sum(5 + (sum(4.2 * len(_lines(c["text"], 62)) + 4.0 * len(_lines(verdict_text(c), 64)) + 2.2 for c in claims)
                    if claims else 6.2) + 1.5 for claims in (bull, bear))
    s = _fit(need, 84 - 6)
    fs = max(s, 0.8)
    y = 84
    for side, claims, col in (("Bull", bull, TEAL), ("Bear", bear, NAVY)):
        ax.text(41, y, side, fontsize=10.5 * fs, color=col, fontweight="bold", va="top")
        y -= 5 * s
        if not claims:  # 模型輸出無法解析時 claims 是空的：照實寫
            ax.text(41, y, "(no claims in the log)", fontsize=9 * fs, color=GRAY, va="top")
            y -= 6.2 * s
        for c in claims:
            ax.text(41, y, _wrap(c["text"], 62), fontsize=9 * fs, color=NAVY, va="top")
            y -= 4.2 * s * len(_lines(c["text"], 62))
            ax.text(43, y, _wrap(verdict_text(c), 64), fontsize=8.6 * fs, color=VCOL[c["verdict"]], fontweight="bold", va="top")
            y -= (4.0 * len(_lines(verdict_text(c), 64)) + 2.2) * s
        y -= 1.5 * s
    _stamp(f, illustrative, source, top_right=True)  # 左下角是事實表，示例標記改放右上角，不壓到內容
    _save(f, out, "Fig04_Evidence")


# ---------------------------------------------------------------------------
# 圖 5：報酬 vs 0050；夏普 vs 200 個隨機組合的分布
# ---------------------------------------------------------------------------


def benchmarks(rec: dict) -> list[tuple[str, float]]:
    """紀錄裡的基準（%）：0050 一定有；等權股票池有就一起畫。兩者是不同的基準，分開畫，不畫成區間。
    舊格式紀錄（只有 benchmark_0050）也能讀。"""
    if "benchmarks" in rec:
        bm = rec["benchmarks"]
        out = [("0050", bm["0050"]["annualized"] * 100)]
        if "equal_weight_universe" in bm:
            out.append(("Equal-weighted universe", bm["equal_weight_universe"]["annualized"] * 100))
        return out
    return [("0050", rec["benchmark_0050"]["annualized"] * 100)]


def fig5(rec: dict, out: Path, bare: bool = False):
    """bare=True：不畫圖內標題、副標與底注（放進文稿時由圖說交代），版面較矮，檔名加 _bare"""
    import numpy as np
    names = list(rec["strategies"])
    short = [n.split(" (")[0].replace("Technical resonance", "Technical resonance\n(≥2 of T5–T8)") for n in names]
    S = [rec["strategies"][n] for n in names]
    f, axes = plt.subplots(1, 2, figsize=(8.6, 3.7 if bare else 4.6), gridspec_kw={"width_ratios": [1, 1.25]})
    f.subplots_adjust(left=0.2, right=0.98, top=0.9 if bare else 0.8, bottom=0.16 if bare else 0.22, wspace=0.12)
    y = np.arange(len(names))
    cols = [AMBER if s["random"]["贏過隨機比例"] < 0.7 else TEAL for s in S]
    a = axes[0]
    a.barh(y, [s["full"]["年化報酬"] * 100 for s in S], color=cols, height=0.55, zorder=2)
    bms = benchmarks(rec)
    styles = [(NAVY, (0, (4, 3)), 1.3), (GRAY, (0, (1, 2)), 1.3)]
    for k, (lab, v) in enumerate(bms):
        col, ls, lw = styles[k]
        a.axvline(v, color=col, ls=ls, lw=lw, zorder=1)
        right = k == 0 or v >= max(x for _, x in bms)  # 數值較大的那條字放右邊，另一條放左邊，避免重疊或被切掉
        a.text(v + 0.5 if right else v - 0.5, -0.75, f"{lab}: {v:.1f}%", ha="left" if right else "right", va="center",
               fontsize=8.6, color=col, zorder=3,
               bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.0})
    b0 = min(v for _, v in bms)
    for i, s in enumerate(S):
        v = s["full"]["年化報酬"] * 100
        if v > b0 - 5:
            a.text(v - 0.6, i, f"{v:.1f}", va="center", ha="right", fontsize=9, color="white", fontweight="bold")
        else:
            a.text(v + 0.6, i, f"{v:.1f}", va="center", fontsize=9)
    a.set_yticks(y, short)
    a.set_xlim(0, 38)
    a.set_ylim(len(names) - 0.4, -1.1)
    a.set_xlabel("Annualized return (%)")
    a.set_title("A  Return vs. benchmarks", loc="left", fontsize=11, fontweight="bold", color=NAVY)
    b = axes[1]
    for i, s in enumerate(S):
        r = np.array(s["random_sharpes"])
        jitter = (np.random.default_rng(i).random(len(r)) - 0.5) * 0.36
        b.scatter(r, i + jitter, s=6, color=GRAY, alpha=0.35, lw=0)
        q = np.percentile(r, [5, 50, 95])
        b.plot([q[0], q[2]], [i, i], color=GRAY, lw=1)
        b.plot([q[1]], [i], marker="|", color=GRAY, ms=12)
        sh = s["full"]["夏普"]
        b.scatter([sh], [i], marker="D", s=46, color=cols[i], zorder=3, edgecolor="white", lw=0.8)
        b.text(sh, i - 0.3, f"{sh:.2f} · beats {s['random']['贏過隨機比例'] * 100:.1f}%", fontsize=8.8, color=cols[i], va="bottom", ha="center")
    b.set_yticks(y, [])
    b.set_xlim(0, 1.3)
    b.set_ylim(len(names) - 0.4, -1.1)
    b.set_xlabel("Sharpe ratio (dots: 200 random portfolios; bar: 5th–95th pct)")
    b.set_title("B  Sharpe vs. random portfolios", loc="left", fontsize=11, fontweight="bold", color=NAVY)
    for ax_ in axes:
        ax_.spines[["top", "right"]].set_visible(False)
        ax_.spines[["left", "bottom"]].set_color(LINE)
        ax_.tick_params(length=0)
    if not bare:
        f.text(0.01, 0.97, "An intuitive rule still needs evidence", fontsize=14, color=NAVY, fontweight="bold", va="top")
        f.text(0.01, 0.905, f"Swing period, {rec['start']} to {rec['end']}; weekly rebalancing; min. holding {rec['min_holding_days']} days; "
                            "costs included; price-volume factors only", fontsize=8.6, color=GRAY, va="top")
        band = ("Dashed: 0050 buy-and-hold (adjusted close). Dotted: equal-weighted average of each day's universe.\n"
                if len(bms) > 1 else "")
        f.text(0.01, 0.015, band + f"Percent = share of {rec['random_baseline']['R']} random portfolios (seed {rec['random_baseline']['seed']}) "
                                   "with a lower Sharpe ratio; descriptive, not a significance test. Source: runs/fig5_run_record.json",
               fontsize=7.8, color=GRAY, va="bottom", linespacing=1.4)
    _save(f, out, "Fig05_Results_bare" if bare else "Fig05_Results")


# ---------------------------------------------------------------------------
# 圖 6：先鎖定再揭露
# ---------------------------------------------------------------------------


def fig6(res: dict, out: Path, bare: bool = False):
    """bare=True：不畫圖內標題與底下兩行註記（放進文稿時由圖說交代），版面較矮，檔名加 _bare"""
    f = plt.figure(figsize=(8.2, 4.0 if bare else 5.8))
    if not bare:
        f.text(0.01, 0.98, "Historical replay with a locked rule and evidence cutoff", fontsize=14, color=NAVY, fontweight="bold", va="top")
    lk = res["lock"]
    hz = lk["failure_condition"]["horizon_days"]
    fail = "; ".join(_rule_text(r) for r in _items(lk["failure_condition"]))
    f.text(0.01, 0.965 if bare else 0.925, _wrap(f"Rule: {'; '.join(_rule_text(r) for r in _items(lk['rule']))}.  "
                                                 f"Failure: {fail[:1].lower() + fail[1:]} within {hz} trading days.", 130),
           fontsize=8.8, color=NAVY, va="top")
    # 時間軸
    t = f.add_axes([0.03, 0.60, 0.94, 0.26] if bare else [0.03, 0.66, 0.94, 0.17])
    t.set_xlim(0, 100)
    t.set_ylim(0, 10)
    t.axis("off")
    t.plot([3, 97], [5, 5], color=LINE, lw=2)
    steps = [(8, "1  Lock", f"Rule, failure condition, window and\ncase-selection declared {lk['declared_at'][:10]}\n(SHA-256 {res['lock_sha256'][:8]})", AMBER),
             (40, "2  Evidence cutoff", "Each signal uses data up to\nits own signal date only", NAVY),
             (72, "3  Reveal", f"Next {lk['failure_condition']['horizon_days']} trading days compared\nwith the locked failure condition", TEAL)]
    for x, h, s, c in steps:
        t.scatter([x], [5], s=60, color=c, zorder=3)
        t.text(x + 1.5, 9.5, h, fontsize=9.8, color=c, fontweight="bold", va="top")
        t.text(x + 1.5, 3.2, s, fontsize=8.2, color=NAVY, va="top")
    for k, (st, col, title) in enumerate((("held", TEAL, "Held"), ("failed", RED, "Failed"))):
        c = res["detail"].get(st)
        ax = f.add_axes([0.07 + 0.47 * k, 0.13, 0.40, 0.34] if bare else [0.07 + 0.47 * k, 0.17, 0.40, 0.38])
        if not c:
            ax.axis("off")
            continue
        days = [0] + [o["day"] for o in c["observations"]]
        vals = [c["signal_pct_vs_ma20"]] + [o["pct_vs_ma20"] for o in c["observations"]]
        ax.axhline(0, color=NAVY, lw=1, ls=(0, (4, 3)))
        ax.text(lk["failure_condition"]["horizon_days"] + 0.1, 0.15, "MA20", fontsize=8, color=NAVY, ha="right", va="bottom")
        ax.plot(days, vals, color=col, lw=1.8, marker="o", ms=4)
        if st == "failed":
            ax.scatter([days[-1]], [vals[-1]], s=80, facecolor="none", edgecolor=RED, lw=1.5, zorder=3)
        ax.set_xlim(-0.3, lk["failure_condition"]["horizon_days"] + 0.3)
        lo = min(vals + [-1])
        ax.set_ylim(lo - 0.8, max(vals) + 1.2)
        ax.set_xticks(range(0, lk["failure_condition"]["horizon_days"] + 1))
        ax.set_xlabel("Trading days after signal", fontsize=9)
        if k == 0:
            ax.set_ylabel("Close vs. MA20 (%)", fontsize=9)
        ax.spines[["top", "right"]].set_visible(False)
        ax.spines[["left", "bottom"]].set_color(LINE)
        ax.tick_params(labelsize=8.5, length=0)
        verdict = f"held all {hz} days" if st == "held" else f"failed on day {c['fail_day']}"
        ax.set_title(f"{title}: {c['stock_id']}, signal {c['signal_date']} — {verdict}", fontsize=9.6, color=col, loc="left", fontweight="bold")
    if not bare:
        n, cnt = res["n_signals"], res["counts"]
        f.text(0.01, 0.065, f"All {n} signals in {lk['window']['start']} to {lk['window']['end']}: held {cnt.get('held', 0)}, failed "
                            f"{cnt.get('failed', 0)}, unknown {cnt.get('unknown', 0)}. The two cases follow the pre-declared selection "
                            "(first held and first failed by date).", fontsize=8.4, color=NAVY)
        f.text(0.01, 0.025, "Retrospective replay of historical data; it illustrates the workflow and does not estimate a success rate. "
                            f"Source: runs/{Path(res.get('_file', 'replay_result.json')).name}", fontsize=7.8, color=GRAY)
    _save(f, out, "Fig06_Replay_bare" if bare else "Fig06_Replay")


def load_jsonl_logs(path, ids):
    """從 eval-translator 的 JSONL 取出指定題目的完整流程（pipeline）紀錄，連同評分結果（_grade）"""
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        x = json.loads(line)
        if x["item"]["id"] in ids and x["log"]["mode"] == "pipeline":
            out[x["item"]["id"]] = {**x["log"], "_grade": x.get("grade", {})}
    missing = sorted(set(ids) - set(out))
    if missing:
        raise SystemExit(f"{Path(path).name} 裡沒有 {', '.join(missing)} 的完整流程（pipeline）紀錄："
                         "eval-translator 的 --modes 要包含 pipeline，也不要用 --ids 把這兩題排除")
    return out
