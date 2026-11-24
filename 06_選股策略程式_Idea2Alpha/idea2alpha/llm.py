"""
llm — 生成式 AI 呼叫的唯一出入口（翻譯員、多空辯論、裁判共用）

設定（環境變數）：
  IDEA2ALPHA_LLM     anthropic | openai | gemini | replay
  IDEA2ALPHA_MODEL   模型名稱（例：claude-sonnet-5-5；OpenAI、Gemini 請填各家目前的模型名稱）
  ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY
  IDEA2ALPHA_REPLAY  replay 模式讀取的錄製檔（JSONL），用於離線重現與自我測試

每一次呼叫都寫進 runs/llm_calls.jsonl：時間、供應商、模型、提示詞版本、溫度、完整輸入與輸出、用量。
報告裡的模型名稱、日期、提示詞版本，一律從這個檔案抄，不要憑記憶寫。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
PROMPTS = ROOT / "prompts"


def load_prompt(name: str) -> tuple[str, str]:
    """回傳 (提示詞內容, 版本字串)。版本 = 檔名 + 內容雜湊前 8 碼，改一個字版本就會變。"""
    f = PROMPTS / f"{name}.md"
    text = f.read_text(encoding="utf-8")
    return text, f"{name}@{hashlib.sha256(text.encode()).hexdigest()[:8]}"


class _Replay:
    """依序回放錄製好的回應（離線測試、重現他人結果）"""

    def __init__(self, path):
        self.items = [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]
        self.i = 0

    def next(self):
        if self.i >= len(self.items):
            raise RuntimeError("replay 檔的回應用完了")
        x = self.items[self.i]
        self.i += 1
        return x["text"] if isinstance(x, dict) else str(x)


_replay = None


def chat(system: str, messages: list[dict], prompt_version: str, temperature: float = 0.0,
         max_tokens: int = 2000, tag: str = "") -> dict:
    """
    messages：[{"role": "user"|"assistant", "content": "..."}]
    回傳 {"text", "provider", "model", "created", "usage"}
    """
    global _replay
    provider = os.environ.get("IDEA2ALPHA_LLM", "").lower()
    model = os.environ.get("IDEA2ALPHA_MODEL", "")
    created = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    usage = {}
    if provider == "replay":
        if _replay is None:
            _replay = _Replay(os.environ["IDEA2ALPHA_REPLAY"])
        text, model = _replay.next(), model or "replay"
    elif provider in ("anthropic", "openai", "gemini"):
        if not model:
            raise RuntimeError("請設定 IDEA2ALPHA_MODEL（模型名稱）")
        text, usage = _call(provider, model, system, messages, temperature, max_tokens)
    else:
        raise RuntimeError("請設定 IDEA2ALPHA_LLM = anthropic / openai / gemini（或 replay 做離線測試）")
    rec = {"created": created, "provider": provider, "model": model, "prompt_version": prompt_version,
           "temperature": temperature, "max_tokens": max_tokens, "tag": tag,
           "system_sha256": hashlib.sha256(system.encode()).hexdigest(), "messages": messages,
           "text": text, "usage": usage}
    RUNS.mkdir(exist_ok=True)
    with open(RUNS / "llm_calls.jsonl", "a", encoding="utf-8") as fo:
        fo.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


RETRY_STATUS = {429, 500, 502, 503, 504, 529}


def _post(url, headers, payload, tries=5, sleep=None):
    """暫時性錯誤（429 額度、5xx／529 伺服器忙、連線逾時）自動重試，間隔 5、10、20、40 秒（有 retry-after 就照它，
    最多 60 秒）。eval-translator 一次要呼叫一兩百次，新帳號的每分鐘額度很容易碰到上限，不重試會整批中斷。"""
    import time

    import requests
    sleep = sleep or time.sleep
    for i in range(tries):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=180)
        except (requests.ConnectionError, requests.Timeout):
            if i == tries - 1:
                raise
            sleep(5 * 2 ** i)
            continue
        if r.status_code not in RETRY_STATUS or i == tries - 1:
            return r
        try:
            wait = min(60.0, float(r.headers.get("retry-after", "")))
        except ValueError:
            wait = 5 * 2 ** i
        sleep(wait)


def _json(r, provider):
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"{provider} API 回應不是 JSON（HTTP {r.status_code}）") from None


def _call(provider, model, system, messages, temperature, max_tokens):
    if provider == "anthropic":
        r = _post("https://api.anthropic.com/v1/messages", {
            "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
            "content-type": "application/json"},
            {"model": model, "max_tokens": max_tokens, "temperature": temperature,
             "system": system, "messages": messages})
        j = _json(r, provider)
        if "content" not in j:
            raise RuntimeError(f"Anthropic API 錯誤：{j}")
        return "".join(b.get("text", "") for b in j["content"]), j.get("usage", {})
    if provider == "openai":
        r = _post("https://api.openai.com/v1/chat/completions", {
            "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            {"model": model, "temperature": temperature, "max_tokens": max_tokens,
             "messages": [{"role": "system", "content": system}] + messages})
        j = _json(r, provider)
        if "choices" not in j:
            raise RuntimeError(f"OpenAI API 錯誤：{j}")
        return j["choices"][0]["message"]["content"], j.get("usage", {})
    # gemini：金鑰放在標頭，不放網址（網址會出現在錯誤訊息與紀錄裡）
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                for m in messages]
    r = _post(url, {"x-goog-api-key": os.environ["GEMINI_API_KEY"], "content-type": "application/json"}, {
        "systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}})
    j = _json(r, provider)
    if "candidates" not in j:
        raise RuntimeError(f"Gemini API 錯誤：{j}")
    return "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"]), j.get("usageMetadata", {})


def parse_json(text: str):
    """模型回應裡取出第一個 JSON 物件（容許外面包了 ```json 區塊）"""
    t = text.strip()
    if "```" in t:
        parts = t.split("```")
        for p in parts[1::2]:
            p = p[4:] if p.lower().startswith("json") else p
            try:
                return json.loads(p.strip())
            except json.JSONDecodeError:
                continue
    s, e = t.find("{"), t.rfind("}")
    if s < 0 or e < 0:
        raise ValueError("回應裡沒有 JSON")
    return json.loads(t[s:e + 1])
