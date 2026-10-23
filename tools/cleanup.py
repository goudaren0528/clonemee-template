r"""
清理 —— 盘出可以清理的会话（Orca 终端）和工作区（Orca 工作区 / git worktree），你确认后关终端、删工作区、删已合并的分支（cleanup skill）。

    python tools/cleanup.py                          # 清单：按项目列出可以清理的会话和工作区，有风险的单独列（只读）
    python tools/cleanup.py --brief                  # 一行「可以清理：N 个会话、M 个工作区」（sweep 快照、开场卡片、夜间巡检用）
    python tools/cleanup.py --for runs/<运行记录>.md   # 只看这个派发（运行记录或交付看板）的会话和工作区，按「已结束」算（跟进 Agent 收尾时用）
    python tools/cleanup.py --run                    # 你确认后执行：清掉「可以清理」的全部
    python tools/cleanup.py --run --only k1,k2 --skip k3 --also k4   # 只清几项 / 跳过几项 / 「要问你」里你点了头的一起清（键见表格最后一列）
    python tools/cleanup.py unlock <目录>            # 删不掉的目录：按进程当前目录找出占着它的进程（只读）
    python tools/cleanup.py unlock <目录> --kill     # 结束这些进程，再删目录、去掉 Orca 登记

盘什么：
  会话   派发已结束（运行记录 done / failed / abandoned）的执行、跟进、流水线会话；收编的、回报已处理的会话；
         没有 agent 在跑、30 分钟没输出的空终端；团队编排已结束的 worker（worker-release，Orca 会先存档输出）
  工作区 Orca 记着删除失败（removalError）或目录已经没了的；派发已结束的；分支已合进 main / master / develop / 起点分支的
每项标 已合并 / 有未推送提交 / 有未提交改动 / 有被忽略的文件 / 还在干活；后四种不进「可以清理」，单独列出来问你（--also 点名才清）。
被忽略的文件：.gitignore 挡住、不算未提交改动，删工作区会跟着没（日志、测试证据）；可重新生成的（node_modules、dist、__pycache__ 这类）不算。
宽限期：只因「运行记录结束」能清的，结束后 15 分钟（closed_at，老记录用文件修改时间）内不清，留给跟进 Agent / 主会话再用；单列「宽限期内」。
fetch：盘点前每个涉及的仓库 git fetch --quiet origin 一次（30 秒超时，失败只提示不挡），「已推送」按最新的远端引用算；--brief 为了快不 fetch。
主会话、当前会话、没结束的派发里登记的会话和工作区不碰；会话退出了但派发还没结束的只提示（sweep / 跟进 Agent 去重启）。

删工作区：关掉里面的终端 → orca worktree rm --force（Orca 会顺手删能证明已合并的分支）→ 目录还在就按进程当前目录（读 PEB）找占用者：
OpenCode 后台服务（opencode serve）起的 MCP 子进程（各类 MCP 服务器）直接结束，其他进程列出来不动 → 再删一次 →
核实过已合并、还留着的分支删掉 → git worktree prune。
在任务工作区里试跑时用 --data <assistant 主目录>，读那边的运行记录、待处理清单和主会话登记。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import env
import notify
from common import ROOT, setup_stdio
from dispatch import orca
from follow import fields
from sweep import handles_of, project_of_path

ENDED = ("done", "failed", "abandoned")
IDLE_MIN = 30  # 空终端：多少分钟没输出才算
BUSY_MIN = 10  # 多少分钟内有输出算还在干活
PROMPT = re.compile(r"^(PS [A-Za-z]:\\.*>|[A-Za-z]:\\[^>]*>|\S*@\S*[:\s].*\$)\s*$")  # 终端停在 shell 提示符：agent 已经退出
BASES = ("main", "master", "develop")
FLAGS = ("有未推送提交", "有未提交改动", "有被忽略的文件", "还在干活")  # 带这几种的不自动清
GRACE_MIN = 15  # 运行记录结束后多少分钟内不清（刚关的工作区被定时清理删掉会让跟进来不及用，所以留一段宽限）
FETCH_SEC = 30  # 每个仓库 git fetch 的超时
# 被忽略但可以重新生成的：不算「有被忽略的文件」
REGEN_DIRS = {"node_modules", ".next", ".nuxt", "dist", "build", "out", "coverage", ".turbo", ".vercel", "__pycache__", ".venv", "venv",
              ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".cache", ".parcel-cache"}
REGEN_PATHS = {".assistant/handoff",  # 交接包：assistant 原件的只读副本
               "inbox/logs"}  # assistant 工作区里每个会话的 hook 日志
REGEN_FILE = re.compile(r"(\.pyc|\.pyo|\.tsbuildinfo|\.egg-info|^\.DS_Store|^Thumbs\.db|^\.eslintcache)$", re.I)


def norm(p):
    return os.path.normcase(os.path.normpath(str(p or ""))).rstrip("\\/") if p else ""


def git(path, *args, timeout=60):
    try:
        r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        return r.returncode, (r.stdout or "").strip() or (r.stderr or "").strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


def short(path, n=2):
    return "/".join(str(path).replace("\\", "/").rstrip("/").split("/")[-n:])


def ago(ms):
    return (time.time() * 1000 - int(ms or 0)) / 60000 if ms else 1e9


def errmsg(res):
    e = res.get("error")
    return str(e.get("message") or e) if isinstance(e, dict) else str(e or "")


def label(stem, fm):
    """派发的短名：任务号（T15）或需求名，都没有就用文件名。"""
    return (fm.get("task") or "").split("#")[-1] or fm.get("req") or stem[11:]


def ended_reason(stem, fm):
    return f"派发收尾（{label(stem, fm)}）" if fm.get("_closing") else f"派发已结束（{label(stem, fm)} · {fm.get('status')}）"


def grace_left(fm):
    """运行记录结束后还剩几分钟宽限：读 closed_at（follow.py --close、delivery.py finish 写的），老记录没有就用文件修改时间。"""
    try:
        t = datetime.strptime(fm.get("closed_at") or "", "%Y-%m-%d %H:%M").timestamp()
    except ValueError:
        t = fm.get("_mtime") or 0
    return max(0, math.ceil(GRACE_MIN - (time.time() - t) / 60))


def idle_reason(t):
    m = ago(t.get("lastOutputAt"))
    return f"空终端（没有 agent 在跑，{int(m // 60)} 小时没输出）" if m >= 60 else f"空终端（没有 agent 在跑，{int(m)} 分钟没输出）"


def mains_at(data):
    """data 目录登记的主会话终端（tools/notify.py 写的 inbox/logs/main-sessions.json）。"""
    try:
        state = json.loads((data / "inbox" / "logs" / "main-sessions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {v.get("handle") for v in (state.get("sessions") or {}).values() if v.get("kind") == "main"}


# ---------- 进程：按当前目录找占用者（Windows 读 PEB） ----------

class _PE(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32), ("th32ProcessID", ctypes.c_uint32),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", ctypes.c_uint32), ("cntThreads", ctypes.c_uint32),
                ("th32ParentProcessID", ctypes.c_uint32), ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_uint32),
                ("szExeFile", ctypes.c_wchar * 260)]


def processes():
    """本机所有进程：{pid: {ppid, exe, cwd, cmd}}。cwd / cmd 从进程 PEB 的 ProcessParameters 读（64 位进程；读不到的留空）。"""
    if os.name != "nt":
        return {}
    k32, ntdll = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("ntdll")
    k32.CreateToolhelp32Snapshot.restype = k32.OpenProcess.restype = ctypes.c_void_p
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    ntdll.NtQueryInformationProcess.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]

    def read(h, addr, n):
        buf, got = ctypes.create_string_buffer(n), ctypes.c_size_t()
        return buf.raw if k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, n, ctypes.byref(got)) else None

    def ustr(h, raw):  # UNICODE_STRING：Length(2) MaximumLength(2) 对齐(4) Buffer(8)
        if not raw:
            return ""
        n, ptr = int.from_bytes(raw[0:2], "little"), int.from_bytes(raw[8:16], "little")
        b = read(h, ptr, n) if n and ptr else None
        return b.decode("utf-16-le", "replace") if b else ""

    def peb(pid):
        h = k32.OpenProcess(0x1000 | 0x0010, False, pid)  # QUERY_LIMITED_INFORMATION | VM_READ
        if not h:
            return "", ""
        try:
            pbi = (ctypes.c_void_p * 6)()
            if ntdll.NtQueryInformationProcess(h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), None) != 0 or not pbi[1]:
                return "", ""
            pp = read(h, pbi[1] + 0x20, 8)  # PEB.ProcessParameters
            params = int.from_bytes(pp, "little") if pp else 0
            if not params:
                return "", ""
            return ustr(h, read(h, params + 0x38, 16)), ustr(h, read(h, params + 0x70, 16))  # CurrentDirectory.DosPath、CommandLine
        finally:
            k32.CloseHandle(h)

    out, snap, e = {}, k32.CreateToolhelp32Snapshot(2, 0), _PE()
    e.dwSize = ctypes.sizeof(_PE)
    ok = k32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        out[e.th32ProcessID] = {"ppid": e.th32ParentProcessID, "exe": e.szExeFile}
        ok = k32.Process32NextW(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    for pid, p in out.items():
        p["cwd"], p["cmd"] = peb(pid) if pid > 4 else ("", "")
    return out


def lockers(path, procs=None):
    """当前目录在 path 里（或下面）的进程：[{pid, exe, cmd, cwd, parent, safe, term, why}]。
    safe：祖先里有 OpenCode 后台服务（opencode serve）——它在 Orca 工作区里起的 MCP 子进程，结束了不影响别的会话（常见的删除失败原因）。
    term：祖先里有 Orca.exe——工作区终端里的进程，关掉终端就会退出。"""
    procs = procs if procs is not None else processes()
    want, me, out = norm(path), os.getpid(), []
    for pid, p in procs.items():
        c = norm(p["cwd"])
        if pid == me or not c or not (c == want or c.startswith(want + os.sep)):
            continue
        chain, cur, seen = [], p["ppid"], set()
        while cur in procs and cur not in seen and len(chain) < 8:
            seen.add(cur)
            chain.append(procs[cur])
            cur = procs[cur]["ppid"]
        serve = next((a for a in chain if "opencode" in a["exe"].lower() and re.search(r"\bserve\b", a["cmd"])), None)
        term = not serve and any(a["exe"].lower() == "orca.exe" for a in chain)
        parent = chain[0]["exe"] if chain else "?"
        why = ("OpenCode 后台服务（opencode serve）起的 MCP 子进程" if serve else "Orca 终端里的进程，关掉工作区的终端就会退出" if term
               else f"父进程 {parent}")
        out.append({"pid": pid, "exe": p["exe"], "cmd": re.sub(r"\s+", " ", p["cmd"])[:160], "cwd": p["cwd"], "parent": parent,
                    "safe": bool(serve), "term": term, "why": why})
    return out


def kill(pid):
    if os.name != "nt":
        return False
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
    if not h:
        return False
    try:
        return bool(k32.TerminateProcess(h, 1))
    finally:
        k32.CloseHandle(h)


# ---------- 数据：运行记录、Orca、git ----------

def load_runs(data):
    """运行记录和交付看板：[(stem, fm, text, 会话 handles, 工作区路径)]。"""
    out = []
    for f in sorted((data / "runs").glob("*.md")):
        if f.name in ("pending.md", "INDEX.md"):
            continue
        text = f.read_text(encoding="utf-8")
        fm = fields(text)
        fm["_mtime"] = f.stat().st_mtime  # 没有 closed_at 的老记录，宽限期按它算
        hs = [h for _, h in handles_of(text, fm)]
        paths = [fm.get(k) for k in ("workdir", "req_worktree") if fm.get(k)]
        paths += re.findall(r"^\|[^|\n]*\|[^|\n]*\|[^|\n]*\|[^|\n]*\| *([A-Za-z]:[/\\][^|]+?) *\|", text, re.M)  # 交付看板工作包表的「工作区」列
        out.append((f.stem, fm, text, hs, [p for p in paths if p]))
    return out


def orca_lists():
    terms = (orca("terminal", "list", "--limit", "300").get("result") or {}).get("terminals", [])
    wts = (orca("worktree", "list").get("result") or {})
    wts = wts.get("worktrees", wts) if isinstance(wts, dict) else wts
    ps = (orca("worktree", "ps", "--limit", "300").get("result") or {}).get("worktrees", [])
    repos = orca("repo", "list").get("result") or {}
    repos = {r["id"]: r["path"] for r in (repos.get("repos", repos) if isinstance(repos, dict) else repos)}
    workers, cursor = [], None
    for _ in range(10):
        res = orca("orchestration", "worker-list", "--terminal-state", "reclaimable", "--limit", "100", *(["--cursor", cursor] if cursor else []))
        r = res.get("result") or {}
        workers += r.get("workers") or []
        cursor = (r.get("page") or {}).get("nextCursor")
        if not cursor:
            break
    return terms, wts, ps, repos, workers


def git_state(path, branch, base):
    """工作区的 git 现状：merged（合进了哪个分支）、unpushed（不在任何远端、也没合进主干的提交数）、dirty（未提交改动数）。"""
    if not Path(path).is_dir():
        return {"gone": True}
    code, refs = git(path, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes")
    refs = set(refs.splitlines()) if not code else set()
    own = (branch or "").removeprefix("refs/heads/")
    names = []
    for b in [base or ""] + list(BASES):
        b = re.sub(r"^(refs/heads/|refs/remotes/origin/|origin/)", "", b)
        if b and b != own and not re.fullmatch(r"[0-9a-f]{7,40}", b) and b not in names:
            names.append(b)
    targets = [r for n in names for r in (f"refs/heads/{n}", f"refs/remotes/origin/{n}") if r in refs]
    merged = next((t.replace("refs/heads/", "").replace("refs/remotes/", "") for t in targets
                   if git(path, "merge-base", "--is-ancestor", "HEAD", t)[0] == 0), "")
    code, n = git(path, "rev-list", "--count", "HEAD", "--not", "--remotes", *targets)
    dirty = [l[2:].strip() for l in git(path, "status", "--porcelain")[1].splitlines() if l.strip()]
    ign = ignored(path)
    return {"merged": merged, "unpushed": 0 if merged or code else int(n or 0), "dirty": len(dirty), "dirty_files": dirty[:2],
            "ignored": len(ign), "ignored_files": ign[:2]}


def regen(rel):
    """被忽略的这一项能不能重新生成（依赖、构建产物、缓存、交接包），能就不算风险。"""
    parts = rel.strip("/").split("/")
    return (any(x in REGEN_DIRS for x in parts) or any(rel.strip("/") == r or rel.startswith(r + "/") for r in REGEN_PATHS)
            or bool(REGEN_FILE.search(parts[-1])))


def ignored(path):
    """工作区里被 .gitignore / info/exclude 挡住、删工作区会跟着没的东西：[相对路径]。
    整个被忽略的目录 git 只报一项（--directory，不递归数文件）；可重新生成的、空文件不算。"""
    code, out = git(path, "-c", "core.quotepath=false", "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "--no-empty-directory")
    if code:
        return []
    keep = []
    for rel in out.splitlines():
        rel = rel.strip().strip('"')
        if not rel or regen(rel):
            continue
        p = Path(path) / rel
        if rel.endswith("/"):
            if any(r.startswith(rel) for r in REGEN_PATHS):  # 上一层目录（.assistant/）：看一层，里面都能重新生成就不算
                try:
                    if all(regen(rel + c.name + "/") for c in os.scandir(p)):
                        continue
                except OSError:
                    pass
        else:
            try:
                if p.stat().st_size == 0:
                    continue
            except OSError:
                continue
        keep.append(rel)
    return keep


def fetch(repo):
    """git fetch --quiet origin：判断「已推送」前把远端引用刷新。没有 origin 的不用 fetch。返回 (成功, 原因)。"""
    code, out = git(repo, "remote")
    if code:
        return False, out[:120]
    if "origin" not in out.split():
        return True, "没有 origin"
    code, out = git(repo, "fetch", "--quiet", "origin", timeout=FETCH_SEC)
    return not code, (out or "").splitlines()[-1][:120] if code and out else ""


def wt_flags(g, busy):
    return ([f"已合并（{g['merged']}）"] if g.get("merged") else []) + ([f"有未推送提交 {g['unpushed']} 个"] if g.get("unpushed") else []) \
        + ([f"有未提交改动 {g['dirty']} 个" + (f"（{'、'.join(g['dirty_files'])}）" if g["dirty"] <= 2 else "")] if g.get("dirty") else []) \
        + ([f"有被忽略的文件 {g['ignored']} 个（{'、'.join(g['ignored_files'])}" + ("…）" if g["ignored"] > 2 else "）")] if g.get("ignored") else []) \
        + (["还在干活"] if busy else [])


# ---------- 盘点 ----------

def inventory(data=ROOT, for_run=None, fetch_remote=False):
    """可以清理的会话和工作区：[item]，item = {kind, key, proj, name, reason, flags, wait, ...}；另返回提示行。
    fetch_remote：先 git fetch 涉及的仓库（cleanup.py 默认要；--brief、sweep 的一行摘要为了快不 fetch，execute 删之前会补）。
    wait：宽限期还剩几分钟（运行记录刚结束），> 0 的不自动清。"""
    terms, wts, ps, repos, workers = orca_lists()
    runs = load_runs(data)
    if for_run:
        stem = Path(for_run).stem
        closing = lambda fm: dict(fm, status="done", _closing=True, **({} if fm.get("status") in ENDED else {"closed_at": f"{datetime.now():%Y-%m-%d %H:%M}"}))
        runs = [(s, closing(fm) if s == stem else fm, t, hs, ps_) for s, fm, t, hs, ps_ in runs]  # 按收尾后算：还没关的从现在起算宽限期
    ended = {h: (s, fm) for s, fm, t, hs, _ in runs if fm.get("status") in ENDED for h in hs}
    open_h = {h: s for s, fm, t, hs, _ in runs if fm.get("status") not in ENDED for h in hs}
    ended_wt = {norm(p): (s, fm) for s, fm, t, hs, paths in runs if fm.get("status") in ENDED for p in paths}
    open_wt = {norm(p): s for s, fm, t, hs, paths in runs if fm.get("status") not in ENDED for p in paths}
    # 收编的会话：派发记录还开着，但回报已经处理过了
    _, rows = notify.read_rows((data / "runs" / "pending.md").read_text(encoding="utf-8") if (data / "runs" / "pending.md").exists() else "")
    handled = {}
    for s, fm, t, hs, _ in runs:
        if fm.get("kind") == "adopted" and fm.get("status") not in ENDED:
            done = [r for r in rows if r["详情"].replace("\\", "/").endswith(f"/runs/{s}.md")]
            if done and all(r["状态"] != "待处理" for r in done):
                for h in hs:
                    handled[h] = (s, done[-1]["编号"])
    me = os.environ.get("ORCA_TERMINAL_HANDLE", "")
    keep = mains_at(data) | ({me} if not for_run else set())  # 只看一个派发时把跟进 Agent 自己也列上（由主会话来关）
    run_proj = {s: fm.get("project") for s, fm, t, hs, _ in runs}
    agents = {a.get("paneKey"): a for w in ps for a in w.get("agents") or []}
    repo_of = {w.get("path"): w.get("repo") for w in ps}
    rel = {}  # 只看这一个派发时：它的会话和工作区
    if for_run:
        stem = Path(for_run).stem
        rel = next(({"h": set(hs), "p": {norm(p) for p in paths}} for s, fm, t, hs, paths in runs if s == stem), {"h": set(), "p": set()})

    def proj(path):
        return project_of_path(path) or repo_of.get(path) or short(path, 1) or "?"

    def tstate(t):
        a = agents.get(f"{t.get('tabId')}:{t.get('leafId')}") or {}
        last = [l for l in (t.get("preview") or "").splitlines() if l.strip()]
        shell = bool(last) and bool(PROMPT.match(last[-1].strip()))
        busy = (a.get("state") == "working" and not shell) or ago(t.get("lastOutputAt")) < BUSY_MIN
        return a, shell, busy

    items, notes = [], []
    live = {t["handle"]: t for t in terms if t.get("handle") and t.get("connected", True)}
    by_wt = {}
    for t in live.values():
        by_wt.setdefault(norm(t.get("worktreePath")), []).append(t)

    # 1. 工作区
    cands = []
    for w in wts:
        p = w.get("path") or ""
        if not p or w.get("isMainWorktree") or norm(p) == norm(ROOT) or (rel and norm(p) not in rel["p"]):
            continue
        if not w.get("removalError") and not w.get("git") and w.get("branch") is None:
            continue  # 不是 git 工作区（文件夹工作区）
        cands.append(w)
    fetched = {}  # 仓库 → ok / failed（没 fetch 的不在里面）
    if fetch_remote:
        groups = {}
        for w in cands:
            if Path(w["path"]).is_dir():
                r = repos.get(w.get("repoId")) or w["path"]
                groups.setdefault(norm(r), r)
        with ThreadPoolExecutor(8) as ex:
            res = list(ex.map(fetch, groups.values()))
        for (k, r), (ok, why) in zip(groups.items(), res):
            fetched[k] = "ok" if ok else "failed"
            if not ok:
                notes.append(f"{short(r, 1)} 仓库 fetch 失败，远端引用可能过期（「有未推送提交」按本地缓存算）" + (f"：{why}" if why else ""))
    with ThreadPoolExecutor(8) as ex:
        states = list(ex.map(lambda w: git_state(w["path"], w.get("branch"), w.get("baseRef")), cands))
    for w, g in zip(cands, states):
        p, np_ = w["path"], norm(w["path"])
        wait = 0
        if w.get("removalError"):
            reason = "删除失败过：" + re.sub(r"\s+", " ", w["removalError"]).split(". ")[-1][:60]
        elif g.get("gone"):
            reason = "目录已经没了，只剩 Orca 登记"
        elif np_ in open_wt:
            continue
        elif np_ in ended_wt:
            s, fm = ended_wt[np_]
            wait = 0 if g.get("merged") else grace_left(fm)  # 分支已合进主干的不用等宽限期
            reason = ended_reason(s, fm)
        elif g.get("merged"):
            reason = f"分支已合进 {g['merged']}"
        else:
            continue
        ts = [t for t in by_wt.get(np_, []) if t["handle"] not in keep]
        busy = any(tstate(t)[2] for t in ts) or np_ in open_wt
        repo = repos.get(w.get("repoId"), "")
        items.append({"kind": "工作区", "key": short(p), "proj": proj(p), "path": p, "repo": repo,
                      "branch": (w.get("branch") or "").removeprefix("refs/heads/"), "base": w.get("baseRef"), "merged": g.get("merged", ""),
                      "name": f"{w.get('displayName') or short(p, 1)} · {(w.get('branch') or '').removeprefix('refs/heads/') or '?'}"
                              + (f"（终端 {len(ts)} 个）" if ts else ""),
                      "reason": reason, "flags": wt_flags(g, busy), "busy": busy, "wait": wait, "terms": [t["handle"] for t in ts],
                      "error": bool(w.get("removalError")), "fetch": fetched.get(norm(repo or p), "")})
    gone_wt = {norm(i["path"]) for i in items if not risky(i) and not i["wait"]}  # 会一起删掉的工作区：里面的终端不用单列

    # 2. 团队编排已结束的 worker
    worker_h = {}
    for wk in workers:
        h = wk.get("agentTerminalHandle")
        if h in live and h not in keep and (not rel or h in rel["h"]):
            worker_h[h] = wk

    # 3. 会话
    for h, t in live.items():
        if h in keep or (rel and h not in rel["h"]) or norm(t.get("worktreePath")) in gone_wt:
            continue
        a, shell, busy = tstate(t)
        busy = busy and h != me  # 收尾时跟进 Agent 自己当然在跑，主会话来清时它已经做完
        title = re.sub(r"^[✳⠀-⣿·\s]+", "", t.get("title") or "")[:40]
        name = f"{title or h[5:13]} · {a.get('agentType') or t.get('agentIdentity') or 'shell'}" + ("（当前会话）" if h == me else "")
        if h in worker_h:
            wk = worker_h[h]
            items.append({"kind": "worker", "key": h[5:13], "proj": proj(t.get("worktreePath")), "handle": h, "dispatch": wk.get("dispatchId"),
                          "name": name, "reason": f"团队编排 worker 已结束（{wk.get('workerState')}）", "flags": ["还在干活"] if busy else []})
            continue
        wait = 0
        if h in ended:
            s, fm = ended[h]
            wait = grace_left(fm)
            reason = ended_reason(s, fm)
            if wait and shell and ago(t.get("lastOutputAt")) >= IDLE_MIN:  # agent 已经退出的空终端不用等宽限期
                wait, reason = 0, idle_reason(t)
        elif h in handled:
            s, nid = handled[h]
            reason = f"收编的会话，回报已处理（{nid}）"
        elif h in open_h:
            if shell and ago(t.get("lastOutputAt")) >= BUSY_MIN:
                notes.append(f"会话退出了、派发还没结束：{name}（{open_h[h]}）——不清理，交给跟进 Agent / sweep 重启")
            continue
        elif shell and ago(t.get("lastOutputAt")) >= IDLE_MIN:
            reason = idle_reason(t)
        else:
            continue
        stem = (ended.get(h) or handled.get(h) or ("",))[0]
        items.append({"kind": "会话", "key": h[5:13], "proj": run_proj.get(stem) or proj(t.get("worktreePath")),
                      "handle": h, "name": name, "reason": reason, "flags": ["还在干活"] if busy else [], "wait": wait})
    for i in items:
        if not i.get("proj"):
            i["proj"] = "?"
    items.sort(key=lambda i: (i["proj"], {"工作区": 0, "会话": 1, "worker": 2}[i["kind"]], i["key"]))
    return items, notes


def risky(i):
    return any(f.split(" ")[0] in FLAGS for f in i["flags"])


def waiting(i):
    """宽限期内（运行记录刚结束）、没有别的风险：不自动清，也不用问。"""
    return bool(i.get("wait")) and not risky(i)


def counts(items):
    ok = [i for i in items if not risky(i) and not waiting(i)]
    n_s = sum(1 for i in ok if i["kind"] != "工作区") + sum(len(i.get("terms") or []) for i in ok if i["kind"] == "工作区")
    return n_s, sum(1 for i in ok if i["kind"] == "工作区"), sum(1 for i in items if risky(i)), sum(1 for i in items if waiting(i))


def brief_line(items):
    n_s, n_w, n_ask, n_wait = counts(items)
    if not (n_s or n_w or n_ask or n_wait):
        return "没有可以清理的会话和工作区"
    return f"可以清理：{n_s} 个会话、{n_w} 个工作区" + (f"；另有 {n_ask} 项要问你（未推送 / 未提交 / 被忽略的文件 / 还在干活）" if n_ask else "") \
        + (f"；{n_wait} 项在宽限期内（派发刚结束，{GRACE_MIN} 分钟后可清）" if n_wait else "")


def render(items, notes, cmd="python tools/cleanup.py"):
    cell = lambda s: str(s).replace("|", "/").replace("\n", " ")
    head = ["| 项目 | 类型 | 对象 | 为什么能清 | 状态 | 键 |", "|---|---|---|---|---|---|"]
    state = lambda i: "、".join(i["flags"] + ([f"宽限期内，还有 {i['wait']} 分钟"] if i.get("wait") else [])) or "—"
    row = lambda i: f"| {cell(i['proj'])} | {i['kind']} | {cell(i['name'])} | {cell(i['reason'])} | {cell(state(i))} | `{i['key']}` |"
    ok = [i for i in items if not risky(i) and not waiting(i)]
    ask, wait = [i for i in items if risky(i)], [i for i in items if waiting(i)]
    out = [f"**{brief_line(items)}**", ""]
    if ok:
        out += head + [row(i) for i in ok] + ["", f"确认后：`{cmd} --run`（只清其中几项加 `--only 键,键`，跳过加 `--skip 键`）", ""]
    if ask:
        out += [f"**要问你：{len(ask)} 项**（有未推送提交 / 未提交改动 / 被忽略的文件 / 还在干活，不会自动清；点头的加 `--also 键`）", ""] \
            + head + [row(i) for i in ask] + [""]
    if wait:
        soon = min(i["wait"] for i in wait)
        out += [f"**宽限期内：{len(wait)} 项**（派发结束不满 {GRACE_MIN} 分钟，留给跟进 Agent / 主会话再用，不自动清；"
                f"{soon} 分钟后可清：到时候再跑 `{cmd} --run`，或等定时清理）", ""] + head + [row(i) for i in wait] + [""]
    if notes:
        out += ["提示：", *[f"- {n}" for n in notes], ""]
    return "\n".join(out).rstrip()


# ---------- 执行 ----------

def wait_gone(path, sec=8):
    for _ in range(sec):
        if not Path(path).exists():
            return True
        time.sleep(1)
    return not Path(path).exists()


def remove_worktree(i, log):
    p = i["path"]
    if i.get("terms"):
        orca("terminal", "close", "--worktree", f"path:{p}", "--all")
    res = orca("worktree", "rm", "--worktree", f"path:{p}", "--force")
    err = "" if res.get("ok") else errmsg(res)[:200]
    if Path(p).exists() and not wait_gone(p):
        lk = lockers(p)
        safe = [x for x in lk if x["safe"]]
        for x in safe:
            kill(x["pid"])
        if safe:
            log.append(f"  结束了 {len(safe)} 个占着目录的 OpenCode MCP 子进程（" + "、".join(f"{x['exe']} {x['pid']}" for x in safe[:6]) + "）")
            time.sleep(1)
            res = orca("worktree", "rm", "--worktree", f"path:{p}", "--force")
            err = "" if res.get("ok") else errmsg(res)[:200]
            if Path(p).exists() and not wait_gone(p, 5) and not [x for x in lockers(p) if not x["safe"]]:
                shutil.rmtree(p, ignore_errors=True)
        if Path(p).exists():
            rest = lockers(p)
            log.append(f"❌ {i['key']}：目录还在（{err or 'Orca 报成功但目录没删掉'}）"
                       + ("；占着它的进程：" + "；".join(f"{x['exe']} {x['pid']}（{x['why']}）{x['cmd'][:80]}" for x in rest[:6])
                          + f"——确认可以结束就跑 python tools/cleanup.py unlock \"{p}\" --kill" if rest else "；没找到占用的进程，可能是杀毒软件或资源管理器开着这个目录"))
            return False
    if err and Path(p).exists():
        log.append(f"❌ {i['key']}：{err}")
        return False
    msg = f"✅ 工作区 {i['key']} 已删"
    repo, br = i.get("repo"), i.get("branch")
    if repo and br and i.get("merged") and git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{br}")[0] == 0:
        target = i["merged"] if i["merged"].startswith("origin/") else f"refs/heads/{i['merged']}"
        if git(repo, "merge-base", "--is-ancestor", f"refs/heads/{br}", target)[0] == 0:
            code, out = git(repo, "branch", "-D", br)
            msg += f"，分支 {br} " + ("已删（核实过已合进 " + i["merged"] + "）" if not code else f"没删掉：{out[:100]}")
    log.append(msg)
    return True


def execute(items, a):
    pick = lambda s: {k.strip() for k in (s or "").split(",") if k.strip()}
    only, skip, also = pick(a.only), pick(a.skip), pick(a.also)
    todo = [i for i in items if (not risky(i) and not i.get("wait") or i["key"] in also) and (not only or i["key"] in only | also) and i["key"] not in skip]
    held = [i for i in items if waiting(i) and i not in todo and i["key"] not in skip and (not only or i["key"] in only)]
    log, repos = [], set()
    # 删工作区之前确认所在仓库 fetch 过：盘点时没 fetch 的（别的工具调进来、--brief）现在补一次，重新核对，冒出风险的不清
    redo = {}
    for i in todo:
        if i["kind"] == "工作区" and not i.get("fetch") and Path(i["path"]).is_dir():
            redo.setdefault(norm(i.get("repo") or i["path"]), []).append(i)
    for group in redo.values():
        r = group[0].get("repo") or group[0]["path"]
        ok, why = fetch(r)
        if not ok:
            log.append(f"⚠ {short(r, 1)} 仓库 fetch 失败，远端引用可能过期，按本地缓存核对" + (f"：{why}" if why else ""))
        for i in group:
            i["fetch"] = "ok" if ok else "failed"
            g = git_state(i["path"], i.get("branch"), i.get("base"))
            i["flags"], i["merged"] = wt_flags(g, i.get("busy")), g.get("merged", "")
            if risky(i) and i["key"] not in also:
                todo.remove(i)
                log.append(f"⏭ 工作区 {i['key']}：fetch 后重新核对，{'、'.join(f for f in i['flags'] if f.split(' ')[0] in FLAGS)}，没清（要清加 --also {i['key']}）")
    if held:
        log.append(f"⏳ 宽限期内没清 {len(held)} 项（{'、'.join(i['key'] for i in held[:4])}），最早 {min(i['wait'] for i in held)} 分钟后可清")
    if not todo:
        print("\n".join(log + ["没有要清的（带风险的要用 --also 点名）"]))
        return
    for i in [i for i in todo if i["kind"] == "worker"]:
        res = orca("orchestration", "worker-release", "--dispatch", i["dispatch"])
        log.append(f"✅ worker {i['key']} 已释放（输出已存档）" if res.get("ok") else f"❌ worker {i['key']}：{errmsg(res)[:200]}")
    for i in [i for i in todo if i["kind"] == "会话"]:
        res = orca("terminal", "close", "--terminal", i["handle"])
        log.append(f"✅ 会话 {i['key']} 已关" if res.get("ok") else f"❌ 会话 {i['key']}：{errmsg(res)[:200]}")
    for i in [i for i in todo if i["kind"] == "工作区"]:
        remove_worktree(i, log)
        if i.get("repo"):
            repos.add(i["repo"])
    for r in sorted(repos):
        git(r, "worktree", "prune")
    if repos:
        log.append(f"✅ git worktree prune：{len(repos)} 个仓库")
    print("\n".join(log))


def cmd_unlock(a):
    p = a.path
    lk = lockers(p)
    if not lk:
        print(f"没有进程的当前目录在 {p} 里" + ("" if Path(p).exists() else "（目录已经不在了）"))
    for x in lk:
        print(f"- {x['pid']} {x['exe']}（{x['why']}）cwd={x['cwd']}\n    {x['cmd']}")
    if not a.kill:
        if lk:
            print(f"\n确认可以结束：python tools/cleanup.py unlock \"{p}\" --kill")
        return
    wt = next((w for w in orca_lists()[1] if norm(w.get("path")) == norm(p)), None)
    if wt and not wt.get("removalError") and git(p, "rev-parse", "--is-inside-work-tree")[0] == 0:
        print(f"{p} 还是有效的工作区，不结束进程、不删目录；要删走 python tools/cleanup.py --run --only {short(p)}")
        return
    if wt and any(x["term"] for x in lk):
        orca("terminal", "close", "--worktree", f"path:{wt['path']}", "--all")
        print("✅ 关掉了这个工作区的 Orca 终端")
        time.sleep(2)
        lk = lockers(p)
    for x in lk:
        print(("✅ 结束 " if kill(x["pid"]) else "❌ 结束不了 ") + f"{x['pid']} {x['exe']}")
    time.sleep(1)
    if wt:
        orca("worktree", "rm", "--worktree", f"path:{p}", "--force")
    if Path(p).exists() and not wait_gone(p, 5):
        shutil.rmtree(p, ignore_errors=True)
    if not Path(p).exists():
        print("✅ 目录已删" + ("，Orca 登记已去掉" if wt else ""))
    else:
        rest = lockers(p)
        print("❌ 目录还在：" + ("；".join(f"{x['exe']} {x['pid']}（{x['why']}）" for x in rest) if rest else "没找到占用的进程，可能是杀毒软件或资源管理器开着这个目录"))


def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="清理做完的会话和工作区")
    ap.add_argument("--brief", action="store_true", help="一行摘要")
    ap.add_argument("--for", dest="for_run", help="只看这个派发（运行记录或交付看板）")
    ap.add_argument("--run", action="store_true", help="执行（你确认后）")
    ap.add_argument("--only", help="只清这几项（键，逗号分隔）")
    ap.add_argument("--skip", help="跳过这几项")
    ap.add_argument("--also", help="「要问你」里你点了头的，一起清")
    ap.add_argument("--data", help="assistant 数据目录（运行记录、主会话登记），默认本仓库")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("unlock", help="按进程当前目录找出占着目录的进程")
    p.add_argument("path")
    p.add_argument("--kill", action="store_true", help="结束这些进程，再删目录")
    a = ap.parse_args()
    if a.cmd == "unlock":
        return cmd_unlock(a)
    if not env.detect()["orca_reachable"]:
        print("连不上 Orca：没法盘会话和工作区" if not a.brief else "")
        return
    data = Path(a.data).resolve() if a.data else ROOT
    for_run = None
    if a.for_run:
        f = Path(a.for_run)
        f = f if f.is_absolute() else data / f
        if not f.exists():
            sys.exit(f"找不到运行记录 {a.for_run}")
        for_run = f
    items, notes = inventory(data, for_run, fetch_remote=not a.brief)  # --brief 为了快不 fetch
    if a.brief:
        print(brief_line(items))
    elif a.run:
        execute(items, a)
    else:
        print(render(items, notes, "python tools/cleanup.py" + (f" --for {a.for_run}" if a.for_run else "")))


if __name__ == "__main__":
    main()
