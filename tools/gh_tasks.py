r"""
项目任务放在 GitHub（milestone + issue）—— 项目偏好 `tasks: github` 时用。

项目偏好写在项目档案 projects/<slug>/index.md：frontmatter 的 prefs（工具读）+ 正文「项目偏好」一节（人和 agent 读）：
    prefs:
      tasks: github                       # 任务和计划用 GitHub 的 milestone + issue 维护（不写 = 只在 assistant）
      tasks_repo: <your-github>/example-repo    # 建在哪个仓库

assistant 的计划仍是总账（目标、顺序、和其他项目一起排）；这类项目的任务行带 issue:owner/repo#n，详情、讨论、关闭都在 GitHub。
- 一个计划 → 一个 milestone（标题用计划标题，截止日用任务里最晚的 due）
- 计划里这个项目的每个未完成任务 → 一个 issue（标题是任务标题；正文是验收和说明；@me 的指派给自己）
- 在 GitHub 上写的内容是对外的：不用内部称呼「你」，不带 private/ 的内容和 assistant 内部链接

    python tools/gh_tasks.py status [--project example-project]   # milestone 进度、未关的 issue、还没建 issue 的任务
    python tools/gh_tasks.py push plans/2026-01-01-example-plan.md [--task T1 ...] [--run]   # 建 milestone 和 issue，回填 issue:（先看方案）
    python tools/gh_tasks.py pull [--run]                              # issue 关了 → 任务打勾、记进展（只读 GitHub，只写 assistant）
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import common
import plan
import recall
from common import ROOT, setup_stdio

PROJECTS = ROOT / "projects"


def is_gh_write(args):
    """gh 子命令是不是写 GitHub：issue / milestone 的创建修改，或 gh api 带 -X / -f / -F / --input（非 GET）。"""
    if args[:1] == ("api",):
        return any(a in ("-X", "--method", "-f", "-F", "--field", "--raw-field", "--input") for a in args)
    return args[:2] not in (("issue", "view"), ("issue", "list"), ("pr", "view"), ("pr", "list"))


def gh(*args, data=None):
    if is_gh_write(args) and common.blocked("gh " + " ".join(args[:2])):
        raise RuntimeError("已拦截外发（ASSISTANT_NO_SEND=1），没有对 GitHub 写入")
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", input=data)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    return r.stdout


def prefs(slug):
    f = PROJECTS / slug / "index.md"
    if not f.exists():
        return {}
    fm, _ = recall.parse(f)
    return fm.get("prefs") or {}


def github_projects():
    out = {}
    for f in sorted(PROJECTS.glob("*/index.md")):
        p = prefs(f.parent.name)
        if p.get("tasks") == "github" and p.get("tasks_repo"):
            out[f.parent.name] = p["tasks_repo"]
    return out


def plan_files():
    return [f for f in sorted((ROOT / "plans").glob("*.md")) if f.name not in ("INDEX.md", "goals.md", "someday.md")]


def plan_title(path):
    m = re.search(r"^# (.+)$", Path(path).read_text(encoding="utf-8"), re.M)
    return m.group(1).strip() if m else Path(path).stem


def task_block(path, tid):
    import dispatch
    return dispatch.task_block(path, tid)


def external(text):
    """写到 GitHub 上的内容：去掉助手内部链接、执行说明、private（称呼不自动替换，写计划时直接写名字或用第三人称）。"""
    text = re.sub(r"\n\s*- 执行：.*", "", text, flags=re.S)               # 执行说明是给 agent 的，不外发
    text = re.sub(r"\[([^\]]+)\]\((?!https?://)[^)]*\)", r"\1", text)      # 相对链接只留文字
    text = "\n".join(l for l in text.splitlines() if "private/" not in l)
    return text.strip()


def issue_body(task, block, ptitle):
    lines = [re.sub(r"^\s*- ", "- ", l) for l in external(block).splitlines() if l.strip()]
    return "\n".join(lines + ["", f"_来自 你的计划「{ptitle}」{task['id']}；状态以这个 issue 为准。_"])


def ensure_milestone(repo, title, due):
    ms = json.loads(gh("api", f"repos/{repo}/milestones?state=all&per_page=100"))
    hit = next((m for m in ms if m["title"] == title), None)
    if hit:
        return hit["number"], False
    args = ["api", f"repos/{repo}/milestones", "-f", f"title={title}"]
    if due:
        args += ["-f", f"due_on={due}T23:59:59Z"]
    return json.loads(gh(*args))["number"], True


def set_issue(path, tid, ref):
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(lines):
        if re.match(rf"^- \[.\] {tid} ", l):
            lines[i] = re.sub(r" · issue:\S+", "", l) + f" · issue:{ref}"
            break
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def people_names():
    """people/ 里登记的人名：issue 标题里不写人名。"""
    names = set()
    for f in (ROOT / "people").glob("*.md"):
        m = re.search(r"^# (.+)$", f.read_text(encoding="utf-8"), re.M)
        if m and f.name != "INDEX.md":
            names.add(m.group(1).strip())
    return names


def cmd_push(a):
    path = Path(a.plan) if Path(a.plan).is_absolute() else ROOT / a.plan
    fm, tasks = plan.parse_plan(path)
    gp = github_projects()
    picked = [t for t in tasks if t["proj"] in gp and t["state"] != "完成" and not t.get("issue")
              and (not a.task or t["id"] in a.task)]
    if not picked:
        print("没有要建 issue 的任务：要是 tasks: github 的项目、未完成、还没有 issue:")
        return
    names = people_names()
    bad = [t["id"] for t in picked if any(n in t["title"] for n in names)]
    if bad:
        sys.exit("❌ 这些任务的标题里有人名（issue 标题要以任务为主，不写人名）：" + "、".join(bad) + "；先改计划里的任务标题")
    ptitle = plan_title(path)
    due = max((t["due"] for t in picked if t["due"]), default="")
    repos = sorted({gp[t["proj"]] for t in picked})
    print(f"# 推到 GitHub（{'执行' if a.run else '只看方案，加 --run 才建'}）\n")
    print(f"- milestone「{ptitle}」，截止 {due or '不设'}，建在 {'、'.join(repos)}（已有同名的就复用）\n")
    print("| 任务 | issue 标题 | 仓库 | 指派 |\n|---|---|---|---|")
    for t in picked:
        print(f"| {t['id']} | {t['title']} | {gp[t['proj']]} | {'<your-github>' if t['owner'] == 'me' else '—'} |")
    if not a.run:
        print("\n正文：任务下的「验收」「说明」等（去掉执行说明、assistant 内部链接和 private），结尾注明来自哪个计划。确认后加 --run")
        return
    ms = {}
    for t in picked:
        repo = gp[t["proj"]]
        if repo not in ms:
            ms[repo], created = ensure_milestone(repo, ptitle, due)
            print(f"- milestone #{ms[repo]}（{'新建' if created else '复用'}）@ {repo}")
        args = ["issue", "create", "-R", repo, "--title", t["title"], "--body-file", "-", "--milestone", ptitle]
        if t["owner"] == "me":
            args += ["--assignee", "@me"]
        url = gh(*args, data=issue_body(t, task_block(path, t["id"]), ptitle)).strip().splitlines()[-1]
        n = url.rstrip("/").split("/")[-1]
        set_issue(path, t["id"], f"{repo}#{n}")
        print(f"- ✅ {t['id']} → {url}")


def cmd_pull(a):
    changed = 0
    for path in plan_files():
        fm, tasks = plan.parse_plan(path)
        for t in tasks:
            m = re.match(r"^([\w.-]+/[\w.-]+)#(\d+)$", t.get("issue", ""))
            if not m:
                continue
            try:
                st = json.loads(gh("issue", "view", m.group(2), "-R", m.group(1), "--json", "state,closedAt,title"))
            except RuntimeError as e:
                print(f"- ⚠ {path.stem}#{t['id']}：读不到 {t['issue']}（{e}）")
                continue
            closed = st["state"] == "CLOSED"
            if closed and t["state"] != "完成":
                print(f"- {path.stem}#{t['id']}：issue 已关闭（{st['closedAt'][:10]}）→ 任务打勾")
                if a.run:
                    mark_done(path, t["id"], st["closedAt"][:10], t["issue"])
                changed += 1
            elif not closed and t["state"] == "完成":
                print(f"- ⚠ {path.stem}#{t['id']}：任务已打勾，但 {t['issue']} 还开着（要不要去 GitHub 关掉，你定）")
    print(f"\n{changed} 个任务要更新" + ("" if a.run or not changed else "，加 --run 写回计划"))


def mark_done(path, tid, day, ref):
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(lines):
        if re.match(rf"^- \[.\] {tid} ", l):
            l = re.sub(r"^- \[.\]", "- [x]", l)
            lines[i] = re.sub(r" · upd:\S+", "", l) + f" · upd:{date.today()}"
            lines.insert(i + 1, f"  - 进展：GitHub issue {ref} 已关闭（{day}）（自动）")
            break
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_status(a):
    gp = github_projects()
    if a.project:
        gp = {k: v for k, v in gp.items() if k == a.project}
    if not gp:
        print("没有项目用 GitHub 管任务（项目档案 frontmatter 加 prefs: {tasks: github, tasks_repo: owner/repo}）")
        return
    for slug, repo in gp.items():
        print(f"## {slug} · {repo}")
        try:
            ms = json.loads(gh("api", f"repos/{repo}/milestones?state=open&per_page=100"))
            for m in ms:
                total = m["open_issues"] + m["closed_issues"]
                print(f"- milestone「{m['title']}」{m['closed_issues']}/{total} 已关，截止 {(m.get('due_on') or '—')[:10]}")
            issues = json.loads(gh("issue", "list", "-R", repo, "--state", "open", "--limit", "50", "--json", "number,title,milestone"))
            for i in issues:
                print(f"  - #{i['number']} {i['title']}" + (f"（{i['milestone']['title']}）" if i.get("milestone") else ""))
        except RuntimeError as e:
            print(f"- ⚠ 读不到：{e}")
        todo = [(p.stem, t) for p in plan_files() for t in plan.parse_plan(p)[1]
                if t["proj"] == slug and t["state"] != "完成" and not t.get("issue")]
        if todo:
            print("- 还没建 issue 的任务：" + "；".join(f"{s}#{t['id']} {t['title'][:20]}" for s, t in todo))
        print()


def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="项目任务放在 GitHub（milestone + issue）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status"); p.add_argument("--project")
    p = sub.add_parser("push"); p.add_argument("plan"); p.add_argument("--task", action="append"); p.add_argument("--run", action="store_true")
    p = sub.add_parser("pull"); p.add_argument("--run", action="store_true")
    a = ap.parse_args()
    {"status": cmd_status, "push": cmd_push, "pull": cmd_pull}[a.cmd](a)


if __name__ == "__main__":
    main()
