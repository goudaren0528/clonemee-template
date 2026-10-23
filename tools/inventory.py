r"""
事情盘点 —— 从所有来源收集「未结的事」，去重后列成清单，一件不漏。

来源：计划任务、todo.md、项目档案的在做 / 待办 / 待确认、领域档案、会话记忆的未结和我的承诺、
thread 类记忆卡、近两周 journal 里的待办、近 7 天夜间巡检的「需要你拍板」、会议行动项、someday 清单。

    python tools/inventory.py              # 打印清单（按来源分组）
    python tools/inventory.py --brief      # 一行计数（早间建议、会话启动用）
    python tools/inventory.py --write      # 写成本周的盘点表 reviews/inventory/YYYY-Www.md（带决定列）
"""
from __future__ import annotations

import argparse
import re
from datetime import date, timedelta

import plan
import recall
from common import ROOT, setup_stdio

CHECK = re.compile(r"^\s*- \[([ ~!])\] (.+)$")   # [ ] 待办、[~] 进行中、[!] 受阻都算没结束
GROUPS = ["计划任务", "项目", "零散待办", "领域", "记忆：未结与承诺", "巡检待拍板", "journal 待办", "会议行动项", "以后再说"]


def clean(text):
    """去掉链接、代码标记、来源后缀，用于展示和去重。"""
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\s+·\s+\[?(计划|journal|项目|档案)\]?.*$", "", t)
    return re.sub(r"\s+", " ", t).strip(" -—·")


def key(text):
    t = re.sub(r"^【[^】]*】", "", clean(text))          # 去掉「【项目 · 待办】」这类分组前缀
    t = re.sub(r"^\S+#T\d+\s*", "", t)                   # 去掉「计划名#T3」
    t = re.sub(r"[（(][^）)]*[）)]", "", t)              # 去掉括号里的补充说明
    return re.sub(r"[\W_]+", "", t.lower())[:80]


def similar(a, b):
    """中文双字切片的重叠度：取较小集合的覆盖率，措辞不同但说的是同一件事也能认出来。"""
    if not a or not b:
        return False
    if a == b or (len(a) > 6 and (a in b or b in a)):
        return True
    sa = {a[i:i + 2] for i in range(len(a) - 1)}
    sb = {b[i:i + 2] for i in range(len(b) - 1)}
    return len(sa & sb) / max(1, min(len(sa), len(sb))) >= 0.6


def section(text, title):
    m = re.search(rf"^##+ {re.escape(title)}[^\n]*\n(.*?)(?=^##+ |\Z)", text, re.S | re.M)
    return m.group(1) if m else ""


def checks(body):
    mark = {" ": "", "~": "（进行中）", "!": "（受阻）"}
    return [mark[m.group(1)] + m.group(2).strip() for l in body.splitlines() if (m := CHECK.match(l))
            and "_待补_" not in m.group(2) and len(m.group(2).strip()) > 2]


def checks_ctx(text):
    """[(小节开头, 条目)]：按「## 」切小节，小节开头（标题和标签行）用来推条目归属的项目。"""
    return [("\n".join(part.splitlines()[:3]), t) for part in re.split(r"(?m)^(?=## )", text) for t in checks(part)]


def fm_date(path, text):
    m = re.search(r"^updated:\s*(\d{4}-\d{2}-\d{2})", text, re.M)
    if m:
        return m.group(1)
    m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    return m.group(1) if m else ""


def collect(today=None):
    today = today or date.today()
    items = []

    def add(group, text, src, when="", proj="", ctx=""):
        """proj 没给就推：先看条目原文（链接、标签、项目叫法），再看 ctx（所在小节的标题和标签行），都推不出来标「?」。"""
        proj = proj or plan.project_of(text, "") or plan.project_of(ctx)
        items.append({"group": group, "proj": proj, "text": clean(text), "src": src.relative_to(ROOT).as_posix(), "when": when})

    for t in plan.open_tasks():
        meta = " · ".join(x for x in [t["state"], ("@" + t["owner"]) if t["owner"] else "",
                                      ("due:" + t["due"]) if t["due"] else "",
                                      "草案" if t["plan_status"] == "draft" else ""] if x)
        f = next((p for p in (ROOT / "plans").rglob(t["plan"] + ".md")), ROOT / "plans")
        items.append({"group": "计划任务", "proj": plan.task_project(t), "text": f"{t['plan']}#{t['id']} {t['title']}（{meta}）",
                      "src": f.relative_to(ROOT).as_posix(), "when": t["upd"] or t["start"]})

    todo = ROOT / "todo.md"
    if todo.exists():
        tx = todo.read_text(encoding="utf-8")
        for ctx, t in checks_ctx(tx):
            add("零散待办", t, todo, fm_date(todo, tx), ctx=ctx.splitlines()[0])

    for f in sorted((ROOT / "projects").glob("*/index.md")):
        tx = f.read_text(encoding="utf-8")
        if re.search(r"^status:\s*(done|superseded|dropped)", tx, re.M):
            continue
        for sec in ("在做", "待办", "待确认", "改进清单"):
            for t in checks(section(tx, sec)):
                add("项目", f"【{sec}】{t}", f, fm_date(f, tx), proj=f.parent.name)

    for f in sorted((ROOT / "areas").glob("*/index.md")):
        tx = f.read_text(encoding="utf-8")
        for sec in ("在做", "待办", "待确认"):
            for t in checks(section(tx, sec)):
                add("领域", f"【{sec}】{t}", f, fm_date(f, tx), proj=f"{f.parent.name}（领域）")

    for f in sorted(recall.SESSIONS.glob("*.md")) if recall.SESSIONS.is_dir() else []:
        fm, body = recall.parse(f)
        if str(fm.get("status", "active")) != "active":
            continue
        projs = [x for x in fm.get("topics") or [] if (ROOT / "projects" / str(x)).is_dir()]
        for sec in ("未结", "我的承诺"):
            for t in checks(section(body, sec)):
                add("记忆：未结与承诺", f"【{sec}】{t}", f, str(fm.get("updated", ""))[:10],
                    ctx=f"projects/{projs[0]}/" if len(projs) == 1 else "")
    for f in sorted(recall.LONG.glob("*.md")) if recall.LONG.is_dir() else []:
        fm, _ = recall.parse(f)
        if fm.get("type") == "thread" and str(fm.get("status", "active")) == "active":
            add("记忆：未结与承诺", f"【线索 {fm.get('id')}】{fm.get('title')}", f, str(fm.get("updated", ""))[:10])

    for f in sorted((ROOT / "reviews" / "nightly").glob("*.md")):
        d = re.search(r"(\d{4}-\d{2}-\d{2})", f.name)
        if not d or date.fromisoformat(d.group(1)) < today - timedelta(days=7):
            continue
        for l in section(f.read_text(encoding="utf-8"), "下一步建议").splitlines():
            pass
        body = f.read_text(encoding="utf-8")
        m = re.search(r"\*\*需要你拍板\*\*\s*\n(.*?)(?=\n## |\Z)", body, re.S)
        for l in (m.group(1) if m else "").splitlines():
            if l.strip().startswith("- "):
                add("巡检待拍板", l.strip()[2:], f, d.group(1))

    for f in sorted((ROOT / "journal").rglob("????-??-??.md")):
        if date.fromisoformat(f.stem) < today - timedelta(days=14):
            continue
        for ctx, t in checks_ctx(f.read_text(encoding="utf-8")):
            add("journal 待办", t, f, f.stem, ctx=ctx)  # ctx：条目标题和标签行（#project/<slug>）

    for f in sorted((ROOT / "meetings").glob("*.md")):
        tx = f.read_text(encoding="utf-8")
        tags = (re.search(r"^tags:(.*)$", tx, re.M) or [None, ""])[1]  # 会议的 tags 里有 project/<slug>
        for sec, label in (("我的行动项", "我"), ("他人行动项", "等别人")):
            for t in checks(section(tx, sec)):
                add("会议行动项", f"【{label}】{t}", f, fm_date(f, tx), ctx=tags.replace("project/", "#project/"))

    some = ROOT / "plans" / "someday.md"
    if some.exists():
        tx = some.read_text(encoding="utf-8")
        for ctx, t in checks_ctx(tx):
            add("以后再说", t, some, fm_date(some, tx), ctx=ctx.splitlines()[0])

    # 去重：同一件事出现在多个来源时只留一条（优先级按 GROUPS 顺序），记下其他来源
    items.sort(key=lambda i: GROUPS.index(i["group"]))
    kept = []
    for it in items:
        k = key(it["text"])
        # 同一文件里的不同条目是不同的事，不合并
        dup = next((x for x in kept if x["src"] != it["src"] and similar(k, x["key"])), None)
        if dup:
            dup["also"].append(it["src"])
            continue
        it.update(key=k, also=[])
        kept.append(it)
    for n, it in enumerate(kept, 1):
        it["id"] = f"I{n}"
    return kept


def last_gtd():
    """最近一次完成 GTD 事情盘点的日期（盘点表 status: done 的 completed / updated）。没有返回 None。"""
    best = None
    for f in (ROOT / "reviews" / "inventory").glob("*.md"):
        fm, _ = recall.parse(f)
        if str(fm.get("status")) != "done":
            continue
        d = str(fm.get("completed") or fm.get("updated") or "")[:10]
        try:
            d = date.fromisoformat(d)
        except ValueError:
            continue
        best = max(best, d) if best else d
    return best


def gtd_status(today=None):
    """GTD 是否超期。返回 (是否需要提醒, 提示文字)。规则：每个周日都要做；任何时候都不能超过 7 天没做。"""
    today = today or date.today()
    last = last_gtd()
    last_sunday = today - timedelta(days=(today.weekday() + 1) % 7)  # 今天是周日就是今天
    since = (today - last).days if last else None
    last_txt = f"上次 {last}（{since} 天前）" if last else "还从没做过"
    if today.weekday() == 6:
        if last == today:
            return False, f"今天的 GTD 事情盘点已完成（{last}）"
        return True, f"今天周日：该做 GTD 事情盘点了（{last_txt}），你发起后按 weekly-inventory skill 进行"
    if last and last >= last_sunday - timedelta(days=1):  # 周六提前做也算
        return False, f"本周 GTD 已完成（{last}）"
    if last is None:  # 新仓库：还没有任何盘点记录，提示一次即可
        return True, "还没做过 GTD 事情盘点；第一次用可以在周日发起一次（weekly-inventory skill）"
    level = "**已超过 7 天，必须尽快做**" if since > 7 else "不能超过 7 天"
    return True, (f"上个周日（{last_sunday}）没有做 GTD 事情盘点，{last_txt}；{level}。"
                  "开场先提醒你发起一次（weekly-inventory skill）")


def brief(items=None):
    items = items if items is not None else collect()
    c = {}
    for it in items:
        c[it["group"]] = c.get(it["group"], 0) + 1
    parts = [f"{g} {c[g]}" for g in GROUPS if g in c]
    return f"未结事项 {len(items)} 件" + (f"（{'，'.join(parts)}）" if parts else "")


def render(items, table=False):
    L = []
    for g in GROUPS:
        group = [i for i in items if i["group"] == g]
        if not group:
            continue
        L += [f"## {g}（{len(group)}）", ""]
        if table:
            L += ["| # | 项目 | 事项 | 来源 | 记录于 | 决定 | 目标 / 完成标准 | 时间 | 顺序 |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for i in group:
            src = f"[{i['src']}](../../{i['src']})" + (f" 等 {len(i['also']) + 1} 处" if i["also"] else "")
            if table:
                L.append(f"| {i['id']} | {i['proj']} | {i['text'].replace('|', '/')} | {src} | {i['when'] or '—'} | | | | |")
            else:
                L.append(f"- {i['id']} {i['proj']}：{i['text']}  —— {i['src']}" + (f"（另见 {len(i['also'])} 处）" if i["also"] else "")
                         + (f" · {i['when']}" if i["when"] else ""))
        L.append("")
    return L


def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="事情盘点")
    ap.add_argument("--brief", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--gtd", action="store_true", help="GTD 是否超期")
    args = ap.parse_args()
    if args.gtd:
        print(gtd_status()[1])
        return
    items = collect()
    if args.brief:
        print(brief(items))
        return
    if not args.write:
        print("\n".join([brief(items), ""] + render(items)))
        return
    y, w, _ = date.today().isocalendar()
    out = ROOT / "reviews" / "inventory" / f"{y}-W{w:02d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    head = ["---", "type: review", f"slug: inventory-{y}-W{w:02d}", "status: draft", f"updated: {date.today()}",
            "tags: [inventory, weekly]", f"summary: {brief(items)}", "---", "",
            f"# 事情盘点 · {y} 年第 {w} 周", "",
            f"> 由 tools/inventory.py 于 {date.today()} 生成，{brief(items)}。按 weekly-inventory skill 逐组过一遍，填「决定」等列。",
            "> 决定：做 · 委托（谁）· 等别人 · 以后再说 · 不做 · 已完成；「项目」列是工具推的，「?」= 推不出来，过的时候补上", "",
            "## 收集：脑子里还没记下的", "", "| # | 项目 | 事项 | 决定 | 目标 / 完成标准 | 时间 | 顺序 |",
            "|---|---|---|---|---|---|---|", "| N1 | | | | | | |", ""]
    out.write_text("\n".join(head + render(items, table=True)), encoding="utf-8")
    print(f"已写入 {out.relative_to(ROOT).as_posix()}：{brief(items)}")


if __name__ == "__main__":
    main()
