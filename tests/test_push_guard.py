"""pre-push 体积闸测试：临时 git 仓库造场景，不碰真实仓库。

覆盖：小提交通过 / 单个大文件拦住 / 累计超限拦住 / 各类密钥拦住 / 文档和夹具不误报 /
ASSISTANT_PUSH_OK 放行 / 按远端已有提交只算增量 / 真钩子 push --dry-run 端到端 / 装钩子与 --check。
    python -B -m unittest tests/test_push_guard.py
"""
from __future__ import annotations

import os as _os, sys as _sys  # noqa: E402
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _isolation  # noqa: E402,F401  外发总开关与凭据隔离

import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import install_hooks  # noqa: E402
import push_guard  # noqa: E402

FAKE = b"assistant-push-guard: fake-secrets\n"


def git(args, cwd, check=True):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise AssertionError(f"git {args} 失败：{p.stderr}")
    return p


class TempRepo:
    """临时 git 仓库：commit(files) 每次提交一批 {相对路径: 字节}，返回 sha。"""

    def __init__(self, root):
        self.path = Path(root) / "repo"
        self.path.mkdir()
        git(["init", "-q", "-b", "main"], self.path)
        git(["config", "user.name", "t"], self.path)
        git(["config", "user.email", "t@t"], self.path)
        git(["config", "core.autocrlf", "false"], self.path)

    def commit(self, files):
        for name, data in files.items():
            p = self.path / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        git(["add", "-A"], self.path)
        git(["commit", "-q", "-m", "c"], self.path)
        return git(["rev-parse", "HEAD"], self.path).stdout.strip()

    def bare(self):
        b = Path(tempfile.mkdtemp(dir=self.path.parent, prefix="bare-"))
        git(["init", "-q", "--bare", str(b)], self.path)
        return b


class GuardSizeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = TempRepo(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, local, remote=""):
        return push_guard.check([(local, remote)], cwd=str(self.repo.path))

    def test_small_commit_passes(self):
        sha = self.repo.commit({"a.txt": b"hello\n", "docs/b.md": b"# doc\n" * 100})
        ok, lines = self.check(sha)
        self.assertTrue(ok, lines)
        self.assertIn("通过", lines[0])

    def test_single_big_file_blocked(self):
        sha = self.repo.commit({"a.txt": b"x", "docs/delivery/big/shots/1.png": b"\0" * (1 << 20) + b"y"})
        ok, lines = self.check(sha)
        self.assertFalse(ok)
        text = "\n".join(lines)
        self.assertIn("超过 1.0 MB", text)
        self.assertIn("docs/delivery/big/shots/1.png", text)

    def test_cumulative_over_limit_blocked(self):
        # 每个 900KB（不超单个上限），12 个共约 10.8MB；内容各不同，否则 git 只存一份
        files = {f"docs/delivery/e/data/f{i}.bin": bytes([i]) + b"\0" * (900 << 10) for i in range(12)}
        ok, lines = self.check(self.repo.commit(files))
        self.assertFalse(ok)
        text = "\n".join(lines)
        self.assertIn("累计", text)
        self.assertNotIn("超过 1.0 MB", text)

    def test_deleted_big_file_in_history_still_blocked(self):
        self.repo.commit({"big.bin": b"\0" * (2 << 20)})
        sha = self.repo.commit({"note.md": b"removed\n"})  # 这一步把 big.bin 从树里删掉
        ok, lines = self.check(sha)
        self.assertFalse(ok)
        self.assertIn("big.bin", "\n".join(lines))

    def test_only_new_objects_after_remote(self):
        first = self.repo.commit({"big.bin": b"\0" * (2 << 20)})
        second = self.repo.commit({"note.md": b"small\n"})
        self.assertFalse(self.check(second)[0])
        self.assertTrue(self.check(second, first)[0])

    def test_empty_ref_line_ignored(self):
        sha = self.repo.commit({"a.txt": b"hi\n"})
        self.assertTrue(push_guard.check([("0" * 40, "0" * 40), (sha, "")], cwd=str(self.repo.path))[0])


class GuardSecretTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = TempRepo(self.tmp.name)
        self._n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def scan(self, name, data):
        self._n += 1
        return push_guard.check([(self.repo.commit({name: data}), "")], cwd=str(self.repo.path))

    def test_secrets_blocked(self):
        cases = {
            "sk.txt": b"key = sk-" + b"a1B2c3D4e5F6g7H8i9J0" + b"\n",
            "gh.txt": b"token: ghp_" + b"A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8" + b"\n",
            "aws.py": b"AKIAABCDEFGHIJKLMNOP\n",
            "id_rsa": b"-----BEGIN RSA PRIVATE KEY-----\n" + b"MIIEow" * 12 + b"\n-----END RSA PRIVATE KEY-----\n",
        }
        for name, data in cases.items():
            with self.subTest(name=name):
                ok, lines = self.scan(name, data)
                self.assertFalse(ok, lines)
                self.assertIn("疑似密钥", "\n".join(lines))

    def test_short_or_kebab_not_flagged(self):
        for name, data in {
            "a.md": b"sk-short\nghp_tooshort\nAKIA123\n",
            "b.md": b"task-sk-" + b"a" * 30 + b"\n",  # 前面紧跟 kebab 词
            "c.py": b'"""-----BEGIN RSA PRIVATE KEY-----"""\n' + "# 只提到私钥文件头的文档/夹具\n".encode(),
            "d.bin": b"\0" * 10 + b"sk-" + b"a1B2c3D4e5F6g7H8i9J0",
        }.items():
            with self.subTest(name=name):
                ok, lines = self.scan(name, data)
                self.assertTrue(ok, lines)

    def test_fake_marker_skips_scan(self):
        ok, lines = self.scan("fixture.py", FAKE + b"AKIAABCDEFGHIJKLMNOP\n")
        self.assertTrue(ok, lines)

    def test_find_secrets_returns_name_and_masked(self):
        hits = push_guard.find_secrets(b"AKIAABCDEFGHIJKLMNOP")
        self.assertEqual(hits[0][0], "AWS 访问密钥 AKIA")
        self.assertNotIn("ABCDEFGHIJKLMNOP", hits[0][1])


class GuardMainTest(unittest.TestCase):
    """main()：pre-push 模式（stdin）、放行变量、手动 --rev。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = TempRepo(self.tmp.name)
        self.sha = self.repo.commit({"big.bin": b"\0" * (2 << 20)})

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, stdin, extra=(), env=None):
        err = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(stdin)), \
             mock.patch.object(sys, "stderr", err), \
             mock.patch("os.environ", {k: v for k, v in os.environ.items() if k != push_guard.OK_ENV}):
            if env:
                os.environ.update(env)
            return push_guard.main([*extra]), err.getvalue()

    def test_stdin_ref_blocked(self):
        line = f"refs/heads/main {self.sha} refs/heads/main {'0' * 40}\n"
        with mock.patch.object(push_guard, "_git", side_effect=lambda a, cwd=None, data=None: self._real(a, data)):
            rc, out = self.run_main(line)
        self.assertEqual(rc, 1)
        self.assertIn("big.bin", out)

    def _real(self, args, data=None):
        p = subprocess.run(["git", *args], cwd=self.repo.path, input=data, capture_output=True)
        return p.returncode, p.stdout

    def test_env_bypass(self):
        rc, out = self.run_main("", env={push_guard.OK_ENV: "1"})
        self.assertEqual(rc, 0)
        self.assertIn("警告", out)

    def test_bad_rev(self):
        rc, out = self.run_main("", extra=["--rev", "nosuchref"])
        self.assertEqual(rc, 2)


class HookEndToEndTest(unittest.TestCase):
    """真钩子：install_hooks 装到临时仓库，git push --dry-run 到临时裸仓库。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = TempRepo(self.tmp.name)
        self.bare = self.repo.bare()

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, **kw):
        out = []
        rc = install_hooks.install(str(self.repo.path), guards=[REPO / "tools" / "push_guard.py"],
                                   out=out.append, **kw)
        return rc, "\n".join(out)

    def push(self):
        p = git(["push", "--dry-run", str(self.bare), "HEAD:refs/heads/t"], self.repo.path, check=False)
        return p.returncode, p.stdout + p.stderr

    def test_small_push_passes(self):
        self.repo.commit({"a.md": b"hi\n"})
        rc, out = self.install()
        self.assertEqual(rc, 0, out)
        self.assertIn("自检：通过", out)
        self.assertTrue((self.repo.path / ".git" / "hooks" / "pre-push").is_file())
        rc, out = self.push()
        self.assertEqual(rc, 0, out)
        self.assertIn("[体积闸] 通过", out)

    def test_big_push_blocked_and_bypass(self):
        self.repo.commit({"shots/big.png": b"\0" * (2 << 20)})
        rc, out = self.install()
        self.assertEqual(rc, 0, out)
        self.assertIn("被拦下", out)
        rc, out = self.push()
        self.assertNotEqual(rc, 0)
        self.assertIn("shots/big.png", out)
        env = dict(os.environ, ASSISTANT_PUSH_OK="1")
        p = subprocess.run(["git", "push", "--dry-run", str(self.bare), "HEAD:refs/heads/t"],
                           cwd=self.repo.path, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("警告", p.stderr)

    def test_check_command(self):
        ok, msg = install_hooks.check(self.repo.path)
        self.assertFalse(ok)
        self.assertIn("没装", msg)
        self.install()
        ok, msg = install_hooks.check(self.repo.path)
        self.assertTrue(ok)
        self.assertIn("已装", msg)

    def test_foreign_hook_not_overwritten(self):
        hook = self.repo.path / ".git" / "hooks" / "pre-push"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
        rc, out = self.install(run_selftest=False)
        self.assertEqual(rc, 1)
        self.assertIn("没覆盖", out)
        self.assertEqual(hook.read_text(encoding="utf-8"), "#!/bin/sh\necho mine\n")
        rc, out = self.install(force=True, run_selftest=False)
        self.assertEqual(rc, 0, out)
        self.assertIn(install_hooks.MARK, hook.read_text(encoding="utf-8"))
        self.assertTrue(hook.with_name("pre-push.assistant-bak").is_file())

    def test_hookspath_respected(self):
        git(["config", "core.hooksPath", "githooks"], self.repo.path)
        rc, out = self.install(run_selftest=False)
        self.assertEqual(rc, 0, out)
        self.assertTrue((self.repo.path / "githooks" / "pre-push").is_file())
        self.assertTrue(install_hooks.check(self.repo.path)[0])
        self.repo.commit({"a.md": b"hi\n"})
        rc, out = self.push()  # --not --remotes 会算上全部对象，但都是小的
        self.assertEqual(rc, 0, out)
        self.assertIn("[体积闸] 通过", out)


if __name__ == "__main__":
    unittest.main()
