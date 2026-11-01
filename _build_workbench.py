"""从数据库重新生成 paperpilot_workbench.html（供控制服务"生成工作台"按钮调用）。

不联网、不采集、不调 LLM，只把当前 DB 里的论文 + 分析结果重新渲染成工作台。
代码候选仓库（repos）属运行时临时数据，未持久化，故这里传空列表。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from paperpilot import config as cfg, db  # noqa: E402
from paperpilot import reports  # noqa: E402


def rebuild():
    """从数据库重新生成工作台 HTML（可被 morning_read / night_review / main 等复用）。"""
    cfg.config()
    db.init_db()
    papers = db.load_papers_for_report()
    stats = db.stats()
    repos = []  # GitHub 候选仓库为运行时数据，未入库，故为空

    # 顺带刷新已分析论文的单篇报告（幂等，仅 completed 的）
    config = cfg.config()
    for p in papers:
        if p.get("analysis_status") == "completed":
            reports.generate_paper_report(p, config)

    dash = reports.generate_dashboard(papers, repos, config, stats)
    print(f"工作台已生成：{dash}")
    print(f"统计：{stats}")
    return dash


def main():
    rebuild()


if __name__ == "__main__":
    main()
