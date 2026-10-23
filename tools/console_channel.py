r"""
assistant 指挥台的本地通道（v1.1 P3）：只听 127.0.0.1 的一个小 HTTP 服务，给出能「发送给主会话」的指挥台页面，
接收卡片上的回复，记进回复分表并推给主会话（tools/notify.py）。

    python tools/console_channel.py ensure [--wait 1.5] [--quiet]   # 会话启动钩子和 start.ps1 调：已在跑就复用，没在跑就拉起
    python tools/console_channel.py url                              # 「打开指挥台」：确保在跑并打印 http://127.0.0.1:<端口>/
    python tools/console_channel.py stop                             # 「关掉指挥台」：只结束状态文件里记着、health 对得上的那个进程
    python tools/console_channel.py status                           # 排查：状态文件、health、是不是这份 assistant
    python tools/console_channel.py serve --port <n>                 # 内部：ensure 拉起的后台进程入口

边界：
- 只在 assistant 主目录里拉起（git 的 git-dir 等于 common-dir），派发出去的 worktree 副本不起；通道读写的始终是它所在那份 assistant
- 只绑定 127.0.0.1；首选端口 17318，被占（别的程序或另一份 assistant）就顺延到 17327；状态文件 inbox/logs/console-channel.json 只在本机
- 令牌每次启动随机生成，只在进程内存和通道给出的页面数据节点里；不写状态文件、不写日志
- 每个请求先校验 Host 严格等于 127.0.0.1:<端口>；提交还要 Origin 是通道自己、令牌对、JSON 类型；没有 CORS
- 只有四个路由：GET /、GET /health、GET /api/replies、POST /api/replies；其余 404 空响应
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import common
import console_contract as cc
from common import ROOT, setup_stdio

APP = "assistant-console"
VERSION = 2
HOST = "127.0.0.1"
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
MESSAGES = {
    "bad_host": "只接受 127.0.0.1 上本通道的地址", "bad_origin": "来源不是本地通道，拒绝",
    "stale": "通道重启过，刷新页面后重发", "bad_type": "只接受页面自己的提交格式", "too_large": "提交内容太大",
    "bad_json": "提交格式不对", "empty": "快捷评价和正文至少要有一个", "need_text": "这个决定要写一句原因",
    "too_long": f"正文超过 {cc.REPLY_MAX_CHARS} 字", "bad_card": "找不到这张卡片（数据可能已经变了，刷新页面）",
    "not_replyable": "这张卡片不接收回复", "other_device": "这件事在另一台设备上跑，请到那台设备的面板上发",
    "bad_choice": "快捷评价不是这张卡片的选项", "bad_lines": "指令不合格或引用的任务 / 角色不存在",
    "sensitive": "疑似敏感内容，没发出去", "rate_limited": f"每分钟最多 {cc.REPLY_PER_MINUTE} 条，稍后再发",
    "not_found": "没有这个地址", "method": "不支持的请求", "internal": "通道内部出错，刷新页面后重试",
    "busy": "记录正被另一个会话写，没记上，过几秒再发",
}


# ---------------------------------------------------------------- 状态文件、锁、主目录判断

def logs_dir(root=ROOT) -> Path:
    return Path(root) / "inbox" / "logs"


def state_path(root=ROOT) -> Path:
    return logs_dir(root) / "console-channel.json"


def root_id(root=ROOT) -> str:
    text = str(Path(root).resolve())
    if os.name == "nt":
        text = text.lower()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def read_state(root=ROOT) -> dict | None:
    try:
        data = json.loads(state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_state(root, data: dict) -> None:
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def clear_state(root=ROOT, instance: str | None = None) -> None:
    state = read_state(root)
    if state is None or instance is None or state.get("instance") == instance:
        try:
            state_path(root).unlink()
        except OSError:
            pass


class Lock:
    """排你文件锁（同 notify.Lock）：同时开两个会话也只起一个通道。

    等到 deadline（单调时钟）还拿不到就抛 notify.LockBusy，不进入；只删自己的锁；60 秒以上视为残留（R4）。
    """

    def __init__(self, root=ROOT, deadline: float | None = None):
        self.path = logs_dir(root) / "console-channel.lock"
        self.deadline = deadline
        self.inner = None

    def __enter__(self):
        import notify
        wait = 5.0 if self.deadline is None else max(0.0, self.deadline - time.monotonic())
        self.inner = notify.Lock(self.path, wait=wait).__enter__()
        return self

    def __exit__(self, *exc):
        if self.inner is not None:
            self.inner.__exit__(*exc)
            self.inner = None


def is_main_checkout(root=ROOT, timeout: float = 10) -> tuple[bool | None, str]:
    """(True, 说明) = assistant 主目录；(False, 说明) = 派发出去的 worktree 副本；(None, 说明) = 判断不了（不拉起）。"""
    root = Path(root)
    out = common.run(["git", "-C", str(root), "rev-parse", "--git-dir", "--git-common-dir"], timeout=timeout)
    if out is None:
        if not (root / ".git").exists():
            return True, "不是 git 仓库，按主目录处理"
        return None, "git 判断失败，没法确定是不是主目录，不拉起通道"
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    if len(lines) < 2:
        return None, "git 判断失败，没法确定是不是主目录，不拉起通道"
    git_dir, common_dir = ((root / line).resolve() for line in lines[:2])
    if git_dir == common_dir:
        return True, "assistant 主目录"
    return False, "派发出去的工作区副本，不拉起指挥台通道"


# ---------------------------------------------------------------- 探测与拉起

def health(port: int, timeout: float = 0.3) -> dict | None:
    """GET /health（直连，不走代理）；不是本通道或连不上返回 None。"""
    conn = http.client.HTTPConnection(HOST, port, timeout=timeout)
    try:
        conn.request("GET", "/health", headers={"Host": f"{HOST}:{port}"})
        resp = conn.getresponse()
        body = resp.read(4096)
        if resp.status != 200:
            return {"app": None}
        data = json.loads(body.decode("utf-8"))
        return data if isinstance(data, dict) else {"app": None}
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        conn.close()


def port_free(port: int) -> bool:
    """这个端口现在能不能绑（试绑一下马上放开）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind((HOST, port))
            return True
        except OSError:
            return False


def parse_ports(text: str | None) -> tuple[int, ...]:
    if not text:
        return cc.CHANNEL_PORTS
    start, _, end = text.partition("-")
    first, last = int(start), int(end or start)
    if not 1024 <= first <= last <= 65535 or last - first > 50:
        raise ValueError("端口段不对")
    return tuple(range(first, last + 1))


def _spawn(root: Path, port: int) -> subprocess.Popen:
    """拉起后台进程：拉起它的会话退出、终端关掉也不受影响。"""
    exe = Path(sys.executable)
    if os.name == "nt" and (exe.parent / "pythonw.exe").exists():
        exe = exe.parent / "pythonw.exe"
    cmd = [str(exe), "-B", str(Path(__file__).resolve()), "serve", "--port", str(port)]
    if Path(root).resolve() != ROOT.resolve():
        cmd += ["--root", str(Path(root).resolve())]
    env = {k: v for k, v in os.environ.items() if k not in ("ORCA_TERMINAL_HANDLE", "CLAUDE_CODE_SESSION_ID")}
    log = logs_dir(root) / "console-channel.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    if log.exists() and log.stat().st_size > 1024 * 1024:
        log.write_text("", encoding="utf-8")
    out = open(log, "a", encoding="utf-8")
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": out, "stderr": out, "cwd": str(root), "env": env, "close_fds": True}
    try:
        if os.name == "nt":
            flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                     | getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:  # 钩子可能在作业对象里：先试脱离作业，被拒就不脱离再起
                return subprocess.Popen(cmd, creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0),
                                        **kwargs)
            except OSError:
                return subprocess.Popen(cmd, creationflags=flags, **kwargs)
        return subprocess.Popen(cmd, start_new_session=True, **kwargs)
    finally:
        out.close()


def _log_tail(root) -> str:
    try:
        lines = (logs_dir(root) / "console-channel.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return lines[-1][:200] if lines else ""


ENSURE_SLACK = 0.35  # ensure 的总时限 = wait + 这么多秒（钩子默认 wait 1.5 → 连同收尾不超过 2 秒，AC-32）


def ensure(root=ROOT, *, wait: float = 1.5, ports=None) -> dict:
    """确保通道在跑。返回 {ok, url, port, reused, reason}；任何情况都不抛异常。

    总时限从入口算（单调时钟）：git 判断、复用检查、拿锁、逐端口 health、等启动都用剩下的预算，
    来不及就返回降级原因（面板改为复制指令），不超过 wait + ENSURE_SLACK 秒（R4）。
    """
    deadline = time.monotonic() + max(0.0, wait) + ENSURE_SLACK
    root = Path(root)
    chosen = None

    def left(cap: float) -> float:
        return max(0.05, min(cap, deadline - time.monotonic()))

    def out_of_time() -> bool:
        return time.monotonic() >= deadline

    def reusable(state, me):
        if not state:
            return None
        h = health(int(state.get("port", 0) or 0), timeout=left(0.3))
        if h and h.get("app") == APP and h.get("rootId") == me and h.get("instance") == state.get("instance"):
            return {"ok": True, "url": state["url"], "port": state["port"], "reused": True, "reason": ""}
        return None

    try:
        main, why = is_main_checkout(root, timeout=left(max(0.0, wait) + ENSURE_SLACK))
        if not main:
            return {"ok": False, "url": None, "reason": why}
        ports = ports or cc.CHANNEL_PORTS
        me = root_id(root)
        # 先不拿锁看一眼：已经在跑就直接复用（另一个会话正拿着锁也不用等）
        hit = reusable(read_state(root), me)
        if hit:
            return hit
        import notify
        try:
            lock = Lock(root, deadline=deadline)
            lock.__enter__()
        except notify.LockBusy:
            return {"ok": False, "url": None, "reason": "另一个会话正在拉起通道，稍后再看（面板先用复制指令）"}
        try:
            state = read_state(root)
            hit = reusable(state, me)
            if hit:
                return hit
            if state:
                clear_state(root)
            for port in ports:
                if out_of_time():
                    return {"ok": False, "url": None, "reason": "探测端口超时，面板改为复制指令"}
                h = health(port, timeout=left(0.3))
                if h and h.get("app") == APP and h.get("rootId") == me:
                    # 状态文件丢了但通道还在：补写状态，复用
                    write_state(root, {k: h.get(k) for k in ("pid", "port", "url", "rootId", "instance", "startedAt",
                                                              "version", "device")})
                    return {"ok": True, "url": h.get("url"), "port": port, "reused": True, "reason": ""}
                if h is not None or not port_free(port):
                    continue  # 别的程序或另一份 assistant 占着
                chosen = port
                break
            if chosen is None:
                return {"ok": False, "url": None,
                        "reason": f"端口 {ports[0]}～{ports[-1]} 都被占用，面板改为复制指令"}
            proc = _spawn(root, chosen)
            proc.returncode = 0  # 后台进程有意脱离：不等它结束，也不让 Popen 析构时报「还在跑」
            while not out_of_time():
                h = health(chosen, timeout=left(0.2))
                if h and h.get("app") == APP and h.get("rootId") == me:
                    return {"ok": True, "url": h.get("url"), "port": chosen, "reused": False, "reason": ""}
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        finally:
            lock.__exit__(None, None, None)
        tail = _log_tail(root)
        return {"ok": False, "url": f"http://{HOST}:{chosen}/", "port": chosen,
                "reason": "通道启动中" + (f"（{tail}）" if tail else "")}
    except Exception as exc:  # noqa: BLE001 —— 会话启动不能因为通道挂掉
        return {"ok": False, "url": None, "reason": f"通道没起来：{type(exc).__name__}"}


def stop(root=ROOT) -> str:
    state = read_state(root)
    if not state:
        return "指挥台通道没在跑（没有状态文件）"
    h = health(int(state.get("port", 0)))
    if not h or h.get("instance") != state.get("instance") or h.get("rootId") != root_id(root):
        clear_state(root)
        return "记着的通道已经不在了，清掉了旧记录"
    try:
        os.kill(int(state["pid"]), signal.SIGTERM)
    except (OSError, ValueError, KeyError) as exc:
        return f"结束通道进程失败：{type(exc).__name__}"
    clear_state(root)
    return f"已关掉指挥台通道（{state.get('url')}）；下次在主目录启动会话会再起来"


# ---------------------------------------------------------------- 服务

def records_version(root=ROOT) -> str:
    """页面之外的记录（运行记录、待处理清单、当日计划、角色）有没有变：文件名 + 修改时间 + 大小的摘要。

    回复分表不算（回复状态由轮询自动更新）；页面拿生成时的版本，轮询时比对，不同就提示「记录有更新 · 刷新」。
    """
    root = Path(root)
    entries = []
    for folder, pattern in (("runs", "*.md"), ("plans/daily", "*.md"), ("agents", "*.md")):
        base = root / folder
        try:
            files = sorted(base.glob(pattern))
        except OSError:
            continue
        for path in files:
            if path.name.lower() == "replies.md":
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            entries.append(f"{folder}/{path.name}:{st.st_mtime_ns}:{st.st_size}")
    return hashlib.sha256("|".join(entries).encode("utf-8")).hexdigest()[:16]


def _local_device() -> dict | None:
    try:
        import device
        fp = device.fingerprint()
        name = None
        try:
            name = (device.load_device(fp) or {}).get("hostname")
        except Exception:  # noqa: BLE001
            name = None
        return {"id": fp, "name": name}
    except Exception:  # noqa: BLE001
        return None


class ChannelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, port: int, *, root=ROOT, environment=None, now=None):
        super().__init__((HOST, port), ChannelHandler)
        self.root = Path(root)
        self.port = self.server_address[1]
        self.url = f"http://{HOST}:{self.port}/"
        self.origin = f"http://{HOST}:{self.port}"
        self.token = secrets.token_urlsafe(32)
        self.instance = secrets.token_hex(8)
        self.started = datetime.now().astimezone().isoformat(timespec="seconds")
        self.recent = deque()
        self.rate_lock = threading.Lock()
        self._environment = environment  # 测试注入；生产按需探测并缓存 60 秒
        self._env_cache = (0.0, None)
        self._device = (environment or {}).get("device") if environment else _local_device()
        self._now = now

    # -- 数据
    def environment(self) -> dict:
        if self._environment is not None:
            return self._environment
        at, cached = self._env_cache
        if cached is None or time.time() - at > 60:
            import env as env_module
            info = env_module.detect(probe=True)
            cached = {"inOrca": bool(info.get("in_orca") or info.get("orca_reachable")),
                      "executorsAvailable": [a for a in info.get("agents_available") or [] if a in cc.EXECUTOR_IDS]}
            if self._device and self._device.get("id"):
                cached["device"] = dict(self._device)
            self._env_cache = (time.time(), cached)
        return cached

    def snapshot(self) -> dict:
        import console_data
        now = self._now() if callable(self._now) else datetime.now().astimezone()
        return console_data.load_snapshot(self.root, now=now, environment=self.environment())

    def channel_info(self) -> dict:
        return {"mode": "channel", "url": self.url, "token": self.token, "instance": self.instance,
                "limits": cc.reply_limits(), "recordsVersion": records_version(self.root)}

    def health_info(self) -> dict:
        return {"app": APP, "version": VERSION, "rootId": root_id(self.root), "instance": self.instance,
                "pid": os.getpid(), "port": self.port, "url": self.url, "startedAt": self.started,
                "device": self._device}

    def allow_rate(self) -> bool:
        now = time.time()
        with self.rate_lock:
            while self.recent and now - self.recent[0] > 60:
                self.recent.popleft()
            if len(self.recent) >= cc.REPLY_PER_MINUTE:
                return False
            self.recent.append(now)
            return True


class ChannelHandler(BaseHTTPRequestHandler):
    server_version = "assistant"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # 日志只记方法、路径（不带查询串）、状态码；不记头和正文（令牌在头里）
    def log_request(self, code="-", size="-"):
        path = urlsplit(self.path).path if isinstance(self.path, str) else "-"
        sys.stderr.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {self.command} {path[:80]} {code}\n")

    def log_message(self, format, *args):  # noqa: A002 —— 只留固定说明，不回显请求内容
        sys.stderr.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} 请求被拒或出错\n")

    def _send(self, status: int, body: bytes = b"", ctype: str = "application/json; charset=utf-8", extra=None):
        self.send_response(status)
        headers = {"Content-Type": ctype, "Content-Length": str(len(body)), "Cache-Control": "no-store",
                   "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                   "X-Frame-Options": "DENY", "Cross-Origin-Resource-Policy": "same-origin"}
        headers.update(extra or {})
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict):
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _error(self, code: str):
        self._json(cc.REPLY_ERROR_CODES.get(code, 500), {"ok": False, "code": code, "message": MESSAGES.get(code, "")})

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") == f"{HOST}:{self.server.port}"

    def _token_ok(self) -> bool:
        given = self.headers.get("X-Assistant-Token", "")
        return bool(given) and hmac.compare_digest(given.encode("utf-8"), self.server.token.encode("utf-8"))

    def do_GET(self):
        if not self._host_ok():
            return self._error("bad_host")
        parts = urlsplit(self.path)
        if parts.path == "/":
            return self._page()
        if parts.path == "/health":
            return self._json(200, self.server.health_info())
        if parts.path == "/api/replies":
            if not self._token_ok():
                return self._error("stale")
            return self._list(parse_qs(parts.query).get("cards", [""])[0])
        return self._send(404)

    def do_POST(self):
        if not self._host_ok():
            return self._error("bad_host")
        if urlsplit(self.path).path != "/api/replies":
            return self._send(404)
        if self.headers.get("Origin", "") != self.server.origin:
            return self._error("bad_origin")
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            return self._error("bad_type")
        if not self._token_ok():
            return self._error("stale")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._error("bad_json")
        if length < 0 or length > cc.REPLY_MAX_BODY_BYTES:
            return self._error("too_large")
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return self._error("bad_json")
        if not isinstance(body, dict) or not cc.utf8_ok(body):
            return self._error("bad_json")  # 孤立代理码点等编不成 UTF-8 的输入：入口就拒，不碰分表（R1）
        if not self.server.allow_rate():
            return self._error("rate_limited")
        import notify
        try:
            return self._reply(body)
        except notify.LockBusy:
            return self._error("busy")  # 等不到清单锁：没写入，页面提示稍后重发（R3）
        except Exception:  # noqa: BLE001 —— 内部错误只给固定说明
            return self._error("internal")

    def do_PUT(self):
        return self._error("method")

    do_DELETE = do_PATCH = do_OPTIONS = do_PUT

    # -- 路由实现
    def _page(self):
        import console_render
        try:
            html = console_render.render_page(self.server.snapshot(), channel=self.server.channel_info())
        except Exception as exc:  # noqa: BLE001
            return self._send(500, f"指挥台生成失败：{type(exc).__name__}".encode("utf-8"), "text/plain; charset=utf-8")
        return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8", {"Content-Security-Policy": CSP})

    def _list(self, cards_param: str):
        import console_data
        path = cc.checked_source(self.server.root, "runs/replies.md", "replies")
        replies = []
        if path is not None:
            env = self.server.environment()
            local = (env.get("device") or {}).get("id") if isinstance(env.get("device"), dict) else None
            replies, _ = console_data.parse_replies(path.read_text(encoding="utf-8", errors="replace"),
                                                    root=self.server.root, local_id=local)
        wanted = {c for c in cards_param.split(",") if c} if cards_param else None
        if wanted is not None:
            replies = [r for r in replies if r.get("card") in wanted]
        return self._json(200, {"ok": True, "replies": replies[:cc.REPLY_MAX_ITEMS],
                                "recordsVersion": records_version(self.server.root),
                                "serverTime": datetime.now().astimezone().isoformat(timespec="seconds")})

    def _reply(self, body: dict):
        import notify
        parsed = cc.parse_card_key(body.get("card"))
        if parsed is None:
            return self._error("bad_card")
        page, ident = parsed
        snapshot = self.server.snapshot()
        quick_id, text, lines = body.get("quick"), body.get("text"), body.get("lines")
        if page in ("dispatch", "roles"):
            if not isinstance(lines, list) or not 1 <= len(lines) <= cc.REPLY_MAX_LINES or not all(
                    cc.check_console_command(line, snapshot) for line in lines):
                return self._error("bad_lines")
            if page == "dispatch" and not all("确认派发" in line for line in lines):
                return self._error("bad_lines")
            if page == "roles" and not all("】角色：" in line for line in lines):
                return self._error("bad_lines")
            row, dup = notify.record_reply(page=page, item="指挥台：" + cc.REPLY_PAGE_LABELS[page],
                                           association="—", lines=lines)
        else:
            items = snapshot.get("pending" if page == "pending" else "runs") or []
            item = next((x for x in items if x.get("id") == ident), None)
            if item is None:
                return self._error("bad_card")
            slot = item.get("reply")
            if slot is None:
                return self._error("not_replyable")
            code = cc.validate_reply_input(slot, quick_id, text)
            if code:
                return self._error(code)
            label = next((c["label"] for c in slot["choices"] if c["id"] == quick_id), "") if quick_id else ""
            subject = f"{item.get('project') or '项目未记录'}：{item.get('item') or '事项未记录'}"
            row, dup = notify.record_reply(page=page, item=subject, association=slot["association"],
                                           quick=label, text=(text or "").strip())
        if dup:
            current = notify.find_reply(row["编号"]) or row
            status, _ = cc.reply_status_of(current["状态"])
            return self._json(200, {"ok": True, "id": row["编号"], "status": status, "duplicate": True,
                                    "message": f"同样的内容刚发过 · 编号 {row['编号']}"})
        result = notify.deliver_reply(row, quick_check=True)
        if result.startswith("已推送"):
            return self._json(200, {"ok": True, "id": row["编号"], "status": "delivered", "duplicate": False,
                                    "message": f"已送达主会话 · 编号 {row['编号']}"})
        if not result.startswith("未推送：没有"):
            # 主会话在打字 / 一时看不到输入框：后台再等最多 40 秒推一次，推不进去就留给你提交时转达
            threading.Thread(target=notify.deliver_reply, args=(row,), daemon=True).start()
        return self._json(200, {"ok": True, "id": row["编号"], "status": "waiting", "duplicate": False,
                                "message": f"已记清单，主会话能收时转 · 编号 {row['编号']}"})


def _bind_notify(root: Path) -> None:
    """通道服务的不是工具所在目录时（测试 / 冒烟用 --root），回复分表等也跟着写到那个根下。"""
    import notify
    notify.ROOT = root
    notify.PENDING = root / "runs" / "pending.md"
    notify.REPLIES = root / "runs" / "replies.md"
    notify.STATE = root / "inbox" / "logs" / "main-sessions.json"
    notify.LOCK = root / "inbox" / "logs" / "pending.lock"


def serve(port: int, root=ROOT) -> int:
    root = Path(root)
    if root.resolve() != ROOT.resolve():
        _bind_notify(root)
    try:
        server = ChannelServer(port, root=root)
    except OSError as exc:
        print(f"{datetime.now():%Y-%m-%d %H:%M:%S} 绑定 {HOST}:{port} 失败：{type(exc).__name__}", file=sys.stderr)
        return 1
    info = server.health_info()
    write_state(root, {k: info[k] for k in ("pid", "port", "url", "rootId", "instance", "startedAt", "version", "device")})
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} 指挥台通道已起：{server.url}", file=sys.stderr)
    if root.resolve() == ROOT.resolve():
        # 顺手重写本机文件页面，让里面的「去本地通道」是当前地址（失败不影响通道）
        def regenerate():
            try:
                import console
                console.run([])
            except BaseException:  # noqa: BLE001
                pass
        threading.Thread(target=regenerate, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        clear_state(root, server.instance)
        server.server_close()
    return 0


def main(argv=None) -> int:
    setup_stdio()
    ap = argparse.ArgumentParser(description="assistant 指挥台本地通道")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ensure")
    p.add_argument("--wait", type=float, default=1.5)
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--ports", help=argparse.SUPPRESS)
    p.add_argument("--root", help=argparse.SUPPRESS)
    for name in ("url", "stop", "status"):
        q = sub.add_parser(name)
        q.add_argument("--root", help=argparse.SUPPRESS)
        q.add_argument("--ports", help=argparse.SUPPRESS)
    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--root", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    root = Path(a.root).resolve() if getattr(a, "root", None) else ROOT
    if a.cmd == "serve":
        return serve(a.port, root)
    ports = parse_ports(getattr(a, "ports", None))
    if a.cmd == "ensure":
        r = ensure(root, wait=a.wait, ports=ports)
        if not a.quiet:
            print(("指挥台：" + r["url"] + ("（复用）" if r.get("reused") else "（新起）")) if r["ok"] else
                  ("指挥台通道没起来：" + r["reason"]))
        return 0
    if a.cmd == "url":
        r = ensure(root, ports=ports)
        print(r["url"] if r["ok"] else "指挥台通道没起来：" + r["reason"])
        return 0 if r["ok"] else 1
    if a.cmd == "stop":
        print(stop(root))
        return 0
    main_ok, why = is_main_checkout(root)
    state = read_state(root)
    h = health(int(state.get("port", 0))) if state else None
    print(f"目录：{why}\n状态文件：{json.dumps(state, ensure_ascii=False) if state else '没有'}\n"
          f"health：{'在跑' if h and h.get('instance') == (state or {}).get('instance') else '不在'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
