"""反向同步：把 Zotero 里「手动加的、PaperPilot 还没有」的论文补录为 Literature 卡。

为什么需要它：
- 你在 Zotero 里手动拖进来的论文（或 PaperPilot 之外收集的文献），
  数据库里没有记录，因此知识卡（方法卡/研究卡/实验卡/综合分析/IMA 导出）
  无法用 [[citekey]] 挂接它们。
- 本脚本从 Zotero 本地 API 读取全部条目，找出「Zotero 有、PaperPilot 没有」的论文，
  生成稳定的 citation key，写入数据库，并生成 01-Literature 文献卡，
  之后这些论文就能像正常论文一样被知识卡引用。

用法：
    python sync_zotero_back.py            # 交互式：先列出新增，再确认入库
    python sync_zotero_back.py --yes      # 直接入库，不确认
    python sync_zotero_back.py --dry-run  # 只列出会新增哪些，不写任何文件

前置：Zotero 客户端需打开，且「设置→高级→允许其他应用通信」已勾选。
"""

import argparse
import sys

from paperpilot import config as cfg
from paperpilot import db
from paperpilot import obsidian, zotero
from paperpilot.citekey import make_citekey
from paperpilot.model import make_paper_key
from paperpilot.topics import score_relevance


def _zotero_to_paper(item, config):
    """把 Zotero 条目转成 PaperPilot 的 paper dict（未分析状态）。"""
    paper = {
        "title": item.get("title") or "(无标题)",
        "authors": item.get("authors") or [],
        "year": item.get("year") or "",
        "doi": item.get("doi") or "",
        "arxiv_id": item.get("arxiv_id") or "",
        "url": item.get("url") or "",
        "abstract": item.get("abstract") or "",
        "venue": "",          # Zotero 元数据里通常有 publicationTitle，这里暂空
        "venue_type": "",
        "publication_status": "unknown",
        "source": "zotero",
        "analysis_status": "pending",
        "reading_status": "unread",
        "zotero_item_key": item.get("zotero_item_key") or "",
        "raw": {},
    }
    paper["paper_key"] = make_paper_key(paper)
    topics, matched, rel = score_relevance(paper, config)
    paper["topics"] = topics
    paper["relevance_score"] = rel
    paper["raw"]["matched_keywords"] = matched
    return paper


def _fetch_items(config):
    url = config["zotero"].get("local_api_url", "http://localhost:23119")
    ok, server_id = zotero.check_local_api(url)
    if not ok:
        print("[反向同步] Zotero 本地 API 不可用，请先打开 Zotero 客户端。")
        return None, None, None

    cached = zotero._load_cached_key()
    key = cached.get("key") if cached.get("server_id") == server_id else ""
    if not key:
        print("[反向同步] 需要只读权限：Zotero 将弹出授权框，请选择 Always Allow…")
        auth = zotero.authorize_local(url, app_name="PaperPilot-Sync")
        if not auth:
            print("[反向同步] 未获得授权，终止。")
            return None, None, None
        key = auth["key"]
        if auth["remember"]:
            zotero._save_cached_key(auth)
    headers = {"Zotero-Server-ID": server_id, "Zotero-API-Key": key,
               "Content-Type": "application/json"}
    return zotero.fetch_all_items(url, key, server_id), url, headers


def main():
    parser = argparse.ArgumentParser(description="反向同步 Zotero 论文到 PaperPilot")
    parser.add_argument("--yes", "-y", action="store_true", help="直接入库，不确认")
    parser.add_argument("--dry-run", action="store_true", help="只列出新增，不写入")
    args = parser.parse_args()

    config = cfg.config()
    db.init_db()

    items, url, headers = _fetch_items(config)
    if items is None:
        sys.exit(1)
    if not items:
        print("[反向同步] Zotero 库中没有任何条目。")
        sys.exit(0)

    # 已有映射：citation_key 与 zotero_item_key 都算「已有」
    conn = db.get_conn()
    existing_cites = {r["citation_key"] for r in conn.execute(
        "SELECT citation_key FROM papers WHERE citation_key IS NOT NULL "
        "AND citation_key != ''").fetchall()}
    existing_zkeys = {r["zotero_item_key"] for r in conn.execute(
        "SELECT zotero_item_key FROM papers WHERE zotero_item_key IS NOT NULL "
        "AND zotero_item_key != ''").fetchall()}
    # 已占用的 title 规范化键（避免 Zotero 里同一篇被重复补录）
    existing_paper_keys = {r["paper_key"] for r in conn.execute(
        "SELECT paper_key FROM papers").fetchall()}
    conn.close()

    # 找出「Zotero 有、PaperPilot 没有」的条目
    to_add = []
    for it in items:
        zk = it.get("zotero_item_key") or ""
        # 已在库中（按 zotero_item_key 或 citation_key 匹配）→ 跳过
        if zk and zk in existing_zkeys:
            continue
        if it.get("citation_key") and it["citation_key"] in existing_cites:
            continue
        paper = _zotero_to_paper(it, config)
        if paper["paper_key"] in existing_paper_keys:
            # 标题/DOI/arXiv 已存在，仅补 zotero_item_key 关联
            to_add.append(("link", paper))
            continue
        to_add.append(("new", paper))

    if not to_add:
        print("[反向同步] Zotero 与 PaperPilot 已完全对齐，无需补录。")
        sys.exit(0)

    print(f"\n[反向同步] 发现 {len(to_add)} 个条目可补录：")
    new_items = [p for kind, p in to_add if kind == "new"]
    link_items = [p for kind, p in to_add if kind == "link"]
    print(f"  · 新增论文：{len(new_items)} 篇")
    print(f"  · 补 Zotero 关联：{len(link_items)} 篇")
    print()
    for kind, p in to_add:
        tag = "新增" if kind == "new" else "关联"
        print(f"  [{tag}] {p['title'][:60]}  ({p.get('year', '?')})")

    if args.dry_run:
        print("\n[反向同步] --dry-run 模式：未写入任何数据。")
        sys.exit(0)

    if not args.yes:
        ans = input("\n是否将这 %d 个条目补录入库并生成文献卡？[y/N] " % len(to_add))
        if ans.strip().lower() not in ("y", "yes"):
            print("已取消。")
            sys.exit(0)

    # 分配 citekey（避免与已有冲突）
    used = set(existing_cites)
    vault_path = config.get("obsidian", {}).get("vault_path") or \
        str(cfg.ROOT / "obsidian_vault")
    obsidian.ensure_vault(vault_path)

    created = linked = 0
    for kind, p in to_add:
        citekey = make_citekey(p.get("authors"), p.get("year"), p.get("title"),
                               used)
        used.add(citekey)
        p["citation_key"] = citekey

        if kind == "new":
            db.upsert_paper(p)
            # 写 Literature 文献卡
            path, action = obsidian.write_paper_note(p, vault_path, citekey)
            db.log_sync("obsidian", p["paper_key"], action, str(path))
            db.set_sync_fields(p["paper_key"],
                               obsidian_note_path=str(path),
                               citation_key=citekey,
                               zotero_item_key=p.get("zotero_item_key"))
            created += 1
        else:
            # 已存在：只补 zotero_item_key 关联（不覆盖已有分析）
            db.set_sync_fields(p["paper_key"],
                               zotero_item_key=p.get("zotero_item_key"))
            linked += 1

        # 把 citekey 回填到 Zotero 条目（标签），使 Zotero 能反向定位 Obsidian 文献卡
        if headers and p.get("zotero_item_key"):
            zotero.patch_citekey(url, headers, p["zotero_item_key"], citekey)

    print(f"\n[反向同步] 完成：新增论文 {created} 篇、补关联 {linked} 篇。")
    print(f"  文献卡目录：{vault_path}/01-Literature/")
    print("  现在知识卡（方法/研究/实验/综合分析/IMA 导出）即可用 [[citekey]] 挂接这些论文。")


if __name__ == "__main__":
    main()
