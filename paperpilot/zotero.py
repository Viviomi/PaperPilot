"""Zotero 同步：生成 BibTeX 导入文件 + 本地 API 尽力入库 + zotero:// 深链。

策略（优雅降级）：
1. 优先尝试 Zotero 7 本地 API（localhost:23119）自动入库；
2. 失败或不可用时，生成 BibTeX 导入文件，用户可在 Zotero 里一键导入；
3. 始终生成 zotero:// 深链，供 Obsidian / 工作台跳转。
"""

import json
import re
from pathlib import Path

import httpx

from .config import env

ZOTERO_ITEM_TYPES = {}


def _bibtex_author(authors):
    """把 'First Last' 转成 BibTeX 的 'Last, First'。"""
    out = []
    for a in authors or []:
        a = a.strip()
        if not a:
            continue
        if "," in a:
            out.append(a)
        else:
            parts = a.split()
            if len(parts) >= 2:
                out.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
            else:
                out.append(a)
    return " and ".join(out)


def _entry_type(paper):
    if paper.get("venue_type") == "journal":
        return "article"
    if paper.get("venue_type") == "conference":
        return "inproceedings"
    return "misc"


def _tex_escape(s):
    if not s:
        return ""
    for ch in "&%$#_{}":
        s = s.replace(ch, "\\" + ch)
    return s


def build_bibtex_entry(paper, citekey):
    authors = _bibtex_author(paper.get("authors") or [])
    title = _tex_escape(paper.get("title") or "")
    year = paper.get("year") or ""
    etype = _entry_type(paper)
    lines = [f"@{etype}{{{citekey},"]
    if authors:
        lines.append(f"  author = {{{authors}}},")
    lines.append(f"  title = {{{title}}},")
    if year:
        lines.append(f"  year = {{{year}}},")
    venue = paper.get("venue")
    if venue:
        field = "journal" if etype == "article" else "booktitle"
        lines.append(f"  {field} = {{{_tex_escape(venue)}}},")
    if paper.get("doi"):
        lines.append(f"  doi = {{{paper['doi']}}},")
    if paper.get("arxiv_id"):
        lines.append(f"  eprint = {{{paper['arxiv_id']}}},")
        lines.append("  archiveprefix = {arXiv},")
    if paper.get("url"):
        lines.append(f"  url = {{{paper['url']}}},")
    if paper.get("abstract"):
        lines.append(f"  abstract = {{{_tex_escape(paper['abstract'][:800])}}},")
    tags = build_zotero_tags(paper)
    if tags:
        lines.append(f"  keywords = {{{', '.join(tags)}}},")
    lines.append("}")
    return "\n".join(lines)


def build_zotero_tags(paper):
    """生成统一标签：citekey/ topic/ method/ status/ quality/ priority/ source/。

    机器标签由 PaperPilot 维护；个人标签由用户在 Zotero 里自行添加，
    程序不删除人工标签。
    """
    tags = []
    # 引用键：作为「Zotero 条目 ↔ Obsidian 文献卡文件名」的桥接标识，
    # 让用户能在 Zotero 里按 citekey 反查 Obsidian 笔记。
    if paper.get("citation_key"):
        tags.append(f"citekey/{paper['citation_key']}")
    for t in paper.get("topics") or []:
        tags.append(f"topic/{t}")
    # 来源
    if paper.get("source"):
        tags.append(f"source/{paper['source']}")
    # 阅读/分析状态
    st = paper.get("analysis_status")
    if st == "completed":
        tags.append("status/analyzed")
    elif st == "pending":
        tags.append("status/to-read")
    if paper.get("reading_status"):
        tags.append(f"status/{paper['reading_status']}")
    # 质量核验
    if paper.get("venue"):
        tags.append("quality/venue-verified")
    if paper.get("doi"):
        tags.append("quality/has-doi")
    # 优先级（按相关度）
    rel = paper.get("relevance_score") or 0
    if rel >= 60:
        tags.append("priority/high")
    elif rel >= 30:
        tags.append("priority/medium")
    else:
        tags.append("priority/low")
    if paper.get("github"):
        tags.append("code/candidate")
    return tags


def export_bibtex(papers, path):
    """生成 BibTeX 文件供 Zotero 导入。返回路径。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    for p in papers:
        citekey = p.get("citation_key") or f"pp{p['id']}"
        entries.append(build_bibtex_entry(p, citekey))
    path.write_text("\n\n".join(entries), encoding="utf-8")
    return path


def zotero_uri(item_key=None, collection=None):
    """生成 zotero:// 深链。item_key 打开条目；collection 打开集合。"""
    if item_key:
        return f"zotero://select/items/{item_key}"
    if collection:
        return f"zotero://select/library/collections/{collection}"
    return "zotero://select/library"


def check_local_api(url):
    """探测 Zotero 本地 API 是否可用（只读探测，无需 key）。返回 (ok, server_id)。"""
    try:
        r = httpx.get(f"{url}/api/", timeout=5)
        return r.status_code == 200, r.headers.get("Zotero-Server-ID", "")
    except httpx.HTTPError:
        return False, ""


def authorize_local(url, app_name="PaperPilot"):
    """运行时申请本地 API 写权限：Zotero 会弹窗让你 Allow / Always Allow / Deny。

    返回 {key, remember, server_id}，被拒绝或失败返回 None。
    """
    ok, server_id = check_local_api(url)
    if not ok:
        return None
    try:
        r = httpx.post(f"{url}/api/local/authorize",
                       headers={"Zotero-Server-ID": server_id,
                                "Content-Type": "application/json"},
                       json={"appName": app_name}, timeout=60)
        if r.status_code == 200:
            d = r.json()
            return {"key": d.get("key"), "remember": d.get("remember"),
                    "server_id": server_id}
        print(f"[Zotero] 授权未通过 status={r.status_code}（可能被拒绝）")
    except httpx.HTTPError as exc:
        print(f"[Zotero] 授权请求失败: {exc}")
    return None


def _key_file():
    return Path(__file__).resolve().parent.parent / "data" / "zotero_local_key.json"


def _load_cached_key():
    try:
        p = _key_file()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_cached_key(auth):
    try:
        p = _key_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(auth), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _extract_item_key(resp):
    """从 Zotero 写入响应中提取新条目的 key。"""
    try:
        d = resp.json()
        return (d.get("successful") or {}).get("0", {}).get("key", "")
    except Exception:  # noqa: BLE001
        return ""


# ---- 集合层级：父集合 + 状态分区 + 主题子集合 ----
# 每篇论文归入「一个状态分区」+「零到多个主题子集合」
_PARENT_COLLECTION = "PaperPilot"
_STATUS_COLLECTIONS = {
    "inbox": "00_Inbox",
    "to_read": "10_ToRead",
    "analyzed": "20_Analyzed",
}
def _topic_collection_names(config):
    """从配置读取「主题 key → 集合名」映射（用 topic 的 label 作为集合名）。"""
    return {k: v.get("label", k) for k, v in (config.get("topics") or {}).items()}


def _fetch_collections(url, headers):
    """返回 {collection_key: {"name": str, "parent": str|False}}。"""
    out = {}
    try:
        r = httpx.get(f"{url}/api/users/0/collections", params={"limit": 100},
                      headers=headers, timeout=20)
        if r.status_code == 200:
            for c in r.json():
                d = c.get("data", {})
                out[c.get("key")] = {"name": d.get("name", ""),
                                     "parent": d.get("parentCollection", False)}
    except httpx.HTTPError:
        pass
    return out


def _create_collection(url, headers, name, parent_key):
    """创建集合，返回 key；失败返回 None。parent_key 为 None 表示顶层。"""
    payload = {"name": name}
    if parent_key:
        payload["parentCollection"] = parent_key
    else:
        payload["parentCollection"] = False
    try:
        r = httpx.post(f"{url}/api/users/0/collections", headers=headers,
                       json=[payload], timeout=20)
        if r.status_code in (200, 201):
            d = r.json()
            return ((d.get("successful") or {}).get("0", {}).get("key")
                    or (d.get("success") or {}).get("0"))
    except httpx.HTTPError:
        pass
    return None


def _ensure_collections(url, headers, config):
    """确保层级集合存在。返回 {'status': {role: key}, 'topic': {topic_key: key}}。"""
    existing = _fetch_collections(url, headers)
    name_to_key = {v["name"]: k for k, v in existing.items()}

    parent_key = name_to_key.get(_PARENT_COLLECTION)
    if not parent_key:
        parent_key = _create_collection(url, headers, _PARENT_COLLECTION, None)
        if parent_key:
            name_to_key[_PARENT_COLLECTION] = parent_key

    result = {"status": {}, "topic": {}}
    for role, name in _STATUS_COLLECTIONS.items():
        k = name_to_key.get(name)
        if not k:
            k = _create_collection(url, headers, name, parent_key)
            if k:
                name_to_key[name] = k
        result["status"][role] = k
    for tk, name in _topic_collection_names(config).items():
        k = name_to_key.get(name)
        if not k:
            k = _create_collection(url, headers, name, parent_key)
            if k:
                name_to_key[name] = k
        result["topic"][tk] = k
    return result


def _status_role(paper):
    if paper.get("analysis_status") == "completed":
        return "analyzed"
    if paper.get("reading_status") == "to-read":
        return "to_read"
    return "inbox"


def _collections_for(paper, colls):
    """返回论文应归属的集合 key 列表（状态分区 + 主题子集合）。"""
    keys = []
    ck = colls["status"].get(_status_role(paper))
    if ck:
        keys.append(ck)
    for t in paper.get("topics") or []:
        tk = colls["topic"].get(t)
        if tk and tk not in keys:
            keys.append(tk)
    return keys


def fetch_all_items(url, key, server_id):
    """读取 Zotero 库中全部条目（顶层 item，分页拉取）。

    返回 [{key, item_type, title, authors, date, year, doi, arxiv_id,
    url, abstract, extra, zotero_item_key, collection_keys, citation_key}]。
    用于反向同步（Zotero 手动加的论文 → 补录 Literature 卡）。
    """
    headers = {"Zotero-Server-ID": server_id, "Zotero-API-Key": key}
    items = []
    start = 0
    limit = 100
    while True:
        try:
            r = httpx.get(
                f"{url}/api/users/0/items/top",
                params={"limit": limit, "start": start,
                        "format": "json"},
                headers=headers, timeout=30,
            )
            if r.status_code != 200:
                print(f"[Zotero] 读取条目失败 status={r.status_code}: {r.text[:120]}")
                break
            try:
                batch = r.json()
            except Exception:
                print(f"[Zotero] 读取条目响应非 JSON: {r.text[:120]}")
                break
            if not batch:
                break
            for it in batch:
                # 兼容两种结构：{data:{...}} 或字段平铺
                d = it.get("data") if isinstance(it.get("data"), dict) else it
                if not d.get("title") and not d.get("itemType"):
                    continue  # 跳过空/垃圾条目（API 异常时可能返回无字段对象）
                item = {
                    "zotero_item_key": it.get("key", ""),
                    "item_type": d.get("itemType", ""),
                    "title": d.get("title", ""),
                    "doi": d.get("DOI", ""),
                    "url": d.get("url", ""),
                    "abstract": d.get("abstractNote", ""),
                    "date": d.get("date", ""),
                    "extra": d.get("extra", ""),
                    "citation_key": d.get("citationKey", ""),
                    "collection_keys": d.get("collections", []) or [],
                }
                # 作者
                authors = []
                for c in d.get("creators", []) or []:
                    if c.get("creatorType") == "author":
                        fn = (c.get("firstName") or "").strip()
                        ln = (c.get("lastName") or "").strip()
                        authors.append(f"{fn} {ln}".strip() or ln or fn)
                item["authors"] = authors
                # 年份
                year = ""
                m = re.search(r"(\d{4})", d.get("date") or "")
                if m:
                    year = m.group(1)
                item["year"] = year
                # arXiv id（从 extra 或 DOI/url 中抽取）
                arxiv_id = ""
                extra = d.get("extra") or ""
                ma = re.search(r"arXiv:\s*([^\s,;]+)", extra, re.IGNORECASE)
                if ma:
                    arxiv_id = ma.group(1)
                if not arxiv_id:
                    u = (d.get("url") or "") + (d.get("DOI") or "")
                    mb = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9v\.]+)", u,
                                   re.IGNORECASE)
                    if mb:
                        arxiv_id = mb.group(1)
                item["arxiv_id"] = arxiv_id
                items.append(item)
            if len(batch) < limit:
                break
            start += limit
        except httpx.HTTPError as exc:
            print(f"[Zotero] 读取条目异常: {exc}")
            break
    return items


def _patch_item_sync(url, headers, item_key, new_colls, citekey="",
                     status_keys=None):
    """对已有 item 做合并式回填：collections（归集）+ citekey 标签（Obsidian 桥接）。

    一次 GET 同时读 collections 与 tags，一次 PATCH 合并写回，不覆盖人工标签/集合。
    幂等：citekey 标签已存在则不再写入。

    status_keys 为状态分区（00_Inbox / 10_ToRead / 20_Analyzed）的集合 key 集合。
    状态分区互斥：论文状态变化时（如 to-read → analyzed），旧分区会被移除，
    否则一篇论文会同时挂在多个状态集合下。主题集合与人工集合不受影响。
    """
    if not item_key or (not new_colls and not citekey):
        return
    cur_colls = []
    cur_tags = []
    version = None
    try:
        r = httpx.get(f"{url}/api/users/0/items/{item_key}", headers=headers,
                      timeout=15)
        if r.status_code == 200:
            d = r.json()
            data = d.get("data", {}) or {}
            cur_colls = data.get("collections", []) or []
            cur_tags = [t.get("tag", "") for t in (data.get("tags") or [])]
            version = d.get("version")
    except httpx.HTTPError:
        pass

    payload = {}
    kept = list(cur_colls)
    if status_keys:
        kept = [c for c in kept if c not in status_keys or c in new_colls]
    merged = list(dict.fromkeys(kept + list(new_colls)))
    if merged:
        payload["collections"] = merged
    if citekey:
        tag = f"citekey/{citekey}"
        if tag not in cur_tags:
            payload["tags"] = [{"tag": t} for t in cur_tags + [tag]]
    if not payload:
        return
    h = dict(headers)
    if version:
        h["If-Unmodified-Since-Version"] = str(version)
    try:
        httpx.patch(f"{url}/api/users/0/items/{item_key}", headers=h,
                    json=payload, timeout=15)
    except httpx.HTTPError:
        pass


def patch_citekey(url, headers, item_key, citekey):
    """单独给一个已有 Zotero 条目回填 citekey 标签（供反向同步补录用）。"""
    _patch_item_sync(url, headers, item_key, [], citekey)


def add_via_local_api(papers, config):
    """全库入库 + 归入集合（幂等、批量）。

    - 尚无 zotero_item_key 的论文 → 批量入库，创建时即带上 collections 字段；
    - 已有 item key 的论文 → 用 PATCH 补 collections（合并，不覆盖人工集合）。

    返回 (成功数, 失败数, 是否可用, {paper_key: zotero_item_key})。
    """
    url = config["zotero"].get("local_api_url", "http://localhost:23119")
    ok, server_id = check_local_api(url)
    if not ok:
        return 0, 0, False, {}

    cached = _load_cached_key()
    key = cached.get("key") if cached.get("server_id") == server_id else ""
    if not key:
        print("[Zotero] 需要写权限：Zotero 将弹出授权对话框，请选择 Always Allow…")
        auth = authorize_local(url)
        if not auth:
            return 0, 0, True, {}
        key = auth["key"]
        if auth["remember"]:
            _save_cached_key(auth)

    headers = {"Zotero-Server-ID": server_id, "Zotero-API-Key": key,
               "Content-Type": "application/json"}
    collections = _ensure_collections(url, headers, config)

    to_add = [p for p in papers if not p.get("zotero_item_key")]
    existing = [p for p in papers if p.get("zotero_item_key")]

    added = failed = 0
    new_keys = {}

    # ---- 批量入库（一次 POST 多篇，创建时带上 collections）----
    BATCH = 50
    for i in range(0, len(to_add), BATCH):
        batch = to_add[i:i + BATCH]
        items = []
        batch_meta = []
        for p in batch:
            cks = _collections_for(p, collections)
            items.append(_to_zotero_item(p, cks))
            batch_meta.append((p, cks))
        try:
            r = httpx.post(f"{url}/api/users/0/items", headers=headers,
                           json=items, timeout=90)
            if r.status_code in (200, 201):
                d = r.json()
                successful = d.get("successful") or {}
                for idx, (p, _cks) in enumerate(batch_meta):
                    ik = successful.get(str(idx), {}).get("key")
                    if ik:
                        added += 1
                        new_keys[p["paper_key"]] = ik
                    else:
                        failed += 1
            elif r.status_code in (401, 428):
                auth = authorize_local(url)
                if auth:
                    headers["Zotero-API-Key"] = auth["key"]
                    if auth["remember"]:
                        _save_cached_key(auth)
                    r = httpx.post(f"{url}/api/users/0/items", headers=headers,
                                   json=items, timeout=90)
                    if r.status_code in (200, 201):
                        d = r.json()
                        successful = d.get("successful") or {}
                        for idx, (p, _cks) in enumerate(batch_meta):
                            ik = successful.get(str(idx), {}).get("key")
                            if ik:
                                added += 1
                                new_keys[p["paper_key"]] = ik
                            else:
                                failed += 1
                    else:
                        failed += len(batch)
                else:
                    failed += len(batch)
                    break
            else:
                print(f"  [Zotero] 批量入库失败 status={r.status_code}: {r.text[:150]}")
                failed += len(batch)
        except httpx.HTTPError as exc:
            print(f"  [Zotero] 批量入库异常: {exc}")
            failed += len(batch)
        print(f"  [Zotero] 进度：已入库 {added} / {len(to_add)}", flush=True)

    # ---- 已有 item 补 collections + 回填 citekey 标签（PATCH，合并不覆盖）----
    status_keys = set((collections.get("status") or {}).values())
    for p in existing:
        _patch_item_sync(url, headers, p["zotero_item_key"],
                         _collections_for(p, collections),
                         p.get("citation_key") or "",
                         status_keys=status_keys)

    # ---- 把新分配的 zotero_item_key 写回 DB（自包含，不依赖调用方）----
    if new_keys:
        try:
            from . import db as _db
            for pk, zk in new_keys.items():
                _db.set_sync_fields(pk, zotero_item_key=zk)
        except Exception as exc:  # noqa: BLE001
            print(f"  [Zotero] 写回 DB 失败（不影响入库）: {exc}")

    return added, failed, True, new_keys


def _to_zotero_item(paper, collection_keys=None):
    """转成 Zotero item JSON（v3 写入格式）。collection_keys 为归属集合 key 列表。"""
    etype = _entry_type(paper)
    item_type = "journalArticle" if etype == "article" else \
                ("conferencePaper" if etype == "inproceedings" else "preprint")
    creators = []
    for a in paper.get("authors") or []:
        a = a.strip()
        if not a:
            continue
        if "," in a:
            last, first = a.split(",", 1)
        else:
            parts = a.split()
            last = parts[-1] if parts else a
            first = " ".join(parts[:-1])
        creators.append({"creatorType": "author", "firstName": first.strip(),
                         "lastName": last.strip()})
    item = {
        "itemType": item_type,
        "title": paper.get("title") or "",
        "creators": creators,
        "abstractNote": (paper.get("abstract") or "")[:2000],
        "date": str(paper.get("year") or ""),
        "DOI": paper.get("doi") or "",
        "url": paper.get("url") or "",
        "extra": _build_extra(paper),
        "tags": [{"tag": t} for t in build_zotero_tags(paper)],
    }
    if collection_keys:
        item["collections"] = collection_keys
    return item


def _build_extra(paper):
    """构造条目的 extra 字段：arXiv id + citekey 桥接信息。

    写入 citekey 让用户能在 Zotero 条目里直接看到对应的 Obsidian 文献卡文件名，
    进而在 Obsidian 里用 [[citekey]] 或文件名定位笔记。
    """
    parts = []
    if paper.get("arxiv_id"):
        parts.append(f"arXiv: {paper['arxiv_id']}")
    if paper.get("citation_key"):
        parts.append(f"PaperPilot citekey: {paper['citation_key']}")
    return "\n".join(parts)
