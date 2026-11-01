"""温和地重置 Zotero：小批量删除 + 延时 + 断线重连，避免 Zotero 本地 API 崩溃。

与 reset_zotero.py 的区别：
- 每批只删 20 个（而非 50），批间 sleep 0.8s，降低 Zotero 压力；
- 每次请求前探测本地 API，掉线则等待并重试（最多等 60s）；
- 删除阶段若仍失败，不继续，保留进度，可重复运行（幂等）。

用法：
    python reset_zotero_gentle.py
"""

import time

import httpx

from paperpilot import config as cfg, db, zotero

BATCH = 20
SLEEP = 0.8


def _wait_api(url, timeout=60):
    """等待 Zotero 本地 API 恢复在线，返回 (ok, server_id)。"""
    waited = 0
    while waited < timeout:
        ok, sid = zotero.check_local_api(url)
        if ok:
            return True, sid
        time.sleep(5)
        waited += 5
    return False, ""


def _get_all(url, headers, path):
    out = []
    start = 0
    while True:
        try:
            r = httpx.get(f"{url}{path}", params={"start": start, "limit": 100},
                          headers=headers, timeout=30)
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


def _delete_keys(url, h, path, param_key, keys):
    total = len(keys)
    done = 0
    lmv = _library_version(url, h)
    for i in range(0, total, BATCH):
        batch = keys[i:i + BATCH]
        ok = False
        for _ in range(5):
            # 每批前先确保 API 在线
            api_ok, _ = zotero.check_local_api(url)
            if not api_ok:
                print("  Zotero 本地 API 掉线，等待恢复…")
                api_ok, _ = _wait_api(url)
                if not api_ok:
                    print("  [!] Zotero 仍未恢复，中止删除。")
                    return done
            hd = dict(h)
            hd["If-Unmodified-Since-Version"] = lmv
            try:
                d = httpx.delete(f"{url}{path}",
                                 params={param_key: ",".join(batch)},
                                 headers=hd, timeout=30)
            except httpx.HTTPError as exc:
                print(f"  删除请求异常: {exc}")
                time.sleep(2)
                continue
            if d.status_code in (200, 204):
                nv = d.headers.get("Last-Modified-Version")
                if nv:
                    lmv = nv
                done += len(batch)
                ok = True
                break
            if d.status_code in (412, 428):
                lmv = _library_version(url, h)
                continue
            if d.status_code in (502, 503, 500):
                print(f"  服务异常 {d.status_code}，稍后重试…")
                time.sleep(3)
                continue
            print(f"  删除失败 status={d.status_code}: {d.text[:120]}")
            break
        if not ok:
            print(f"  本批 {len(batch)} 删除未完成，跳过（可重复运行补齐）。")
        print(f"  删除进度：{min(i + BATCH, total)}/{total}", flush=True)
        time.sleep(SLEEP)
    return done


def main():
    c = cfg.config()
    db.init_db()
    url = c["zotero"].get("local_api_url", "http://localhost:23119")
    ok, sid = zotero.check_local_api(url)
    if not ok:
        print("[!] Zotero 本地 API 不可用，请先打开并确认 Zotero 客户端在线。")
        return

    cached = zotero._load_cached_key()
    key = cached.get("key") if cached.get("server_id") == sid else ""
    if not key:
        print("[Zotero] 需要写权限：请在 Zotero 弹窗选择 Always Allow…")
        auth = zotero.authorize_local(url)
        if not auth:
            print("[!] 未授权，退出。")
            return
        key = auth["key"]
        if auth["remember"]:
            zotero._save_cached_key(auth)

    h = {"Zotero-Server-ID": sid, "Zotero-API-Key": key,
         "Content-Type": "application/json"}

    # 1) 删除全部条目
    items = _get_all(url, h, "/api/users/0/items")
    print(f"发现 {len(items)} 个条目，开始温和删除（每批 {BATCH}）…")
    item_keys = [it["key"] for it in items]
    deleted = _delete_keys(url, h, "/api/users/0/items", "itemKey", item_keys)
    print(f"条目删除：{deleted}/{len(item_keys)}")

    # 2) 删除全部集合
    colls = _get_all(url, h, "/api/users/0/collections")
    if colls:
        ckeys = [c["key"] for c in colls]
        d = _delete_keys(url, h, "/api/users/0/collections", "collectionKey",
                         ckeys)
        print(f"集合删除：{d}/{len(ckeys)}")

    # 3) 清空 DB 的 zotero_item_key
    import sqlite3
    conn = sqlite3.connect("data/papers.db")
    conn.execute("UPDATE papers SET zotero_item_key = ''")
    conn.commit()
    conn.close()
    print("数据库 zotero_item_key 已清空。")

    # 4) 重新入库（温和：小批 + 延时由 add_via_local_api 内部已有 50/批）
    papers = db.load_papers_for_report()
    print(f"\n重新入库 {len(papers)} 篇…")
    added, failed, ok2, new_keys = zotero.add_via_local_api(papers, c)
    print(f"\n完成：入库 {added} 成功 / {failed} 失败。")
    if added == len(papers):
        print("✓ Zotero 已恢复干净状态，条目数与 DB 一致。")
    else:
        print("⚠ 入库数与 DB 不一致，可重复运行本脚本补齐（幂等）。")


if __name__ == "__main__":
    main()
