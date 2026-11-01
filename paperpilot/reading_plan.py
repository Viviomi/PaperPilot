"""每日分层推荐：A 级精读 2 篇 + B 级比较 3 篇 + C 级快览 5 篇。

推荐理由与阅读任务从已有深度分析结果派生（不额外调用大模型），
让「读什么、为什么读、读完要核验什么」一目了然。
"""


def _pick_task(a):
    """从批判性评估里挑一条最值得核验的，作为阅读任务。"""
    crit = a.get("critique") or []
    for c in crit:
        if c.get("severity") == "high" and (c.get("verify_method") or c.get("observation")):
            return c.get("verify_method") or c.get("observation")
    for c in crit:
        if c.get("verify_method"):
            return c.get("verify_method")
    if crit:
        return crit[0].get("observation") or ""
    return "核验核心创新点是否相对最接近的基线有实质差异"


def _reason(p, a):
    parts = []
    if a.get("problem"):
        parts.append(f"解决：{a['problem'][:80]}")
    contribs = a.get("contributions") or []
    if contribs:
        parts.append(f"关键创新：{contribs[0].get('claim', '')[:60]}")
    if not parts and p.get("abstract"):
        parts.append((p.get("abstract") or "")[:80])
    return "；".join(parts) or "与当前研究方向相关"


def build_reading_plan(papers, config):
    """返回 {'A': [...], 'B': [...], 'C': [...]}，每项含推荐理由与阅读任务。"""
    ranked = sorted(papers, key=lambda x: (x.get("priority") or 0), reverse=True)
    plan = {"A": [], "B": [], "C": []}
    for i, p in enumerate(ranked):
        if i < 2:
            tier = "A"
        elif i < 5:
            tier = "B"
        elif i < 10:
            tier = "C"
        else:
            break
        a = p.get("analysis") or {}
        plan[tier].append({
            "title": p.get("title"),
            "citekey": p.get("citation_key"),
            "venue": p.get("venue") or "预印本",
            "priority": p.get("priority"),
            "topics": p.get("topics") or [],
            "reason": _reason(p, a),
            "reading_task": _pick_task(a),
        })
    return plan
