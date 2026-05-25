# 政治新聞負面聲量網站

這是一個可以給一般使用者打開看的網站：使用者輸入政治人物或政黨，網站抓取自由時報政治 RSS 與中天新聞搜尋，再用 AI 判斷新聞文本對該對象的負面框架強度。

它衡量的是「媒體文本中的負面聲量」，不是民調，也不是人民真實反感度。

## 設定 API key (Google Gemini)

本系統目前預設使用 **Google Gemini (gemini-1.5-flash)** 模型，它在 Google AI Studio 提供相當優渥的免費額度。

請不要把 key 寫進程式。用 PowerShell 設環境變數：

```powershell
$env:GOOGLE_API_KEY="您的 Google API Key"
```

如果您尚未取得 Key，請至 [Google AI Studio](https://aistudio.google.com/app/apikey) 申請。

## 執行

啟動網站：

```powershell
python .\web_app.py
```

打開：

```text
http://127.0.0.1:8000
```

如果是在 Heroku 或其他雲端平台執行，請確保已設置 `GOOGLE_API_KEY` 環境變數。

## 常用命令

只抓新聞：

```powershell
python .\political_news_ai.py crawl
```

只分析尚未分析的新聞 (需有 API Key)：

```powershell
python .\political_news_ai.py analyze --ai
```

強制重新分析某個對象：

```powershell
python .\political_news_ai.py analyze --ai --target "某政治人物" --force
```

## 自訂名單

政治人物與政黨在 `config/entities.json`。

新聞來源在 `config/sources.json`。
