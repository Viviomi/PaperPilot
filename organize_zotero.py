# -*- coding: utf-8 -*-
"""一键整理：把数据库里所有论文入库 Zotero，并按分区归类（幂等）。

用法：
    1. 先打开 Zotero 桌面客户端（保持运行）；
    2. 确认「编辑 → 设置 → 高级 → 允许其他应用程序在本计算机与 Zotero 通信」已勾选；
    3. 运行：python organize_zotero.py

行为：
    - 尚无 Zotero 条目的论文 → 自动入库；
    - 已有条目的论文 → 只补归集（不重复入库）；
    - 每篇归入「一个状态分区」+「零到多个主题子集合」：
        PaperPilot/00_Inbox     （未读）
        PaperPilot/10_ToRead    （待精读）
        PaperPilot/20_Analyzed  （已深度分析）
        PaperPilot/多智能体协同 / 脑机接口 / 小样本学习 / 迁移学习 / 智能体设计
"""

from paperpilot import config as cfg, db, zotero


def main():
    c = cfg.config()
    db.init_db()
    papers = db.load_papers_for_report()

    print("=" * 60)
    print(f"待整理论文：{len(papers)} 篇")
    print("=" * 60)

    added, failed, ok, new_keys = zotero.add_via_local_api(papers, c)

    if not ok:
        print()
        print("[!] Zotero 本地 API 不可用。请先：")
        print("    1. 打开 Zotero 桌面客户端（保持运行，别关）；")
        print("    2. 「编辑 → 设置 → 高级」勾选「允许其他应用程序在本计算机与 Zotero 通信」；")
        print("    3. 重新运行本脚本。")
        return

    for pk, zk in new_keys.items():
        db.set_sync_fields(pk, zotero_item_key=zk)

    print()
    print("=" * 60)
    print(f"入库 {added} 成功 / {failed} 失败")
    print("=" * 60)

    # 按 DB 数据统计各分区的预期篇数
    from collections import Counter
    status = Counter()
    topics = Counter()
    for p in papers:
        status[zotero._status_role(p)] += 1
        for t in p.get("topics") or []:
            topics[t] += 1

    print("\n状态分区（每篇一篇）：")
    label = {"inbox": "00_Inbox（未读）", "to_read": "10_ToRead（待精读）",
             "analyzed": "20_Analyzed（已分析）"}
    for role in ("inbox", "to_read", "analyzed"):
        print(f"  - {label[role]}: {status.get(role, 0)} 篇")

    print("\n主题子集合（一篇可属多个主题）：")
    topic_label = {"multi_agent": "多智能体协同", "bci": "脑机接口",
                   "few_shot": "小样本学习", "transfer": "迁移学习",
                   "agents": "智能体设计"}
    for tk, name in topic_label.items():
        print(f"  - {name}: {topics.get(tk, 0)} 篇")

    print("\n完成。请到 Zotero 左侧「PaperPilot」集合查看整理结果。")


if __name__ == "__main__":
    main()
