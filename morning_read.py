"""早间精读：从库中每个 topic 选一篇最有价值的未精读论文，精读并写入 Obsidian。

对应每日 07:00 的定时任务（Action2）。

用法：
    python morning_read.py              # 每个 topic 选一篇（默认）
    python morning_read.py --limit 3    # 最多精读 3 篇
    python morning_read.py --dry-run    # 只打印将选哪些，不真正精读
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paperpilot import config as cfg, daily_state, db
from paperpilot.config import topic_labels
from paperpilot.ranker import compute_priority


def _unread(p):
    return p.get("reading_status") != "read"


def select_papers(papers, config, limit=None):
    """按 topic 分组，每组取优先级最高的一篇未精读论文，全局去重。

    返回 list[dict]，每个元素是 paper（附 _topic 字段标记归属主题）。
    """
    weights = config["ranking"]["weights"]
    by_topic = {}
    for p in papers:
        for t in p.get("topics") or []:
            by_topic.setdefault(t, []).append(p)

    chosen = []
    seen = set()
    # 按 config.yaml 里主题定义的顺序遍历，保证每日选择稳定
    for t in config["topics"]:
        pool = [p for p in by_topic.get(t, []) if _unread(p)]
        if not pool:
            continue
        pool.sort(key=lambda p: compute_priority(p, weights), reverse=True)
        for p in pool:
            if p["paper_key"] in seen:
                continue
            p["_topic"] = t
            chosen.append(p)
            seen.add(p["paper_key"])
            break
        if limit and len(chosen) >= limit:
            break
    return chosen


def _rebuild_workbench():
    """精读后自动重新生成工作台，更新已分析/已点评等统计与列表。"""
    try:
        import _build_workbench as bw
        bw.rebuild()
    except Exception as exc:  # noqa: BLE001
        print(f"[工作台] 重新生成失败（可稍后手动点「生成工作台」）：{exc}")


def run(limit=None, dry_run=False):
    config = cfg.config()
    db.init_db()
    papers = db.load_papers_for_report()
    chosen = select_papers(papers, config, limit)

    labels = topic_labels()
    print(f"选出 {len(chosen)} 篇待精读：")
    for p in chosen:
        t = labels.get(p.get("_topic"), p.get("_topic"))
        print(f"  [{t}] {p.get('citation_key') or p.get('paper_key')} — "
              f"{(p.get('title') or '')[:60]}")

    if dry_run:
        print("[dry-run] 未执行精读。")
        return

    if not chosen:
        print("无未精读候选，跳过。")
        daily_state.update(read=[])
        _rebuild_workbench()
        return

    from read_paper import _deep_read
    from argparse import Namespace

    done = []
    for p in chosen:
        print(f"\n>>> 精读：{(p.get('title') or '')[:60]}")
        try:
            r = _deep_read(p, config, Namespace(depth="auto", force=False))
            if r.get("status") == "completed":
                done.append({
                    "citekey": p.get("citation_key") or p.get("paper_key"),
                    "title": p.get("title") or "",
                    "topic": p.get("_topic") or "",
                })
        except Exception as exc:  # noqa: BLE001
            print(f"    精读失败：{exc}")

    daily_state.update(read=done)
    print(f"\n[早间精读] 完成 {len(done)}/{len(chosen)} 篇。")
    _rebuild_workbench()


def main():
    ap = argparse.ArgumentParser(description="早间精读：每 topic 选一篇未精读论文精读")
    ap.add_argument("--limit", type=int, default=0,
                    help="每天最多精读篇数（0=每 topic 一篇）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将精读的论文，不真正执行")
    args = ap.parse_args()
    run(limit=(args.limit or None), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
