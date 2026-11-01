"""关键词标签提取与扩充。

为每篇论文生成「关键词标签」（与主题标签 topics 并列，最多 5 个）：

1. 优先从 score_relevance 已算出的 matched_keywords（论文实际命中了哪些主题
   关键词）里选取，按「标题命中 > 摘要命中」的优先级排序，命中标题的关键词
   最能代表论文，排在前面；
2. 若命中关键词不足 5 个，用 LLM 从标题+摘要提炼论文自身的主题关键词补充
   （归一化去重后只取不足的数量）；
3. LLM 提炼出的新关键词会「回写」到 config.yaml 中对应主题的 keywords 列表，
   作为候选检索词，下次采集/重算时参与召回，形成关键词自动扩充闭环。

设计约束：
- 关键词标签最多 5 个（MAX_KEYWORD_TAGS）；
- 所有关键词统一小写、去首尾空白；
- 回写 config.yaml 用字符串级精确编辑，保留原有格式与注释。
"""

from __future__ import annotations

import json
import re

from . import config as cfg

MAX_KEYWORD_TAGS = 5  # 默认值；实际从 config.app.max_keyword_tags 读取


def _max_tags(config):
    """读取每篇论文关键词标签上限（默认 5）。"""
    try:
        return int(config.get("app", {}).get("max_keyword_tags", MAX_KEYWORD_TAGS))
    except (TypeError, ValueError):
        return MAX_KEYWORD_TAGS


def _max_topic_keywords(config):
    """读取每个主题关键词数量上限（默认 0=不限制）。"""
    try:
        return int(config.get("app", {}).get("max_topic_keywords", 0))
    except (TypeError, ValueError):
        return 0


def _norm(s: str) -> str:
    return (s or "").lower().strip()


def _matched_keywords_sorted(matched_keywords):
    """把 matched_keywords（{topic_key: [关键词原文]}）拍平成有序列表。

    排序规则：标题命中的词优先（score_relevance 里 title 命中权重更高，
    更能代表论文主题），同一关键词只保留一次。由于 matched_keywords 只
    存关键词原文而不区分命中位置，这里对每个 topic 内的关键词保持原有
    顺序（score_relevance 按 keywords 列表顺序命中），跨 topic 按 config
    主题顺序排。
    """
    seen = set()
    out = []
    if not matched_keywords:
        return out
    for _topic, kws in matched_keywords.items():
        for kw in kws:
            norm = _norm(kw)
            if norm and norm not in seen:
                seen.add(norm)
                out.append(norm)
    return out


def _llm_extract_keywords(paper, config, max_n, with_topic=False):
    """用 LLM 从标题+摘要提炼最多 max_n 个论文主题关键词。

    当 with_topic=True 时，让 LLM 同时判断每个关键词最可能属于哪个主题
    （从 config 的 topics 里选，key 或 label 均可），返回
    [(关键词, topic_key或None)]；否则返回 [关键词]。

    LLM 未配置/失败/无有效结果时返回 []。
    """
    api_key = cfg.env("LLM_API_KEY", "")
    if not api_key:
        return []
    title = paper.get("title") or ""
    abstract = (paper.get("abstract") or "")[:1500]
    if not title and not abstract:
        return []
    # 构造主题候选表，供 LLM 做归属判断
    topics = config.get("topics", {})
    topic_options = []
    for tkey, t in topics.items():
        topic_options.append({"key": tkey, "label": t.get("label", tkey)})

    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            base_url=cfg.env("LLM_BASE_URL", "https://api.openai.com/v1"),
            timeout=60.0,
            max_retries=1,
        )
        model = cfg.env("LLM_MODEL", "gpt-4.1-mini")
        if with_topic:
            system = (
                "你是科研论文关键词提取助手。给定论文的标题和摘要，请提炼出最能"
                "代表这篇论文研究主题与方法的英文关键词（小写，含常见缩写、近义词、"
                "子方向术语）。同时，对每个关键词判断它最可能属于给定的哪一个研究"
                "主题：若确实属于某个主题，topic 填该主题的 key；若都不属于，"
                "topic 填 null。只输出一个 JSON 对象，含一个字段 keywords，值为"
                "对象数组，每个对象形如 {\"keyword\": \"...\", \"topic\": \"主题key或null\"}。"
                "只输出 JSON，不要任何解释。"
            )
            user = json.dumps({
                "title": title,
                "abstract": abstract,
                "max": max_n,
                "topics": topic_options,
            }, ensure_ascii=False)
        else:
            system = (
                "你是科研论文关键词提取助手。给定论文的标题和摘要，请提炼出最能"
                "代表这篇论文研究主题与方法的英文关键词（小写，含常见缩写、近义词、"
                "子方向术语）。只输出一个 JSON 对象，包含一个字段 keywords，"
                "值为字符串数组。只输出 JSON，不要任何解释。"
            )
            user = json.dumps({
                "title": title,
                "abstract": abstract,
                "max": max_n,
            }, ensure_ascii=False)

        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        items = data.get("keywords") or []
        valid_topics = set(topics.keys())
        out = []
        seen = set()
        for it in items:
            if with_topic:
                if isinstance(it, dict):
                    kw = _norm(str(it.get("keyword", "")))
                    tkey = (it.get("topic") or "").strip()
                    tkey = tkey if tkey in valid_topics else None
                else:
                    kw = _norm(str(it))
                    tkey = None
            else:
                kw = _norm(str(it))
                tkey = None
            if kw and kw not in seen:
                seen.add(kw)
                out.append((kw, tkey) if with_topic else kw)
        return out[:max_n]
    except Exception:
        return []


def _guess_topic_for_keyword(keyword, config):
    """判断一个关键词最可能属于哪个主题（用于回写 config）。

    规则：若关键词已经在某个主题的 keywords 里（归一化相等），返回该主题；
    否则用包含关系（新词与主题关键词互为子串）判断，返回第一个命中的主题；
    都不中则返回 None（不回写）。
    """
    topics = config.get("topics", {})
    # 1) 精确命中（全库归一化）
    for tkey, t in topics.items():
        for kw in t.get("keywords", []):
            if _norm(kw) == keyword:
                return tkey
    # 2) 主题关键词包含关系（例如新词 "soft actor critic" 与主题里 "actor-critic" 相关）
    for tkey, t in topics.items():
        for kw in t.get("keywords", []):
            k = _norm(kw)
            if k and (k in keyword or keyword in k):
                return tkey
    return None


def _keyword_exists_anywhere(keyword, config, exclude_topic=None):
    """判断关键词是否已存在于全库任一主题的 keywords（归一化相等）。"""
    for tkey, t in config.get("topics", {}).items():
        if tkey == exclude_topic:
            continue
        for kw in t.get("keywords", []):
            if _norm(kw) == keyword:
                return True
    return False


def _all_existing_keywords(config):
    """返回全库所有主题的关键词（归一化后、去重、保持顺序）。"""
    seen = set()
    out = []
    for t in config.get("topics", {}).values():
        for kw in t.get("keywords", []):
            n = _norm(kw)
            if n and n not in seen:
                seen.add(n)
                out.append(n)
    return out


def _literal_dedup(candidate, existing_keywords):
    """字面层去重（不调 LLM）：归一化相等 或 互为子串 时返回已有词。

    返回：
        已有词（字符串）：candidate 与它相等或互为子串，应被该词替代；
        None：字面无法判断，需交给 LLM 语义判断。
    """
    c = _norm(candidate)
    if not c:
        return None
    # 1) 完全相等
    for k in existing_keywords:
        if k == c:
            return k
    # 2) 互为子串（如 "domain adaptation" 与 "source-free domain adaptation"）
    for k in existing_keywords:
        if k and (k in c or c in k):
            return k
    return None


def _semantic_dedup(candidates, config):
    """用 LLM 批量判断候选关键词是否与已有词语义等价/被包含。

    参数：
        candidates: 候选关键词列表（小写）。
        config: 全局配置。

    返回 dict：{候选词: 决策词}。决策规则——
        - 与某已有词语义等价或被包含 → 映射到该已有词（用已有词替代）；
        - 是独立的新概念 → 映射到候选词自身（保留）；
        - 无效/无法判断 → 不出现（调用方视为丢弃）。

    字面层（相等/子串）先本地判定，不再交给 LLM；只有字面判不了的才进
    LLM。LLM 未配置或失败时，返回"全保留"（映射到自身），语义合并退化为
    不做，保证功能可用。
    """
    existing = _all_existing_keywords(config)

    result = {}
    need_llm = []
    for c in candidates:
        c = _norm(c)
        if not c:
            continue
        lit = _literal_dedup(c, existing)
        if lit is not None:
            # 字面层命中：用已有词替代（即使 c == 已有词也保持一致）
            result[c] = lit
        else:
            need_llm.append(c)

    if need_llm:
        llm_map = _llm_semantic_dedup(need_llm, existing, config)
        for c in need_llm:
            if c in llm_map:
                result[c] = llm_map[c]
            else:
                result[c] = c  # LLM 未给出结论 → 保留自身
    return result


def _llm_semantic_dedup(candidates, existing_keywords, config):
    """LLM 语义去重的一次调用。返回 {候选词: 决策词}。

    LLM 未配置/失败时返回 {}（调用方会回退为保留自身）。
    """
    api_key = cfg.env("LLM_API_KEY", "")
    if not api_key or not candidates:
        return {}
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            base_url=cfg.env("LLM_BASE_URL", "https://api.openai.com/v1"),
            timeout=90.0,
            max_retries=1,
        )
        model = cfg.env("LLM_MODEL", "gpt-4.1-mini")
        system = (
            "你是科研论文关键词去重助手。给定一批「候选关键词」和一份「已有关键词」"
            "列表，请判断每个候选词是否与某个已有词在语义上等价、是同义改写、或是"
            "被已有词包含的下位词/子方向，或是过于碎片化、无法独立表达完整概念的"
            "修饰性残词。规则：\n"
            "1. 若候选词与某已有词等价/同义/被其包含，用该已有词替代，映射到"
            "该已有词；\n"
            "2. 若候选词是独立的新概念（不在已有词覆盖范围内），映射到候选词自身；\n"
            "3. 若候选词是碎片残词（如仅剩修饰前缀，本身不构成完整术语），且其完整"
            "形式已被某个已有词覆盖，则映射到那个已有词；若无法归并，映射到 null"
            "（表示丢弃）；\n"
            "4. 若候选词无效（无意义、过泛、非专业术语），映射到 null（丢弃）。\n"
            "只输出一个 JSON 对象，字段为 mapping，值为「候选词 → 决策词」的键值对"
            "（决策词为已有词、候选词自身、或 null）。只输出 JSON，不要任何解释。"
        )
        user = json.dumps({
            "candidates": candidates,
            "existing": existing_keywords,
        }, ensure_ascii=False)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        mapping = data.get("mapping") or {}
        out = {}
        valid_existing = set(existing_keywords)
        for c, target in mapping.items():
            c = _norm(str(c))
            if not c:
                continue
            if target is None:
                continue  # null → 丢弃，不写入
            target = _norm(str(target))
            if not target:
                continue
            # 决策词必须是已有词或候选词自身，否则忽略（防止 LLM 造新词）
            if target in valid_existing or target == c:
                out[c] = target
        return out
    except Exception:
        return {}


def _append_keyword_to_config(keyword, topic_key, config):
    """把新关键词回写到 config.yaml 的指定主题 keywords 列表末尾。

    去重策略：全库归一化去重——若该词已存在于任一主题（含目标主题）的
    keywords，则跳过；且受每主题关键词上限（app.max_topic_keywords）约束。
    用字符串级精确编辑，保留原格式。
    返回是否写入。
    """
    topics = config.get("topics", {})
    if topic_key not in topics:
        return False
    # 全库归一化去重
    if _keyword_exists_anywhere(keyword, config):
        return False
    # 每主题上限
    max_topic_kws = _max_topic_keywords(config)
    if max_topic_kws > 0:
        if len(topics[topic_key].get("keywords", [])) >= max_topic_kws:
            return False

    yaml_path = cfg.ROOT / "config.yaml"
    text = yaml_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    out = []
    i = 0
    in_block = False
    last_kw_idx = -1
    while i < len(lines):
        line = lines[i]
        if line == f"  {topic_key}:":
            in_block = True
            out.append(line)
            i += 1
            continue
        if in_block:
            stripped = line.lstrip()
            # 块结束：0 缩进顶层键 或 下一个 2 空格 topic key
            if line and not line.startswith(" ") and stripped:
                in_block = False
            elif line.startswith("  ") and not line.startswith("    ") \
                    and stripped.endswith(":"):
                in_block = False
        if in_block and line.startswith("    - "):
            last_kw_idx = len(out)
        out.append(line)
        i += 1

    if last_kw_idx == -1:
        # 该主题没有关键词行（异常），不强行插入
        return False
    # 在最后一个关键词行之后插入新关键词行
    out.insert(last_kw_idx + 1, f"    - {keyword}")
    yaml_path.write_text("\n".join(out), encoding="utf-8")
    cfg._config = None  # 清缓存，让后续 config() 重新加载
    return True


def extract_keyword_tags(paper, config, matched_keywords=None,
                         use_llm=True, collect_new=False):
    """为论文生成关键词标签（最多 5 个）。

    参数：
        paper: 论文 dict（含 title/abstract）。
        config: 全局配置。
        matched_keywords: score_relevance 返回的 {topic_key: [关键词]}，
            若为 None 则内部重算。
        use_llm: 是否用 LLM 提炼补充（全库重算时可关，省成本）。
        collect_new: 为 True 时，返回的 new_keywords 是 [(关键词, 归属主题)]，
            由调用方统一批量回写 config；为 False 时（默认）LLM 新词直接
            就地回写 config（用于单篇/重点论文即时回写）。

    关键词标签构成（最多 5 个，优先级从高到低）：
        1. 命中的主题关键词（matched_keywords，标题命中优先）；
        2. LLM 提炼的论文主题词（若不足 5 个），LLM 同时判断每个词归属哪个
           主题（语义层）；归属判断再叠加字面层（精确/包含）兜底。

    返回 (keywords: list, new_keywords):
        keywords      最终的关键词标签（<=5 个，小写去重）。
        new_keywords  若 collect_new=True，为 [(kw, topic_key)]（未归属主题的
            记为 topic_key=None）；否则为已回写 config 的关键词列表。
    """
    if matched_keywords is None:
        from .topics import score_relevance
        _topics, matched_keywords, _rel = score_relevance(paper, config)

    max_tags = _max_tags(config)
    # 命中关键词：先做语义合并（等价/子串/碎片残词 → 用已有词替代），再截断
    raw_hits = _matched_keywords_sorted(matched_keywords)
    if use_llm:
        hit_dedup = _semantic_dedup(raw_hits, config) if raw_hits else {}
    else:
        # 不调 LLM（午夜采集等快速路径）：仅字面层去重（相等/互为子串用已有词替代）
        existing = _all_existing_keywords(config)
        hit_dedup = {}
        for h in raw_hits:
            n = _norm(h)
            lit = _literal_dedup(n, existing)
            hit_dedup[n] = lit if lit is not None else n
    tags = []
    seen_hits = set()
    for h in raw_hits:
        resolved = hit_dedup.get(_norm(h))
        if resolved is None:
            continue  # LLM 判为丢弃/碎片残词
        resolved = _norm(resolved)
        if not resolved or resolved in seen_hits:
            continue
        seen_hits.add(resolved)
        tags.append(resolved)
        if len(tags) >= max_tags:
            break

    new_keywords = []
    if use_llm and len(tags) < max_tags:
        need = max_tags - len(tags)
        # 语义层：LLM 一次调用，同时提炼关键词并判断主题归属
        llm_kws = _llm_extract_keywords(paper, config, need, with_topic=True)
        # 语义合并：等价/被包含的词用已有词替代（标签与检索词统一）
        dedup_map = _semantic_dedup([kw for kw, _t in llm_kws], config)
        existing = set(tags)
        for kw, llm_topic in llm_kws:
            kw = dedup_map.get(_norm(kw), _norm(kw))
            if not kw:
                continue  # LLM 判为丢弃
            if kw in existing:
                continue
            if len(tags) >= max_tags:
                break
            tags.append(kw)
            existing.add(kw)
            # 归属判断：优先 LLM 语义判断，其次字面匹配（精确/包含）兜底
            topic_key = llm_topic or _guess_topic_for_keyword(kw, config)
            if collect_new:
                new_keywords.append((kw, topic_key))
            else:
                if topic_key and _append_keyword_to_config(kw, topic_key, config):
                    new_keywords.append(kw)

    return tags[:max_tags], new_keywords


def batch_write_new_keywords(new_keyword_pairs, config):
    """批量把 LLM 提炼的新关键词回写 config.yaml（全库归一化去重 + 每主题上限）。

    参数：
        new_keyword_pairs: [(关键词, 归属主题key)] 的集合；topic_key 为 None
            的跳过（无法归属的主题词不回写）。
    返回：实际写入的关键词数量。
    """
    max_topic_kws = _max_topic_keywords(config)  # 0 = 不限制

    # 语义合并：等价/被包含的候选词先用已有词替代（标签与检索词统一）。
    # 这一步会调 LLM（批量一次），把「已有词」归一化后合并进 existing。
    candidates = [_norm(kw) for kw, _t in new_keyword_pairs if _norm(kw)]
    dedup_map = _semantic_dedup(candidates, config)

    # 全库归一化去重：收集所有已存在关键词
    existing = set()
    for t in config.get("topics", {}).values():
        for kw in t.get("keywords", []):
            n = _norm(kw)
            if n:
                existing.add(n)

    # 去重 + 按主题分组（保持首次出现顺序）
    seen = set()
    per_topic = {}  # topic_key -> [关键词]（有序去重）
    for kw, topic_key in new_keyword_pairs:
        # 语义合并：等价/被包含 → 替换为已有词（或丢弃）
        resolved = dedup_map.get(_norm(kw))
        if resolved is None:
            continue  # LLM 判为丢弃 / 无效
        n = _norm(resolved)
        if not n or n in existing or n in seen:
            continue
        if not topic_key:
            continue
        seen.add(n)
        existing.add(n)
        per_topic.setdefault(topic_key, []).append(n)

    if not per_topic:
        return 0

    # 每主题上限：超过 max_topic_kws 的主题截断新增词
    if max_topic_kws > 0:
        cur_count = {k: len(v.get("keywords", []))
                     for k, v in config.get("topics", {}).items()}
        for topic_key in list(per_topic.keys()):
            room = max_topic_kws - cur_count.get(topic_key, 0)
            if room <= 0:
                del per_topic[topic_key]
            else:
                per_topic[topic_key] = per_topic[topic_key][:room]

    if not per_topic:
        return 0

    yaml_path = cfg.ROOT / "config.yaml"
    text = yaml_path.read_text(encoding="utf-8")
    lines = text.split("\n")

    # 对每个主题块，在其最后一个关键词行后插入新词
    # 先扫描一遍，记录每个主题块内最后一个 `    - ` 行的位置
    topic_last_kw = {}  # topic_key -> 最后一个关键词行在 lines 中的索引
    i = 0
    current_topic = None
    while i < len(lines):
        line = lines[i]
        # 匹配 2 空格缩进的 topic key
        if line.startswith("  ") and not line.startswith("    ") \
                and line.rstrip().endswith(":") and not line.lstrip().startswith("-"):
            # 可能是 topic key（如 "  machine_learning:"）
            candidate = line.strip().rstrip(":")
            if candidate in config.get("topics", {}):
                current_topic = candidate
        elif line.startswith("    - "):
            if current_topic is not None:
                topic_last_kw[current_topic] = i
        elif line and not line.startswith(" ") and not line.startswith("#"):
            current_topic = None  # 0 缩进顶层键，离开 topics 块
        i += 1

    # 按行号从大到小插入（从文件末尾往开头插，避免索引偏移）
    insertions = []  # (行号, 新词列表)
    for topic_key, kws in per_topic.items():
        if topic_key in topic_last_kw:
            insertions.append((topic_last_kw[topic_key], kws))

    if not insertions:
        return 0

    insertions.sort(key=lambda x: x[0], reverse=True)
    for idx, kws in insertions:
        block = "\n".join(f"    - {kw}" for kw in kws)
        # 在 idx 这一行之后插入新关键词行
        lines[idx + 1:idx + 1] = [block]

    yaml_path.write_text("\n".join(lines), encoding="utf-8")
    cfg._config = None
    return sum(len(kws) for _idx, kws in insertions)
