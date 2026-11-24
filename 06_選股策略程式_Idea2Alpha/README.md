# Idea2Alpha 選股策略程式（競賽用起始版）

規劃書〈策略庫〉的 23 個策略、〈策略組合篩選器與時間週期〉的五種組合與三種週期、〈綜合比較與驗證機制〉的回測規則，都已寫成可以跑的 Python。這是規劃書「實作步驟」第 2～7 步的起點：資料管線、因子字典、策略定義檔、篩選引擎（Python 版）、回測引擎（Python 版）、夜間批次要輸出的東西。AI 角色（翻譯員、解說員、多方／空方／裁判）的程式已經補上，見文末〈生成式 AI 模組〉，但還沒用真實模型跑過；前端（React）與雲端函式還沒做，留給你們。

## 先跑起來

```bash
pip install -r requirements.txt
python -m idea2alpha.cli selftest          # 模擬資料跑完整流程，9 項檢查（含過度擬合、偷看未來、翻譯員離線回放），約 1 分鐘，不需要網路
```

看到「selftest 全部通過」就代表程式與環境沒問題。selftest 不會呼叫模型，所以它不會測到你的 API 金鑰與模型設定。接著用真資料：

```bash
# 1. 到 https://finmindtrade.com 免費註冊，拿 API token
export FINMIND_TOKEN=你的token            # Windows PowerShell：$env:FINMIND_TOKEN="你的token"

# 2. 抓資料（先用 20 檔試，確定能跑再抓 300～400 檔）
python -m idea2alpha.cli fetch --stocks 2330,2317,2454,2308,2303,2382,2881,2891,1301,2002,2603,3008,2412,1216,2912
python -m idea2alpha.cli fetch --top 400   # 證交所＋櫃買今天成交值前 400 名

# 3. 組因子面板（三種週期各一份）
python -m idea2alpha.cli panel --period short,swing,long

# 4. 篩選、回測、比較
python -m idea2alpha.cli screen   --strategies G1,G2 --mode all --period swing
python -m idea2alpha.cli screen   --template 技術共振 --period short
python -m idea2alpha.cli backtest --strategies T5,T6,T7,T8 --mode vote --k 2 --period short
python -m idea2alpha.cli backtest --strategies G1,L4 --period swing      # 勾 L4 = 開大盤風險濾網（G1 有 6 個條件，會自動做過度擬合檢查）
python -m idea2alpha.cli arena    --period swing --l4                    # 策略競技場
python -m idea2alpha.cli stats    --strategies G2 --period swing         # 策略體檢卡：訊號後報酬分布
python -m idea2alpha.cli overlap  --strategies Q2,Q3,G1,T8 --period long # 衝突提示
```

**抓資料要多久：** FinMind 免費會員每小時約 600 次請求，一檔股票 10 個資料集。400 檔第一次約 7 小時（程式會自己等額度、可以中斷再接著抓），之後每天只補新資料。先用 20 檔把流程跑通，再讓電腦整晚抓。

## 資料夾

| 檔案 | 內容 |
|---|---|
| `strategies/*.json` | 23 個策略定義檔（L1–L4、V1–V4、G1–G2、Q1–Q3、T1–T8、C1–C2），每個附出處、適合週期、所需資料 |
| `templates.json` | 一鍵範本：價值品質、成長動能、籌碼跟單、技術共振、存股安心，加上規劃書的範例條件 |
| `periods.json` | 短線／波段／長線三種週期：K 線頻率、均線組、指標參數、換股頻率、評估天數 |
| `factors.json` | 因子字典（95 個，含「剛突破月線」這類事件因子）：中文名、單位、合理範圍。AI 翻譯員（M1）的白名單就用這份 |
| `idea2alpha/data.py` | FinMind 抓取與快取（`cache/`），證交所／櫃買 OpenAPI 取當日成交值排名 |
| `idea2alpha/factors.py` | 因子計算：價量、技術指標、估值、財報、月營收、股利、籌碼、大盤層（L4） |
| `idea2alpha/pipeline.py` | 組成 date × stock_id 的因子面板，算每日股票池（成交值前 300 名）與排名類因子 |
| `idea2alpha/engine.py` | 條件 JSON 篩選引擎：AND／OR／投票／加權／主策略＋濾網，可巢狀；條件檢查（validate） |
| `idea2alpha/backtest.py` | 回測：換股、成本、等權、L4、樣本內外、多空頭分段、隨機基準；訊號後報酬分布 |
| `idea2alpha/gaf.py` | L3 線型影像辨識：GAF 影像＋分類器（邏輯迴歸起點，可換 CNN） |
| `idea2alpha/synthetic.py` | 模擬資料（selftest 用） |
| `idea2alpha/cli.py` | 命令列入口（指令一覽在檔案開頭） |
| `idea2alpha/llm.py` | 生成式 AI 呼叫的唯一出入口：Anthropic／OpenAI／Gemini／離線回放，暫時性錯誤自動重試，每次呼叫記到 `runs/llm_calls.jsonl` |
| `idea2alpha/translate.py` | 翻譯員：想法 → 澄清問題 → 條件 JSON；字典層與語意層檢查，錯了回饋模型修一次 |
| `idea2alpha/eval_translator.py` | 30 題測試集的評估：直接翻譯／只給字典／完整流程，以「執行結果相同」判對錯 |
| `idea2alpha/facts.py`、`claims.py` | 帶日期的事實表；論點檢查（supported／insufficient／unsupported／invalid） |
| `idea2alpha/debate.py` | 解說員、多方、空方、裁判，分開呼叫、只看同一份事實表 |
| `idea2alpha/replay.py` | 先鎖定（SHA-256）再揭露的歷史重播 |
| `idea2alpha/figures.py` | 報告圖 3–6，全部從 `runs/` 的紀錄檔產生 |
| `prompts/` | 各角色的提示詞；版本 = 檔名＋內容雜湊前 8 碼 |
| `testset/` | `translator_30.json`（30 題測試集，2026-11-22 已人工審題，審題者見 `reviewed_by`）；`illustrative/`（示例紀錄，不是模型輸出） |
| `runs/` | 執行紀錄：圖 5 回測紀錄、重播鎖定檔與結果、事實表；之後的評估、辯論、模型呼叫紀錄也寫在這裡 |
| `figures/` | 圖 3–6（png／pdf／svg）。圖 3、圖 4 的 `Fig03_Translation`、`Fig04_Evidence` 是示例；用真實模型紀錄畫的是 `*_real`（2026-11-20）。注意：`figures` 指令每次都會重畫並覆蓋 `Fig03_Translation`、`Fig04_Evidence`，不會動到 `*_real` |
| `output/` | 面板與回測輸出，都不上傳 GitHub。面板 `panel_swing.parquet` 要先跑 `fetch`、`panel` 做出來；已經有一份就放在這裡（跑 `panel --period swing` 會覆蓋它）。其他輸出執行時自動產生 |

## 條件 JSON（策略庫、使用者自選、AI 翻譯員輸出都用同一種格式）

```json
{
  "logic": "all",
  "rules": [
    {"factor": "rev_yoy_3m", "op": ">", "value": 20, "label": "近3月營收年增率 > 20%"},
    {"factor": "inst_net_buy_days", "op": ">=", "value": 3, "label": "投信或外資連買 ≥ 3 日"},
    {"factor": "var5_rank", "op": "<", "value": 0.8, "label": "排除下檔風險最高 20%"}
  ]
}
```

- 規則三種寫法：因子對數字（`value`）、因子對因子（`ref`，例如 `{"factor":"ma_s","op":">","ref":"ma_m"}`）、引用策略（`{"strategy":"V1"}`），可以巢狀。
- 組合：`all`、`any`、`vote`（加 `"k"`）、`weighted`（每條加 `"weight"`，取 `"top_n"`）、`filter`（`"main"` 選股、`"filters"` 剔除）。
- 均線、KD、RSI、%R、MACD、布林都依週期換算：策略只寫 `ma_s`／`ma_m`／`ma_l`，切到長線就是 10／30／52 週均線。
- 因子缺值時那條規則算「不符合」，篩選結果會附 `coverage`（資料覆蓋率）。
- `python -m idea2alpha.cli validate --json 你的條件.json` 會檢查因子是否在白名單、數值是否在合理範圍；AI 翻譯員的輸出先過這關再執行。

## 回測規則（`backtest.py`，照規劃書〈回測共同規則〉）

- 股票池：每天 20 日平均成交值前 300 名；訊號日收盤產生、次一交易日開盤成交；等權、最多 20 檔。
- 換股：短線每日、波段每週最後一個交易日、長線每月最後一個交易日。
- **最短持有**：買進後至少抱 5／20／60 個交易日（短線／波段／長線，`--hold` 可改）。突破、黃金交叉這類只亮一天的訊號，若一換股就賣，成本會吃掉一切。
- 成本：手續費 0.1425% 雙邊、證交稅 0.3%、滑價 0.1% 雙邊。
- 勾 L4：換股日的持股比例乘上 `l4_scale`（1、0.5、0.25），其餘放現金。
- 過度擬合警示：條件（展開策略後的葉節點）超過 5 個時，`backtest` 會自動逐一拿掉每個條件重跑，列出夏普與年化報酬的變化，存到 `output/backtest_ablation.csv`；拿掉後夏普上升或幾乎不變的條件，可能只是在湊歷史。不到 6 個條件也想檢查就加 `--ablation`。
- 隨機基準：同股票池、同檔數、同換股比例、同最短持有，跑 `--random` 次，看策略夏普贏過幾成；低於 70% 標示「與運氣難以區分」。
- 報告：全期、樣本內 2019–2023、樣本外 2024 起、多頭／空頭段（0050 是否在 200 日線上）、交易筆數、勝率、平均持有天數、年換手率。

**用實驗室全市場日線試跑的結果（2019/1～2026/10，波段週期，只有價量資料，供對照程式是否正常）：** T1 12-1 月動能年化 26.7%、夏普 0.82；Q3 低波動 15.9%、0.96；T3 52 週新高 18.8%、0.73；技術共振（T5–T8 至少 2 個）7.7%、0.40；以 200 個隨機組合比較，四者的夏普分別贏過 85.0%、94.0%、90.5%、30.5%。完整設定、隨機分布與每週權益見 `runs/fig5_run_record.json`。數字會隨股票池與資料更新而變，作品裡一律用當天實際輸出。

**0050 與等權股票池是兩個不同的基準（11-19 已查明）：** 0050 還原收盤買進持有 = 29.7%（FinMind，含 2025-06-18 的 1 拆 4）；24.1% 是「每天前 300 檔的等權平均」，舊紀錄把它放在回測的基準欄位卻標成「0050 年化」，才看起來像 0050 有兩個數字。現在 `run_backtest` 輸出「基準名稱」，基準欄改名「基準年化」；紀錄由 `scripts/make_fig5_record.py` 產生（有 `FINMIND_TOKEN` 和 `output/panel_swing.parquet` 就能重跑），`benchmarks` 分開存 `0050` 與 `equal_weight_universe`，圖 5 畫成兩條線。結論：四個策略都輸 0050；只有 T1（26.7%）贏等權股票池。舊紀錄在 `runs/superseded/`。

## 偷看未來的防線（評審最可能追問）

1. 第 t 天收盤算訊號，第 t+1 天開盤成交。
2. 月營收從公告日（`create_time`，沒有就次月 10 日）起才可用；季報依法定期限：Q1→5/15、Q2→8/14、Q3→11/14、Q4→隔年 3/31；股利從董事會公告日起。
3. 價格用還原權值（除權息、分割），指標只用比值。
4. 週K 只在週最後一個交易日更新。
5. `selftest` 會把資料截斷在 2021/6/30 重算，截斷日以前的每一個因子都必須完全相同；改了因子程式就重跑一次。

## 已知限制（照實寫進報告）

- **測試範圍：** 價量、技術指標、動態股票池、回測用實驗室全市場日線（含下市股）實際跑過；籌碼因子用台積電 2012 年起的真實資料跑過；財報、月營收、股利的欄位解析用 FinMind 真實回應格式測過，但沒有跑過 300 檔的完整歷史。第一次抓完資料後，`screen` 的 `coverage` 欄位與 `arena` 的「資料覆蓋率」要看一下，低於 0.8 的策略先查資料。
- **倖存者偏誤：** `fetch --top 400` 用的是「今天」的成交值名單，回測過去會偏樂觀。要做規劃書的動態股票池，需要含下市股票的全市場歷史日線：實驗室有一份（2010/10 起、2,799 檔），跟老師拿，用 `panel --daily-all 資料夾` 讀入；財報等其他資料仍要逐檔抓。
- **C2 大戶持股**需要 FinMind 付費等級的集保資料；沒有資料時 C2 一律不命中（`fetch --holders` 可試）。
- **減資**沒有做價格還原（除權息與分割有做），少數減資股的技術指標會失真。
- **G1 的「法人持股增加」**用外資＋投信 20 日淨買超代替；**V2 的企業價值**用長短期借款與公司債估負債。
- **L3** 要先跑 `python -m idea2alpha.cli gaf --period swing` 產生分數；2023 年底以前的分數是樣本內，只能看 2024 起那段。
- 有實驗室全市場日線與除權息檔時：`python -m idea2alpha.cli panel --daily-all daily_all資料夾 --events-file tw_dividend_events.csv` 會自動挑出歷年曾進成交值前 450 名的股票（含已下市），每天依當時成交值重算股票池。
- 國外策略的門檻是照原書寫的初始參數，回測後再調，但只能在 2019–2023 調，2024 起只看不調。
- 這裡的回測是給「策略競技場」用的研究數字，不是投資建議；前端照規劃書〈法規合規與風險揭露〉處理。

## 生成式 AI 模組（翻譯員、多空辯論、歷史重播）

| 指令 | 做什麼 | 需要 API 金鑰 | 需要面板 |
|---|---|---|---|
| `translate "低價、剛突破月線、放量"` | 互動式翻譯：模型提問 → 你選 → 條件 JSON → 檢查 | 要 | 不用 |
| `eval-translator --modes direct,dict,pipeline` | 30 題測試集，三種做法同一模型比較（第 3.5 節的表） | 要 | 要 |
| `facts --stock 2610 --date 2026-07-28` | 帶日期的事實表（多空辯論唯一能用的資料） | 不用 | 要 |
| `explain --stock 2610 --date 2026-07-28 --hide-name` | 解說員：三句白話說明為什麼符合條件，每句自動檢查 | 要 | 要 |
| `debate --stock 2610 --date 2026-07-28 --hide-name` | 多方、空方、裁判分開生成，每句理由自動檢查 | 要 | 要 |
| `replay --rule r.json --start 2026-07-01 --end 2026-09-30` | 先鎖定（寫入雜湊），再 `replay --reveal 鎖定檔` 揭露 | 不用 | 揭露時要 |
| `figures --fig3 … --fig4 … --fig5 … --fig6 …` | 從紀錄檔產生報告圖 3–6；加 `--bare` 另外輸出放進英文稿用的圖 5、圖 6（不含圖內標題與註記） | 不用 | 不用 |

「面板」指 `output/panel_swing.parquet`。它不上傳 GitHub，要先跑 `fetch`、`panel` 做出來；已經有一份就放在 `output/`（跑 `panel` 會覆蓋它）。

設定模型（三家擇一；Windows PowerShell，只在這個視窗有效）：

```powershell
$env:IDEA2ALPHA_LLM = "anthropic"            # 或 openai、gemini
$env:IDEA2ALPHA_MODEL = "你們實際使用的模型名稱"
$env:ANTHROPIC_API_KEY = "你的金鑰"           # 或 OPENAI_API_KEY、GEMINI_API_KEY
```

macOS／Linux 用 `export IDEA2ALPHA_LLM=anthropic` 這種寫法。金鑰只放環境變數，不寫進任何檔案。

- 每次呼叫都記在 `runs/llm_calls.jsonl`（模型、時間、提示詞版本、完整輸入輸出）。報告寫的模型名稱與日期從這裡抄。
- 碰到額度上限（HTTP 429）、伺服器忙（5xx、529）或連線逾時，會自動等 5、10、20、40 秒重試；仍失敗才中斷。
- 提示詞在 `prompts/`，版本 = 檔名＋內容雜湊，改一個字版本就變。
- `IDEA2ALPHA_LLM=replay` 可以回放錄製好的回應，離線重現結果；selftest 用的就是這個。
- 評估時，模型問的澄清問題由測試題預設的「腳本回答」回覆；只要模型問了至少一題，該題其餘的預設回答也會一併附上。
- `testset/translator_30.json` 由 GAI 起草，**每題要有組員確認標準答案並填上 `reviewed_by`**，才能拿來報告；`review_note` 裡的「GAI 交叉檢查」只是建議，不算審過。
- `testset/illustrative/` 是示例紀錄（測試用回應，不是模型輸出），圖上會自動標 ILLUSTRATIVE；跑完真實紀錄後重畫就會拿掉。真實紀錄裡模型沒追問、或第一次就答對時，圖會照實標示，不會留白。
- 論點檢查只核對數字與證據類型，不檢查方向（引用「今天站上月線：否」卻寫「站上月線」不會被攔），理由合不合理仍要人看。
- 完整交件步驟見上一層的 `給同學_Idea2Alpha交付清單.md`。
