import argparse
import io
import json
import os
import sqlite3
import threading
import traceback
from contextlib import redirect_stdout
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import political_news_ai as engine


REFRESH_LOCK = threading.Lock()
JOB_STATE = {
    "running": False,
    "last_ok": None,
    "last_error": None,
    "log": "",
}


HTML = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>自由時報政治新聞 AI 分析</title>
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
  <header class="hero">
    <div class="brand">自由時報政治新聞 AI 分析 <span class="version">v1.0.3</span></div>
    <h1>查政治人物或政黨的負面聲量</h1>
    <form id="searchForm" class="search-box">
      <input id="targetInput" type="search" placeholder="輸入政治人物或政黨，例如：賴清德、民眾黨" autocomplete="off" required>
      <button id="searchButton" class="primary" type="submit" title="搜尋">
        <span aria-hidden="true">⌕</span>
        <span>搜尋</span>
      </button>
    </form>
    <p>可切換自由時報或中天新聞。每次更新會盡量收集每個來源最新政治新聞 25 篇，不是固定幾天；判讀代表新聞文本中的正負面框架，不等於民調。</p>
  </header>

  <main>
    <section class="controls" aria-label="篩選">
      <label>
        <span>新聞來源</span>
        <select id="sourceSelect">
          <option value="自由時報政治">自由時報政治</option>
          <option value="中天新聞政治">中天新聞政治</option>
        </select>
      </label>
      <label>
        <span>檢測範圍</span>
        <select id="modeSelect">
          <option value="body">內文</option>
          <option value="title">標題</option>
        </select>
      </label>
      <button id="refreshButton" type="button" title="重新整理目前搜尋">↻ 更新目前搜尋</button>
    </section>

    <section class="metric-guide" aria-label="指標說明">
      <div>
        <strong>檢測範圍</strong>
        <span>選「標題」只判斷新聞標題；選「內文」會使用標題與目前抓到的摘要或內文。</span>
      </div>
      <div>
        <strong>判讀結果</strong>
        <span>畫面只顯示負面、中立或正面，不顯示分數。</span>
      </div>
      <div>
        <strong>負面標準</strong>
        <span>系統仍在背景用分數判斷：高負面為負面，低負面為正面，中間為中立。</span>
      </div>
    </section>

    <section class="status-row" aria-live="polite">
      <div>
        <strong id="targetName">尚未搜尋</strong>
        <span>查詢對象</span>
      </div>
      <div>
        <strong id="analysisCount">0</strong>
        <span>相關判讀</span>
      </div>
      <div>
        <strong id="updatedAt">尚未更新</strong>
        <span>最後更新</span>
      </div>
    </section>

    <section class="panel">
      <div class="panel-heading">
        <h2>分析摘要</h2>
        <span id="modelName"></span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>查詢</th>
              <th>來源</th>
              <th>篇數</th>
              <th>整體判讀</th>
            </tr>
          </thead>
          <tbody id="summaryBody"></tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <div class="panel-heading">
        <h2 id="articleSectionTitle">文章判讀</h2>
        <span id="jobStatus"></span>
      </div>
      <div id="articles" class="article-list"></div>
    </section>
  </main>

  <script src="/static/app.js"></script>
</body>
</html>
"""


CSS = """
:root {
  color-scheme: dark;
  --ink: #f3f6fb;
  --muted: #9aa4b2;
  --line: #2c3442;
  --paper: #121821;
  --paper-2: #171f2b;
  --ground: #05070b;
  --accent: #35a7ff;
  --accent-strong: #8bd0ff;
  --danger: #ff4d5e;
  --gold: #f3b94e;
  --green: #27c287;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  color: var(--ink);
  background: var(--ground);
  font-family: system-ui, -apple-system, "Segoe UI", "Noto Sans TC", sans-serif;
}

.hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding: 56px clamp(16px, 4vw, 40px) 32px;
  background:
    radial-gradient(circle at top, rgba(53, 167, 255, 0.16), transparent 34rem),
    linear-gradient(180deg, #0b111b 0%, #05070b 100%);
  border-bottom: 1px solid var(--line);
  text-align: center;
}

.brand {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--accent-strong);
  font-weight: 800;
}

.version {
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--muted);
  font-size: 12px;
  padding: 2px 8px;
}

h1, h2, h3, p { margin-top: 0; }
h1 { max-width: 900px; margin-bottom: 0; font-size: clamp(32px, 6vw, 64px); line-height: 1.05; }
h2 { margin-bottom: 0; font-size: 20px; }
p { color: var(--muted); line-height: 1.6; }

main {
  width: min(1180px, calc(100% - 32px));
  margin: 0 auto;
  padding: 22px 0 40px;
}

button, input, select {
  font: inherit;
}

button {
  min-height: 40px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--paper);
  color: var(--ink);
  cursor: pointer;
}

button:disabled {
  cursor: progress;
  opacity: 0.65;
}

.primary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 0 18px;
  border-color: var(--accent);
  background: var(--accent);
  color: #04111f;
  font-weight: 800;
}

.search-box {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  width: min(760px, 100%);
  gap: 10px;
  padding: 8px;
  border: 1px solid #33445a;
  border-radius: 8px;
  background: rgba(18, 24, 33, 0.92);
}

.search-box input {
  min-height: 54px;
  width: 100%;
  border: 1px solid transparent;
  border-radius: 8px;
  background: #0b111b;
  color: var(--ink);
  font-size: 17px;
  padding: 0 14px;
}

.search-box button {
  min-width: 112px;
  min-height: 54px;
}

.controls {
  display: grid;
  grid-template-columns: minmax(160px, 220px) minmax(160px, 220px) auto;
  gap: 12px;
  align-items: end;
  margin-bottom: 16px;
}

.metric-guide {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}

.metric-guide > div {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--paper-2);
  padding: 12px 14px;
}

.metric-guide strong {
  display: block;
  margin-bottom: 6px;
  color: var(--ink);
  font-size: 14px;
}

.metric-guide span {
  display: block;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.55;
}

label {
  display: grid;
  gap: 6px;
  color: var(--muted);
  font-size: 13px;
  font-weight: 650;
}

input, output, select {
  min-height: 40px;
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--paper);
  color: var(--ink);
  padding: 0 12px;
}

select {
  appearance: auto;
}

input[type="range"] {
  padding: 0;
  accent-color: var(--accent);
}

output {
  display: grid;
  place-items: center;
  font-weight: 700;
}

.status-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}

.status-row > div,
.table-wrap,
.article {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--paper);
}

.status-row > div {
  padding: 14px 16px;
}

.status-row strong {
  display: block;
  font-size: 22px;
}

.status-row span,
.panel-heading span,
.meta,
.evidence {
  color: var(--muted);
  font-size: 13px;
}

.panel {
  margin-top: 16px;
}

.panel-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.table-wrap {
  overflow: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  min-width: 640px;
}

th, td {
  padding: 12px 14px;
  text-align: left;
  border-bottom: 1px solid #202a38;
}

th {
  background: #1b2533;
  color: #dce8f7;
  font-size: 13px;
}

tr:last-child td { border-bottom: 0; }

.article-list {
  display: grid;
  gap: 12px;
}

.article {
  display: grid;
  grid-template-columns: 74px 1fr;
  gap: 14px;
  padding: 14px;
}

.article-plain {
  grid-template-columns: 1fr;
}

.sentiment-badge {
  min-width: 66px;
  min-height: 40px;
  padding: 0 10px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  background: var(--green);
  color: #04111f;
  font-weight: 800;
}

.sentiment-badge.negative { background: var(--danger); color: #fff; }
.sentiment-badge.neutral { background: var(--gold); color: #1f1a12; }
.sentiment-badge.positive { background: var(--green); color: #04111f; }

.article h3 {
  margin-bottom: 6px;
  font-size: 17px;
  line-height: 1.35;
}

.article a {
  color: var(--accent-strong);
  text-decoration-thickness: 1px;
  text-underline-offset: 3px;
}

.meta, .reason, .evidence {
  margin-bottom: 6px;
}

.reason {
  color: #d5dae3;
  font-size: 14px;
}

.empty {
  border: 1px dashed var(--line);
  border-radius: 8px;
  padding: 24px;
  text-align: center;
  color: var(--muted);
  background: rgba(18, 24, 33, 0.7);
}

@media (max-width: 760px) {
  .controls,
  .metric-guide,
  .status-row,
  .search-box {
    grid-template-columns: 1fr;
  }

  .article {
    grid-template-columns: 1fr;
  }
}
"""


JS = """
const state = {
  summary: [],
  articles: [],
  fallbackArticles: [],
  meta: {},
  target: "",
  source: "自由時報政治",
  mode: "body",
};

const $ = (id) => document.getElementById(id);

function formatDate(value) {
  if (!value) return "尚未更新";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-TW", { hour12: false });
}

function sentimentClass(score) {
  if (score >= 0.65) return "negative";
  if (score >= 0.4) return "neutral";
  return "positive";
}

function sentimentLabel(score) {
  if (score >= 0.65) return "負面";
  if (score >= 0.4) return "中立";
  return "正面";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function render() {
  $("targetName").textContent = state.target || "尚未搜尋";
  $("analysisCount").textContent = state.meta.analysis_count ?? 0;
  $("updatedAt").textContent = formatDate(state.meta.last_analyzed_at);
  $("modelName").textContent = state.meta.model ? `模型：${state.meta.model}` : "";
  $("sourceSelect").value = state.source;
  $("modeSelect").value = state.mode;
  $("articleSectionTitle").textContent = `${state.source}文章判讀`;

  $("summaryBody").innerHTML = state.summary.length
    ? state.summary.map((row) => `
      <tr>
        <td>${escapeHtml(row.entity)}</td>
        <td>${escapeHtml(row.source)}</td>
        <td>${row.mentions}</td>
        <td>${sentimentLabel(Number(row.avg_negative))}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="4">輸入政治人物或政黨後按搜尋。</td></tr>`;

  const articles = state.articles;
  $("articles").innerHTML = articles.length
    ? articles.map((item) => `
      <article class="article">
        <div class="sentiment-badge ${sentimentClass(Number(item.negative_score))}">${sentimentLabel(Number(item.negative_score))}</div>
        <div>
          <h3>${escapeHtml(item.entity)} · ${sentimentLabel(Number(item.negative_score))}</h3>
          <p class="meta">${escapeHtml(item.source)} · ${escapeHtml(item.published || "")}</p>
          <p><a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title)}</a></p>
          <p class="reason">${escapeHtml(item.reason || "")}</p>
          <p class="evidence">${escapeHtml(item.evidence || "")}</p>
        </div>
      </article>
    `).join("")
    : state.fallbackArticles.length
      ? `
        <div class="empty">這個來源目前沒有找到含「${escapeHtml(state.target)}」的判讀結果。下面先列出最新抓到的文章。</div>
        ${state.fallbackArticles.map((item) => `
          <article class="article article-plain">
            <div>
              <h3>${escapeHtml(item.source)}</h3>
              <p class="meta">${escapeHtml(item.published || "")}</p>
              <p><a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title)}</a></p>
              <p class="evidence">${escapeHtml(item.summary || "")}</p>
            </div>
          </article>
        `).join("")}
      `
      : `<div class="empty">目前沒有符合條件的文章。</div>`;
}

async function loadData(target = state.target, source = $("sourceSelect").value, mode = $("modeSelect").value) {
  const params = new URLSearchParams();
  if (target) params.set("target", target);
  if (source) params.set("source", source);
  if (mode) params.set("mode", mode);
  const query = params.toString() ? `?${params}` : "";
  const response = await fetch(`/api/dashboard${query}`);
  if (!response.ok) throw new Error("load failed");
  const data = await response.json();
  state.summary = data.summary;
  state.articles = data.articles;
  state.fallbackArticles = data.fallback_articles || [];
  state.meta = data.meta;
  state.target = data.target || target || "";
  state.source = data.source || source || "自由時報政治";
  state.mode = data.mode || mode || "body";
  render();
}

async function refreshData(target) {
  const searchButton = $("searchButton");
  const refreshButton = $("refreshButton");
  searchButton.disabled = true;
  refreshButton.disabled = true;
  $("jobStatus").textContent = "更新中...";
  try {
    const source = $("sourceSelect").value;
    const mode = $("modeSelect").value;
    const body = new URLSearchParams({ target, source, mode });
    const headers = {};
    const savedToken = localStorage.getItem("adminToken");
    if (savedToken) headers["X-Admin-Token"] = savedToken;
    let response = await fetch("/api/refresh", { method: "POST", headers, body });
    if (response.status === 401) {
      const token = window.prompt("請輸入更新密碼");
      if (!token) throw new Error("沒有更新權限");
      localStorage.setItem("adminToken", token);
      response = await fetch("/api/refresh", {
        method: "POST",
        headers: { "X-Admin-Token": token },
        body,
      });
    }
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || "refresh failed");
    $("jobStatus").textContent = "更新完成";
    await loadData(target, source, mode);
  } catch (error) {
    $("jobStatus").textContent = `更新失敗：${error.message}`;
  } finally {
    searchButton.disabled = false;
    refreshButton.disabled = false;
  }
}

$("sourceSelect").addEventListener("change", () => loadData(state.target, $("sourceSelect").value, $("modeSelect").value));
$("modeSelect").addEventListener("change", () => loadData(state.target, $("sourceSelect").value, $("modeSelect").value));

$("searchForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const target = $("targetInput").value.trim();
  if (!target) return;
  await refreshData(target);
});

$("refreshButton").addEventListener("click", async () => {
  const target = $("targetInput").value.trim() || state.target;
  if (!target) {
    $("targetInput").focus();
    return;
  }
  await refreshData(target);
});

loadData().catch((error) => {
  $("jobStatus").textContent = `讀取失敗：${error.message}`;
});
"""


def query_dashboard(target=None, source=None, mode=None):
    engine.init_db()
    target = (target or "").strip()
    source = (source or "自由時報政治").strip()
    mode = (mode or "body").strip()
    if not target:
        return {
            "summary": [],
            "articles": [],
            "meta": {
                "article_count": 0,
                "analysis_count": 0,
                "last_analyzed_at": None,
                "model": None,
            },
            "job": JOB_STATE,
            "target": "",
            "source": source,
            "mode": mode,
        }

    where = "WHERE x.entity = ? AND a.source = ?"
    params = [target, source]

    with sqlite3.connect(engine.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        summary = conn.execute(
            f"""
            SELECT entity, entity_type, a.source, COUNT(*) AS mentions,
                   AVG(negative_score) AS avg_negative,
                   MAX(model) AS model
            FROM analyses x
            JOIN articles a ON a.id = x.article_id
            {where}
            GROUP BY entity, entity_type, a.source
            ORDER BY avg_negative DESC, mentions DESC
            """,
            params,
        ).fetchall()
        articles = conn.execute(
            f"""
            SELECT x.entity, x.entity_type, x.negative_score, x.sentiment,
                   x.confidence, x.evidence, x.reason, x.model, x.analyzed_at,
                   a.title, a.url, a.source, a.published
            FROM analyses x
            JOIN articles a ON a.id = x.article_id
            {where}
            ORDER BY x.negative_score DESC, x.id DESC
            LIMIT 200
            """,
            params,
        ).fetchall()
        fallback_articles = []
        if not articles:
            fallback_articles = conn.execute(
                """
                SELECT source, title, url, published, summary
                FROM articles
                WHERE source = ?
                ORDER BY id DESC
                LIMIT 20
                """,
                (source,),
            ).fetchall()
        meta = conn.execute(
            f"""
            SELECT
              (SELECT COUNT(*) FROM articles) AS article_count,
              (SELECT COUNT(*) FROM analyses x JOIN articles a ON a.id = x.article_id {where}) AS analysis_count,
              (SELECT MAX(analyzed_at) FROM analyses x JOIN articles a ON a.id = x.article_id {where}) AS last_analyzed_at,
              (SELECT model FROM analyses x JOIN articles a ON a.id = x.article_id {where} ORDER BY analyzed_at DESC, x.id DESC LIMIT 1) AS model
            """,
            params * 3,
        ).fetchone()
    return {
        "summary": [dict(row) for row in summary],
        "articles": [dict(row) for row in articles],
        "fallback_articles": [dict(row) for row in fallback_articles],
        "meta": dict(meta),
        "job": JOB_STATE,
        "target": target,
        "source": source,
        "mode": mode,
    }


def run_refresh(use_ai=True, target=None, source=None, mode=None):
    target = (target or "").strip()
    source = (source or "自由時報政治").strip()
    mode = (mode or "body").strip()
    if not target:
        return {"error": "請輸入政治人物或政黨"}
    if not REFRESH_LOCK.acquire(blocking=False):
        return {"error": "已經有更新工作在跑"}

    JOB_STATE.update({"running": True, "last_error": None, "log": ""})
    log = io.StringIO()
    try:
        with redirect_stdout(log):
            engine.crawl(limit_per_source=int(os.getenv("CRAWL_LIMIT_PER_SOURCE", "25")))
            if source == "中天新聞政治":
                engine.crawl_site_search(
                    source_name=source,
                    site_domain="ctinews.com",
                    target=target,
                    limit=int(os.getenv("SOURCE_SEARCH_LIMIT", "20")),
                )
            engine.analyze(
                limit=int(os.getenv("ANALYZE_LIMIT", "120")),
                use_ai=use_ai,
                target=target,
                force=True,
                mode=mode,
            )
        JOB_STATE.update({"running": False, "last_ok": engine.now_iso(), "log": log.getvalue()})
        return {"ok": True, "log": JOB_STATE["log"]}
    except Exception as exc:
        JOB_STATE.update(
            {
                "running": False,
                "last_error": str(exc),
                "log": log.getvalue() + "\n" + traceback.format_exc(),
            }
        )
        return {"error": str(exc), "log": JOB_STATE["log"]}
    finally:
        REFRESH_LOCK.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "PoliticalNewsAI/0.2"

    def log_message(self, fmt, *args):
        return

    def send_text(self, body, content_type="text/plain; charset=utf-8", status=HTTPStatus.OK):
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_json(self, payload, status=HTTPStatus.OK):
        self.send_text(
            json.dumps(payload, ensure_ascii=False),
            content_type="application/json; charset=utf-8",
            status=status,
        )

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self.send_text(HTML, content_type="text/html; charset=utf-8")
        elif path == "/static/app.css":
            self.send_text(CSS, content_type="text/css; charset=utf-8")
        elif path == "/static/app.js":
            self.send_text(JS, content_type="application/javascript; charset=utf-8")
        elif path == "/api/dashboard":
            query = parse_qs(urlparse(self.path).query)
            self.send_json(
                query_dashboard(
                    target=query.get("target", [""])[0],
                    source=query.get("source", ["自由時報政治"])[0],
                    mode=query.get("mode", ["body"])[0],
                )
            )
        elif path == "/api/job":
            self.send_json(JOB_STATE)
        else:
            self.send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/refresh":
            self.send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return

        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode("utf-8")) if length else {}

        token = os.getenv("ADMIN_TOKEN")
        if token:
            query = parse_qs(urlparse(self.path).query)
            provided = self.headers.get("X-Admin-Token") or query.get("token", [""])[0]
            if provided != token:
                self.send_json({"error": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                return

        use_ai = os.getenv("DISABLE_AI", "").lower() not in {"1", "true", "yes"}
        result = run_refresh(
            use_ai=use_ai,
            target=form.get("target", [""])[0],
            source=form.get("source", ["自由時報政治"])[0],
            mode=form.get("mode", ["body"])[0],
        )
        status = HTTPStatus.OK if "error" not in result else HTTPStatus.CONFLICT
        self.send_json(result, status=status)


def main(argv=None):
    port = int(os.environ.get("PORT", 8000))

    engine.init_db()
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Serving http://0.0.0.0:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
