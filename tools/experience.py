r"""
体验环境 —— 有界面的交付在发「完成」之前，在本地把它启动好，你点链接就能体验。
验收 Agent 的 accept-test skill（体验准备）用它；单任务派发没有验收 Agent 时，执行会话自己用。

    python tools/experience.py start <名字> --cwd <项目目录> --cmd "<启动命令>" --url http://127.0.0.1:5173/
    python tools/experience.py start <名字> --url file:///C:/.../index.html     # 静态页面：不用启动，只检查文件在
    python tools/experience.py list                  # 本机开着的体验环境（到期的标出来）
    python tools/experience.py stop <名字>           # 你「验收通过」或「打回」后关掉
    python tools/experience.py stop --expired        # 关掉所有过了到期时间的

- 启动命令在后台跑（不占当前终端），输出写进日志；等地址能打开才算启动成功，超时就关掉并给出日志末尾
- 每个环境默认保留到当天 24:00：start 时同时起一个看门狗，到点自动 stop（提前 stop 了就什么都不做）
- 记录放在 inbox/experience/<名字>.json（本机状态，不进 git）
- 只用于本地和测试数据，地址只监听本机；不要拿它连生产
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from common import ROOT, setup_stdio

STATE = ROOT / "inbox" / "experience"


def record_path(name):
    return STATE / f"{name}.json"


def load(name):
    f = record_path(name)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def midnight():
    return (datetime.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def is_up(url):
    """地址能打开就算起来了：file:// 看文件在不在；http 只要有响应（5xx 除外）。"""
    if url.startswith("file:"):
        p = urllib.request.url2pathname(re.sub(r"^file:(//)?", "", url))
        return Path(p).exists() or Path(p.lstrip("/")).exists()
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except (urllib.error.URLError, OSError, ValueError):
        return False


def spawn(cmd, cwd, log, detach_args=None):
    """后台起一个进程，和当前会话脱钩（会话关了它还在）。"""
    kw = {"cwd": cwd, "stdin": subprocess.DEVNULL}
    if log:
        kw["stdout"] = kw["stderr"] = open(log, "a", encoding="utf-8")
    else:
        kw["stdout"] = kw["stderr"] = subprocess.DEVNULL
    if os.name == "nt":
        # 自己的隐藏控制台（DETACHED_PROCESS 会让子进程的输出丢掉）
        base = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        try:
            return subprocess.Popen(cmd, shell=isinstance(cmd, str), creationflags=base | 0x01000000, **kw)  # 0x01000000：脱离 Orca 终端的作业对象
        except OSError:
            return subprocess.Popen(cmd, shell=isinstance(cmd, str), creationflags=base, **kw)
    return subprocess.Popen(cmd, shell=isinstance(cmd, str), start_new_session=True, **kw)


def kill(pid):
    if not pid:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            pass


def card(rec, ok=True):
    """体验卡：「完成」消息和待验收卡片要用的几行。"""
    stop = f"python {ROOT.as_posix()}/tools/experience.py stop {rec['name']}"
    if ok:
        return "\n".join([
            f"- 体验链接：{rec['url']}",
            (f"- 环境：已启动，{rec['started']} 起，到期 {rec['expires']} 自动关" if rec.get("pid")
             else "- 环境：静态页面，不用启动，直接打开链接"),
            f"- 关闭：{stop}（你验收通过或打回后执行）",
            f"- 日志：{rec.get('log') or '—'}",
        ])
    return "\n".join([
        f"- 体验环境启动不了：{rec.get('error', '')}",
        f"- 手动启动：在 {rec.get('cwd') or '—'} 里运行 `{rec.get('cmd') or '—'}`，然后打开 {rec['url']}",
        f"- 日志：{rec.get('log') or '—'}",
    ])


def cmd_start(a):
    STATE.mkdir(parents=True, exist_ok=True)
    if load(a.name):
        stop(a.name, quiet=True)  # 同名的旧环境先关掉，避免占着端口
    now = datetime.now()
    rec = {"name": a.name, "url": a.url, "cmd": a.cmd or "", "cwd": str(Path(a.cwd).resolve()) if a.cwd else "",
           "started": f"{now:%Y-%m-%d %H:%M}", "expires": f"{midnight():%Y-%m-%d %H:%M}", "token": f"{now:%Y%m%d%H%M%S%f}",
           "pid": 0, "log": ""}
    if a.cmd:
        rec["log"] = (STATE / f"{a.name}.log").as_posix()
        proc = spawn(a.cmd, rec["cwd"] or None, rec["log"])
        rec["pid"] = proc.pid
        deadline = time.time() + a.timeout
        while time.time() < deadline and not is_up(a.url):
            if proc.poll() is not None:
                break
            time.sleep(2)
    if not is_up(a.url):
        kill(rec["pid"])
        tail = ""
        if rec["log"] and Path(rec["log"]).exists():
            tail = "\n".join(Path(rec["log"]).read_text(encoding="utf-8", errors="replace").splitlines()[-15:])
        rec["error"] = f"{a.timeout} 秒内打不开 {a.url}" if a.cmd else f"文件不存在 {a.url}"
        print("❌ 体验环境启动不了（照样发「完成」，把下面几行写进去，不要跳过）\n" + card(rec, ok=False))
        if tail:
            print("日志末尾：\n" + tail)
        sys.exit(1)
    if rec["pid"]:  # 看门狗：到 24:00 自动关；提前 stop 时一起关掉
        secs = int((midnight() - now).total_seconds())
        rec["watchdog"] = spawn([sys.executable, str(Path(__file__).resolve()), "stop", a.name, "--after", str(secs),
                                 "--token", rec["token"]], str(ROOT), None).pid
    record_path(a.name).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    print("✅ 体验环境已就绪\n" + card(rec))


def stop(name, token=None, quiet=False):
    rec = load(name)
    if not rec or (token and rec.get("token") != token):
        if not quiet:
            print(f"没有开着的体验环境 {name}")
        return False
    kill(rec.get("pid"))
    if not token:  # 不是看门狗自己来关的：把看门狗也关掉
        kill(rec.get("watchdog"))
    record_path(name).unlink(missing_ok=True)
    if not quiet:
        print(f"✅ 已关掉体验环境 {name}（{rec['url']}）")
    return True


def cmd_stop(a):
    if a.after:
        time.sleep(a.after)
    if a.expired:
        now = f"{datetime.now():%Y-%m-%d %H:%M}"
        names = [f.stem for f in STATE.glob("*.json") if json.loads(f.read_text(encoding="utf-8"))["expires"] <= now] if STATE.is_dir() else []
        for n in names:
            stop(n)
        if not names:
            print("没有过期的体验环境")
        return
    if not a.name:
        sys.exit("要关哪个？给名字，或者 --expired")
    stop(a.name, a.token)


def cmd_list(a):
    files = sorted(STATE.glob("*.json")) if STATE.is_dir() else []
    if not files:
        print("本机没有开着的体验环境")
        return
    now = f"{datetime.now():%Y-%m-%d %H:%M}"
    for f in files:
        r = json.loads(f.read_text(encoding="utf-8"))
        state = "已过期" if r["expires"] <= now else ("能打开" if is_up(r["url"]) else "打不开")
        print(f"- {r['name']}：{r['url']} · {state} · {r['started']} 起，到期 {r['expires']} · pid {r.get('pid') or '—'}")


def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="体验环境")
    sub = ap.add_subparsers(dest="op", required=True)
    p = sub.add_parser("start")
    p.add_argument("name", help="环境名，一般用 <项目>-<需求>")
    p.add_argument("--url", required=True, help="你要打开的地址（http://127.0.0.1:… 或 file:///…）")
    p.add_argument("--cmd", help="启动命令；静态页面不用")
    p.add_argument("--cwd", help="在哪个目录里运行启动命令")
    p.add_argument("--timeout", type=int, default=120, help="等地址能打开的秒数")
    p = sub.add_parser("stop")
    p.add_argument("name", nargs="?")
    p.add_argument("--expired", action="store_true")
    p.add_argument("--after", type=int, default=0, help=argparse.SUPPRESS)
    p.add_argument("--token", help=argparse.SUPPRESS)
    sub.add_parser("list")
    a = ap.parse_args()
    {"start": cmd_start, "stop": cmd_stop, "list": cmd_list}[a.op](a)


if __name__ == "__main__":
    main()
