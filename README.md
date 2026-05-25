# 政治新聞負面聲量網站

這是一個可以給一般使用者打開看的網站：使用者輸入政治人物或政黨，網站抓取自由時報政治 RSS 與中天新聞搜尋，再用 AI 判斷新聞文本對該對象的負面框架強度。

它衡量的是「媒體文本中的負面聲量」，不是民調，也不是人民真實反感度。

## 設定 API key (NVIDIA NIM)

本系統目前使用 **NVIDIA NIM (LLama 3.1)** 系列模型。

請在專案目錄下建立 `.env` 檔案，內容如下：

```text
NVIDIA_API_KEY=您的 NVIDIA API Key
AI_API_BASE=https://integrate.api.nvidia.com/v1
AI_MODEL=nvidia/llama-3.1-8b-instruct
```

或者使用 PowerShell 設置環境變數：

```powershell
$env:NVIDIA_API_KEY="您的 NVIDIA API Key"
```

## 執行

啟動網站：

```powershell
python .\web_app.py
```

打開：

```text
http://127.0.0.1:8000
```

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
