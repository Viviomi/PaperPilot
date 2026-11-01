# -*- coding: utf-8 -*-
"""重置 Zotero 文献库并重新整理（清空 → 干净入库 + 正确归集）。

用途：当 Zotero 里积累了重复/未归集的条目时，一键恢复干净状态。

流程：
    1. 删除 Zotero 里全部条目与集合（本库为 PaperPilot 专用，可安全清空）；
    2. 清空数据库里的 zotero_item_key；
    3. 用修复后的逻辑重新入库全部论文，并归入「状态分区 + 主题子集合」。

注意：Zotero 的 DELETE 要求 If-Unmodified-Since-Version 头（库版本号），
且每次删除后版本递增，需动态同步，否则返回 412/428。
"""

import sqlite3

import httpx

from paperpilot import config as cfg, db, zotero


def _get_all(url, headers, path, params=None):
    """分页拉取某个集合资源的全部记录。"""
    out = []
    start = 0
    while True:
        p = dict(params or {})
        p["start"] = start
        p["limit"] = 100
        try:
            r = httpx.get(f"{url}{path}", params=p, headers=headers, timeout=30)
        except httpx.HTTPError:
            break
        if r.status_code != 200:
            break
        data = r.json()
        if not data:
            break
        out.extend(data)
        if len(data) < 100:
            break
        start += 100
    return out


def _library_version(url, h):
    try:
        r = httpx.get(f"{url}/api/users/0/items", params={"limit": 1},
                      headers=h, timeout=15)
        return r.headers.get("Last-Modified-Version", "0")
    except httpx.HTTPError:
        return "0"


def _delete_batch(url, h, path, param_key, keys):
    """批量删除，自动维护 If-Unmodified-Since-Version。"""
    total = len(keys)
    done = 0
    lmv = _library_version(url, h)
    for i in range(0, total, 50):
        batch = keys[i:i + 50]
        ok = False
        for _ in range(4):
            hd = dict(h)
            hd["If-Unmodified-Since-Version"] = lmv
            try:
                d = httpx.delete(f"{url}{path}",
                                 params={param_key: ",".join(batch)},
                                 headers=hd, timeout=30)
            except httpx.HTTPError as exc:
                print(f"  删除请求异常: {exc}")
                break
            if d.status_code in (200, 204):
                nv = d.headers.get("Last-Modified-Version")
                if nv:
                    lmv = nv
                done += len(batch)
                ok = True
                break
            if d.status_code in (412, 428):
                # 版本过期：重新同步库版本后重试
                lmv = _library_version(url, h)
                continue
            print(f"  删除失败 status={d.status_code}: {d.text[:120]}")
            break
        if not ok:
            print(f"  本批 {len(batch)} 个删除未完成，已跳过。")
        if (i // 50) % 5 == 0:
            print(f"  删除进度：{min(i + 50, total)}/{total}", flush=True)
    return done


def main():
    c = cfg.config()
    db.init_db()
    url = c["zotero"].get("local_api_url", "http://localhost:23119")
    ok, sid = zotero.check_local_api(url)
    if not ok:
        print("[!] Zotero 本地 API 不可用，请先打开 Zotero 客户端。")
        return

    cached = zotero._load_cached_key()
    key = cached.get("key") if cached.get("server_id") == sid else ""
    if not key:
        print("[Zotero] 需要写权限：请在 Zotero 弹窗里选择 Always Allow…")
        auth = zotero.authorize_local(url)
        if not auth:
            print("[!] 未授权，退出。")
            return
        key = auth["key"]
        if auth["remember"]:
            zotero._save_cached_key(auth)

    h = {"Zotero-Server-ID": sid, "Zotero-API-Key": key,
         "Content-Type": "application/json"}

    # ---- 1) 删除全部条目 ----
    all_items = _get_all(url, h, "/api/users/0/items")
    print(f"发现 {len(all_items)} 个条目，正在删除…")
    item_keys = [it["key"] for it in all_items]
    deleted = _delete_batch(url, h, "/api/users/0/items", "itemKey", item_keys)
    print(f"条目删除完成：{deleted}/{len(item_keys)}。")

    # ---- 2) 删除全部集合 ----
    colls = _get_all(url, h, "/api/users/0/collections")
    if colls:
        ckeys = [c["key"] for c in colls]
        d = _delete_batch(url, h, "/api/users/0/collections", "collectionKey",
                          ckeys)
        print(f"集合删除完成：{d}/{len(ckeys)}。")

    # ---- 3) 清空 DB 的 zotero_item_key ----
    conn = sqlite3.connect("data/papers.db")
    conn.execute("UPDATE papers SET zotero_item_key = ''")
    conn.commit()
    conn.close()
    print("数据库 zotero_item_key 已清空。")

    # ---- 4) 重新入库 + 归集 ----
    papers = db.load_papers_for_report()
    print(f"\n重新入库 {len(papers)} 篇…")
    added, failed, ok2, new_keys = zotero.add_via_local_api(papers, c)
    for pk, zk in new_keys.items():
        db.set_sync_fields(pk, zotero_item_key=zk)
    print(f"\n完成：入库 {added} 成功 / {failed} 失败。")


if __name__ == "__main__":
    main()
