"""报告生成：Markdown 日报 / 单篇报告 / 自包含 HTML 工作台。"""

import json
import re
from datetime import datetime
from pathlib import Path

from .config import REPORTS_DIR, ROOT, topic_labels
from .ranker import compute_priority

DASHBOARD_PATH = ROOT / "paperpilot_workbench.html"


def _slugify(title):
    t = re.sub(r"[^\w\u4e00-\u9fff]+", "-", (title or "").strip())
    return (t or "paper")[:60].strip("-")


def _venue_badge(paper):
    if paper.get("venue"):
        return paper["venue"]
    return "预印本"


def _links(paper):
    out = []
    if paper.get("url"):
        out.append(f"[原文]({paper['url']})")
    if paper.get("arxiv_id"):
        out.append(f"[arXiv](https://arxiv.org/abs/{paper['arxiv_id']})")
    if paper.get("doi"):
        out.append(f"[DOI](https://doi.org/{paper['doi']})")
    if paper.get("obsidian_note_path"):
        out.append(f"[笔记]({paper['obsidian_note_path']})")
    if paper.get("zotero_item_key"):
        out.append(f"[Zotero](zotero://select/items/{paper['zotero_item_key']})")
    return " · ".join(out) or "—"


# ----------------------------------------------------------------------
# Markdown 日报
# ----------------------------------------------------------------------
def generate_daily_report(papers, repos, config, stats, run_info):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    path = REPORTS_DIR / f"daily_{date}.md"

    labels = topic_labels()
    lines = []
    lines.append(f"# PaperPilot 每日研究简报 · {date}")
    lines.append("")
    lines.append("## 概览")
    lines.append("")
    lines.append(f"- 论文总量：**{stats['total']}** 篇")
    lines.append(f"- 已完成深度分析：**{stats['analyzed']}** 篇")
    lines.append(f"- 本次采集：发现 {run_info.get('found', 0)} 篇，新增 "
                 f"{run_info.get('new', 0)} 篇，分析 {run_info.get('analyzed', 0)} 篇")
    if stats.get("by_venue"):
        lines.append("- 各会议/期刊论文数：")
        for v, n in stats["by_venue"].items():
            lines.append(f"  - {v}: {n}")
    lines.append("")

    # 分层推荐（A2 + B3 + C5）
    from .reading_plan import build_reading_plan
    plan = build_reading_plan(papers, config)
    tier_titles = {"A": "A 级 · 主推精读", "B": "B 级 · 比较阅读", "C": "C 级 · 快速浏览"}
    for tier in ("A", "B", "C"):
        items = plan.get(tier, [])
        if not items:
            continue
        lines.append(f"## {tier_titles[tier]}（{len(items)} 篇）")
        lines.append("")
        for it in items:
            lines.append(f"### {it['title']}")
            lines.append("")
            lines.append(f"- 会议/期刊：{it['venue']} · 优先级 {it['priority']}")
            lines.append(f"- 为什么读：{it['reason']}")
            lines.append(f"- 阅读任务：{it['reading_task']}")
            lines.append("")

    lines.append("## 全部候选论文")
    lines.append("")
    lines.append("| # | 标题 | 会议/期刊 | 主题 | 关键词 | 相关度 | 状态 |")
    lines.append("|---|------|----------|------|--------|--------|------|")
    for i, p in enumerate(papers, 1):
        title = (p.get("title") or "")[:60]
        if p.get("url"):
            title = f"[{title}]({p['url']})"
        topics = "、".join(labels.get(t, t) for t in (p.get("topics") or []))
        keywords = "、".join(p.get("keywords") or [])
        status = {"completed": "已分析", "pending": "待分析",
                  "failed": "失败", "skipped": "跳过"}.get(
                      p.get("analysis_status"), p.get("analysis_status"))
        lines.append(f"| {i} | {title} | {_venue_badge(p)} | {topics} | "
                     f"{keywords} | {p.get('relevance_score', 0)} | {status} |")
    lines.append("")

    if repos:
        lines.append("## 代码候选仓库（未核验）")
        lines.append("")
        lines.append("> 以下仓库为按主题搜索的候选结果，尚未核验是否与论文相关，"
                     "请确认 README / 作者 / Release 后再采用。")
        lines.append("")
        by_topic = {}
        for r in repos:
            by_topic.setdefault(r["topic"], []).append(r)
        for topic, items in by_topic.items():
            lines.append(f"### {labels.get(topic, topic)}")
            lines.append("")
            for r in items:
                lines.append(f"- [{r['full_name']}]({r['url']}) "
                             f"⭐ {r['stars']} · {r.get('language') or '—'} · "
                             f"{r.get('match_type', '')}")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ----------------------------------------------------------------------
# 单篇深度分析报告
# ----------------------------------------------------------------------
def generate_paper_report(paper, config):
    analysis = paper.get("analysis") or {}
    if not analysis:
        return None
    pd = REPORTS_DIR / "papers"
    pd.mkdir(parents=True, exist_ok=True)
    path = pd / f"{paper['id']}_{_slugify(paper['title'])}.md"

    lines = [f"# {paper['title']}", ""]
    lines.append(f"- 会议/期刊：{_venue_badge(paper)}")
    lines.append(f"- 作者：{', '.join(paper.get('authors') or []) or '—'}")
    lines.append(f"- 链接：{_links(paper)}")
    lines.append("")

    if analysis.get("problem"):
        lines += ["## 研究问题", "", analysis["problem"], ""]

    if analysis.get("contributions"):
        lines += ["## 创新点", ""]
        for i, c in enumerate(analysis["contributions"], 1):
            lines.append(f"### {i}. {c.get('claim', '')}")
            lines.append(f"- 为什么是创新：{c.get('why_novel', '')}")
            lines.append(f"- 解决什么问题：{c.get('problem_solved', '')}")
            lines.append(f"- 创新类型：{c.get('novelty_type', '')}")
            lines.append(f"- 证据状态：{c.get('evidence_status', '')}")
            if c.get("evidence_quote"):
                lines.append(f"- 原文证据：> {c['evidence_quote']}")
            lines.append(f"- 可靠性：{c.get('reliability', '')}")
            lines.append(f"- 可迁移性：{c.get('transferable', '')}")
            lines.append("")

    if analysis.get("closest_prior_work"):
        lines += ["## 相关论文与继承", ""]
        for i, r in enumerate(analysis["closest_prior_work"], 1):
            lines.append(f"### {i}. {r.get('title', '')}")
            lines.append(f"- 关系类型：{r.get('relation_type', '')}")
            lines.append(f"- 继承思想：{r.get('inherited_ideas', '')}")
            lines.append(f"- 派生原因：{r.get('derivation_reason', '')}")
            lines.append(f"- 好处：{r.get('benefit', '')}")
            lines.append("")

    mig = analysis.get("migration") or {}
    if mig:
        lines += ["## 技术迁移", ""]
        if mig.get("target_scenarios"):
            lines += ["### 可复用场景", ""]
            lines += [f"- {s}" for s in mig["target_scenarios"]]
            lines.append("")
        if mig.get("limitations"):
            lines += ["### 技术缺陷", ""]
            lines += [f"- {s}" for s in mig["limitations"]]
            lines.append("")
        if mig.get("improvement_directions"):
            lines += ["### 改进方向", ""]
            lines += [f"- {s}" for s in mig["improvement_directions"]]
            lines.append("")

    scores = analysis.get("scores") or {}
    if scores:
        lines += ["## 评分（0-4）", ""]
        lines.append(f"- 创新潜力：{scores.get('novelty_potential', '—')}")
        lines.append(f"- 证据可信度：{scores.get('evidence_quality', '—')}")
        lines.append(f"- 迁移价值：{scores.get('transfer_value', '—')}")
        lines.append("")

    if analysis.get("uncertainties"):
        lines += ["## 待验证事项", ""]
        lines += [f"- {s}" for s in analysis["uncertainties"]]
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ----------------------------------------------------------------------
# 自包含 HTML 工作台
# ----------------------------------------------------------------------
_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PaperPilot · 顶会顶刊论文工作台</title>
<style>
  :root {
    --bg: #f5f6f8; --card: #ffffff; --border: #e5e7eb;
    --text: #1f2430; --muted: #6b7280; --accent: #3b5bdb; --accent-soft: #eef2ff;
    --red: #d64545; --green: #2f9e44; --amber: #e08a00;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
         background: var(--bg); color: var(--text); }
  header { background: var(--card); border-bottom: 1px solid var(--border);
           padding: 16px 28px; display: flex; align-items: center; gap: 16px;
           flex-wrap: wrap; position: sticky; top: 0; z-index: 5; }
  header h1 { font-size: 20px; margin: 0; }
  header .sub { color: var(--muted); font-size: 13px; }
  .stats { display: flex; gap: 12px; margin-left: auto; flex-wrap: wrap; }
  .chip { background: var(--accent-soft); color: var(--accent); border-radius: 999px;
          padding: 4px 12px; font-size: 13px; font-weight: 600; }
  .chip.muted { background: #eef0f3; color: var(--muted); }
  .wrap { max-width: 1180px; margin: 24px auto; padding: 0 20px; }
  .controls { background: var(--card); border: 1px solid var(--border); border-radius: 12px;
              padding: 16px; margin-bottom: 18px; }
  .controls .row { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
  input[type=text], select { padding: 8px 12px; border: 1px solid var(--border);
          border-radius: 8px; font-size: 14px; background: #fff; color: var(--text); }
  input[type=text] { flex: 1; min-width: 220px; }
  .tags { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
  .tag { border: 1px solid var(--border); border-radius: 999px; padding: 5px 12px;
         cursor: pointer; font-size: 13px; user-select: none; background: #fff; }
  .tag.on { background: var(--accent); color: #fff; border-color: var(--accent); }
  .paper { background: var(--card); border: 1px solid var(--border); border-radius: 12px;
           padding: 16px 18px; margin-bottom: 12px; }
  .paper .top { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
  .paper .title { font-size: 16px; font-weight: 650; color: var(--accent);
                  text-decoration: none; }
  .paper .title:hover { text-decoration: underline; }
  .badge { font-size: 12px; border-radius: 6px; padding: 2px 8px; font-weight: 600; }
  .badge.venue { background: #fff4e6; color: var(--amber); }
  .badge.journal { background: #e6fcf5; color: #0ca678; }
  .badge.preprint { background: #f1f3f5; color: var(--muted); }
  .badge.topic { background: var(--accent-soft); color: var(--accent); }
  .badge.kw { background: #f3f0ff; color: #7048e8; }
  .badge.reviewed { background: #d3f9d8; color: #2b8a3e; }
  .score { font-size: 13px; color: var(--muted); }
  .meta { color: var(--muted); font-size: 13px; margin-top: 6px; }
  .abstract { color: #374151; font-size: 14px; margin-top: 8px; line-height: 1.55; }
  .links { margin-top: 8px; font-size: 13px; }
  .links a { color: var(--accent); text-decoration: none; margin-right: 12px; }
  .analysis { margin-top: 12px; border-top: 1px dashed var(--border); padding-top: 10px; }
  .analysis h4 { margin: 10px 0 4px; font-size: 14px; }
  .analysis .item { border-left: 3px solid var(--accent-soft); padding-left: 10px;
                    margin: 8px 0; }
  .analysis .item b { color: var(--text); }
  .detail { display: none; }
  .paper.open .detail { display: block; }
  .expand { color: var(--accent); cursor: pointer; font-size: 13px; }
  .repo { background: var(--card); border: 1px solid var(--border); border-radius: 12px;
          padding: 12px 16px; margin-bottom: 8px; }
  .repo a { color: var(--accent); font-weight: 600; text-decoration: none; }
  .empty { text-align: center; color: var(--muted); padding: 40px; }
  footer { text-align: center; color: var(--muted); font-size: 12px; padding: 24px; }
  /* ---- 操作面板（本地控制服务） ---- */
  .panel { background: var(--card); border: 1px solid var(--border); border-radius: 12px;
           padding: 16px; margin-bottom: 18px; }
  .panel .ph { font-size: 14px; font-weight: 650; margin: 0 0 10px; display: flex;
               align-items: center; gap: 8px; }
  .panel .ph .dot { width: 8px; height: 8px; border-radius: 50%; background: #ccc;
                    display: inline-block; }
  .panel .ph .dot.ok { background: var(--green); }
  .panel .ph .dot.bad { background: var(--red); }
  .panel .hint { font-size: 12px; color: var(--muted); margin: 0 0 12px; }
  .opbtns { display: flex; gap: 8px; flex-wrap: wrap; }
  .opbtn { border: 1px solid var(--border); background: #fff; color: var(--text);
           border-radius: 8px; padding: 8px 14px; font-size: 13px; cursor: pointer;
           transition: all .15s; }
  .opbtn:hover { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); }
  .opbtn.run { background: var(--accent); color: #fff; border-color: var(--accent); }
  .opbtn.danger { color: var(--red); }
  .opbtn.danger:hover { border-color: var(--red); background: #fdeaea; }
  .opbtn:disabled { opacity: .5; cursor: not-allowed; }
  .opbtn .sp { display: inline-block; width: 12px; height: 12px; border: 2px solid #fff;
               border-top-color: transparent; border-radius: 50%; animation: spin .7s linear infinite;
               vertical-align: -2px; margin-right: 6px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  /* ---- 每篇论文的精读按钮 ---- */
  .readbtn { display: inline-flex; align-items: center; gap: 5px; margin-left: auto;
             border: 1px solid var(--border); background: #fff; color: var(--accent);
             border-radius: 8px; padding: 4px 12px; font-size: 12px; cursor: pointer;
             white-space: nowrap; transition: all .15s; }
  .readbtn:hover { background: var(--accent); color: #fff; border-color: var(--accent); }
  .readbtn.done { color: var(--green); border-color: #bfe3c8; background: #f0faf2; }
  .readbtn.done:hover { background: var(--green); color: #fff; border-color: var(--green); }
  .readbtn.running { opacity: .6; cursor: wait; }
  .readbtn .sp { display: inline-block; width: 10px; height: 10px; border: 2px solid currentColor;
                 border-top-color: transparent; border-radius: 50%; animation: spin .7s linear infinite; }
  .logwrap { margin-top: 12px; display: none; }
  .logwrap.on { display: block; }
  .loghead { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
  .loghead .lt { font-size: 13px; font-weight: 600; }
  .loghead .lclose { margin-left: auto; cursor: pointer; color: var(--muted); font-size: 13px; }
  .log { background: #0f1720; color: #d7e0ea; border-radius: 8px; padding: 12px 14px;
         font-family: "SF Mono", Consolas, "Courier New", monospace; font-size: 12px;
         line-height: 1.6; max-height: 320px; overflow-y: auto; white-space: pre-wrap;
         word-break: break-all; }
  .opmodal { position: fixed; inset: 0; background: rgba(0,0,0,.35); display: none;
             align-items: center; justify-content: center; z-index: 20; }
  .opmodal.on { display: flex; }
  .opmodal .box { background: #fff; border-radius: 12px; padding: 20px 22px; width: 360px;
                  max-width: 90vw; box-shadow: 0 10px 40px rgba(0,0,0,.2); }
  .opmodal .box h3 { margin: 0 0 10px; font-size: 16px; }
  .opmodal .box .f { margin: 10px 0; }
  .opmodal .box label { display: block; font-size: 13px; color: var(--muted); margin-bottom: 4px; }
  .opmodal .box input[type=text] { width: 100%; }
  .opmodal .box .mf { display: flex; gap: 10px; justify-content: flex-end; margin-top: 16px; }
  .opmodal .box button { border: 1px solid var(--border); background: #fff; border-radius: 8px;
                         padding: 7px 16px; cursor: pointer; font-size: 13px; }
  .opmodal .box button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
  @media (max-width: 700px) { .stats { margin-left: 0; } }
</style>
</head>
<body>
<header>
  <h1>📚 PaperPilot 工作台</h1>
  <span class="sub">顶会顶刊论文追踪 · 创新分析 · 技术迁移</span>
  <div class="stats" id="stats"></div>
</header>
<div class="wrap">
  <div class="controls">
    <div class="row">
      <input type="text" id="q" placeholder="搜索标题 / 摘要 / 作者，例如 EEG、multi-agent、meta-learning">
      <select id="venue"><option value="all">全部会议/期刊</option></select>
      <select id="source"><option value="all">全部来源</option></select>
      <select id="analysisStatus">
        <option value="all">全部分析状态</option>
        <option value="analyzed">已分析</option>
        <option value="unanalyzed">未分析</option>
      </select>
      <select id="reviewStatus">
        <option value="all">全部点评状态</option>
        <option value="reviewed">已点评</option>
        <option value="unreviewed">未点评</option>
      </select>
      <select id="kw"><option value="all">全部关键词</option></select>
      <select id="sort">
        <option value="priority">按优先级</option>
        <option value="relevance">按相关度</option>
        <option value="date">按日期</option>
      </select>
    </div>
    <div class="tags" id="topicTags"></div>
  </div>
  <div class="panel">
    <p class="ph">⚙️ 一键操作 <span class="dot" id="srvDot"></span>
      <span style="font-size:12px;color:var(--muted);font-weight:400" id="srvText">检测服务…</span></p>
    <p class="hint" id="srvHint">这些操作在本机执行 Python 脚本，需要先启动本地控制服务。</p>
    <div class="opbtns" id="opBtns"></div>
    <div class="logwrap" id="logWrap">
      <div class="loghead"><span class="lt" id="logTitle"></span>
        <span class="lclose" onclick="closeLog()">✕ 关闭</span></div>
      <div class="log" id="logBox"></div>
    </div>
  </div>
  <div class="opmodal" id="opModal">
    <div class="box">
      <h3 id="opModalTitle">执行操作</h3>
      <div id="opModalFields"></div>
      <div class="mf">
        <button onclick="closeModal()">取消</button>
        <button class="primary" id="opModalGo">执行</button>
      </div>
    </div>
  </div>
  <div id="list"></div>
  <h2 style="margin-top:30px">🔗 代码候选仓库（未核验）</h2>
  <div id="repos"></div>
</div>
<footer>PaperPilot · 生成时间 __DATE__ · 本页数据来自本地数据库</footer>
<script>
const APP = __PAYLOAD__;
const labels = APP.labels || {};
const papers = APP.papers || [];
const repos = APP.repos || [];
let state = { q:"", topics:[], venue:"all", source:"all", analysis:"all", review:"all", kw:"all", sort:"priority" };

function esc(s){ return (s==null?"":String(s)).replace(/[&<>"']/g,
  c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

function renderStats(){
  const total = papers.length;
  const analyzed = papers.filter(p=>p.analysis_status==="completed").length;
  const reviewed = papers.filter(p=>p.has_review).length;
  const venues = Object.keys(APP.stats.by_venue||{}).length;
  document.getElementById("stats").innerHTML =
    `<span class="chip">论文 ${total}</span>`+
    `<span class="chip">已分析 ${analyzed}</span>`+
    `<span class="chip">已点评 ${reviewed}</span>`+
    `<span class="chip muted">会议/期刊 ${venues}</span>`+
    `<span class="chip muted">${APP.generated_at||""}</span>`;
}

function buildFilters(){
  const venues = [...new Set(papers.map(p=>p.venue||"预印本"))];
  const selV = document.getElementById("venue");
  venues.forEach(v=>{ const o=document.createElement("option"); o.value=v; o.textContent=v; selV.appendChild(o); });
  const sources = [...new Set(papers.map(p=>p.source))];
  const selS = document.getElementById("source");
  sources.forEach(s=>{ const o=document.createElement("option"); o.value=s; o.textContent=s; selS.appendChild(o); });
  const allTopics = [...new Set(papers.flatMap(p=>p.topics||[]))];
  const tags = document.getElementById("topicTags");
  allTopics.forEach(t=>{
    const d=document.createElement("div"); d.className="tag"; d.textContent=labels[t]||t;
    d.onclick=()=>{ d.classList.toggle("on");
      state.topics = allTopics.filter(x=>{
        const el=[...document.querySelectorAll("#topicTags .tag.on")];
        return el.map(e=>e.textContent).includes(labels[x]||x);
      });
      render(); };
    tags.appendChild(d);
  });
  // 关键词筛选下拉：按出现频次降序
  const kwCount = {};
  papers.forEach(p=>(p.keywords||[]).forEach(k=>{ kwCount[k]=(kwCount[k]||0)+1; }));
  const kws = Object.keys(kwCount).sort((a,b)=>kwCount[b]-kwCount[a]);
  const selK = document.getElementById("kw");
  kws.forEach(k=>{
    const o=document.createElement("option"); o.value=k; o.textContent=`${k}（${kwCount[k]}）`; selK.appendChild(o);
  });
}

function topicLabel(t){ return labels[t]||t; }
function venueBadge(p){
  if(p.venue){ return `<span class="badge ${p.venue_type==='journal'?'journal':'venue'}">${esc(p.venue)}</span>`; }
  return `<span class="badge preprint">预印本</span>`;
}
function links(p){
  let h="";
  if(p.url) h+=`<a href="${esc(p.url)}" target="_blank">原文</a>`;
  if(p.arxiv_id) h+=`<a href="https://arxiv.org/abs/${esc(p.arxiv_id)}" target="_blank">arXiv</a>`;
  if(p.doi) h+=`<a href="https://doi.org/${esc(p.doi)}" target="_blank">DOI</a>`;
  if(p.obsidian_note_path) h+=`<a href="obsidian://open?path=${encodeURIComponent(p.obsidian_note_path)}">笔记</a>`;
  if(p.zotero_item_key) h+=`<a href="zotero://select/items/${esc(p.zotero_item_key)}">Zotero</a>`;
  return h;
}
/* 判断一篇论文是否已精读过：有笔记路径（且视为已写）或状态为 read */
function isRead(p){ return (p.reading_status==="read") || !!p.obsidian_note_path; }
function readBtn(p){
  const done = isRead(p);
  if(done){
    return `<button class="readbtn done" data-viewnote="${esc(p.paper_key||"")}" `+
           `title="在 Obsidian 打开精读笔记">查看精读报告</button>`+
           `<button class="readbtn" data-reread="${esc(p.paper_key||"")}" `+
           `title="在现有笔记基础上继续精读，深化完善分析">继续精读</button>`;
  }
  return `<button class="readbtn" data-readbtn="${esc(p.paper_key||"")}" `+
         `title="执行单篇精读并写入 Obsidian">精读</button>`;
}
/* 打开 Obsidian 笔记（已精读） */
function openNote(p){
  if(p.obsidian_note_path){
    window.open("obsidian://open?path=" + encodeURIComponent(p.obsidian_note_path), "_self");
  } else {
    alert("该论文标记为已读，但未记录笔记路径。可点「生成工作台」刷新后重试。");
  }
}
/* 执行单篇精读（未精读）：调后端 read_paper 动作，完成后刷新工作台 */
function deepRead(p, btn, force){
  if(!SRV.ok){ alert("本地控制服务未连接，无法执行精读。请先启动服务。"); return; }
  if(running){ alert("已有操作在运行，请稍候。"); return; }
  const tip = force
    ? "对「"+(p.title||"").slice(0,60)+"」执行继续精读？\n将在现有笔记基础上深化完善，会调用大模型。"
    : "对「"+(p.title||"").slice(0,60)+"」执行深度精读？\n会调用大模型，单篇约 40-50 秒。";
  if(!confirm(tip)) return;
  btn.classList.add("running");
  btn.innerHTML = '<span class="sp"></span>' + (force ? "继续精读中" : "精读中");
  running = true;
  showLog("单篇精读 · " + (p.title||"").slice(0,40));
  appendLog("正在精读：" + p.title + (force ? "（继续精读）" : "") + "\n");
  fetch(SRV.base + "/api/run", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({action:"read_paper", args:{key: p.citation_key || p.paper_key || String(p.id), force: !!force}})
  }).then(resp=>{
    if(!resp.ok || !resp.body) throw new Error("HTTP "+resp.status);
    const reader = resp.body.getReader();
    const dec = new TextDecoder("utf-8");
    let buf = "";
    function pump(){
      return reader.read().then(({done, value})=>{
        if(done){ onReadDone(btn, true); return; }
        buf += dec.decode(value, {stream:true});
        let idx;
        while((idx = buf.indexOf("\n")) >= 0){
          const line = buf.slice(0, idx); buf = buf.slice(idx+1);
          try{ const o=JSON.parse(line); if(o.__done__){ /* 等 done */ } }
          catch(e){ appendLog(line + "\n"); }
        }
        return pump();
      });
    }
    return pump();
  }).catch(e=>{
    appendLog("[错误] " + e.message + "\n");
    onReadDone(btn, false);
  });
}
function onReadDone(btn, ok){
  running = false;
  btn.classList.remove("running");
  appendLog("\n" + (ok?"[完成] 精读已写入 Obsidian":"[未完成] 请查看上方日志") + "\n");
  document.getElementById("logTitle").textContent = "精读完成";
  // 精读后工作台数据是静态内嵌的，需重新生成才能反映状态；
  // 这里自动触发 build_workbench 动作，完成后刷新页面。
  if(ok){
    appendLog("正在重新生成工作台并刷新…\n");
    fetch(SRV.base + "/api/run", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({action:"build_workbench", args:{}})
    }).then(resp=>{
      if(!resp.ok || !resp.body) throw new Error("HTTP "+resp.status);
      const reader = resp.body.getReader();
      const dec = new TextDecoder("utf-8");
      let buf = "";
      function pump(){
        return reader.read().then(({done, value})=>{
          if(done){ window.location.reload(); return; }
          buf += dec.decode(value, {stream:true});
          let idx;
          while((idx = buf.indexOf("\n")) >= 0){
            const line = buf.slice(0, idx); buf = buf.slice(idx+1);
            try{ const o=JSON.parse(line); if(o.__done__){ /* 等流结束 */ } }
            catch(e){ appendLog(line + "\n"); }
          }
          return pump();
        });
      }
      return pump();
    }).catch(e=>{
      appendLog("[刷新失败] " + e.message + "\n请手动刷新页面或点「生成工作台」按钮。\n");
    });
  }
}
function analysisHTML(a){
  if(!a) return "";
  let h="";
  if(a.problem) h+=`<h4>研究问题</h4><div>${esc(a.problem)}</div>`;
  if(a.contributions && a.contributions.length){
    h+=`<h4>创新点</h4>`;
    a.contributions.forEach(c=>{
      h+=`<div class="item"><b>${esc(c.claim||"")}</b><br>`+
         `为什么是创新：${esc(c.why_novel||"")}<br>`+
         `解决什么问题：${esc(c.problem_solved||"")}<br>`+
         `证据状态：${esc(c.evidence_status||"")} · 可靠性：${esc(c.reliability||"")}<br>`+
         `可迁移性：${esc(c.transferable||"")}</div>`;
    });
  }
  if(a.method && Object.keys(a.method).length){
    const md=a.method;
    h+=`<h4>方法机制</h4><div class="item">`;
    if(md.inputs) h+=`输入：${esc(md.inputs)}<br>`;
    if(md.outputs) h+=`输出：${esc(md.outputs)}<br>`;
    if(md.key_steps) h+=`关键步骤：${esc(md.key_steps.join("；"))}<br>`;
    if(md.training) h+=`训练：${esc(md.training)}<br>`;
    if(md.inference) h+=`推理：${esc(md.inference)}`;
    h+=`</div>`;
  }
  const inh=a.inheritance||{};
  const inhLabels={direct_inheritance:"直接继承",method_related:"方法相关",team_continuity:"团队延续",independent_prior:"独立先例",candidate_transfer:"候选迁移"};
  let inhAny=false;
  Object.keys(inhLabels).forEach(k=>{
    const arr=inh[k]||[];
    if(!arr.length) return;
    inhAny=true;
    h+=`<h4>${inhLabels[k]}</h4>`;
    arr.forEach(r=>{ h+=`<div class="item"><b>${esc(r.title||"")}</b><br>${Object.entries(r).filter(([f,v])=>f!=="title"&&v).map(([f,v])=>esc(f)+"："+esc(v)).join("<br>")}</div>`; });
  });
  if(inh.unresolved){ inhAny=true; h+=`<h4>尚未确认的继承关系</h4><div class="item">${esc(inh.unresolved)}</div>`; }
  if(inhAny) h+=``;
  if(a.critique && a.critique.length){
    h+=`<h4>批判性评估</h4>`;
    a.critique.forEach(c=>{
      h+=`<div class="item"><b>${esc(c.dimension||"")}</b>（${esc(c.category||"")} · ${esc(c.severity||"")}）<br>`+
         `观察：${esc(c.observation||"")}`+
         (c.verify_method?`<br>验证：${esc(c.verify_method)}`:"")+`</div>`;
    });
  }
  const m=a.migration||{};
  if(m.target_scenarios && m.target_scenarios.length) h+=`<h4>可复用场景</h4><div>${esc(m.target_scenarios.join("；"))}</div>`;
  if(m.improvements && m.improvements.length){
    h+=`<h4>改进方向</h4>`;
    m.improvements.forEach(im=>{
      h+=`<div class="item"><b>${esc(im.proposed||"")}</b>`+
         (im.related_work?`<br>近期相关工作：${esc(im.related_work)}`:"")+
         (im.experiment?`<br>实验：${esc(im.experiment)}`:"")+
         (im.risk?`<br>风险：${esc(im.risk)}`:"")+`</div>`;
    });
  }
  if(m.limitations && m.limitations.length) h+=`<h4>技术缺陷</h4><div>${esc(m.limitations.join("；"))}</div>`;
  const s=a.scores||{};
  if(s.novelty_potential!=null) h+=`<h4>评分</h4><div>创新 ${s.novelty_potential}/4 · 证据 ${s.evidence_quality}/4 · 迁移 ${s.transfer_value}/4 · 等级 ${esc(a.evidence_level||"L1")}</div>`;
  return h;
}

function render(){
  const q = state.q.toLowerCase();
  const filtered = papers.filter(p=>{
    if(state.topics.length && !(p.topics||[]).some(t=>state.topics.includes(t))) return false;
    if(state.venue!=="all" && (p.venue||"预印本")!==state.venue) return false;
    if(state.source!=="all" && p.source!==state.source) return false;
    if(state.analysis==="analyzed" && p.analysis_status!=="completed") return false;
    if(state.analysis==="unanalyzed" && p.analysis_status==="completed") return false;
    if(state.review==="reviewed" && !p.has_review) return false;
    if(state.review==="unreviewed" && p.has_review) return false;
    if(state.kw!=="all" && !(p.keywords||[]).includes(state.kw)) return false;
    if(q){
      const hay = ((p.title||"")+" "+(p.abstract||"")+" "+(p.authors||[]).join(" ")).toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  });
  const sortKey = state.sort==="date" ? "published_at" : (state.sort==="relevance"?"relevance_score":"priority");
  filtered.sort((a,b)=>{ const x=a[sortKey]||0, y=b[sortKey]||0; return x<y?1:x>y?-1:0; });

  const list = document.getElementById("list");
  if(!filtered.length){ list.innerHTML=`<div class="empty">没有匹配的论文</div>`; return; }
  list.innerHTML = filtered.map(p=>{
    const topics = (p.topics||[]).map(t=>`<span class="badge topic">${topicLabel(t)}</span>`).join(" ");
    const kws = (p.keywords||[]).map(k=>`<span class="badge kw">${esc(k)}</span>`).join(" ");
    const anal = analysisHTML(p.analysis);
    return `<div class="paper" id="p${p.id}">
      <div class="top">
        <a class="title" href="${esc(p.url||"#")}" target="_blank">${esc(p.title)}</a>
        ${venueBadge(p)}
        ${p.has_review?`<span class="badge reviewed">已点评</span>`:""}
        <span class="score">相关度 ${p.relevance_score||0} · 优先级 ${p.priority||0}</span>
        ${readBtn(p)}
      </div>
      <div class="meta">${topics} &nbsp; ${esc((p.authors||[]).slice(0,5).join(", "))}${(p.authors||[]).length>5?" 等":""} &nbsp; · &nbsp; ${esc(p.published_at||"")} &nbsp; · &nbsp; 来源 ${esc(p.source)}</div>
      ${kws?`<div class="meta">关键词 ${kws}</div>`:""}
      <div class="links">${links(p)}</div>
      <div class="abstract">${esc((p.abstract||"").slice(0,260))}${(p.abstract||"").length>260?"…":""}</div>
      ${anal?`<div class="expand" onclick="document.getElementById('p${p.id}').classList.toggle('open')">▸ 展开深度分析</div>
      <div class="detail"><div class="analysis">${anal}</div></div>`:""}
    </div>`;
  }).join("");

  const rlist = document.getElementById("repos");
  if(!repos.length){ rlist.innerHTML=`<div class="empty">暂无代码候选仓库</div>`; return; }
  const byTopic = {};
  repos.forEach(r=>{ (byTopic[r.topic]=byTopic[r.topic]||[]).push(r); });
  rlist.innerHTML = Object.keys(byTopic).map(t=>{
    return `<h3 style="margin:18px 0 8px">${esc(labels[t]||t)}</h3>`+
      byTopic[t].map(r=>`<div class="repo">
        <a href="${esc(r.url)}" target="_blank">${esc(r.full_name)}</a>
        ⭐ ${r.stars} · ${esc(r.language||"—")} · ${esc(r.license||"无许可")}
        <div class="meta">${esc(r.description||"")}</div>
      </div>`).join("");
  }).join("");
}

document.getElementById("q").addEventListener("input", e=>{ state.q=e.target.value; render(); });
document.getElementById("venue").addEventListener("change", e=>{ state.venue=e.target.value; render(); });
document.getElementById("source").addEventListener("change", e=>{ state.source=e.target.value; render(); });
document.getElementById("analysisStatus").addEventListener("change", e=>{ state.analysis=e.target.value; render(); });
document.getElementById("reviewStatus").addEventListener("change", e=>{ state.review=e.target.value; render(); });
document.getElementById("kw").addEventListener("change", e=>{ state.kw=e.target.value; render(); });
document.getElementById("sort").addEventListener("change", e=>{ state.sort=e.target.value; render(); });

/* 精读按钮事件委托（卡片是 innerHTML 动态渲染的） */
document.getElementById("list").addEventListener("click", e=>{
  const viewBtn = e.target.closest("[data-viewnote]");
  if(viewBtn){
    const p = papers.find(x=>x.paper_key===viewBtn.getAttribute("data-viewnote"));
    if(p) openNote(p);
    return;
  }
  const rereadBtn = e.target.closest("[data-reread]");
  if(rereadBtn){
    const p = papers.find(x=>x.paper_key===rereadBtn.getAttribute("data-reread"));
    if(p) deepRead(p, rereadBtn, true);
    return;
  }
  const btn = e.target.closest("[data-readbtn]");
  if(!btn) return;
  const key = btn.getAttribute("data-readbtn");
  const p = papers.find(x=>x.paper_key===key);
  if(!p) return;
  deepRead(p, btn, false);
});

/* ================= 一键操作面板 ================= */
const SRV = { base: "", ok: false, actions: [] };
// 通过 http 访问时后端同源；file:// 打开时提示启动服务
(function detectServer(){
  const proto = location.protocol;
  if(proto === "http:" || proto === "https:"){
    SRV.base = "";
    ping();
  } else {
    setServer(false, "file:// 模式无法执行本地脚本，请启动控制服务后通过浏览器访问");
  }
})();

function setServer(ok, msg){
  SRV.ok = ok;
  const dot = document.getElementById("srvDot");
  const txt = document.getElementById("srvText");
  const hint = document.getElementById("srvHint");
  dot.className = "dot " + (ok ? "ok" : "bad");
  txt.textContent = msg || (ok ? "服务已连接" : "服务未连接");
  if(!ok){
    hint.innerHTML = "启动方式：在项目目录运行 <code>python paperpilot_server.py</code>，"
      + "然后浏览器访问 <code>http://127.0.0.1:8899/</code>（本页按钮需要服务在线）。";
  } else {
    hint.textContent = "点击按钮在本机执行对应脚本，日志实时显示在下方。";
  }
}

function ping(){
  fetch(SRV.base + "/api/health", {cache:"no-store"})
    .then(r=>r.ok?r.json():Promise.reject())
    .then(()=>{ setServer(true); loadActions(); })
    .catch(()=>setServer(false));
}

function loadActions(){
  fetch(SRV.base + "/api/actions", {cache:"no-store"})
    .then(r=>r.json())
    .then(d=>{
      SRV.actions = d.actions || [];
      renderButtons();
    })
    .catch(()=>setServer(false));
}

function renderButtons(){
  const box = document.getElementById("opBtns");
  box.innerHTML = "";
  SRV.actions.forEach(a=>{
    const b = document.createElement("button");
    b.className = "opbtn" + (a.confirm ? " danger" : "");
    b.textContent = a.label;
    b.title = a.desc;
    b.onclick = ()=>openModal(a);
    box.appendChild(b);
  });
}

/* ---- 参数弹窗 ---- */
let curAction = null;
function openModal(a){
  curAction = a;
  document.getElementById("opModalTitle").textContent = a.label;
  const f = document.getElementById("opModalFields");
  f.innerHTML = "";
  if(a.desc){ const d=document.createElement("div"); d.className="hint"; d.textContent=a.desc; f.appendChild(d); }
  const vals = {};
  (a.params||[]).forEach(p=>{
    const w = document.createElement("div"); w.className="f";
    const l = document.createElement("label"); l.textContent = p.label; w.appendChild(l);
    if(p.type === "bool"){
      const c = document.createElement("input"); c.type="checkbox"; c.id="pf_"+p.key;
      const sp = document.createElement("span"); sp.style.marginLeft="6px"; sp.textContent=p.label;
      w.appendChild(c); w.appendChild(sp);
      c.onchange = ()=>vals[p.key]=c.checked;
    } else {
      const inp = document.createElement("input"); inp.type="text"; inp.id="pf_"+p.key;
      if(p.placeholder) inp.placeholder=p.placeholder;
      w.appendChild(inp);
      inp.oninput = ()=>vals[p.key]=inp.value;
    }
    f.appendChild(w);
  });
  if(!(a.params||[]).length && a.confirm){
    const w = document.createElement("div"); w.className="hint"; w.textContent=a.confirm; f.appendChild(w);
  }
  document.getElementById("opModal").classList.add("on");
  document.getElementById("opModalGo").onclick = ()=>{ closeModal(); runAction(a, vals); };
}
function closeModal(){ document.getElementById("opModal").classList.remove("on"); }

/* ---- 执行 + 流式日志 ---- */
let running = false;
function runAction(a, args){
  if(running) return;
  if(a.confirm && !confirm(a.confirm)) return;
  running = true;
  setAllButtons(true, a.key);
  showLog(a.label + " · 运行中…");
  appendLog("正在执行：" + a.label + "\n");
  fetch(SRV.base + "/api/run", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({action: a.key, args: args||{}})
  }).then(resp=>{
    if(!resp.ok || !resp.body) throw new Error("HTTP " + resp.status);
    const reader = resp.body.getReader();
    const dec = new TextDecoder("utf-8");
    let buf = "";
    function pump(){
      return reader.read().then(({done, value})=>{
        if(done){ finishRun(); return; }
        buf += dec.decode(value, {stream:true});
        let idx;
        while((idx = buf.indexOf("\n")) >= 0){
          const line = buf.slice(0, idx); buf = buf.slice(idx+1);
          try{
            const obj = JSON.parse(line);
            if(obj.__done__){ finishRun(obj.code); }
          }catch(e){ appendLog(line + "\n"); }
        }
        return pump();
      });
    }
    return pump();
  }).catch(e=>{
    appendLog("[错误] " + e.message + "\n");
    finishRun(-1);
  });
}

function setAllButtons(disabled, activeKey){
  document.querySelectorAll("#opBtns .opbtn").forEach(b=>{
    b.disabled = disabled;
    if(disabled && b.textContent === (activeKey?findLabel(activeKey):"")){
      b.classList.add("run");
      b.innerHTML = '<span class="sp"></span>' + b.textContent;
    } else {
      b.classList.remove("run");
    }
  });
}
function findLabel(key){ const a=SRV.actions.find(x=>x.key===key); return a?a.label:""; }
function showLog(t){ document.getElementById("logWrap").classList.add("on");
  document.getElementById("logTitle").textContent = t;
  document.getElementById("logBox").textContent = ""; }
function appendLog(s){ const b=document.getElementById("logBox"); b.textContent += s; b.scrollTop = b.scrollHeight; }
function closeLog(){ if(running) return; document.getElementById("logWrap").classList.remove("on"); }
function finishRun(code){
  running = false;
  setAllButtons(false);
  appendLog("\n" + (code===0 ? "[完成] 执行成功" : "[完成] 退出码 " + code) + "\n");
  document.getElementById("logTitle").textContent = "已完成";
  // 操作可能改变了数据库，提示刷新工作台
  if(code===0){
    const t = document.getElementById("logTitle");
    t.textContent = "已完成 · 建议点「生成工作台」刷新数据";
  }
}

buildFilters();
renderStats();
render();
</script>
</body>
</html>
"""


def generate_dashboard(papers, repos, config, stats):
    labels = topic_labels()
    weights = config["ranking"]["weights"]
    from .feedback import has_review_content
    display = []
    for p in papers:
        note_path = p.get("obsidian_note_path") or ""
        has_review = bool(note_path) and has_review_content(note_path)
        d = {
            "id": p["id"],
            "paper_key": p.get("paper_key"),
            "title": p["title"],
            "abstract": p.get("abstract") or "",
            "authors": p.get("authors") or [],
            "venue": p.get("venue"),
            "venue_type": p.get("venue_type"),
            "published_at": p.get("published_at"),
            "year": p.get("year"),
            "doi": p.get("doi"),
            "arxiv_id": p.get("arxiv_id"),
            "url": p.get("url"),
            "source": p.get("source"),
            "topics": p.get("topics") or [],
            "keywords": p.get("keywords") or [],
            "github": p.get("github") or [],
            "relevance_score": p.get("relevance_score") or 0,
            "priority": compute_priority(p, weights),
            "analysis_status": p.get("analysis_status"),
            "analysis": p.get("analysis"),
            "citation_key": p.get("citation_key"),
            "zotero_item_key": p.get("zotero_item_key"),
            "obsidian_note_path": p.get("obsidian_note_path"),
            "reading_status": p.get("reading_status"),
            "has_review": has_review,
        }
        display.append(d)

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "stats": stats,
        "labels": labels,
        "papers": display,
        "repos": repos,
    }
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = (_HTML_TEMPLATE
            .replace("__PAYLOAD__", data_json)
            .replace("__DATE__", datetime.now().strftime("%Y-%m-%d")))
    DASHBOARD_PATH.write_text(html, encoding="utf-8")
    return DASHBOARD_PATH
