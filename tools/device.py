r"""
设备感知的本地路径登记 —— 换设备不写死路径。

registry/projects.yaml 只记 GitHub 仓库，不记本地路径。
每台设备一个文件 registry/devices/<设备指纹>.yaml，记录：
  - 设备指纹（机器 ID 的哈希，不存原始 ID）、主机名、系统、首次见到 / 最近检查 / 最近扫描日期
  - 每个 tracked 仓库在这台设备上的本地克隆路径，以及是否已确认

检查逻辑（同一设备、路径没变时几乎零开销）：
  1. 校验已登记路径：目录还在、origin 还是这个仓库 → 不动；失效的移除并报告
  2. 只有出现「从没在这台设备上找过的仓库」，或缺失仓库距上次扫描超过 7 天，才扫描磁盘
  3. 扫到唯一一个克隆 → 自动确认；扫到多个 → 记为待确认，由我确认或排除

用法:
    python tools/device.py                   # 检查（会话启动 hook 也会自动跑）
    python tools/device.py --rescan          # 强制重新扫描
    python tools/device.py show              # 打印本设备登记情况
    python tools/device.py confirm <路径>     # 确认某个待确认的克隆
    python tools/device.py reject <路径或通配>  # 排除（例如 agent 工作区副本），以后不再登记
"""
from __future__ import annotations

import argparse
import fnmatch
import getpass
import hashlib
import os
import platform
import re
import uuid
from datetime import date
from pathlib import Path

import yaml

from common import ROOT, find_repos, load_registry, origin_of, repo_keys, run, setup_stdio, tracked_repos

DEVICES = ROOT / "registry" / "devices"
RESCAN_DAYS = 7


# ---------- 设备指纹 ----------

def _machine_id():
    system = platform.system()
    if system == "Windows":
        out = run(["reg", "query", r"HKLM\SOFTWARE\Microsoft\Cryptography", "/v", "MachineGuid"]) or ""
        m = re.search(r"MachineGuid\s+REG_SZ\s+(\S+)", out)
        if m:
            return m.group(1)
    elif system == "Darwin":
        out = run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"]) or ""
        m = re.search(r'"IOPlatformUUID" = "([^"]+)"', out)
        if m:
            return m.group(1)
    else:
        for f in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                v = Path(f).read_text().strip()
                if v:
                    return v
            except OSError:
                pass
    return platform.node() + "-" + format(uuid.getnode(), "x")  # 兜底：主机名 + MAC


def fingerprint():
    """稳定的设备指纹：机器 ID 加盐哈希取 12 位，不暴露原始 ID。"""
    return hashlib.sha256(("assistant:" + _machine_id()).encode()).hexdigest()[:12]


def _default_roots():
    home = Path.home()
    roots = [str(home)]
    if platform.system() == "Windows":
        system_drive = os.environ.get("SystemDrive", "C:").upper()
        for letter in "CDEFGHIJ":
            drive = letter + ":"
            if drive != system_drive and Path(drive + "/").exists():
                roots.append(drive + "/")
    return roots


# ---------- 设备文件 ----------

def device_file(fp=None):
    return DEVICES / ((fp or fingerprint()) + ".yaml")


def load_device(fp=None):
    fp = fp or fingerprint()
    f = device_file(fp)
    if f.exists():
        with open(f, encoding="utf-8") as fh:
            dev = yaml.safe_load(fh) or {}
    else:
        dev = {}
    dev.setdefault("fingerprint", fp)
    dev.setdefault("hostname", platform.node())
    dev.setdefault("os", platform.system() + " " + platform.release())
    try:
        dev.setdefault("user", getpass.getuser())
    except Exception:
        dev.setdefault("user", "")
    dev.setdefault("first_seen", date.today().isoformat())
    dev.setdefault("scan_roots", _default_roots())
    dev.setdefault("scan_depth", 4)
    dev.setdefault("repos", {})
    dev.setdefault("absent", {})
    dev.setdefault("ignored_paths", [])
    return dev


def save_device(dev):
    DEVICES.mkdir(parents=True, exist_ok=True)
    head = ("# 本设备的本地路径登记，由 tools/device.py 维护。\n"
            "# repos: 仓库 -> 本地克隆列表；confirmed=false 的需要我确认（device.py confirm / reject）\n"
            "# absent: 扫描过但本设备上没有克隆的仓库；ignored_paths: 排除的路径（支持通配）\n")
    body = yaml.safe_dump(dev, allow_unicode=True, sort_keys=False, width=200)
    device_file(dev["fingerprint"]).write_text(head + body, encoding="utf-8")


def _norm(p):
    return os.path.normcase(os.path.normpath(str(p)))


def _is_ignored(dev, path):
    p = _norm(path)
    return any(fnmatch.fnmatch(p, _norm(pat)) for pat in dev.get("ignored_paths") or [])


def local_paths(github, confirmed_only=True, dev=None):
    """某仓库在本设备上的本地路径（默认只返回已确认的）。"""
    dev = dev or load_device()
    key = github.lower()
    for gh, entries in (dev.get("repos") or {}).items():
        if gh.lower() == key:
            return [e["path"] for e in entries or [] if e.get("confirmed") or not confirmed_only]
    return []


def register(github, path):
    """把一个刚克隆好的路径登记为本设备的已确认克隆（clone.py 用）。"""
    dev = load_device()
    entries = dev["repos"].setdefault(github, [])
    if _norm(path) not in {_norm(e["path"]) for e in entries}:
        entries.append({"path": str(path), "confirmed": True, "seen": date.today().isoformat()})
    dev["absent"].pop(github, None)
    save_device(dev)


# ---------- 检查 ----------

def _days_since(d):
    try:
        return (date.today() - date.fromisoformat(str(d))).days
    except (TypeError, ValueError):
        return 10 ** 6


def scan(dev):
    """扫描磁盘，返回 {origin 小写: [本地路径]}。"""
    found = {}
    for root in dev.get("scan_roots") or []:
        if not Path(root).is_dir():
            continue
        for d in find_repos(root, int(dev.get("scan_depth") or 4)):
            if _is_ignored(dev, d):
                continue
            o = origin_of(d)
            if o:
                found.setdefault(o.lower(), []).append(str(d))
    return found


def check(rescan=False):
    """校验并按需扫描。返回 (dev, 变化列表, 待确认列表, 是否扫描过)。"""
    reg = load_registry()
    dev = load_device()
    today = date.today().isoformat()
    changes = []

    wanted = {}  # 仓库主名 -> 可匹配名集合
    self_repos = set()  # 本仓库自身（registry 里标 self: true），不靠 origin 匹配
    for _, repo in tracked_repos(reg):
        if repo.get("github"):
            wanted[repo["github"]] = repo_keys(repo)
            if repo.get("self"):
                self_repos.add(repo["github"])

    repos = dev["repos"]
    absent = dev["absent"]

    for gh in self_repos:
        if [e.get("path") for e in repos.get(gh, [])] != [str(ROOT)]:
            repos[gh] = [{"path": str(ROOT), "confirmed": True, "seen": today}]
            absent.pop(gh, None)
            changes.append(f"{gh}：登记为本仓库 {ROOT}")

    # 1. 校验已登记的路径
    for gh in list(repos):
        if gh in self_repos:
            continue
        if gh not in wanted:
            del repos[gh]
            changes.append(f"{gh} 已不在 tracked，移除本设备登记")
            continue
        keep = []
        for e in repos[gh] or []:
            o = origin_of(e["path"])
            if o and o.lower() in wanted[gh]:
                keep.append(e)
            else:
                why = "目录不存在" if not Path(e["path"]).is_dir() else f"origin 变成了 {o or '空'}"
                changes.append(f"{gh}：{e['path']} 失效（{why}），已移除")
        if keep:
            repos[gh] = keep
        else:
            del repos[gh]
    for gh in list(absent):
        if gh not in wanted:
            del absent[gh]

    # 2. 需要时才扫描
    missing = [gh for gh in wanted if gh not in repos]
    never_looked = [gh for gh in missing if gh not in absent]
    stale = [gh for gh in missing if _days_since(absent.get(gh)) >= RESCAN_DAYS]
    scanned = bool(rescan or never_looked or stale)
    if scanned:
        found = scan(dev)
        for gh, keys in wanted.items():
            paths = sorted({p for k in keys for p in found.get(k, [])})
            existing = {_norm(e["path"]) for e in repos.get(gh, [])}
            new = [p for p in paths if _norm(p) not in existing]
            if not new:
                if gh not in repos:
                    absent[gh] = today
                continue
            absent.pop(gh, None)
            auto = not existing and len(new) == 1  # 唯一匹配，自动确认
            for p in new:
                repos.setdefault(gh, []).append({"path": p, "confirmed": auto, "seen": today})
                changes.append(f"{gh}：发现本地克隆 {p}" + ("（唯一匹配，已自动确认）" if auto else "（待确认）"))
        dev["last_scan"] = today

    pending = [(gh, e["path"]) for gh, es in repos.items() for e in es if not e.get("confirmed")]
    # 在本仓库的工作区副本里（Orca 派发会新建 git worktree，.git 是文件）只读不写，
    # 否则会把副本路径登记成 assistant 的位置，合并回 main 时污染设备文件
    linked_worktree = (ROOT / ".git").is_file()
    if not linked_worktree and (scanned or changes or dev.get("last_checked") != today
                                or not device_file(dev["fingerprint"]).exists()):
        dev["last_checked"] = today
        save_device(dev)
    return dev, changes, pending, scanned


def summary(dev, changes, pending, scanned):
    repos = dev.get("repos") or {}
    n_ok = sum(1 for es in repos.values() if any(e.get("confirmed") for e in es))
    head = (f"设备 {dev.get('hostname')}（指纹 {dev['fingerprint']}）："
            f"{n_ok} 个仓库有本地克隆，{len(dev.get('absent') or {})} 个本设备没有（只采 GitHub 侧）"
            + ("；本次扫描了磁盘" if scanned else ""))
    lines = [head]
    lines += ["- " + c for c in changes]
    if pending:
        lines.append(f"- {len(pending)} 个克隆待确认：python tools/device.py show 查看，confirm / reject 处理")
    return lines


# ---------- 命令行 ----------

def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="设备感知的本地路径登记")
    ap.add_argument("action", nargs="?", default="check", choices=["check", "show", "confirm", "reject"])
    ap.add_argument("target", nargs="?", help="confirm / reject 的路径（reject 支持通配）")
    ap.add_argument("--rescan", action="store_true", help="强制重新扫描磁盘")
    ap.add_argument("--quiet", action="store_true", help="没有变化时不输出")
    args = ap.parse_args()

    if args.action == "check":
        dev, changes, pending, scanned = check(args.rescan)
        if not args.quiet or changes or pending:
            print("\n".join(summary(dev, changes, pending, scanned)))
        return

    dev = load_device()
    if args.action == "show":
        print(f"# 设备 {dev['hostname']}（{dev['fingerprint']}，{dev['os']}）")
        print(f"首次 {dev['first_seen']} · 最近检查 {dev.get('last_checked', '—')} · 最近扫描 {dev.get('last_scan', '—')}")
        print(f"扫描根目录：{', '.join(dev['scan_roots'])}（深度 {dev['scan_depth']}）\n")
        for gh, es in sorted(dev["repos"].items()):
            for e in es:
                print(f"{'✅' if e.get('confirmed') else '❓'} {gh}  {e['path']}")
        for gh in sorted(dev["absent"]):
            print(f"➖ {gh}  本设备没有克隆")
        if dev["ignored_paths"]:
            print("\n排除：" + "；".join(dev["ignored_paths"]))
        return

    if not args.target:
        ap.error(args.action + " 需要一个路径")
    hit = 0
    if args.action == "confirm":
        for es in dev["repos"].values():
            for e in es:
                if _norm(e["path"]) == _norm(args.target):
                    e["confirmed"] = True
                    hit += 1
    else:  # reject
        pat = _norm(args.target)
        if args.target not in dev["ignored_paths"]:
            dev["ignored_paths"].append(args.target)
        for gh in list(dev["repos"]):
            before = len(dev["repos"][gh])
            dev["repos"][gh] = [e for e in dev["repos"][gh] if not fnmatch.fnmatch(_norm(e["path"]), pat)]
            hit += before - len(dev["repos"][gh])
            if not dev["repos"][gh]:
                del dev["repos"][gh]
    save_device(dev)
    print(f"{args.action}：处理了 {hit} 个登记")


if __name__ == "__main__":
    main()
