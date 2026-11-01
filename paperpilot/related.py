"""学术关联检索：为深度分析提供真实的相关文献。

用 OpenAlex 检索三类关联，解决「方法机制与技术继承=未知」的根因——
不再让大模型凭空猜，而是把可核验的文献喂给它：
1. 参考文献（referenced_works）：它直接引用了谁；
2. 施引论文（cites:ID）：谁在它之后继承了它；
3. 作者近期工作（author.id）：团队延续与同门工作；
4. 同题相似工作（title 检索）：独立先例与竞争方法。
"""

import time

import httpx

API = "https://api.openalex.org/works"
HEADERS = {"User-Agent": "PaperPilot/0.1 (mailto:research@example.com)"}


def _get(client, url, params=None, tries=2):
    for i in range(tries):
        try:
            r = client.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 502, 503):
                time.sleep(2 * (i + 1))
                continue
            return None
        except httpx.HTTPError:
            time.sleep(2 * (i + 1))
    return None


def resolve_openalex_id(paper, client):
    """解析论文的 OpenAlex work id（Wxxx）。优先 raw，其次 DOI，再标题。"""
    oa = (paper.get("raw") or {}).get("openalex_id")
    if oa:
        return oa.split("/")[-1]
    doi = paper.get("doi")
    if doi:
        d = _get(client, f"{API}/doi:{doi}")
        if d:
            return d.get("id", "").split("/")[-1]
    title = (paper.get("title") or "").strip()
    if title:
        d = _get(client, API, {"filter": f"title.search:{title[:120]}", "per_page": 1})
        if d and d.get("results"):
            return d["results"][0].get("id", "").split("/")[-1]
    return None


def _to_related(w, hint):
    loc = (w.get("primary_location") or {}) or {}
    src = loc.get("source") or {}
    authors = []
    for a in (w.get("authorships") or []) or []:
        au = (a.get("author") or {}) or {}
        name = (au.get("display_name") or "").strip()
        if name:
            authors.append(name)
    abstract_inv = (w.get("abstract_inverted_index") or None)
    abstract = ""
    if abstract_inv:
        # OpenAlex 的倒排索引 → 还原摘要文本
        try:
            positions = {}
            for word, idxs in abstract_inv.items():
                for i in idxs:
                    positions[i] = word
            abstract = " ".join(positions[i] for i in sorted(positions))
        except Exception:  # noqa: BLE001
            abstract = ""
    return {
        "title": (w.get("display_name") or "").strip(),
        "year": (w.get("publication_date") or "")[:4],
        "venue": src.get("display_name") or "",
        "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
        "openalex_id": (w.get("id") or "").split("/")[-1],
        "authors": authors,
        "abstract": abstract,
        "cited_by_count": w.get("cited_by_count"),
        "relation_hint": hint,
    }


def retrieve_related(paper, config, limit=20):
    """返回 (相关文献列表, 检索范围说明)。

    相关文献去重、带关系提示与检索路径；检索范围说明记录各类检索的查询
    范围与失败状态，供分析引擎如实描述「检索候选≠已核验关系」。
    失败时返回空列表，不阻断分析。
    """
    wid = None
    out = []
    seen = set()
    # 记录每个候选的检索路径（同一论文可来自多条路径）
    paths = {}
    scope = {
        "source": "OpenAlex",
        "methods": [],
        "exhaustive_review": False,
        "note": "检索结果是候选集合，不代表已穷尽相关工作。",
    }

    def add(w, hint):
        if not w:
            return
        r = _to_related(w, hint)
        key = r["title"].lower().strip()
        if not key or key in seen:
            return
        seen.add(key)
        paths[key] = [hint]
        out.append(r)

    def add_path(w, hint):
        # 已存在则追加检索路径，不重复加条目
        if not w:
            return
        r = _to_related(w, hint)
        key = r["title"].lower().strip()
        if key in paths:
            if hint not in paths[key]:
                paths[key].append(hint)
            # 补充可能缺失的字段
            for idx, item in enumerate(out):
                if item["title"].lower().strip() == key:
                    for f in ("abstract", "authors", "cited_by_count", "doi"):
                        if not item.get(f) and r.get(f):
                            item[f] = r[f]
                    break
            return
        add(w, hint)

    try:
        with httpx.Client(headers=HEADERS, timeout=30) as client:
            wid = resolve_openalex_id(paper, client)
            if wid:
                work = _get(client, f"{API}/{wid}")
                if work:
                    # 1) 参考文献（尽量取全，按主题/方法由分析引擎筛选）
                    ref_ids = work.get("referenced_works", [])[:40]
                    if ref_ids:
                        ids = "|".join(x.split("/")[-1] for x in ref_ids)
                        d = _get(client, API, {"filter": f"openalex_id:{ids}",
                                               "per_page": 40})
                        for w in (d or {}).get("results", []):
                            add(w, "reference")
                        scope["methods"].append(
                            {"type": "referenced_works", "count": len(ref_ids),
                             "ok": bool(d)})
                    # 2) 施引（最新 + 代表性，不限纯最新）
                    d = _get(client, API, {"filter": f"cites:{wid}",
                                           "per_page": 20,
                                           "sort": "publication_date:desc"})
                    for w in (d or {}).get("results", []):
                        add(w, "citation")
                    scope["methods"].append(
                        {"type": "citing_works", "count": 20, "ok": bool(d)})
                    # 3) 作者工作（扩大覆盖，不限年份）
                    for a in (work.get("authorships") or [])[:5]:
                        aid = (a.get("author") or {}).get("id", "")
                        if not aid:
                            continue
                        aid = aid.split("/")[-1]
                        d = _get(client, API, {
                            "filter": f"author.id:{aid}",
                            "per_page": 8, "sort": "publication_date:desc"})
                        for w in (d or {}).get("results", []):
                            add_path(w, "author_work")
                    scope["methods"].append(
                        {"type": "author_works", "ok": True})
            # 4) 同题相似工作（用更多标题词 + 摘要级搜索）
            title = (paper.get("title") or "").strip()
            if title:
                kw = " ".join(title.split()[:10])
                d = _get(client, API, {
                    "search": kw, "per_page": 12,
                    "sort": "publication_date:desc"})
                for w in (d or {}).get("results", []):
                    add_path(w, "topic_similar")
                scope["methods"].append(
                    {"type": "topic_search", "query": kw, "ok": bool(d)})
    except Exception:  # noqa: BLE001
        pass

    # 去掉与当前论文同标题的条目，并写入检索路径
    self_title = (paper.get("title") or "").lower().strip()
    out = [r for r in out if r["title"].lower().strip() != self_title]
    for r in out:
        r["retrieval_paths"] = paths.get(r["title"].lower().strip(), [])
    return out[:limit], scope
