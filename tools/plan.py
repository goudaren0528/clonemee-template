r"""
计划任务视图 —— 解析 plans/ 下计划文档里的任务行（格式见 .claude/skills/plan/SKILL.md）。

    python tools/plan.py              # 所有未完成任务：逾期 / 进行中 / 受阻 / 7 天内到期 / 其他
    python tools/plan.py --owner me   # 只看某个负责人
    python tools/plan.py --agent      # 只看派给 agent 执行的（exec 不是 me / assistant）
    python tools/plan.py --brief      # 一行摘要（会话启动 hook 用）
    python tools/plan.py --report     # 进展汇报的原始表（项目、事项、当前进展、开始日期、记录日期、当前状态）
"""
from __future__ import annotations

import argparse
import re
from datetime import date, timedelta
from functools import lru_cache

import yaml

from common import ROOT, load_registry, norm_remote, setup_stdio

PLANS = ROOT / "plans"
TASK_RE = re.compile(r"^- \[(.)\] (T\d+) (.+)$")
STATE = {" ": "待办", "~": "进行中", "!": "受阻", "x": "完成", "-": "放弃"}
HUMAN = {"me", "assistant", ""}


def parse_plan(path):
    text = path.read_text(encoding="utf-8")
    fm = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            try:
                fm = yaml.safe_load(text[3:end]) or {}
            except yaml.YAMLError:
                fm = {}
    tasks = []
    for line in text.splitlines():
        sub = re.match(r"^\s+- 进展：(.+)$", line)
        if sub and tasks:
            tasks[-1]["progress"] = sub.group(1).strip()
            continue
        m = TASK_RE.match(line)
        if not m:
            continue
        mark, tid, rest = m.groups()
        parts = [p.strip() for p in rest.split(" · ")]
        task = {"plan": path.stem, "plan_title": str(fm.get("summary") or path.stem),
                "plan_status": str(fm.get("status", "active")), "id": tid,
                "state": STATE.get(mark, "待办"), "title": parts[0], "owner": "", "exec": "",
                "proj": "", "repo": "", "due": "", "dep": "", "run": "", "start": "", "upd": "", "issue": "", "progress": ""}
        for p in parts[1:]:
            if p.startswith("@"):
                task["owner"] = p[1:]
            elif ":" in p:
                k, v = p.split(":", 1)
                if k in task:
                    task[k] = v.strip()
        tasks.append(task)
    return fm, tasks


def all_tasks():
    out = []
    if not PLANS.is_dir():
        return out
    today = date.today().isoformat()
    for f in sorted(PLANS.rglob("*.md")):
        if f.name in ("goals.md", "someday.md", "INDEX.md") or "prd" in f.relative_to(PLANS).parts:
            continue  # PRD 目录里的「后续任务」只是建议草稿，不是计划任务
        if f.parent.name == "daily" and f.stem != today:
            continue  # 日计划只看今天的；过去的留作记录
        _, tasks = parse_plan(f)
        out += tasks
    return out


def open_tasks(owner=None, agent=False):
    ts = [t for t in all_tasks() if t["state"] not in ("完成", "放弃")
          and t["plan_status"] not in ("done", "dropped", "paused")]
    if owner:
        ts = [t for t in ts if t["owner"] == owner]
    if agent:
        ts = [t for t in ts if t["exec"] not in HUMAN]
    return ts


def bucket(t, today):
    soon = (date.fromisoformat(today) + timedelta(days=7)).isoformat()
    if t["due"] and t["due"] < today:
        return "逾期"
    if t["state"] in ("进行中", "受阻"):
        return t["state"]
    if t["due"] and t["due"] <= soon:
        return "7 天内到期"
    return "其他"


def brief():
    today = date.today().isoformat()
    ts = open_tasks()
    drafts = sorted({t["plan"] for t in all_tasks() if t["plan_status"] == "draft"})
    if not ts and not drafts:
        return ""
    c = {}
    for t in ts:
        c[bucket(t, today)] = c.get(bucket(t, today), 0) + 1
    parts = [f"{k} {v}" for k, v in c.items() if k != "其他"]
    line = f"计划任务：未完成 {len(ts)} 个" + ("（" + "，".join(parts) + "）" if parts else "")
    if drafts:
        line += f"；待确认的草案计划 {len(drafts)} 个：" + "、".join(drafts)
    return line + "。`python tools/plan.py` 查看"


@lru_cache(maxsize=1)
def _names():
    """叫法 → 归属：项目写 slug，领域写「slug（领域）」。来源：projects/、areas/ 目录名，项目标题的主名，登记的仓库名，
    术语表里只指向一个项目的别名。"""
    names = {"health": "health（领域）", "健康": "health（领域）", "生活": "life（领域）", "财务": "finance（领域）",
             "学习": "learning（领域）"}  # 健康档案在 private/health；中文叫法是 todo、周计划里的分组名
    for d in (ROOT / "areas").glob("*/index.md"):
        names[d.parent.name.lower()] = f"{d.parent.name}（领域）"
    for f in (ROOT / "projects").glob("*/index.md"):
        slug = f.parent.name
        title = next((l[2:] for l in f.read_text(encoding="utf-8").splitlines() if l.startswith("# ")), "")
        main = re.split(r"[（(/—]", title)[0].strip().lower()
        if len(main) >= 2:
            names[main] = slug
        names[slug.lower()] = slug
    for e in (load_registry().get("tracked") or []):  # 仓库名（前后端、CMS 这类）
        for r in e.get("repos") or []:
            names.setdefault(norm_remote(r["github"]).split("/")[-1].lower(), e["slug"])
    gl = ROOT / "memory" / "glossary.md"
    for l in (gl.read_text(encoding="utf-8").splitlines() if gl.exists() else []):
        slugs = set(re.findall(r"projects/([\w-]+)/", l))
        if not l.startswith("| ") or len(slugs) != 1:
            continue
        slug = next(iter(slugs))
        for term in l.strip("| ").split("|")[0].split("/"):
            if len(term.strip()) >= 2:
                names.setdefault(term.strip().lower(), slug)
    return names


def project_of(text, fallback="?"):
    """一段文字归属哪个项目 / 领域：先看标签和链接（#project/x、projects/x/、#area/x、areas/x/、private/x/），
    再找文字里最早出现的项目叫法（slug、标题、术语表别名）；都没有返回 fallback（「?」= 推不出来，汇报时补上）。"""
    m = re.search(r"(?:#project/|projects/)([\w-]+)", text)
    if m:
        return m.group(1)
    m = re.search(r"(?:#area/|areas/|private/)([\w-]+)", text)
    if m:
        return f"{m.group(1)}（领域）"
    low, best = text.lower(), None
    for name, label in _names().items():
        if name.isascii():
            m = re.search(rf"(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])", low)
            i = m.start() if m else -1
        else:
            i = low.find(name)
        if i >= 0 and (best is None or (i, -len(name)) < best[0]):
            best = ((i, -len(name)), label)
    return best[1] if best else fallback


def task_project(t, fallback="?"):
    """计划任务归属的项目：任务行的 proj:；没写的看标题；再不行用同一个计划里其他任务最常见的 proj:（日计划、周计划除外）。"""
    if t.get("proj"):
        return t["proj"]
    got = project_of(t["title"], "")
    if got:
        return got
    if not re.match(r"\d{4}-(\d{2}-\d{2}|W\d{2})$", t["plan"]):
        projs = [x["proj"] for x in all_tasks() if x["plan"] == t["plan"] and x["proj"]]
        if projs:
            return max(set(projs), key=projs.count)
    return fallback


def project_rows():
    """项目档案的进展原始数据：当前状态首条、开始日期（时间线最早一行）、记录日期（当前状态的更新日期）。"""
    rows = []
    for f in sorted((ROOT / "projects").glob("*/index.md")):
        text = f.read_text(encoding="utf-8")
        fm = {}
        if text.startswith("---"):
            end = text.find("\n---", 3)
            try:
                fm = yaml.safe_load(text[3:end]) or {}
            except yaml.YAMLError:
                fm = {}
        title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), f.parent.name)
        sec = re.search(r"^## 当前状态\s*\n(.*?)(?=^## )", text, re.S | re.M)
        body = sec.group(1) if sec else ""
        upd = re.search(r"_(?:更新|自动巡检)：?\s*(\d{4}-\d{2}-\d{2})", body)
        first = next((l.strip("- ").strip() for l in body.splitlines()
                      if l.strip().startswith("- ") and "更新" not in l[:8]), "")
        if not first:
            first = next((l.strip() for l in body.splitlines() if l.strip() and not l.startswith("_")), "")
        dates = re.findall(r"^- (\d{4}-\d{2}-\d{2}) —", text, re.M)
        rows.append({"slug": f.parent.name, "title": title, "status": str(fm.get("status", "active")),
                     "progress": re.sub(r"\s+", " ", first)[:120],
                     "start": str(fm.get("started") or (min(dates) if dates else ""))[:10],
                     "upd": upd.group(1) if upd else str(fm.get("updated", ""))[:10]})
    return rows


def report(proj=None, plan_slug=None):
    """汇报用的原始数据（Markdown 表格），由 progress-report skill 再加工。第一列是项目（slug；推不出来的标 ?）。"""
    STATUS = {"active": "进行中", "paused": "暂停", "done": "已完成", "superseded": "已废弃", "dropped": "已放弃"}
    head = ["| 项目 | 事项 | 当前进展 | 开始日期 | 记录日期 | 当前状态 |", "|---|---|---|---|---|---|"]
    out = []
    if not plan_slug:
        rows = [r for r in project_rows() if not proj or r["slug"] == proj]
        out += ["## 项目", ""] + head
        for r in rows:
            out.append(f"| {r['slug']} | 项目整体 | {r['progress'] or '—'} | {r['start'] or '—'} | {r['upd'] or '—'} | "
                       f"{STATUS.get(r['status'], r['status'])} |")
        out.append("")
    ts = [t for t in all_tasks() if (not proj or t["proj"] == proj) and (not plan_slug or plan_slug in t["plan"])]
    if ts:
        out += ["## 计划任务", ""] + head
        for t in ts:
            state = t["state"] + ("（草案）" if t["plan_status"] == "draft" else "")
            out.append(f"| {task_project(t)} | {t['plan']}#{t['id']} {t['title']} | {t['progress'] or '—'} | "
                       f"{t['start'] or '—'} | {t['upd'] or '—'} | {state} |")
    return "\n".join(out)


def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="计划任务视图")
    ap.add_argument("--owner")
    ap.add_argument("--agent", action="store_true")
    ap.add_argument("--brief", action="store_true")
    ap.add_argument("--report", action="store_true", help="输出进展汇报的原始数据表（项目 + 计划任务）")
    ap.add_argument("--proj", help="--report 时只看某个项目")
    ap.add_argument("--plan", help="--report 时只看某个计划")
    args = ap.parse_args()

    if args.report:
        print(report(args.proj, args.plan))
        return
    if args.brief:
        print(brief() or "没有未完成的计划任务。")
        return
    today = date.today().isoformat()
    ts = open_tasks(args.owner, args.agent)
    if not ts:
        print("没有未完成的计划任务。")
        return
    order = ["逾期", "进行中", "受阻", "7 天内到期", "其他"]
    groups = {k: [] for k in order}
    for t in ts:
        groups[bucket(t, today)].append(t)
    for k in order:
        if not groups[k]:
            continue
        print(f"## {k}（{len(groups[k])}）")
        for t in sorted(groups[k], key=lambda t: (t["due"] or "9999", t["plan"], t["id"])):
            meta = " · ".join(x for x in [
                ("@" + t["owner"]) if t["owner"] else "",
                ("exec:" + t["exec"]) if t["exec"] else "",
                ("proj:" + t["proj"]) if t["proj"] else "",
                ("due:" + t["due"]) if t["due"] else "",
                ("草案" if t["plan_status"] == "draft" else ""),
            ] if x)
            print(f"- [{t['state']}] {t['plan']}#{t['id']} {t['title']}" + (f"  （{meta}）" if meta else ""))
        print()


if __name__ == "__main__":
    main()
