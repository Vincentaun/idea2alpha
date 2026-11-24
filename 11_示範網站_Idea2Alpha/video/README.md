# 介紹影片（Idea2Alpha intro video）

| 檔案 | 內容 |
|---|---|
| `Idea2Alpha_intro.mp4` | 1 分 52 秒，1920×1080、30 fps，自然人聲英文旁白（Gemini 語音）與燒入的英文字幕 |
| `Idea2Alpha_intro.srt` | 同一份字幕的獨立檔（上傳 YouTube 等平台時可用） |
| `old_voice/` | 前一版（Windows 內建語音 Microsoft Zira），保留備查 |
| `source/` | 重新製作影片的程式 |

影片是實際操作線上網站 https://idea2alpha.pages.dev 錄下來的：動手試（開啟評估紀錄中的測試題 B03、換一個回答、看檢查與符合條件的股票）、翻譯員測試、回測、多空辯論、時光回放、問 AI（兩個即時問題）與語言切換。工作台那一段用的是評估紀錄，沒有即時呼叫翻譯員；問 AI 的兩個回答是錄影當時 gemini-3.5-flash-lite 即時產生的。

旁白由 Google Gemini 文字轉語音產生（`source/gemini_voice.py`，預設模型 `gemini-3.8-flash-tts`、聲音 Kore），用你自己的 Gemini API 金鑰在你自己的 PowerShell 視窗執行；金鑰不會寫進任何檔案。

重新製作（需要 Node.js 18 以上、Python 的 numpy、Pillow、imageio-ffmpeg，以及已安裝的 Google Chrome），在 `source` 資料夾裡：

```powershell
python -I gemini_voice.py lines.json voice_gemini
python -I trim_voice.py voice_gemini voice
node record.mjs
python -I assemble.py out\Idea2Alpha_intro.mp4
```

- `lines.json`：每一句旁白（`say` 給語音唸，`show` 是字幕）。
- `gemini_voice.py`：每一句產生一個 WAV；已經有檔案的句子會略過，所以中斷後可以直接重跑。換聲音加 `--voice Charon --force`。
- `trim_voice.py`：去掉頭尾靜音、統一音量、轉成 44.1 kHz；`--tempo 1.05` 可在不變音調的情況下加快語速。
- `record.mjs`、`cdp.mjs`：用無頭 Chrome 開啟網站、移動游標、捲動、點擊並錄下畫面，依每句旁白的長度控制節奏。
- `assemble.py`：剪掉等待 AI 的時間、在點選選單的那一刻接到下一頁、加快換頁片段、加上字幕與旁白，輸出 MP4 與 SRT。
- `cards/`：片頭與片尾。

---

English summary: `Idea2Alpha_intro.mp4` (1:52, 1080p) walks through the live site with natural English narration from Gemini text-to-speech and burned-in subtitles; `Idea2Alpha_intro.srt` holds the same subtitles, and `old_voice/` keeps the earlier Windows-voice version. The workbench part uses a recorded test idea; the two Ask the AI answers are live gemini-3.5-flash-lite replies. `source/` rebuilds the video with the commands above; `gemini_voice.py` runs with your own key in your own PowerShell window.
