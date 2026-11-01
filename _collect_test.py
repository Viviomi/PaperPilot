"""轻量采集测试（约 20% 规模）：只采集 arXiv 单一数据源 + 限制数量，
跳过 LLM 深度分析与关键词提炼，验证「采集→评分→入库→生成工作台」核心链路。

供控制服务"采集·小规模测试"按钮调用，快速、零 API 成本。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from paperpilot import config as cfg, db, reports  # noqa: E402
from paperpilot.collectors import arxiv  # noqa: E402
from paperpilot.keywords import extract_keyword_tags  # noqa: E402
from paperpilot.citekey import make_citekey  # noqa: E402
from paperpilot.model import make_paper_key  # noqa: E402
from paperpilot.topics import score_relevance  # noqa: E402


def main():
    config = cfg.config()
    db.init_db()

    # 只采 arXiv，且把分类砍到 2 个、每类上限压到 15（约 20% 规模）
    arxiv_cfg = dict(config.get("sources", {}).get("arxiv", {}))
    arxiv_cfg["categories"] = (arxiv_cfg.get("categories") or [])[:2]
    arxiv_cfg["max_results_per_query"] = 15
    tmp_config = dict(config)
    tmp_config["sources"] = dict(config.get("sources", {}))
    tmp_config["sources"]["arxiv"] = arxiv_cfg

    print("[采集测试] 仅采集 arXiv，分类×2、每类≤15 篇，跳过 LLM 分析。")
    try:
        papers = arxiv.collect_arxiv(tmp_config)
    except Exception as exc:  # noqa: BLE001
        print(f"[采集测试] arXiv 采集失败: {exc}")
        papers = []
    print(f"[采集测试] 采集到 {len(papers)} 篇候选论文。")

    # 去重 + 评分 + 入库（跳过 LLM：关键词标签用 use_llm=False；旧论文不重复分析）
    seen = set()
    existing_keys = db.get_citation_key_map()
    used_keys = set(existing_keys.values())
    existing_keywords = db.get_keyword_map()
    new_count = 0
    for p in papers:
        p["paper_key"] = make_paper_key(p)
        if p["paper_key"] in seen:
            continue
        seen.add(p["paper_key"])
        topics, matched, rel = score_relevance(p, config)
        p["topics"] = topics
        p["relevance_score"] = rel
        p.setdefault("analysis_status", "pending")
        p.setdefault("reading_status", "unread")
        p["raw"]["matched_keywords"] = matched
        if p["paper_key"] not in existing_keys:
            kw_tags, _ = extract_keyword_tags(p, config, matched, use_llm=False)
            p["keywords"] = kw_tags
            citekey = make_citekey(
                p.get("authors"), p.get("year"), p.get("title"), used_keys)
            p["citation_key"] = citekey
            used_keys.add(citekey)
        else:
            p["keywords"] = existing_keywords.get(p["paper_key"], [])
            p["citation_key"] = existing_keys[p["paper_key"]]
        if db.upsert_paper(p) == "new":
            new_count += 1

    print(f"[采集测试] 新增入库 {new_count} 篇。")

    # 重新生成工作台
    all_papers = db.load_papers_for_report()
    stats = db.stats()
    dash = reports.generate_dashboard(all_papers, [], config, stats)
    print(f"[采集测试] 工作台已刷新：{dash}")
    print(f"[采集测试] 统计：{stats}")
    print("[采集测试] 完成（未做 LLM 深度分析，可点「全量采集」正式跑）。")


if __name__ == "__main__":
    main()
