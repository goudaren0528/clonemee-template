r"""
客观工作数据采集器

从 tracked 工作面的 git 提交、GitHub PR 抽取客观证据，
输出 Markdown 草稿到 inbox/ 供确认后并入 journal。

只采集 registry/projects.yaml 中 tracked 且 active 的工作面。发现新工作面用 discover.py。
本地路径来自本设备的登记（tools/device.py）；本设备没有克隆的仓库从 GitHub API 取提交。

用法:
    python tools/collect.py --days 7
    python tools/collect.py --since 2027-01-01 --write
    python tools/collect.py --days 1 --no-gh
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import device
from common import ROOT, SEP, load_registry, norm_remote, repo_keys, run, setup_stdio

INBOX = ROOT / "inbox"

DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")


# ---------- 采集源 ----------

def collect_git(path, slug, authors, since, repo_name):
    """某仓库里我在 since 之后的提交。"""
    fmt = "%H" + SEP + "%an" + SEP + "%ae" + SEP + "%aI" + SEP + "%s"
    # 只写日期时 git 会取「那天的此刻」, 起始日当天更早的提交会漏掉, 所以显式写零点
    out = run(["git", "log", "--all", "--since=" + since + " 00:00:00", "--pretty=format:" + fmt],
              cwd=str(path))
    if not out:
        return []

    rows = []
    for line in out.splitlines():
        parts = line.split(SEP)
        if len(parts) != 5:
            continue
        sha, an, ae, iso, subj = parts
        who = (an + " " + ae).lower()
        if not any(a.lower() in who for a in authors):
            continue
        rows.append({"date": iso[:10], "sha": sha[:8], "subject": subj.strip(),
                     "slug": slug, "repo": repo_name})
    return rows


def local_midnight_utc(day):
    """本地时区某天零点, 换成 GitHub API 要的 UTC 时间串。"""
    local = datetime.fromisoformat(day).astimezone()
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def collect_gh_commits(repo, slug, logins, since):
    """本地没克隆的仓库, 从 GitHub API 取我各账号在 since 之后的提交。"""
    rows = []
    for login in logins:
        out = run(["gh", "api", "--paginate",
                   "repos/" + repo + "/commits?per_page=100&author=" + login
                   + "&since=" + local_midnight_utc(since),
                   "--jq", ".[] | [.sha, .commit.author.date, .commit.message] | @tsv"],
                  timeout=120)
        for line in (out or "").splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            sha, iso, msg = parts
            subj = msg.replace("\\n", "\n").splitlines()[0] if msg else ""
            rows.append({"date": iso[:10], "sha": sha[:8], "subject": subj.strip(),
                         "slug": slug, "repo": repo.split("/")[-1]})
    return rows


def collect_github(user, since, repo_to_slug):
    """我在 since 之后有更新的 PR, 只保留 tracked 仓库的。"""
    out = run(["gh", "search", "prs", "--author", user, "--updated", ">=" + since,
               "--json", "title,url,repository,updatedAt,state", "--limit", "100"],
              timeout=120)
    if not out:
        return [], 0
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return [], 0

    rows, dropped = [], 0
    for pr in data:
        repo = (pr.get("repository") or {}).get("nameWithOwner", "")
        slug = repo_to_slug.get(repo.lower())
        if not slug:
            dropped += 1  # 非 tracked 仓库, 交给 discover.py 去发现
            continue
        rows.append({"date": (pr.get("updatedAt") or "")[:10], "repo": repo,
                     "slug": slug, "title": pr.get("title", ""),
                     "state": pr.get("state", ""), "url": pr.get("url", "")})
    return rows, dropped


# ---------- 渲染 ----------

def render(since, until, commits, prs, skipped, dropped, names, multi_repo):
    L = []
    A = L.append
    A("# 采集草稿 " + since + " → " + until)
    A("")
    A("> 由 tools/collect.py 于 " + format(datetime.now(), "%Y-%m-%d %H:%M") + " 生成。")
    A("> **这是客观数据，不是 journal。** 确认并补上主观上下文后，再并入 journal/。")
    A("")
    A("**概览**：" + str(len(commits)) + " 个提交 · " + str(len(prs)) + " 个 PR")
    A("")

    by_date = defaultdict(lambda: {"commits": [], "prs": []})
    for c in commits:
        by_date[c["date"]]["commits"].append(c)
    for p in prs:
        if p["date"]:
            by_date[p["date"]]["prs"].append(p)

    if not by_date:
        A("_该时间段没有采集到数据。_")
        A("")

    for d in sorted(by_date, reverse=True):
        b = by_date[d]
        wd = "一二三四五六日"[date.fromisoformat(d).weekday()]
        A("## " + d + " (周" + wd + ")")
        A("")

        if b["commits"]:
            per = defaultdict(list)
            for c in b["commits"]:
                per[c["slug"]].append(c)
            A("### 提交")
            for slug in sorted(per):
                A("- **" + names.get(slug, slug) + "** #project/" + slug)
                # 多仓库工作面始终标出仓库名, 否则看不出活落在哪个仓库
                for c in per[slug]:
                    tag = " _(" + c["repo"] + ")_" if slug in multi_repo else ""
                    A("  - " + c["sha"] + " " + c["subject"] + tag)
            A("")

        if b["prs"]:
            A("### Pull Requests")
            for p in sorted(b["prs"], key=lambda x: x["slug"]):
                A("- [" + p["state"] + "] " + p["repo"] + " — ["
                  + p["title"] + "](" + p["url"] + ")")
            A("")

    A("---")
    A("")
    A("## 待我补充")
    A("")
    A("- 这段时间最花精力的是什么？")
    A("- 有哪些不体现在代码里的工作（会议、沟通、方案、运营）？")
    A("- 有哪些决策值得单独记成 decisions/？")
    A("")

    if skipped or dropped:
        A("## 备注")
        A("")
        for s in skipped:
            A("- " + s)
        if dropped:
            A("- 有 " + str(dropped) + " 个 PR 落在未跟踪的仓库，已忽略。"
              + "如果那是新工作面，跑 python tools/discover.py")
        A("")

    return "\n".join(L)


# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser(description="采集客观工作数据到 inbox/")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--days", type=int, help="回溯天数")
    g.add_argument("--since", help="起始日期 YYYY-MM-DD")
    ap.add_argument("--write", action="store_true", help="写入 inbox/ 而非打印")
    ap.add_argument("--no-gh", action="store_true", help="跳过 GitHub 查询")
    args = ap.parse_args()

    setup_stdio()

    if args.since:
        try:
            since_d = date.fromisoformat(args.since)
        except ValueError:
            sys.exit("日期格式错误: " + args.since + "，应为 YYYY-MM-DD")
    else:
        since_d = date.today() - timedelta(days=args.days or 7)
    since = since_d.isoformat()
    until = date.today().isoformat()

    reg = load_registry()
    authors = reg["identity"]["git_authors"]
    user = reg["identity"]["github"]
    logins = [user] + list(reg["identity"].get("github_alt") or [])

    tracked = reg.get("tracked") or []
    if not tracked:
        print("registry/projects.yaml 的 tracked 是空的，没有可采集的工作面。",
              file=sys.stderr)
        print("跑 python tools/discover.py 看候选，确认后把条目移进 tracked。",
              file=sys.stderr)
        sys.exit(1)

    # 先校验本设备的路径登记（路径没变时几乎零开销）
    dev, changes, pending, _ = device.check()
    skipped = ["本设备路径登记：" + c for c in changes]
    if pending:
        skipped.append(f"本设备有 {len(pending)} 个克隆待确认，暂不采集：python tools/device.py show")

    names = dict((e["slug"], e.get("name", e["slug"])) for e in tracked)
    multi_repo = set(e["slug"] for e in tracked if len(e.get("repos") or []) > 1)
    repo_to_slug = {}
    commits = []
    seen_shas = set()

    for entry in tracked:
        if entry.get("status", "active") != "active":
            continue  # paused / done / superseded 不采集
        slug = entry["slug"]
        for repo in entry.get("repos") or []:
            gh = norm_remote(repo.get("github", ""))
            for k in repo_keys(repo):
                repo_to_slug[k] = slug
            name = gh.split("/")[-1]
            paths = [Path(p) for p in device.local_paths(gh, dev=dev)]
            found = []
            if paths:
                for p in paths:
                    found += collect_git(p, slug, authors, since, name)
            elif gh and not args.no_gh:
                found = collect_gh_commits(gh, slug, logins, since)  # 本设备没有克隆, 走 GitHub
            # 同一仓库的多个本地克隆会产出相同提交, 按 sha 去重
            for c in found:
                if c["sha"] not in seen_shas:
                    seen_shas.add(c["sha"])
                    commits.append(c)

    if args.no_gh:
        prs, dropped = [], 0
    else:
        prs, dropped = collect_github(user, since, repo_to_slug)
        if not prs and not dropped:
            skipped.append("GitHub PR 查询无结果或 gh 不可用")

    text = render(since, until, commits, prs, skipped, dropped,
                  names, multi_repo)

    if args.write:
        INBOX.mkdir(exist_ok=True)
        out = INBOX / (until + "-collect.md")
        out.write_text(text, encoding="utf-8")
        print("已写入 " + str(out))
        print("  " + str(len(commits)) + " 提交 · " + str(len(prs)) + " PR")
    else:
        print(text)


if __name__ == "__main__":
    main()
