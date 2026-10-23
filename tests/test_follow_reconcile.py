"""僵尸运行记录 reconcile 的单测：会话都没了 + 超时、交付看板全合入、验收已写。

运行记录全部写在临时目录，Orca 用 live 参数直接喂（不碰真实终端）；不读仓库里真实的 runs/。
    python -B -m unittest tests/test_follow_reconcile.py
"""
from __future__ import annotations

import os as _os, sys as _sys  # noqa: E402
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _isolation  # noqa: E402,F401  外发总开关与凭据隔离

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import follow  # noqa: E402

NOW = datetime(2027, 3, 8, 16, 0)
DEAD = "term_11111111-1111-1111-1111-111111111111"      # 不在 Orca 里的会话
ALIVE = "term_22222222-2222-2222-2222-222222222222"     # 还开着的会话


def make_run(name, body, **fm):
    """写一份运行记录：frontmatter 默认 dispatched、今天创建，body 直接接在后面。"""
    head = {"type": "run", "slug": name, "status": "dispatched", "created": "2027-03-08 09:00",
            "updated": "2027-03-08", "task": f"plan#T-{name}", "project": "assistant", "repo": "<your-github>/cloneme",
            "agent": "claude", "mode": "orca-existing", "summary": f"测试 {name}"}
    head.update({k: v for k, v in fm.items() if v is not None})
    lines = ["---"] + [f"{k}: {v}" for k, v in head.items()] + ["---", "", f"# {name}", "", body.strip(), ""]
    return "\n".join(lines)


# a）会话都不在 + 最后一条监督超过 2 小时；验收还空着
RUN_A = make_run("case-a", """
## 派发

- 2027-03-08 09:00 — 派发

## 监督

- 2027-03-08 09:30 — 检查：执行会话在跑
""", handle=f"'{DEAD}'")

# b）交付看板：工作包全部合入、集成测试已过；执行会话还开着（只看 b 也能命中）
RUN_B = make_run("case-b", """
## 阶段
- 当前：集成测试

## 工作包
| 包 | 状态 | 依赖 |
|---|---|---|
| P0 | 已合入 | — |
| P1 | 已合入 | P0 |

## 监督

- 2027-03-08 15:50 — 集成测试通过（abc1234）：全套 24/24，进合回 develop
""", kind="delivery", follower=f"'{ALIVE}'")

# c）「验收」一节已有内容；会话还开着、监督也近（只看 c 也能命中）
RUN_C = make_run("case-c", """
## 监督

- 2027-03-08 15:55 — 验收报告看过了

## 验收

- 2027-03-08 15:00 — 验收通过：示例工作包全部合入，集成测试通过
""", handle=f"'{ALIVE}'")

# 已让位：会话都没了、超时，但写了 waiting_on / 没写 next_check → 建议转等你
RUN_W = make_run("case-wait", """
## 监督

- 2027-03-08 10:00 — 让位：等你 回复示例问题，关掉执行会话和跟进 Agent
""", handle=f"'{DEAD}'", waiting_on="等你 回复示例问题")

# 还活着：会话开着、刚监督过、验收空着 → 不算僵尸
RUN_ACTIVE = make_run("case-active", """
## 监督

- 2027-03-08 15:58 — 检查：在干活
""", status="running", handle=f"'{ALIVE}'")

# 已经结束：不扫
RUN_DONE = make_run("case-done", """
## 验收

- 2027-03-07 20:00 — 完成
""", status="done", handle=f"'{DEAD}'")


class ReconcileCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="assistant-zombie-")
        self.addCleanup(self._tmp.cleanup)
        self.runs = Path(self._tmp.name)
        self.files = {}
        for name, text in (("case-a", RUN_A), ("case-b", RUN_B), ("case-c", RUN_C), ("case-wait", RUN_W),
                           ("case-active", RUN_ACTIVE), ("case-done", RUN_DONE)):
            f = self.runs / f"2027-03-08-{name}.md"
            f.write_text(text, encoding="utf-8")
            self.files[name] = f

    def scan(self, live=(ALIVE,)):
        return follow.zombie_scan(self.runs, now=NOW, live=set(live))

    def by_slug(self, scan):
        return {r["slug"]: r for r in scan["rows"]}

    def test_three_conditions_suggest_close(self):
        """a 会话都没了 + 超时、b 工作包全合入 + 集成测试过、c 验收已写：分别进「建议关闭」。"""
        scan = self.scan()
        rows = self.by_slug(scan)
        self.assertEqual(set(rows), {"case-a", "case-b", "case-c", "case-wait"})
        self.assertEqual(rows["case-a"]["action"], "close")
        self.assertEqual(rows["case-a"]["hits"], ["a"])
        self.assertEqual(rows["case-b"]["action"], "close")
        self.assertEqual(rows["case-b"]["hits"], ["b"])  # 会话还开着，只靠交付看板
        self.assertEqual(rows["case-c"]["action"], "close")
        self.assertEqual(rows["case-c"]["hits"], ["c"])  # 会话还开着，只靠验收
        self.assertEqual(scan["scanned"], 5)  # done 的不扫
        self.assertTrue(scan["live_known"])

    def test_waiting_on_goes_to_wait(self):
        """写了 waiting_on 的让位记录进「建议转等你」，建议复查日期 = 今天 + 3 天。"""
        row = self.by_slug(self.scan())["case-wait"]
        self.assertEqual(row["action"], "wait")
        self.assertEqual(row["waiting_on"], "等你 回复示例问题")
        self.assertEqual(row["suggest_next"], "2027-03-11")
        self.assertEqual(row["hits"], ["a"])

    def test_nothing_matched_reports_nothing(self):
        """一条都不满足时不报：只留 case-active。"""
        only_active = Path(self._tmp.name) / "only"
        only_active.mkdir()
        (only_active / "2027-03-08-active.md").write_text(RUN_ACTIVE, encoding="utf-8")
        scan = follow.zombie_scan(only_active, now=NOW, live={ALIVE})
        self.assertEqual(scan["rows"], [])
        self.assertEqual(follow.zombie_count(only_active, now=NOW, live={ALIVE}), 0)
        self.assertEqual(follow.zombie_line(only_active, now=NOW, live={ALIVE}), "")
        self.assertIn("没有僵尸运行记录", follow.render_zombies(scan))

    def test_other_device_skips_condition_a(self):
        """别的设备上派发的记录：这边看不到它的会话，不走条件 a（b、c 照样判）。"""
        other = Path(self._tmp.name) / "other"
        other.mkdir()
        text = make_run("case-other-device", RUN_A.split("---", 2)[2], handle=f"'{DEAD}'", device="ba9876543210")
        (other / "2027-03-08-other.md").write_text(text, encoding="utf-8")
        scan = follow.zombie_scan(other, now=NOW, live={ALIVE})
        self.assertEqual(scan["rows"], [])
        self.assertEqual(scan["elsewhere"], 1)
        self.assertIn("1 条在别的设备上派发", follow.render_zombies(scan))

    def test_orca_down_skips_condition_a(self):
        """Orca 连不上（live=None）：a 判定跳过，b、c 照样报。"""
        scan = follow.zombie_scan(self.runs, now=NOW, live=None)
        self.assertFalse(scan["live_known"])
        self.assertEqual(set(self.by_slug(scan)), {"case-b", "case-c"})
        self.assertIn("Orca 连不上", follow.render_zombies(scan))

    def test_dry_run_changes_nothing(self):
        """不加 --apply：一个文件都不改。"""
        before = {f.name: f.read_text(encoding="utf-8") for f in self.files.values()}
        buf = io.StringIO()
        with redirect_stdout(buf):
            print(follow.render_zombies(self.scan()))
        self.assertIn("建议关闭", buf.getvalue())
        self.assertIn("建议转等你", buf.getvalue())
        self.assertIn("没改任何文件", buf.getvalue())
        for f in self.files.values():
            self.assertEqual(f.read_text(encoding="utf-8"), before[f.name])

    def test_apply_closes_and_writes_next_check(self):
        """--apply：建议关闭的改 done（写 closed_at、监督留一行），建议转你等的只补 next_check。"""
        scan = self.scan()
        changes = follow.apply_zombies(scan, now=NOW)
        self.assertEqual(len(changes), 4)
        a = self.files["case-a"].read_text(encoding="utf-8")
        self.assertIn("status: done", a)
        self.assertIn("closed_at:", a)
        self.assertIn("reconcile（--apply）", a)
        b = self.files["case-b"].read_text(encoding="utf-8")
        self.assertIn("status: done", b)
        c = self.files["case-c"].read_text(encoding="utf-8")
        self.assertIn("status: done", c)
        w = self.files["case-wait"].read_text(encoding="utf-8")
        self.assertIn("status: dispatched", w)  # 让位不等于结束
        self.assertIn("waiting_on: 等你 回复示例问题", w)
        self.assertIn("next_check: 2027-03-11", w)
        # 再扫一遍：关掉的没了；让位那条 status 还是 dispatched，但 next_check 没到 → 不算要提醒的僵尸
        again = follow.zombie_scan(self.runs, now=NOW + follow.STALE * 2, live={ALIVE})
        self.assertEqual(set(self.by_slug(again)), {"case-wait"})
        self.assertFalse(self.by_slug(again)["case-wait"]["due"])
        self.assertEqual(follow.zombie_count(self.runs, now=NOW + follow.STALE * 2, live={ALIVE}), 0)
        # 复查日期到了（10-26 再看），又该提醒了
        later = follow.zombie_scan(self.runs, now=datetime(2027, 3, 12, 9, 0), live={ALIVE})
        self.assertTrue(self.by_slug(later)["case-wait"]["due"])
        self.assertEqual(follow.zombie_count(self.runs, now=datetime(2027, 3, 12, 9, 0), live={ALIVE}), 1)

    def test_brief_line(self):
        """sweep / 开场卡片那一行。"""
        line = follow.zombie_line(self.runs, now=NOW, live={ALIVE})
        self.assertEqual(line, "有 4 条僵尸运行记录（疑似已结束 / 已让位），跑 python tools/follow.py --reconcile 看清单")
        out = follow.render_zombies(self.scan())
        self.assertIn("扫了 5 条", out)
        self.assertIn("## 建议关闭（status → done）· 3 条", out)
        self.assertIn("## 建议转等你（status 保持 dispatched，只补 next_check）· 1 条", out)


class InjectedClockCase(unittest.TestCase):
    def test_stamp_waiting_uses_injected_now(self):
        """stamp_waiting 的 updated 用注入的 now，不用真实时钟。"""
        text = make_run("x", "## 监督"+chr(10), waiting_on="等对方回复")
        out = follow.stamp_waiting(text, "2027-03-11", now=datetime(2020, 1, 2, 3, 4))
        self.assertIn("updated: 2020-01-02", out)
        self.assertIn("next_check: 2027-03-11", out)


if __name__ == "__main__":
    unittest.main()
