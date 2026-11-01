"""晚间研究点评：读取 Obsidian 精读笔记的「我的判断」，生成有证据的点评与下一步行动。

原则：
- 只处理有实质内容（非占位）的判断，原始笔记永不改写；
- 点评写入独立的 06-Daily-Research-Reviews/ 目录，不进入个人判断区；
- 点评围绕你的判断展开（观点质量/逻辑审查/遗漏研究/值得深入/可验证/下一步），
  不是把笔记再总结一遍。
"""

import json
import re
from datetime import datetime
from pathlib import Path

from .config import env
from .schemas import validate_analysis

REVIEW_SYSTEM = """你是一名严谨的科研同伴。用户在他的精读笔记中写下了对一篇论文的「我的判断」。
请围绕这些判断给出有证据、有建设性的点评，帮助他把阅读转化为研究增量。

输出 JSON，包含：
- opinion_quality：他的判断有哪些证据支持、哪些还只是推测；
- logic_review：是否存在从相关性直接推断因果、把作者主张当作事实等逻辑问题；
- missed_work：可能改变他判断的相关研究（只写方向，不编造具体论文标题）；
- promising_ideas：哪些观察值得发展成可检验的研究假设；
- verification：应该去核验哪段原文、哪个实验或哪篇论文；
- next_action：下一步最值得投入时间的一件具体任务。

严格规则：不得编造论文/引用/实验结果；只基于他写的内容与常识推理；不得无条件附和；
如果他的判断信息太少，明确指出还缺什么信息。只输出 JSON。"""


def _extract_section(text, keyword):
    m = re.search(rf"^##\s+\d+\.\s*{keyword}\s*$(.*?)(?=^##\s|\Z)",
                  text, re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _field_values(judgment):
    fields = {}
    for key in ("我真正学到的东西", "我不同意的结论", "与自己研究的关系", "下一步行动"):
        # 只匹配「标签：值」同行写法，避免把下一行的标签误当成本行的值
        # （原 \s* + DOTALL 会把换行吃掉，导致空占位行被误判为有内容）。
        m = re.search(rf"^[-*]\s*{re.escape(key)}\s*[:：][ \t]*(.+)$",
                      judgment, re.MULTILINE)
        if m:
            val = m.group(1).strip()
            if val:
                fields[key] = val
    return fields


def has_review_content(note_path):
    """判断一篇文献卡是否已有实质点评内容。

    只看 USER 区块（「我的判断」+「个人笔记与精读批注」），剔除模板占位行
    （空标签、提示文字）后，若仍有实质内容则视为「已点评」。
    """
    from . import obsidian
    try:
        text = Path(note_path).read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return False
    i = text.find(obsidian.USER_START)
    if i == -1:
        return False
    j = text.find(obsidian.USER_END, i)
    raw = text[i + len(obsidian.USER_START): j if j != -1 else len(text)]
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "（在此记录" in s:
            continue
        if s.startswith("- ") or s.startswith("* "):
            body = s[2:].strip()
            if re.match(r"^[^：:]+[：:]\s*$", body):  # 仅标签、冒号后无内容
                continue
        return True
    return False


def scan_notes(vault_path):
    """扫描文献卡，返回有实质「我的判断」内容的笔记列表。"""
    lit = Path(vault_path) / "01-Literature"
    if not lit.exists():
        return []
    notes = []
    for f in sorted(lit.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        judgment = _extract_section(text, "我的判断")
        fields = _field_values(judgment)
        if not fields:
            continue
        title_m = re.search(r"^#\s+(.*)$", text, re.MULTILINE)
        notes.append({
            "path": str(f),
            "citekey": f.stem,
            "title": title_m.group(1).strip() if title_m else f.stem,
            "judgment": fields,
        })
    return notes


def _review_note(note):
    api_key = env("LLM_API_KEY", "")
    if not api_key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=env("LLM_BASE_URL", "https://api.openai.com/v1"),
                        timeout=120.0, max_retries=2)
        user = json.dumps({
            "论文": note["title"],
            "我的判断": note["judgment"],
        }, ensure_ascii=False)
        resp = client.chat.completions.create(
            model=env("LLM_MODEL", "gpt-4.1-mini"),
            messages=[{"role": "system", "content": REVIEW_SYSTEM},
                      {"role": "user", "content": user}],
            temperature=0.3, response_format={"type": "json_object"})
        txt = resp.choices[0].message.content
        try:
            return json.loads(txt)
        except Exception:
            m = re.search(r"\{.*\}", txt, re.DOTALL)
            return json.loads(m.group(0)) if m else None
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _write_report(reviews, vault_path):
    out = Path(vault_path) / "06-Daily-Research-Reviews"
    out.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    path = out / f"research_review_{date}.md"

    lines = [f"# 晚间研究点评 · {date}", ""]
    if not reviews:
        lines.append("今日没有填写「我的判断」的笔记，无需点评。")
    for note, rv in reviews:
        lines.append(f"## {note['title']}")
        lines.append(f"- 引用：[[{note['citekey']}]]")
        lines.append("")
        lines.append("### 你的判断")
        for k, v in note["judgment"].items():
            lines.append(f"- **{k}**：{v}")
        lines.append("")
        lines.append("### PaperPilot 点评")
        if rv is None:
            lines.append("- （未配置 LLM，跳过点评）")
        elif "error" in rv:
            lines.append(f"- （点评失败：{rv['error']}）")
        else:
            pairs = (("opinion_quality", "观点质量"), ("logic_review", "逻辑审查"),
                     ("missed_work", "可能遗漏的研究"), ("promising_ideas", "值得深入的想法"),
                     ("verification", "可执行的核验"), ("next_action", "建议的下一步"))
            for k, lab in pairs:
                if rv.get(k):
                    lines.append(f"- **{lab}**：{rv[k]}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_evening(config):
    """晚间点评主流程：扫描→点评→写报告。返回 (处理数, 报告路径)。"""
    vault_path = config["obsidian"].get("vault_path") or "obsidian_vault"
    notes = scan_notes(vault_path)
    reviews = []
    for note in notes:
        rv = _review_note(note)
        reviews.append((note, rv))
    path = _write_report(reviews, vault_path)
    return len(notes), path


# ---------------------------------------------------------------------------
# 分析结果质量审查：对 analyze_paper 的输出做「确定性校验 + 可选 LLM 语义审查」。
# 与上面的「晚间点评」（对用户笔记的点评）职责不同，二者不混用。
# ---------------------------------------------------------------------------

REVIEW_ANALYSIS_SYSTEM = """你是一名严谨的论文分析质量审查员。下面是一份由另一个模型生成的论文分析档案，以及它可用的原始材料（论文摘要、相关文献列表）。

请审查这份分析档案，重点找出：
- overclaim：把「作者声称」写成「已证实」，或把摘要的性能声明当成独立验证结果；
- evidence_gap_confusion：把「材料缺失」误判成「论文本身有缺陷」；
- fabricated_relation：把标题相似、作者重合、引用关系直接当成方法继承；
- unverifiable_improvement：改进建议不可执行、无法回溯到证据，或引用了候选列表之外的论文。

只输出 JSON：
{"passed": true/false, "issues": [{"type": "...", "field": "...", "problem": "...", "suggestion": "..."}], "verdict": "通过/需修复/需人工复核"}

不得编造论文或证据；只基于给定材料判断。只输出 JSON。"""


def review_analysis(result, paper=None, related=None, use_llm=False):
    """对分析结果做质量审查。

    第一层：确定性校验（schemas.validate_analysis）。
    第二层（可选 use_llm=True）：LLM 语义审查（过度断言/证据链/关系编造）。
    返回 {"deterministic": {...}, "semantic": {...}|None}。
    """
    deterministic = validate_analysis(result, paper=paper, related=related)
    semantic = None
    if use_llm and result.get("status") == "completed":
        api_key = env("LLM_API_KEY", "")
        if api_key:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=api_key,
                                base_url=env("LLM_BASE_URL", "https://api.openai.com/v1"),
                                timeout=120.0, max_retries=1)
                user = json.dumps({
                    "论文标题": (paper or {}).get("title", ""),
                    "论文摘要": (paper or {}).get("abstract", ""),
                    "相关文献": related or [],
                    "分析档案": {k: result.get(k) for k in (
                        "problem", "contributions", "method", "inheritance",
                        "critique", "migration", "scores")},
                }, ensure_ascii=False)
                resp = client.chat.completions.create(
                    model=env("LLM_MODEL", "gpt-4.1-mini"),
                    messages=[{"role": "system", "content": REVIEW_ANALYSIS_SYSTEM},
                              {"role": "user", "content": user}],
                    temperature=0.2, response_format={"type": "json_object"})
                txt = resp.choices[0].message.content
                try:
                    semantic = json.loads(txt)
                except Exception:
                    m = re.search(r"\{.*\}", txt, re.DOTALL)
                    semantic = json.loads(m.group(0)) if m else None
            except Exception as exc:  # noqa: BLE001
                semantic = {"passed": None, "issues": [{"problem": str(exc)}]}
    return {"deterministic": deterministic, "semantic": semantic}
