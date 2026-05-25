<<<<<<< HEAD
# 政治新聞負面聲量網站

這是一個可以給一般使用者打開看的網站：使用者輸入政治人物或政黨，網站只抓自由時報政治 RSS，再用 AI 判斷新聞文本對該對象的負面框架強度。

它衡量的是「媒體文本中的負面聲量」，不是民調，也不是人民真實反感度。

## 設定 API key

請不要把 key 寫進程式。用 PowerShell 設環境變數：

```powershell
$env:NVIDIA_API_KEY="你的新 NVIDIA API key"
$env:AI_API_BASE="https://integrate.api.nvidia.com/v1"
$env:AI_MODEL="nvidia/nemotron-3-super-120b-a12b"
```

如果你剛剛已經把 key 貼到聊天裡，建議到 NVIDIA 後台撤銷並重建一把。

`.env.example` 是給別人看的範本，不能放真的 API key。真正的 key 請放在自己的 `.env` 或 PowerShell 環境變數裡。

## 執行

啟動網站：

```powershell
python .\web_app.py
```

打開：

```text
http://127.0.0.1:8000
```

如果要讓同一個 Wi-Fi / 區網的人也能打開：

```powershell
python .\web_app.py --host 0.0.0.0 --port 8000
```

然後請他們用你的電腦區網 IP，例如：

```text
http://你的區網IP:8000
```

如果要放到公開網路，建議設定更新密碼，讓大家可以看，但只有你能觸發爬蟲與 AI 分析：

```powershell
$env:ADMIN_TOKEN="換成一組你自己的密碼"
python .\web_app.py --host 0.0.0.0 --port 8000
```

第一次按「更新」時網站會要求輸入這組密碼。

命令列一次跑完：

```powershell
python .\political_news_ai.py run --ai
```

產出的報表在：

```text
reports\index.html
```

## 常用命令

只抓新聞：

```powershell
python .\political_news_ai.py crawl
```

只分析尚未分析的新聞：

```powershell
python .\political_news_ai.py analyze --ai
```

換模型或調整判讀規則後，強制重新分析舊資料：

```powershell
python .\political_news_ai.py analyze --ai --target "賴清德" --force
```

只分析某個人物或政黨：

```powershell
python .\political_news_ai.py run --ai --target "賴清德"
```

不用 AI，改用關鍵字 fallback：

```powershell
python .\political_news_ai.py run
```

## 自訂名單

政治人物與政黨在 `config/entities.json`。

新聞來源在 `config/sources.json`。目前限定自由時報政治 RSS。
=======
# myapp
>>>>>>> 813f1a1a4f455a2f9d0cb6f9dcc3f2f054d5d2ac
