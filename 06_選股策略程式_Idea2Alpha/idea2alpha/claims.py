"""
claims — 論點檢查：AI 寫的每一句理由，是否真的被它引用的事實支持

判定（由嚴到寬，第一個不過就停）：
  invalid       引用了不存在的事實編號／沒有引用任何事實／含投資建議字眼
  unsupported   句中出現的數字，在它引用的事實裡找不到（容許四捨五入差異）
  insufficient  用了需要特定證據的推論詞，但引用的事實沒有那種標籤：
                  加速／放緩 → 需要 trend（兩個時點的比較）
                  持續／連續／一直 → 需要 multi_day
                  優於大盤／領先／前段 → 需要 benchmark
  supported     以上都通過
這是程式能判斷的部分；「理由合不合理」仍要人看。檢查結果原樣交給裁判，裁判不能推翻。
"""
from __future__ import annotations

import re

NUM = re.compile(r"(?<![A-Za-z\d.\[F\-−])[-−]?\d[\d,]*(?:\.\d+)?")
NEEDS = [
    (re.compile(r"加速|放緩|轉強|轉弱|accelerat|decelerat"), "trend", "「加速／放緩」需要同一指標兩個時點的事實",
     "“accelerating/slowing” needs the same measure at two dates"),
    (re.compile(r"持續|連續|一直|每天|sustain|consecutive|persist"), "multi_day", "「持續／連續」需要跨多日的事實",
     "“sustained” needs a multi-day fact; the cited fact covers one day"),
    (re.compile(r"優於大盤|勝過大盤|領先|前段班|outperform|top"), "benchmark", "「優於大盤／領先」需要比較基準的事實",
     "“outperform/top” needs a benchmark fact"),
]
ADVICE = re.compile(r"建議買進|建議賣出|值得買|可以買|目標價|會漲到|保證|buy now|price target")


def _nums(text: str) -> list[float]:
    out = []
    for m in NUM.findall(text):
        try:
            out.append(float(m.replace(",", "").replace("−", "-")))
        except ValueError:
            pass
    return out


def check_claim(claim: dict, sheet: dict) -> dict:
    facts = {f["id"]: f for f in sheet["facts"]}
    text = claim.get("text", "")
    cites = [c.strip("[] ") for c in claim.get("cites", [])]
    cites += [c for c in re.findall(r"\[(F\d{2})\]", text) if c not in cites]
    res = {"text": text, "cites": cites, "verdict": "supported", "reasons": [], "reasons_en": []}

    def fail(v, why, en=""):
        res["verdict"], res["reasons"], res["reasons_en"] = v, res["reasons"] + [why], res["reasons_en"] + [en or why]
        return res

    if ADVICE.search(text):
        return fail("invalid", "含投資建議或價格預測字眼", "contains advice or a price forecast")
    if not cites:
        return fail("invalid", "沒有引用任何事實", "cites no fact")
    bad = [c for c in cites if c not in facts]
    if bad:
        return fail("invalid", f"引用了不存在的事實：{', '.join(bad)}", f"cites unknown fact {', '.join(bad)}")
    cited = [facts[c] for c in cites]
    allowed = []
    for f in cited:
        v = f["value"]
        allowed += [float(v), abs(float(v))]
    for x in _nums(re.sub(r"\[F\d{2}\]", "", text)):
        if x in (1, 5, 20, 52, 60, 10, 12) and any(str(int(x)) in f["label"] for f in cited):
            continue  # 指標名稱裡的數字（例：20 日均線、52 週）
        if not any(abs(x - a) <= max(0.051, abs(a) * 0.005) for a in allowed):
            return fail("unsupported", f"數字 {x:g} 不在引用的事實中", f"the number {x:g} is not in the cited facts")
    tags = {f["tag"] for f in cited}
    for pat, need, why, en in NEEDS:
        if pat.search(text) and need not in tags:
            fail("insufficient", why, en)
    return res


def check_all(claims: list[dict], sheet: dict) -> list[dict]:
    return [check_claim(c, sheet) for c in claims]
