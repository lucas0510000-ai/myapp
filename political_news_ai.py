import argparse
import html
import json
import os
import re
import ssl
import sqlite3
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
DB_PATH = DATA_DIR / "news.db"
SOURCES_PATH = ROOT / "config" / "sources.json"
ENTITIES_PATH = ROOT / "config" / "entities.json"

DEFAULT_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.5-flash"


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def request_url(url, timeout=25):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "political-news-ai/0.1 (+local research tool)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), ssl.SSLCertVerificationError):
            context = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=timeout, context=context) as response:
                return response.read()
        raise


def clean_text(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def init_db():
    DATA_DIR.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT,
                url TEXT NOT NULL UNIQUE,
                published TEXT,
                fetched_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER NOT NULL,
                entity TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                negative_score REAL NOT NULL,
                sentiment TEXT NOT NULL,
                confidence REAL NOT NULL,
                evidence TEXT,
                reason TEXT,
                model TEXT NOT NULL,
                analyzed_at TEXT NOT NULL,
                UNIQUE(article_id, entity),
                FOREIGN KEY(article_id) REFERENCES articles(id)
            )
            """
        )


def parse_rss_items(xml_bytes):
    root = ET.fromstring(xml_bytes)
    channel_items = root.findall(".//item")
    if channel_items:
        for item in channel_items:
            yield {
                "title": clean_text(item.findtext("title")),
                "summary": clean_text(item.findtext("description")),
                "url": clean_text(item.findtext("link")),
                "published": clean_text(item.findtext("pubDate")),
            }
        return

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//atom:entry", ns):
        link = ""
        link_node = entry.find("atom:link", ns)
        if link_node is not None:
            link = link_node.attrib.get("href", "")
        yield {
            "title": clean_text(entry.findtext("atom:title", default="", namespaces=ns)),
            "summary": clean_text(entry.findtext("atom:summary", default="", namespaces=ns)),
            "url": clean_text(link),
            "published": clean_text(entry.findtext("atom:updated", default="", namespaces=ns)),
        }


def parse_html_items(html_bytes, base_url):
    text = html_bytes.decode("utf-8", errors="ignore")
    seen = set()
    link_pattern = r'<a\b[^>]*href=["\']([^"\']*?/news/items/[^"\']+)["\'][^>]*>(.*?)</a>'
    for match in re.finditer(link_pattern, text, re.I | re.S):
        href, label = match.groups()
        url = urllib.parse.urljoin(base_url, html.unescape(href))
        title = clean_text(label)
        if not title or len(title) < 8 or url in seen or "訂閱" in title or "廣告" in title:
            continue
        seen.add(url)
        yield {
            "title": title,
            "summary": "",
            "url": url,
            "published": "",
        }


def parse_source_items(response_bytes, source_url):
    try:
        return list(parse_rss_items(response_bytes))
    except ET.ParseError:
        return list(parse_html_items(response_bytes, source_url))


def crawl_html_source(conn, source_name, source_url, limit_per_source):
    inserted = 0
    items = parse_html_items(request_url(source_url), source_url)
    saved = 0
    for item in items:
        if saved >= limit_per_source:
            break
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO articles
            (source, title, summary, url, published, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                source_name,
                item["title"],
                item["summary"],
                item["url"],
                item["published"],
                now_iso(),
            ),
        )
        inserted += cur.rowcount
        saved += 1
    return inserted, saved


def crawl(limit_per_source):
    init_db()
    sources = load_json(SOURCES_PATH)
    inserted = 0
    with sqlite3.connect(DB_PATH) as conn:
        for source in sources:
            name = source["name"]
            url = source["url"]
            print(f"Fetching {name}...")
            try:
                if "ctinews.com" in url:
                    inserted_count, saved_count = crawl_html_source(conn, name, url, limit_per_source)
                    inserted += inserted_count
                    print(f"  collected {saved_count} article link(s).")
                    continue
                items = parse_source_items(request_url(url), url)[:limit_per_source]
            except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
                print(f"  skipped: {exc}")
                continue

            for item in items:
                if not item["title"] or not item["url"]:
                    continue
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO articles
                    (source, title, summary, url, published, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        item["title"],
                        item["summary"],
                        item["url"],
                        item["published"],
                        now_iso(),
                    ),
                )
                inserted += cur.rowcount
            time.sleep(0.5)
    print(f"Added {inserted} new article(s).")


def crawl_site_search(source_name, site_domain, target, limit=20):
    init_db()
    if site_domain == "ctinews.com":
        url = f"https://ctinews.com/search/{urllib.parse.quote(target)}"
    else:
        query = urllib.parse.quote(f"site:{site_domain} {target}")
        url = f"https://news.google.com/rss/search?q={query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    inserted = 0
    try:
        items = parse_source_items(request_url(url), url)[:limit]
    except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
        print(f"  search skipped: {exc}")
        return 0

    with sqlite3.connect(DB_PATH) as conn:
        for item in items:
            title = re.sub(r"\s*-\s*中天新聞網\s*$", "", item["title"])
            if not title or not item["url"]:
                continue
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO articles
                (source, title, summary, url, published, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    source_name,
                    title,
                    item["summary"],
                    item["url"],
                    item["published"],
                    now_iso(),
                ),
            )
            inserted += cur.rowcount
    print(f"Added {inserted} {source_name} search article(s).")
    return inserted


def guess_entity_type(name, entities):
    for entity in entities:
        names = [entity["name"], *entity.get("aliases", [])]
        if name in names:
            return entity["type"]
    if "黨" in name or name in {"藍營", "綠營", "白營"}:
        return "party"
    return "person"


def article_text(article, mode="body"):
    if mode == "title":
        return article["title"]
    return f"{article['title']} {article['summary'] or ''}"


def entity_hits(article, entities, target=None, mode="body"):
    text = article_text(article, mode)
    if target:
        target = target.strip()
        if target and target in text:
            return [{"name": target, "type": guess_entity_type(target, entities), "aliases": []}]
        return []

    hits = []
    for entity in entities:
        names = [entity["name"], *entity.get("aliases", [])]
        if any(name and name in text for name in names):
            hits.append(entity)
    return hits


def clamp(value, low=0.0, high=1.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(low, min(high, number))


def heuristic_analysis(article, entity, mode="body"):
    text = article_text(article, mode)
    negative_terms = [
        "批評",
        "痛批",
        "爭議",
        "不滿",
        "抗議",
        "質疑",
        "下台",
        "貪",
        "弊案",
        "失言",
        "炎上",
        "道歉",
        "違法",
        "怒",
        "惡修",
        "強行",
        "禍首",
    ]
    positive_terms = ["支持", "肯定", "讚", "滿意", "表揚", "成功", "政績", "感動"]
    neg = sum(text.count(term) for term in negative_terms)
    pos = sum(text.count(term) for term in positive_terms)
    score = clamp(0.25 + neg * 0.18 - pos * 0.12)
    if score >= 0.65:
        sentiment = "negative"
    elif score <= 0.35:
        sentiment = "positive_or_neutral"
    else:
        sentiment = "mixed_or_unclear"
    return {
        "entity": entity["name"],
        "entity_type": entity["type"],
        "negative_score": score,
        "sentiment": sentiment,
        "confidence": 0.45,
        "evidence": text[:160],
        "reason": "No AI key found; used keyword fallback scoring.",
    }


def extract_json_object(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("model did not return JSON")
    return json.loads(match.group(0))


def call_ai(article, entity, api_key, api_base, model, mode="body"):
    endpoint = f"{api_base.rstrip('/')}/{model}:generateContent?key={api_key}"
    title = article["title"]
    summary = article["summary"] or ""
    analysis_text = title if mode == "title" else f"{title}\n{summary}".strip()
    prompt = f"""
You are analyzing Taiwanese political news for media negativity, not real public opinion.
Return only one JSON object.

Entity: {entity["name"]}
Entity type: {entity["type"]}
Detection mode: {"title only" if mode == "title" else "title and article text/summary"}
Text to analyze:
{analysis_text}

Score how negatively this article frames the entity.
Use negative_score from 0 to 1.
Use sentiment as one of: negative, positive_or_neutral, mixed_or_unclear.
Use these thresholds consistently:
- negative_score >= 0.65: negative
- negative_score <= 0.35: positive_or_neutral
- otherwise: mixed_or_unclear
Only judge negativity toward the named entity, not the whole article.
If the entity is only mentioned in passing, the text is an ad, or the title/summary
does not contain enough context to judge framing, use negative_score <= 0.20,
sentiment "mixed_or_unclear", and low confidence.
If the article merely quotes someone attacking the entity, still score the article framing,
but mention that in reason. Do not infer facts not present in the text.

JSON shape:
{{
  "entity": "{entity["name"]}",
  "entity_type": "{entity["type"]}",
  "negative_score": 0.0,
  "sentiment": "mixed_or_unclear",
  "confidence": 0.0,
  "evidence": "short phrase from the article",
  "reason": "short explanation in Traditional Chinese"
}}
""".strip()
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 500,
        }
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    content = data["candidates"][0]["content"]["parts"][0]["text"]
    parsed = extract_json_object(content)
    return {
        "entity": entity["name"],
        "entity_type": entity["type"],
        "negative_score": clamp(parsed.get("negative_score")),
        "sentiment": str(parsed.get("sentiment", "mixed_or_unclear")),
        "confidence": clamp(parsed.get("confidence")),
        "evidence": clean_text(str(parsed.get("evidence", "")))[:240],
        "reason": clean_text(str(parsed.get("reason", "")))[:320],
    }


def analyze(limit, use_ai, target=None, force=False, mode="body"):
    init_db()
    entities = load_json(ENTITIES_PATH)
    api_key = os.getenv("GOOGLE_API_KEY")
    api_base = os.getenv("AI_API_BASE", DEFAULT_API_BASE)
    model = os.getenv("AI_MODEL", DEFAULT_MODEL)
    ai_enabled = use_ai and bool(api_key)
    if use_ai and not api_key:
        print("No GOOGLE_API_KEY env var found; using keyword fallback.")

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if force:
            if target:
                conn.execute("DELETE FROM analyses WHERE entity = ?", (target,))
            else:
                conn.execute("DELETE FROM analyses")

        if target:
            rows = conn.execute(
                """
                SELECT a.*
                FROM articles a
                LEFT JOIN analyses x ON x.article_id = a.id AND x.entity = ?
                WHERE x.id IS NULL
                ORDER BY a.id DESC
                LIMIT ?
                """,
                (target, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT a.*
                FROM articles a
                LEFT JOIN analyses x ON x.article_id = a.id
                WHERE x.id IS NULL
                ORDER BY a.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        saved = 0
        for row in rows:
            article = dict(row)
            hits = entity_hits(article, entities, target=target, mode=mode)
            if not hits:
                continue
            for entity in hits:
                try:
                    result = (
                        call_ai(article, entity, api_key, api_base, model, mode=mode)
                        if ai_enabled
                        else heuristic_analysis(article, entity, mode=mode)
                    )
                except Exception as exc:
                    print(f"AI failed for {entity['name']} on article {article['id']}: {exc}")
                    result = heuristic_analysis(article, entity, mode=mode)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO analyses
                    (article_id, entity, entity_type, negative_score, sentiment,
                     confidence, evidence, reason, model, analyzed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        article["id"],
                        result["entity"],
                        result["entity_type"],
                        result["negative_score"],
                        result["sentiment"],
                        result["confidence"],
                        result["evidence"],
                        result["reason"],
                        model if ai_enabled else "keyword-fallback",
                        now_iso(),
                    ),
                )
                saved += 1
                time.sleep(0.2 if not ai_enabled else 0.8)
        print(f"Saved {saved} analysis row(s).")


def render_report():
    init_db()
    REPORT_DIR.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        summary = conn.execute(
            """
            SELECT entity, entity_type, COUNT(*) AS mentions,
                   ROUND(AVG(negative_score), 3) AS avg_negative,
                   ROUND(AVG(confidence), 3) AS avg_confidence
            FROM analyses
            GROUP BY entity, entity_type
            ORDER BY avg_negative DESC, mentions DESC
            """
        ).fetchall()
        details = conn.execute(
            """
            SELECT x.*, a.title, a.url, a.source, a.published
            FROM analyses x
            JOIN articles a ON a.id = x.article_id
            ORDER BY x.negative_score DESC, x.id DESC
            LIMIT 100
            """
        ).fetchall()

    rows = "\n".join(
        f"<tr><td>{html.escape(r['entity'])}</td><td>{html.escape(r['entity_type'])}</td>"
        f"<td>{r['mentions']}</td><td>{r['avg_negative']}</td><td>{r['avg_confidence']}</td></tr>"
        for r in summary
    )
    detail_cards = "\n".join(
        f"""
        <article>
          <div class="score">{r['negative_score']:.2f}</div>
          <h3>{html.escape(r['entity'])} · {html.escape(r['sentiment'])}</h3>
          <p><a href="{html.escape(r['url'])}">{html.escape(r['title'])}</a></p>
          <p class="meta">{html.escape(r['source'])} {html.escape(r['published'] or '')}</p>
          <p>{html.escape(r['reason'] or '')}</p>
          <p class="evidence">{html.escape(r['evidence'] or '')}</p>
        </article>
        """
        for r in details
    )
    document = f"""
<!doctype html>
<html lang="zh-Hant">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>政治新聞負面聲量</title>
<style>
body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0; color: #202124; background: #f7f7f4; }}
header {{ padding: 28px 36px 18px; background: #ffffff; border-bottom: 1px solid #ddd; }}
h1 {{ margin: 0 0 6px; font-size: 28px; }}
main {{ max-width: 1120px; margin: 0 auto; padding: 24px; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; margin-bottom: 24px; }}
th, td {{ text-align: left; border-bottom: 1px solid #e5e5e0; padding: 10px 12px; }}
th {{ background: #eceee8; }}
article {{ position: relative; background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 16px 88px 16px 16px; margin: 12px 0; }}
article h3 {{ margin: 0 0 8px; font-size: 18px; }}
a {{ color: #245a91; }}
.meta, .evidence {{ color: #64645f; font-size: 14px; }}
.guide {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 18px 0 24px; }}
.guide div {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 12px 14px; }}
.guide strong {{ display: block; margin-bottom: 6px; }}
.guide span {{ display: block; color: #64645f; font-size: 14px; line-height: 1.55; }}
.score {{ position: absolute; right: 16px; top: 16px; width: 54px; height: 54px; border-radius: 50%; background: #9b2c2c; color: #fff; display: grid; place-items: center; font-weight: 700; }}
@media (max-width: 760px) {{ .guide {{ grid-template-columns: 1fr; }} }}
</style>
<header>
  <h1>政治新聞媒體文本框架分析</h1>
  <div>⚠️ <strong>聲明：</strong> 由 AI 與關鍵字模型自動生成。分析結果「僅供參考」，不代表事實、民調、民意或任何政治立場。Generated at {html.escape(now_iso())}。</div>
</header>
<main>
  <section class="guide" aria-label="指標說明">
    <div><strong>最低負面分數</strong><span>用來篩選文章。分數低於這個值的文章會被隱藏，不會改變 AI 原本的判讀。</span></div>
    <div><strong>平均負面分數</strong><span>該對象所有相關文章的負面分數平均值，範圍 0 到 1；越接近 1，新聞文本越偏負面框架。</span></div>
    <div><strong>平均信心</strong><span>AI 對自己判讀的把握程度平均值，範圍 0 到 1；它不是負面程度。</span></div>
    <div><strong>負面標準</strong><span>0.65 以上視為明顯負面，0.35 以下偏正面或中性，中間是不明確或混合。</span></div>
  </section>
  <h2>排名</h2>
  <table>
    <thead><tr><th>對象</th><th>類型</th><th>篇數</th><th>平均負面分數</th><th>平均信心</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <h2>文章判讀</h2>
  {detail_cards}
</main>
</html>
""".strip()
    out = REPORT_DIR / "index.html"
    out.write_text(document, encoding="utf-8")
    print(f"Report written to {out}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Crawl RSS political news and score media negativity toward entities."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    crawl_p = sub.add_parser("crawl", help="Fetch RSS articles")
    crawl_p.add_argument("--limit-per-source", type=int, default=15)
    analyze_p = sub.add_parser("analyze", help="Analyze unmatched articles")
    analyze_p.add_argument("--limit", type=int, default=40)
    analyze_p.add_argument("--ai", action="store_true", help="Use AI API when an API key env var is set")
    analyze_p.add_argument("--target", help="Only analyze articles mentioning this person or party")
    analyze_p.add_argument("--force", action="store_true", help="Re-analyze existing matching rows")
    analyze_p.add_argument("--mode", choices=["title", "body"], default="body", help="Analyze title only or title plus summary/body")
    sub.add_parser("report", help="Generate HTML report")
    run_p = sub.add_parser("run", help="Crawl, analyze, and generate report")
    run_p.add_argument("--limit-per-source", type=int, default=15)
    run_p.add_argument("--analyze-limit", type=int, default=40)
    run_p.add_argument("--ai", action="store_true")
    run_p.add_argument("--target", help="Only analyze articles mentioning this person or party")
    run_p.add_argument("--force", action="store_true", help="Re-analyze existing matching rows")
    run_p.add_argument("--mode", choices=["title", "body"], default="body", help="Analyze title only or title plus summary/body")
    args = parser.parse_args(argv)

    if args.command == "crawl":
        crawl(args.limit_per_source)
    elif args.command == "analyze":
        analyze(args.limit, args.ai, target=args.target, force=args.force, mode=args.mode)
    elif args.command == "report":
        render_report()
    elif args.command == "run":
        crawl(args.limit_per_source)
        analyze(args.analyze_limit, args.ai, target=args.target, force=args.force, mode=args.mode)
        render_report()
    else:
        parser.print_help()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
