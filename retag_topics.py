"""重算全库论文的主题标签、关键词标签与相关度（改 config.yaml 的 topics 后使用）。

场景：你在 config.yaml 里新增/修改/删除了 topic 或关键词后，已经入库的
论文不会自动重新归类。本脚本对全库论文重新跑 score_relevance，更新
topics / keywords / relevance_score / raw.matched_keywords，并把新主题
集合归集到 Zotero、新主题目录下的索引刷新到 Obsidian（可选）。

关键词标签：每篇最多 5 个。优先用命中的主题关键词；不足 5 个时可用 LLM
（--llm）从标题+摘要提炼补充。LLM 新提炼、且能归属到某个主题的新关键词，
会批量回写 config.yaml 对应主题的 keywords（全库归一化去重）。

用法：
    python retag_topics.py --dry-run    # 只看会改变哪些，不写库
    python retag_topics.py              # 实际重算并写库（只算命中关键词）
    python retag_topics.py --llm        # 重算 + LLM 提炼补齐关键词标签（慢）
    python retag_topics.py --sync       # 重算 + 同步 Zotero 归集 + 刷新日报

幂等：可重复运行。
"""

import argparse
import json

from paperpilot import config as cfg
from paperpilot import db
from paperpilot.keywords import batch_write_new_keywords, extract_keyword_tags
from paperpilot.topics import score_relevance


def main():
    ap = argparse.ArgumentParser(description="重算全库论文主题标签与关键词标签")
    ap.add_argument("--dry-run", action="store_true", help="只显示差异，不写库")
    ap.add_argument("--sync", action="store_true", help="重算后同步 Zotero 归集")
    ap.add_argument("--llm", action="store_true",
                    help="用 LLM 提炼补充关键词标签（不足5个时）；默认关闭以省成本")
    ap.add_argument("--limit", type=int, default=0,
                    help="只处理前 N 篇（调试用，0=全部）")
    args = ap.parse_args()

    config = cfg.config()
    db.init_db()
    conn = db.get_conn()
    rows = conn.execute("SELECT id, paper_key, title, abstract, venue, "
                        "topics_json, keywords_json, relevance_score "
                        "FROM papers").fetchall()
    if args.limit > 0:
        rows = rows[:args.limit]

    changed = 0
    total = 0
    updates = []  # (paper_key, new_topics, new_keywords, new_score, old_topics, old_score)
    collected_new = []  # LLM 提炼的新词 [(kw, topic_key)]

    for r in rows:
        total += 1
        paper = {
            "title": r["title"] or "",
            "abstract": r["abstract"] or "",
            "venue": r["venue"] or "",
        }
        topics, matched, rel = score_relevance(paper, config)
        old_topics = json.loads(r["topics_json"] or "[]")
        # 关键词标签（最多 5 个）：命中关键词优先 + LLM 提炼补充（可选）
        keywords, new_kws = extract_keyword_tags(
            paper, config, matched, use_llm=args.llm, collect_new=True)
        collected_new.extend(new_kws)
        old_keywords = json.loads(r["keywords_json"] or "[]")
        if (topics != old_topics or keywords != old_keywords
                or abs(rel - (r["relevance_score"] or 0)) > 0.01):
            changed += 1
            updates.append((r["paper_key"], topics, keywords, rel,
                            old_topics, r["relevance_score"]))

    print(f"共 {total} 篇，{changed} 篇主题标签/关键词标签/相关度将发生变化。")
    if args.llm:
        print(f"LLM 提炼出新关键词 {len(collected_new)} 个（去重后回写 config）。\n")
    else:
        print()

    if args.dry_run:
        # 预览前 30 条差异
        for pk, topics, keywords, rel, old, old_rel in updates[:30]:
            print(f"  {pk}: {old} → {topics} (相关度 {old_rel} → {rel})")
            print(f"      关键词: {keywords}")
        if len(updates) > 30:
            print(f"  … 其余 {len(updates)-30} 条略")
        print("\n[dry-run] 未写库。用不带 --dry-run 再跑一次以实际更新。")
        conn.close()
        return

    # 写库
    cur = conn.cursor()
    for pk, topics, keywords, rel, old, old_rel in updates:
        cur.execute(
            "UPDATE papers SET topics_json = ?, keywords_json = ?, "
            "relevance_score = ? WHERE paper_key = ?",
            (json.dumps(topics, ensure_ascii=False),
             json.dumps(keywords, ensure_ascii=False), rel, pk),
        )
    conn.commit()
    conn.close()
    print(f"✓ 已更新 {changed} 篇论文的主题标签、关键词标签与相关度。")

    # LLM 新词批量回写 config（全库归一化去重）
    if args.llm and collected_new:
        written = batch_write_new_keywords(collected_new, config)
        print(f"✓ 已回写 {written} 个新关键词到 config.yaml。")

    # 打印各主题分布
    c2 = db.get_conn()
    from paperpilot.config import topic_labels
    labels = topic_labels()
    dist = {k: 0 for k in config["topics"]}
    for r in c2.execute("SELECT topics_json FROM papers").fetchall():
        for t in json.loads(r["topics_json"] or "[]"):
            if t in dist:
                dist[t] += 1
    c2.close()
    print("\n主题分布：")
    for k, n in dist.items():
        print(f"  {labels.get(k, k)}：{n} 篇")

    if args.sync:
        print("\n同步 Zotero 归集…")
        from paperpilot import zotero
        all_papers = db.load_papers_for_report()
        added, failed, ok, new_keys = zotero.add_via_local_api(all_papers, config)
        if ok:
            print(f"  Zotero 入库 {added} 成功 / {failed} 失败（含新主题归集）")
        else:
            print("  Zotero 本地 API 不可用（请打开 Zotero 客户端后重跑 --sync）")


if __name__ == "__main__":
    main()
