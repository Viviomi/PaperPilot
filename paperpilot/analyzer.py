"""LLM 深度分析引擎（v3）。

对照 Keshav《How to Read a Paper》的三遍阅读法，对每篇重点论文输出可审计的
结构化档案。核心改进（相对 v2）：
- 三遍阅读 + Five Cs：先全景（Category/Context/Correctness/Contributions/Clarity），
  再方法与证据，最后虚拟重实现与研究设计；
- 严格证据边界：区分「已证实 / 有依据的推断 / 待验证」，明确「材料不足」
  与「论文本身有缺陷」；
- 分析分级：analysis_depth（skim/standard/deep）与 evidence_level（L1/L2/L3）
  正交分离，证据等级由程序计算而非模型自报；
- 结构校验：输出经 validate_analysis 校验，quality_status 区分任务状态与质量状态；
- 更强的 JSON 解析容错。

使用 OpenAI 兼容接口；未配置 LLM_API_KEY 时返回 analysis_unavailable。
"""

import json
import re

from .analysis_level import classify_analysis
from .config import env, topic_labels

PROMPT_VERSION = "v3"

# 结论状态三态（全文统一，避免「有证据/待验证」等混用）
EVIDENCE_STATUS = ("已证实", "有依据的推断", "待验证")

SYSTEM_PROMPT = """你是一名严谨的计算机科学研究分析员，同时具备文献综述、实验设计、算法审查和科研复现能力。研究领域包括多智能体协同、脑机接口、小样本学习、迁移学习和智能体设计。

你的任务不是复述摘要，而是根据输入材料生成一份可审计、可验证、可用于研究决策的结构化论文档案。

你必须遵循 S. Keshav《How to Read a Paper》的核心思想：
- 第一遍：建立论文全景，回答 Five Cs（Category / Context / Correctness / Contributions / Clarity）。
- 第二遍：分析方法与证据，检查实验是否支持主张。
- 第三遍：尝试虚拟重实现，挑战假设，提出未来研究方向。
- 文献综述：从相关文献中识别研究脉络、共同引用、研究团队和潜在缺失的先例。

重要限制：你只能看到本次输入提供的论文元数据、摘要以及相关文献元数据。除非输入明确提供全文、方法章节、图表、实验结果、参考文献正文或代码，否则不得声称自己已经阅读、检查或复现了这些内容。应明确区分「论文材料不足」和「论文方法本身存在缺陷」。

## 一、证据与结论状态

每一条实质性判断都必须区分以下状态：
- "已证实"：由输入中直接可见的明确证据支持。这里仅指已由当前材料证实，不代表经过独立实验验证。
- "有依据的推断"：有明确事实作为依据，但推理仍存在其他可能解释。
- "待验证"：当前材料不足，必须通过全文、引用原文、代码、实验或外部核验才能判断。

不得将作者的主张直接等同于客观事实，也不得将摘要中的性能声明等同于独立验证结果。

证据优先级：
1. 输入中提供的原文片段、实验数据或明确方法描述；
2. 摘要和论文元数据；
3. 相关文献元数据及其关系提示；
4. 基于以上证据作出的有限推断。

相关文献标题、年份、作者或 venue 只能证明对应元数据被提供，不能单独证明两篇论文采用相同机制、存在直接继承关系或构成首次提出。

若输入没有提供页码或章节定位，不得编造页码、章节号或原文引句。无法准确引用原文时，使用忠实的转述，并明确它是转述而非直接引文。

## 二、第一遍：论文全景与 Five Cs

首先回答：
1. Category：论文属于哪种类型？（新算法 / 新架构 / 理论分析 / 测量研究 / 系统原型 / 经验研究 / 综述 / 应用组合）不能仅凭标题判定；证据不足时标记待验证。
2. Context：论文位于什么研究背景？识别研究问题、研究脉络、理论基础和输入中可见的相关工作，区分已知背景与仍需检索的背景。
3. Correctness：现有材料是否足以支持作者的假设和结论？只有摘要时，不得断言证明正确、统计显著或实验充分。
4. Contributions：作者声称的主要贡献是什么？「作者声称新颖」不等于「已证实首次提出」。
5. Clarity：仅评估实际提供的文本。摘要清晰不代表全文清晰；没有全文时不得评价全文组织、图表质量或复现说明质量。

同时给出：paper_type、research_problem、main_claim、reading_recommendation（skim / read_carefully / deep_read / insufficient_material）、reading_recommendation_reason、missing_materials。reading_recommendation 是阅读优先级建议，不是论文质量或录用概率判断。

## 三、第二遍：方法机制与证据审查

### 3.1 方法机制
尽可能重建：输入、输出、优化目标；核心模块、关键步骤和模块间信息流；训练阶段、推理阶段及其区别；关键假设、依赖条件和适用边界；论文未披露或当前材料未提供的实现细节。

每个步骤都应区分：source_supported（材料直接支持）/ inferred（合理推断）/ undisclosed（未说明）。不要根据领域惯例擅自补全模型结构、损失函数、超参数、训练数据、工具调用策略或推理算法。如果只有摘要，输出「摘要级方法重建」并明确哪些步骤不能确认。

### 3.2 证据与实验质量
从基线公平性、数据集与泄漏风险、指标匹配、统计显著性、消融、泛化、鲁棒性、计算成本与复现性等维度审查，但只对有材料支持的维度作确定判断。未提供的信息描述为「证据缺口」，不能直接认定作者没做相应实验。

### 3.3 虚拟重实现
尝试构建可复现的算法草图：前置假设、输入与预处理、逐步计算、关键状态/变量/模块、输出与评估方式、尚不能确定的实现选择、能区分本文与竞争方法的关键实验。必须区分「根据材料重建的步骤」与「为了复现而提出的假设」。不得声称已经运行代码、复现结果或验证性能，除非输入确实提供了相应执行证据。

## 四、学术继承：五类严格分离

所有关系只能基于给定的相关文献列表。不得生成列表之外的具体论文标题，不得把检索候选当作已确认关系。
- direct_inheritance 直接继承：必须有直接引用证据 + 方法被明确采用/扩展/改造的证据。仅有参考文献关系最多说明存在引用，不能自动证明方法继承。
- method_related 方法相关：有材料支持机制、目标或技术路线相似，但直接继承未确认。只有标题时通常只能记为待核验候选。
- team_continuity 团队延续：作者重合或团队轨迹。作者重合只能支持团队关联，不能自动证明方法连续性。
- independent_prior 独立先例：其他团队在相近问题上的相似思路。必须说明证据是否足以证明时间先后、团队独立性和实质相似，否则标记为候选先例。
- candidate_transfer 候选迁移：可迁移机制、目标领域、依赖假设和潜在障碍。明确区分原论文已验证用途与本次迁移假设。

每项关系记录：title、relation_status（已证实/有依据的推断/待验证）、evidence、mechanism_or_relation、difference、verification_needed。

inheritance.unresolved 必须说明：本次输入实际包含哪些检索类别、找到哪些候选关系、当前证据为何不足、下一步需检查什么。不得虚构检索过的数据库、查询式或检索范围。

## 五、批判性评估：六类缺陷

每条批判包含：dimension、observation、evidence、evidence_status、category、severity、impact、verify_method。

category 只能是：author_reported / evidence_gap / methodological_risk / contradictory_evidence / deployment_risk / hypothesis_to_test。severity 只能是 high / medium / low。

不要为了显得深入而强行生成缺陷。材料不足时优先输出证据缺口或待验证假设，不得把缺少全文误判为论文本身有缺陷。contradictory_evidence 必须有实际的相反证据。每项批判都应给出能够证伪或确认该判断的验证方法。

## 六、迁移与改进：完整研究链条

每条改进方向按顺序组织：现有问题 → 问题证据 → 给定列表中的相关工作 → 方法差异 → 拟议改进 → 预期收益 → 实验设计 → 风险与失败条件 → 新颖性待验证项。
- related_work 只能引用输入列表中真实提供的论文；没有合适的就明确标记「当前候选列表不足」。
- 说明改进是修补、组合、扩展还是新研究假设。
- 预期收益写成待验证的预测，不得写成已获结果。
- 实验尽量包含基线、消融、指标、数据划分、统计分析和失败案例。
- 指出哪些实验结果会否定该改进方向。

## 七、评分规则

所有评分为 0-4 的整数：0=材料完全没提供足够证据；1=仅有初步迹象；2=有一定证据但存在重要缺口；3=证据较充分；4=多方面强证据。评分对象是「当前材料支持程度」，不是作者能力、论文声望或模型主观信心。
- novelty_potential：当前证据对潜在新颖性的支持程度，不等同于已证明原创性。
- evidence_quality：当前材料的证据质量和可审计程度。
- transfer_value：迁移机制的清晰度、可行性及其证据支持程度。

不得因为摘要写得有说服力就给高分。材料不足时允许低分并在 uncertainties 说明原因。evidence_level 表示证据材料层级（L1=主要依赖元数据摘要；L2=含较充分方法/实验/相关工作证据；L3=含全文级核验或可检查的复现证据），不代表论文质量等级。

## 八、输出约束

只输出一个合法 JSON 对象。不得使用 Markdown 代码围栏或 JSON 以外的解释。不得输出 NaN、注释或尾随逗号。缺少信息时使用空数组、空字符串或 null，并在相应 unresolved、undisclosed 或 uncertainties 字段解释原因。不要为了填满字段而重复同一条结论。严格遵守用户消息提供的 JSON 结构；若 schema 字段名与本指令概念名不同，使用 schema 字段名，并把状态与证据放入最合适的已有字段。"""


# 不同分析深度的阶段指令（叠加到 SYSTEM_PROMPT 之后）
DEPTH_INSTRUCTIONS = {
    "skim": """\n\n## 本次分析阶段：第一遍阅读（skim）
优先回答 Five Cs：Category、Context、Correctness、Contributions、Clarity。
输出核心问题、主要贡献、初步证据判断、阅读优先级和材料缺口。
不要强行生成大量批判或迁移建议；critique 与 migration.improvements 可留空或仅列最关键的 1-2 条。""",
    "standard": """\n\n## 本次分析阶段：第一遍 + 第二遍阅读（standard）
在 Five Cs 基础上分析方法机制、作者主张与证据的对应关系、实验设计的可见优缺点及学术关联。
没有全文时，将相关判断标为「待验证」，不要臆造实验细节。""",
    "deep": """\n\n## 本次分析阶段：三遍阅读（deep）
在全景和方法分析基础上，尝试虚拟重实现，逐项审查假设、方法选择和实验设计，
识别隐含假设与可证伪的研究问题。明确区分材料支持的步骤与复现假设。
没有全文或复现证据时，不得声称完成了复现。""",
}


def _build_user_prompt(paper, related, plan, prior_notes=""):
    labels = topic_labels()
    topics = [labels.get(t, t) for t in (paper.get("topics") or [])]
    related_list = []
    for r in related or []:
        related_list.append({
            "title": r.get("title"),
            "year": r.get("year"),
            "venue": r.get("venue") or "",
            "authors": r.get("authors") or [],
            "doi": r.get("doi") or "",
            "openalex_id": r.get("openalex_id") or "",
            "relation_hint": r.get("relation_hint") or "",
            "abstract": r.get("abstract") or "",
            "cited_by_count": r.get("cited_by_count"),
        })
    task_text = ("基于给定材料执行三遍阅读式分析。先判断材料层级，再分析贡献、"
                 "机制、证据、学术关系和可验证的改进方向。")
    if prior_notes:
        task_text = ("这是一次「继续精读」：下方 prior_user_notes 是用户此前的手写"
                     "精读笔记与判断。请在其基础上深化——呼应、修正、补充或挑战这些"
                     "判断，而不是从零复述摘要。" + task_text)
    payload = {
        "task": task_text,
        "source_policy": {
            "allowed_sources": [
                "当前论文的输入元数据",
                "当前论文的输入摘要",
                "给定的相关文献列表",
            ],
            "do_not_invent": [
                "论文标题、作者、引用、实验数据、页码和方法细节",
                "未提供的论文全文内容",
                "未经核验的直接继承关系",
                "未实际执行的复现结果",
            ],
            "missing_information": ("必须标记为待验证或证据缺口，"
                                    "不能把材料缺失描述为论文已被证明存在缺陷。"),
        },
        "analysis_plan": plan.to_dict(),
        "current_paper": {
            "title": paper.get("title"),
            "authors": paper.get("authors") or [],
            "venue": paper.get("venue") or "",
            "published_at": paper.get("published_at"),
            "doi": paper.get("doi") or "",
            "topics": topics,
            "abstract": paper.get("abstract") or "",
        },
        "related_papers": related_list,
        "analysis_requirements": [
            "第一遍：回答 Five Cs，给出阅读优先级与理由。",
            "第二遍：拆解方法机制，评估主张与证据是否匹配。",
            "第三遍：构建虚拟重实现步骤，区分原文事实与实现假设。",
            "学术继承：分别分析五类关系，证据不足时不得强行归类。",
            "批判性评估：使用六类 category，并给出可执行的验证方法。",
            "改进方向：建立问题、证据、相关工作、方法差异和实验的完整链条。",
        ],
        "输出JSON结构": {
            "problem": "本文解决的核心问题",
            "paper_type": "论文类型（新算法/新架构/理论分析/测量研究/系统原型/经验研究/综述/应用组合）",
            "five_cs": {
                "category": "论文类型", "context": "研究背景",
                "correctness": "假设与结论在当前材料下的支撑程度",
                "contributions": "主要贡献", "clarity": "材料清晰度",
            },
            "reading_recommendation": "skim/read_carefully/deep_read/insufficient_material",
            "reading_recommendation_reason": "阅读优先级理由",
            "missing_materials": ["当前缺少、需全文/代码才能判断的材料"],
            "contributions": [{
                "claim": "创新点", "why_novel": "为什么是创新",
                "problem_solved": "解决什么问题",
                "novelty_type": "新问题/新算法/新架构/新理论/新应用组合",
                "evidence_quote": "摘要原文证据", "evidence_status": "已证实/有依据的推断/待验证",
                "reliability": "可靠性评估", "transferable": "可迁移性评估",
            }],
            "method": {
                "inputs": "输入", "outputs": "输出",
                "key_steps": ["关键步骤（区分 source_supported/inferred/undisclosed）"],
                "training": "训练阶段做什么", "inference": "推理阶段做什么",
                "virtual_reimplementation": ["虚拟重实现步骤（区分材料重建 vs 复现假设）"],
                "undisclosed": ["摘要未披露的实现细节"],
            },
            "inheritance": {
                "direct_inheritance": [{"title": "", "relation_status": "已证实/有依据的推断/待验证", "inherited_what": "继承了哪些机制", "evidence": "证据", "mechanism_diff": "相对原方法的变化", "verification_needed": "还需核验什么"}],
                "method_related": [{"title": "", "relation_status": "", "similarity": "相似之处", "difference": "关键差异", "verification_needed": ""}],
                "team_continuity": [{"title": "", "relation_status": "", "continuation_of": "延续了哪些工作", "verification_needed": ""}],
                "independent_prior": [{"title": "", "relation_status": "", "prior_idea": "先例思路", "difference": "与本文差异", "verification_needed": ""}],
                "candidate_transfer": [{"title": "", "mechanism": "可迁移机制", "target_domain": "目标领域", "verification_needed": ""}],
                "unresolved": "若无法确认直接继承，说明检索范围、候选关联、缺失证据；否则留空",
            },
            "critique": [{
                "dimension": "问题定义/新颖性/方法合理性/实验设计/统计可靠性/泛化能力/复现性/实用性",
                "observation": "观察", "evidence": "证据或注明推断",
                "evidence_status": "已证实/有依据的推断/待验证",
                "category": "author_reported/evidence_gap/methodological_risk/contradictory_evidence/deployment_risk/hypothesis_to_test",
                "severity": "high/medium/low", "impact": "影响",
                "verify_method": "如何验证",
            }],
            "migration": {
                "target_scenarios": ["可复用场景"],
                "improvements": [{
                    "problem": "当前局限", "evidence": "证据",
                    "related_work": "近期相关工作(来自给定列表)", "method_diff": "方法差异",
                    "proposed": "拟改进", "expected_gain": "预期收益",
                    "experiment": "实验设计", "risk": "风险", "novelty_uncertain": "新颖性待验证项",
                }],
                "limitations": ["技术缺陷"],
            },
            "scores": {"novelty_potential": 0, "evidence_quality": 0, "transfer_value": 0},
            "uncertainties": ["待验证/不确定事项"],
        },
    }
    if prior_notes:
        payload["prior_user_notes"] = prior_notes
    return json.dumps(payload, ensure_ascii=False)


def _parse_json(text):
    """尽量健壮地解析模型输出为 JSON 对象。

    依次尝试：直接解析 → 去掉代码围栏 → 提取首个 {...} 块 →
    修复常见尾逗号。返回 dict 或 None。
    """
    text = (text or "").strip()
    if not text:
        return None

    # 1) 直接解析
    try:
        return json.loads(text)
    except Exception:
        pass

    # 2) 去掉 Markdown 代码围栏
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except Exception:
            pass

    # 3) 提取首个 {...} 块（用括号配对而非贪婪正则）
    start = text.find("{")
    if start != -1:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except Exception:
                        # 4) 修复尾逗号后重试
                        fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
                        try:
                            return json.loads(fixed)
                        except Exception:
                            pass
                    break
    return None


def _fallback(paper, reason, status="analysis_unavailable"):
    """分析不可用时的结果。明确不产生有效分析，避免误导前端。"""
    return {
        "status": status,
        "prompt_version": PROMPT_VERSION,
        "quality_status": "needs_review",
        "reason": reason,
        "problem": "", "contributions": [], "method": {},
        "inheritance": {}, "critique": [],
        "migration": {"target_scenarios": [], "improvements": [], "limitations": []},
        "scores": {"novelty_potential": None, "evidence_quality": None,
                   "transfer_value": None},
        "evidence_level": "L1",
        "uncertainties": ["分析不可用，仅保留关键词相关度评估。"],
        "novelty_score": None, "evidence_score": None, "transfer_score": None,
        "analysis_depth": "skim",
    }


def analyze_paper(paper, config, related=None, requested_depth="auto",
                  prior_notes=""):
    """对一篇论文执行三遍阅读式分析。返回结构化档案 dict。

    参数：
        paper: 论文 dict（含 title/authors/abstract/venue 等）。
        config: 全局配置。
        related: 相关文献列表（来自 related.retrieve_related）。
        requested_depth: auto / skim / standard / deep。

    返回字段兼容 v2（novelty_score 等），并新增 analysis_depth /
    evidence_level / quality_status。
    """
    plan = classify_analysis(paper, requested_depth=requested_depth)

    api_key = env("LLM_API_KEY", "")
    if not api_key:
        r = _fallback(paper, "LLM 未配置：请在 paperpilot/.env 的 LLM_API_KEY= 后填入密钥")
        r["analysis_depth"] = plan.analysis_depth
        r["evidence_level"] = plan.evidence_level
        r["analysis_plan"] = plan.to_dict()
        return r

    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            base_url=env("LLM_BASE_URL", "https://api.openai.com/v1"),
            timeout=180.0,
            max_retries=2,
        )
        model = env("LLM_MODEL", "gpt-4.1-mini")

        system_prompt = (SYSTEM_PROMPT
                         + DEPTH_INSTRUCTIONS[plan.analysis_depth])
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _build_user_prompt(paper, related, plan, prior_notes)},
        ]

        # 优先 JSON 模式；若不支持则回退普通模式（明确区分两类失败）
        # 显式给足输出上限：深度分析的 JSON 常达 8-10KB，用服务商默认上限会被截断，
        # 截断后的 JSON 无法修复，表现为「模型输出无法解析为 JSON」。
        max_out = int(env("LLM_MAX_TOKENS", "16384") or 16384)
        resp = None
        try:
            resp = client.chat.completions.create(
                model=model, messages=messages, temperature=0.2,
                max_tokens=max_out,
                response_format={"type": "json_object"})
        except Exception as exc:
            try:
                resp = client.chat.completions.create(
                    model=model, messages=messages, temperature=0.2,
                    max_tokens=max_out)
            except Exception:
                return _fallback(paper, f"分析请求失败: {exc}", status="failed")

        finish = getattr(resp.choices[0], "finish_reason", None)
        if finish == "length":
            print(f"  ! 输出被截断（finish_reason=length），"
                  f"可调大 .env 的 LLM_MAX_TOKENS")

        data = _parse_json(resp.choices[0].message.content)
        if data is None:
            return _fallback(paper, "模型输出无法解析为 JSON", status="failed")

        scores = data.get("scores", {}) or {}
        result = {
            "status": "completed",
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "problem": data.get("problem", ""),
            "paper_type": data.get("paper_type", ""),
            "five_cs": data.get("five_cs", {}) or {},
            "reading_recommendation": data.get("reading_recommendation", ""),
            "reading_recommendation_reason": data.get("reading_recommendation_reason", ""),
            "missing_materials": data.get("missing_materials", []) or [],
            "contributions": data.get("contributions", []) or [],
            "method": data.get("method", {}) or {},
            "inheritance": data.get("inheritance", {}) or {},
            "critique": data.get("critique", []) or [],
            "migration": data.get("migration", {}) or {},
            "scores": scores,
            "uncertainties": data.get("uncertainties", []) or [],
            "novelty_score": scores.get("novelty_potential"),
            "evidence_score": scores.get("evidence_quality"),
            "transfer_score": scores.get("transfer_value"),
            # 分级信息：证据等级由程序覆盖，不信任模型自报
            "analysis_depth": plan.analysis_depth,
            "evidence_level": plan.evidence_level,
            "analysis_plan": plan.to_dict(),
            "quality_status": "pending_validation",
        }
        return result
    except Exception as exc:  # noqa: BLE001
        r = _fallback(paper, f"分析失败: {exc}", status="failed")
        r["analysis_depth"] = plan.analysis_depth
        r["evidence_level"] = plan.evidence_level
        r["analysis_plan"] = plan.to_dict()
        return r
