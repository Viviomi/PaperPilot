"""PaperPilot 主流程：采集 → 去重 → 评分 → 深度分析 → 报告 → 通知。

用法：
    python main.py            # 手动运行一次
    python scheduler.py       # 每天 08:00 自动运行（需本机常开）
"""

import sys
import time

from paperpilot import config as cfg
from paperpilot import db, notify, obsidian, related as related_mod, reports, schemas, zotero
from paperpilot import ima as ima_mod
from paperpilot.analyzer import analyze_paper
from paperpilot.citekey import make_citekey
from paperpilot.collectors import arxiv, crossref, github, openalex, openreview
from paperpilot.keywords import extract_keyword_tags
from paperpilot.model import make_paper_key
from paperpilot.ranker import rank_papers, select_top
from paperpilot.topics import score_topics


def _dedup(papers):
    """同一次运行内去重：按 DOI/arXiv/标题；合并补充字段。"""
    seen = {}
    for p in papers:
        p["paper_key"] = make_paper_key(p)
        key = p["paper_key"]
        if key in seen:
            old = seen[key]
            for f in ("abstract", "doi", "url", "pdf_url", "venue",
                      "publication_status"):
                if not old.get(f) and p.get(f):
                    old[f] = p[f]
            old["topics"] = list(set((old.get("topics") or []) +
                                     (p.get("topics") or [])))
            old["sources"] = list(set((old.get("sources") or []) +
                                      [old.get("source"), p.get("source")]))
            continue
        p["sources"] = [p.get("source")]
        seen[key] = p
    return list(seen.values())


def _link_repos(papers, repos):
    """把带 arXiv ID 的仓库挂到对应论文（候选未核验）。"""
    index = {p.get("arxiv_id"): p for p in papers if p.get("arxiv_id")}
    linked = 0
    for r in repos:
        aid = r.get("arxiv_id")
        if aid and aid in index:
            entry = {"name": r["full_name"], "url": r["url"],
                     "stars": r["stars"], "match_type": r["match_type"],
                     "verified": False}
            index[aid].setdefault("github", []).append(entry)
            linked += 1
    return linked


def _sync_integrations(config, all_papers, repos, stats, run_info):
    """Zotero / Obsidian / IMA 同步。三者互不影响，均优雅降级。"""
    root = cfg.ROOT

    # 本次要沉淀的重点论文（待读 + 已完成分析），只同步这一批，不全量灌入
    top_notes = [p for p in all_papers
                 if p.get("reading_status") == "to-read"
                 or p.get("analysis_status") == "completed"]

    # ---- Zotero：BibTeX 导出（全库）+ 本地 API 全库入库（增量 + 幂等归集）----
    zcfg = config.get("zotero", {})
    if zcfg.get("enabled", True):
        try:
            if zcfg.get("export_bibtex", True):
                bib = zotero.export_bibtex(
                    all_papers,
                    root / zcfg.get("bibtex_path",
                                    "data/exports/paperpilot_import.bib"))
                print(f"[Zotero] BibTeX 导入文件：{bib}")
            added, failed, ok, new_keys = zotero.add_via_local_api(all_papers, config)
            if ok:
                print(f"[Zotero] 本地 API 入库：{added} 成功 / {failed} 失败；"
                      f"已按状态分区 + 主题归集")
                for pk, zk in new_keys.items():
                    db.set_sync_fields(pk, zotero_item_key=zk)
            else:
                print("[Zotero] 本地 API 不可用：请先打开 Zotero 客户端（并确认"
                      "「设置→高级→允许其他应用通信」已勾选）；或改用手动导入 BibTeX 文件")
        except Exception as exc:  # noqa: BLE001
            print(f"[Zotero] 同步失败: {exc}")

    # ---- Obsidian：Vault + 文献卡 + 日报 + 模板 ----
    ocfg = config.get("obsidian", {})
    if ocfg.get("enabled", True):
        try:
            vault_path = ocfg.get("vault_path") or str(root / "obsidian_vault")
            vault = obsidian.ensure_vault(vault_path)
            obsidian.write_templates(vault)
            # 所有「已完成分析」+「待精读」的论文都要写文献卡（写卡幂等，不截断），
            # 否则排序靠后的已分析论文会因 export_top_notes 截断而永久缺卡。
            notes = top_notes
            written = 0
            for p in notes:
                ck = p.get("citation_key") or make_citekey(
                    p.get("authors"), p.get("year"), p.get("title"))
                path, action = obsidian.write_paper_note(p, vault, ck)
                db.log_sync("obsidian", p["paper_key"], action, str(path))
                db.set_sync_fields(p["paper_key"], obsidian_note_path=str(path))
                if action != "skipped":
                    written += 1
            obsidian.write_daily_briefing(all_papers, repos, stats, vault,
                                          config, run_info)
            print(f"[Obsidian] 文献卡 {written} 篇 + 日报 → {vault}")
        except Exception as exc:  # noqa: BLE001
            print(f"[Obsidian] 导出失败: {exc}")

    # ---- IMA：精选导出（人工导入模式）----
    icfg = config.get("ima", {})
    if icfg.get("enabled", True):
        try:
            ima_path = ima_mod.export_to_ima(
                all_papers, root / icfg.get("export_dir", "data/ima_exports"),
                config, icfg.get("export_limit", 20))
            print(f"[IMA] 精选导出：{ima_path}")
        except Exception as exc:  # noqa: BLE001
            print(f"[IMA] 导出失败: {exc}")


def collect_papers(config):
    """采集各信源论文，去重、挂仓库。返回 (papers, repos, per_venue)。"""
    papers = []
    arxiv_count = 0
    try:
        arxiv_papers = arxiv.collect_arxiv(config)
        papers += arxiv_papers
        arxiv_count = len(arxiv_papers)
    except Exception as exc:  # noqa: BLE001
        print(f"[采集] arXiv 失败: {exc}")
    try:
        papers += openalex.collect_conferences(config)
    except Exception as exc:  # noqa: BLE001
        print(f"[采集] OpenAlex 会议 失败: {exc}")
    # 仅当 arXiv 被限流/失败时才启用 OpenAlex 预印本兜底（省配额、避免无谓 429）
    try:
        if arxiv_count < 50:
            print("[采集] arXiv 结果偏少，启用 OpenAlex 预印本兜底…")
            papers += openalex.collect_preprints(config)
    except Exception as exc:  # noqa: BLE001
        print(f"[采集] OpenAlex 预印本 失败: {exc}")
    try:
        papers += crossref.collect_crossref(config)
    except Exception as exc:  # noqa: BLE001
        print(f"[采集] Crossref 失败: {exc}")
    try:
        papers += openreview.collect_openreview(config)
    except Exception as exc:  # noqa: BLE001
        print(f"[采集] OpenReview 失败: {exc}")
    repos = []
    try:
        repos = github.collect_github(config)
    except Exception as exc:  # noqa: BLE001
        print(f"[采集] GitHub 失败: {exc}")

    papers = _dedup(papers)
    _link_repos(papers, repos)

    per_venue = {}
    for p in papers:
        v = p.get("venue") or "预印本"
        per_venue[v] = per_venue.get(v, 0) + 1
    return papers, repos, per_venue


def _is_top_venue(venue, config):
    """判断 venue 是否属于配置里的顶会/顶刊（含 OpenReview 承办会议）。"""
    v = (venue or "").strip().lower()
    if not v:
        return False
    keys = set()
    venues = config.get("venues", {})
    for k in (venues.get("conferences") or {}):
        keys.add(k.lower())
    for k in (venues.get("journals") or {}):
        keys.add(k.lower())
    orv = config.get("sources", {}).get("openreview", {}).get("venues") or {}
    for k in orv:
        keys.add(k.lower())
    return v in keys


def _pick_by_topic(passed, per_topic, budget):
    """按领域配额从已达标论文中选取。budget=0 表示不限额。

    每篇归入最高分主题，每主题取相关度 Top per_topic；budget>0 时按领域
    轮转(round-robin)收敛，保证领域分布均衡、不被大领域独占。
    """
    by_topic = {}      # topic_key -> [papers]
    topic_order = []   # 保持 topic 首次出现顺序
    for p in passed:
        ts = p.get("_topic_scores") or {}
        if ts:
            best = max(ts, key=lambda k: (ts[k], -list(ts).index(k)))
        else:
            best = "__none__"
        if best not in by_topic:
            by_topic[best] = []
            topic_order.append(best)
        by_topic[best].append(p)

    groups = {t: sorted(by_topic[t],
                        key=lambda x: (x.get("relevance_score") or 0),
                        reverse=True)[:per_topic]
              for t in topic_order}

    if not budget:
        return [p for t in topic_order for p in groups[t]]

    selected = []
    idx = 0
    while len(selected) < budget:
        progressed = False
        for t in topic_order:
            if idx < len(groups[t]):
                selected.append(groups[t][idx])
                progressed = True
                if len(selected) >= budget:
                    break
        if not progressed:
            break
        idx += 1
    return selected


def _select_new_papers(candidates, min_rel, per_topic, max_new,
                       wildcard=0, config=None):
    """对已评分的新论文做入库筛选：相关度阈值 + 领域配额 + 机动名额 + 全局上限。

    返回筛选后的论文列表（稳定、可复现）。
    - 阈值：relevance_score < min_rel 的直接丢弃；
    - 机动名额：被阈值过滤掉、但属于「顶会且零关键词命中」的全新方向论文，
      优先保底 wildcard 篇，避免漏掉新方向；
    - 领域配额：达标论文每篇归入「最高分主题」，每主题取相关度 Top per_topic；
    - 全局上限：领域配额与机动名额之和受 max_new 约束（机动名额优先，剩余
      额度给领域，按领域轮转收敛保证均衡）。
    """
    # 1. 阈值过滤
    passed = [p for p in candidates
              if (p.get("relevance_score") or 0) >= min_rel]
    filtered = [p for p in candidates
                if (p.get("relevance_score") or 0) < min_rel]

    # 2. 机动名额：顶会 + 零关键词命中（全新方向）
    wildcard_picks = []
    if wildcard and config:
        wc = [p for p in filtered
              if _is_top_venue(p.get("venue"), config)
              and not (p.get("_topic_scores") or {})]
        wc.sort(key=lambda x: (x.get("title") or "").lower())  # 稳定排序
        n_wild = wildcard if not max_new else min(wildcard, max_new)
        for p in wc[:n_wild]:
            p.setdefault("raw", {})["wildcard"] = True
        wildcard_picks = wc[:n_wild]

    # 3. 领域配额（机动名额优先保底，剩余额度给领域）
    if max_new:
        budget = max(0, max_new - len(wildcard_picks))
        if budget <= 0:
            domain_picks = []
        elif not per_topic:
            passed.sort(key=lambda x: (x.get("relevance_score") or 0),
                        reverse=True)
            domain_picks = passed[:budget]
        else:
            domain_picks = _pick_by_topic(passed, per_topic, budget)
    else:
        # 无全局上限
        if not per_topic:
            passed.sort(key=lambda x: (x.get("relevance_score") or 0),
                        reverse=True)
            domain_picks = passed
        else:
            domain_picks = _pick_by_topic(passed, per_topic, 0)

    # 4. 合并：领域在前，机动在后
    return domain_picks + wildcard_picks


def ingest_papers(config, papers, use_llm=True, max_new=0):
    """评分、筛选、打关键词标签、分配稳定 citekey 并入库。

    返回 (new_count, new_papers)：新增篇数与新增论文 dict 列表（供后续
    Zotero 同步等只处理新增论文）。

    use_llm=False 时跳过 LLM 关键词提炼（仅字面命中），用于午夜采集等
    需要快速入库、不做 LLM 分析的场景。

    max_new>0 时限制本次运行最多「新增入库」的论文数（已存在的论文更新
    不受此限制）。

    筛选规则（仅作用于新论文，已在库论文不受影响）：
    1. 相关度阈值 app.ingest_min_relevance：低于即丢弃；
    2. 机动名额 app.ingest_wildcard_limit：顶会零关键词论文优先保底；
    3. 领域配额 app.ingest_per_topic_limit：每主题取相关度 Top N；
    4. 全局上限 max_new：领域轮转收敛。

    关键词分析只对「新增论文」执行；已在库的论文保留其既有 keywords 与
    citekey，不重复调用 LLM 提炼。
    """
    existing_keys = db.get_citation_key_map()
    used_keys = set(existing_keys.values())
    existing_keywords = db.get_keyword_map()

    min_rel = config["app"].get("ingest_min_relevance", 0)
    per_topic = config["app"].get("ingest_per_topic_limit", 0)
    wildcard = config["app"].get("ingest_wildcard_limit", 0)

    # 第一遍：评分，区分新/旧论文
    new_candidates = []
    existing_updates = []
    for p in papers:
        topics, matched, rel, topic_scores = score_topics(p, config)
        p["topics"] = topics
        p["relevance_score"] = rel
        p.setdefault("analysis_status", "pending")
        p.setdefault("reading_status", "unread")
        p["raw"]["matched_keywords"] = matched
        p["_topic_scores"] = topic_scores

        if p["paper_key"] in existing_keys:
            # 已入库：不重复关键词分析，保留既有 keywords 与 citekey
            p["keywords"] = existing_keywords.get(p["paper_key"], [])
            p["citation_key"] = existing_keys[p["paper_key"]]
            existing_updates.append(p)
        else:
            new_candidates.append(p)

    # 第二遍：筛选新论文（阈值 + 领域配额 + 机动名额 + 全局上限）
    selected_new = _select_new_papers(new_candidates, min_rel, per_topic,
                                      max_new, wildcard=wildcard, config=config)

    # 第三遍：入库（旧论文先更新，新论文做关键词分析后入库）
    new_count = 0
    new_papers = []
    for p in existing_updates:
        db.upsert_paper(p)
    for p in selected_new:
        kw_tags, new_kws = extract_keyword_tags(
            p, config, p["raw"].get("matched_keywords") or {}, use_llm=use_llm)
        p["keywords"] = kw_tags
        if new_kws:
            p["raw"]["new_keywords"] = new_kws
        citekey = make_citekey(
            p.get("authors"), p.get("year"), p.get("title"), used_keys)
        p["citation_key"] = citekey
        used_keys.add(citekey)
        res = db.upsert_paper(p)
        if res == "new":
            new_count += 1
            new_papers.append(p)

    return new_count, new_papers


def run_daily():
    config = cfg.config()
    db.init_db()
    run_id = db.start_run()

    papers, repos, per_venue = collect_papers(config)
    max_new = config["app"].get("daily_ingest_limit", 0)
    new_count, _ = ingest_papers(config, papers, max_new=max_new)

    print(f"\n共采集 {len(papers)} 篇候选论文，新增 {new_count} 篇。")

    # 选出需要深度分析的新论文（按相关度排序，取前 N）
    min_score = config["ranking"].get("min_relevance_score", 15)
    limit = config["app"].get("daily_analysis_limit", 10)
    # 未分析 + 上次失败(可重试) 的论文都纳入候选，避免失败后永久卡死
    candidates = [p for p in papers
                  if p["analysis_status"] in ("pending", "failed")
                  and p["relevance_score"] >= min_score]
    ranked = rank_papers(candidates, config)
    top = select_top(ranked, limit)

    # 标记重点论文为「待精读」，供 Zotero/Obsidian/工作台分层展示
    for p in top:
        p["reading_status"] = "to-read"
        db.set_sync_fields(p["paper_key"], reading_status="to-read")

    analyzed = 0
    if top:
        print(f"\n开始深度分析 {len(top)} 篇重点论文…")
        for p in top:
            pid = db.get_paper_id(p["paper_key"])
            if pid is None:
                continue
            related, scope = related_mod.retrieve_related(p, config)
            result = analyze_paper(p, config, related=related)
            # 结构校验：通过的标 validated，否则 needs_review（不阻断入库）
            if result.get("status") == "completed":
                validation = schemas.validate_analysis(result, paper=p, related=related)
                result["validation"] = validation
                result["quality_status"] = (
                    "validated" if validation["passed"] else "needs_review")
            db.mark_analyzed(pid, result)
            p["analysis_status"] = result.get("status", "completed")
            if result.get("status") == "completed":
                p["novelty_score"] = result.get("novelty_score")
                p["evidence_score"] = result.get("evidence_score")
                p["transfer_score"] = result.get("transfer_score")
                analyzed += 1
                print(f"  ✓ {p['title'][:60]}")
            else:
                print(f"  · {p['title'][:60]} [{result.get('status')}] "
                      f"{result.get('reason', '')}")
            time.sleep(1)

    # 生成报告（基于全库，含历史累积）
    all_papers = db.load_papers_for_report()
    for p in all_papers:
        if p.get("analysis_status") == "completed":
            reports.generate_paper_report(p, config)

    stats = db.stats()
    run_info = {"found": len(papers), "new": new_count, "analyzed": analyzed}
    daily_path = reports.generate_daily_report(all_papers, repos, config,
                                               stats, run_info)
    dash_path = reports.generate_dashboard(all_papers, repos, config, stats)

    # 同步到 Zotero / Obsidian / IMA
    _sync_integrations(config, all_papers, repos, stats, run_info)

    # 通知
    notify_cfg = config.get("notifications", {})
    if notify_cfg.get("email_enabled"):
        subject = f"PaperPilot 日报 {time.strftime('%Y-%m-%d')}"
        notify.send_email(subject, daily_path.read_text(encoding="utf-8")[:4000])
    if notify_cfg.get("feishu_enabled"):
        notify.send_feishu(daily_path.read_text(encoding="utf-8")[:1900])

    db.finish_run(run_id, "success", len(papers), new_count, analyzed)

    print("\n" + "=" * 60)
    print(f"日报：{daily_path}")
    print(f"工作台：{dash_path}")
    print(f"统计：{stats}")
    print("=" * 60)
    return stats


def run_collect_only():
    """午夜任务：仅采集 + 评分入库 + 同步到 Zotero，不做 LLM 深度分析。"""
    from paperpilot import daily_state

    config = cfg.config()
    db.init_db()
    run_id = db.start_run()

    papers, repos, per_venue = collect_papers(config)
    max_new = config["app"].get("daily_ingest_limit", 0)
    new_count, new_papers = ingest_papers(config, papers, use_llm=False,
                                          max_new=max_new)
    print(f"\n[午夜采集] 发现 {len(papers)} 篇候选，新增 {new_count} 篇（上限 {max_new or '不限'}）。")

    zcfg = config.get("zotero", {})
    if zcfg.get("enabled", True) and new_papers:
        try:
            if zcfg.get("export_bibtex", True):
                zotero.export_bibtex(
                    new_papers, cfg.ROOT / zcfg.get("bibtex_path",
                                                    "data/exports/paperpilot_import.bib"))
            added, failed, ok, new_keys = zotero.add_via_local_api(new_papers, config)
            if ok:
                print(f"[Zotero] 本地 API 入库：{added} 成功 / {failed} 失败")
                for pk, zk in new_keys.items():
                    db.set_sync_fields(pk, zotero_item_key=zk)
            else:
                print("[Zotero] 本地 API 不可用：请先打开 Zotero 客户端")
        except Exception as exc:  # noqa: BLE001
            print(f"[Zotero] 同步失败: {exc}")

    daily_state.update(collect={
        "found": len(papers), "new": new_count,
        "per_venue": per_venue, "time": daily_state.now_str(),
    })

    db.finish_run(run_id, "success", len(papers), new_count, 0)

    # 采集入库后重新生成工作台（论文总数/状态可能变化）
    try:
        import _build_workbench as bw
        bw.rebuild()
    except Exception as exc:  # noqa: BLE001
        print(f"[工作台] 重新生成失败：{exc}")

    print("[午夜采集] 完成。")
    return {"found": len(papers), "new": new_count, "per_venue": per_venue}


if __name__ == "__main__":
    if "--collect-only" in sys.argv:
        run_collect_only()
    else:
        run_daily()
