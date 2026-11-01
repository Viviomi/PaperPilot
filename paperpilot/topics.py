"""主题匹配与相关度评分。

评分规则（可解释）：
- 关键词命中：标题命中 3 分，摘要命中 1 分；
- 每个主题的原始分 = 命中分之和 * 主题权重；
- 命中顶会顶刊再加 venue_bonus 分；
- 最终 relevance_score 归一化到 0-100。
"""


def score_topics(paper, config):
    """完整评分：返回 (matched_topics, matched_keywords, relevance, topic_scores)。

    topic_scores 为 {topic_key: 该主题命中分(未归一化)}，供领域配额筛选时
    把一篇命中多主题的论文归入其「最高分主题」（每篇只占一个领域名额）。
    """
    topics = config["topics"]
    ranking = config["ranking"]
    title_w = float(ranking.get("title_match_weight", 3.0))
    abs_w = float(ranking.get("abstract_match_weight", 1.0))
    venue_bonus = float(ranking.get("venue_bonus", 3.0))

    title = (paper.get("title") or "").lower()
    abstract = (paper.get("abstract") or "").lower()

    matched_topics = []
    matched_keywords = {}
    topic_scores = {}
    raw = 0.0

    for key, t in topics.items():
        hits = []
        for kw in t.get("keywords", []):
            k = kw.lower().strip()
            if not k:
                continue
            if k in title:
                hits.append((kw, "title"))
            elif k in abstract:
                hits.append((kw, "abstract"))
        if hits:
            matched_topics.append(key)
            matched_keywords[key] = [h[0] for h in hits]
            tscore = sum(title_w if loc == "title" else abs_w for _, loc in hits)
            topic_scores[key] = tscore
            raw += float(t.get("weight", 1.0)) * tscore

    if paper.get("venue"):
        raw += venue_bonus

    relevance = round(min(100.0, raw * 5), 2)
    return matched_topics, matched_keywords, relevance, topic_scores


def score_relevance(paper, config):
    """向后兼容包装：返回 (matched_topics, matched_keywords, relevance)。"""
    topics, matched, relevance, _topic_scores = score_topics(paper, config)
    return topics, matched, relevance
