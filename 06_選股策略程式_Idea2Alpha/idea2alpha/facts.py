"""
facts — 帶日期的事實表（多空辯論與解說員唯一可用的資料）

每一條事實：編號 F01…、中英文名稱、數值（已四捨五入，AI 引用時必須照抄）、單位、資料日期、來源因子、標籤。
標籤讓論點檢查知道這條事實能支持哪一類推論：
  level     單一時點的水準（例：收盤價、本益比）
  event     當天發生的事件（例：剛突破月線）
  multi_day 跨多日的資料（例：近 5 日均量、連續天數）
  trend     同一指標兩個時點的比較（例：本月與上月營收年增率），只有它能支持「加速／放緩」
  benchmark 與大盤或股票池比較（例：百分位、排名）
  risk      風險類（例：波動度、下檔風險）
"""
from __future__ import annotations

import math

import pandas as pd

# (因子, 中文, 英文, 單位, 小數位, 標籤, 轉換)
SPEC = [
    ("close_raw", "收盤價", "Close", "元", 2, "level", None),
    ("_pct_vs_ma20", "收盤相對 20 日均線", "Close vs. MA20", "%", 1, "level", None),
    ("cross_up_ma20", "當日向上突破 20 日均線", "Crossed above MA20 today", "是/否", 0, "event", None),
    ("days_above_ma20", "連續收在 20 日均線之上", "Consecutive days above MA20", "日", 0, "multi_day", None),
    ("vol_ratio20", "成交量 ÷ 前 20 日均量", "Volume / prior 20-day average", "倍", 2, "level", None),
    ("vol5_ratio20", "近 5 日均量 ÷ 再之前 20 日均量", "5-day avg volume / prior 20-day avg", "倍", 2, "multi_day", None),
    ("ret_1d", "當日漲跌幅", "1-day return", "%", 1, "level", None),
    ("ret_5d", "近 5 日漲跌幅", "5-day return", "%", 1, "multi_day", None),
    ("high_52w_dist", "距 52 週高點", "Distance from 52-week high", "%", 1, "level", None),
    ("mom_12_1_pct", "12-1 月報酬在股票池的百分位", "12-1 momentum percentile", "百分位", 0, "benchmark", lambda v: v * 100),
    ("vol_1y", "近 1 年年化波動度", "1-year volatility", "%", 1, "risk", None),
    ("var5", "近 1 年日報酬 5% 分位", "1-day 5% VaR (1-year)", "%", 1, "risk", None),
    ("turnover_rank", "20 日成交值排名", "20-day turnover rank", "名", 0, "benchmark", None),
    ("PER", "本益比", "P/E", "倍", 1, "level", None),
    ("PBR", "股價淨值比", "P/B", "倍", 2, "level", None),
    ("dividend_yield", "現金殖利率", "Dividend yield", "%", 2, "level", None),
    ("rev_yoy_m", "單月營收年增率", "Monthly revenue YoY", "%", 1, "level", None),
    ("_rev_yoy_m_prev", "上月營收年增率", "Previous month revenue YoY", "%", 1, "trend", None),
    ("rev_yoy_3m", "近 3 月營收年增率", "3-month revenue YoY", "%", 1, "multi_day", None),
    ("eps_yoy_q", "單季 EPS 年增率", "Quarterly EPS YoY", "%", 1, "level", None),
    ("roe_ttm", "近四季 ROE", "TTM ROE", "%", 1, "level", None),
    ("fscore", "Piotroski F-Score", "Piotroski F-Score", "分", 0, "level", None),
    ("inst_net_20d", "外資＋投信近 20 日淨買超", "Foreign + trust net buy, 20 days", "張", 0, "multi_day", None),
    ("trust_buy_days", "投信連續買超天數", "Investment-trust buying streak", "日", 0, "multi_day", None),
    ("margin_chg_10d", "融資餘額 10 日變化", "10-day margin balance change", "%", 1, "multi_day", None),
]


def build(panel: pd.DataFrame, stock_id: str, date, hide_name: bool = False) -> dict:
    """回傳 {"stock_id", "as_of", "facts": [...]}；資料缺的事實不列出（不要讓 AI 看到空值去猜）"""
    d = pd.Timestamp(date)
    row = panel.loc[(d, stock_id)]
    ext = {}
    if "adj_close" in row and "ma20" in row and row["ma20"] > 0:
        ext["_pct_vs_ma20"] = (row["adj_close"] / row["ma20"] - 1) * 100
    if "rev_yoy_m" in panel:  # 上月營收年增率：取上一個「不同值」的時點
        s = panel.xs(stock_id, level="stock_id")["rev_yoy_m"].loc[:d].dropna()
        ch = s[s.ne(s.shift())]
        if len(ch) >= 2:
            ext["_rev_yoy_m_prev"] = ch.iloc[-2]
    facts, n = [], 0
    for f, zh, en, unit, nd, tag, conv in SPEC:
        v = ext.get(f, row.get(f) if f in row else None)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        v = conv(v) if conv else v
        n += 1
        if unit == "是/否":
            val, shown = int(v), ("是" if v == 1 else "否")
        else:
            val = round(float(v), nd) if nd else int(round(float(v)))
            shown = f"{val:,.{nd}f}" if nd else f"{val:,}"
        facts.append({"id": f"F{n:02d}", "label": zh, "label_en": en, "value": val, "shown": shown, "unit": unit,
                      "as_of": str(d.date()), "source": f.lstrip("_"), "tag": tag})
    return {"stock_id": "（已遮蔽）" if hide_name else stock_id, "as_of": str(d.date()), "facts": facts}


def to_text(sheet: dict) -> str:
    lines = [f"事實表（資料日期 {sheet['as_of']}，股票 {sheet['stock_id']}）"]
    for x in sheet["facts"]:
        lines.append(f"[{x['id']}] {x['label']}：{x['shown']} {x['unit']}（來源 {x['source']}）")
    return "\n".join(lines)
