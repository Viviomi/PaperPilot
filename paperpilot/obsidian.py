"""Obsidian 知识导出：文献卡 / 方法卡 / 研究卡 / 日报。

原则：
- 自动生成的内容放在 %% PAPERPILOT_AUTO %% 区块内，手写内容放在
  %% PAPERPILOT_USER %% 区块内；更新时只重写 AUTO 区块、保留手写内容。
- 幂等写入：同一天多次运行不产生重复笔记。
"""

import json
from datetime import datetime
from pathlib import Path

from .config import topic_labels
from .zotero import zotero_uri

AUTO_START = "%% PAPERPILOT_AUTO_START %%"
AUTO_END = "%% PAPERPILOT_AUTO_END %%"
USER_START = "%% PAPERPILOT_USER_START %%"
USER_END = "%% PAPERPILOT_USER_END %%"

_VAULT_BASE_DIRS = [
    "00-Inbox/PaperPilot-Daily",
    "01-Literature",
    "02-Methods",
    "04-Research-Questions",
    "05-Experiments",
    "06-Synthesis",
    "09-Ima-Exports",
    "99-Templates",
]


def _safe_dirname(label):
    """把主题 label 转成安全的目录名。"""
    for ch in '\\/:*?"<>|':
        label = label.replace(ch, "-")
    return label.strip() or "Untitled"


def ensure_vault(vault_path):
    vault = Path(vault_path)
    for d in _VAULT_BASE_DIRS:
        (vault / d).mkdir(parents=True, exist_ok=True)
    # 动态按主题建目录（03-Topics/<label>），主题来自 config.yaml
    for label in topic_labels().values():
        tdir = vault / "03-Topics" / _safe_dirname(label)
        tdir.mkdir(parents=True, exist_ok=True)
        # 每个主题目录放一个引导 README（仅首次创建，不覆盖用户后续编辑）
        readme = tdir / "_README.md"
        if not readme.exists():
            readme.write_text(_topic_readme(label), encoding="utf-8")
    return vault


def _topic_readme(label):
    """主题文件夹的引导说明（说明这个目录怎么用、子方向笔记怎么建）。"""
    return f"""# {label} · 子方向索引

本文件夹收集「{label}」方向下的**子方向笔记**——一个 idea 类一篇。

## 怎么用
1. 用 `99-Templates/子方向笔记模板.md` 新建一篇，命名如 `{label}-扩散模型高效采样.md`；
2. 在「要参考的论文」里挂 `[[citekey]]`，与 `01-Literature` 文献卡双向链接；
3. 随精读逐步沉淀：共性方案 → 方法变体 → 改进措施 → 可切入缺口。

## 子方向清单
- [[...]]

## 状态约定
active（正在跟进）→ dormant（暂缓）→ archived（已归档）
"""


def _yaml_list(items):
    return json.dumps(list(items or []), ensure_ascii=False)


def _frontmatter(paper, citekey):
    fm = {
        "type": "paper",
        "paper_id": paper.get("paper_key", ""),
        "citekey": citekey,
        "title": paper.get("title", ""),
        "authors": paper.get("authors") or [],
        "year": paper.get("year"),
        "venue": paper.get("venue") or "",
        "publication_status": paper.get("publication_status", "unknown"),
        "zotero_key": paper.get("zotero_item_key") or "",
        "doi": paper.get("doi") or "",
        "arxiv_id": paper.get("arxiv_id") or "",
        "topics": paper.get("topics") or [],
        "keywords": paper.get("keywords") or [],
        "reading_status": paper.get("reading_status") or "unread",
        "analysis_status": "ai-draft" if paper.get("analysis_status") == "completed" else "pending",
        "novelty_score": paper.get("novelty_score"),
        "evidence_confidence": paper.get("evidence_score"),
        "code_status": "candidate" if paper.get("github") else "unknown",
    }
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            lines.append(f"{k}: {_yaml_list(v)}")
        elif isinstance(v, str):
            s = v.replace('"', '\\"')
            lines.append(f'{k}: "{s}"')
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def _auto_body(paper):
    """AI 自动生成的正文（证据来自摘要/元数据，需人工核验）。"""
    a = paper.get("analysis") or {}
    labels = topic_labels()
    p = paper
    L = []
    L.append(f"# {p.get('title') or ''}")
    L.append("")
    L.append("> 本文由 PaperPilot 自动生成，尚未人工核验。重要结论请回到原文确认。")
    L.append("")

    # 1 一句话总结
    L.append("## 1. 一句话总结")
    L.append("")
    L.append((a.get("problem") or "（待深度分析）").strip() or "（待深度分析）")
    L.append("")

    # 链接
    L.append("## 元信息与链接")
    L.append("")
    L.append(f"- 会议/期刊：{p.get('venue') or '预印本'}")
    L.append(f"- 作者：{', '.join(p.get('authors') or []) or '—'}")
    if p.get("url"):
        L.append(f"- 原文：{p['url']}")
    if p.get("arxiv_id"):
        L.append(f"- arXiv：https://arxiv.org/abs/{p['arxiv_id']}")
    if p.get("doi"):
        L.append(f"- DOI：https://doi.org/{p['doi']}")
    if p.get("zotero_item_key"):
        L.append(f"- Zotero：[打开条目]({zotero_uri(item_key=p['zotero_item_key'])})")
    L.append("")

    # 主题标签
    topics = "、".join(labels.get(t, t) for t in (p.get("topics") or []))
    L.append(f"- 主题：{topics or '—'}")
    L.append("")
    # 关键词标签
    keywords = "、".join(p.get("keywords") or [])
    if keywords:
        L.append(f"- 关键词：{keywords}")
        L.append("")

    # 3 创新点
    contributions = a.get("contributions") or []
    L.append("## 3. 核心创新点")
    L.append("")
    if contributions:
        for i, c in enumerate(contributions, 1):
            L.append(f"### 创新点 {i}：{c.get('claim', '')}")
            L.append("")
            L.append(f"- 为什么是创新：{c.get('why_novel', '')}")
            L.append(f"- 解决什么问题：{c.get('problem_solved', '')}")
            L.append(f"- 创新类型：{c.get('novelty_type', '')}")
            L.append(f"- 证据状态：{c.get('evidence_status', '待验证')}")
            if c.get("evidence_quote"):
                L.append(f"- 原文证据：> {c['evidence_quote']}")
            L.append(f"- 可靠性：{c.get('reliability', '')}")
            L.append(f"- 可迁移性：{c.get('transferable', '')}")
            L.append("")
    else:
        L.append("（尚未深度分析）")
        L.append("")

    # 4 方法机制
    method = a.get("method") or {}
    L.append("## 4. 方法机制")
    L.append("")
    if method:
        if method.get("inputs"):
            L.append(f"- 输入：{method['inputs']}")
        if method.get("outputs"):
            L.append(f"- 输出：{method['outputs']}")
        if method.get("key_steps"):
            L.append("- 关键步骤：")
            L += [f"  - {s}" for s in method["key_steps"]]
        if method.get("training"):
            L.append(f"- 训练：{method['training']}")
        if method.get("inference"):
            L.append(f"- 推理：{method['inference']}")
        if method.get("undisclosed"):
            L.append("- 未披露细节：")
            L += [f"  - {s}" for s in method["undisclosed"]]
    else:
        L.append("（尚未深度分析）")
    L.append("")

    # 5 学术继承（五类分别存储）
    inh = a.get("inheritance") or {}
    L.append("## 5. 学术继承")
    L.append("")
    rel_fields = {
        "direct_inheritance": ("直接继承", ("inherited_what", "evidence", "mechanism_diff")),
        "method_related": ("方法相关", ("similarity", "difference")),
        "team_continuity": ("团队延续", ("continuation_of",)),
        "independent_prior": ("独立先例", ("prior_idea", "difference")),
        "candidate_transfer": ("候选迁移", ("mechanism", "target_domain")),
    }
    any_inh = False
    for key, (label, fields) in rel_fields.items():
        items = inh.get(key) or []
        if not items:
            continue
        any_inh = True
        L.append(f"### {label}")
        L.append("")
        for r in items:
            L.append(f"- [[{r.get('title', '')}]]")
            for f in fields:
                if r.get(f):
                    L.append(f"  - {f}：{r[f]}")
        L.append("")
    if inh.get("unresolved"):
        any_inh = True
        L.append(f"> {inh['unresolved']}")
        L.append("")
    if not any_inh:
        L.append("（尚未深度分析）")
        L.append("")

    # 6 批判性评估
    crit = a.get("critique") or []
    L.append("## 6. 批判性评估")
    L.append("")
    cat_labels = {"author_reported": "作者自述", "evidence_gap": "证据缺口",
                  "methodological_risk": "方法风险", "contradictory_evidence": "矛盾证据",
                  "deployment_risk": "部署风险", "hypothesis_to_test": "待验证假设"}
    if crit:
        for c in crit:
            cat = cat_labels.get(c.get("category", ""), c.get("category", ""))
            L.append(f"- **{c.get('dimension', '')}**（{cat} · {c.get('severity', '')}）")
            L.append(f"  - 观察：{c.get('observation', '')}")
            if c.get("evidence"):
                L.append(f"  - 依据：{c.get('evidence', '')}")
            if c.get("verify_method"):
                L.append(f"  - 验证：{c.get('verify_method', '')}")
        L.append("")
    else:
        L.append("（尚未深度分析）")
        L.append("")

    # 7 迁移与改进方向
    mig = a.get("migration") or {}
    L.append("## 7. 迁移分析与改进方向")
    L.append("")
    if mig.get("target_scenarios"):
        L.append("- 可复用场景：")
        L += [f"  - {s}" for s in mig["target_scenarios"]]
        L.append("")
    improvements = mig.get("improvements") or []
    if improvements:
        L.append("### 改进方向")
        L.append("")
        chain = (("problem", "现有问题"), ("evidence", "证据"),
                 ("related_work", "近期相关工作"), ("method_diff", "方法差异"),
                 ("expected_gain", "预期收益"), ("experiment", "实验设计"),
                 ("risk", "风险"), ("novelty_uncertain", "新颖性待验证"))
        for i, im in enumerate(improvements, 1):
            L.append(f"**{i}. {im.get('proposed', '')}**")
            for f, lab in chain:
                if im.get(f):
                    L.append(f"- {lab}：{im[f]}")
            L.append("")
    if mig.get("limitations"):
        L.append("- 技术缺陷：")
        L += [f"  - {s}" for s in mig["limitations"]]
        L.append("")
    if not mig:
        L.append("（尚未深度分析）")
        L.append("")

    # 8 局限与待验证 + 评分
    L.append("## 8. 局限与待验证事项")
    L.append("")
    scores = a.get("scores") or {}
    if scores:
        L.append(f"- 评分：创新 {scores.get('novelty_potential', '—')}/4 · "
                 f"证据 {scores.get('evidence_quality', '—')}/4 · "
                 f"迁移 {scores.get('transfer_value', '—')}/4")
    L.append(f"- 证据等级：{a.get('evidence_level', 'L1')}")
    uncertainties = a.get("uncertainties") or []
    if uncertainties:
        L += [f"- {s}" for s in uncertainties]
    else:
        L.append("- 待人工精读后补充")
    L.append("")
    return "\n".join(L)


def _user_template():
    return "\n".join([
        "## 9. 我的判断",
        "",
        "- 我真正学到的东西：",
        "- 我不同意的结论：",
        "- 与自己研究的关系：",
        "- 下一步行动：",
        "",
        "## 10. 个人笔记与精读批注",
        "",
        "（在此记录精读笔记，PaperPilot 不会覆盖本区块）",
        "",
    ])


def _split_sections(text):
    """按标记拆出 auto / user 区块内容。"""
    def extract(start, end):
        i = text.find(start)
        if i == -1:
            return None
        j = text.find(end, i)
        if j == -1:
            return text[i + len(start):]
        return text[i + len(start):j]
    auto = extract(AUTO_START, AUTO_END)
    user = extract(USER_START, USER_END)
    return auto, user


def paper_note_content(paper, citekey):
    auto = _auto_body(paper)
    return "\n".join([
        _frontmatter(paper, citekey),
        "",
        AUTO_START,
        auto.rstrip(),
        AUTO_END,
        USER_START,
        _user_template().rstrip(),
        USER_END,
    ])


def write_paper_note(paper, vault_path, citekey):
    """幂等写入文献卡。返回 (path, action)。"""
    vault = Path(vault_path)
    path = vault / "01-Literature" / f"{citekey}.md"
    new_content = paper_note_content(paper, citekey)

    if not path.exists():
        path.write_text(new_content, encoding="utf-8")
        return path, "created"

    old = path.read_text(encoding="utf-8")
    old_auto, old_user = _split_sections(old)
    new_auto, new_user = _split_sections(new_content)
    # 保留手写区块；AUTO 区块用新的
    user = old_user if old_user is not None else new_user
    merged = "\n".join([
        _frontmatter(paper, citekey),
        "",
        AUTO_START,
        (new_auto or "").rstrip(),
        AUTO_END,
        USER_START,
        (user or _user_template()).rstrip(),
        USER_END,
    ])
    if merged == old:
        return path, "skipped"
    path.write_text(merged, encoding="utf-8")
    return path, "updated"


def write_daily_briefing(papers, repos, stats, vault_path, config, run_info):
    """写每日简报到 00-Inbox/PaperPilot-Daily/。"""
    vault = Path(vault_path)
    date = datetime.now().strftime("%Y-%m-%d")
    folder = vault / "00-Inbox" / "PaperPilot-Daily"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"daily_{date}.md"

    labels = topic_labels()
    top = [p for p in papers if p.get("reading_status") == "to-read"][:10]
    if not top:
        top = papers[:10]

    L = [f"# PaperPilot 每日简报 · {date}", ""]
    L.append(f"- 新发现：{run_info.get('found', 0)} 篇 · 新增 {run_info.get('new', 0)} 篇"
             f" · 深度分析 {run_info.get('analyzed', 0)} 篇")
    L.append(f"- 累计论文：{stats.get('total', 0)} 篇 · 已完成分析 {stats.get('analyzed', 0)} 篇")
    L.append("")

    L.append("## 今日优先阅读")
    L.append("")
    for p in top:
        L.append(f"### [[{p.get('citation_key') or ''}|{p.get('title') or ''}]]")
        topics = "、".join(labels.get(t, t) for t in (p.get("topics") or []))
        L.append(f"- 主题：{topics or '—'} · 会议/期刊：{p.get('venue') or '预印本'}")
        L.append(f"- 相关度：{p.get('relevance_score', 0)} · 优先级：{p.get('priority', 0)}")
        if p.get("abstract"):
            L.append(f"- 摘要：{(p.get('abstract') or '')[:150]}…")
        L.append("")

    L.append("## 主题动态")
    L.append("")
    for t in config["topics"]:
        n = sum(1 for p in papers if t in (p.get("topics") or []))
        L.append(f"- {labels.get(t, t)}：{n} 篇相关")
    L.append("")

    L.append("## 待办")
    L.append("")
    L.append("- [ ] 精读重点论文（Zotero 高亮 + 批注）")
    L.append("- [ ] 核验相关工作与证据")
    L.append("- [ ] 复现候选方法 / 更新方法卡")
    L.append("- [ ] 更新研究假设与实验卡")
    L.append("")

    path.write_text("\n".join(L), encoding="utf-8")
    return path


def write_templates(vault_path):
    """写入方法卡/研究卡/实验卡/综合分析/IMA 导出 5 套模板到 99-Templates。"""
    vault = Path(vault_path)
    tpl = vault / "99-Templates"
    tpl.mkdir(parents=True, exist_ok=True)

    (tpl / "方法卡模板.md").write_text(METHOD_CARD_TEMPLATE, encoding="utf-8")
    (tpl / "研究卡模板.md").write_text(RESEARCH_CARD_TEMPLATE, encoding="utf-8")
    (tpl / "实验卡模板.md").write_text(EXPERIMENT_CARD_TEMPLATE, encoding="utf-8")
    (tpl / "综合分析模板.md").write_text(SYNTHESIS_TEMPLATE, encoding="utf-8")
    (tpl / "IMA导出清单模板.md").write_text(IMA_EXPORT_TEMPLATE, encoding="utf-8")
    (tpl / "子方向笔记模板.md").write_text(TOPIC_NOTE_TEMPLATE, encoding="utf-8")
    return tpl


TOPIC_NOTE_TEMPLATE = """---
type: topic-note
topic: ""                # 所属主题 key（如 generative_model / bci / few_shot）
status: active           # active / dormant / archived
source_papers: []        # 本子方向要参考的 Literature 文献 citekey
---

# 子方向名称（例如：扩散模型的高效采样）

> 本笔记是某个 topic 下的一个「类」，用于沉淀这一类 idea 的共性规律，
> 放在 `03-Topics/<主题名>/` 下，一个类一篇。

## 1. 一句话定位
这一类方法要解决什么问题、处于该 topic 的哪个分支、和相邻类的边界在哪。

## 2. 要参考的论文（Literature 文献卡）
两类来源都支持，均形成双向链接：
- Literature 文献卡：[[citekey]]，例如 [[zhang2026memorya]]
- Zotero 手动加的论文（尚无文献卡）：先跑 `python sync_zotero_back.py` 补录成卡片后改用 [[citekey]]；或临时用 [标题](zotero://select/items/KEY) 跳转

- [[citekey]] —— 为什么值得参考 / 与本类的关系
- [[citekey]] —— 为什么值得参考 / 与本类的关系

## 3. 共性方案
这一类工作反复出现的套路（问题建模 / 损失设计 / 训练技巧 / 评估方式）。
- 共性点 1：
- 共性点 2：

## 4. 方法变体谱系
不同工作从哪些维度做了变体，按维度分组，看清演进脉络。

### 变体维度 1：名称
- 代表工作 [[citekey]]：具体改了什么 / 相比基线的差异
- 代表工作 [[citekey]]：具体改了什么

### 变体维度 2：名称
- 代表工作 [[citekey]]：具体改了什么

## 5. 值得关注的改进措施
- 现状：→ 改进方向：→ 预期收益：
- 现状：→ 改进方向：→ 预期收益：

## 6. 尚未解决 / 可切入的缺口
- 缺口描述：

## 7. 与我的研究的关系
- 可借鉴点：
- 可迁移点：
- 潜在选题：

## 状态
active  # active → dormant → archived
"""

METHOD_CARD_TEMPLATE = """---
type: method
status: candidate
topics: []
source_papers: []
---

# 方法名称

## 一句话概括
这个方法解决什么问题、核心机制是什么、为什么有效。

## 机制拆解
- 输入：
- 输出：
- 关键步骤：
- 优化目标：
- 计算成本：
- 训练需求：

## 论文来源
两类来源都支持，均形成双向链接：
- Literature 文献卡：[[citekey]]，例如 [[zhang2026memorya]] —— 这篇论文如何引入 / 改进了这个方法
- Zotero 手动加的论文（尚无文献卡）：先跑 `python sync_zotero_back.py` 补录成卡片后改用 [[citekey]]；或临时用 [标题](zotero://select/items/KEY) 跳转

## 适用条件
- （在什么前提下该方法才有效）

## 失效条件
- （什么情况下会失效）

## 迁移候选
- （可迁移到哪些场景，尤其是我的研究领域）

## 可证伪的假设
（例如：在 X 条件下，该方法比基线更优）

## 验证设计
（用哪些基线、指标、消融来验证上面的假设）
"""

RESEARCH_CARD_TEMPLATE = """---
type: research-question
status: candidate
topics: []
source_papers: []
---

# 研究问题标题

## 问题描述
用一两句话说清楚要研究的问题。

## 论文来源
两类来源都支持，均形成双向链接：
- Literature 文献卡：[[citekey]]，例如 [[zhang2026memorya]]
- Zotero 手动加的论文（尚无文献卡）：先跑 `python sync_zotero_back.py` 补录成卡片后改用 [[citekey]]；或临时用 [标题](zotero://select/items/KEY) 跳转

## 已解决什么
- （这个方向已有的结论）

## 现有方法在哪些假设下失效
- （现有工作的边界与局限）

## 我的假设
- （你提出的、可检验的假设）

## 与最近相关工作的区别
- （你的想法与已有工作的本质不同）

## 最小可行实验
- （最小代价的验证方案）

## 什么结果会否定这个假设
- （证伪条件）

## 替代路径
- （如果失败，备选方案）

## 验证状态
candidate  # candidate → literature-checked → experiment-designed → testing → supported / rejected
"""

EXPERIMENT_CARD_TEMPLATE = """---
type: experiment
status: planned
topics: []
related_question: ""
source_papers: []
---

# 实验标题

## 论文来源
两类来源都支持，均形成双向链接：
- Literature 文献卡：[[citekey]]，例如 [[zhang2026memorya]]
- Zotero 手动加的论文（尚无文献卡）：先跑 `python sync_zotero_back.py` 补录成卡片后改用 [[citekey]]；或临时用 [标题](zotero://select/items/KEY) 跳转

## 要验证的假设
- 来自研究卡：[[研究卡名称]]

## 实验设计
- 数据集与划分：
- 基线方法：
- 评价指标：
- 随机种子 / 重复次数：

## 执行清单
- 环境依赖：
- 复现代码 / 参考实现：
- 计算资源：

## 结果记录
- 主要结果：
- 消融结果：
- 与基线对比：

## 结论
- 假设是否被支持：
- 意外发现 / 教训：
- 下一步：

## 状态
planned  # planned → running → done → analyzed
"""

SYNTHESIS_TEMPLATE = """---
type: synthesis
status: draft
topics: []
---

# 综合分析标题

## 主题范围与核心问题
这篇综合要回答什么问题。

## 相关论文清单
两类来源都支持，均形成双向链接：
- Literature 文献卡：[[citekey]]，例如 [[zhang2026memorya]]
- Zotero 手动加的论文（尚无文献卡）：先跑 `python sync_zotero_back.py` 补录成卡片后改用 [[citekey]]；或临时用 [标题](zotero://select/items/KEY) 跳转

- [[论文A]] —— 核心贡献 / 机制
- [[论文B]] —— 核心贡献 / 机制

## 技术路线与分歧
### 路线 1：名称
- 代表工作：[[...]]
- 核心思路：

### 路线 2：名称
- 代表工作：[[...]]
- 核心思路：

## 方法对比
| 方法 | 研究问题 | 核心机制 | 数据 | 指标 | 优势 | 失效条件 |
|------|---------|---------|------|------|------|---------|
|  |  |  |  |  |  |  |

## 尚未解决的问题
- 

## 跨领域迁移机会
- 

## 结论与下一步
- 
"""

IMA_EXPORT_TEMPLATE = """---
type: ima-export
status: draft
---

# IMA 精选导出清单

## 本目录用途
把要交给 ima 问答的精选资料放在 09-Ima-Exports/（论文卡 / 方法卡 / 研究卡）。

## 本次导出内容
- [ ] 论文卡：[[...]]
- [ ] 方法卡：[[...]]
- [ ] 研究卡：[[...]]

## 论文来源
两类来源都支持，均形成双向链接：
- Literature 文献卡：[[citekey]]，例如 [[zhang2026memorya]]
- Zotero 手动加的论文（尚无文献卡）：先跑 `python sync_zotero_back.py` 补录成卡片后改用 [[citekey]]；或临时用 [标题](zotero://select/items/KEY) 跳转

## 准备问 ima 的问题
1. 
2. 

## ima 回答要点（回流）
- 结论：
- 依据 / 原文位置：
- 待人工核验的点：

## 状态
draft  # draft → imported → reviewed
"""
