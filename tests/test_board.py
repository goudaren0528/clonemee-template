"""台账扫描 board.py 的单测：不在三栏、暂停没写复查日期、复查日期到了或过期。

计划的未结任务用 tasks 参数直接喂，不读仓库里的真实计划；今日计划和 todo 写在临时目录。
    python -B -m unittest tests/test_board.py
"""
from __future__ import annotations

import os as _os, sys as _sys  # noqa: E402
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _isolation  # noqa: E402,F401  外发总开关与凭据隔离

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import board  # noqa: E402

TODAY = date(2026, 10, 22)

DAILY = """---
type: plan
slug: daily-2026-10-22
status: active
summary: 测试用
---

# 今日建议 · 2026-10-22 周四

- [ ] T1 示例：派发出去的任务
- [ ] T2 example-a：等外部团队回复示例问题

## 推进位与排队

**本机推进位 1/6**

| 位 | 项目 | 事项 | 现在 | 下一步 |
|---|---|---|---|---|
| 1 | assistant | T1 派发出去的任务 | 在跑 | 等完成 |

**排队（要派 / 已确认）**

| 排队 | 项目 | 事项 | 派发已确认 | 为什么排这里 |
|---|---|---|---|---|
| 1 | assistant | 控制台 v1.2 | 否 | 等你定 |

**暂停与等外部（必须写明等谁、等什么、什么时候回来看）**

| 项目 | 事项 | 等谁 / 等什么 | 回来看 |
|---|---|---|---|
| example-a | T2 等外部团队回复示例问题 | 等外部团队回复 | 2026-10-23 |
| example-b | Vercel 部署 | 等你在控制台连仓库 | 等你说 |
| example-c | 示例清单 V1 | 等你在 #5 回一句 | 2026-10-20 |
"""

TODO = """# 待办

## 工作
- [ ] 更新示例文档 · 2026-10-25 · [example-a](projects/example-a/index.md)
- [x] 已经做完的事
- [-] 不做的事
"""

TASKS = [
    {"plan": "2026-10-22", "id": "T1", "title": "示例：派发出去的任务", "proj": "assistant", "due": "2026-10-22"},
    {"plan": "2026-10-22", "id": "T2", "title": "example-a：等外部团队回复示例问题", "proj": "example-a", "due": "2026-10-22"},
    {"plan": "2026-10-22", "id": "T3", "title": "assistant：没进三栏的任务", "proj": "assistant", "due": "2026-10-24"},
]


class BoardCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="assistant-board-")
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def write(self, name, text):
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return p

    def scan(self, daily=DAILY, todo=TODO, tasks=None, today=TODAY):
        return board.scan(today=today, daily=self.write("daily.md", daily),
                          todo=self.write("todo.md", todo), tasks=TASKS if tasks is None else tasks)

    # ---------- 三类问题 ----------

    def test_three_kinds_detected(self):
        """暂停缺日期、复查日期过期、未结任务不在三栏——三类都能检出，过期的排在前面。"""
        res = self.scan()
        kinds = [i["kind"] for i in res["issues"]]
        self.assertEqual(kinds, ["overdue", "no-date", "missing", "missing"])
        self.assertEqual(res["counts"], {"overdue": 1, "due": 0, "no-date": 1, "missing": 2})
        self.assertEqual(res["issues"][0]["review"], "2026-10-20")
        self.assertEqual(res["issues"][0]["problem"], "复查日期已过期")
        self.assertIn("已过期 2 天", res["issues"][0]["advice"])
        self.assertEqual(res["issues"][1]["project"], "example-b")
        self.assertEqual(res["issues"][1]["problem"], "暂停没写复查日期")
        # 不在三栏：日计划的 T3 和 todo 里的「更新示例文档」（T1、T2 在台账里，不报）
        missing = [i["item"] for i in res["issues"] if i["kind"] == "missing"]
        self.assertTrue(any("没进三栏" in m for m in missing))
        self.assertTrue(any("更新示例文档" in m for m in missing))
        self.assertFalse(any("派发出去的任务" in m for m in missing))
        self.assertFalse(any("等外部团队回复示例问题" in m for m in missing))

    def test_due_today(self):
        """复查日期是今天就报「今天到期」。"""
        daily = DAILY.replace("| 2026-10-23 |", "| 2026-10-22 |")
        res = self.scan(daily=daily, tasks=[], todo="")
        self.assertEqual([i["kind"] for i in res["issues"]], ["overdue", "due", "no-date"])
        self.assertEqual(res["issues"][1]["problem"], "复查日期今天到期")

    def test_future_review_date_not_reported(self):
        """复查日期还没到的不报。"""
        res = self.scan(tasks=[], todo="")
        self.assertEqual([i["kind"] for i in res["issues"]], ["overdue", "no-date"])

    def test_missing_review_column(self):
        """暂停表少写「回来看」一整列（行只有三格）也当缺复查日期。"""
        daily = """# 今日建议

## 推进位与排队

| 项目 | 事项 | 等谁 / 等什么 | 回来看 |
|---|---|---|---|
| example-a | 等外部团队回复示例问题 | 等外部团队回复 |
"""
        res = self.scan(daily=daily, todo="", tasks=[])
        self.assertEqual([i["kind"] for i in res["issues"]], ["no-date"])

    def test_all_in_columns_no_issue(self):
        """三栏齐全、暂停的都有未来日期、未结任务都对得上：不报，brief 输出空串。"""
        daily = """# 今日建议

## 推进位与排队

| 位 | 项目 | 事项 | 现在 | 下一步 |
|---|---|---|---|---|
| 1 | assistant | T1 派发出去的任务 | 在跑 | 等完成 |

| 排队 | 项目 | 事项 | 派发已确认 | 为什么排这里 |
|---|---|---|---|---|
| 1 | example-a | T2 等外部团队回复示例问题 | 否 | 快做完 |

| 项目 | 事项 | 等谁 / 等什么 | 回来看 |
|---|---|---|---|
| example-b | Vercel 部署 | 等你连仓库 | 10-23 |
"""
        tasks = TASKS[:2]
        todo = "- [x] 已经做完的事\n"  # 只有做完的：不算未结
        res = self.scan(daily=daily, todo=todo, tasks=tasks)
        self.assertEqual(res["issues"], [])
        self.assertEqual(board.brief(today=TODAY, daily=self.write("daily.md", daily),
                                     todo=self.write("todo.md", todo), tasks=tasks), "")
        self.assertIn("台账齐全", board.render(res["issues"]))

    def test_missing_section(self):
        """今日计划没有「推进位与排队」三栏：标记 has_board=False，所有未结项都算不在三栏。"""
        daily = "# 今日建议\n\n- [ ] T1 示例：派发出去的任务\n"
        res = self.scan(daily=daily, todo="- [ ] 一件小事\n", tasks=TASKS[:1])
        self.assertFalse(res["has_board"])
        self.assertEqual([i["kind"] for i in res["issues"]], ["missing", "missing"])
        line = board.brief(today=TODAY, daily=self.write("daily.md", daily),
                           todo=self.write("todo.md", "- [ ] 一件小事\n"), tasks=TASKS[:1])
        self.assertTrue(line.startswith("今日计划没有「推进位与排队」三栏"))

    def test_duplicate_reported_once(self):
        """日计划、周计划里同一件事（文字几乎一样）只报一次。"""
        tasks = [dict(TASKS[2]),
                 {"plan": "2026-W43", "id": "T3", "title": "assistant：没进三栏的任务", "proj": "assistant", "due": "2026-10-24"}]
        res = self.scan(daily="# 今日建议\n", todo="", tasks=tasks)
        self.assertEqual(len([i for i in res["issues"] if i["kind"] == "missing"]), 1)

    def test_todo_done_and_dropped_ignored(self):
        res = self.scan(tasks=[], daily="# 今日建议\n\n## 推进位与排队\n", todo="")
        self.assertEqual(res["issues"], [])

    # ---------- 表格、摘要、CLI ----------

    def test_render_table(self):
        res = self.scan()
        out = board.render(res["issues"])
        self.assertEqual(out.splitlines()[0], "| 项目 | 事项 | 问题 | 复查日期 | 建议 |")
        self.assertEqual(len(out.splitlines()), 1 + 1 + len(res["issues"]))

    def test_brief_counts(self):
        res = self.scan()
        line = board._brief(res)
        self.assertTrue(line.startswith("台账有 4 项要处理：过期 1、缺复查日期 1、不在三栏 2"))
        self.assertIn("先看 example-c", line)
        self.assertNotIn("|", line)

    def test_review_date_parsing(self):
        self.assertEqual(board.review_date("2026-10-23", TODAY), date(2026, 10, 23))
        self.assertEqual(board.review_date("10-23（到期）", TODAY), date(2026, 10, 23))
        self.assertEqual(board.review_date("10-25（截止）", TODAY), date(2026, 10, 25))
        self.assertEqual(board.review_date("明天", TODAY), date(2026, 10, 23))
        self.assertEqual(board.review_date("今天", TODAY), TODAY)
        self.assertEqual(board.review_date("01-05", TODAY), date(2027, 1, 5))  # 跨年
        self.assertIsNone(board.review_date("等你说", TODAY))
        self.assertIsNone(board.review_date("—", TODAY))
        self.assertIsNone(board.review_date("", TODAY))

    def test_cli_json(self):
        fake = {"date": "2026-10-22", "has_board": True,
                "counts": {"overdue": 0, "due": 0, "no-date": 0, "missing": 0}, "issues": []}
        buf = io.StringIO()
        with mock.patch.object(board, "scan", return_value=fake), mock.patch.object(sys, "argv", ["board.py", "--json"]):
            with redirect_stdout(buf):
                board.main()
        self.assertEqual(json.loads(buf.getvalue())["date"], "2026-10-22")

    def test_cli_brief_silent_when_clean(self):
        fake = {"date": "2026-10-22", "has_board": True,
                "counts": {"overdue": 0, "due": 0, "no-date": 0, "missing": 0}, "issues": []}
        buf = io.StringIO()
        with mock.patch.object(board, "scan", return_value=fake), mock.patch.object(sys, "argv", ["board.py", "--brief"]):
            with redirect_stdout(buf):
                board.main()
        self.assertEqual(buf.getvalue().strip(), "")

    def test_col_key_and_table_kind(self):
        """三张表按表头认（别的写法也能认出来）。"""
        self.assertEqual([board._col_key(h) for h in ["位", "设备", "项目", "事项", "现在", "下一步"]],
                         ["slot", "device", "project", "item", "now", "next"])
        self.assertEqual(board._col_key("等谁 / 等什么"), "waiting")
        self.assertEqual(board._col_key("复查日期"), "review")
        self.assertEqual(board._table_kind(["project", "item", "waiting", "review"]), "paused")
        self.assertEqual(board._table_kind(["queue", "project", "item"]), "queue")
        self.assertEqual(board._table_kind(["slot", "project", "item"]), "slot")
        self.assertIsNone(board._table_kind(["project", "item"]))


if __name__ == "__main__":
    unittest.main()
