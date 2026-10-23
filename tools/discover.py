r"""
工作面发现器

扫本地 git 仓库 + 查 GitHub 活跃度，找出"可能是我工作面"的候选，
与 registry/projects.yaml 比对后，只报告【尚未确认】的。

它永远不会自动把东西加进 tracked —— 那是你的决定。
扫描根目录和深度取自本设备的登记（registry/devices/<指纹>.yaml），可用参数覆盖。

用法:
    python tools/discover.py              # 报告未确认的候选
    python tools/discover.py --all        # 连已确认/已忽略的一起列
    python tools/discover.py --no-gh      # 只扫本地
    python tools/discover.py --roots D:/ E:/ --depth 5
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import device
from common import ROOT, SEP, find_repos, load_registry, norm_remote, repo_keys, run, setup_stdio, tracked_repos


def scan_local(roots, authors, depth):
    """找所有 git 仓库, 统计我的提交。"""
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    found = []
    for root in roots:
        rp = Path(root)
        if not rp.is_dir():
            continue
        for d in find_repos(rp, depth):
            if d.resolve() == ROOT.resolve():
                continue  # 跳过本仓库
            log = run(["git", "log", "--all", "--pretty=%aI%x1f%an <%ae>"],
                      cwd=str(d), timeout=60)
            if log is None:
                continue
            n90 = ntot = 0
            last = ""
            for line in log.splitlines():
                iso, _, who = line.partition(SEP)
                if not any(a.lower() in who.lower() for a in authors):
                    continue
                ntot += 1
                if iso[:10] >= cutoff:
                    n90 += 1
                if not last:
                    last = iso[:10]
            remote = run(["git", "remote", "get-url", "origin"], cwd=str(d)) or ""
            found.append({
                "path": str(d),
                "name": d.name,
                "remote": norm_remote(remote),
                "commits_90d": n90,
                "commits_total": ntot,
                "last_commit": last,
            })
    return found


def scan_github(logins, days=180):
    """近 N 天我各账号有 PR 的仓库。"""
    since = (date.today() - timedelta(days=days)).isoformat()
    counter = collections.Counter()
    for user in logins:
        out = run(["gh", "search", "prs", "--author", user, "--created", f">={since}",
                   "--json", "repository", "--limit", "200"], timeout=120)
        try:
            data = json.loads(out) if out else []
        except json.JSONDecodeError:
            data = []
        counter.update(x["repository"]["nameWithOwner"] for x in data)
    return counter


def scan_owned(logins, days=180):
    """我各账号名下近 N 天有推送的仓库（不含 fork 和已归档）。返回 {repo: 最近推送日期}。"""
    since = (date.today() - timedelta(days=days)).isoformat()
    owned = {}
    for user in logins:
        out = run(["gh", "repo", "list", user, "--limit", "500", "--json",
                   "nameWithOwner,pushedAt,isFork,isArchived"], timeout=120)
        try:
            data = json.loads(out) if out else []
        except json.JSONDecodeError:
            data = []
        for x in data:
            pushed = (x.get("pushedAt") or "")[:10]
            if x.get("isFork") or x.get("isArchived") or pushed < since:
                continue
            owned[x["nameWithOwner"]] = pushed
    return owned


def main():
    setup_stdio()
    dev = device.load_device()
    ap = argparse.ArgumentParser(description="发现可能的工作面候选")
    ap.add_argument("--roots", nargs="*", default=dev["scan_roots"], help="扫描根目录（默认取本设备登记）")
    ap.add_argument("--depth", type=int, default=int(dev["scan_depth"]), help="每个根目录往下找仓库的最大深度")
    ap.add_argument("--all", action="store_true", help="连已确认/已忽略的一起列")
    ap.add_argument("--no-gh", action="store_true", help="跳过 GitHub")
    args = ap.parse_args()

    reg = load_registry()
    authors = reg["identity"]["git_authors"]
    logins = [reg["identity"]["github"]] + list(reg["identity"].get("github_alt") or [])

    tracked_keys = set()
    for _, r in tracked_repos(reg):
        tracked_keys |= repo_keys(r)
    ignored_keys = {norm_remote(e.get("github", "")).lower() for e in reg.get("ignored") or []}
    ignored_keys.discard("")

    local = [r for r in scan_local(args.roots, authors, args.depth)
             if not device._is_ignored(dev, r["path"])]
    prs = {} if args.no_gh else scan_github(logins)
    owned = {} if args.no_gh else scan_owned(logins)

    # 同一 remote 的多个本地目录（worktree、副本）只留我提交最多的一个
    best = {}
    for r in local:
        key = (r["remote"] or r["path"]).lower()
        if key not in best or r["commits_total"] > best[key]["commits_total"]:
            best[key] = r

    # 本地仓库 + 只在 GitHub 上出现的仓库（有 PR 或我名下近期有推送）, 合成候选集
    rows = []
    for r in best.values():
        r["prs_180d"] = prs.get(r["remote"], 0)
        r["pushed"] = owned.get(r["remote"], "")
        rows.append(r)
    for repo in set(prs) | set(owned):
        if repo.lower() not in best:
            rows.append({"path": "", "name": repo.split("/")[-1], "remote": repo,
                         "commits_90d": 0, "commits_total": 0, "last_commit": "",
                         "prs_180d": prs.get(repo, 0), "pushed": owned.get(repo, "")})

    for r in rows:
        key = r["remote"].lower()
        r["state"] = "tracked" if key in tracked_keys else "ignored" if key in ignored_keys else "new"

    # 没有 remote、也没有我的提交的本地仓库多是测试夹具或实验残留, 默认不列
    residue = [r for r in rows if r["state"] == "new" and not r["remote"] and not r["commits_total"]]
    if not args.all:
        rows = [r for r in rows if r not in residue]

    rows.sort(key=lambda r: (r["commits_90d"] + r["prs_180d"] * 3, r["pushed"]), reverse=True)
    show = rows if args.all else [r for r in rows if r["state"] == "new"]

    if not show:
        print("没有未确认的新工作面。")
        print(f"(已跟踪 {sum(1 for r in rows if r['state']=='tracked')} 个，"
              f"已忽略 {sum(1 for r in rows if r['state']=='ignored')} 个)")
        return

    print(f"# 待确认的工作面候选（{len(show)} 个）\n")
    print("| 状态 | 仓库 | 90天提交 | 180天PR | 最后提交 | 我名下最近推送 | 本设备路径 |")
    print("|---|---|---:|---:|---|---|---|")
    for r in show:
        mark = {"new": "🆕", "tracked": "✅", "ignored": "🚫"}[r["state"]]
        print(f"| {mark} | {r['remote'] or r['name']} | {r['commits_90d']} | "
              f"{r['prs_180d']} | {r['last_commit'] or '—'} | {r['pushed'] or '—'} | "
              f"{r['path'] or '未克隆'} |")
    print("\n确认方式：把要监控的加进 `registry/projects.yaml` 的 `tracked`，")
    print("不要的加进 `ignored`（下次就不再提示）。本地路径不用写，`tools/device.py` 会自动登记。")
    if residue and not args.all:
        print(f"\n另有 {len(residue)} 个无 remote、无我提交的本地仓库（测试夹具/残留）未列出，`--all` 可见。")


if __name__ == "__main__":
    main()
