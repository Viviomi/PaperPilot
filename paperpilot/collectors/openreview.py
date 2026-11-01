"""OpenReview 采集器：按会议 venueid 拉取投稿，本地按主题关键词过滤。

OpenReview 是 NeurIPS / ICML / ICLR 等 ML 顶会的官方投稿与评审平台，
论文在会议正式出版前就在此公开（含录用状态），是比 arXiv 更早、更权威
的会议论文来源。

API 说明（API v2）：
- 端点：https://api2.openreview.net/notes/search
- 全文检索：term 参数（用 venueid 精确片段即可精准过滤某届会议）
- 分页：limit + offset
- 无需认证（公开读取）
- 注意：/notes 端点对自动化客户端有 Cloudflare 挑战（403），
  但 /notes/search 与 /groups 不受影响，本采集器只用 /notes/search。
"""

import re
import time
from datetime import datetime, timezone

import httpx

API_URL = "https://api2.openreview.net/notes/search"

# 会议名 -> venueid 前缀（不含年份，程序自动探测最近一届）
# 仅 OpenReview 原生承办的会议才有数据；CVPR/ICCV/AAAI/KDD/ECCV 不在其上。
DEFAULT_VENUES = {
    "NeurIPS": "NeurIPS.cc",
    "ICML": "ICML.cc",
    "ICLR": "ICLR.cc",
}

_YEAR = datetime.now(timezone.utc).year


def _ms_to_year(ms):
    """毫秒时间戳 -> 年份（缺省返回 None）。"""
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).year
    except (ValueError, OSError):
        return None


def _find_latest_venueid(client, prefix, max_back=3):
    """从当前年份往回探测，找到有投稿的最近一届 venueid。

    返回 (venueid, year) 或 (None, None)。
    """
    for back in range(max_back + 1):
        year = _YEAR - back
        vid = f"{prefix}/{year}/Conference"
        try:
            r = client.get(API_URL, params={
                "term": vid, "source": "forum", "limit": 1,
            })
            if r.status_code != 200:
                continue
            data = r.json()
            # count 可能缺失，用返回的 notes 是否非空判断
            if data.get("count") or data.get("notes"):
                return vid, year
        except httpx.HTTPError:
            continue
    return None, None


def _to_paper(note, venue_name, year):
    """把 OpenReview note 转成 paper dict（字段契约与其它采集器一致）。"""
    c = note.get("content", {}) or {}

    def val(key):
        v = c.get(key)
        if isinstance(v, dict):
            return v.get("value")
        return v

    title = " ".join((val("title") or "").split())
    abstract = " ".join((val("abstract") or "").split())
    authors = val("authors") or []
    if isinstance(authors, str):
        authors = [a.strip() for a in authors.split(",") if a.strip()]

    venueid = val("venueid") or ""
    venue_short = val("venue") or ""

    # 时间：优先 pdate（公开时间），否则 cdate（创建时间）
    ts = note.get("pdate") or note.get("cdate")
    pub_year = _ms_to_year(ts) or year
    published_at = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d") if ts else ""

    # arXiv ID：从 abstract / venue 里提取（仅接受明确的 arXiv 编号格式）
    arxiv_id = ""
    m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", abstract, re.IGNORECASE)
    if m:
        arxiv_id = m.group(1)

    pdf = val("pdf") or ""
    pdf_url = ""
    if pdf:
        pdf_url = pdf if pdf.startswith("http") else f"https://openreview.net{pdf}"

    forum = note.get("id") or note.get("forum") or ""
    url = f"https://openreview.net/forum?id={forum}" if forum else ""

    # 录用状态：venueid 是 Conference（正式录用）还是 Submission（在审/被拒）
    is_accepted = venueid.endswith("/Conference")

    return {
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "published_at": published_at,
        "year": pub_year,
        "venue": venue_name,
        "venue_type": "conference",
        "publication_status": "accepted" if is_accepted else "preprint",
        "doi": "",
        "arxiv_id": arxiv_id,
        "url": url,
        "pdf_url": pdf_url,
        "source": "openreview",
        "topics": [],
        "github": [],
        "raw": {"openreview_id": forum,
               "venueid": venueid,
               "venue_short": venue_short,
               "keywords": val("keywords") or []},
    }


def _build_matchers(topic_kws):
    """把主题关键词编译成匹配器。

    短缩写（<=4 字符，如 GAN/BCI/MARL）用词边界匹配，避免子串误报
    （否则 "gan" 会误匹配 organization / begin / bargain 等）。
    长词（>=5 字符）用普通子串匹配。
    返回 [(正则, 关键词)]。
    """
    matchers = []
    for kw in topic_kws:
        if len(kw) <= 4:
            # 短词：词边界 + 转义
            matchers.append((re.compile(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])"),
                             kw))
        else:
            matchers.append((re.compile(re.escape(kw)), kw))
    return matchers


def _collect_venue(client, name, prefix, config, matchers, delay):
    """拉取某会议的最近一届投稿，本地按主题过滤。"""
    max_per = config["sources"]["openreview"].get("max_results_per_venue", 200)
    venueid, year = _find_latest_venueid(client, prefix)
    if not venueid:
        print(f"[OpenReview] {name}：未在 OpenReview 找到会议数据，跳过")
        return []

    # 分页拉取全部投稿（每页最多 1000）
    papers = []
    offset = 0
    page_size = min(1000, max(max_per, 200))
    while True:
        try:
            r = client.get(API_URL, params={
                "term": venueid, "source": "forum",
                "limit": page_size, "offset": offset,
            })
            if r.status_code != 200:
                print(f"[OpenReview] {name}：请求失败 status={r.status_code}，停止")
                break
            data = r.json()
            notes = data.get("notes", [])
        except httpx.HTTPError as exc:
            print(f"[OpenReview] {name}：请求异常 {exc}，停止")
            break

        if not notes:
            break

        for n in notes:
            p = _to_paper(n, name, year)
            # 本地按主题关键词过滤（标题 + 摘要 + 关键词）
            hay = " ".join([p["title"], p["abstract"],
                            " ".join(p["raw"].get("keywords") or [])]).lower()
            for rx, kw in matchers:
                if rx.search(hay):
                    p["topics"] = []
                    papers.append(p)
                    break

        # 分页控制：拉满 max_per 或已取完
        offset += len(notes)
        total = data.get("count")
        if len(papers) >= max_per:
            papers = papers[:max_per]
            break
        if total and offset >= int(total):
            break
        if len(notes) < page_size:
            break
        time.sleep(delay)

    print(f"[OpenReview] {name} {year}：采集 {len(papers)} 篇（主题匹配）")
    return papers


def collect_openreview(config):
    """OpenReview 采集主入口。"""
    if not config["sources"].get("openreview", {}).get("enabled", True):
        return []
    delay = config["app"].get("request_delay", 3)
    venues = config["sources"]["openreview"].get("venues", DEFAULT_VENUES)
    max_per = config["sources"]["openreview"].get("max_results_per_venue", 200)

    # 汇总所有主题关键词（去重、小写），用于本地过滤
    topic_kws = set()
    for t in config["topics"].values():
        for kw in t.get("keywords", []):
            topic_kws.add(kw.strip().lower())
    matchers = _build_matchers(sorted(topic_kws, key=len, reverse=True))

    headers = {"User-Agent": "PaperPilot/0.1 (personal research tracker)"}
    papers = []
    with httpx.Client(headers=headers, timeout=40) as client:
        for name, prefix in venues.items():
            papers += _collect_venue(client, name, prefix, config,
                                     matchers, delay)
            time.sleep(delay)
    return papers
