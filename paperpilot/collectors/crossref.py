"""Crossref 采集器：按期刊 ISSN 拉取最新论文（TPAMI / TIP / TNNLS）。"""

import re
import time
from datetime import datetime, timedelta, timezone

import httpx

API_URL = "https://api.crossref.org/journals/{issn}/works"


def _strip_jats(text):
    """去掉 CrossRef abstract 里的 JATS XML 标签。"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def _to_paper(item, name):
    title = (item.get("title") or [""])[0] or ""
    authors = []
    for a in item.get("author", []) or []:
        nm = " ".join(x for x in [a.get("given", ""), a.get("family", "")] if x)
        if nm:
            authors.append(nm.strip())

    issued = (item.get("issued") or {}).get("date-parts", [[None]])
    dp = issued[0] if issued else [None]
    year = dp[0] if dp and len(dp) > 0 else None
    month = dp[1] if dp and len(dp) > 1 else None
    day = dp[2] if dp and len(dp) > 2 else None
    if year:
        published_at = f"{year:04d}"
        if month:
            published_at += f"-{month:02d}"
            if day:
                published_at += f"-{day:02d}"
    else:
        published_at = ""

    doi = item.get("DOI", "")
    pdf = ""
    for link in item.get("link", []) or []:
        if link.get("content-type") == "application/pdf":
            pdf = link.get("URL", "")
            break

    return {
        "title": " ".join(title.split()),
        "abstract": _strip_jats(item.get("abstract", "")),
        "authors": authors,
        "published_at": published_at,
        "year": int(year) if year else None,
        "venue": name,
        "venue_type": "journal",
        "publication_status": "published",
        "doi": doi,
        "arxiv_id": "",
        "url": item.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
        "pdf_url": pdf,
        "source": "crossref",
        "topics": [],
        "github": [],
        "raw": {"container_title": (item.get("container-title") or [""])[0]},
    }


def collect_crossref(config):
    if not config["sources"]["crossref"].get("enabled", True):
        return []
    lookback = config["sources"]["crossref"].get("lookback_days", 90)
    from_date = (datetime.now(timezone.utc) - timedelta(days=lookback)).strftime("%Y-%m-%d")
    max_per = config["sources"]["crossref"].get("max_results_per_journal", 60)
    mailto = config["sources"]["crossref"].get("mailto", "")
    delay = config["app"].get("request_delay", 3)

    headers = {"User-Agent": "PaperPilot/0.1 (mailto:research@example.com)"}
    papers = []
    journals = config["venues"]["journals"]

    with httpx.Client(headers=headers, timeout=40) as client:
        for name, meta in journals.items():
            issn = meta.get("issn")
            if not issn:
                continue
            params = {
                "filter": f"from-pub-date:{from_date}",
                "rows": max_per,
                "select": "DOI,title,author,issued,container-title,abstract,URL,link",
            }
            if mailto:
                params["mailto"] = mailto
            print(f"[Crossref] {name} (ISSN {issn}) …")
            try:
                r = client.get(API_URL.format(issn=issn), params=params)
                if r.status_code != 200:
                    print(f"  -> 失败 status={r.status_code}")
                    time.sleep(delay)
                    continue
                items = r.json().get("message", {}).get("items", [])
                got = 0
                for item in items:
                    papers.append(_to_paper(item, name))
                    got += 1
                print(f"  -> 采集 {got} 篇")
            except httpx.HTTPError as exc:
                print(f"  -> 请求异常: {exc}")
            time.sleep(delay)

    return papers
