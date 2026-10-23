"""清理工具 cleanup.py 的三处防误删：宽限期、被忽略的文件、盘点前 git fetch。

临时目录里建真 git 仓库（裸仓库当 origin + 主仓库 + worktree），Orca 全部打桩，不碰真实终端和工作区。
    python -B -m unittest tests/test_cleanup.py
"""
from __future__ import annotations

import os as _os, sys as _sys  # noqa: E402
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _isolation  # noqa: E402,F401  外发总开关与凭据隔离

import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import cleanup  # noqa: E402
import follow  # noqa: E402

GIT = shutil.which("git")
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def run(cwd, *args):
    return subprocess.run([GIT, *args], cwd=cwd, capture_output=True, text=True, check=True, env=ENV).stdout.strip()


def stamp(minutes_ago):
    return f"{datetime.now() - timedelta(minutes=minutes_ago):%Y-%m-%d %H:%M}"


@unittest.skipUnless(GIT, "没有 git")
class CleanupCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="assistant-cleanup-")
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.bare, self.repo, self.wt, self.data = base / "origin.git", base / "repo", base / "wt", base / "data"
        run(base, "init", "-q", "--bare", "-b", "main", str(self.bare))
        run(base, "init", "-q", "-b", "main", str(self.repo))
        (self.repo / ".gitignore").write_text("*.log\nnode_modules/\n", encoding="utf-8")
        run(self.repo, "add", ".")
        run(self.repo, "commit", "-q", "-m", "init")
        run(self.repo, "remote", "add", "origin", str(self.bare))
        run(self.repo, "push", "-q", "-u", "origin", "main")
        run(self.repo, "worktree", "add", "-q", "-b", "feat", str(self.wt), "main")
        (self.wt / "a.txt").write_text("a", encoding="utf-8")
        run(self.wt, "add", ".")
        run(self.wt, "commit", "-q", "-m", "feat")
        run(self.wt, "push", "-q", "origin", "feat")  # 已推送、没合并：只因为运行记录结束才能清
        (self.data / "runs").mkdir(parents=True)
        self.terms = []
        for p in (mock.patch.object(cleanup, "orca_lists", lambda: (self.terms, self.wts(), [], {"r1": str(self.repo)}, [])),
                  mock.patch.dict(os.environ, {"ORCA_TERMINAL_HANDLE": ""})):
            p.start()
            self.addCleanup(p.stop)

    def wts(self):
        return [{"path": str(self.wt), "branch": "refs/heads/feat", "repoId": "r1", "git": True, "baseRef": "main", "displayName": "wt"}]

    def write_run(self, status="done", closed_at=None, handle="", name="2026-10-22-demo-T1"):
        f = self.data / "runs" / f"{name}.md"
        f.write_text("\n".join(["---", "type: run", f"slug: {name}", f"status: {status}"]
                               + ([f"closed_at: '{closed_at}'"] if closed_at else [])
                               + ["task: demo#T1", "project: demo", f"workdir: '{self.wt}'"]
                               + ([f"handle: '{handle}'"] if handle else []) + ["summary: 测试", "---", "", "# 测试", ""]),
                     encoding="utf-8")
        return f

    def items(self, **kw):
        return cleanup.inventory(self.data, **kw)

    def wt_item(self, items):
        return next(i for i in items if i["kind"] == "工作区")


class TestGrace(CleanupCase):
    def test_within_grace_not_cleanable(self):
        self.write_run(closed_at=stamp(5))
        items, _ = self.items()
        i = self.wt_item(items)
        self.assertTrue(cleanup.waiting(i))
        self.assertFalse(cleanup.risky(i), "宽限期不算「要问你」")
        self.assertTrue(9 <= i["wait"] <= 10, i["wait"])
        self.assertEqual(cleanup.counts(items), (0, 0, 0, 1))
        self.assertIn("1 项在宽限期内", cleanup.brief_line(items))
        out = cleanup.render(items, [])
        self.assertIn("宽限期内：1 项", out)
        self.assertIn("宽限期内，还有", out)

    def test_after_grace_cleanable(self):
        self.write_run(closed_at=stamp(cleanup.GRACE_MIN + 1))
        items, _ = self.items()
        i = self.wt_item(items)
        self.assertEqual(i["wait"], 0)
        self.assertEqual(cleanup.counts(items), (0, 1, 0, 0))
        self.assertIn("派发已结束", i["reason"])

    def test_old_record_falls_back_to_mtime(self):
        f = self.write_run()  # 没有 closed_at
        old = time.time() - 3600
        os.utime(f, (old, old))
        self.assertEqual(self.wt_item(self.items()[0])["wait"], 0)
        os.utime(f, None)  # 刚改过：按修改时间还在宽限期内
        self.assertGreater(self.wt_item(self.items()[0])["wait"], 0)

    def test_merged_branch_has_no_grace(self):
        run(self.repo, "merge", "-q", "--ff-only", "feat")
        self.write_run(closed_at=stamp(1))
        i = self.wt_item(self.items()[0])
        self.assertEqual(i["wait"], 0)
        self.assertTrue(i["merged"])

    def test_open_run_not_listed(self):
        self.write_run(status="dispatched")
        self.assertEqual([i for i in self.items()[0] if i["kind"] == "工作区"], [])

    def test_for_run_closing_shows_full_grace(self):
        f = self.write_run(status="dispatched")  # 跟进 Agent 收尾前：按「现在结束」算
        items, _ = self.items(for_run=f)
        i = self.wt_item(items)
        self.assertEqual(i["wait"], cleanup.GRACE_MIN)
        self.assertIn("派发收尾", i["reason"])
        self.assertIn(f"{cleanup.GRACE_MIN} 分钟后可清", cleanup.render(items, [], "python tools/cleanup.py --for x"))

    def test_session_grace(self):
        h = "term_aaaaaaaa-0000-0000-0000-000000000000"
        self.terms[:] = [{"handle": h, "worktreePath": str(self.wt), "title": "执行", "preview": "working…",
                          "lastOutputAt": (time.time() - 12 * 60) * 1000}]  # 没在输出、也没退到 shell
        self.write_run(closed_at=stamp(2), handle=h)
        s = next(i for i in self.items()[0] if i["kind"] == "会话")
        self.assertGreater(s["wait"], 0)
        self.assertTrue(cleanup.waiting(s))

    def test_execute_holds_waiting(self):
        self.write_run(closed_at=stamp(1))
        items, _ = self.items(fetch_remote=True)
        removed = []
        with mock.patch.object(cleanup, "remove_worktree", lambda i, log: removed.append(i["key"])), \
                mock.patch.object(cleanup, "orca", lambda *a: {"ok": True}), redirect_stdout(io.StringIO()) as out:
            cleanup.execute(items, SimpleNamespace(only=None, skip=None, also=None))
        self.assertEqual(removed, [])
        self.assertIn("宽限期内没清 1 项", out.getvalue())
        # 你点名（--also）的照清
        with mock.patch.object(cleanup, "remove_worktree", lambda i, log: removed.append(i["key"])), \
                mock.patch.object(cleanup, "orca", lambda *a: {"ok": True}), redirect_stdout(io.StringIO()):
            cleanup.execute(items, SimpleNamespace(only=None, skip=None, also=cleanup.short(self.wt)))
        self.assertEqual(removed, [cleanup.short(self.wt)])


class TestStampClosed(unittest.TestCase):
    TEXT = "---\ntype: run\nstatus: dispatched\nupdated: 2026-10-21\n---\n\n## 验收\n\nclosed_at: 正文里的不动\n"

    def test_append_close_writes_closed_at(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "r.md"
            f.write_text(self.TEXT, encoding="utf-8")
            follow.append(f, "验收", "- 结束", status="done")
            text = f.read_text(encoding="utf-8")
            fm = follow.fields(text)
            self.assertEqual(fm["status"], "done")
            self.assertRegex(fm["closed_at"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
            self.assertIn("status: done\nclosed_at: '", text)
            self.assertIn("closed_at: 正文里的不动", text)

    def test_reclose_updates_and_log_only_does_not(self):
        text = self.TEXT.replace("status: dispatched", "status: failed\nclosed_at: '2026-10-01 08:00'")
        again = follow.stamp_closed(text)
        self.assertEqual(again.count("closed_at: '"), 1)
        self.assertNotIn("2026-10-01 08:00", again)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "r.md"
            f.write_text(self.TEXT, encoding="utf-8")
            follow.append(f, "监督", "- 记一笔")
            self.assertNotIn("closed_at", follow.fields(f.read_text(encoding="utf-8")))


class TestIgnored(CleanupCase):
    def test_ignored_with_content_is_risky(self):
        (self.wt / "evidence").mkdir()
        (self.wt / "evidence" / "run.log").write_text("集成测试日志", encoding="utf-8")
        (self.wt / "top.log").write_text("x", encoding="utf-8")
        self.write_run(closed_at=stamp(60))
        i = self.wt_item(self.items()[0])
        self.assertTrue(cleanup.risky(i))
        flag = next(f for f in i["flags"] if f.startswith("有被忽略的文件"))
        self.assertIn("2 个", flag)
        self.assertIn("evidence/", flag)
        self.assertEqual(cleanup.counts(self.items()[0])[2], 1)

    def test_only_regenerable_not_flagged(self):
        nm = self.wt / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("x", encoding="utf-8")
        (self.wt / "empty.log").write_text("", encoding="utf-8")  # 空文件不算
        handoff = self.wt / ".assistant" / "handoff" / "plan-T1"
        handoff.mkdir(parents=True)
        (handoff / "prd.md").write_text("副本", encoding="utf-8")
        with open(Path(run(self.wt, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "info" / "exclude", "a", encoding="utf-8") as f:
            f.write(".assistant/\n")
        self.write_run(closed_at=stamp(60))
        i = self.wt_item(self.items()[0])
        self.assertEqual(cleanup.ignored(str(self.wt)), [])
        self.assertFalse(cleanup.risky(i), i["flags"])


class TestFetch(CleanupCase):
    def test_fetch_refreshes_remote_refs(self):
        (self.wt / "b.txt").write_text("b", encoding="utf-8")
        run(self.wt, "add", ".")
        run(self.wt, "commit", "-q", "-m", "b")
        run(self.wt, "push", "-q", "origin", "HEAD:refs/heads/feat")
        run(self.wt, "update-ref", "refs/remotes/origin/feat", "HEAD~1")  # 本地缓存的远端引用落后：看起来像没推送
        self.write_run(closed_at=stamp(60))
        stale = self.wt_item(self.items()[0])
        self.assertIn("有未推送提交 1 个", stale["flags"])
        fresh = self.wt_item(self.items(fetch_remote=True)[0])
        self.assertFalse(cleanup.risky(fresh), fresh["flags"])
        self.assertEqual(fresh["fetch"], "ok")

    def test_fetch_failure_noted_not_blocking(self):
        run(self.repo, "remote", "set-url", "origin", str(Path(self._tmp.name) / "nope.git"))
        self.write_run(closed_at=stamp(60))
        items, notes = self.items(fetch_remote=True)
        self.assertTrue(any("repo 仓库 fetch 失败，远端引用可能过期" in n for n in notes), notes)
        i = self.wt_item(items)
        self.assertEqual(i["fetch"], "failed")
        self.assertEqual(cleanup.counts(items)[1], 1, "fetch 失败不挡")
        self.assertIn("fetch 失败", cleanup.render(items, notes))

    def test_execute_fetches_when_inventory_did_not(self):
        (self.wt / "b.txt").write_text("b", encoding="utf-8")
        run(self.wt, "add", ".")
        run(self.wt, "commit", "-q", "-m", "b")
        self.write_run(closed_at=stamp(60))
        items, _ = self.items()  # 没 fetch：本地看是「有未推送提交」，先手动把它当成干净的
        i = self.wt_item(items)
        i["flags"] = [f for f in i["flags"] if not f.startswith("有未推送提交")]
        self.assertEqual(i["fetch"], "")
        removed = []
        with mock.patch.object(cleanup, "remove_worktree", lambda i, log: removed.append(i["key"])), \
                mock.patch.object(cleanup, "orca", lambda *a: {"ok": True}), redirect_stdout(io.StringIO()) as out:
            cleanup.execute(items, SimpleNamespace(only=None, skip=None, also=None))
        self.assertEqual(removed, [], "删之前补 fetch、重新核对，冒出未推送提交就不清")
        self.assertIn("fetch 后重新核对", out.getvalue())
        self.assertEqual(i["fetch"], "ok")


if __name__ == "__main__":
    unittest.main()
