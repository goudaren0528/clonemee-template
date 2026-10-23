"""飞书通知 tools/feishu_notify.py 的单测：卡片结构、干跑不发、缺 webhook 报错清晰、
发送失败退出码非 0、输出里不出现 webhook。

用本地 http.server 收 POST，不碰真的飞书群。
    python -B -m unittest tests/test_feishu_notify.py
"""
from __future__ import annotations

import os as _os, sys as _sys  # noqa: E402
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _isolation  # noqa: E402,F401  外发总开关与凭据隔离

import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import feishu_notify as fn  # noqa: E402

# 假的 webhook（本地服务），不是真的机器人地址
TOKEN = "fake-token-0d9a"


class Recorder(BaseHTTPRequestHandler):
    """收 POST、记下来，按类属性回一个飞书风格的响应。"""

    posts: list[dict] = []
    reply: dict = {"code": 0, "msg": "success"}
    status: int = 200

    def do_POST(self):  # noqa: N802（http.server 的约定）
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        payload = json.loads(body.decode("utf-8") or "{}")
        type(self).posts.append({"path": self.path, "body": payload, "headers": dict(self.headers)})
        data = json.dumps(type(self).reply).encode("utf-8")
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # 测试里不要访问日志
        pass


class FeishuNotifyCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        Recorder.posts = []
        Recorder.reply = {"code": 0, "msg": "success"}
        Recorder.status = 200
        # 这个类用本机 http.server 验证真实 POST，所以关掉总开关（只在本类；服务只监听 127.0.0.1）
        _p = mock.patch.dict(os.environ, {"ASSISTANT_NO_SEND": "0"})
        _p.start()
        self.addCleanup(_p.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.missing = Path(self.tmp.name) / "missing.md"
        self.webhook = f"http://127.0.0.1:{self.port}/open-apis/bot/v2/hook/{TOKEN}"

    # ---------- 跑 CLI ----------

    def run_main(self, *args, env=None):
        """调 main()，返回 (退出码, stdout, stderr)；默认把默认凭证文件指向不存在的路径。"""
        out, err = StringIO(), StringIO()
        environment = dict(env or {})
        with mock.patch.dict(os.environ, environment, clear=False):
            if "FEISHU_WEBHOOK" not in environment:
                os.environ.pop("FEISHU_WEBHOOK", None)
            with mock.patch.object(fn, "DEFAULT_WEBHOOK_FILE", self.missing):
                with redirect_stdout(out), redirect_stderr(err):
                    code = fn.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def base_args(self, kind="merge", number="7", title="示例：修复导出按钮样式"):
        return ["--kind", kind, "--repo", "<your-github>/example-repo", "--number", number, "--title", title]

    # ---------- 卡片结构 ----------

    def test_merge_card_structure(self):
        payload = fn.build_payload(
            kind="merge",
            repo="<your-github>/example-repo",
            title="示例：修复导出按钮样式",
            number="7",
            detail="示例说明",
            url="https://github.com/<your-github>/example-repo/pull/7",
        )
        self.assertEqual(payload["msg_type"], "interactive")
        card = payload["card"]
        self.assertEqual(card["schema"], "2.0")
        self.assertEqual(card["header"]["title"]["content"], "[example-repo] PR #7 已合并")
        self.assertEqual(card["header"]["template"], "green")
        elements = card["body"]["elements"]
        self.assertEqual(len(elements), 2)
        markdown = elements[0]
        self.assertEqual(markdown["tag"], "markdown")
        lines = markdown["content"].splitlines()
        self.assertTrue(3 <= len(lines) <= 5, lines)
        self.assertIn("**仓库**：<your-github>/example-repo", lines[0])
        self.assertIn("**标题**：示例：修复导出按钮样式", lines[1])
        self.assertIn("**编号**：#7", lines[2])
        self.assertIn("**详情**：示例说明", lines[3])
        self.assertTrue(lines[4].startswith("**时间**："))
        button = elements[1]
        self.assertEqual(button["tag"], "button")
        self.assertEqual(button["text"]["content"], "查看 PR")
        self.assertEqual(button["behaviors"][0], {"type": "open_url", "default_url": "https://github.com/<your-github>/example-repo/pull/7"})

    def test_templates_by_kind(self):
        issue = fn.build_payload(kind="issue", repo="<your-github>/example-repo", title="示例：列表页分页错位", number="12")
        pr = fn.build_payload(kind="pr", repo="<your-github>/example-repo", title="新需求", number="31")
        merge = fn.build_payload(kind="merge", repo="<your-github>/example-repo", title="新需求", number="31")
        self.assertEqual(issue["card"]["header"]["title"]["content"], "[example-repo] Issue #12 已提出")
        self.assertEqual(issue["card"]["header"]["template"], "blue")
        self.assertEqual(pr["card"]["header"]["title"]["content"], "[example-repo] PR #31 待评审")
        self.assertEqual(pr["card"]["header"]["template"], "turquoise")
        self.assertEqual(merge["card"]["header"]["template"], "green")
        closed = fn.build_payload(kind="issue", repo="<your-github>/example-repo", title="示例：列表页分页错位", number="12", action="closed")
        self.assertEqual(closed["card"]["header"]["title"]["content"], "[example-repo] Issue #12 已关闭")
        self.assertEqual(closed["card"]["header"]["template"], "red")

    def test_default_url_and_button_label(self):
        issue = fn.build_payload(kind="issue", repo="<your-github>/example-repo", title="报错", number="12")
        button = issue["card"]["body"]["elements"][-1]
        self.assertEqual(button["behaviors"][0]["default_url"], "https://github.com/<your-github>/example-repo/issues/12")
        self.assertEqual(button["text"]["content"], "查看 Issue")
        merge = fn.build_payload(kind="merge", repo="<your-github>/example-repo", title="报错", number="7")
        self.assertEqual(merge["card"]["body"]["elements"][-1]["behaviors"][0]["default_url"],
                         "https://github.com/<your-github>/example-repo/pull/7")

    def test_detail_is_one_line_and_truncated(self):
        payload = fn.build_payload(
            kind="issue", repo="o/r", title="标题", number="1",
            detail="第一行\n第二行\t还有很长" + "x" * 500,
        )
        content = payload["card"]["body"]["elements"][0]["content"]
        detail_line = [line for line in content.splitlines() if line.startswith("**详情**")][0]
        self.assertIn("第一行 第二行 还有很长", detail_line)
        self.assertNotIn("\n", detail_line)
        self.assertLessEqual(len(detail_line), len("**详情**：") + fn.MAX_DETAIL)

    def test_without_number_keeps_three_lines(self):
        payload = fn.build_payload(kind="merge", repo="<your-github>/example-repo", title="标题")
        lines = payload["card"]["body"]["elements"][0]["content"].splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(payload["card"]["header"]["title"]["content"], "[example-repo] PR 已合并")

    # ---------- 干跑 / 发送 ----------

    def test_dry_run_prints_card_and_sends_nothing(self):
        code, out, err = self.run_main(*self.base_args(), "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(Recorder.posts, [])
        payload = json.loads(out)
        self.assertEqual(payload["card"]["header"]["title"]["content"], "[example-repo] PR #7 已合并")
        self.assertEqual(err, "")

    def test_dry_run_works_without_webhook(self):
        code, out, _ = self.run_main("--kind", "issue", "--repo", "o/r", "--number", "1", "--title", "t", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("card", json.loads(out))

    def test_send_posts_card(self):
        code, out, err = self.run_main(*self.base_args(), env={"FEISHU_WEBHOOK": self.webhook})
        self.assertEqual(code, 0, err)
        self.assertEqual(len(Recorder.posts), 1)
        post = Recorder.posts[0]
        self.assertEqual(post["path"], f"/open-apis/bot/v2/hook/{TOKEN}")
        self.assertIn("application/json", post["headers"].get("Content-Type", ""))
        self.assertEqual(post["body"]["msg_type"], "interactive")
        self.assertEqual(post["body"]["card"]["header"]["title"]["content"], "[example-repo] PR #7 已合并")
        self.assertIn("已发送：[example-repo] PR #7 已合并", out)

    def test_webhook_never_printed(self):
        code, out, err = self.run_main(*self.base_args(), env={"FEISHU_WEBHOOK": self.webhook})
        self.assertEqual(code, 0)
        for text in (out, err):
            self.assertNotIn(self.webhook, text)
            self.assertNotIn(TOKEN, text)

    def test_webhook_file(self):
        path = Path(self.tmp.name) / "feishu-webhook.md"
        path.write_text(f"- **URL**：`http://127.0.0.1:{self.port}/hook/from-file`\n", encoding="utf-8")
        code, _, err = self.run_main(*self.base_args(), "--webhook-file", str(path))
        self.assertEqual(code, 0, err)
        self.assertEqual(Recorder.posts[0]["path"], "/hook/from-file")

    def test_default_webhook_file(self):
        path = Path(self.tmp.name) / "private.md"
        path.write_text(f"webhook: http://127.0.0.1:{self.port}/hook/default\n", encoding="utf-8")
        with mock.patch.object(fn, "DEFAULT_WEBHOOK_FILE", path):
            out, err = StringIO(), StringIO()
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("FEISHU_WEBHOOK", None)
                with redirect_stdout(out), redirect_stderr(err):
                    code = fn.main(self.base_args())
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(Recorder.posts[0]["path"], "/hook/default")

    # ---------- 失败路径 ----------

    def test_missing_webhook_error_is_clear(self):
        code, out, err = self.run_main(*self.base_args())
        self.assertEqual(code, 1)
        self.assertEqual(Recorder.posts, [])
        self.assertIn("飞书通知未发送", err)
        self.assertIn("FEISHU_WEBHOOK", err)
        self.assertIn("--webhook-file", err)
        self.assertIn("private/feishu-webhook.md", err)
        self.assertEqual(out, "")

    def test_webhook_file_without_url(self):
        path = Path(self.tmp.name) / "empty.md"
        path.write_text("# 空文件\n没有地址\n", encoding="utf-8")
        code, _, err = self.run_main(*self.base_args(), "--webhook-file", str(path))
        self.assertEqual(code, 1)
        self.assertIn("没有找到 webhook 地址", err)

    def test_missing_webhook_file(self):
        code, _, err = self.run_main(*self.base_args(), "--webhook-file", str(Path(self.tmp.name) / "nope.md"))
        self.assertEqual(code, 1)
        self.assertIn("读不到凭证文件", err)

    def test_http_error_exit_code(self):
        Recorder.status = 500
        Recorder.reply = {"code": 19002, "msg": "invalid token"}
        code, out, err = self.run_main(*self.base_args(), env={"FEISHU_WEBHOOK": self.webhook})
        self.assertEqual(code, 1)
        self.assertIn("飞书通知发送失败", err)
        self.assertIn("HTTP 500", err)
        self.assertNotIn(self.webhook, err + out)
        self.assertNotIn(TOKEN, err + out)

    def test_feishu_error_code_exit_code(self):
        Recorder.reply = {"code": 19002, "msg": "invalid webhook token"}
        code, _, err = self.run_main(*self.base_args(), env={"FEISHU_WEBHOOK": self.webhook})
        self.assertEqual(code, 1)
        self.assertIn("code=19002", err)
        self.assertNotIn(TOKEN, err)

    def test_connection_error_exit_code(self):
        # 端口 1 上不会有服务：连接失败也要有原因、退出码非 0、不泄露 webhook
        dead = "http://127.0.0.1:1/open-apis/bot/v2/hook/dead-token"
        code, out, err = self.run_main(*self.base_args(), env={"FEISHU_WEBHOOK": dead})
        self.assertEqual(code, 1)
        self.assertIn("飞书通知发送失败", err)
        self.assertNotIn("dead-token", err + out)

    def test_bad_repo(self):
        code, out, err = self.run_main("--kind", "pr", "--repo", "example-repo", "--title", "t")
        self.assertEqual(code, 2)
        self.assertIn("owner/name", err)
        self.assertEqual(Recorder.posts, [])

    def test_timeout_is_ten_seconds(self):
        self.assertEqual(fn.DEFAULT_TIMEOUT, 10.0)

    def test_extract_webhook(self):
        self.assertEqual(fn.extract_webhook("`https://open.feishu.cn/open-apis/bot/v2/hook/abc-123`"),
                         "https://open.feishu.cn/open-apis/bot/v2/hook/abc-123")
        self.assertEqual(fn.extract_webhook("没有地址"), "")


if __name__ == "__main__":
    unittest.main()
