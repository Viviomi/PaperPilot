"""论文数据模型：规范化、唯一键、主题归属等通用工具。"""

import re


def normalize_title(title: str) -> str:
    """规范化标题用于去重匹配：小写、去标点、压缩空白。"""
    t = (title or "").lower()
    t = re.sub(r"[^\w\u4e00-\u9fff]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def make_paper_key(paper: dict) -> str:
    """生成论文唯一键：优先 DOI，其次 arXiv ID，最后规范化标题。"""
    doi = (paper.get("doi") or "").strip().lower()
    if doi:
        return "doi:" + doi
    arxiv = (paper.get("arxiv_id") or "").strip()
    if arxiv:
        return "arxiv:" + arxiv
    return "title:" + normalize_title(paper.get("title", ""))


def clean_arxiv_id(raw: str) -> str:
    """从各种形式提取 arXiv ID 主体（去掉版本号与 URL）。"""
    if not raw:
        return ""
    m = re.search(r"(\d{4}\.\d{4,5})(?:v\d+)?", raw)
    return m.group(1) if m else raw.strip()


def strip_arxiv_version(raw: str) -> str:
    """把 2403.12345v2 -> 2403.12345。"""
    return re.sub(r"v\d+$", "", (raw or "").strip())
