# Idea2Alpha

**Turning investment ideas into testable, falsifiable stock hypotheses with generative AI**

- **Live demo:** https://idea2alpha.pages.dev
- **Demo video:** https://youtu.be/9I3tyS4bJbA

Idea2Alpha was built for the 2026 International Generative Artificial Intelligence Innovation Application Competition.

## How it works

You describe a stock-picking idea in one sentence. A generative AI translator turns it into conditions drawn from a fixed factor dictionary, and the program checks them before anything runs. A deterministic engine then screens Taiwan stocks and backtests the idea against the 0050 ETF, an out-of-sample period and 200 random portfolios. The AI explains each signal and stages a bull–bear debate in which every claim must cite a fact from a dated fact sheet. The rule throughout: programs compute the numbers, the AI writes the words.

## What's in this repository

| Folder | Contents |
|---|---|
| `06_選股策略程式_Idea2Alpha` | The Python core: factor dictionary, 23 strategy definitions, screening and backtest engines, the AI translator and its checks, fact sheets, explanations and debates, and the locked replay. `runs/` holds the recorded model runs. |
| `11_示範網站_Idea2Alpha` | The intro video and the files used to make it. |

## Try the Python core

Python 3.10 or later:

```bash
cd 06_選股策略程式_Idea2Alpha
pip install -r requirements.txt
python -m idea2alpha.cli selftest
```

The self-test runs the whole pipeline on simulated data in about a minute, with no network and no model calls. For real data, download it with your own free FinMind token and build the factor panel (`fetch`, then `panel`). `06_選股策略程式_Idea2Alpha/README.md` covers this, the model settings and every command.

## Note

For research and education only. Screens list stocks that match your conditions; nothing here is a recommendation or investment advice.

---

## 中文說明

**Idea2Alpha：用生成式 AI 把投資想法變成可檢驗、可證偽的選股假設**

- 線上示範：https://idea2alpha.pages.dev
- 介紹影片：https://youtu.be/9I3tyS4bJbA

用一句話說出選股想法，AI 翻譯員把它轉成因子字典裡的條件，程式先檢查再執行。篩選與回測都由程式計算，對照 0050、樣本外期間與 200 個隨機組合；AI 負責解說訊號與多空辯論，每句論點都要引用帶日期的事實表。分工原則：AI 負責語言，程式負責數字。

Python 核心在 `06_選股策略程式_Idea2Alpha`（先跑 `python -m idea2alpha.cli selftest`）；介紹影片與製作影片用的檔案在 `11_示範網站_Idea2Alpha`。要用真實資料，先用自己的 FinMind token 抓資料、組因子面板（`fetch`、`panel`，見 `06_` 裡的 README）。

僅供研究與教學。篩選結果是「符合條件」的股票，不是推薦，也不構成投資建議。
