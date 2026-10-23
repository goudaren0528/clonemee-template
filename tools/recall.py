r"""
记忆检索与索引 —— 渐进式披露的 L1 层（规范见 playbooks/memory.md）。

    python tools/recall.py 关键词 [关键词...]   # 列出命中的记忆和记录：标题 / 日期 / 摘要 / 时效，不输出全文
    python tools/recall.py 关键词 --scope memory  # 只搜 memory/（默认搜全部记录）
    python tools/recall.py --due                 # 需要巩固或复核的记忆
    python tools/recall.py --reindex             # 重建 memory/INDEX.md（会话启动 hook 会自动跑）
    python tools/recall.py --next-id             # 下一个长期记忆卡编号
"""
from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path

import yaml

from common import ROOT, setup_stdio

MEMORY = ROOT / "memory"
SESSIONS = MEMORY / "sessions"
LONG = MEMORY / "long"
GLOSSARY = MEMORY / "glossary.md"
INDEX = MEMORY / "INDEX.md"

SCOPES = {
    "memory": ["memory/sessions", "memory/long", "memory/glossary.md"],
    "all": ["memory/sessions", "memory/long", "memory/glossary.md", "profile", "todo.md", "plans",
            "projects", "areas", "decisions", "people", "meetings", "journal", "reviews",
            "playbooks", "private"],
}
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
SHORT_ROWS = 15  # INDEX 里短期记忆最多列几条，超出就该巩固了


# ---------- 解析 ----------

def parse(path):
    """返回 (frontmatter dict, 正文)。没有 frontmatter 时返回空 dict。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            try:
                fm = yaml.safe_load(text[3:end]) or {}
                if isinstance(fm, dict):
                    return fm, text[end + 4:]
            except yaml.YAMLError:
                pass
    return {}, text


def title_of(fm, body, path):
    if fm.get("title"):
        return str(fm["title"])
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def date_of(fm, path):
    for k in ("updated", "created", "started"):
        if fm.get(k):
            return str(fm[k])[:10]
    m = DATE_RE.search(path.name)
    return m.group(1) if m else ""


def flags(fm, today):
    """时效标记：过期 / 待复核。只对 active 的记忆生效。"""
    if str(fm.get("status", "active")) != "active":
        return []
    out = []
    if fm.get("expires") and str(fm["expires"])[:10] < today:
        out.append("已过期")
    if fm.get("review_after") and str(fm["review_after"])[:10] < today:
        out.append("待复核")
    return out


def iter_files(scope, include_archive=False):
    for rel in SCOPES[scope]:
        p = ROOT / rel
        if p.is_file():
            yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*.md")):
                if "archive" in f.relative_to(ROOT).parts and not include_archive:
                    continue
                if f.name == "INDEX.md" and f.parent == MEMORY:
                    continue
                yield f


def open_items(body):
    sec = re.search(r"^## 未结\s*\n(.*?)(?=^## |\Z)", body, re.S | re.M)
    if not sec:
        return 0
    return sum(1 for l in sec.group(1).splitlines() if l.strip().startswith("- [ ]") and len(l.strip()) > 5)


# ---------- 检索 ----------

def search(keywords, scope="all", limit=12, include_archive=False):
    today = date.today().isoformat()
    kws = [k.lower() for k in keywords]
    hits = []
    for f in iter_files(scope, include_archive):
        fm, body = parse(f)
        title = title_of(fm, body, f)
        head = " ".join([title, str(fm.get("summary", "")), " ".join(map(str, fm.get("tags") or fm.get("topics") or []))]).lower()
        low = body.lower()
        matched = [k for k in kws if k in head or k in low]
        if not matched:
            continue
        score = len(matched) * 100 + sum(head.count(k) * 5 + low.count(k) for k in kws)
        # 命中的小节标题，给出更细的指针
        sections = []
        for m in re.finditer(r"^(#{2,3} .+)$", body, re.M):
            start = m.end()
            nxt = re.search(r"^#{2,3} ", body[start:], re.M)
            chunk = body[start:start + nxt.start()] if nxt else body[start:]
            if any(k in (m.group(1) + chunk).lower() for k in kws):
                sections.append(m.group(1).lstrip("# ").strip())
        hits.append((len(matched), score, date_of(fm, f), f, fm, title, sections))
    hits.sort(key=lambda h: (h[0], h[2], h[1]), reverse=True)
    out = []
    for n, (_, _, d, f, fm, title, sections) in enumerate(hits[:limit], 1):
        meta = " · ".join(x for x in [str(fm.get("type", "")), d,
                                      ("★" + str(fm["importance"])) if fm.get("importance") else ""] if x)
        fl = flags(fm, today)
        summary = str(fm.get("summary", "")).strip()
        line = f"{n}. {f.relative_to(ROOT).as_posix()}  [{meta}]  {title}"
        if summary:
            line += f" —— {summary[:80]}"
        if fl:
            line += "  ⚠" + "、".join(fl)
        out.append(line)
        for s in sections[:3]:
            out.append(f"   └ {s[:70]}")
    if len(hits) > limit:
        out.append(f"（还有 {len(hits) - limit} 条，加 --limit 查看）")
    return out or ["没有命中。换个关键词，或用 --scope all / 加 --archive 搜归档。"]


# ---------- 时效 ----------

def due():
    today = date.today().isoformat()
    items = []
    for f in sorted(SESSIONS.glob("*.md")) if SESSIONS.is_dir() else []:
        fm, _ = parse(f)
        if "已过期" in flags(fm, today):
            items.append(f"会话过期待巩固：{f.relative_to(ROOT).as_posix()}（{fm.get('summary', '')}）")
    for f in sorted(LONG.glob("*.md")) if LONG.is_dir() else []:
        fm, _ = parse(f)
        for fl in flags(fm, today):
            items.append(f"记忆卡{fl}：{f.relative_to(ROOT).as_posix()}（{fm.get('title', '')}）")
    return items


def next_id():
    nums = [int(m.group(1)) for f in (LONG.glob("M-*.md") if LONG.is_dir() else [])
            if (m := re.match(r"M-(\d+)", f.name))]
    return f"M-{(max(nums) + 1 if nums else 1):04d}"


# ---------- 索引 ----------

def _cell(s):
    return str(s or "").replace("|", "/").replace("\n", " ").strip()


def reindex():
    today = date.today().isoformat()
    sessions, cards = [], []
    for f in SESSIONS.glob("*.md") if SESSIONS.is_dir() else []:
        fm, body = parse(f)
        if str(fm.get("status", "active")) == "active":
            sessions.append((str(fm.get("started", date_of(fm, f)))[:16], f, fm, open_items(body)))
    for f in LONG.glob("*.md") if LONG.is_dir() else []:
        fm, _ = parse(f)
        if str(fm.get("status", "active")) == "active":
            cards.append((int(fm.get("importance") or 2), date_of(fm, f), f, fm))
    sessions.sort(key=lambda x: x[0], reverse=True)
    cards.sort(key=lambda x: (x[0], x[1]), reverse=True)
    n_due = len(due())

    L = ["# 记忆索引", "",
         "> 自动生成（`python tools/recall.py --reindex`），不要手改；改记忆文件本身。规范见 [playbooks/memory.md](../playbooks/memory.md)。",
         "> **L0**：会话启动时自动注入，只放一行摘要。要细节先 `python tools/recall.py <关键词>`（L1 列表），再读原文（L2）。", "",
         f"**生成于** {today} · 短期 {len(sessions)} 条 · 长期 {len(cards)} 条 · 待巩固/复核 {n_due} 条", "",
         "## 短期记忆（会话，默认 30 天过期）", ""]
    if sessions:
        L += ["| 开始 | 类型 | 会话 | 摘要 | 未结 | 过期 |", "|---|---|---|---|---:|---|"]
        for started, f, fm, n_open in sessions[:SHORT_ROWS]:
            fl = "⚠" if "已过期" in flags(fm, today) else ""
            L.append(f"| {started} | {_cell(fm.get('kind') or ('自动' if 'auto' in (fm.get('topics') or []) else '—'))} | [{_cell(fm.get('id', f.stem))}](sessions/{f.name}) | {_cell(fm.get('summary'))} | "
                     f"{n_open or ''} | {str(fm.get('expires', ''))[:10]}{fl} |")
        if len(sessions) > SHORT_ROWS:
            L.append(f"\n_还有 {len(sessions) - SHORT_ROWS} 条较早的会话没列出——该做巩固了（见 playbooks/memory.md）。_")
    else:
        L.append("_暂无。_")
    L += ["", "## 长期记忆卡", ""]
    if cards:
        L += ["| id | 类型 | 标题 | 可信度 | 重要度 | 复核 |", "|---|---|---|---|---:|---|"]
        for imp, _, f, fm in cards:
            fl = "⚠" if flags(fm, today) else ""
            L.append(f"| [{_cell(fm.get('id', f.stem))}](long/{f.name}) | {_cell(fm.get('type'))} | {_cell(fm.get('title'))} | "
                     f"{_cell(fm.get('confidence'))} | {imp} | {str(fm.get('review_after', ''))[:10]}{fl} |")
    else:
        L.append("_暂无。_")
    terms = []
    if GLOSSARY.exists():
        terms = [m.group(1).strip() for m in re.finditer(r"^\| ([^|]+?) \|", GLOSSARY.read_text(encoding="utf-8"), re.M)
                 if m.group(1).strip() not in ("术语", "---")]
    L += ["", "## 术语表", "",
          f"[glossary.md](glossary.md)：{len(terms)} 条" + ("——" + "、".join(terms[:12]) if terms else ""), "",
          "## 其他长期记忆的入口", "",
          "- 关于你：[profile/](../profile/)（about / preferences / now）· 待办 [todo.md](../todo.md)",
          "- 经历（只追加）：[journal/](../journal/) · 回顾：[reviews/](../reviews/)",
          "- 主题档案：[projects/](../projects/) · [areas/](../areas/) · [decisions/](../decisions/) · [people/](../people/) · [meetings/](../meetings/)",
          "- 做事方式：[CLAUDE.md](../CLAUDE.md) · [playbooks/](../playbooks/README.md) · [templates/](../templates/)", ""]
    text = "\n".join(L)
    old = INDEX.read_text(encoding="utf-8") if INDEX.exists() else ""
    # 只有内容变化时才写（忽略生成日期行），避免每次启动都产生改动
    strip = lambda t: re.sub(r"\*\*生成于\*\* \S+", "", t)
    if strip(old) != strip(text):
        MEMORY.mkdir(exist_ok=True)
        INDEX.write_text(text, encoding="utf-8")
        return True
    return False


def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="记忆检索与索引")
    ap.add_argument("keywords", nargs="*")
    ap.add_argument("--scope", choices=list(SCOPES), default="all")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--archive", action="store_true", help="也搜 memory/archive")
    ap.add_argument("--due", action="store_true")
    ap.add_argument("--reindex", action="store_true")
    ap.add_argument("--next-id", action="store_true")
    args = ap.parse_args()

    if args.reindex:
        print("memory/INDEX.md " + ("已更新" if reindex() else "无变化"))
    elif args.due:
        print("\n".join(due()) or "没有需要巩固或复核的记忆。")
    elif args.next_id:
        print(next_id())
    elif args.keywords:
        print("\n".join(search(args.keywords, args.scope, args.limit, args.archive)))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
