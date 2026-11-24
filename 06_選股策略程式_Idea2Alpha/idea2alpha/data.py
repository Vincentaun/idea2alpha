"""
data — FinMind 資料抓取與本機快取

每個 (資料集, 股票) 存成一個 cache/<資料集>/<代號>.csv.gz；再跑一次只補新日期。
token 用環境變數 FINMIND_TOKEN（免費註冊即可，未登入額度很低）。

免費等級可用：日K、本益比/淨值比/殖利率、三大法人、融資融券、月營收、三大財報、股利、
            除權息結果、減資/分割、期貨三大法人、期貨日K。
需付費等級：集保股權分散（C2 策略）、依日期抓全市場（一次一天全部股票）。
"""
from __future__ import annotations

import datetime as dt
import os
import time
from pathlib import Path

import pandas as pd

API = "https://api.finmindtrade.com/api/v4/data"
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"

# 資料集 → 去重用的鍵
KEYS = {
    "TaiwanStockPrice": ["date"],
    "TaiwanStockPER": ["date"],
    "TaiwanStockInstitutionalInvestorsBuySell": ["date", "name"],
    "TaiwanStockMarginPurchaseShortSale": ["date"],
    "TaiwanStockMonthRevenue": ["date"],
    "TaiwanStockFinancialStatements": ["date", "type"],
    "TaiwanStockBalanceSheet": ["date", "type"],
    "TaiwanStockCashFlowsStatement": ["date", "type"],
    "TaiwanStockDividend": ["date", "year"],
    "TaiwanStockDividendResult": ["date"],
    "TaiwanStockSplitPrice": ["date"],
    "TaiwanStockHoldingSharesPer": ["date", "HoldingSharesLevel"],
    "TaiwanFuturesInstitutionalInvestors": ["date", "institutional_investors"],
    "TaiwanFuturesDaily": ["date", "contract_date", "trading_session"],
}
# 財報類一次抓整段就好（每季才更新）
SLOW = {"TaiwanStockFinancialStatements", "TaiwanStockBalanceSheet", "TaiwanStockCashFlowsStatement",
        "TaiwanStockDividend", "TaiwanStockDividendResult", "TaiwanStockSplitPrice", "TaiwanStockMonthRevenue"}


def _get(dataset: str, data_id: str | None, start: str, end: str | None = None, retries: int = 4) -> pd.DataFrame:
    import requests
    params = {"dataset": dataset, "start_date": start}
    if data_id:
        params["data_id"] = data_id
    if end:
        params["end_date"] = end
    tok = os.environ.get("FINMIND_TOKEN")
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    for k in range(retries):
        try:
            r = requests.get(API, params=params, headers=headers, timeout=90)
            j = r.json()
        except Exception as e:  # 網路錯誤：等一下再試
            print(f"  {dataset} {data_id}: {e}，{5 * (k + 1)} 秒後重試")
            time.sleep(5 * (k + 1))
            continue
        if j.get("status") == 200:
            return pd.DataFrame(j.get("data", []))
        msg = str(j.get("msg", ""))
        if "level" in msg.lower():
            raise PermissionError(f"{dataset} 需要 FinMind 付費等級：{msg}")
        if "limit" in msg.lower() or r.status_code == 402:
            wait = int(os.environ.get("FINMIND_WAIT", "600"))
            print(f"  FinMind 額度用完（{msg[:60]}），等 {wait // 60} 分鐘再繼續（可以 Ctrl+C 中斷，之後再跑會接著抓）")
            time.sleep(wait)
            continue
        print(f"  {dataset} {data_id}: {msg}")
        time.sleep(3)
    raise RuntimeError(f"抓取失敗：{dataset} {data_id}")


def load(dataset: str, data_id: str | None = None, start: str = "2013-01-01", refresh: bool = True,
         cache: Path | None = None) -> pd.DataFrame:
    """讀快取；refresh=True 時補抓快取最後一天之後的資料。"""
    cache = cache or CACHE
    f = cache / dataset / f"{data_id or '_all'}.csv.gz"
    old = pd.read_csv(f, dtype=str) if f.exists() else None
    if refresh:
        s = start
        if old is not None and len(old):
            last = pd.to_datetime(old["date"]).max()
            if dataset in SLOW:
                # 財報每季更新：快取超過 20 天才重抓近兩年
                if (pd.Timestamp.today() - pd.Timestamp(f.stat().st_mtime, unit="s")).days < 20:
                    return _typed(old)
                s = (last - pd.DateOffset(years=2)).strftime("%Y-%m-%d")
            else:
                s = (last - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        new = _get(dataset, data_id, s)
        if len(new):
            new = new.astype(str)
            df = pd.concat([old, new]) if old is not None else new
            keys = [k for k in KEYS.get(dataset, ["date"]) if k in df.columns]
            df = df.drop_duplicates(keys, keep="last").sort_values("date")
            f.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(f, index=False, compression="gzip")
            old = df
        elif old is None:
            return pd.DataFrame()
    return _typed(old) if old is not None else pd.DataFrame()


def _typed(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    for c in df.columns:
        if c in ("date", "stock_id", "name", "type", "year", "origin_name", "country", "Note", "contract_date",
                 "trading_session", "institutional_investors", "futures_id", "stock_or_cache_dividend",
                 "HoldingSharesLevel", "industry_category", "stock_name", "create_time",
                 "CashExDividendTradingDate", "StockExDividendTradingDate", "CashDividendPaymentDate",
                 "AnnouncementDate", "AnnouncementTime"):
            continue
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def stock_info(refresh: bool = False) -> pd.DataFrame:
    f = CACHE / "TaiwanStockInfo.csv"
    if f.exists() and not refresh:
        return pd.read_csv(f, dtype=str)
    d = _get("TaiwanStockInfo", None, "2000-01-01")
    f.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(f, index=False)
    return d.astype(str)


def common_stocks(info: pd.DataFrame) -> pd.DataFrame:
    """上市櫃 4 碼普通股（排除 ETF、權證、特別股、創新板以外的特殊代號）"""
    x = info[info["type"].isin(["twse", "tpex"]) & info["stock_id"].str.fullmatch(r"[1-9]\d{3}")]
    x = x[~x["industry_category"].isin(["ETF", "ETN", "受益證券", "存託憑證", "Index", "大盤"])]
    return x.drop_duplicates("stock_id", keep="last")


def load_daily_all(folder: str | Path) -> pd.DataFrame:
    """
    讀實驗室的全市場日線（每年一個 parquet，含已下市股票；欄位同 TaiwanStockPrice）。
    有這份資料就能做「當時成交值前 300 名」的動態股票池，不必一檔一檔抓。
    """
    fs = sorted(Path(folder).glob("*.parquet"))
    df = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    df["stock_id"] = df["stock_id"].astype(str)
    return df


# 下面是給 cli.py 用的批次抓取 ------------------------------------------------
STOCK_SETS = {
    "price": ["TaiwanStockPrice", "TaiwanStockDividendResult", "TaiwanStockSplitPrice"],
    "valuation": ["TaiwanStockPER"],
    "chips": ["TaiwanStockInstitutionalInvestorsBuySell", "TaiwanStockMarginPurchaseShortSale"],
    "fundamental": ["TaiwanStockMonthRevenue", "TaiwanStockFinancialStatements", "TaiwanStockBalanceSheet",
                    "TaiwanStockCashFlowsStatement", "TaiwanStockDividend"],
    "holders": ["TaiwanStockHoldingSharesPer"],  # 付費
}


def fetch_stocks(ids: list[str], sets=("price", "valuation", "chips", "fundamental"), start="2013-01-01"):
    todo = [(ds, s) for s in ids for k in sets for ds in STOCK_SETS[k]]
    t0 = time.time()
    for n, (ds, s) in enumerate(todo, 1):
        try:
            load(ds, s, start=start)
        except PermissionError as e:
            print(f"略過：{e}")
            sets = tuple(k for k in sets if ds not in STOCK_SETS[k])
        if n % 50 == 0:
            print(f"  {n}/{len(todo)}，已用 {(time.time() - t0) / 60:.1f} 分鐘")


def fetch_market(start="2013-01-01"):
    """大盤層資料：0050（基準）、台指期三大法人、小台/微台日K與法人（散戶多空比）"""
    load("TaiwanStockPrice", "0050", start=start)
    load("TaiwanStockDividendResult", "0050", start=start)
    load("TaiwanStockSplitPrice", "0050", start=start)
    load("TaiwanFuturesInstitutionalInvestors", "TX", start="2018-06-01")
    for fid in ["MTX", "TMF"]:
        s = "2018-06-01" if fid == "MTX" else "2024-07-01"
        load("TaiwanFuturesInstitutionalInvestors", fid, start=s)
        _fetch_futures_daily(fid, s)


def _fetch_futures_daily(fid, start):
    """期貨日K一次最多一年，分段抓"""
    f = CACHE / "TaiwanFuturesDaily" / f"{fid}.csv.gz"
    old = pd.read_csv(f, dtype=str) if f.exists() else None
    s = pd.Timestamp(start) if old is None else pd.to_datetime(old["date"]).max() - pd.Timedelta(days=7)
    parts = [old] if old is not None else []
    today = pd.Timestamp.today()
    while s <= today:
        e = min(s + pd.DateOffset(years=1) - pd.Timedelta(days=1), today)
        d = _get("TaiwanFuturesDaily", fid, s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d"))
        if len(d):
            parts.append(d.astype(str))
        s = e + pd.Timedelta(days=1)
    if parts:
        df = pd.concat(parts).drop_duplicates(["date", "contract_date", "trading_session"], keep="last")
        f.parent.mkdir(parents=True, exist_ok=True)
        df.sort_values("date").to_csv(f, index=False, compression="gzip")


def candidates_today(n: int = 400) -> list[str]:
    """
    用證交所、櫃買中心 OpenAPI（免費、免 token）取「最近一個交易日」成交值前 n 名的普通股，當作抓資料的候選名單。
    注意：這是今天的名單，拿來回測過去有倖存者偏誤；有全市場歷史日線（load_daily_all）時改用動態股票池。
    """
    import requests
    rows = []
    tw = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=60).json()
    rows += [(r["Code"], float(r["TradeValue"] or 0)) for r in tw]
    tp = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", timeout=60).json()
    rows += [(r["SecuritiesCompanyCode"], float(str(r["TransactionAmount"]).replace(",", "") or 0)) for r in tp]
    df = pd.DataFrame(rows, columns=["stock_id", "value"])
    df = df[df["stock_id"].str.fullmatch(r"[1-9]\d{3}")]
    return df.sort_values("value", ascending=False)["stock_id"].head(n).tolist()


def today_str() -> str:
    return dt.date.today().isoformat()
