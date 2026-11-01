"""排序：综合相关度 + LLM 分析得分，选出每日重点论文。"""


def compute_priority(paper, weights):
    """0-100 综合优先级。

    未做深度分析时以相关度为准；分析后按配置权重融合。
    LLM 分数 0-4 线性映射到 0-100（乘 25）。
    """
    rel = paper.get("relevance_score", 0) or 0
    nov = paper.get("novelty_score")
    evi = paper.get("evidence_score")
    tra = paper.get("transfer_score")

    if nov is None or evi is None or tra is None:
        return round(rel, 2)

    repro = 2.0  # v1 未做代码核验，取中性分
    norm = 25.0  # 0-4 -> 0-100
    priority = (
        weights.get("relevance", 0.30) * rel +
        weights.get("novelty_potential", 0.25) * (nov * norm) +
        weights.get("evidence_quality", 0.20) * (evi * norm) +
        weights.get("transfer_value", 0.15) * (tra * norm) +
        weights.get("reproducibility", 0.10) * (repro * norm)
    )
    return round(priority, 2)


def rank_papers(papers, config):
    weights = config["ranking"]["weights"]
    for p in papers:
        p["priority"] = compute_priority(p, weights)
    return sorted(papers, key=lambda x: x["priority"], reverse=True)


def select_top(papers, limit):
    return papers[:max(0, limit)]
