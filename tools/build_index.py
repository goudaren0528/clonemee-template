r"""
索引生成器 —— 渐进式披露的 L1 层：根 INDEX.md（总地图）+ 各目录 INDEX.md，全部从文件的
frontmatter（title / summary / status / updated）自动生成，不手工维护。

    python tools/build_index.py                     # 生成全部索引（会话启动 hook 也会自动跑）
    python tools/build_index.py --backfill-summary  # 给缺 summary 的文档补上（从「一句话」或首段提取）
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from urllib.parse import unquote

import inventory
import recall
from common import ROOT, setup_stdio

GEN = "> 自动生成（`python tools/build_index.py`），不要手改；改源文件的 frontmatter（summary / status）。"
STATUS_ORDER = {"active": 0, "draft": 1, "paused": 2, "done": 3, "superseded": 4, "dropped": 5, "archived": 6}

# 目录地图：(目录, 放什么, 索引文件)
MAP = [
    ("profile/", "关于我：身份、偏好、当前状态", "profile/INDEX.md"),
    ("plans/", "**核心**：长期目标、计划、周计划、当日计划、以后再说", "plans/INDEX.md"),
    ("projects/", "工作面档案：现状、仓库、待办、时间线", "projects/INDEX.md"),
    ("areas/", "持续性领域：生活、财务、学习（健康和财务明细在 private/）", "areas/INDEX.md"),
    ("journal/", "经历，按日期，只追加", None),
    ("memory/", "对话记忆：会话记忆（短期）、记忆卡（长期）、术语表", "memory/INDEX.md"),
    ("decisions/", "有取舍的决定及理由", "decisions/INDEX.md"),
    ("reviews/", "夜间巡检、事情盘点、进展汇报、周报月报", "reviews/INDEX.md"),
    ("people/", "协作者", "people/INDEX.md"),
    ("meetings/", "会议纪要", "meetings/INDEX.md"),
    ("playbooks/ · .claude/skills/ · templates/", "规范：skill、流程、模板", "playbooks/INDEX.md"),
    ("agents/", "Agent 区：角色化 agent（产品、规划、开发、验收、跟进、调研、排查），派发时注入", "agents/INDEX.md"),
    ("runs/", "派发记录：每次把任务派给 agent 一份（任务说明、监督、验收）；待处理清单 pending.md（派出去的会话回报主会话的消息）", None),
    ("tools/", "脚本：采集、检索、索引、定时任务、环境识别、派发", "tools/INDEX.md"),
    ("registry/", "工作面注册表（GitHub 仓库）与各设备的本地路径登记", None),
]


# ---------- 提取 ----------

def summary_of(fm, body, limit=70):
    s = str(fm.get("summary") or fm.get("description") or "").strip()
    if not s:
        m = re.search(r"^## 一句话\s*\n+(.+)$", body, re.M)
        if m:
            s = m.group(1)
    if not s:
        s = next((l for l in body.splitlines() if l.strip() and not l.startswith(("#", ">", "|", "<!--", "```", "---", "_"))), "")
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"[*`]", "", s).strip(" -")
    return s if len(s) <= limit else s[:limit] + "…"


def load(path):
    fm, body = recall.parse(path)
    return {"path": path, "rel": path.relative_to(ROOT).as_posix(), "fm": fm,
            "title": recall.title_of(fm, body, path), "summary": summary_of(fm, body),
            "status": str(fm.get("status", "")), "updated": recall.date_of(fm, path)}


def link(doc, base):
    rel = doc["path"].relative_to(ROOT / base) if base else doc["path"].relative_to(ROOT)
    return f"[{doc['title']}]({rel.as_posix()})"


def write_if_changed(path, text):
    strip = lambda t: re.sub(r"\*\*生成于\*\* \S+", "", t)
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if strip(old) != strip(text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return True
    return False


def head(title, extra=""):
    return [f"# {title}", "", GEN] + ([extra] if extra else []) + ["", f"**生成于** {date.today()}", ""]


# ---------- 各目录 ----------

def projects_index():
    docs = [load(f) for f in sorted((ROOT / "projects").glob("*/index.md"))]
    L = head("项目索引", "> 一行一个工作面；细节看档案。注册表（GitHub 仓库）在 [registry/projects.yaml](../registry/projects.yaml)。")
    groups = [("进行中", lambda d: d["status"] == "active"), ("暂停", lambda d: d["status"] == "paused"),
              ("已结束", lambda d: d["status"] in ("done", "superseded", "dropped"))]
    for name, pred in groups:
        ds = [d for d in docs if pred(d)]
        if not ds:
            continue
        L += [f"## {name}（{len(ds)}）", "", "| 项目 | 一句话 | 开始 | 更新 |", "|---|---|---|---|"]
        for d in sorted(ds, key=lambda d: d["updated"], reverse=True):
            L.append(f"| {link(d, 'projects')} | {d['summary']} | {str(d['fm'].get('started', '—'))[:10]} | {d['updated']} |")
        L.append("")
    return docs, L


def plans_index():
    plans = [load(f) for f in sorted((ROOT / "plans").glob("*.md")) if f.name != "INDEX.md"]
    weekly = [load(f) for f in sorted((ROOT / "plans" / "weekly").glob("*.md"), reverse=True)]
    cutoff = (date.today() - timedelta(days=7)).isoformat()
    daily = [load(f) for f in sorted((ROOT / "plans" / "daily").glob("*.md"), reverse=True) if f.stem >= cutoff]
    L = head("计划索引", "> 计划是核心文档，格式见 [plan skill](../.claude/skills/plan/SKILL.md)。未完成的任务：`python tools/plan.py`。")
    L += ["## 目标与计划", "", "| 计划 | 摘要 | 状态 | 更新 |", "|---|---|---|---|"]
    for d in sorted(plans, key=lambda d: (STATUS_ORDER.get(d["status"], 9), d["updated"])):
        L.append(f"| {link(d, 'plans')} | {d['summary']} | {d['status'] or '—'} | {d['updated']} |")
    L += ["", "## 周计划", ""] + ([f"- {link(d, 'plans')} — {d['summary']}" for d in weekly] or ["_暂无。_"])
    L += ["", "## 当日计划（近 7 天）", ""] + ([f"- {link(d, 'plans')} — {d['status']} · {d['summary']}" for d in daily] or ["_暂无。_"])
    return plans, L + [""]


def simple_index(folder, title, intro, pattern="*.md", limit=None, reverse=True):
    docs = [load(f) for f in sorted((ROOT / folder).glob(pattern), reverse=reverse) if f.name not in ("INDEX.md", ".gitkeep")]
    L = head(title, intro)
    shown = docs[:limit] if limit else docs
    L += [f"- {link(d, folder)} — {d['summary'] or '—'}" + (f" · {d['status']}" if d["status"] else "") for d in shown] or ["_暂无。_"]
    if limit and len(docs) > limit:
        L.append(f"\n_还有 {len(docs) - limit} 份更早的，见目录本身。_")
    return docs, L + [""]


def areas_index():
    docs = [load(f) for f in sorted((ROOT / "areas").glob("*/index.md"))]
    L = head("领域索引", "> 持续性领域。健康和财务明细放在 `private/` 下。")
    L += [f"- {link(d, 'areas')} — {d['summary']} · 更新 {d['updated']}" for d in docs]
    return docs, L + [""]


def reviews_index():
    L = head("回顾与报告索引")
    for sub, title, limit in (("nightly", "夜间巡检（近 14 份）", 14), ("inventory", "事情盘点（GTD）", None), ("progress", "存档的进展汇报", 20)):
        docs = [load(f) for f in sorted((ROOT / "reviews" / sub).glob("*.md"), reverse=True)]
        L += [f"## {title}", ""] + ([f"- {link(d, 'reviews')} — {d['summary']}" + (f" · {d['status']}" if sub == "inventory" else "")
                                    for d in (docs[:limit] if limit else docs)] or ["_暂无。_"]) + [""]
    weekly = [load(f) for f in sorted((ROOT / "reviews").glob("*.md"), reverse=True) if f.name != "INDEX.md"]
    L += ["## 周报 / 月报", ""] + ([f"- {link(d, 'reviews')} — {d['summary']}" for d in weekly] or ["_暂无。_"]) + [""]
    return L


def rules_index():
    L = head("规范目录：skill、流程、模板", "> 高频、有触发场景的写成 skill（Claude Code 按描述自动调用）；低频参考流程是 playbook；模板供新建文件用。变更记录见 [README](README.md)。")
    skills = sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md"))
    L += ["## Skills（`.claude/skills/`）", "", "| skill | 什么时候用 |", "|---|---|"]
    for f in skills:
        fm, _ = recall.parse(f)
        desc = re.sub(r"\s+", " ", str(fm.get("description", ""))).split("：")[0].split("。")[0:2]
        L.append(f"| [{fm.get('name', f.parent.name)}](../.claude/skills/{f.parent.name}/SKILL.md) | {'。'.join(desc)} |")
    L += ["", "## Playbooks（`playbooks/`）", ""]
    for f in sorted((ROOT / "playbooks").glob("*.md")):
        if f.name in ("INDEX.md", "README.md"):
            continue
        d = load(f)
        L.append(f"- {link(d, 'playbooks')} — {d['summary'] or '—'}" + (" · 已迁移为 skill" if d["status"] == "superseded" else ""))
    L += ["", "## 模板", "", "按记录类型找模板：[templates/INDEX.md](../templates/INDEX.md)（写任何记录之前先查）。"]
    return len(skills), L + [""]


def templates_index():
    """记录类型 → 模板 总表，数据源 templates/catalog.yaml。"""
    import yaml
    cat = yaml.safe_load((ROOT / "templates" / "catalog.yaml").read_text(encoding="utf-8")) or {}
    L = head("记录类型 → 模板", "> **写任何记录之前，先在这里找到类型、读对应模板，再按模板写**。没有对应类型的新记录，先建模板（你同意）再写。\n"
             "> 数据源 [catalog.yaml](catalog.yaml)；「条目模板」见 [entries.md](entries.md)；mode：新建 / 追加 / 改写。")
    L += ["| 记录类型 | 模板 | 写到哪 | mode |", "|---|---|---|---|"]
    for t in cat.get("types") or []:
        tpl = str(t["template"])
        if tpl.startswith("entries.md#"):
            cell = f"[{tpl}](entries.md#{tpl.split('#', 1)[1]})"
        elif tpl.endswith(".md") and (ROOT / "templates" / tpl).exists():
            cell = f"[{tpl}]({tpl})"
        else:
            cell = tpl
        L.append(f"| {t['name']} | {cell} | `{t['path']}` | {t['mode']} |")
    return L + [""]


REQUIRED = ("type", "status", "updated", "summary")
CHECK_DIRS = ("profile", "plans", "projects", "areas", "decisions", "meetings", "people", "reviews",
              "memory/long", "memory/sessions", "playbooks", "private", "runs", "agents")


def check():
    """覆盖检查：模板是否存在、文档 frontmatter 是否齐全。返回问题列表。"""
    import yaml
    problems = []
    cat = yaml.safe_load((ROOT / "templates" / "catalog.yaml").read_text(encoding="utf-8")) or {}
    entries = (ROOT / "templates" / "entries.md").read_text(encoding="utf-8")
    listed = set()
    for t in cat.get("types") or []:
        tpl = str(t["template"])
        if tpl.startswith("entries.md#"):
            if f"\n## {tpl.split('#', 1)[1]}\n" not in entries:
                problems.append(f"总表「{t['name']}」引用的条目模板 {tpl} 不存在")
        elif re.fullmatch(r"[\w-]+\.md", tpl):
            listed.add(tpl)
            if not (ROOT / "templates" / tpl).exists():
                problems.append(f"总表「{t['name']}」引用的模板 templates/{tpl} 不存在")
    for f in sorted((ROOT / "templates").glob("*.md")):
        if f.name not in listed | {"entries.md", "INDEX.md"}:
            problems.append(f"模板 templates/{f.name} 没登记在 catalog.yaml")
    for d in CHECK_DIRS:
        for f in sorted((ROOT / d).rglob("*.md")):
            if f.name in ("INDEX.md",) or "archive" in f.parts:
                continue
            fm, _ = recall.parse(f)
            miss = [k for k in REQUIRED if not fm.get(k)]
            if f.parent.name == "daily" or "sessions" in f.parts:
                miss = [k for k in miss if k != "type" or not fm.get("type")]
            if miss:
                problems.append(f"{f.relative_to(ROOT).as_posix()} 缺 frontmatter：{', '.join(miss)}")
    return problems


LINK_RE = re.compile(r'!?\[[^\]\r\n]*\]\(\s*(<[^>\r\n]+>|[^)\s]+)(?:\s+"[^"\r\n]*")?\s*\)')
# 不查的目录：派发证据、运行记录、日志、会话记忆这类「历史快照」，链接指向的证据文件按规矩不入库
LINK_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".assistant", "archive",
                  "docs", "runs", "reviews", "journal", "memory", "plans", "inbox"}
LINK_REF = re.compile(r"^\s*\[[^\]]+\]:\s*(<[^>]+>|\S+)")
LINK_HTML = re.compile(r"""\b(?:href|src)=["']([^"']+)["']""")
LINK_ABS = re.compile(r"^([a-z][a-z0-9+.-]*:|#|/|\\)", re.I)


def check_links(root=None):
    """死链检查：只查仓库内的相对 Markdown 链接（跳过 http(s)、mailto、纯锚点、绝对路径、代码块里的例子）。
    返回 ["文件:行号 → 目标"]。"""
    root = root or ROOT
    problems = []
    for f in sorted(root.rglob("*.md")):
        rel = f.relative_to(root)
        if LINK_SKIP_DIRS & set(rel.parts):
            continue
        fenced = False
        for no, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
                continue
            if fenced:
                continue
            clean = re.sub(r"`[^`]*`", "", line)
            found = [m.group(1) for m in LINK_RE.finditer(clean)]
            found += LINK_REF.findall(clean) + LINK_HTML.findall(clean)  # 引用式定义、HTML 的 href / src
            for tgt in found:
                if tgt.startswith("<") and tgt.endswith(">"):
                    tgt = tgt[1:-1]
                if LINK_ABS.match(tgt) or "<" in tgt or "YYYY" in tgt:  # 模板里的占位链接不算
                    continue
                path = unquote(tgt.split("#", 1)[0].split("?", 1)[0])
                if path and not (f.parent / path).exists():
                    problems.append(f"{rel.as_posix()}:{no} → {tgt}")
    return problems


def tools_index():
    L = head("工具索引", "> 全部在本仓库 `tools/` 下，用法以各文件开头的说明为准。")
    files = sorted(list((ROOT / "tools").glob("*.py")) + list((ROOT / "tools").glob("*.ps1")) + list((ROOT / "tools").glob("*.html")))
    for f in files:
        text = f.read_text(encoding="utf-8-sig", errors="replace")
        if f.suffix == ".py":
            m = re.search(r'^r?"""\s*\n?(.*?)"""', text, re.S | re.M)
            doc = m.group(1) if m else ""
        elif f.suffix == ".ps1":
            doc = "\n".join(l.lstrip("# ") for l in text.splitlines() if l.startswith("#"))
        else:
            m = re.search(r"<title>(.*?)</title>", text)
            doc = (m.group(1) + "（浏览器打开）") if m else ""
        lines = [l.strip() for l in doc.splitlines() if l.strip()]
        first = lines[0] if lines else ""
        usage = [l for l in lines[1:] if l.startswith(("python ", "powershell ")) and "#" in l][:4]
        L.append(f"### `{f.name}`" + (" （模块）" if f.name in ("common.py",) else ""))
        L.append(first)
        if usage:
            L += ["```", *usage, "```"]
        L.append("")
    return len(files), L


# ---------- 根 INDEX ----------

def root_index(projects, plans, n_skills, n_tools):
    import plan as planmod
    today = date.today().isoformat()
    counts = {
        "profile/": len(list((ROOT / "profile").glob("*.md"))) - (1 if (ROOT / "profile" / "INDEX.md").exists() else 0),
        "plans/": len(plans), "projects/": len(projects),
        "areas/": len(list((ROOT / "areas").glob("*/index.md"))),
        "journal/": len(list((ROOT / "journal").rglob("????-??-??.md"))),
        "memory/": len(list((ROOT / "memory").rglob("*.md"))) - 1,
        "decisions/": len([f for f in (ROOT / "decisions").glob("*.md") if f.name != "INDEX.md"]),
        "reviews/": len([f for f in (ROOT / "reviews").rglob("*.md") if f.name != "INDEX.md"]),
        "people/": len([f for f in (ROOT / "people").glob("*.md") if f.name != "INDEX.md"]),
        "meetings/": len([f for f in (ROOT / "meetings").glob("*.md") if f.name != "INDEX.md"]),
        "playbooks/ · .claude/skills/ · templates/": n_skills, "tools/": n_tools,
        "registry/": len(list((ROOT / "registry").rglob("*.yaml"))),
        "agents/": len([f for f in (ROOT / "agents").glob("*.md") if f.name != "INDEX.md"]),
        "runs/": len([f for f in (ROOT / "runs").glob("*.md") if f.name != "pending.md"]),
    }
    L = ["# INDEX — 个人助手总地图", "", GEN,
         "> **渐进式披露**：L0 = CLAUDE.md + 会话启动注入（状态 + [记忆索引](memory/INDEX.md)）；"
         "L1 = 本页 + 各目录 INDEX；L2 = 具体文件。按关键词找：`python tools/recall.py 关键词`。", "",
         f"**生成于** {today}", "", "## 目录地图", "", "| 目录 | 放什么 | 数量 | 索引 |", "|---|---|---:|---|"]
    for d, what, idx in MAP:
        n = counts.get(d, "")
        n = f"{n} 个 skill" if d.startswith("playbooks") else n
        L.append(f"| `{d}` | {what} | {n} | {f'[INDEX]({idx})' if idx else '—'} |")
    journals = sorted((ROOT / "journal").rglob("????-??-??.md"))
    daily = ROOT / "plans" / "daily" / f"{today}.md"
    L += ["", "## 现在", ""]
    L.append(f"- 状态快照：[profile/now.md](profile/now.md) · 偏好：[profile/preferences.md](profile/preferences.md) · 零散待办：[todo.md](todo.md)")
    L.append(f"- 当日计划：" + (f"[{today}](plans/daily/{today}.md)" if daily.exists() else "今天还没有"))
    L.append(f"- 最近 journal：" + (f"[{journals[-1].stem}]({journals[-1].relative_to(ROOT).as_posix()})" if journals else "—"))
    L.append(f"- GTD：{inventory.gtd_status()[1]}")
    L.append(f"- {planmod.brief() or '没有未完成的计划任务'}")
    active = [p for p in projects if p["status"] == "active"]
    L += ["", f"## 进行中的项目（{len(active)}）", ""]
    L += [f"- [{p['title']}]({p['rel']}) — {p['summary'][:40]}" for p in sorted(active, key=lambda p: p["updated"], reverse=True)]
    live = [p for p in plans if p["status"] in ("active", "draft") and p["fm"].get("slug") not in ("goals", "someday")]
    L += ["", "## 进行中的计划", ""] + ([f"- [{p['title']}]({p['rel']}) — {p['status']} · {p['summary'][:50]}" for p in live] or ["_暂无。_"])
    decs = [load(f) for f in sorted((ROOT / "decisions").glob("2*.md"), reverse=True)[:5]]
    L += ["", "## 最近决策", ""] + ([f"- [{d['title']}]({d['rel']})" for d in decs] or ["_暂无。_"])
    L += ["", "## 外部系统", "", "| 系统 | 管什么 | 怎么访问 |", "|---|---|---|",
          "| GitHub | 代码与 PR | `gh` CLI（账号见 registry/projects.yaml 的 `identity.github`） |",
          "| 项目 / 团队知识库（可选） | 代码级记录、团队公共知识 | 按你用的工具填，例如团队 wiki、知识库 |",
          "| 工单 / 消息（可选） | 团队工单 | 按你用的工具填，例如邮件、工单系统 |", ""]
    return L


def build():
    changed = []
    projects, L = projects_index()
    if write_if_changed(ROOT / "projects" / "INDEX.md", "\n".join(L)): changed.append("projects")
    plans, L = plans_index()
    if write_if_changed(ROOT / "plans" / "INDEX.md", "\n".join(L)): changed.append("plans")
    _, L = areas_index()
    if write_if_changed(ROOT / "areas" / "INDEX.md", "\n".join(L)): changed.append("areas")
    for folder, title, intro in (("agents", "Agent 区", "> 角色化 agent，派发时用 `--role <slug>` 注入。说明见 [playbooks/agents.md](../playbooks/agents.md)。"),
                                 ("decisions", "决策索引", "> 倒序。决策被推翻时新建一条并回链。"),
                                 ("people", "协作者索引", ""), ("meetings", "会议索引", "> 倒序。")):
        _, L = simple_index(folder, title, intro)
        if write_if_changed(ROOT / folder / "INDEX.md", "\n".join(L)): changed.append(folder)
    _, L = simple_index("profile", "关于我", "> 会话启动时读 now 和 preferences。", reverse=False)
    if write_if_changed(ROOT / "profile" / "INDEX.md", "\n".join(L)): changed.append("profile")
    if write_if_changed(ROOT / "reviews" / "INDEX.md", "\n".join(reviews_index())): changed.append("reviews")
    n_skills, L = rules_index()
    if write_if_changed(ROOT / "playbooks" / "INDEX.md", "\n".join(L)): changed.append("playbooks")
    if write_if_changed(ROOT / "templates" / "INDEX.md", "\n".join(templates_index())): changed.append("templates")
    n_tools, L = tools_index()
    if write_if_changed(ROOT / "tools" / "INDEX.md", "\n".join(L)): changed.append("tools")
    recall.reindex()
    if write_if_changed(ROOT / "INDEX.md", "\n".join(root_index(projects, plans, n_skills, n_tools))): changed.append("INDEX")
    return changed


def backfill_summary():
    """给缺 summary 的文档补一行 summary（从「一句话」或首段提取），返回补了几个。"""
    n = 0
    for folder in ("projects", "areas", "decisions", "playbooks", "profile", "people", "meetings", "plans"):
        for f in (ROOT / folder).rglob("*.md"):
            if f.name in ("INDEX.md", "README.md") or "daily" in f.parts:
                continue
            text = f.read_text(encoding="utf-8")
            if not text.startswith("---\n") or re.search(r"^summary:", text.split("\n---", 2)[0], re.M):
                continue
            fm, body = recall.parse(f)
            s = summary_of({}, body, limit=60).replace('"', "'")
            if not s:
                continue
            end = text.find("\n---", 3)
            f.write_text(text[:end] + f'\nsummary: "{s}"' + text[end:], encoding="utf-8")
            n += 1
    return n


def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="索引生成器")
    ap.add_argument("--backfill-summary", action="store_true")
    ap.add_argument("--check", action="store_true", help="检查模板覆盖、文档 frontmatter 是否齐全，以及仓库内相对链接有没有死链")
    args = ap.parse_args()
    if args.check:
        problems = check() + [f"死链 {p}" for p in check_links()]
        print("\n".join(problems) if problems else "检查通过：模板全覆盖，文档 frontmatter 齐全，没有死链")
        sys.exit(1 if problems else 0)
    if args.backfill_summary:
        print(f"补了 {backfill_summary()} 个 summary")
    changed = build()
    print("索引已更新：" + ("、".join(changed) if changed else "无变化"))


if __name__ == "__main__":
    main()
