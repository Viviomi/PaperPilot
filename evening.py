"""晚间研究点评：读取 Obsidian 精读笔记的「我的判断」，生成点评与下一步行动。

用法：
    python evening.py
可配合 Windows 任务计划在每天晚间运行，与早上的 main.py 形成闭环。
"""

from paperpilot import config as cfg
from paperpilot import feedback


def main():
    config = cfg.config()
    n, path = feedback.run_evening(config)
    print(f"晚间点评：处理 {n} 篇笔记")
    print(f"报告：{path}")


if __name__ == "__main__":
    main()
