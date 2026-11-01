"""每日定时任务（需本机保持开机）：
- 00:00 采集新论文并入库 Zotero（Action1）
- 07:00 每个 topic 选一篇未精读论文精读并写入 Obsidian（Action2）
- 23:00 点评当天笔记 + 生成日报 + 发邮件（Action3 + Action4）

更稳的方式：用 Windows 任务计划程序分别调用
    python main.py --collect-only
    python morning_read.py
    python night_review.py
"""

from apscheduler.schedulers.blocking import BlockingScheduler
from zoneinfo import ZoneInfo

from main import run_collect_only
from morning_read import run as run_morning_read
from night_review import run as run_night_review
from paperpilot.config import config


def main():
    cfg = config()
    tz = cfg["app"].get("timezone", "Asia/Shanghai")
    sc = cfg.get("schedule", {}) or {}

    def _get(k, default):
        return int(sc.get(k, default))

    collect_hour, collect_minute = _get("collect_hour", 0), _get("collect_minute", 0)
    read_hour, read_minute = _get("read_hour", 7), _get("read_minute", 0)
    review_hour, review_minute = _get("review_hour", 23), _get("review_minute", 0)

    scheduler = BlockingScheduler(timezone=ZoneInfo(tz))
    scheduler.add_job(
        run_collect_only, trigger="cron",
        hour=collect_hour, minute=collect_minute,
        id="collect", max_instances=1, coalesce=True, misfire_grace_time=3600)
    scheduler.add_job(
        run_morning_read, trigger="cron",
        hour=read_hour, minute=read_minute,
        id="morning_read", max_instances=1, coalesce=True, misfire_grace_time=3600)
    scheduler.add_job(
        run_night_review, trigger="cron",
        hour=review_hour, minute=review_minute,
        id="night_review", max_instances=1, coalesce=True, misfire_grace_time=3600)

    print("PaperPilot 定时器已启动：")
    print(f"  - 每天 {collect_hour:02d}:{collect_minute:02d}  采集入库 Zotero")
    print(f"  - 每天 {read_hour:02d}:{read_minute:02d}  早间精读写 Obsidian")
    print(f"  - 每天 {review_hour:02d}:{review_minute:02d}  晚间点评 + 日报邮件")
    print(f"时区：{tz}")
    scheduler.start()


if __name__ == "__main__":
    main()
