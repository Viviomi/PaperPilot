"""分析分级：决定「分析到什么程度」与「材料能支持多深入的判断」。

借鉴 S. Keshav《How to Read a Paper》的三遍阅读法，把两个概念分开：
- analysis_depth（分析深度）：这次应该分析到什么程度？skim / standard / deep。
- evidence_level（证据等级）：当前材料能支持多深入的判断？L1 / L2 / L3。

注意：deep 不代表材料充分；L3 也不代表论文质量高。两者是正交维度。

本模块是纯 Python 确定性逻辑，不调用 LLM、不依赖数据库，保证同一篇论文
无论从定时任务、命令行还是网页进入分析，都得到一致的分级结果。
"""

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class AnalysisPlan:
    analysis_depth: str
    evidence_level: str
    available_materials: tuple
    missing_materials: tuple
    reasons: tuple

    def to_dict(self):
        return asdict(self)


def classify_analysis(paper, requested_depth="auto"):
    """根据实际可用材料和用户意图决定分析深度。

    requested_depth: auto / skim / standard / deep。

    不能因为字段存在就认为材料真实、完整。full_text、method_text 等字段
    应由上游实际提供；当前 PaperPilot 默认只有摘要+元数据，因此通常落在
    standard/L1，这是正确的保守分级，不是缺陷。
    """
    materials = []
    missing = []
    reasons = []

    abstract = (paper.get("abstract") or "").strip()
    full_text = (paper.get("full_text") or "").strip()
    method_text = (paper.get("method_text") or "").strip()
    experiment_text = (paper.get("experiment_text") or "").strip()
    references = paper.get("references") or []
    code_url = (paper.get("code_url") or "").strip()
    reproduction = paper.get("reproduction_evidence")

    if paper.get("title"):
        materials.append("metadata")

    if abstract:
        materials.append("abstract")
    else:
        missing.append("abstract")

    if full_text:
        materials.append("full_text")

    if method_text:
        materials.append("method_text")

    if experiment_text:
        materials.append("experiment_text")

    if references:
        materials.append("references")

    if code_url:
        materials.append("code_reference")

    if reproduction:
        materials.append("reproduction_evidence")

    # 证据等级：只描述当前材料的支持能力。
    has_experiment = bool(experiment_text)
    has_method = bool(method_text)
    has_full_text = bool(full_text)

    if reproduction:
        evidence_level = "L3"
    elif has_full_text and has_method and has_experiment:
        evidence_level = "L2"
    else:
        evidence_level = "L1"

    if not has_method:
        missing.append("method_details")

    if not has_experiment:
        missing.append("experiment_details")

    if not references:
        missing.append("reference_list")

    # 用户显式指定时，尊重用户意图，但不伪造材料。
    if requested_depth in {"skim", "standard", "deep"}:
        depth = requested_depth
        reasons.append(f"用户指定分析深度：{depth}")
    elif requested_depth == "auto":
        if not abstract:
            depth = "skim"
            reasons.append("缺少摘要，先进行有限的元数据筛选")
        elif has_full_text and has_method and has_experiment:
            depth = "deep"
            reasons.append("已提供全文、方法和实验材料")
        else:
            depth = "standard"
            reasons.append("摘要可用，但全文级证据尚不完整")
    else:
        raise ValueError("requested_depth 必须为 auto/skim/standard/deep")

    if depth == "deep" and evidence_level == "L1":
        reasons.append(
            "执行深度分析请求，但材料不足；虚拟重实现只能输出待验证假设"
        )

    return AnalysisPlan(
        analysis_depth=depth,
        evidence_level=evidence_level,
        available_materials=tuple(materials),
        missing_materials=tuple(dict.fromkeys(missing)),
        reasons=tuple(reasons),
    )
