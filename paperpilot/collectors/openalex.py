"""OpenAlex 采集器：会议论文（按 source id，最近一届）+ arXiv 预印本兜底。"""

import time
from datetime import datetime, timedelta, timezone

import httpx

from ..model import clean_arxiv_id

API_URL = "https://api.openalex.org/works"
ARXIV_SOURCE = "S4306400194"  # OpenAlex 中 arXiv 的 source id


def reconstruct_abstract(inv):
    """把 OpenAlex 的 abstract_inverted_index 还原成文本。"""
    if not inv:
        return ""
    positions = {}
    for word, idxs in inv.items():
        for i in idxs:
            positions[i] = word
    return " ".join(positions[i] for i in sorted(positions))


def _to_paper(w, venue_name=None, kind="preprint"):
    title = w.get("display_name") or ""
    abstract = reconstruct_abstract(w.get("abstract_inverted_index"))
    authors = [a["author"]["display_name"] for a in w.get("authorships", [])
               if a.get("author")]
    pub_date = w.get("publication_date") or ""
    year = int(pub_date[:4]) if pub_date else None

    doi = (w.get("doi") or "").replace("https://doi.org/", "")
    loc = w.get("primary_location") or {}
    url = loc.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else w.get("id"))
    pdf = loc.get("pdf_url") or ""
    arxiv_id = clean_arxiv_id(pdf) if "arxiv.org" in (pdf or "") else ""

    return {
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "published_at": pub_date,
        "year": year,
        "venue": venue_name,
        "venue_type": kind,
        "publication_status": "published" if venue_name else "preprint",
        "doi": doi,
        "arxiv_id": arxiv_id,
        "url": url,
        "pdf_url": pdf,
        "source": "openalex",
        "topics": [],
        "github": [],
        "raw": {"openalex_id": w.get("id"),
               "cited_by_count": w.get("cited_by_count"),
               "type": w.get("type")},
    }


def _days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def _retry_after(resp, default=3):
    v = resp.headers.get("Retry-After", "")
    if v and v.isdigit():
        return max(1, int(v))
    return default


def _collect_by_filter(client, params, delay):
    """请求一次，带网络错误/限流重试，返回结果列表。"""
    for attempt in range(3):
        try:
            r = client.get(API_URL, params=params)
            if r.status_code == 200:
                time.sleep(delay)
                return [w for w in r.json().get("results", [])]
            if r.status_code in (429, 502, 503):
                wait = _retry_after(r, default=delay + attempt * 3)
                print(f"  -> 限流/服务不可用({r.status_code})，{wait}s 后重试…")
                time.sleep(wait)
                continue
            print(f"  -> 失败 status={r.status_code}")
            time.sleep(delay)
            return []
        except httpx.HTTPError as exc:
            wait = delay + attempt * 3
            print(f"  -> 请求异常: {exc}，{wait}s 后重试…")
            time.sleep(wait)
    return []


def collect_conferences(config):
    """按会议 source id 拉取最近一届论文。"""
    venues = config["venues"].get("conferences", {})
    conf_lookback = config["sources"]["openalex"].get("conference_lookback_days", 730)
    max_per = config["sources"]["openalex"].get("max_results_per_venue", 40)
    mailto = config["sources"]["openalex"].get("mailto", "")
    delay = config["app"].get("request_delay", 3)
    from_date = _days_ago(conf_lookback)

    headers = {"User-Agent": "PaperPilot/0.1 (mailto:research@example.com)"}
    papers = []
    with httpx.Client(headers=headers, timeout=40) as client:
        for name, meta in venues.items():
            source = meta.get("openalex_source")
            if not source:
                continue
            params = {
                "filter": f"primary_location.source.id:{source},"
                          f"from_publication_date:{from_date}",
                "per_page": max_per,
                "sort": "publication_date:desc",
            }
            if mailto:
                params["mailto"] = mailto
            print(f"[OpenAlex] 会议 {name} …")
            got = _collect_by_filter(client, params, delay)
            for w in got:
                papers.append(_to_paper(w, venue_name=name, kind="conference"))
            print(f"  -> 采集 {len(got)} 篇")
    return papers


def collect_preprints(config):
    """arXiv 预印本兜底：按主题查询 OpenAlex 中 arXiv source。"""
    topics = config["topics"]
    lookback = config["sources"]["openalex"].get("arxiv_lookback_days", 7)
    mailto = config["sources"]["openalex"].get("mailto", "")
    delay = config["app"].get("request_delay", 3)
    from_date = _days_ago(lookback)

    headers = {"User-Agent": "PaperPilot/0.1 (mailto:research@example.com)"}
    papers = []
    with httpx.Client(headers=headers, timeout=40) as client:
        for topic, t in topics.items():
            # 用主题最有代表性的关键词做全文检索
            kw = t["keywords"][0]
            params = {
                "filter": f"primary_location.source.id:{ARXIV_SOURCE},"
                          f"from_publication_date:{from_date}",
                "search": kw,
                "per_page": 40,
                "sort": "publication_date:desc",
            }
            if mailto:
                params["mailto"] = mailto
            print(f"[OpenAlex] 预印本 {topic} ({kw}) …")
            got = _collect_by_filter(client, params, delay)
            for w in got:
                p = _to_paper(w)
                p["topics"] = [topic]
                papers.append(p)
            print(f"  -> 采集 {len(got)} 篇")
    return papers


def collect_openalex(config):
    if not config["sources"]["openalex"].get("enabled", True):
        return []
    papers = collect_conferences(config)
    papers += collect_preprints(config)
    return papers
