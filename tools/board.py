r"""
台账扫描 —— 今日计划「推进位与排队」三栏对未结任务，找出漏网的。

    python tools/board.py                     # 表格：不在三栏 / 暂停没写复查日期 / 复查日期到了或过期
    python tools/board.py --brief             # 一行摘要（sweep 末尾、开场卡片用；没问题时不输出）
    python tools/board.py --json              # 机读（morning-brief、别的工具用）
    python tools/board.py --date 2027-03-09   # 按某天的计划算（默认今天）

三类问题（过期的排在前面）：
  不在三栏    `plan.py` 的未结任务、`todo.md` 的未结项，三栏（推进中 / 排队 / 暂停与等外部）里哪栏都没有
  缺复查日期  「暂停与等外部」一行没写回来看的日期——没日期就不会有人回来看
  到期 / 过期 回来看的日期已到（今天）或过期

只读：不改今日计划、不写待处理清单；要你处理的事由主会话按 report-cards 出卡片。
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import plan
from common import ROOT, setup_stdio

BOARD_TITLE = "推进位与排队"
KINDS = {"slot": "推进中", "queue": "排队", "paused": "暂停与等外部"}
PROBLEM = {"overdue": "复查日期已过期", "due": "复查日期今天到期",
           "no-date": "暂停没写复查日期", "missing": "不在三栏"}
LABEL = {"overdue": "过期", "due": "今天到期", "no-date": "缺复查日期", "missing": "不在三栏"}
ORDER = {"overdue": 0, "due": 1, "no-date": 2, "missing": 3}
MATCH_SAME_PROJ = 0.34  # 同一项目里，事项文字重叠到这个比例就算台账里已经有它
MATCH_STRONG = 0.6      # 项目对不上（或没写）时，文字得几乎一样才算


# ---------- 今日计划：三栏 ----------

def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _col_key(name):
    """表头一格 → 字段名；认几种写法（回来看 / 复查日期、等谁 / 等什么）。"""
    n = re.sub(r"\s+", "", name)
    if n == "项目":
        return "project"
    if n == "事项":
        return "item"
    if n == "设备":
        return "device"
    if n in ("等谁/等什么", "等谁等什么", "等谁", "等什么"):
        return "waiting"
    if n in ("回来看", "复查日期", "什么时候回来看"):
        return "review"
    if n.startswith("位"):
        return "slot"
    if n.startswith("排队"):
        return "queue"
    if n == "现在":
        return "now"
    if n == "下一步":
        return "next"
    if n == "派发已确认":
        return "confirmed"
    if n == "为什么排这里":
        return "why"
    return n


def _table_kind(keys):
    """三张表按表头认：有「等谁 / 回来看」是暂停栏，第一列「排队」是排队栏，第一列「位」是推进栏。"""
    if "review" in keys or "waiting" in keys:
        return "paused"
    if "queue" in keys:
        return "queue"
    if "slot" in keys:
        return "slot"
    return None


def _tables(block):
    """一节里的连续表格行（以 | 开头和结尾的行）。"""
    out, cur = [], []
    for line in block.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            cur.append(s)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def board_rows(text):
    """今日计划「推进位与排队」一节的三张表：返回 (有没有这一节, [{kind, project, item, waiting, review, slot}])。"""
    m = re.search(rf"^##\s+{re.escape(BOARD_TITLE)}\s*$", text, re.M)
    if not m:
        return False, []
    rest = text[m.end():]
    nxt = re.search(r"^##\s", rest, re.M)
    block = rest[:nxt.start()] if nxt else rest
    rows = []
    for tbl in _tables(block):
        keys = [_col_key(h) for h in _cells(tbl[0])]
        kind = _table_kind(keys)
        if not kind:
            continue
        for line in tbl[1:]:
            cs = _cells(line)
            if all(c and set(c) <= set("-:") for c in cs):  # 分隔行 |---|---|
                continue
            row = dict(zip(keys, cs))
            row["kind"] = kind
            rows.append(row)
    return True, rows


# ---------- 复查日期 ----------

FULL_DATE = re.compile(r"(20\d{2})-(\d{1,2})-(\d{1,2})")
SHORT_DATE = re.compile(r"(?<!\d)(\d{1,2})-(\d{1,2})(?!\d)")
RELATIVE = (("大后天", 3), ("后天", 2), ("明天", 1), ("今天", 0))


def review_date(text, today):
    """「回来看」一格 → 日期：2027-03-10 / 03-10 / 明天 / 今天；写「等你说」「—」这类返回 None。"""
    t = (text or "").strip()
    m = FULL_DATE.search(t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = SHORT_DATE.search(t)
    if m:
        try:
            d = date(today.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
        if (today - d).days > 180:  # 跨年：十月写的 01-05 指的是明年
            d = date(d.year + 1, d.month, d.day)
        return d
    for word, days in RELATIVE:
        if word in t:
            return today + timedelta(days=days)
    return None


# ---------- 未结任务和 todo ----------

TODO_RE = re.compile(r"^- \[( |~|!)\] (.+)$")


def _due_of(text):
    m = FULL_DATE.search(text or "")
    return m.group(0) if m else ""


def todo_items(path):
    """todo.md 里 `[ ]` / `[~]` / `[!]` 的条目：{proj, title, source, due}。"""
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        m = TODO_RE.match(line.strip())
        if not m:
            continue
        body = m.group(2).strip()
        out.append({"proj": plan.project_of(body), "title": body.split(" · ")[0].strip(),
                    "source": "todo", "due": _due_of(body)})
    return out


def _match_title(title):
    """比对用的标题：去掉任务号和开头的「项目：」。"""
    t = re.sub(r"^\s*T\d+\s+", "", title or "")
    return re.sub(r"^[^：:\s]{1,16}[：:]\s*", "", t)


# ---------- 文字比对 ----------

ASCII_WORD = re.compile(r"[a-z0-9][a-z0-9_.+#-]*")
CJK_RUN = re.compile(r"[一-鿿]+")


def _norm_text(text):
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text or "")  # 链接只留文字
    t = re.sub(r"[`*_#>|\[\]()（）—–~]", " ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def _tokens(text):
    """ASCII 词 + 中文两字组：够粗，换个说法也能对上大部分。"""
    t = _norm_text(text)
    out = {w for w in ASCII_WORD.findall(t) if len(w) >= 2 and not w.isdigit()}
    for run in CJK_RUN.findall(t):
        if len(run) == 1:
            out.add(run)
        out |= {run[i:i + 2] for i in range(len(run) - 1)}
    return out


def similarity(a, b):
    """两段文字的重叠比例（相对短的一方）：0～1。"""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _same_proj(a, b):
    """项目一栏可比：没写、写了「?」的当兼容；「life」和「life（领域）」算同一个。"""
    clean = lambda s: re.sub(r"（领域）", "", (s or "").strip().lower())
    a, b = clean(a), clean(b)
    if not a or not b or "?" in (a, b):
        return True
    return a == b or a.startswith(b) or b.startswith(a)


def row_matches(title, proj, row):
    """一条未结任务是不是台账里的这一行：任务号对上（项目也要对得上），或者文字够像。"""
    text = row.get("item") or ""
    if not text:
        return False
    same = _same_proj(proj, row.get("project"))
    for tid in set(re.findall(r"(?<![A-Za-z0-9])T\d+(?![0-9])", title or "")):
        if re.search(rf"(?<![A-Za-z0-9]){tid}(?![0-9])", text) and same:
            return True
    sim = similarity(_match_title(title), text)
    return (same and sim >= MATCH_SAME_PROJ) or sim >= MATCH_STRONG


# ---------- 扫描 ----------

def _issue(project, item, kind, review, advice, when=None):
    return {"project": project or "?", "item": (item or "—").strip(), "kind": kind,
            "problem": PROBLEM[kind], "review": review or "—", "advice": advice, "_when": when}


def _shown(day, raw):
    return day.isoformat() if day else (raw or "—").strip()


def scan(today=None, daily=None, todo=None, tasks=None):
    """扫三类问题。today / daily / todo / tasks 只为测试和补跑留口子（默认今天、真实文件、plan.open_tasks）。"""
    today = today or date.today()
    daily = Path(daily) if daily else ROOT / "plans" / "daily" / f"{today.isoformat()}.md"
    todo = Path(todo) if todo else ROOT / "todo.md"
    has_board, rows = board_rows(daily.read_text(encoding="utf-8") if daily.exists() else "")
    issues = []

    # 1. 暂停与等外部：复查日期
    for r in rows:
        if r.get("kind") != "paused":
            continue
        raw = (r.get("review") or "").strip()
        day = review_date(raw, today)
        if day is None:
            issues.append(_issue(r.get("project"), r.get("item"), "no-date", raw,
                                 "补上回来看的日期（YYYY-MM-DD）：没日期就不会有人回来看"))
        elif day < today:
            issues.append(_issue(r.get("project"), r.get("item"), "overdue", day.isoformat(),
                                 f"已过期 {(today - day).days} 天：今天接手（进推进中或排队）或重定复查日期，"
                                 "按 report-cards 出卡片问你", day))
        elif day == today:
            issues.append(_issue(r.get("project"), r.get("item"), "due", day.isoformat(),
                                 "今天到期：条件满足了就进推进中或排队，没满足就补新的复查日期，出卡片问你", day))

    # 2. 未结任务 / todo：三栏里哪栏都没有
    items = [{"proj": plan.task_project(t), "title": t.get("title", ""),
              "source": f"{t.get('plan', '')}#{t.get('id', '')}", "due": t.get("due", "")}
             for t in (plan.open_tasks() if tasks is None else tasks)]
    items += todo_items(todo)
    items.sort(key=lambda it: (0 if str(it["source"]).startswith(today.isoformat()) else 1 if it["source"] != "todo" else 2,
                               _due_of(it["due"]) or "9999", it["title"]))
    kept = []
    for it in items:
        if any(row_matches(it["title"], it["proj"], r) for r in rows):
            continue
        if any(it["proj"] == k["proj"] and similarity(it["title"], k["title"]) >= 0.6 for k in kept):
            continue  # 日计划、周计划和 todo 写的是同一件事：只报一次
        kept.append(it)
        day = review_date(it["due"], today)
        advice = ("零散小事：加进排队栏，或写明不占推进位（做完就从 todo 删）" if it["source"] == "todo"
                  else "加进三栏：要派 → 排队；等别人 → 暂停与等外部（等谁、等什么、回来看）；不做 → 标放弃")
        issues.append(_issue(it["proj"], it["title"], "missing", _shown(day, it["due"]), advice, day))

    issues.sort(key=lambda i: (ORDER[i["kind"]], i["_when"] or date.max, i["project"], i["item"]))
    for i in issues:
        i.pop("_when", None)
    counts = Counter(i["kind"] for i in issues)
    return {"date": today.isoformat(), "has_board": has_board,
            "counts": {k: counts.get(k, 0) for k in ORDER}, "issues": issues}


def _brief(res):
    """一行摘要：台账有 N 项要处理：过期 x、今天到期 y……（没问题返回空串）。"""
    issues = res["issues"]
    if not issues:
        return ""
    if not res["has_board"]:
        return f"今日计划没有「{BOARD_TITLE}」三栏：{len(issues)} 项未结任务都不在台账里（`python tools/board.py` 看清单）"
    c = Counter(i["kind"] for i in issues)
    parts = "、".join(f"{LABEL[k]} {c[k]}" for k in ORDER if c.get(k))
    first = issues[0]
    return f"台账有 {len(issues)} 项要处理：{parts}（先看 {first['project']}：{_short(first['item'])}）"


def brief(today=None, daily=None, todo=None, tasks=None):
    """给 sweep、开场卡片用的一行摘要（没问题时是空串）。"""
    return _brief(scan(today, daily, todo, tasks))


def _short(text, n=24):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[:n] + "…"


def _cell(text):
    return re.sub(r"\s+", " ", str(text)).replace("|", "/")


def render(issues):
    """表格：项目、事项、问题、复查日期、建议。"""
    if not issues:
        return "台账齐全：未结任务都在三栏里，暂停的都有复查日期，也没有到期的。"
    out = ["| 项目 | 事项 | 问题 | 复查日期 | 建议 |", "|---|---|---|---|---|"]
    for i in issues:
        out.append("| " + " | ".join(_cell(i[k]) for k in ("project", "item", "problem", "review", "advice")) + " |")
    return "\n".join(out)


def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="台账扫描：不在三栏 / 暂停没写复查日期 / 复查日期到了或过期")
    ap.add_argument("--json", action="store_true", help="机读输出")
    ap.add_argument("--brief", action="store_true", help="一行摘要（没问题不输出）")
    ap.add_argument("--date", help="按哪天的计划算（YYYY-MM-DD，默认今天）")
    ap.add_argument("--daily", help="今日计划文件（默认 plans/daily/<日期>.md）")
    ap.add_argument("--todo", help="todo 文件（默认 todo.md）")
    a = ap.parse_args()
    res = scan(date.fromisoformat(a.date) if a.date else None, a.daily, a.todo)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif a.brief:
        line = _brief(res)
        if line:
            print(line)
    else:
        if not res["has_board"]:
            print(f"⚠ 今日计划没有「{BOARD_TITLE}」三栏——按 templates/daily-plan.md 补上，下面按「都不在台账里」算\n")
        print(render(res["issues"]))
        if res["issues"]:
            c = Counter(i["kind"] for i in res["issues"])
            print(f"\n共 {len(res['issues'])} 项：" + "、".join(f"{LABEL[k]} {c[k]}" for k in ORDER if c.get(k)))


if __name__ == "__main__":
    main()
