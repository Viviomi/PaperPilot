"""晚间点评 + 日报 + 邮件：读取当天新增的「我的判断」，点评并生成日报发送到邮箱。

对应每日 23:00 的定时任务（Action3 + Action4 + 发邮件）。

用法：
    python night_review.py              # 点评当天新增笔记 + 生成日报 + 发邮件
    python night_review.py --dry-run    # 只生成日报并打印，不发送邮件
    python night_review.py --no-email   # 生成日报但不发邮件
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paperpilot import config as cfg, daily_state, db
from paperpilot import feedback, notify

LEARNING_EVAL_SYSTEM = """你是一名严谨的科研导师。根据用户今天的精读论文与点评，评估他的学习情况。

输出 JSON，字段：
- strengths：今天表现好的方面（字符串列表，可空）
- weaknesses：比较薄弱的方面（字符串列表，必填，每条一句话、具体可执行）
- knowledge_gaps：需要补充的知识/方法（字符串列表，必填，每条具体到方向）
- advice：给用户的一句话建议

严格规则：只基于给定材料，不编造；如果材料太少，明确指出信息不足。只输出 JSON。"""


def _weekday_cn(dt):
    return ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六",
            "星期日"][dt.weekday()]


def _rebuild_workbench():
    """点评后自动重新生成工作台，让「已点评」计数等统计保持最新。"""
    try:
        import _build_workbench as bw
        bw.rebuild()
    except Exception as exc:  # noqa: BLE001
        print(f"[工作台] 重新生成失败（可稍后手动点「生成工作台」）：{exc}")


def _today_notes(vault_path):
    """返回当天（文件修改时间）有实质「我的判断」内容的文献卡列表。"""
    today = datetime.now().strftime("%Y-%m-%d")
    out = []
    for n in feedback.scan_notes(vault_path):
        try:
            mday = datetime.fromtimestamp(
                Path(n["path"]).stat().st_mtime).strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            continue
        if mday == today:
            out.append(n)
    return out


def _evaluate_learning(read_list, reviews, config):
    """Action4：调用 LLM 评估当天学习情况。返回 dict 或 None。"""
    api_key = cfg.env("LLM_API_KEY", "")
    if not api_key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key,
                        base_url=cfg.env("LLM_BASE_URL", "https://api.openai.com/v1"),
                        timeout=120.0, max_retries=1)
        user = json.dumps({
            "今天精读的论文": [
                {"标题": r.get("title"), "主题": r.get("topic")} for r in read_list],
            "今天的点评": [
                {"标题": n.get("title"), "我的判断": n.get("judgment"), "点评": rv}
                for n, rv in reviews],
        }, ensure_ascii=False)
        resp = client.chat.completions.create(
            model=cfg.env("LLM_MODEL", "gpt-4.1-mini"),
            messages=[{"role": "system", "content": LEARNING_EVAL_SYSTEM},
                      {"role": "user", "content": user}],
            temperature=0.3, response_format={"type": "json_object"})
        txt = resp.choices[0].message.content
        try:
            return json.loads(txt)
        except Exception:
            m = __import__("re").search(r"\{.*\}", txt, __import__("re").DOTALL)
            return json.loads(m.group(0)) if m else None
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def build_daily_report(collect, read_list, reviews, eval_result):
    now = datetime.now()
    lines = [f"#日报： {now.strftime('%Y/%m/%d %H:%M:%S')}  {_weekday_cn(now)}",
             ""]

    # Action1：论文库更新情况
    lines.append("### Action1：（论文库更新情况）")
    if collect:
        lines.append(f"- 今日发现候选论文 {collect.get('found', 0)} 篇，"
                     f"新增入库 {collect.get('new', 0)} 篇。")
        per_venue = collect.get("per_venue") or {}
        if per_venue:
            lines.append("- 各会议/期刊更新：")
            for v, n in sorted(per_venue.items(), key=lambda x: -x[1]):
                lines.append(f"  - {v}：{n}")
    else:
        lines.append("- 今日未运行采集（无更新记录）。")
    lines.append("")

    # Action2：精读论文名称列表
    lines.append("### Action2：（精读论文名称列表）")
    if read_list:
        for i, r in enumerate(read_list, 1):
            topic = r.get("topic") or ""
            lines.append(f"{i}. [{topic}] {r.get('title')}（{r.get('citekey')}）")
    else:
        lines.append("- 今日未精读。")
    lines.append("")

    # Action3：精读论文点评情况
    lines.append("### Action3：（精读论文点评情况）")
    if reviews:
        for n, rv in reviews:
            lines.append(f"- **{n.get('title')}**")
            for k, v in (n.get("judgment") or {}).items():
                lines.append(f"  - {k}：{v}")
            if rv is None:
                lines.append("  - 点评：未配置 LLM，跳过")
            elif "error" in rv:
                lines.append(f"  - 点评失败：{rv['error']}")
            else:
                pairs = (("opinion_quality", "观点质量"), ("logic_review", "逻辑审查"),
                         ("missed_work", "可能遗漏的研究"), ("promising_ideas", "值得深入的想法"),
                         ("verification", "可执行的核验"), ("next_action", "建议的下一步"))
                for k, lab in pairs:
                    if rv.get(k):
                        lines.append(f"  - 点评·{lab}：{rv[k]}")
    else:
        lines.append("- 今日无新增笔记，未点评。")
    lines.append("")

    # Action4：学习情况评价
    lines.append("### Action4：（对我学习情况的评价：哪些方面比较薄弱、哪些方面知识需要增加等）")
    if eval_result and "error" not in eval_result:
        strengths = eval_result.get("strengths") or []
        weaknesses = eval_result.get("weaknesses") or []
        gaps = eval_result.get("knowledge_gaps") or []
        if strengths:
            lines.append("- 表现好的方面：")
            lines += [f"  - {s}" for s in strengths]
        if weaknesses:
            lines.append("- 比较薄弱的方面：")
            lines += [f"  - {s}" for s in weaknesses]
        if gaps:
            lines.append("- 需要补充的知识/方法：")
            lines += [f"  - {s}" for s in gaps]
        if eval_result.get("advice"):
            lines.append(f"- 建议：{eval_result['advice']}")
    else:
        lines.append("- （未配置 LLM 或今日无数据，暂无法评价）")
    lines.append("")

    return "\n".join(lines)


def run(dry_run=False, no_email=False):
    config = cfg.config()
    db.init_db()
    vault_path = config.get("obsidian", {}).get("vault_path") or "obsidian_vault"

    # 1. 当天新增的「我的判断」笔记
    notes = _today_notes(vault_path)
    print(f"当天有「我的判断」新增内容的笔记：{len(notes)} 篇")
    for n in notes:
        print(f"  - {n['citekey']}")

    # 2. 逐篇点评
    reviews = []
    for n in notes:
        rv = feedback._review_note(n)
        reviews.append((n, rv))
        if rv is None:
            print(f"  点评 {n['citekey']}：未配置 LLM，跳过")
        elif "error" in rv:
            print(f"  点评 {n['citekey']}：失败 {rv['error']}")
        else:
            print(f"  点评 {n['citekey']}：完成")

    # 3. 读当天状态（采集 + 早间精读）
    st = daily_state.load()
    collect = st.get("collect")
    read_list = st.get("read") or []

    # 4. Action4 学习情况评价（有数据才调 LLM）
    eval_result = None
    if read_list or reviews:
        eval_result = _evaluate_learning(read_list, reviews, config)

    # 5. 生成日报
    report = build_daily_report(collect, read_list, reviews, eval_result)
    out_dir = cfg.REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"evening_daily_{datetime.now().strftime('%Y-%m-%d')}.md"
    out.write_text(report, encoding="utf-8")
    print(f"\n日报：{out}")

    if dry_run:
        print("=" * 60)
        print(report)
        print("[dry-run] 未发送邮件。")
        return

    # 6. 发邮件
    sent = False
    if not no_email and config.get("notifications", {}).get("email_enabled"):
        subject = f"PaperPilot 日报 {datetime.now().strftime('%Y-%m-%d')}"
        sent = notify.send_email(subject, report)
        print(f"[邮件] {'发送成功' if sent else '发送失败（请检查 .env 的 SMTP 配置）'}")
    else:
        print("[邮件] 未启用（notifications.email_enabled=false 或 --no-email）")

    # 7. 回写状态（供次日/排查）
    daily_state.update(review=[
        {"citekey": n["citekey"], "title": n["title"], "judgment": n["judgment"]}
        for n in notes])
    print("[晚间点评] 完成。")

    # 8. 重新生成工作台（点评可能改变「已点评」状态）
    _rebuild_workbench()


def main():
    ap = argparse.ArgumentParser(description="晚间点评 + 日报 + 邮件")
    ap.add_argument("--dry-run", action="store_true",
                    help="只生成日报并打印，不发送邮件")
    ap.add_argument("--no-email", action="store_true",
                    help="生成日报但不发邮件")
    args = ap.parse_args()
    run(dry_run=args.dry_run, no_email=args.no_email)


if __name__ == "__main__":
    main()
