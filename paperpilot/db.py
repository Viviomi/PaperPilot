"""SQLite 数据库操作：初始化、论文 upsert、分析/运行记录。"""

import json
import sqlite3
from datetime import datetime, timezone

from .config import DB_PATH, ROOT

PAPER_COLUMNS = [
    "paper_key", "title", "abstract", "authors_json", "published_at", "year",
    "venue", "venue_type", "publication_status", "doi", "arxiv_id", "url",
    "pdf_url", "source", "topics_json", "keywords_json", "github_json", "raw_json",
    "relevance_score", "novelty_score", "evidence_score", "transfer_score",
    "analysis_status", "citation_key", "zotero_item_key", "obsidian_note_path",
    "reading_status", "evidence_status",
]

# 升级新增的列（旧库自动补齐）
NEW_COLUMNS = {
    "citation_key": "TEXT",
    "zotero_item_key": "TEXT",
    "obsidian_note_path": "TEXT",
    "reading_status": "TEXT DEFAULT 'unread'",
    "evidence_status": "TEXT DEFAULT 'unverified'",
    "keywords_json": "TEXT DEFAULT '[]'",
}


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
    conn = get_conn()
    try:
        conn.executescript(schema)
        conn.commit()
    finally:
        conn.close()
    migrate()


def migrate() -> None:
    """为旧库补齐新增列，并确保 sync_log 表存在。幂等。"""
    conn = get_conn()
    try:
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(papers)")}
        for col, decl in NEW_COLUMNS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE papers ADD COLUMN {col} {decl}")
        conn.execute("""CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT,
            content_hash TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def upsert_paper(paper: dict) -> str:
    """插入或更新一篇论文。返回 'new' 或 'updated' 或 'unchanged'。"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, relevance_score, analysis_status FROM papers WHERE paper_key = ?",
            (paper["paper_key"],),
        ).fetchone()

        values = {c: paper.get(c) for c in PAPER_COLUMNS}
        # JSON 字段序列化
        values["authors_json"] = json.dumps(
            paper.get("authors", []) or [], ensure_ascii=False)
        values["topics_json"] = json.dumps(
            paper.get("topics", []) or [], ensure_ascii=False)
        values["keywords_json"] = json.dumps(
            paper.get("keywords", []) or [], ensure_ascii=False)
        values["github_json"] = json.dumps(
            paper.get("github", []) or [], ensure_ascii=False)
        values["raw_json"] = json.dumps(
            paper.get("raw", {}) or {}, ensure_ascii=False)
        values["updated_at"] = _now()

        if row is None:
            values["created_at"] = _now()
            cols = list(values.keys())
            placeholders = ", ".join("?" for _ in cols)
            conn.execute(
                f"INSERT INTO papers ({', '.join(cols)}) VALUES ({placeholders})",
                [values[c] for c in cols],
            )
            conn.commit()
            return "new"

        # 已有记录：合并主题与 github（新数据补充，不覆盖已有分析）
        set_clause = []
        params = []
        # 同步字段由同步流程（set_sync_fields）单独维护，UPSERT 不得用采集器
        # 返回的空值覆盖它们，否则会导致 Zotero/Obsidian 重复入库/重建。
        _SYNC_ONLY = ("zotero_item_key", "citation_key", "obsidian_note_path",
                      "reading_status", "evidence_status")
        for c in PAPER_COLUMNS:
            if c in ("analysis_status", "novelty_score", "evidence_score",
                     "transfer_score") or c in _SYNC_ONLY:
                continue  # 分析状态/分数与同步字段由各自流程单独更新
            set_clause.append(f"{c} = ?")
            params.append(values[c])
        set_clause.append("updated_at = ?")
        params.append(values["updated_at"])
        params.append(paper["paper_key"])
        conn.execute(
            f"UPDATE papers SET {', '.join(set_clause)} WHERE paper_key = ?",
            params,
        )
        conn.commit()
        return "updated"
    finally:
        conn.close()


def mark_analyzed(paper_id: int, result: dict) -> None:
    """把 LLM 分析结果写回 papers 表，并记录 analyses。"""
    conn = get_conn()
    try:
        conn.execute(
            """UPDATE papers SET
                 novelty_score = ?, evidence_score = ?, transfer_score = ?,
                 analysis_status = ?
               WHERE id = ?""",
            (
                result.get("novelty_score"),
                result.get("evidence_score"),
                result.get("transfer_score"),
                result.get("status", "completed"),
                paper_id,
            ),
        )
        conn.execute(
            """INSERT OR REPLACE INTO analyses
               (paper_id, model, prompt_version, result_json, report_path)
               VALUES (?, ?, ?, ?, ?)""",
            (
                paper_id,
                result.get("model"),
                result.get("prompt_version", "v1"),
                json.dumps(result, ensure_ascii=False),
                result.get("report_path"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_paper_id(paper_key: str):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM papers WHERE paper_key = ?", (paper_key,)).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def start_run() -> int:
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO collection_runs (status) VALUES ('running')")
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def finish_run(run_id: int, status: str, papers_found: int,
               papers_new: int, papers_analyzed: int, error: str = "") -> None:
    conn = get_conn()
    try:
        conn.execute(
            """UPDATE collection_runs
               SET finished_at = ?, status = ?, papers_found = ?,
                   papers_new = ?, papers_analyzed = ?, error_message = ?
               WHERE id = ?""",
            (_now(), status, papers_found, papers_new, papers_analyzed,
             error, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def stats() -> dict:
    conn = get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        analyzed = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE analysis_status = 'completed'"
        ).fetchone()[0]
        by_venue = {
            r["venue"]: r["n"] for r in conn.execute(
                "SELECT venue, COUNT(*) AS n FROM papers "
                "WHERE venue IS NOT NULL AND venue != '' "
                "GROUP BY venue ORDER BY n DESC"
            ).fetchall()
        }
        return {"total": total, "analyzed": analyzed, "by_venue": by_venue}
    finally:
        conn.close()


def load_papers_for_report() -> list:
    """返回带最新分析结果的论文列表，供 Markdown / HTML 报告使用。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT p.*, a.result_json, a.report_path
               FROM papers p
               LEFT JOIN analyses a
                 ON a.paper_id = p.id
                 AND a.id = (SELECT MAX(id) FROM analyses
                             WHERE paper_id = p.id)
               ORDER BY p.relevance_score DESC, p.published_at DESC
            """).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # 反序列化并重命名为报告/工作台使用的字段名
            for src, dst in (("authors_json", "authors"),
                             ("topics_json", "topics"),
                             ("keywords_json", "keywords"),
                             ("github_json", "github")):
                try:
                    d[dst] = json.loads(d.get(src) or "[]")
                except Exception:
                    d[dst] = []
                d.pop(src, None)
            try:
                d["raw"] = json.loads(d.get("raw_json") or "{}")
            except Exception:
                d["raw"] = {}
            d.pop("raw_json", None)
            try:
                d["analysis"] = json.loads(d.get("result_json") or "null")
            except Exception:
                d["analysis"] = None
            d.pop("result_json", None)
            out.append(d)
        return out
    finally:
        conn.close()


def set_sync_fields(paper_key, **fields):
    """更新论文的同步相关字段（citation_key / zotero_item_key /
    obsidian_note_path / reading_status）。只更新传入的非 None 字段。"""
    allowed = {"citation_key", "zotero_item_key", "obsidian_note_path",
               "reading_status", "evidence_status"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn = get_conn()
    try:
        conn.execute(
            f"UPDATE papers SET {set_clause} WHERE paper_key = ?",
            list(updates.values()) + [paper_key],
        )
        conn.commit()
    finally:
        conn.close()


def log_sync(entity_type, entity_key, action, detail="", content_hash=""):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO sync_log (entity_type, entity_key, action, detail, "
            "content_hash) VALUES (?, ?, ?, ?, ?)",
            (entity_type, entity_key, action, detail, content_hash),
        )
        conn.commit()
    finally:
        conn.close()


def last_sync(entity_type, entity_key):
    """返回最近一次同步的 (action, content_hash)，用于增量判断。"""
    conn = get_conn()
    try:
        r = conn.execute(
            "SELECT action, content_hash FROM sync_log WHERE entity_type = ? "
            "AND entity_key = ? ORDER BY id DESC LIMIT 1",
            (entity_type, entity_key),
        ).fetchone()
        return (r["action"], r["content_hash"]) if r else None
    finally:
        conn.close()


def get_citation_key_map():
    """返回 {paper_key: citation_key} 已有映射，用于稳定分配引用键。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT paper_key, citation_key FROM papers "
            "WHERE citation_key IS NOT NULL AND citation_key != ''"
        ).fetchall()
        return {r["paper_key"]: r["citation_key"] for r in rows}
    finally:
        conn.close()


def get_keyword_map():
    """返回 {paper_key: [keywords]} 已有映射，供入库时对旧论文保留关键词、
    避免重复 LLM 提炼。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT paper_key, keywords_json FROM papers").fetchall()
        out = {}
        for r in rows:
            try:
                out[r["paper_key"]] = json.loads(r["keywords_json"] or "[]")
            except Exception:
                out[r["paper_key"]] = []
        return out
    finally:
        conn.close()
