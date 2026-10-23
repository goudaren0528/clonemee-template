r"""
夜间巡检 · 第一段：确定性采集（不调用模型）。

对 tracked 且 active 的每个仓库，取指定日期内我的提交，附带每个提交的增删行数和主要改动文件，
加上当天有更新的 PR，生成摘要 inbox/nightly-YYYY-MM-DD.md，交给第二段（nightly-review skill）
读 diff、更新文档。本地有克隆的读本地 git，没有的走 GitHub API。

    python tools/nightly.py                  # 今天
    python tools/nightly.py --date 2027-03-08
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, datetime, timedelta

import device
import plan
from collect import collect_github, local_midnight_utc
from common import ROOT, SEP, load_registry, norm_remote, repo_keys, run, setup_stdio

INBOX = ROOT / "inbox"
MAX_FILES = 6           # 每个提交最多列几个改动文件
PATCH_FILES = 4         # 每个提交最多附几个文件的 diff
PATCH_LINES = 50        # 每个文件 diff 最多几行
PROJECT_PATCH_BUDGET = 600  # 每个项目 diff 总行数上限
SKIP_PARTS = ("artifacts/", "evidence/", "/fixtures/", "node_modules/", "dist/", "vendor/")
SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".json", ".lock", ".log", ".txt", ".csv",
            ".zip", ".pdf", ".map", ".snap")
SKIP_NAMES = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "go.sum")


def pick_files(files):
    """挑出最能说明改动的文件：源码 > 脚本 / 配置 > 测试 > 文档；跳过证据、截图、锁文件。"""
    def ok(f):
        low = f.lower()
        return not (any(p in low for p in SKIP_PARTS) or low.endswith(SKIP_EXT) or low.rsplit("/", 1)[-1] in SKIP_NAMES)

    def rank(f):
        low = f.lower()
        if "test" in low or "spec" in low:
            return 2
        if low.endswith(".md"):
            return 3
        return 0 if any(s in low for s in ("src/", "internal/", "cmd/", "app/", "lib/")) else 1
    return sorted([f for f in files if ok(f)], key=rank)[:PATCH_FILES]


def clip(patch):
    lines = [l for l in (patch or "").splitlines() if not l.startswith(("diff --git", "index ", "--- ", "+++ "))]
    return lines[:PATCH_LINES] + ([f"…（截断，原 {len(lines)} 行）"] if len(lines) > PATCH_LINES else [])


def local_commits(path, authors, day, repo_name):
    nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    fmt = "@@" + SEP.join(["%H", "%an", "%ae", "%aI", "%s"])
    out = run(["git", "log", "--all", "--no-merges", f"--since={day} 00:00:00", f"--until={nxt} 00:00:00",
               "--numstat", "--pretty=format:" + fmt], cwd=str(path), timeout=120) or ""
    commits, cur = [], None
    for line in out.splitlines():
        if line.startswith("@@"):
            sha, an, ae, iso, subj = (line[2:].split(SEP) + [""] * 5)[:5]
            who = (an + " " + ae).lower()
            cur = None
            if any(a.lower() in who for a in authors):
                cur = {"sha": sha[:8], "time": iso[11:16], "subject": subj, "add": 0, "del": 0,
                       "files": [], "repo": repo_name}
                commits.append(cur)
        elif cur and line.strip():
            parts = line.split("\t")
            if len(parts) == 3:
                a, d, f = parts
                cur["add"] += int(a) if a.isdigit() else 0
                cur["del"] += int(d) if d.isdigit() else 0
                cur["files"].append(f)
    for c in commits:
        c["patches"] = {}
        for f in pick_files(c["files"]):
            full = next((x for x in commits_sha(path, c["sha"])), c["sha"])
            c["patches"][f] = clip(run(["git", "show", full, "--format=", "-U2", "--", f], cwd=str(path), timeout=60))
    return commits


def commits_sha(path, short):
    out = run(["git", "rev-parse", short], cwd=str(path))
    return [out.strip()] if out else []


def gh_commits(repo, logins, day):
    nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    commits = []
    for login in logins:
        out = run(["gh", "api", f"repos/{repo}/commits?per_page=50&author={login}"
                   f"&since={local_midnight_utc(day)}&until={local_midnight_utc(nxt)}",
                   "--jq", "[.[].sha]"], timeout=120)
        for sha in json.loads(out) if out else []:
            d = run(["gh", "api", f"repos/{repo}/commits/{sha}"], timeout=120)
            if not d:
                continue
            c = json.loads(d)
            iso = c["commit"]["author"]["date"]
            files = c.get("files", [])
            chosen = pick_files([f["filename"] for f in files])
            commits.append({"sha": sha[:8], "time": datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%H:%M"),
                            "subject": c["commit"]["message"].splitlines()[0],
                            "add": c["stats"]["additions"], "del": c["stats"]["deletions"],
                            "files": [f["filename"] for f in files], "repo": repo.split("/")[-1],
                            "patches": {f["filename"]: clip(f.get("patch")) for f in files if f["filename"] in chosen}})
    return commits


def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="夜间巡检 · 确定性采集")
    ap.add_argument("--date", default=date.today().isoformat())
    args = ap.parse_args()
    day = args.date

    dev, changes, pending, _ = device.check()
    reg = load_registry()
    authors = reg["identity"]["git_authors"]
    logins = [reg["identity"]["github"]] + list(reg["identity"].get("github_alt") or [])

    per_slug, names, where, repo_to_slug = defaultdict(list), {}, defaultdict(list), {}
    for entry in reg.get("tracked") or []:
        if entry.get("status", "active") != "active":
            continue
        slug = entry["slug"]
        names[slug] = entry.get("name", slug)
        seen = set()
        for repo in entry.get("repos") or []:
            gh = norm_remote(repo.get("github", ""))
            for k in repo_keys(repo):
                repo_to_slug[k] = slug
            paths = device.local_paths(gh, dev=dev)
            if paths:
                where[slug] += [f"{gh}（本地 {p}）" for p in paths]
                found = [c for p in paths for c in local_commits(p, authors, day, gh.split("/")[-1])]
            else:
                where[slug].append(f"{gh}（GitHub）")
                found = gh_commits(gh, logins, day)
            for c in found:
                if c["sha"] not in seen:
                    seen.add(c["sha"])
                    per_slug[slug].append(c)

    prs, _ = collect_github(reg["identity"]["github"], day, repo_to_slug)
    prs = [p for p in prs if p["date"] == day]
    pr_by_slug = defaultdict(list)
    for p in prs:
        pr_by_slug[p["slug"]].append(p)

    tasks = defaultdict(list)
    for t in plan.open_tasks():
        if t["proj"]:
            tasks[t["proj"]].append(t)

    active = sorted(set(per_slug) | set(pr_by_slug), key=lambda s: -sum(c["add"] + c["del"] for c in per_slug[s]))
    L = [f"# 夜间巡检摘要 {day}", "",
         f"> 由 tools/nightly.py 于 {datetime.now():%Y-%m-%d %H:%M} 生成，只含客观数据。"
         "第二段由 nightly-review skill 读 diff、更新文档。", "",
         f"**概览**：{len(active)} 个项目有动静 · {sum(len(v) for v in per_slug.values())} 个提交 · {len(prs)} 个 PR 有更新", ""]
    if changes or pending:
        L += ["**设备路径**：" + "；".join(changes + [f"{len(pending)} 个克隆待确认"]), ""]
    for slug in active:
        cs = sorted(per_slug[slug], key=lambda c: c["time"])
        L += [f"## {names.get(slug, slug)}  `#project/{slug}`", "",
              f"- 仓库：{'；'.join(where[slug])}",
              f"- 提交 {len(cs)} 个，+{sum(c['add'] for c in cs)} / -{sum(c['del'] for c in cs)}"]
        for c in cs:
            files = "、".join(c["files"][:MAX_FILES]) + (f" 等 {len(c['files'])} 个" if len(c["files"]) > MAX_FILES else "")
            L.append(f"  - {c['time']} `{c['sha']}` _{c['repo']}_ {c['subject']}（+{c['add']}/-{c['del']}）：{files}")
        for p in pr_by_slug.get(slug, []):
            L.append(f"- PR [{p['state']}] {p['repo']} — [{p['title']}]({p['url']})")
        for t in tasks.get(slug, []):
            L.append(f"- 相关计划任务：{t['plan']}#{t['id']} {t['title']}（{t['state']}）")
        budget = PROJECT_PATCH_BUDGET
        diff_lines = []
        for c in cs:
            for f, lines in (c.get("patches") or {}).items():
                if budget <= 0:
                    break
                take = lines[:budget]
                budget -= len(take)
                diff_lines += [f"#### `{c['sha']}` {f}", "```diff"] + take + ["```"]
        if diff_lines:
            L += ["", "<details><summary>关键 diff 片段</summary>", ""] + diff_lines + ["", "</details>"]
            if budget <= 0:
                L.append("_diff 已达本项目上限，其余省略。_")
        L.append("")
    quiet = [names[s] for s in names if s not in active]
    L += ["## 今天没有动静", "", "、".join(quiet) or "—", ""]

    INBOX.mkdir(exist_ok=True)
    out = INBOX / f"nightly-{day}.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"已写入 {out.relative_to(ROOT).as_posix()}：{len(active)} 个项目有动静")


if __name__ == "__main__":
    main()
