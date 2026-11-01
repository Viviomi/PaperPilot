"""从 Zotero 集合反向同步 topic 到 config.yaml。

场景：你在 Zotero 里手动增/删/改了 PaperPilot 父集合下的主题子集合，
想让这些变化自动反映到 config.yaml 的 topics，并自动补全新 topic 的
英文 key 和关键词（用 LLM 生成），然后重算标签、归集 Zotero 集合、
建 Obsidian 目录。

用法：
    python sync_topics_from_zotero.py --dry-run   # 只列出差异，不改任何文件
    python sync_topics_from_zotero.py             # 列出差异后交互确认
    python sync_topics_from_zotero.py --yes       # 直接应用（含删除）

映射约定：
    Zotero 集合名（中文 label）== config.yaml topic 的 label。
    状态集合 00_Inbox/10_ToRead/20_Analyzed 与父集合 PaperPilot 不参与。

前置：Zotero 客户端需打开，且「设置→高级→允许其他应用通信」已勾选。
"""

import argparse
import sys

from paperpilot import config as cfg
from paperpilot.topic_sync import (
    apply_topic_changes,
    diff_topics,
    get_zotero_topic_collections,
    sync_after_topics_change,
)


def _print_diff(config, added, removed):
    print("\n当前 config topics（label）：")
    for k, v in config["topics"].items():
        print(f"  {v.get('label', k)}  (key={k})")
    print()
    if added:
        print("【新增】Zotero 有、config 无的集合 → 将自动补全 key + 关键词：")
        for label in added:
            print(f"  + {label}")
    if removed:
        print("【删除】config 有、Zotero 无的主题 → 将从 config 删除：")
        for key, label in removed:
            print(f"  - {label}  (key={key})")
    if not added and not removed:
        print("Zotero 集合与 config topics 已完全一致，无需同步。")


def main():
    ap = argparse.ArgumentParser(description="从 Zotero 集合反向同步 topic 到 config.yaml")
    ap.add_argument("--dry-run", action="store_true", help="只列差异，不写任何文件")
    ap.add_argument("--yes", "-y", action="store_true", help="直接应用，不确认")
    args = ap.parse_args()

    config = cfg.config()
    zotero_names = get_zotero_topic_collections(config)
    if zotero_names is None:
        print("[主题同步] Zotero 本地 API 不可用，请先打开 Zotero 客户端。")
        sys.exit(1)

    print(f"[主题同步] Zotero 主题集合（{len(zotero_names)} 个）：{zotero_names}")
    added, removed = diff_topics(config, zotero_names)
    _print_diff(config, added, removed)

    if not added and not removed:
        sys.exit(0)

    if args.dry_run:
        print("\n[主题同步] --dry-run：未修改任何文件。")
        sys.exit(0)

    if removed and not args.yes:
        labels = ", ".join(l for _k, l in removed)
        ans = input(f"\n⚠️ 将删除 {len(removed)} 个主题：{labels}。"
                    f"删除后这些主题的论文标签会消失。确认继续？[y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            print("已取消。")
            sys.exit(0)

    if not args.yes:
        ans = input("\n确认应用上述增删改动？[y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            print("已取消。")
            sys.exit(0)

    removed_keys = [k for k, _l in removed]
    added_entries, removed_labels, dedup_map = apply_topic_changes(
        config, added, removed_keys)

    print("\n[主题同步] config.yaml 已更新：")
    for e in added_entries:
        print(f"  + 新增 topic：{e['label']} (key={e['key']}, "
              f"{len(e['keywords'])} 个关键词)")
    for l in removed_labels:
        print(f"  - 删除 topic：{l}")
    if dedup_map:
        print("\n[关键词去重] 新 topic 的关键词已从其它主题中移除，避免重复召回：")
        for old_key, kws in dedup_map.items():
            old_label = config["topics"].get(old_key, {}).get("label", old_key)
            print(f"  · 从「{old_label}」({old_key}) 删除：{', '.join(kws)}")
    else:
        print("\n[关键词去重] 无跨主题重复关键词，无需处理。")

    # 重新加载 config，走同步流程
    config = cfg.config()
    print("\n[主题同步] 开始同步（重算标签 + Zotero 归集 + Obsidian 目录）…")
    changed, vault, zr = sync_after_topics_change(config)
    print(f"  · 重算标签：{changed} 篇论文发生变化")
    print(f"  · Obsidian 目录：{vault / '03-Topics'}")
    if zr:
        added_n, failed_n, ok = zr
        if ok:
            print(f"  · Zotero 归集：入库 {added_n} 成功 / {failed_n} 失败")
        else:
            print("  · Zotero 本地 API 不可用（集合/目录已更新，Zotero 归集请重跑）")

    print("\n[主题同步] 完成。")


if __name__ == "__main__":
    main()
