"""tools 共用的小工具：仓库根目录、命令执行、注册表读取、本地 git 仓库扫描。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "registry" / "projects.yaml"
SEP = chr(31)  # git pretty 字段分隔符
UI_TOKENS = ROOT / "tools" / "ui-tokens.css"  # 视觉规范：问卷、原型、评审页共用

# 扫描本地仓库时跳过的目录名（以 . 开头的目录也一律跳过）
SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", "dist", "build",
             ".gradle", ".m2", "AppData", "Library", "$RECYCLE.BIN",
             "System Volume Information", "Program Files", "Program Files (x86)",
             "Windows", "ProgramData"}


def setup_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def ui_tokens():
    """视觉规范（六级字号、间距、圆角、浅色深色配色），生成 HTML 时原样内嵌。"""
    return UI_TOKENS.read_text(encoding="utf-8").strip()


def run(cmd, cwd=None, timeout=60):
    """跑命令, 返回 stdout; 失败返回 None 而不抛异常。"""
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.stdout if p.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def norm_remote(url):
    """https://github.com/Org/Repo.git -> Org/Repo"""
    if not url:
        return ""
    u = url.strip().rstrip("/").removesuffix(".git")
    for pre in ("https://github.com/", "http://github.com/", "git@github.com:",
                "ssh://git@github.com/"):
        u = u.removeprefix(pre)
    return u


def origin_of(path):
    """本地目录的 origin（Org/Repo 形式）；不是 git 仓库或没有 origin 返回空串。"""
    if not Path(path).is_dir():
        return ""
    return norm_remote(run(["git", "remote", "get-url", "origin"], cwd=str(path)) or "")


def load_registry():
    with open(REGISTRY, encoding="utf-8") as f:
        return yaml.safe_load(f)


def repo_keys(repo):
    """一个仓库的所有可匹配名（小写）：github 主名 + aliases（改名前的旧名等）。"""
    keys = {norm_remote(repo.get("github", "")).lower()}
    keys |= {norm_remote(a).lower() for a in repo.get("aliases") or []}
    keys.discard("")
    return keys


def tracked_repos(reg, active_only=False):
    """遍历 tracked 下的 (工作面, 仓库)。"""
    for entry in reg.get("tracked") or []:
        if active_only and entry.get("status", "active") != "active":
            continue
        for repo in entry.get("repos") or []:
            yield entry, repo


def find_repos(root, max_depth):
    """广度优先找含 .git 目录的仓库, 严格限制深度; 找到仓库就不再往下钻。

    不用 shell 的 find —— Windows 上那会解析到 FIND.EXE;
    也不用 rglob —— 那会把整个盘走一遍。
    """
    repos = []
    frontier = [(Path(root), 0)]
    while frontier:
        d, depth = frontier.pop()
        try:
            entries = list(os.scandir(d))
        except (PermissionError, OSError):
            continue
        if any(e.name == ".git" and e.is_dir() for e in entries):
            repos.append(d)
            continue  # 不进入仓库内部找嵌套仓库
        if depth >= max_depth:
            continue
        for e in entries:
            if e.name in SKIP_DIRS or e.name.startswith("."):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    frontier.append((Path(e.path), depth + 1))
            except OSError:
                continue
    return repos


# ---------- 外发总开关 ----------
# ASSISTANT_NO_SEND=1 时，所有向外发消息 / 起会话的出口只打印「已拦截外发」、返回成功、不发网络请求不碰终端。
# tests/ 公共入口（tests/_isolation.py）默认设上；跑测试、演示、干跑时都应带上。
NO_SEND_ENV = "ASSISTANT_NO_SEND"
# orca 子命令里算「外发」的：往终端写字、拉起 agent。其余（list / show / read / worktree …）不属于消息外发
ORCA_OUTWARD = {("terminal", "send"), ("agent", "launch")}


def no_send():
    """总开关是否打开：ASSISTANT_NO_SEND 设了且不是 0 / false / no / off / 空。"""
    return os.environ.get(NO_SEND_ENV, "").strip().lower() not in ("", "0", "false", "no", "off")


def blocked(what):
    """开关打开时打印一行「已拦截外发」并返回 True，调用方据此直接按成功返回；没打开返回 False。"""
    if not no_send():
        return False
    print(f"已拦截外发（{NO_SEND_ENV}=1）：{what}", file=sys.stderr)
    return True


def orca_blocked(args):
    """orca 命令参数是外发类（terminal send / agent launch）且开关打开时，返回替身结果，否则返回 None。"""
    if tuple(args[:2]) in ORCA_OUTWARD and blocked("orca " + " ".join(args[:2])):
        return {"ok": True, "blocked": True, "result": {}}
    return None
