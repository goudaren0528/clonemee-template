"""P4 测试：会话启动钩子里的指挥台通道一段（session_start.channel_lines）。只测这个函数，不跑整个钩子（整个钩子会重建索引）。
    python -B -m unittest tests/test_session_start_channel.py
"""
from __future__ import annotations

import os as _os, sys as _sys  # noqa: E402
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _isolation  # noqa: E402,F401  外发总开关与凭据隔离

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import session_start  # noqa: E402


class TestChannelLines(unittest.TestCase):
    def test_running_gives_link(self):
        out, greet = session_start.channel_lines(
            lambda: {"ok": True, "url": "http://127.0.0.1:17318/", "reused": False}, lambda: [])
        self.assertIn("http://127.0.0.1:17318/（新起）", out[0])
        self.assertIn("打开指挥台", out[0])
        self.assertIn("console_channel.py stop", out[0])
        self.assertEqual(greet, ["指挥台：http://127.0.0.1:17318/"])
        out, _ = session_start.channel_lines(
            lambda: {"ok": True, "url": "http://127.0.0.1:17319/", "reused": True}, lambda: [])
        self.assertIn("（复用）", out[0])

    def test_failure_is_reported_not_raised(self):
        out, greet = session_start.channel_lines(
            lambda: {"ok": False, "url": None, "reason": "端口 17318～17327 都被占用，面板改为复制指令"}, lambda: [])
        self.assertIn("都被占用", out[0])
        self.assertTrue(greet and greet[0].startswith("指挥台没起来"))

        def boom():
            raise RuntimeError("x")

        out, greet = session_start.channel_lines(boom, boom)
        self.assertIn("通道检查失败：RuntimeError", out[0])
        self.assertEqual(len(greet), 1)

    def test_worktree_copy_stays_quiet(self):
        out, greet = session_start.channel_lines(
            lambda: {"ok": False, "url": None, "reason": "派发出去的工作区副本，不拉起指挥台通道"}, lambda: [])
        self.assertIn("副本", out[0])
        self.assertEqual(greet, [], "派发出去的会话不在卡片上提指挥台")

    def test_waiting_replies_counted(self):
        out, greet = session_start.channel_lines(
            lambda: {"ok": True, "url": "http://127.0.0.1:17318/", "reused": True}, lambda: [{"编号": "N1021-07"}, {"编号": "N1021-08"}])
        self.assertIn("指挥台回复 2 条待转", out[1])
        self.assertIn("notify.py replies --waiting", out[1])
        self.assertIn("指挥台回复 2 条待转", greet)


if __name__ == "__main__":
    unittest.main()
