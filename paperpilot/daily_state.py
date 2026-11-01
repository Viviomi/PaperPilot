"""跨定时任务共享的「每日状态」：采集/精读/点评结果，供晚间日报汇总。

三个定时任务（午夜采集、早上精读、晚间点评）各自独立运行，需要一个轻量的
状态载体把一天的结果串起来。这里用 data/daily/YYYY-MM-DD.json 持久化，
避免引入新表或改动调度框架。

约定字段（均为可选的列表/标量）：
- collect: {"new": int, "found": int, "per_venue": {venue: count}, "time": str}
- read:    [{"citekey": str, "title": str, "topic": str}, ...]
- review:  [{"citekey": str, "title": str, "judgment": {field: text}}, ...]
"""

import json
from datetime import datetime

from .config import ROOT

STATE_DIR = ROOT / "data" / "daily"


def _day(day=None):
    return day or datetime.now().strftime("%Y-%m-%d")


def _path(day=None):
    return STATE_DIR / f"{_day(day)}.json"


def load(day=None):
    p = _path(day)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def update(day=None, **fields):
    """合并写入当天状态，返回最新状态 dict。"""
    p = _path(day)
    p.parent.mkdir(parents=True, exist_ok=True)
    st = load(day)
    st.update(fields)
    p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    return st


def now_str():
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")
