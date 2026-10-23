"""固定日程 tools/schedule.py 的单测：每周 X 当天、提前一天、无关日期，
外加一次性日程、提前 N 天 / 只当天 / 不提醒、解析失败和 CLI。

表写在临时目录，不读仓库里真实的 areas/work/index.md。
    python -B -m unittest tests/test_schedule.py
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

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import schedule  # noqa: E402

TODAY = date(2027, 3, 9)  # 周四
MONDAY = date(2027, 3, 6)  # 周六，距周二三天（变量名沿用，只表示「更早的一天」）


def doc(*rows):
    """一张固定日程表（四列：时间 / 事项 / 提醒要求 / 备注）。"""
    lines = ["---", "type: area", "---", "", "# 工作（领域）", "", "## 固定日程", "",
             "| 时间 | 事项 | 提醒要求 | 备注 |", "|---|---|---|---|"]
    lines += [f"| {r} |" for r in rows]
    lines += ["", "## 当前状态", "- 测试", ""]
    return "\n".join(lines)


WEEKLY = "每周二 10:30 | 示例读书会 | 提前 1 天提醒 | 示例备注"


class ScheduleCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="assistant-schedule-")
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def write(self, text, name="work.md"):
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return p

    def card(self, day=TODAY, *rows):
        return schedule.card(day, self.write(doc(*rows)))

    # ---------- 三种基本情况 ----------

    def test_weekly_on_the_day(self):
        """每周 X 当天：今天有这件事，明天没有。"""
        res = self.card(TODAY, WEEKLY)
        self.assertEqual(res["error"], "")
        self.assertEqual([e["item"] for e in res["today"]], ["示例读书会"])
        self.assertEqual(res["today"][0]["time"], "10:30")
        self.assertEqual(res["tomorrow"], [])

    def test_weekly_one_day_early(self):
        """提前一天（默认 1 天）：周一提醒，今天没有这件事、明天有。"""
        res = self.card(date(2027, 3, 8), WEEKLY)
        self.assertEqual(res["today"], [])
        self.assertEqual([e["item"] for e in res["tomorrow"]], ["示例读书会"])
        self.assertEqual(len(res["reminders"]), 1)
        self.assertEqual(res["reminders"][0]["date"], "2027-03-09")
        self.assertEqual(res["reminders"][0]["advance"], 1)

    def test_weekly_unrelated_day(self):
        """无关日期：周四、周六都没有这件事，也不提醒。"""
        for day in (date(2027, 3, 11), date(2027, 3, 13)):
            res = self.card(day, WEEKLY)
            self.assertEqual((res["today"], res["tomorrow"], res["reminders"]), ([], [], []), day)

    # ---------- 提前天数 / 不提醒 ----------

    def test_advance_days_configured(self):
        """提醒要求写了「提前 3 天」：三天前提醒，前一天不提醒（数字或中文都认）。"""
        for text in ("提前 3 天提醒", "提前三天提醒"):
            res = self.card(MONDAY, f"每周二 10:30 | 示例读书会 | {text} |")
            self.assertEqual(len(res["reminders"]), 1, text)
            self.assertEqual(res["reminders"][0]["date"], "2027-03-09")
            self.assertEqual(res["reminders"][0]["advance"], 3)
            self.assertEqual(self.card(date(2027, 3, 8), f"每周二 10:30 | 读书会 | {text} |")["reminders"], [])

    def test_same_day_only(self):
        """「提前 0 天」只在当天提醒。"""
        rows = ("每周二 10:30 | 示例读书会 | 提前 0 天提醒 |",)
        self.assertEqual(len(self.card(TODAY, *rows)["reminders"]), 1)
        self.assertEqual(self.card(date(2027, 3, 8), *rows)["reminders"], [])

    def test_silent(self):
        """「不提醒」：当天照常显示，但不提前提醒。"""
        res = self.card(date(2027, 3, 8), "每周二 10:30 | 示例读书会 | 不提醒 |")
        self.assertEqual(res["reminders"], [])
        self.assertTrue(res["tomorrow"][0]["silent"])
        self.assertEqual([e["item"] for e in self.card(TODAY, "每周二 10:30 | 示例读书会 | 不提醒 |")["today"]],
                         ["示例读书会"])

    # ---------- 一次性日程 ----------

    def test_one_off(self):
        """`2027-03-18 14:00`：当天出现，前一天提醒，别的日子没有。"""
        row = "2027-03-18 14:00 | 季度回顾 | | 会议室 A"
        self.assertEqual([e["item"] for e in self.card(date(2027, 3, 18), row)["today"]], ["季度回顾"])
        self.assertEqual([e["item"] for e in self.card(date(2027, 3, 17), row)["tomorrow"]], ["季度回顾"])
        self.assertEqual([r["date"] for r in self.card(date(2027, 3, 17), row)["reminders"]], ["2027-03-18"])
        self.assertEqual(self.card(date(2027, 3, 19), row)["today"], [])
        self.assertEqual(self.card(date(2027, 3, 16), row)["reminders"], [])

    def test_time_optional_and_dash_style(self):
        """时间可以省，日期也认 2027/03/18。"""
        entries, err = schedule.parse(doc("2027/03/18 | 体检 | |", "每周五 | 示例站会 | |"))
        self.assertEqual(err, "")
        self.assertEqual([(e["date"], e["time"]) for e in entries], [("2027-03-18", ""), (None, "")])
        self.assertEqual([e["weekday"] for e in entries], [None, 4])

    # ---------- 解析失败 ----------

    def test_parse_failure_keeps_going(self):
        """看不懂的一行报错，别的行照常；不抛异常。"""
        entries, err = schedule.parse(doc("每四 15:00 | 写错的行 | |", WEEKLY))
        self.assertEqual(len(entries), 1)
        self.assertIn("时间看不懂", err)
        self.assertIn("第 1 行", err)
        self.assertIn("写错的行", err)

    def test_bad_clock_and_bad_date(self):
        for when, hint in (("每周二 25:00", "不存在"), ("2026-02-30", "日期不存在")):
            entries, err = schedule.parse(doc(f"{when} | 事项 | |"))
            self.assertEqual(entries, [], when)
            self.assertIn(hint, err, when)

    def test_missing_table_section_or_file(self):
        self.assertEqual(schedule.parse("# 工作\n\n没有固定日程\n"), ([], ""))
        self.assertEqual(schedule.parse(doc())[0], [])  # 只有表头
        entries, err = schedule.load(self.dir / "nope.md")
        self.assertEqual(entries, [])
        self.assertIn("找不到", err)
        res = schedule.card(TODAY, self.dir / "nope.md")
        self.assertIn("找不到", res["error"])
        self.assertEqual((res["today"], res["tomorrow"]), ([], []))

    def test_error_field_in_card(self):
        res = self.card(TODAY, "每周八 15:00 | 周八不存在 | |")
        self.assertIn("时间看不懂", res["error"])
        self.assertEqual(res["today"], [])

    # ---------- 展示 ----------

    def test_label(self):
        entry = {"item": "示例读书会", "time": "10:30"}
        self.assertEqual(schedule.label(entry), "10:30 示例读书会")
        self.assertEqual(schedule.label(entry, date(2027, 3, 18)), "周四 10:30 示例读书会")
        self.assertEqual(schedule.label({"item": "站会", "time": ""}, date(2027, 3, 15)), "周一 站会")

    # ---------- CLI ----------

    def test_cli_json_and_text(self):
        path = self.write(doc(WEEKLY, "2027-03-18 10:00 | 季度回顾 | |"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            sys.argv = ["schedule.py", "--json", "--date", "2027-03-08", "--path", str(path)]
            schedule.main()
        data = json.loads(buf.getvalue())
        self.assertEqual(data["date"], "2027-03-08")
        self.assertEqual(data["weekday"], "一")
        self.assertEqual([e["item"] for e in data["tomorrow"]], ["示例读书会"])
        self.assertEqual([r["date"] for r in data["reminders"]], ["2027-03-09"])

        buf = io.StringIO()
        with redirect_stdout(buf):
            sys.argv = ["schedule.py", "--date", "2027-03-09", "--days", "10", "--path", str(path)]
            schedule.main()
        text = buf.getvalue()
        self.assertIn("今天 2027-03-09 周二：10:30 示例读书会", text)
        self.assertIn("明天 2027-03-10 周三：无", text)
        self.assertIn("2027-03-18 周四", text)  # 一次性日程
        self.assertNotIn("解析失败", text)


if __name__ == "__main__":
    unittest.main()
