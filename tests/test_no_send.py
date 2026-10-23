"""外发总开关 ASSISTANT_NO_SEND 的守门测试。
    python -B -m unittest tests/test_no_send.py

守门写法（新增外发工具时照抄）：开关打开 → patch 掉所有能出网 / 出终端的底层函数，一旦被调用就 fail，
再走一遍真实入口，断言「返回成功 + 打印已拦截外发 + 底层一次没被碰」。
这里所有用例都不依赖当前时间。
"""
from __future__ import annotations

import os as _os, sys as _sys  # noqa: E402
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _isolation  # noqa: E402,F401  外发总开关与凭据隔离

import os
import socket
import subprocess
import sys
import unittest
import urllib.request
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import common  # noqa: E402
import dispatch  # noqa: E402
import feishu_notify as fn  # noqa: E402
import gh_tasks  # noqa: E402
import notify  # noqa: E402


def _boom(*a, **k):
    raise AssertionError(f"开关打开时不该有外发：{a[:2]}")


class NoSendGuard(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"ASSISTANT_NO_SEND": "1"})
        p.start()
        self.addCleanup(p.stop)
        patches = [
            mock.patch.object(socket, "create_connection", _boom),
            mock.patch.object(socket.socket, "connect", _boom),
            mock.patch.object(urllib.request, "urlopen", _boom),
            mock.patch.object(subprocess, "run", _boom),
            mock.patch.object(subprocess, "Popen", _boom),
        ]
        for q in patches:
            q.start()
            self.addCleanup(q.stop)

    def test_default_is_on_in_tests(self):
        self.assertTrue(common.no_send())
        for off in ("", "0", "false", "No", "off"):
            with mock.patch.dict(os.environ, {"ASSISTANT_NO_SEND": off}):
                self.assertFalse(common.no_send(), off)

    def test_webhook_is_dead_and_private_file_redirected(self):
        self.assertIn("127.0.0.1:9", os.environ["FEISHU_WEBHOOK"])
        self.assertFalse(str(fn.DEFAULT_WEBHOOK_FILE.resolve()).startswith(str((REPO / "private").resolve())))

    def test_feishu_send_intercepted_no_network(self):
        err = StringIO()
        with redirect_stderr(err):
            self.assertTrue(fn.send("http://127.0.0.1:9/x", {"msg_type": "text"}))
        self.assertIn("已拦截外发", err.getvalue())

    def test_orca_outward_commands_intercepted_reads_not(self):
        for mod in (notify, dispatch):
            with redirect_stderr(StringIO()) as err:
                r = mod.orca("terminal", "send", "--terminal", "t", "--text", "hi", "--enter")
                r2 = mod.orca("agent", "launch", "--agent", "claude")
            self.assertTrue(r["ok"] and r["blocked"], mod.__name__)
            self.assertTrue(r2["ok"] and r2["blocked"], mod.__name__)
            self.assertIn("已拦截外发", err.getvalue())
        self.assertIsNone(common.orca_blocked(("terminal", "read", "--terminal", "t")))
        self.assertIsNone(common.orca_blocked(("worktree", "list")))

    def test_toast_and_retry_intercepted(self):
        with redirect_stderr(StringIO()):
            notify.toast({"类型": "完成", "事项": "x", "一句话": "y", "编号": "N1", "详情": ""})
            notify.spawn_retry("N1", "")

    def test_gh_writes_blocked_reads_allowed(self):
        self.assertTrue(gh_tasks.is_gh_write(("issue", "create", "-R", "a/b")))
        self.assertTrue(gh_tasks.is_gh_write(("api", "repos/a/b/milestones", "-X", "POST")))
        self.assertFalse(gh_tasks.is_gh_write(("api", "repos/a/b/milestones?state=all")))
        self.assertFalse(gh_tasks.is_gh_write(("issue", "view", "1")))
        with redirect_stderr(StringIO()), self.assertRaises(RuntimeError):
            gh_tasks.gh("issue", "create", "-R", "a/b", "--title", "t")

    def test_every_test_module_imports_isolation(self):
        missing = [p.name for p in sorted((REPO / "tests").glob("test_*.py"))
                   if "import _isolation" not in p.read_text(encoding="utf-8")]
        self.assertEqual(missing, [], "新测试文件顶部要 import _isolation（见 tests/_isolation.py）")


if __name__ == "__main__":
    unittest.main()
