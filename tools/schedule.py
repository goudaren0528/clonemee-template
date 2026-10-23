r"""
固定日程 —— areas/work/index.md 的「固定日程」表 → 今天 / 明天 / 提前几天要提醒的事（开场卡片、早间建议共用）。

    python tools/schedule.py                 # 今天、明天，以及今天该提前提的
    python tools/schedule.py --days 7        # 未来 7 天都有什么
    python tools/schedule.py --date 2027-03-18
    python tools/schedule.py --json          # 机读（session_start.py、morning-brief skill 用）

表在 `## 固定日程` 一节，四列：时间 | 事项 | 提醒要求 | 备注
  时间      `每周二 10:30`（每周循环）或 `2027-03-18 14:00`（一次性）；时间可以省，周写「一二三四五六日」
  事项      一句话
  提醒要求  默认提前 1 天 + 当天各提醒一次；写「提前 3 天」改提前天数（「提前 0 天」只当天）；写「不提醒」就不提前提醒
  备注      只给人看，工具不读

解析失败不抛异常：load() 返回 (entries, error)，卡片上只提示「固定日程解析失败：…」。
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, timedelta
from pathlib import Path

from common import ROOT, setup_stdio

DEFAULT_PATH = ROOT / "areas" / "work" / "index.md"
SECTION = "固定日程"
WEEK_CN = "一二三四五六日"
WEEKDAYS = {c: i for i, c in enumerate(WEEK_CN)} | {"天": 6}
CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
WEEKLY = re.compile(r"^每周([一二三四五六日天])(?:\s*(\d{1,2})[:：](\d{2}))?$")
ONCE = re.compile(r"^(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?:\s*(\d{1,2})[:：](\d{2}))?$")
ADVANCE = re.compile(r"提前\s*([0-9零一两二三四五六七八九十]+)\s*天")
QUIET = ("不提醒", "不用提醒", "无需提醒", "免提醒")


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _header_key(cell):
    n = re.sub(r"\s+", "", cell)
    if n in ("时间", "time", "when"):
        return "when"
    if n in ("事项", "内容", "item"):
        return "item"
    if n.startswith("提醒"):
        return "remind"
    if n in ("备注", "note", "说明"):
        return "note"
    return ""


def _cn_number(text):
    """「3」「三」「三天」里的数字 → int。"""
    s = (text or "").strip()
    if s.isdigit():
        return int(s)
    if "十" in s:
        a, _, b = s.partition("十")
        return (CN_DIGITS.get(a, 1) if a else 1) * 10 + (CN_DIGITS.get(b, 0) if b else 0)
    return CN_DIGITS.get(s)


def _time(h, m):
    """时间两格 → ('HH:MM', '')；没写时返回 ('', '')。"""
    if h is None:
        return "", ""
    h, m = int(h), int(m)
    if h > 23 or m > 59:
        return "", f"时间 {h}:{m:02d} 不存在"
    return f"{h:02d}:{m:02d}", ""


def parse_row(row, source=""):
    """表里一行 → 日程；看不懂时返回 (None, 原因)。"""
    when = (row.get("when") or "").strip()
    item = (row.get("item") or "").strip()
    if not item:
        return None, "事项是空的"
    m = WEEKLY.match(when)
    if m:
        kind, weekday, once = "weekly", WEEKDAYS[m.group(1)], None
        time, err = _time(m.group(2), m.group(3))
    else:
        m = ONCE.match(when)
        if not m:
            return None, "时间看不懂（写「每周二 10:30」或「2027-03-18 14:00」）"
        try:
            once = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None, "日期不存在"
        kind, weekday = "once", None
        time, err = _time(m.group(4), m.group(5))
    if err:
        return None, err
    remind = (row.get("remind") or "").strip()
    quiet = any(q in remind for q in QUIET)
    am = ADVANCE.search(remind)
    advance = _cn_number(am.group(1)) if am else 1
    if advance is None:  # 数字写法认不出来：按默认
        advance = 1
    return {"when": when, "item": item, "remind": remind, "note": (row.get("note") or "").strip(),
            "kind": kind, "weekday": weekday, "date": once.isoformat() if once else None,
            "time": time, "advance": advance, "silent": quiet, "source": source}, ""


def parse(text, source=""):
    """整份 Markdown → (日程列表, 错误说明)。没有「固定日程」一节时是 ([], '')。"""
    m = re.search(rf"^##\s+{re.escape(SECTION)}\s*$", text, re.M)
    if not m:
        return [], ""
    rest = text[m.end():]
    nxt = re.search(r"^##\s", rest, re.M)
    block = rest[:nxt.start()] if nxt else rest
    lines = [l.strip() for l in block.splitlines() if l.strip().startswith("|")]
    if not lines:
        return [], "「固定日程」一节里没有表"
    keys = [_header_key(c) for c in _cells(lines[0])]
    if "when" not in keys or "item" not in keys:
        return [], "表头要有时、事项两列（时间 / 事项 / 提醒要求 / 备注）"
    entries, errors, n = [], [], 0
    for line in lines[1:]:
        cs = _cells(line)
        if all(c and set(c) <= set("-: ") for c in cs):  # 分隔行 |---|---|
            continue
        row = dict(zip(keys, cs))
        if not any(row.get(k) for k in ("when", "item")):
            continue
        n += 1
        entry, err = parse_row(row, source)
        if err:
            errors.append(f"第 {n} 行「{row.get('when') or '空'} · {row.get('item') or '空'}」：{err}")
        else:
            entries.append(entry)
    return entries, "；".join(errors)


def _rel(path):
    try:
        return Path(path).relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def load(path=None):
    """读表 → (日程列表, 错误说明)；文件读不到也只返回错误，不抛异常。"""
    p = Path(path) if path else DEFAULT_PATH
    rel = _rel(p)
    if not p.exists():
        return [], f"找不到 {rel}"
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:  # noqa: BLE001 —— 卡片只要一句提示
        return [], f"读不到 {rel}：{e}"
    return parse(text, source=rel)


def occurs_on(entry, day):
    """这一天有没有这件事。"""
    if entry["kind"] == "weekly":
        return day.weekday() == entry["weekday"]
    return entry["date"] == day.isoformat()


def occurrences(entries, start, days):
    """从 start 起 days 天内发生的事：[{date, weekday, entry}]，按日期排。"""
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        for e in entries:
            if occurs_on(e, d):
                out.append({"date": d.isoformat(), "weekday": WEEK_CN[d.weekday()], "entry": e})
    return out


def reminders(entries, day):
    """今天该提前提的事：发生在 day + 提前天数 的那天（默认提前 1 天）；「不提醒」的跳过。"""
    out = []
    for e in entries:
        if e["silent"]:
            continue
        target = day + timedelta(days=e["advance"])
        if occurs_on(e, target):
            out.append({"date": target.isoformat(), "weekday": WEEK_CN[target.weekday()],
                        "advance": e["advance"], "entry": e})
    return out


def card(day=None, path=None, days=7):
    """开场卡片 / 早间建议要的一份数据（解析失败装在 error 里）。"""
    day = day or date.today()
    entries, error = load(path)
    return {"date": day.isoformat(), "weekday": WEEK_CN[day.weekday()],
            "today": [e for e in entries if occurs_on(e, day)],
            "tomorrow": [e for e in entries if occurs_on(e, day + timedelta(days=1))],
            "reminders": reminders(entries, day),
            "upcoming": occurrences(entries, day, days),
            "path": _rel(path or DEFAULT_PATH), "error": error}


def label(entry, day=None):
    """一行里的一件事：「10:30 示例读书会」；给了日期就带周几：「周二 10:30 示例读书会」。"""
    parts = []
    if day is not None:
        parts.append("周" + WEEK_CN[day.weekday()])
    if entry["time"]:
        parts.append(entry["time"])
    parts.append(entry["item"])
    return " ".join(parts)


def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="固定日程：areas/work/index.md 的「固定日程」表")
    ap.add_argument("--date", help="按哪天算（YYYY-MM-DD，默认今天）")
    ap.add_argument("--days", type=int, default=0, help="列出未来几天的全部日程")
    ap.add_argument("--json", action="store_true", help="机读输出")
    ap.add_argument("--path", help="表所在文件（默认 areas/work/index.md）")
    a = ap.parse_args()
    day = date.fromisoformat(a.date) if a.date else date.today()
    data = card(day, a.path, days=a.days or 7)
    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    if data["error"]:
        print(f"⚠ 固定日程解析失败：{data['error']}")
    print(f"固定日程（{data['path'] or (a.path or 'areas/work/index.md')}）")
    for key, name, d in (("today", "今天", day), ("tomorrow", "明天", day + timedelta(days=1))):
        items = data[key]
        print(f"{name} {d.isoformat()} 周{WEEK_CN[d.weekday()]}：" +
              ("无" if not items else "；".join(label(e, None if key == "today" else d) for e in items)))
    rems = data["reminders"]
    print("今天该提前提的：" + ("无" if not rems else "；".join(
        f"{r['advance']} 天后（{r['date']} 周{r['weekday']}）{label(r['entry'])}" for r in rems)))
    if a.days:
        print(f"\n未来 {a.days} 天：")
        if not data["upcoming"]:
            print("  无")
        for o in data["upcoming"]:
            print(f"  {o['date']} 周{o['weekday']}  {label(o['entry'])}")


if __name__ == "__main__":
    main()
