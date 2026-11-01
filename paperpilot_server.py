"""PaperPilot 本地控制服务：让工作台（paperpilot_workbench.html）的按钮能一键
触发本项目的各种 Python 脚本，并实时回传日志。

纯标准库实现，无第三方依赖。

用法：
    python paperpilot_server.py            # 默认 http://127.0.0.1:8899
    python paperpilot_server.py --port 8900
    python paperpilot_server.py --no-browser   # 不自动打开浏览器

接口：
    GET  /             返回工作台 HTML（带操作按钮）
    GET  /api/health   健康检查
    GET  /api/actions  动作清单
    POST /api/run      执行动作；body: {"action": "...", "args": {...}}
                       响应为 chunked 流，逐行回传脚本 stdout/stderr，
                       最后一行形如 {"__done__": true, "code": 0}
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKBENCH = ROOT / "paperpilot_workbench.html"


def _find_python():
    """找一个带全依赖（yaml/httpx/openai 等）的解释器来跑子进程。

    问题背景：本机有多个 python（miniconda 主环境、.workbuddy managed、以及
    各种 conda env），启动服务时 shell 里激活的是哪个，sys.executable 就指向
    哪个，未必装齐了项目依赖。这里按优先级探测「能 import yaml」的解释器。

    优先级：
      1. 环境变量 PAPERPILOT_PYTHON（显式指定，最优先）
      2. miniconda 主环境 python.exe（已知全依赖）
      3. 当前 sys.executable（若它本身能 import yaml）
      4. 其它常见位置探测
    """
    candidates = []
    env_py = os.environ.get("PAPERPILOT_PYTHON", "").strip()
    if env_py and Path(env_py).exists():
        candidates.append(env_py)

    home = Path.home()
    candidates += [
        str(home / "miniconda3" / "python.exe"),
        str(home / "anaconda3" / "python.exe"),
        sys.executable,
    ]

    seen = set()
    for py in candidates:
        if not py or py in seen:
            continue
        seen.add(py)
        p = Path(py)
        if not p.exists():
            continue
        try:
            r = subprocess.run(
                [py, "-c", "import yaml"],
                capture_output=True, timeout=15,
            )
            if r.returncode == 0:
                return py
        except Exception:
            continue
    # 兜底：至少返回当前解释器
    return sys.executable


PYTHON = _find_python()

# 动作白名单：action -> (脚本, 默认参数列表, 说明)
ACTIONS = {
    "collect": {
        "script": "main.py",
        "args": [],
        "label": "全量采集",
        "desc": "从 arXiv / OpenAlex / Crossref / GitHub / OpenReview 采集新论文并分析",
        "confirm": "将联网采集新论文并调用大模型深度分析，耗时较长（可能数十分钟）。确定执行？",
    },
    "collect_test": {
        "script": "_collect_test.py",
        "args": [],
        "label": "采集·小规模测试",
        "desc": "仅采集 arXiv 单一数据源（约 20% 规模），跳过 LLM 分析，快速验证采集链路",
        "confirm": "",
    },
    "ingest_list": {
        "script": "ingest_one.py",
        "args": ["--list"],
        "label": "单篇入库·列出待处理",
        "desc": "列出 Zotero 里刚加入、尚未入库的条目",
        "confirm": "",
    },
    "ingest": {
        "script": "ingest_one.py",
        "args": [],
        "label": "单篇入库·精读",
        "desc": "针对 Zotero 里临时新增的一篇论文做入库 + 深度精读（可填标题关键词定位）",
        "confirm": "将针对指定论文做入库 + 深度分析（会调用大模型）。确定执行？",
        "params": [
            {"key": "title", "label": "标题关键词", "placeholder": "留空则交互选择，如 domain adaptation", "flag": "--title", "type": "text"},
            {"key": "all", "label": "处理所有待入库条目", "flag": "--all", "type": "bool"},
        ],
    },
    "read_paper": {
        "script": "read_paper.py",
        "args": [],
        "label": "单篇精读（库内）",
        "desc": "对库中已有论文做单篇精读并写入 Obsidian；已精读过则直接返回现有笔记路径",
        "confirm": "将对指定论文做深度精读（会调用大模型，单篇约 40-50 秒）。确定执行？",
        "params": [
            {"key": "key", "label": "论文定位", "placeholder": "paper_key / citekey / id / 标题关键词", "flag": "--key", "type": "text"},
            {"key": "force", "label": "强制重新精读", "flag": "--force", "type": "bool"},
        ],
    },
    "retag_dryrun": {
        "script": "retag_topics.py",
        "args": ["--dry-run"],
        "label": "重算标签·预览",
        "desc": "预览重算主题/关键词标签会改变哪些论文（不写库）",
        "confirm": "",
    },
    "retag": {
        "script": "retag_topics.py",
        "args": [],
        "label": "重算标签·执行",
        "desc": "重算全库主题/关键词标签并写库",
        "confirm": "将重算全库论文的主题/关键词标签。确定执行？",
    },
    "zotero_back_dryrun": {
        "script": "sync_zotero_back.py",
        "args": ["--dry-run"],
        "label": "Zotero 反向同步·预览",
        "desc": "预览 Zotero 里新增条目会补录哪些文献卡（不写入）",
        "confirm": "",
    },
    "zotero_back": {
        "script": "sync_zotero_back.py",
        "args": ["--yes"],
        "label": "Zotero 反向同步·执行",
        "desc": "把 Zotero 新增条目补录成 Obsidian 文献卡",
        "confirm": "将把 Zotero 新增条目补录成文献卡。确定执行？",
    },
    "topic_sync_dryrun": {
        "script": "sync_topics_from_zotero.py",
        "args": ["--dry-run"],
        "label": "主题反向同步·预览",
        "desc": "预览 Zotero 集合与 config.yaml 主题的差异（不改文件）",
        "confirm": "",
    },
    "topic_sync": {
        "script": "sync_topics_from_zotero.py",
        "args": ["--yes"],
        "label": "主题反向同步·执行",
        "desc": "用 Zotero 集合反向更新 config.yaml 主题（增删改）",
        "confirm": "将用 Zotero 集合反向更新 config.yaml 主题。确定执行？",
    },
    "build_workbench": {
        "script": "_build_workbench.py",
        "args": [],
        "label": "生成工作台",
        "desc": "从数据库重新生成 paperpilot_workbench.html",
        "confirm": "",
    },
    "evening": {
        "script": "evening.py",
        "args": [],
        "label": "晚间点评",
        "desc": "读取 Obsidian 精读笔记，生成点评与下一步行动",
        "confirm": "",
    },
}


def _actions_meta():
    """返回给前端展示的动作清单（不含脚本路径等内部信息）。"""
    out = []
    for key, a in ACTIONS.items():
        out.append({
            "key": key,
            "label": a["label"],
            "desc": a["desc"],
            "confirm": a.get("confirm", ""),
            "params": a.get("params", []),
        })
    return out


def _build_command(action_key, payload_args):
    """根据动作与前端传入的参数，构造子进程命令 (list[str])。"""
    a = ACTIONS[action_key]
    cmd = [PYTHON, "-X", "utf8", str(ROOT / a["script"])] + list(a["args"])
    for p in a.get("params", []):
        key, flag = p["key"], p["flag"]
        val = (payload_args or {}).get(key)
        if p.get("type") == "bool":
            if val:
                cmd.append(flag)
        else:
            if val and str(val).strip():
                cmd.append(flag)
                cmd.append(str(val).strip())
    return cmd


def _run_stream(action_key, payload_args, emit):
    """在子进程里跑命令，逐行回调 emit(line)。"""
    cmd = _build_command(action_key, payload_args)
    emit(f"$ {' '.join(cmd)}\n")
    emit("-" * 60 + "\n")

    # 用 UTF-8，忽略 Windows 控制台编码差异；合并 stderr 到 stdout
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,  # 避免脚本进入交互 input() 挂起服务
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        bufsize=1,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    # 逐行读取并回调（用线程避免阻塞）
    for line in proc.stdout:
        emit(line)

    proc.wait()
    emit("\n" + "-" * 60 + "\n")
    emit(f"[完成] 退出码 {proc.returncode}\n")
    return proc.returncode


class Handler(BaseHTTPRequestHandler):
    server_version = "PaperPilotLocal/1.0"

    def log_message(self, fmt, *args):  # 精简日志
        sys.stderr.write("[%s] %s\n" % (self.address_string(), fmt % args))

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status=200):
        if WORKBENCH.exists():
            body = WORKBENCH.read_bytes()
        else:
            body = (f"<!doctype html><meta charset=utf-8><body>工作台尚未生成。"
                    f"请先运行 build_workbench 动作。</body>").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "/index.html":
            self._send_html()
        elif path == "/api/health":
            self._send_json({"ok": True, "time": time.time(),
                             "python": PYTHON})
        elif path == "/api/actions":
            self._send_json({"actions": _actions_meta()})
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path != "/api/run":
            self._send_json({"error": "not found"}, status=404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            data = {}
        action = data.get("action", "")
        args = data.get("args", {})

        if action not in ACTIONS:
            self._send_json({"error": f"未知动作: {action}"}, status=400)
            return

        # 流式响应（chunked）：逐行回传
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def emit(line):
            if getattr(self, "_client_gone", False):
                return  # 客户端已断连，不再尝试写
            try:
                data = line.encode("utf-8")
                self.wfile.write(("%x\r\n" % len(data)).encode("ascii"))
                self.wfile.write(data + b"\r\n")
                self.wfile.flush()
            except (ConnectionAbortedError, BrokenPipeError,
                    ConnectionResetError, OSError):
                # 客户端（浏览器标签页/curl）中途断开：标记并静默，避免刷屏
                self._client_gone = True

        try:
            code = _run_stream(action, args, emit)
            emit(json.dumps({"__done__": True, "code": code},
                            ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001
            emit(f"\n[错误] {exc}\n")
            emit(json.dumps({"__done__": True, "code": -1},
                            ensure_ascii=False) + "\n")
        finally:
            try:
                if not getattr(self, "_client_gone", False):
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser(description="PaperPilot 本地控制服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--no-browser", action="store_true",
                    help="不自动打开浏览器")
    args = ap.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"PaperPilot 控制服务已启动：{url}")
    print(f"工作台文件：{WORKBENCH}")
    print("按 Ctrl+C 停止。")

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
