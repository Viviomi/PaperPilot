"""IMA 交互层：导出精选资料 + 科研指令模板 + 适配器桩。

IMA（腾讯知识管家）当前无确认的公开双向自动化 API，先以「人工导入」模式接入：
把精选论文笔记/综述复制到导出目录，用户手动导入 IMA 知识库后问答。
"""

import shutil
from datetime import datetime
from pathlib import Path

from .config import topic_labels

IMA_PROMPTS = """# PaperPilot × IMA 科研指令模板

## 系统指令（可贴在 IMA 每次问答前）

你是一名严谨的计算机科学研究助手，服务于多智能体协同、脑机接口、小样本学习、
迁移学习和智能体设计等研究方向。回答任何涉及论文方法、创新、实验或技术继承的问题时：

1. 优先引用已收录的论文与结构化分析。
2. 区分作者声明、原文实验结果与你的推断。
3. 比较最接近的已有方法，指出真正的机制差异。
4. 评价实验设计、基线公平性、消融、泛化和可复现性。
5. 提出技术迁移建议时，说明必要假设、负迁移风险、基线和验证指标。
6. 无法找到原文证据时，明确说明未知或待核验。
7. 不得虚构论文、引用、实验结果、页码和代码仓库。
8. 对新研究假设，说明是否已有直接证据支持。

## 1. 精读辅助

请依据当前论文原文，按研究问题、核心假设、方法机制、数学建模、创新点、对比基线、
消融实验、泛化能力和局限性逐项分析。每个关键结论给出对应原文位置；无法从资料确认的
内容标为未知，不要推测实验结果。

## 2. 多论文对比

比较当前知识库中的相关论文，制作方法对比表，至少包括：研究问题、关键机制、训练数据、
计算成本、评价指标、优势、失效条件和迁移前提。区分原文明确说明的事实与模型推断。

## 3. 技术迁移

从当前知识库寻找可能迁移到脑机接口的多智能体、小样本学习或迁移学习方法。为每个候选
方案列出目标任务、必要假设、数据要求、基线、消融实验、潜在负迁移和能否定该假设的实验结果。

## 4. 研究选题

结合当前研究项目与已有论文，提出三个可验证的研究假设。逐一列出最接近的已有工作、尚未
解决的问题、潜在创新、最小可行实验、预期成本、失败风险，以及需要补充的证据。不要把
文献数量不足直接等同于研究空白。
"""


class IMAAdapter:
    """IMA 自动化接口桩：待官方确认提供文件写入/知识库检索 API 后再实现。"""

    def test_connection(self):
        raise NotImplementedError("IMA 官方自动化 API 尚未确认")

    def upload_document(self, file_path):
        raise NotImplementedError("IMA 官方自动化 API 尚未确认")

    def search_knowledge(self, query):
        raise NotImplementedError("IMA 官方自动化 API 尚未确认")


def export_to_ima(papers, export_dir, config, limit=20):
    """把精选论文生成一份整合 Markdown + 复制重点笔记，供 IMA 人工导入。"""
    out = Path(export_dir)
    out.mkdir(parents=True, exist_ok=True)

    labels = topic_labels()
    # 1) 精选论文整合文件（供 IMA 快速了解最新进展）
    selected = [p for p in papers
                if p.get("reading_status") in ("to-read", "analyzed")
                or p.get("analysis_status") == "completed"][:limit]
    if not selected:
        selected = papers[:limit]

    lines = [f"# PaperPilot 精选论文（{datetime.now().strftime('%Y-%m-%d')}）", ""]
    for p in selected:
        topics = "、".join(labels.get(t, t) for t in (p.get("topics") or []))
        lines.append(f"## {p.get('title') or ''}")
        lines.append(f"- 会议/期刊：{p.get('venue') or '预印本'} · 主题：{topics or '—'}")
        if p.get("url"):
            lines.append(f"- 链接：{p['url']}")
        a = p.get("analysis") or {}
        if a.get("problem"):
            lines.append(f"- 研究问题：{a['problem']}")
        contribs = a.get("contributions") or []
        for c in contribs[:2]:
            lines.append(f"- 创新点：{c.get('claim', '')}")
        if p.get("abstract"):
            lines.append(f"- 摘要：{(p.get('abstract') or '')[:200]}…")
        lines.append("")

    path = out / f"paperpilot_精选论文_{datetime.now().strftime('%Y%m%d')}.md"
    path.write_text("\n".join(lines), encoding="utf-8")

    # 2) 科研指令模板
    (out / "ima_科研指令.md").write_text(IMA_PROMPTS, encoding="utf-8")
    return path
