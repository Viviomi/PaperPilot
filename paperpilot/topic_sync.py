"""从 Zotero 集合反向同步 topic 到 config.yaml。

方向：Zotero（用户在界面上增删改主题集合）→ config.yaml 的 topics。

用法见顶层脚本 sync_topics_from_zotero.py。核心函数：
- get_zotero_topic_collections: 读 Zotero PaperPilot 父集合下的主题子集合（label 名列表）。
- diff_topics: 对比 Zotero 集合名 vs config topics 的 label，算出新增/删除/改名。
- llm_generate_topic: 用 LLM 根据中文 label 生成英文蛇形 key + 关键词。
- apply_topic_changes: 用 ruamel.yaml 保留注释/格式地增删改 config.yaml 的 topics。
- sync_after_topics_change: 改完 config 后，重算全库标签 + 归集 Zotero 集合 + 建 Obsidian 目录。

设计约束：
- Zotero 集合名 == config topic 的 label（中文）。key 是内部蛇形标识，由程序/LLM 生成。
- 状态集合（00_Inbox/10_ToRead/20_Analyzed）与父集合 PaperPilot 不参与主题同步。
- 删除 topic 前会提示确认（由调用方处理），这里只提供 diff 结果。
"""

from __future__ import annotations

import re

from . import config as cfg
from . import zotero

# 与 zotero.py 保持一致
_PARENT_COLLECTION = "PaperPilot"
_STATUS_COLLECTION_NAMES = {"00_Inbox", "10_ToRead", "20_Analyzed"}

# 常见中文主题名 → 英文蛇形 key 的内置映射（LLM 不可用时的兜底）
_BUILTIN_KEY_MAP = {
    "生成式模型": "generative_model",
    "多智能体协同": "multi_agent",
    "脑机接口": "bci",
    "小样本学习": "few_shot",
    "迁移学习": "transfer",
    "智能体设计": "agents",
    "机器学习": "machine_learning",
    "强化学习": "reinforcement_learning",
    "自然语言处理": "nlp",
    "计算机视觉": "computer_vision",
    "大语言模型": "llm",
    "推荐系统": "recommender_system",
    "知识图谱": "knowledge_graph",
    "图神经网络": "graph_neural_network",
    "联邦学习": "federated_learning",
    "对比学习": "contrastive_learning",
    "自监督学习": "self_supervised_learning",
    "多模态学习": "multimodal_learning",
    "元学习": "meta_learning",
}


def get_zotero_topic_collections(config):
    """返回 Zotero 里 PaperPilot 父集合下的主题子集合 label 名列表。

    排除父集合本身与三个状态集合。失败返回 None。
    """
    url = config["zotero"].get("local_api_url", "http://localhost:23119")
    ok, sid = zotero.check_local_api(url)
    if not ok:
        return None
    cached = zotero._load_cached_key()
    key = cached.get("key") if cached.get("server_id") == sid else ""
    if not key:
        auth = zotero.authorize_local(url, app_name="PaperPilot-TopicSync")
        if auth:
            key = auth["key"]
            if auth.get("remember"):
                zotero._save_cached_key(auth)
        else:
            return None
    headers = {"Zotero-Server-ID": sid, "Zotero-API-Key": key}
    colls = zotero._fetch_collections(url, headers)

    # 找 PaperPilot 父集合 key
    parent_key = None
    for ck, v in colls.items():
        if v["name"] == _PARENT_COLLECTION and not v["parent"]:
            parent_key = ck
            break
    if not parent_key:
        return []

    names = []
    for ck, v in colls.items():
        if v["parent"] == parent_key and v["name"] not in _STATUS_COLLECTION_NAMES:
            names.append(v["name"])
    return names


def diff_topics(config, zotero_names):
    """对比 Zotero 集合名与 config topics 的 label，返回 (added, removed, renamed)。

    added:   [(zotero_label,)]  Zotero 有、config 无（label 集合差）。
    removed: [(config_key, label)]  config 有、Zotero 无。
    renamed: [(config_key, old_label, new_label)]  （暂按 added+removed 处理，
             因为 label 变了无法自动判断是改名还是删+增；这里留空由调用方决定）。
    """
    labels_to_key = {v.get("label", k): k for k, v in config["topics"].items()}
    zset = set(zotero_names)
    cset = set(labels_to_key.keys())

    added = sorted(zset - cset)
    removed = sorted((labels_to_key[l], l) for l in (cset - zset))
    return added, removed


def _to_snake_key(name: str) -> str:
    """中文/混合名 → 英文蛇形 key 的保守兜底（拼音不支持，仅做内置映射+清理）。"""
    if name in _BUILTIN_KEY_MAP:
        return _BUILTIN_KEY_MAP[name]
    # 兜底：把非 ASCII 去掉，保留字母数字，空格转下划线，小写
    s = re.sub(r"[^0-9A-Za-z ]+", " ", name)
    s = re.sub(r"\s+", "_", s.strip()).lower()
    if not s:
        # 纯中文且无映射：用 label 的 unicode 编码做稳定 key（不优雅但稳定）
        s = "topic_" + re.sub(r"[^0-9A-Za-z]", "", name)[:12].lower() or "topic_new"
    return s


def llm_generate_topic(label: str, config):
    """用 LLM 根据中文 label 生成 {key, keywords}。

    返回 dict；LLM 未配置或失败时返回 None（调用方用内置映射兜底）。
    """
    api_key = cfg.env("LLM_API_KEY", "")
    if not api_key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            base_url=cfg.env("LLM_BASE_URL", "https://api.openai.com/v1"),
            timeout=60.0,
            max_retries=1,
        )
        model = cfg.env("LLM_MODEL", "gpt-4.1-mini")
        system = (
            "你是科研文献主题体系的构建助手。给定一个中文研究主题名，"
            "请输出一个 JSON 对象，包含两个字段：\n"
            "1. key：英文蛇形命名（snake_case，小写+下划线，用于程序内部标识，"
            "如 generative_model、multi_agent）；\n"
            "2. keywords：8~15 个英文检索关键词（小写，覆盖该主题的常见英文表达、"
            "缩写、近义词、子方向），用于论文召回与相关度打分。\n"
            "只输出 JSON，不要任何解释。"
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"主题名：{label}"},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        import json
        data = json.loads(resp.choices[0].message.content)
        key = (data.get("key") or "").strip()
        kws = data.get("keywords") or []
        kws = [str(k).strip().lower() for k in kws if str(k).strip()]
        if not key or not kws:
            return None
        # 校验 key 合法性
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            return None
        return {"key": key, "keywords": kws}
    except Exception:
        return None


def _generate_topic_entry(label: str, config, existing_keys: set) -> dict:
    """为一个新 label 生成完整的 topic entry（key + label + weight + keywords）。"""
    key = None
    kws = None
    entry = llm_generate_topic(label, config)
    if entry:
        key = entry["key"]
        kws = entry["keywords"]
    if not key:
        key = _to_snake_key(label)
    if not kws:
        kws = [label.lower()]
    # 保证 key 唯一
    if key in existing_keys:
        base = key
        i = 2
        while f"{base}{i}" in existing_keys:
            i += 1
        key = f"{base}{i}"
    return {"key": key, "label": label, "weight": 1.0, "keywords": kws}


def _render_topic_block(key: str, label: str, keywords: list) -> str:
    """渲染一个 topic 的 YAML 文本块（缩进与项目原风格一致：2 空格 key，
    关键词用无缩进 list，即 `- ` 顶格与 label 同级）。"""
    lines = [f"  {key}:", f"    label: {label}", "    weight: 1.0", "    keywords:"]
    for kw in keywords:
        lines.append(f"    - {kw}")
    return "\n".join(lines)


def _delete_topic_block(text: str, key: str) -> str:
    """从 YAML 文本中删除指定 key 的 topic 块（含其下属所有行）。"""
    lines = text.split("\n")
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # 匹配形如 "  <key>:" 的顶层 topic key（2 空格缩进）
        if line == f"  {key}:":
            # 跳过该块：直到遇到下一个 2 空格缩进的键或 0 缩进的键
            i += 1
            while i < len(lines):
                nxt = lines[i]
                stripped = nxt.lstrip()
                if nxt and not nxt.startswith(" ") and stripped:
                    break  # 遇到 0 缩进的顶层键，块结束
                if nxt.startswith("  ") and not nxt.startswith("    ") \
                        and stripped.endswith(":") and not stripped.startswith("-"):
                    # 另一个 2 空格 topic key
                    break
                if nxt.startswith("  ") and not nxt.startswith("    ") \
                        and stripped.endswith(":") and stripped.startswith("-"):
                    break
                i += 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _delete_keyword_from_topic_block(text: str, key: str, keyword: str) -> str:
    """从指定 topic 块中删除一行 `    - <keyword>`。

    精确匹配归一化后的关键词（小写去空格）。关键词行格式固定为
    4 空格 + "- " + 关键词。返回删除后的文本（若未找到则原样返回）。
    """
    lines = text.split("\n")
    target = keyword.lower().strip()
    out = []
    i = 0
    in_block = False
    removed = False
    while i < len(lines):
        line = lines[i]
        if line == f"  {key}:":
            in_block = True
            out.append(line)
            i += 1
            continue
        if in_block:
            # 块结束判断：遇到下一个 2 空格 topic key 或 0 缩进顶层键
            stripped = line.lstrip()
            if line and not line.startswith(" ") and stripped:
                in_block = False  # 0 缩进顶层键
                out.append(line)
                i += 1
                continue
            if line.startswith("  ") and not line.startswith("    ") \
                    and stripped.endswith(":"):
                in_block = False  # 下一个 topic key
                out.append(line)
                i += 1
                continue
            # 关键词行：`    - <kw>`
            if line.startswith("    - ") and not removed:
                kw = line[len("    - "):].strip().lower()
                if kw == target:
                    removed = True
                    i += 1
                    continue  # 跳过这一行
        out.append(line)
        i += 1
    return "\n".join(out)


def dedup_keywords_between_topics(config, new_keys):
    """新 topic 的关键词若已存在于其它 topic，则从那些旧 topic 中删除。

    场景：某词原来是 topic A 下的一个 keyword，后来被"升格"成独立 topic B，
    则 B 的每个关键词若同时出现在 A（或任何其它旧 topic）里，就从那里删掉，
    避免同一词既是一个独立主题、又是另一个主题的子关键词（导致重复召回）。

    参数：
        new_keys: 新增 topic 的 key 列表（只对这些新 topic 的 keywords 做去重，
                  避免误删已有主题之间的正常共享词）。
    返回：{old_topic_key: [被删除的关键词列表]}，仅包含确实发生删除的项。
    """
    topics = config.get("topics", {})
    new_key_set = set(new_keys)
    # 归一化：keyword -> 它在哪些旧 topic 中出现
    old_kw_map = {}  # norm_kw -> list[(topic_key, 原关键词文本)]
    for tkey, t in topics.items():
        if tkey in new_key_set:
            continue  # 跳过新 topic 本身
        for kw in t.get("keywords", []):
            norm = kw.lower().strip()
            if not norm:
                continue
            old_kw_map.setdefault(norm, []).append((tkey, kw))

    removed_map = {}  # old_topic_key -> [删除的关键词原文]
    for tkey in new_key_set:
        t = topics.get(tkey, {})
        for kw in t.get("keywords", []):
            norm = kw.lower().strip()
            if norm in old_kw_map:
                for old_key, old_kw in old_kw_map[norm]:
                    removed_map.setdefault(old_key, [])
                    if old_kw not in removed_map[old_key]:
                        removed_map[old_key].append(old_kw)
    return removed_map


def _norm(s: str) -> str:
    return s.lower().strip()


def apply_topic_changes(config, added_labels, removed_keys):
    """修改磁盘上的 config.yaml：新增 added_labels 对应 topic，删除 removed_keys。

    用字符串级精确编辑（而非 ruamel），确保注释、浮点格式、其余内容
    完全不变。新增的 topic 插在 topics 块内最后一个已有 topic 之后、
    分隔注释之前。

    新增 topic 后还会做「关键词去重」：新 topic 的关键词若已作为其它
    旧 topic 的子关键词存在，则从那些旧 topic 中删除（避免一个词既是
    独立主题、又是另一个主题的子词导致重复召回）。

    返回 (added_entries, removed_labels, dedup_map)，其中 dedup_map 为
    {旧topic_key: [被删除的关键词列表]}。
    """
    yaml_path = cfg.ROOT / "config.yaml"
    text = yaml_path.read_text(encoding="utf-8")

    # 1) 删除
    removed_labels = []
    for key in removed_keys:
        if key in config.get("topics", {}):
            removed_labels.append(config["topics"][key].get("label", key))
            text = _delete_topic_block(text, key)

    # 2) 新增：找到 topics 块内最后一个 2 空格 topic key 的位置，在其块后插入
    existing_keys = set(config["topics"].keys()) - set(removed_keys)
    added_entries = []
    new_blocks = []
    for label in added_labels:
        entry = _generate_topic_entry(label, config, existing_keys)
        existing_keys.add(entry["key"])
        added_entries.append(entry)
        new_blocks.append(_render_topic_block(entry["key"], label,
                                              entry["keywords"]))

    if new_blocks:
        # 定位 topics 块：从 "topics:" 行到下一个 0 缩进的顶层键（ranking: 等）之前
        lines = text.split("\n")
        topics_start = None
        for idx, ln in enumerate(lines):
            if ln.rstrip() == "topics:":
                topics_start = idx
                break
        if topics_start is None:
            raise ValueError("config.yaml 中未找到 topics: 块")

        # 找到 topics 块内最后一个 2 空格 topic key 的块结束位置（即下一个
        # 0 缩进顶层键或文件结尾之前的插入点）
        insert_at = None
        j = topics_start + 1
        while j < len(lines):
            ln = lines[j]
            if ln and not ln.startswith(" ") and not ln.startswith("#"):
                # 0 缩进顶层键（如 ranking:），在此之前插入
                insert_at = j
                break
            j += 1
        if insert_at is None:
            insert_at = len(lines)

        # 向上回退：跳过紧邻 ranking 之前的分隔注释块（# ---）与空行，
        # 让新 topic 插在 topics 块末尾、分隔注释之前，保持注释紧贴 ranking。
        while insert_at > topics_start:
            prev = lines[insert_at - 1]
            if prev.strip() == "" or prev.lstrip().startswith("#"):
                insert_at -= 1
            else:
                break

        # 在 insert_at 处插入新块（每个块后空一行）
        blocks_text = ("\n\n".join(new_blocks)) + "\n"
        lines = lines[:insert_at] + blocks_text.split("\n") + lines[insert_at:]
        text = "\n".join(lines)

    # 3) 关键词去重：新 topic 的关键词若已存在于旧 topic，则从旧 topic 删除
    dedup_map = {}
    new_keys = [e["key"] for e in added_entries]
    if new_keys:
        # 用「新 topic 已写入磁盘后的最新配置」来算去重（重新加载）
        cfg._config = None
        fresh = cfg.config()
        dedup_map = dedup_keywords_between_topics(fresh, new_keys)
        for old_key, kws in dedup_map.items():
            for kw in kws:
                text = _delete_keyword_from_topic_block(text, old_key, kw)

    yaml_path.write_text(text, encoding="utf-8")
    # 清缓存，让后续 config() 重新加载
    cfg._config = None
    return added_entries, removed_labels, dedup_map


def sync_after_topics_change(config):
    """改完 config 后：重算全库标签 + 归集 Zotero 集合 + 建 Obsidian 目录。"""
    from . import db, obsidian
    from .topics import score_relevance

    db.init_db()
    # 1) 重算全库标签
    conn = db.get_conn()
    rows = conn.execute("SELECT id, paper_key, title, abstract, venue, "
                        "topics_json, relevance_score FROM papers").fetchall()
    changed = 0
    cur = conn.cursor()
    for r in rows:
        paper = {"title": r["title"] or "", "abstract": r["abstract"] or "",
                 "venue": r["venue"] or ""}
        topics, _matched, rel = score_relevance(paper, config)
        import json
        old = json.loads(r["topics_json"] or "[]")
        if topics != old or abs(rel - (r["relevance_score"] or 0)) > 0.01:
            cur.execute("UPDATE papers SET topics_json=?, relevance_score=? "
                        "WHERE paper_key=?",
                        (json.dumps(topics, ensure_ascii=False), rel,
                         r["paper_key"]))
            changed += 1
    conn.commit()
    conn.close()

    # 2) 建 Obsidian 主题目录
    vault = obsidian.ensure_vault(config["obsidian"]["vault_path"])

    # 3) Zotero 集合归集（确保新集合创建 + 论文归集）
    zotero_result = None
    all_papers = db.load_papers_for_report()
    added, failed, ok, _new_keys = zotero.add_via_local_api(all_papers, config)
    zotero_result = (added, failed, ok)

    return changed, vault, zotero_result
