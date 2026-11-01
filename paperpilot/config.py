"""配置加载与常用路径、环境变量访问。"""

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = DATA_DIR / "reports"
LOG_DIR = ROOT / "logs"
DB_PATH = DATA_DIR / "papers.db"

_config = None
_env_loaded = False


def _load_env():
    global _env_loaded
    if _env_loaded:
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:
        pass
    _env_loaded = True


def config():
    global _config
    if _config is None:
        with open(ROOT / "config.yaml", encoding="utf-8") as f:
            _config = yaml.safe_load(f)
        _load_env()
    return _config


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def all_venues() -> dict:
    """返回 {venue名: {openalex_source, issn, type}}，type 为 conference/journal。"""
    c = config()["venues"]
    out = {}
    for vtype, items in c.items():
        kind = "conference" if vtype == "conferences" else "journal"
        for name, meta in items.items():
            out[name] = dict(meta, type=kind)
    return out


def venue_names() -> list:
    return list(all_venues().keys())


def topic_keywords() -> dict:
    return {k: v.get("keywords", []) for k, v in config()["topics"].items()}


def topic_labels() -> dict:
    return {k: v.get("label", k) for k, v in config()["topics"].items()}
