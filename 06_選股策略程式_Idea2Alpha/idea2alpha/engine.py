"""
engine — 條件 JSON 的篩選引擎

一條規則（rule）有三種寫法，可以互相巢狀：
  {"factor": "rev_yoy_3m", "op": ">", "value": 20}                 因子 vs 數字
  {"factor": "ma_s", "op": ">", "ref": "ma_m"}                      因子 vs 因子（可加 "mult": 0.95）
  {"strategy": "V1"}                                                引用策略庫裡的策略
  {"logic": "all", "rules": [ ... ]}                                子組合

組合方式（logic）：
  all       全部符合（AND）
  any       任一符合（OR）
  vote      至少 k 個（"k": 2），分數 = 命中數
  weighted  加權分數（每條 "weight"），取分數前 top_n 檔（"top_n": 20）
  filter    主策略＋濾網：{"logic":"filter","main":{...},"filters":[...]}；濾網全部要過

op：> >= < <= == != between（value 為 [下限, 上限]）
因子缺值時該條規則視為「不符合」，並計入 coverage（資料覆蓋率）。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
STRATEGY_DIR = HERE / "strategies"


def load_library(folder: Path = STRATEGY_DIR) -> dict:
    lib = {}
    for f in sorted(folder.glob("*.json")):
        s = json.loads(f.read_text(encoding="utf-8"))
        if "id" in s:
            lib[s["id"]] = s
    return lib


def load_factor_dict() -> dict:
    return json.loads((HERE / "factors.json").read_text(encoding="utf-8"))


_OPS = {
    ">": np.greater, ">=": np.greater_equal, "<": np.less, "<=": np.less_equal,
    "==": np.equal, "!=": np.not_equal,
}


def _rule_hit(df: pd.DataFrame, r: dict) -> tuple[pd.Series, pd.Series]:
    """回傳 (hit, has_data)"""
    f = r["factor"]
    if f not in df:
        nan = pd.Series(False, df.index)
        return nan, nan
    x = df[f].astype(float)
    if r["op"] == "between":
        lo, hi = r["value"]
        hit = (x >= lo) & (x <= hi)
        ok = x.notna()
    elif "ref" in r:
        y = df[r["ref"]].astype(float) * r.get("mult", 1.0) if r["ref"] in df else pd.Series(np.nan, df.index)
        hit = pd.Series(_OPS[r["op"]](x, y), df.index)
        ok = x.notna() & y.notna()
    else:
        hit = pd.Series(_OPS[r["op"]](x, float(r["value"])), df.index)
        ok = x.notna()
    return hit & ok, ok


class Evaluator:
    def __init__(self, panel: pd.DataFrame, library: dict | None = None):
        self.df = panel
        self.lib = library if library is not None else load_library()
        self._cache: dict[str, tuple] = {}

    # 回傳 (hit: bool Series, score: float Series, coverage: float Series)
    def eval(self, node: dict):
        if "strategy" in node:
            sid = node["strategy"]
            if sid not in self._cache:
                if sid not in self.lib:
                    raise KeyError(f"策略庫沒有 {sid}")
                if self.lib[sid].get("role") == "overlay":  # L4：不選股，回測時調整持股比例
                    t = pd.Series(True, self.df.index)
                    self._cache[sid] = (t, pd.Series(0.0, self.df.index), pd.Series(1.0, self.df.index))
                else:
                    self._cache[sid] = self.eval(self.lib[sid])
            return self._cache[sid]
        if "factor" in node:
            hit, ok = _rule_hit(self.df, node)
            return hit, hit.astype(float), ok.astype(float)
        logic = node.get("logic", "all")
        if logic == "filter":
            h, s, c = self.eval(node["main"])
            for flt in node.get("filters", []):
                fh, _, fc = self.eval(flt)
                h = h & fh
                c = np.minimum(c, fc)
            return h, s.where(h, 0.0), c
        rules = [r for r in node["rules"] if not uses_overlay(r, self.lib) or "logic" in r]
        if not rules:  # 只有 L4 → 全部視為符合
            t = pd.Series(True, self.df.index)
            return t, pd.Series(0.0, self.df.index), pd.Series(1.0, self.df.index)
        node = {**node, "rules": rules}
        subs = [self.eval(r) for r in rules]
        H =pd.concat([x[0] for x in subs], axis=1).astype(bool)
        C = pd.concat([x[2] for x in subs], axis=1)
        cov = C.mean(axis=1)
        if logic == "all":
            hit = H.all(axis=1)
            score = H.sum(axis=1).astype(float)
        elif logic == "any":
            hit = H.any(axis=1)
            score = H.sum(axis=1).astype(float)
        elif logic == "vote":
            k = node.get("k", 2)
            score = H.sum(axis=1).astype(float)
            hit = score >= k
        elif logic == "weighted":
            w = np.array([r.get("weight", 1.0) for r in node["rules"]], dtype=float)
            score = pd.Series(H.to_numpy().astype(float) @ (w / w.sum()), H.index)
            n = node.get("top_n", 20)
            sr = score.where(self.df["in_universe"] == 1, -1.0) if "in_universe" in self.df else score
            rank = sr.groupby(level="date").rank(ascending=False, method="first")
            hit = (rank <= n) & (score > 0)
        else:
            raise ValueError(f"未知的 logic：{logic}")
        return hit, score, cov

    def screen(self, node: dict, date=None, universe_only: bool = True) -> pd.DataFrame:
        """回傳某一天（預設最新）的符合清單，依分數、成交值排序"""
        hit, score, cov = self.eval(node)
        df = pd.DataFrame({"hit": hit, "score": score, "coverage": cov})
        if universe_only and "in_universe" in self.df:
            df = df[self.df["in_universe"] == 1]
        dates = df.index.get_level_values("date")
        d = dates.max() if date is None else pd.Timestamp(date)
        day = df.xs(d, level="date")
        day = day[day["hit"]]
        extra = [c for c in ["close_raw", "turnover_rank", "risk_light", "var5"] if c in self.df]
        day = day.join(self.df.xs(d, level="date")[extra])
        return day.sort_values(["score", "turnover_rank"], ascending=[False, True])

    def counts(self, ids: list[str], date=None) -> pd.Series:
        """每個策略單獨命中幾檔（畫面上的「即時檔數」）"""
        out = {}
        for sid in ids:
            out[sid] = len(self.screen({"strategy": sid}, date))
        return pd.Series(out)

    def overlap(self, ids: list[str]) -> pd.DataFrame:
        """兩兩策略的共同入選率（歷史平均）：|A∩B| / min(|A|,|B|)，低於 5% 標示「本質互斥」"""
        u = self.df["in_universe"] == 1 if "in_universe" in self.df else slice(None)
        H = {sid: self.eval({"strategy": sid})[0][u] for sid in ids}
        m = pd.DataFrame(index=ids, columns=ids, dtype=float)
        for a in ids:
            for b in ids:
                na, nb = H[a].sum(), H[b].sum()
                m.loc[a, b] = (H[a] & H[b]).sum() / min(na, nb) if min(na, nb) else np.nan
        return m


def uses_overlay(node: dict, library: dict) -> bool:
    """組合裡有沒有勾 L4 這類 overlay 策略"""
    if "strategy" in node:
        return library.get(node["strategy"], {}).get("role") == "overlay"
    kids = node.get("rules", []) + node.get("filters", []) + ([node["main"]] if "main" in node else [])
    return any(uses_overlay(k, library) for k in kids)


def combo(ids: list[str], mode: str = "all", k: int = 2, weights: list[float] | None = None, top_n: int = 20,
          filters: list[dict] | None = None) -> dict:
    """把勾選的策略組成條件 JSON（前端的五種組合方式）"""
    rules = [{"strategy": s} for s in ids]
    if mode == "filter":
        return {"logic": "filter", "main": rules[0], "filters": rules[1:] + (filters or [])}
    node = {"logic": mode, "rules": rules}
    if mode == "vote":
        node["k"] = k
    if mode == "weighted":
        for r, w in zip(rules, weights or [1.0] * len(rules)):
            r["weight"] = w
        node["top_n"] = top_n
    if filters:
        return {"logic": "filter", "main": node, "filters": filters}
    return node


def validate(node: dict, factor_dict: dict, library: dict) -> list[str]:
    """檢查條件 JSON：因子要在字典裡、數值在合理範圍內（給 AI 翻譯員的輸出把關用）"""
    errs = []

    def walk(n):
        if "strategy" in n:
            if n["strategy"] not in library:
                errs.append(f"未知策略 {n['strategy']}")
            return
        if "factor" in n:
            f = n["factor"]
            if f not in factor_dict:
                errs.append(f"因子不在白名單：{f}")
                return
            if n.get("op") not in list(_OPS) + ["between"]:
                errs.append(f"{f}：不支援的運算子 {n.get('op')}")
            lo, hi = factor_dict[f].get("range", [None, None])
            vals = n.get("value")
            vals = vals if isinstance(vals, list) else [vals]
            for v in vals:
                if v is None:
                    continue
                if (lo is not None and v < lo) or (hi is not None and v > hi):
                    errs.append(f"{f}：數值 {v} 超出合理範圍 [{lo}, {hi}]")
            if "ref" in n and n["ref"] not in factor_dict:
                errs.append(f"參照因子不在白名單：{n['ref']}")
            return
        for r in n.get("rules", []):
            walk(r)
        if "main" in n:
            walk(n["main"])
        for r in n.get("filters", []):
            walk(r)
    walk(node)
    return errs


# ---------------------------------------------------------------------------
# 過度擬合警示（規劃書〈防止回測好看的三個設計〉第 3 點）
# ---------------------------------------------------------------------------
OVERFIT_LIMIT = 5


def count_conditions(node: dict, library: dict) -> int:
    """數葉節點條件數；引用的策略展開計算，L4 這類 overlay 不算"""
    if "strategy" in node:
        s = library.get(node["strategy"], {})
        return 0 if s.get("role") == "overlay" else count_conditions(s, library)
    if "factor" in node:
        return 1
    kids = node.get("rules", []) + node.get("filters", []) + ([node["main"]] if "main" in node else [])
    return sum(count_conditions(k, library) for k in kids)


def _label(r: dict, library: dict) -> str:
    if "label" in r:
        return r["label"]
    if "strategy" in r:
        return f"{r['strategy']} {library.get(r['strategy'], {}).get('name', '')}".strip()
    if "factor" in r:
        rhs = r.get("ref", r.get("value"))
        return f"{r['factor']} {r['op']} {rhs}"
    return f"子組合（{r.get('logic', 'all')}）"


def ablation_variants(node: dict, library: dict) -> list[tuple[str, dict]]:
    """
    逐一拿掉一個條件的版本：[(拿掉的條件, 新條件 JSON), ...]
    只有一個策略引用時先展開成它的規則；主策略＋濾網則逐一拿掉濾網。
    """
    if "strategy" in node:
        node = library[node["strategy"]]
    if node.get("logic") == "filter":
        fl = node.get("filters", [])
        return [(_label(f, library), {**node, "filters": fl[:i] + fl[i + 1:]}) for i, f in enumerate(fl)]
    rules = [r for r in node.get("rules", []) if not ("strategy" in r and library.get(r["strategy"], {}).get("role") == "overlay")]
    keep_overlay = [r for r in node.get("rules", []) if r not in rules]
    if len(rules) == 1 and "strategy" in rules[0]:
        return ablation_variants(rules[0], library)
    out = []
    for i, r in enumerate(rules):
        rest = rules[:i] + rules[i + 1:]
        if not rest:
            continue
        v = {k: x for k, x in node.items() if k not in ("id", "name", "rules")}
        v["rules"] = rest + keep_overlay
        if v.get("logic") == "vote":
            v["k"] = min(v.get("k", 2), len(rest))
        out.append((_label(r, library), v))
    return out
