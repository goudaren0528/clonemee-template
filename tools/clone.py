r"""
克隆缺失的仓库 —— 换设备时，tracked 里的项目本设备没有克隆，提醒你并在你说「克隆」后帮着克隆、登记。

    python tools/clone.py --missing                       # 本设备缺哪些克隆，哪些有未完成的任务（优先）
    python tools/clone.py <your-github>/example-repo          # 只看：建议克隆到哪
    python tools/clone.py <your-github>/example-repo --run [--path D:/example-repo]   # 克隆 + 登记进 Orca + 登记进本设备路径表

建议路径按本设备的习惯：同一个 GitHub 账号 / 组织的仓库大多放在哪个目录，就放哪（比如团队的 <your-org> 在 D:\work）；
没有先例就放第一个非系统盘根目录。你确认后才克隆（「确认派发」不顺带克隆）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import device
import env
import plan
from common import ROOT, load_registry, norm_remote, setup_stdio


def suggest(gh):
    owner, name = gh.split("/")
    dev = device.load_device()
    parents = Counter(str(Path(e["path"]).parent) for g, es in (dev.get("repos") or {}).items()
                      if g.split("/")[0].lower() == owner.lower() for e in es if e.get("confirmed"))
    if parents:
        base = parents.most_common(1)[0][0]
    else:
        roots = [r for r in dev.get("scan_roots") or [] if Path(r).is_dir() and not str(Path(r)).lower().startswith(("c:", str(Path.home()).lower()))]
        base = roots[0] if roots else str(Path.home())
    return str(Path(base) / name)


def open_tasks_by_project():
    """各项目未完成的计划任务数（找 plans/ 下 active / draft 的计划和今天的当日计划）。"""
    count = Counter()
    for f in list((ROOT / "plans").glob("*.md")) + list((ROOT / "plans" / "daily").glob("*.md")):
        try:
            fm, tasks = plan.parse_plan(f)
        except Exception:
            continue
        if f.parent.name == "daily" and f.stem != date.today().isoformat():
            continue
        if str(fm.get("status")) in ("done", "dropped", "superseded"):
            continue
        for t in tasks:
            if t.get("proj") and t["state"] in ("待办", "进行中", "受阻"):
                count[t["proj"]] += 1
    return count


def missing():
    """本设备没有克隆的 tracked 仓库：[(项目, 仓库, 未完成任务数, 建议路径)]，有任务的排前面。"""
    reg = load_registry()
    dev = device.load_device()
    have = {g.lower() for g, es in (dev.get("repos") or {}).items() if any(e.get("confirmed") for e in es)}
    tasks = open_tasks_by_project()
    out = []
    for e in reg.get("tracked") or []:
        if str(e.get("status", "active")) not in ("active", ""):
            continue
        for r in e.get("repos") or []:
            gh = norm_remote(r["github"])
            if gh.lower() not in have and not r.get("self"):
                out.append((e["slug"], gh, tasks.get(e["slug"], 0), suggest(gh)))
    return sorted(out, key=lambda x: -x[2])


def clone(gh, path):
    if Path(path).exists():
        sys.exit(f"❌ {path} 已经存在：换一个路径（--path），或者它就是这个仓库的话跑 python tools/device.py --rescan")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    print(f"- 克隆 {gh} → {path}")
    r = subprocess.run(["git", "clone", f"https://github.com/{gh}.git", path])
    if r.returncode:
        sys.exit("❌ 克隆失败（看上面的输出：权限、网络）")
    device.register(gh, path)
    print("- ✅ 已登记进本设备路径表")
    if env.detect()["orca_reachable"]:
        from dispatch import orca
        res = orca("repo", "add", "--path", path)
        print("- ✅ 已登记进 Orca" if res.get("ok") else f"- ⚠ Orca 登记失败：{str(res.get('error'))[:200]}")


def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="克隆本设备缺的仓库")
    ap.add_argument("repo", nargs="?", help="owner/repo")
    ap.add_argument("--path", help="克隆到哪（默认按本设备习惯建议）")
    ap.add_argument("--missing", action="store_true")
    ap.add_argument("--run", action="store_true", help="真正克隆；不加只看建议")
    a = ap.parse_args()
    if a.missing or not a.repo:
        rows = missing()
        if not rows:
            print("本设备 tracked 的仓库都有克隆。")
            return
        print("| 项目 | 仓库 | 未完成任务 | 建议克隆到 |\n|---|---|---|---|")
        for proj, gh, n, path in rows:
            print(f"| {proj} | {gh} | {n or '—'} | {path} |")
        print("\n克隆：python tools/clone.py <仓库> --run [--path …]")
        return
    gh = norm_remote(a.repo) if "/" in a.repo else sys.exit("仓库写成 owner/repo")
    path = a.path or suggest(gh)
    if not a.run:
        print(f"建议克隆到 {path}（确认后加 --run；换位置加 --path）")
        return
    clone(gh, path)


if __name__ == "__main__":
    main()
