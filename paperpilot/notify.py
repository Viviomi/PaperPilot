"""通知：邮件 / 飞书推送（均需在 .env 配置凭据）。"""

import smtplib
from email.header import Header
from email.mime.text import MIMEText

import httpx

from .config import env


def send_email(subject, body):
    host = env("SMTP_HOST")
    if not host:
        return False
    port = int(env("SMTP_PORT", "465"))
    user = env("SMTP_USER")
    password = env("SMTP_PASSWORD")
    to = env("SMTP_TO")

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = user
    msg["To"] = to

    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()
        server.login(user, password)
        server.sendmail(user, [to], msg.as_string())
        server.quit()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[邮件] 发送失败: {exc}")
        return False


def send_feishu(text):
    webhook = env("FEISHU_WEBHOOK_URL")
    if not webhook:
        return False
    # 飞书文本消息上限约 2000 字，超长截断
    payload = {
        "msg_type": "text",
        "content": {"text": text[:1900]},
    }
    try:
        r = httpx.post(webhook, json=payload, timeout=30)
        return r.status_code == 200
    except Exception as exc:  # noqa: BLE001
        print(f"[飞书] 发送失败: {exc}")
        return False
