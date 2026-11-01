PRAGMA foreign_keys = ON;

-- 论文主记录：一篇论文只保存一条，避免不同信源重复入库
CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_key TEXT NOT NULL UNIQUE,          -- 规范化唯一键：doi / arxiv / 标题哈希
    title TEXT NOT NULL,
    abstract TEXT DEFAULT '',
    authors_json TEXT DEFAULT '[]',
    published_at TEXT,                        -- ISO 日期
    year INTEGER,
    venue TEXT,                               -- 会议/期刊名，如 NeurIPS / TPAMI
    venue_type TEXT DEFAULT 'unknown',        -- conference / journal / preprint / unknown
    publication_status TEXT DEFAULT 'unknown',
    doi TEXT,
    arxiv_id TEXT,
    url TEXT,
    pdf_url TEXT,
    source TEXT NOT NULL,                     -- arxiv / openalex / crossref / github
    topics_json TEXT DEFAULT '[]',
    keywords_json TEXT DEFAULT '[]',          -- 关键词标签（最多 5 个）
    github_json TEXT DEFAULT '[]',
    raw_json TEXT DEFAULT '{}',
    relevance_score REAL DEFAULT 0,
    novelty_score REAL,
    evidence_score REAL,
    transfer_score REAL,
    analysis_status TEXT DEFAULT 'pending',   -- pending / completed / failed / skipped
    citation_key TEXT,                        -- Better BibTeX 风格引用键，跨软件稳定标识
    zotero_item_key TEXT,                     -- Zotero 条目 key
    obsidian_note_path TEXT,                  -- Obsidian 文献卡路径
    reading_status TEXT DEFAULT 'unread',     -- unread / to-read / reading / analyzed
    evidence_status TEXT DEFAULT 'unverified',-- unverified / verified / disputed
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_papers_published ON papers(published_at);
CREATE INDEX IF NOT EXISTS idx_papers_score ON papers(relevance_score DESC);
CREATE INDEX IF NOT EXISTS idx_papers_venue ON papers(venue);

-- 深度分析结果（按 prompt 版本可重跑）
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL REFERENCES papers(id),
    model TEXT,
    prompt_version TEXT NOT NULL,
    result_json TEXT NOT NULL,
    report_path TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(paper_id, prompt_version)
);

-- 结论的证据：每个重要结论都能追溯到原文
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL REFERENCES papers(id),
    claim TEXT NOT NULL,
    evidence_text TEXT,
    source_location TEXT,
    evidence_type TEXT,
    confidence REAL,
    verification_status TEXT DEFAULT 'unverified'
);

-- 论文关系：引用/继承/改进/组合/候选迁移
CREATE TABLE IF NOT EXISTS paper_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_paper_id INTEGER NOT NULL REFERENCES papers(id),
    target_paper_id INTEGER NOT NULL REFERENCES papers(id),
    relation_type TEXT NOT NULL,
    explanation TEXT,
    evidence TEXT,
    confidence REAL,
    verification_status TEXT DEFAULT 'candidate',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_paper_id, target_paper_id, relation_type)
);

-- 技术迁移假设 / 实验卡片
CREATE TABLE IF NOT EXISTS migration_ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source_papers_json TEXT DEFAULT '[]',
    target_domain TEXT,
    hypothesis TEXT,
    assumptions_json TEXT DEFAULT '[]',
    risks_json TEXT DEFAULT '[]',
    experiment_json TEXT DEFAULT '{}',
    status TEXT DEFAULT 'proposed',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 每次采集运行记录
CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    papers_found INTEGER DEFAULT 0,
    papers_new INTEGER DEFAULT 0,
    papers_analyzed INTEGER DEFAULT 0,
    error_message TEXT
);

-- 人工反馈：用于校准排序
CREATE TABLE IF NOT EXISTS user_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL REFERENCES papers(id),
    relevance_label INTEGER,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 同步日志：记录每次向 Zotero/Obsidian/ima 同步的动作，支持增量与追溯
CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,        -- zotero / obsidian / ima
    entity_key TEXT NOT NULL,         -- paper_key 或文件路径
    action TEXT NOT NULL,             -- created / updated / skipped / failed
    detail TEXT,
    content_hash TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
