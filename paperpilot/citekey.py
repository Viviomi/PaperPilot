"""稳定的引用键（citation key）生成，Better BibTeX 风格。

格式：<第一作者姓><年份><标题首个实义词>，小写，例如 wang2025multiagent。
碰撞时追加 a/b/c。作为 Zotero 与 Obsidian 之间跨软件的唯一标识。
"""

import re

_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "on", "in", "with", "to",
    "from", "by", "via", "toward", "towards", "using", "learning", "deep",
    "novel", "new", "towards", "robust", "efficient", "self", "supervised",
    "unsupervised", "few", "shot", "attention", "network", "networks",
    "model", "models", "based", "toward", "multi", "agent", "agents",
    "framework", "method", "methods", "approach", "towards",
}


def _first_author_surname(authors):
    if not authors:
        return "unknown"
    a = (authors[0] or "").strip()
    if not a:
        return "unknown"
    if "," in a:
        surname = a.split(",")[0]
    else:
        parts = a.split()
        surname = parts[-1] if parts else a
    surname = re.sub(r"[^a-zA-Z]+", "", surname)
    return surname.lower() or "unknown"


def _first_keyword(title):
    words = re.findall(r"[a-zA-Z0-9]+", title or "")
    for w in words:
        if w.lower() not in _STOPWORDS and len(w) >= 3:
            return w.lower()
    return words[0].lower() if words else "paper"


def make_citekey(authors, year, title, used=None):
    surname = _first_author_surname(authors)
    year_str = re.sub(r"[^0-9]", "", str(year)) if year else "nd"
    word = _first_keyword(title)
    base = f"{surname}{year_str}{word}"
    base = re.sub(r"[^a-z0-9]", "", base)
    if not base:
        base = "paper"

    used = used if used is not None else set()
    key = base
    suffix = "a"
    while key in used:
        key = base + suffix
        suffix = chr(ord(suffix) + 1)
    used.add(key)
    return key
