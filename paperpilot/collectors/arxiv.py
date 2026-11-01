"""arXiv 采集器：按主题关键词 + 分类拉取最新预印本，并识别顶会录用提示。"""

import re
import time
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from ..model import strip_arxiv_version

API_URL = "https://export.arxiv.org/api/query"

# 主题 -> 相关 arXiv 分类，用于缩小范围、降低噪声
TOPIC_CATEGORIES = {
    "multi_agent": ["cs.MA", "cs.AI", "cs.LG"],
    "bci": ["eess.SP", "cs.NE", "cs.LG", "q-bio.NC"],
    "few_shot": ["cs.LG", "cs.CV", "cs.AI"],
    "transfer": ["cs.LG", "cs.CV", "cs.AI"],
    "agents": ["cs.CL", "cs.AI", "cs.MA"],
}

_VENUE_PATTERN = None


def _venue_regex(venue_names):
    """构建用于识别顶会顶刊的（缩写|全称）正则。"""
    names = sorted(venue_names, key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b",
                      re.IGNORECASE)


def detect_venue(text, venue_names):
    """在 arXiv comment / journal_ref 中识别顶会顶刊（带年份/录用语境更可信）。"""
    global _VENUE_PATTERN
    if not text:
        return None
    if _VENUE_PATTERN is None:
        _VENUE_PATTERN = _venue_regex(venue_names)
    m = _VENUE_PATTERN.search(text)
    if not m:
        return None
    name = m.group(1)
    # 规范化缩写（如 NIPS -> NeurIPS）
    return name


def _build_query(keywords, cats):
    # 用前 5 个关键词，降低查询复杂度，减少 arXiv 超时
    kw_clause = " OR ".join(f'all:"{kw}"' for kw in keywords[:5])
    cat_clause = " OR ".join(f"cat:{c}" for c in cats)
    return f"({cat_clause}) AND ({kw_clause})"


def _retry_after(resp, default=10):
    """从 Retry-After 头读取等待秒数。"""
    v = resp.headers.get("Retry-After", "")
    if v and v.isdigit():
        return max(1, int(v))
    return default


def _fetch(client, params, delay):
    rate_limited = False
    for attempt in range(2):
        try:
            r = client.get(API_URL, params=params, timeout=60)
            if r.status_code == 429:
                rate_limited = True
                wait = _retry_after(r, default=10 * (attempt + 1))
                print(f"  [arXiv] 限流(429)，{wait}s 后重试…")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r, False
        except httpx.HTTPError as exc:
            print(f"  [arXiv] 请求失败: {exc}")
            time.sleep(delay * (attempt + 1))
    return None, rate_limited


def _to_paper(entry, topic, venue_names):
    arxiv_raw = entry.get("id", "")
    arxiv_id = strip_arxiv_version(arxiv_raw.rsplit("/", 1)[-1])
    comment = entry.get("arxiv_comment", "") or ""
    journal_ref = entry.get("arxiv_journal_ref", "") or ""
    venue = detect_venue(f"{comment} {journal_ref}", venue_names)

    published = entry.get("published", "")
    pub_date = published[:10] if published else ""
    year = int(pub_date[:4]) if pub_date else None

    authors = [a.get("name", "") for a in entry.get("authors", [])]

    return {
        "title": " ".join((entry.get("title") or "").split()),
        "abstract": " ".join((entry.get("summary") or "").split()),
        "authors": authors,
        "published_at": pub_date,
        "year": year,
        "venue": venue,
        "venue_type": "conference" if venue else "preprint",
        "publication_status": "accepted" if venue else "preprint",
        "doi": "",
        "arxiv_id": arxiv_id,
        "url": entry.get("link", ""),
        "pdf_url": (entry.get("link", "") or "").replace("/abs/", "/pdf/"),
        "source": "arxiv",
        "topics": [topic],
        "github": [],
        "raw": {"arxiv_comment": comment, "journal_ref": journal_ref,
               "arxiv_id_version": arxiv_raw.rsplit("/", 1)[-1]},
    }


def collect_arxiv(config):
    if not config["sources"]["arxiv"].get("enabled", True):
        return []
    topics = config["topics"]
    venue_names = list(config["venues"]["conferences"].keys()) + \
                  list(config["venues"]["journals"].keys())
    max_results = config["sources"]["arxiv"].get("max_results_per_query", 60)
    delay = config["app"].get("request_delay", 3)
    lookback = config["app"].get("lookback_days", 7)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback)).strftime("%Y-%m-%d")

    headers = {"User-Agent": "PaperPilot/0.1 (personal research tracker)"}
    papers = []

    with httpx.Client(headers=headers, timeout=40) as client:
        for idx, (topic, t) in enumerate(topics.items()):
            cats = TOPIC_CATEGORIES.get(topic, ["cs.AI", "cs.LG"])
            query = _build_query(t["keywords"], cats)
            params = {
                "search_query": query,
                "start": 0,
                "max_results": max_results,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
            print(f"[arXiv] 主题 {topic}: {query[:80]}…")
            if idx > 0:
                time.sleep(delay)
            r, rate_limited = _fetch(client, params, delay)
            if r is None:
                if rate_limited:
                    print("[arXiv] 当前 IP 被限流，跳过其余主题（由 OpenAlex 预印本兜底）。")
                    break
                continue
            feed = feedparser.parse(r.text)
            count = 0
            for entry in feed.entries:
                pub = entry.get("published", "")[:10]
                if pub and pub < cutoff:
                    continue
                papers.append(_to_paper(entry, topic, venue_names))
                count += 1
            print(f"  -> 采集 {count} 篇")

    return papers
