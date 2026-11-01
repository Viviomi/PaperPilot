"""单篇论文即时入库 + 深度分析精读。

场景：你临时找到一篇论文，随手拖进 Zotero（或用浏览器插件保存），想立刻让
PaperPilot 对**这一篇**做入库、打标签、深度分析、生成报告与文献卡，而不是等
每日全量同步。

流程（对选中的一篇论文）：
1. 从 Zotero 本地 API 读取条目，找出「Zotero 有、PaperPilot 还没有」的候选；
2. 确认要处理哪一篇（--title 关键词过滤 / 交互选择 / --all 全部处理）；
3. 补录进数据库：生成 citation key、算主题标签 + 关键词标签（含 LLM 提炼补充）；
4. 立即做三遍阅读式深度分析（含 OpenAlex 相关文献检索）；
5. 生成单篇报告 + Obsidian 文献卡；
6. 归集进 Zotero 对应状态分区 + 主题集合。

用法：
    python ingest_one.py --list                      # 列出 Zotero 里待入库的条目
    python ingest_one.py --title "domain adaptation" # 按标题关键词匹配并处理
    python ingest_one.py                             # 交互选择一篇
    python ingest_one.py --all                       # 处理所有待入库条目
    python ingest_one.py --title "..." --depth deep   # 指定分析深度 skim/standard/deep

前置：Zotero 客户端需打开，且「设置→高级→允许其他应用通信」已勾选。
"""

import argparse
import sys
import time

from paperpilot import config as cfg
from paperpilot import db, obsidian, related as related_mod, reports, schemas, zotero
from paperpilot.analyzer import analyze_paper
from paperpilot.citekey import make_citekey
from paperpilot.keywords import extract_keyword_tags
from paperpilot.model import make_paper_key
from paperpilot.topics import score_relevance


def _fetch_items(config):
    """读 Zotero 本地 API 全部条目（复用反向同步的授权逻辑）。"""
    url = config["zotero"].get("local_api_url", "http://localhost:23119")
    ok, server_id = zotero.check_local_api(url)
    if not ok:
        print("[ingest] Zotero 本地 API 不可用，请先打开 Zotero 客户端。")
        return None

    cached = zotero._load_cached_key()
    key = cached.get("key") if cached.get("server_id") == server_id else ""
    if not key:
        print("[ingest] 需要只读权限：Zotero 将弹出授权框，请选择 Always Allow…")
        auth = zotero.authorize_local(url, app_name="PaperPilot-Ingest")
        if not auth:
            print("[ingest] 未获得授权，终止。")
            return None
        key = auth["key"]
        if auth["remember"]:
            zotero._save_cached_key(auth)
    return zotero.fetch_all_items(url, key, server_id)


def _existing_keys(conn):
    """返回已有映射，用于去重与 citekey 分配。"""
    cites = {r["citation_key"] for r in conn.execute(
        "SELECT citation_key FROM papers WHERE citation_key IS NOT NULL "
        "AND citation_key != ''").fetchall()}
    zkeys = {r["zotero_item_key"] for r in conn.execute(
        "SELECT zotero_item_key FROM papers WHERE zotero_item_key IS NOT NULL "
        "AND zotero_item_key != ''").fetchall()}
    pkeys = {r["paper_key"] for r in conn.execute(
        "SELECT paper_key FROM papers").fetchall()}
    return cites, zkeys, pkeys


def _zotero_to_paper(item, config):
    """把 Zotero 条目转成 paper dict（复用反向同步的字段映射）。"""
    paper = {
        "title": item.get("title") or "(无标题)",
        "authors": item.get("authors") or [],
        "year": item.get("year") or "",
        "doi": item.get("doi") or "",
        "arxiv_id": item.get("arxiv_id") or "",
        "url": item.get("url") or "",
        "abstract": item.get("abstract") or "",
        "venue": "",
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


def _find_candidates(items, config):
    """找出 Zotero 有、PaperPilot 还没有的条目。返回 [(kind, paper)]。"""
    conn = db.get_conn()
    cites, zkeys, pkeys = _existing_keys(conn)
    conn.close()

    candidates = []
    for it in items:
        zk = it.get("zotero_item_key") or ""
        if zk and zk in zkeys:
            continue
        if it.get("citation_key") and it["citation_key"] in cites:
            continue
        paper = _zotero_to_paper(it, config)
        if paper["paper_key"] in pkeys:
            candidates.append(("link", paper))
        else:
            candidates.append(("new", paper))
    return candidates


def _pick(candidates, args):
    """根据命令行参数挑选要处理的条目。返回 [(kind, paper)]。"""
    if not candidates:
        return []
    if args.all:
        return candidates

    if args.title:
        kw = args.title.lower()
        picked = [c for c in candidates if kw in (c[1]["title"] or "").lower()]
        if not picked:
            print(f"[ingest] 没有标题含「{args.title}」的待入库条目。")
            return []
        return picked

    # 交互选择
    print(f"\n发现 {len(candidates)} 个待入库条目：")
    for i, (kind, p) in enumerate(candidates, 1):
        tag = "新增" if kind == "new" else "关联"
        print(f"  {i}. [{tag}] {p['title'][:70]}  ({p.get('year', '?')})")
    print("  0. 全部处理")
    print("  q. 退出")
    try:
        ans = input("\n选择序号（默认 1）：").strip()
    except EOFError:
        ans = ""
    if ans.lower() in ("q", "quit"):
        return []
    if ans == "0":
        return candidates
    if ans == "":
        ans = "1"
    try:
        idx = int(ans) - 1
        if 0 <= idx < len(candidates):
            return [candidates[idx]]
    except ValueError:
        pass
    print("[ingest] 无效输入，已取消。")
    return []


def _ingest_one(kind, paper, config, used_cites, vault_path, args):
    """对一篇论文执行：入库 → 分析 → 报告 → 文献卡 → 归集。返回结果摘要 dict。"""
    # 1) 分配 citekey 并入库
    citekey = make_citekey(paper.get("authors"), paper.get("year"),
                           paper.get("title"), used_cites)
    used_cites.add(citekey)
    paper["citation_key"] = citekey

    # 关键词标签（命中优先 + LLM 提炼补充）
    matched = paper["raw"].get("matched_keywords", {})
    kw_tags, new_kws = extract_keyword_tags(paper, config, matched, use_llm=True)
    paper["keywords"] = kw_tags
    if new_kws:
        paper["raw"]["new_keywords"] = new_kws

    if kind == "new":
        db.upsert_paper(paper)
    else:
        # 已存在：补 zotero 关联 + 刷新关键词/主题（不覆盖已有分析）
        db.set_sync_fields(paper["paper_key"],
                           zotero_item_key=paper.get("zotero_item_key"))
        db.upsert_paper(paper)

    pid = db.get_paper_id(paper["paper_key"])
    if pid is None:
        return {"title": paper["title"], "status": "error",
                "reason": "入库后未取得 paper id"}

    print(f"\n  标题：{paper['title']}")
    print(f"  citekey：{citekey}")
    print(f"  主题：{paper.get('topics') or '—'}")
    print(f"  关键词：{paper.get('keywords') or '—'}")

    # 2) 深度分析
    depth = args.depth or "auto"
    print(f"  正在深度分析（depth={depth}）…")
    related, scope = related_mod.retrieve_related(paper, config)
    result = analyze_paper(paper, config, related=related,
                           requested_depth=depth)
    if result.get("status") == "completed":
        validation = schemas.validate_analysis(result, paper=paper,
                                               related=related)
        result["validation"] = validation
        result["quality_status"] = (
            "validated" if validation["passed"] else "needs_review")
    db.mark_analyzed(pid, result)
    paper["analysis_status"] = result.get("status", "completed")

    if result.get("status") == "completed":
        paper["novelty_score"] = result.get("novelty_score")
        paper["evidence_score"] = result.get("evidence_score")
        paper["transfer_score"] = result.get("transfer_score")
        # 读回最新（含 analysis），用于生成报告与文献卡
        full = db.load_papers_for_report()
        paper_full = next((p for p in full if p["paper_key"] == paper["paper_key"]), paper)
        report_path = reports.generate_paper_report(paper_full, config)
        note_path, _action = obsidian.write_paper_note(paper_full, vault_path,
                                                       citekey)
        db.set_sync_fields(paper["paper_key"],
                           obsidian_note_path=str(note_path))
        # 标记为待精读
        db.set_sync_fields(paper["paper_key"], reading_status="to-read")
        return {"title": paper["title"], "status": "completed",
                "report": str(report_path), "note": str(note_path)}
    else:
        return {"title": paper["title"], "status": result.get("status", "failed"),
                "reason": result.get("reason", "")}


def main():
    ap = argparse.ArgumentParser(description="单篇论文即时入库 + 深度分析")
    ap.add_argument("--list", action="store_true",
                    help="只列出 Zotero 里待入库的条目，不处理")
    ap.add_argument("--title", type=str, default="",
                    help="按标题关键词匹配要处理的论文")
    ap.add_argument("--all", action="store_true",
                    help="处理所有待入库条目")
    ap.add_argument("--depth", type=str, default="",
                    choices=["auto", "skim", "standard", "deep"],
                    help="分析深度（默认 auto 自动判断）")
    ap.add_argument("--no-zotero-sync", action="store_true",
                    help="入库后不归集 Zotero 集合（避免高频操作掉线）")
    args = ap.parse_args()

    config = cfg.config()
    db.init_db()

    items = _fetch_items(config)
    if items is None:
        sys.exit(1)
    if not items:
        print("[ingest] Zotero 库中没有任何条目。")
        sys.exit(0)

    candidates = _find_candidates(items, config)

    if args.list:
        if not candidates:
            print("[ingest] Zotero 与 PaperPilot 已完全对齐，没有待入库条目。")
        else:
            print(f"\n待入库条目 {len(candidates)} 个：")
            for kind, p in candidates:
                tag = "新增" if kind == "new" else "关联"
                print(f"  [{tag}] {p['title'][:70]}  ({p.get('year', '?')})")
        sys.exit(0)

    picked = _pick(candidates, args)
    if not picked:
        sys.exit(0)

    # citekey 分配需考虑全库已有
    conn = db.get_conn()
    cites, _zkeys, _pkeys = _existing_keys(conn)
    conn.close()
    used_cites = set(cites)

    vault_path = config.get("obsidian", {}).get("vault_path") or \
        str(cfg.ROOT / "obsidian_vault")
    obsidian.ensure_vault(vault_path)

    print(f"\n开始处理 {len(picked)} 篇论文…")
    done = []
    for i, (kind, paper) in enumerate(picked, 1):
        print(f"\n[{i}/{len(picked)}]")
        r = _ingest_one(kind, paper, config, used_cites, vault_path, args)
        done.append(r)
        time.sleep(1)

    # 汇总
    print("\n" + "=" * 60)
    for r in done:
        if r["status"] == "completed":
            print(f"  ✓ {r['title'][:60]}")
            print(f"    报告：{r.get('report')}")
            print(f"    文献卡：{r.get('note')}")
        else:
            print(f"  · {r['title'][:60]} [{r['status']}] {r.get('reason', '')}")
    print("=" * 60)

    # 归集 Zotero 集合（可选）
    if not args.no_zotero_sync:
        print("\n归集 Zotero 集合…")
        try:
            full = db.load_papers_for_report()
            added, failed, ok, _nk = zotero.add_via_local_api(full, config)
            if ok:
                print(f"  Zotero 归集：{added} 成功 / {failed} 失败")
            else:
                print("  Zotero 本地 API 不可用，跳过归集（不影响入库与分析）。")
        except Exception as exc:  # noqa: BLE001
            print(f"  Zotero 归集失败（不影响核心流程）: {exc}")


if __name__ == "__main__":
    main()
