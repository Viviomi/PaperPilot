"""GitHub 采集器：按研究主题搜索代码仓库，作为论文的候选代码关联。

注意：仓库与论文的关联在本版为「候选未核验」，需人工确认（README 是否引用论文、
作者是否匹配、有无正式 Release 等）。
"""

import re
import time

import httpx

from ..config import env
from ..model import clean_arxiv_id

API_URL = "https://api.github.com/search/repositories"

_ARXIV_RE = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*)(\d{4}\.\d{4,5})", re.IGNORECASE)


def _to_repo(item, topic):
    desc = item.get("description") or ""
    m = _ARXIV_RE.search(desc)
    license_name = ""
    if item.get("license"):
        license_name = item["license"].get("spdx_id", "")
    return {
        "topic": topic,
        "full_name": item.get("full_name", ""),
        "url": item.get("html_url", ""),
        "stars": item.get("stargazers_count", 0),
        "description": desc,
        "language": item.get("language"),
        "updated_at": item.get("pushed_at"),
        "license": license_name,
        "arxiv_id": clean_arxiv_id(m.group(1)) if m else "",
        "match_type": "description_arxiv" if m else "topic_keyword",
        "verified": False,
    }


def collect_github(config):
    if not config["sources"]["github"].get("enabled", True):
        return []
    token = env("GITHUB_TOKEN", "")
    per_query = config["sources"]["github"].get("max_repos_per_query", 5)
    delay = config["app"].get("request_delay", 3)

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "PaperPilot/0.1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    repos = []
    topics = config["topics"]

    with httpx.Client(headers=headers, timeout=40) as client:
        for idx, (topic, t) in enumerate(topics.items()):
            # 用主题下前几个最具区分度的关键词组查询
            keywords = [k for k in t["keywords"] if len(k) > 2][:5]
            q = " OR ".join(f'"{k}"' for k in keywords)
            params = {"q": q, "sort": "stars", "order": "desc",
                      "per_page": per_query}
            print(f"[GitHub] 主题 {topic}: {q[:70]}…")
            if idx > 0:
                time.sleep(delay)
            try:
                r = client.get(API_URL, params=params)
                if r.status_code == 403:
                    print("  -> 限流或鉴权失败(403)，请配置 GITHUB_TOKEN")
                    time.sleep(delay)
                    continue
                if r.status_code != 200:
                    print(f"  -> 失败 status={r.status_code}")
                    time.sleep(delay)
                    continue
                items = r.json().get("items", [])
                for it in items:
                    repos.append(_to_repo(it, topic))
                print(f"  -> 采集 {len(items)} 个仓库")
            except httpx.HTTPError as exc:
                print(f"  -> 请求异常: {exc}")

    return repos
