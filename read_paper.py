"""对数据库中**已存在**的论文执行单篇精读（工作台「精读」按钮的后端）。

与 ingest_one.py 的区别：ingest_one 处理的是「Zotero 里有、PaperPilot 还没有」的
条目；本脚本处理的是「PaperPilot 库里已有、但尚未精读（或想重新精读）」的论文。

流程（对指定的一篇论文）：
1. 按 --key 定位论文（支持 paper_key / citation_key / 数据库 id，模糊匹配标题）；
2. 判断是否已精读过：reading_status == "read" 且 Obsidian 笔记文件存在；
   - 已精读且未强制 → 直接返回现有笔记路径（前端据此打开 Obsidian）；
   - 未精读（或 --force）→ 走完整精读流程；
3. 精读：retrieve_related → analyze_paper → validate → mark_analyzed；
4. 生成单篇报告 + 写 Obsidian 文献卡；
5. 标记 reading_status = "read"，写回 obsidian_note_path。

用法：
    python read_paper.py --key <paper_key|citation_key|id>
    python read_paper.py --key <标题关键词> --match title
    python read_paper.py --list-read            # 列出已精读/未精读统计
    python read_paper.py --key ... --force      # 忽略已有精读，强制重新精读
    python read_paper.py --key ... --depth deep # 指定分析深度 skim/standard/deep
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paperpilot import config as cfg
from paperpilot import db, obsidian, related as related_mod, reports, schemas
from paperpilot.analyzer import analyze_paper


def _find_paper(conn, key, match):
    """按 key 定位论文，返回完整 paper dict（含 analysis 等）。"""
    if match == "id":
        row = conn.execute(
            "SELECT * FROM papers WHERE id = ?", (key,)).fetchone()
    elif match == "title":
        row = conn.execute(
            "SELECT * FROM papers WHERE title LIKE ? ORDER BY relevance_score DESC "
            "LIMIT 1", (f"%{key}%",)).fetchone()
    else:  # auto：依次尝试 paper_key / citation_key / id
        row = conn.execute(
            "SELECT * FROM papers WHERE paper_key = ? OR citation_key = ? "
            "LIMIT 1", (key, key)).fetchone()
        if row is None and str(key).isdigit():
            row = conn.execute(
                "SELECT * FROM papers WHERE id = ?", (int(key),)).fetchone()

    if row is None:
        return None
    # 复用 db 的反序列化逻辑：取全量（带 analysis）
    full = db.load_papers_for_report()
    for p in full:
        if p["paper_key"] == row["paper_key"]:
            return p
    return dict(row)


def _note_exists(paper):
    p = paper.get("obsidian_note_path")
    if not p:
        return False
    return Path(p).exists()


def _is_read(paper):
    return (paper.get("reading_status") == "read" and _note_exists(paper))


def _extract_user_notes(note_path):
    """提取文献卡 USER 区块里用户已填写的实质内容，作为「继续精读」的上下文。"""
    try:
        text = Path(note_path).read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""
    i = text.find(obsidian.USER_START)
    if i == -1:
        return ""
    j = text.find(obsidian.USER_END, i)
    raw = text[i + len(obsidian.USER_START): j if j != -1 else len(text)]
    keep = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "（在此记录" in s:
            continue
        if s.startswith("- "):
            body = s[2:]
            if re.match(r"^[^：:]+[：:]\s*$", body):  # 仅标签、冒号后无内容
                continue
        keep.append(s)
    return "\n".join(keep).strip()


def _backfill_zotero(paper, config):
    """单独精读的论文若缺 Zotero 关联，尝试补录一条（Zotero 未开则静默跳过）。"""
    try:
        from paperpilot import zotero as zotero_mod
        added, failed, ok, new_keys = zotero_mod.add_via_local_api([paper], config)
        if not ok:
            print("  Zotero 本地 API 不可用，跳过补录（下次打开 Zotero 后再精读/同步即可）")
            return
        zk = new_keys.get(paper["paper_key"])
        if zk:
            db.set_sync_fields(paper["paper_key"], zotero_item_key=zk)
            print(f"  已补录 Zotero：{zk}")
        else:
            print("  Zotero 补录未返回条目 key（可能已存在或写入失败）")
    except Exception as exc:  # noqa: BLE001
        print(f"  Zotero 补录异常（忽略）: {exc}")


def _deep_read(paper, config, args):
    """对一篇论文执行完整精读并写回。返回结果摘要 dict。"""
    # 0) 继续精读：若已有笔记，提取用户手写区块作为上下文
    prior_notes = ""
    note_path = paper.get("obsidian_note_path")
    if note_path and Path(note_path).exists():
        prior_notes = _extract_user_notes(note_path)
        if prior_notes:
            print("  检测到已有精读笔记，将在其基础上继续精读…")

    # 1) 深度分析
    depth = args.depth or "auto"
    print(f"  正在深度分析（depth={depth}）…")
    related, scope = related_mod.retrieve_related(paper, config)
    result = analyze_paper(paper, config, related=related,
                           requested_depth=depth, prior_notes=prior_notes)
    if result.get("status") == "completed":
        validation = schemas.validate_analysis(result, paper=paper,
                                               related=related)
        result["validation"] = validation
        result["quality_status"] = (
            "validated" if validation["passed"] else "needs_review")

    pid = db.get_paper_id(paper["paper_key"])
    if pid is None:
        return {"title": paper.get("title"), "status": "error",
                "reason": "未取得 paper id"}
    db.mark_analyzed(pid, result)
    paper["analysis_status"] = result.get("status", "completed")

    if result.get("status") != "completed":
        return {"title": paper.get("title"),
                "status": result.get("status", "failed"),
                "reason": result.get("reason", "")}

    # 2) 报告 + 文献卡
    paper["novelty_score"] = result.get("novelty_score")
    paper["evidence_score"] = result.get("evidence_score")
    paper["transfer_score"] = result.get("transfer_score")
    paper["analysis"] = result
    report_path = reports.generate_paper_report(paper, config)
    citekey = paper.get("citation_key") or ""
    if not citekey:
        from paperpilot.citekey import make_citekey
        conn = db.get_conn()
        used = {r["citation_key"] for r in conn.execute(
            "SELECT citation_key FROM papers WHERE citation_key IS NOT NULL "
            "AND citation_key != ''").fetchall()}
        conn.close()
        citekey = make_citekey(paper.get("authors"), paper.get("year"),
                               paper.get("title"), used)
        db.set_sync_fields(paper["paper_key"], citation_key=citekey)

    vault_path = config.get("obsidian", {}).get("vault_path") or \
        str(cfg.ROOT / "obsidian_vault")
    obsidian.ensure_vault(vault_path)
    note_path, _action = obsidian.write_paper_note(paper, vault_path, citekey)

    # 3) 标记已精读 + 写回笔记路径
    db.set_sync_fields(paper["paper_key"],
                       obsidian_note_path=str(note_path),
                       reading_status="read")

    # 4) 若尚未入库 Zotero，尝试补录（单独精读的论文常因当时 Zotero 未开而缺关联）
    if not paper.get("zotero_item_key"):
        _backfill_zotero(paper, config)

    return {"title": paper.get("title"), "status": "completed",
            "report": str(report_path), "note": str(note_path)}


def main():
    ap = argparse.ArgumentParser(description="对库中已有论文执行单篇精读")
    ap.add_argument("--key", type=str, default="",
                    help="论文定位：paper_key / citation_key / 数据库 id / 标题关键词")
    ap.add_argument("--match", type=str, default="auto",
                    choices=["auto", "id", "title"],
                    help="定位方式（默认 auto 自动判断）")
    ap.add_argument("--depth", type=str, default="",
                    choices=["auto", "skim", "standard", "deep"],
                    help="分析深度（默认 auto）")
    ap.add_argument("--force", action="store_true",
                    help="已精读过也强制重新精读")
    ap.add_argument("--list-read", action="store_true",
                    help="列出已精读/未精读统计")
    args = ap.parse_args()

    config = cfg.config()
    db.init_db()

    if args.list_read:
        conn = db.get_conn()
        read = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE reading_status = 'read'"
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        conn.close()
        print(f"已精读 {read} / 共 {total} 篇")
        sys.exit(0)

    if not args.key:
        print("请用 --key 指定论文（paper_key / citation_key / id / 标题关键词）。")
        sys.exit(1)

    conn = db.get_conn()
    paper = _find_paper(conn, args.key, args.match)
    conn.close()
    if paper is None:
        print(f"[read_paper] 未找到匹配「{args.key}」的论文。")
        sys.exit(1)

    print(f"\n目标论文：{paper.get('title')}")
    print(f"  paper_key：{paper.get('paper_key')}")
    print(f"  citekey：{paper.get('citation_key') or '—'}")
    print(f"  主题：{paper.get('topics') or '—'}")
    print(f"  关键词：{paper.get('keywords') or '—'}")
    print(f"  阅读状态：{paper.get('reading_status') or 'unread'}")

    # 已精读且未强制 → 直接返回笔记路径
    if _is_read(paper) and not args.force:
        note = paper.get("obsidian_note_path")
        print(f"\n该论文已精读过，笔记已存在：{note}")
        print(f"__NOTE__{note}")
        sys.exit(0)

    if args.force and _is_read(paper):
        print("  （--force 已指定，忽略已有精读，重新精读）")

    print(f"\n开始精读「{paper.get('title')}」…")
    r = _deep_read(paper, config, args)

    print("\n" + "=" * 60)
    if r["status"] == "completed":
        print(f"  ✓ {r['title'][:60]}")
        print(f"    报告：{r.get('report')}")
        print(f"    文献卡：{r.get('note')}")
        print(f"__NOTE__{r.get('note')}")
    else:
        print(f"  · {r['title'][:60]} [{r['status']}] {r.get('reason', '')}")
    print("=" * 60)
    sys.exit(0 if r["status"] == "completed" else 1)


if __name__ == "__main__":
    main()
