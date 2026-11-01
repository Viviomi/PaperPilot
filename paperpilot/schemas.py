"""分析结果结构校验：确保模型输出不仅结构正确，且枚举值、分数、关系标题合法。

确定性校验，不调用 LLM。与 analyzer.py 的语义分析解耦：
- 本模块检查「结构、枚举、分数范围、关系标题白名单」等硬规则；
- 语义审查（过度断言、证据链断裂）由 feedback.py 负责。
"""

ALLOWED_EVIDENCE_STATUS = {"已证实", "有依据的推断", "待验证"}

ALLOWED_CRITIQUE_CATEGORIES = {
    "author_reported",
    "evidence_gap",
    "methodological_risk",
    "contradictory_evidence",
    "deployment_risk",
    "hypothesis_to_test",
}

ALLOWED_SEVERITIES = {"high", "medium", "low"}

ALLOWED_EVIDENCE_LEVELS = {"L1", "L2", "L3"}

ALLOWED_READING_RECOMMENDATIONS = {
    "skim", "read_carefully", "deep_read", "insufficient_material", "",
}


def _allowed_titles(related):
    """从相关文献列表提取标题白名单（小写、去空白）。"""
    return { (r.get("title") or "").strip().lower()
             for r in (related or []) if (r.get("title") or "").strip() }


def validate_analysis(data, paper=None, related=None):
    """校验分析结果。返回 {"passed": bool, "issues": [str]}。

    覆盖：scores 范围、evidence_level、critique 枚举、evidence_status 三态、
    学术继承关系标题是否来自候选列表。
    """
    issues = []

    if not isinstance(data, dict):
        return {"passed": False, "issues": ["分析结果必须是 JSON 对象"]}

    # 分数 0-4 整数（允许 null）
    scores = data.get("scores")
    if not isinstance(scores, dict):
        issues.append("scores 缺失或格式错误")
        scores = {}
    for key in ("novelty_potential", "evidence_quality", "transfer_value"):
        value = scores.get(key)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value not in range(5)
        ):
            issues.append(f"scores.{key} 必须为 0-4 整数或 null")

    # evidence_level 枚举
    if data.get("evidence_level") not in ALLOWED_EVIDENCE_LEVELS:
        issues.append("evidence_level 必须为 L1/L2/L3")

    # reading_recommendation 枚举
    rr = data.get("reading_recommendation")
    if rr is not None and rr not in ALLOWED_READING_RECOMMENDATIONS:
        issues.append("reading_recommendation 取值不合法")

    # critique 数组校验
    critique = data.get("critique", [])
    if not isinstance(critique, list):
        issues.append("critique 必须是数组")
        critique = []
    for i, item in enumerate(critique):
        if not isinstance(item, dict):
            issues.append(f"critique[{i}] 必须是对象")
            continue
        if item.get("category") not in ALLOWED_CRITIQUE_CATEGORIES:
            issues.append(f"critique[{i}].category 不合法: {item.get('category')}")
        if item.get("severity") not in ALLOWED_SEVERITIES:
            issues.append(f"critique[{i}].severity 不合法: {item.get('severity')}")
        status = item.get("evidence_status")
        if status is not None and status not in ALLOWED_EVIDENCE_STATUS:
            issues.append(f"critique[{i}].evidence_status 不合法: {status}")

    # contributions 的 evidence_status 三态
    contributions = data.get("contributions", [])
    if isinstance(contributions, list):
        for i, item in enumerate(contributions):
            if isinstance(item, dict):
                st = item.get("evidence_status")
                if st is not None and st not in ALLOWED_EVIDENCE_STATUS:
                    issues.append(f"contributions[{i}].evidence_status 不合法: {st}")

    # 学术继承关系标题白名单（不得凭空生成列表外标题）
    whitelist = _allowed_titles(related)
    inheritance = data.get("inheritance", {}) or {}
    if isinstance(inheritance, dict) and whitelist:
        for rel_key in ("direct_inheritance", "method_related", "team_continuity",
                        "independent_prior", "candidate_transfer"):
            rel_items = inheritance.get(rel_key, []) or []
            if not isinstance(rel_items, list):
                issues.append(f"inheritance.{rel_key} 必须是数组")
                continue
            for i, item in enumerate(rel_items):
                if not isinstance(item, dict):
                    continue
                title = (item.get("title") or "").strip().lower()
                if title and title not in whitelist:
                    issues.append(
                        f"inheritance.{rel_key}[{i}] 的标题不在候选文献列表中: "
                        f"{item.get('title')}")

    return {"passed": not issues, "issues": issues}
