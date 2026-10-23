"""P2 生产入口集成冒烟：参数、环境投影、原子输出、覆盖防护、失败保旧文件。

依赖边界：本分支只有 P0（与 P2），P1 ``tools/console_data.py`` 尚未合入，因此

- 真实适配器路径只验证「缺依赖时明确失败、不伪造数据、不写文件」；
- 全链路写盘用**显式注入的替身 loader**（``snapshot_fixture``）验证，替身只证明
  渲染/输出管线，不冒充完整适配器集成；完整集成留待 P1/P3/P4 合包后另测；
- 写盘一律写到临时 ``tool_root``，不碰仓库里的 ``inbox/console/``。

运行：``python -B -m unittest tests.test_console_integration -v``
"""
from __future__ import annotations

import os as _os, sys as _sys  # noqa: E402
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _isolation  # noqa: E402,F401  外发总开关与凭据隔离

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import console  # noqa: E402
import console_contract as cc  # noqa: E402
import console_render as cr  # noqa: E402
import console_support  # noqa: E402

try:  # 直接跑 tests/ 目录
    from test_console_render import make_standin_assets  # noqa: E402
except ModuleNotFoundError:  # 以 tests 包方式运行
    from tests.test_console_render import make_standin_assets  # noqa: E402


def _digest(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _make_dir_link(link: Path, target: Path) -> str | None:
    """建目录链接；成功返回 None，失败返回可放进 skipTest 的原因。"""
    try:
        os.symlink(target, link, target_is_directory=True)
        return None
    except (OSError, NotImplementedError) as exc:
        if os.name == "nt":
            proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                  capture_output=True, text=True, errors="replace")
            if proc.returncode == 0:
                return None
            detail = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")
            return f"symlink 失败（{exc}），junction 失败（{detail[:100]}）"
        return str(exc)


class ConsoleTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="assistant-p2-console-")
        self.tmp = Path(self._tmp.name)
        self.asset_root = make_standin_assets(self.tmp / "assets")
        self.tool_root = self.tmp / "tool"
        self.tool_root.mkdir()
        self.output_path = self.tool_root / "inbox" / "console" / "index.html"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_console(self, argv, **kwargs) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = console.run(
                argv,
                asset_root=kwargs.pop("asset_root", self.asset_root),
                output_path=kwargs.pop("output_path", self.output_path),
                tool_root=kwargs.pop("tool_root", self.tool_root),
                now=kwargs.pop("now", console_support.DEFAULT_NOW),
                **kwargs,
            )
        return code, out.getvalue(), err.getvalue()

    def main_capture(self, argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = console.main(argv)
        return code, out.getvalue(), err.getvalue()


class ArgumentTests(ConsoleTestBase):
    def test_help(self) -> None:
        code, out, _err = self.main_capture(["--help"])
        self.assertEqual(code, console.EXIT_OK)
        self.assertIn("--root", out)
        self.assertIn("inbox/console/index.html", out)
        self.assertFalse(self.output_path.exists(), "帮助不应写文件")

    def test_usage_errors(self) -> None:
        missing = self.tmp / "no-such-dir"
        a_file = self.tmp / "a-file.txt"
        a_file.write_text("x", encoding="utf-8")
        cases = {
            "相对路径": ["--root", "relative/path"],
            "不存在": ["--root", str(missing)],
            "是文件": ["--root", str(a_file)],
            "UNC": ["--root", "\\\\server\\share"],
            "设备路径": ["--root", "\\\\.\\C:\\Windows"],
            "未知参数": ["--force"],
            "root 缺值": ["--root"],
            "空 root": ["--root", ""],
            "ADS": ["--root", str(self.tmp / "data") + ":stream"],
        }
        for name, argv in cases.items():
            with self.subTest(case=name):
                code, _out, err = self.main_capture(argv)
                self.assertEqual(code, console.EXIT_USAGE)
                self.assertIn("失败", err)
                self.assertNotIn("Traceback", err)
                self.assertNotIn("--force", err, "未知参数不得回显原始字符串")
                self.assertNotIn(":stream", err, "ADS 原文不得回显")

    def test_unknown_argument_text_is_not_echoed(self) -> None:
        marker = "--" + "秘密" * 20
        code, _out, err = self.main_capture([marker])
        self.assertEqual(code, console.EXIT_USAGE)
        self.assertNotIn(marker, err)
        self.assertIn("不支持的参数", err)

    @unittest.skipUnless(os.name == "nt", "ADS 是 Windows 语义")
    def test_resolve_root_rejects_ads_even_if_path_exists(self) -> None:
        data = self.tmp / "data"
        data.mkdir()
        with self.assertRaises(console.UsageError):
            console.resolve_root(str(data) + ":stream")

    def test_resolve_root_rejects_network_after_resolution(self) -> None:
        data = self.tmp / "data"
        data.mkdir()
        network = Path("\\\\server\\share\\data")
        with mock.patch.object(Path, "resolve", return_value=network):
            with self.assertRaises(console.UsageError) as ctx:
                console.resolve_root(str(data))
        self.assertIn("网络", str(ctx.exception))

    def test_resolve_root_allows_local_junction_target(self) -> None:
        target = self.tmp / "target"
        target.mkdir()
        link = self.tmp / "link"
        reason = _make_dir_link(link, target)
        if reason:
            self.skipTest(f"本机无法创建目录链接：{reason}")
        self.assertEqual(console.resolve_root(str(link)), target.resolve())

    def test_resolve_root_accepts_existing_absolute_dir(self) -> None:
        data = self.tmp / "data"
        data.mkdir()
        self.assertEqual(console.resolve_root(str(data)), data.resolve())

    def test_parse_args_forms(self) -> None:
        self.assertEqual(console.parse_args([]), {"help": False, "root": None})
        self.assertEqual(console.parse_args(["--root", "C:/x"]), {"help": False, "root": "C:/x"})
        self.assertEqual(console.parse_args(["--root=C:/x"]), {"help": False, "root": "C:/x"})
        self.assertTrue(console.parse_args(["-h"])["help"])

    def test_output_path_is_fixed_under_tool_root(self) -> None:
        self.assertEqual(console.OUTPUT_RELATIVE.as_posix(), "inbox/console/index.html")
        self.assertEqual(console.OUTPUT_PATH, REPO_ROOT / "inbox" / "console" / "index.html")
        self.assertEqual(console.ASSET_ROOT, REPO_ROOT / "tools" / "console_assets")


class EnvironmentTests(ConsoleTestBase):
    def test_projection_uses_detect_without_probe(self) -> None:
        fake = {"in_orca": True, "agents_available": ["claude", "codex", "unknown-agent"],
                "orca_reachable": True, "host": "Orca"}
        # 通道状态文件只在本机存在（inbox/logs/console-channel.json），不隔离会让本机多带 device
        with mock.patch("env.detect", return_value=fake) as detect,                 mock.patch("console._channel_state", return_value=None):
            projection = console.detect_environment()
        detect.assert_called_once_with(probe=False)
        self.assertEqual(projection, {"inOrca": True, "executorsAvailable": ["claude", "codex"]})
        self.assertEqual(set(projection), {"inOrca", "executorsAvailable"})

    def test_projection_keeps_device_from_channel_state(self) -> None:
        """通道状态里记了设备就带上（本机才有的信息），形状是 id + name。"""
        fake = {"in_orca": True, "agents_available": ["claude"], "orca_reachable": True, "host": "Orca"}
        state = {"device": {"id": "abc123", "name": "DESKTOP-X", "extra": "丢掉"}}
        with mock.patch("env.detect", return_value=fake),                 mock.patch("console._channel_state", return_value=state):
            projection = console.detect_environment()
        self.assertEqual(projection, {"inOrca": True, "executorsAvailable": ["claude"],
                                      "device": {"id": "abc123", "name": "DESKTOP-X"}})


class DependencyStateTests(ConsoleTestBase):
    def test_missing_p1_fails_explicitly_without_writing(self) -> None:
        if importlib.util.find_spec("console_data") is not None:
            self.skipTest("P1 已合入：缺依赖分支不再适用，完整 CLI 冒烟改由合包后的集成用例覆盖")
        data = self.tmp / "data"
        data.mkdir()
        before = _digest(data)
        repo_output = REPO_ROOT / "inbox" / "console" / "index.html"
        existed = repo_output.exists()
        if existed:
            stat_before = repo_output.stat().st_mtime_ns

        code, out, err = self.main_capture(["--root", str(data)])

        self.assertEqual(code, console.EXIT_DEPENDENCY)
        self.assertIn("console_data", err)
        self.assertIn("P1", err)
        self.assertIn("不会", err)
        self.assertEqual(out, "")
        self.assertEqual(_digest(data), before, "缺依赖时不得改动数据根")
        if existed:
            self.assertEqual(repo_output.stat().st_mtime_ns, stat_before)
        else:
            self.assertFalse(repo_output.exists(), "缺依赖时不得创建生成物")

    def test_invalid_snapshot_is_reported_not_rendered(self) -> None:
        broken = console_support.snapshot_fixture(scenario="full-orca")
        del broken["stats"]
        with self.assertRaises(console.SnapshotError) as ctx:
            self.run_console([], loader=lambda root, *, now, environment: broken,
                             environment={"inOrca": False, "executorsAvailable": []})
        self.assertEqual(ctx.exception.exit_code, console.EXIT_SNAPSHOT)
        self.assertFalse(self.output_path.exists())


class InjectedLoaderEndToEndTests(ConsoleTestBase):
    """替身 loader：只证明渲染 → 原子输出管线，不代表 P1 真实适配已通过。"""

    def test_full_pipeline_with_standin_loader(self) -> None:
        data = self.tmp / "data"
        data.mkdir()
        manifest = console_support.make_fixture(data, scenario="full-orca")
        before = _digest(data)
        snap = console_support.snapshot_fixture(scenario="full-orca")
        seen: dict = {}

        def loader(root, *, now, environment):
            seen["root"] = root
            seen["now"] = now
            seen["environment"] = environment
            return snap

        code, out, err = self.run_console(
            ["--root", str(data)], loader=loader, environment=manifest["environment"])

        self.assertEqual(code, console.EXIT_OK, err)
        self.assertEqual(seen["root"], data.resolve())
        self.assertEqual(seen["environment"], manifest["environment"])
        self.assertEqual(_digest(data), before, "生成过程不得改动输入记录")
        self.assertTrue(self.output_path.is_file())
        html = self.output_path.read_text(encoding="utf-8")
        self.assertIn('id="console-snapshot"', html)
        self.assertIn('id="console-avatars"', html)
        self.assertIn(str(self.output_path), out)
        self.assertIn(self.output_path.as_uri(), out)
        self.assertEqual([p for p in self.output_path.parent.iterdir()
                          if p.name.startswith(".index-")], [], "临时文件必须清理")
        self.assertEqual(json.loads(html.split('id="console-snapshot">', 1)[1]
                                    .split("</script>", 1)[0])["schemaVersion"],
                         cc.SCHEMA_VERSION)

    def test_default_root_is_tool_repo(self) -> None:
        """不传 --root 时使用 common.ROOT，而不是当前终端 cwd。"""
        snap = console_support.snapshot_fixture(scenario="empty")
        seen: dict = {}

        def loader(root, *, now, environment):
            seen["root"] = root
            return snap

        code, _out, err = self.run_console([], loader=loader,
                                           environment={"inOrca": False,
                                                        "executorsAvailable": []})
        self.assertEqual(code, console.EXIT_OK, err)
        self.assertEqual(seen["root"], console.common.ROOT)


class OutputSafetyTests(ConsoleTestBase):
    def _write(self, html: str) -> Path:
        return console.write_output(html, path=self.output_path, tool_root=self.tool_root)

    def test_atomic_replace_and_no_temp_leftovers(self) -> None:
        self._write("first")
        self._write("second")
        self.assertEqual(self.output_path.read_text(encoding="utf-8"), "second")
        leftovers = [p.name for p in self.output_path.parent.iterdir()
                     if p.name not in ("index.html",)]
        self.assertEqual(leftovers, [])

    def test_rejects_symlinked_directory(self) -> None:
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.tool_root / "inbox").mkdir(parents=True)
        reason = _make_dir_link(self.tool_root / "inbox" / "console", outside)
        if reason:
            self.skipTest(f"本机无法创建目录链接，覆盖防护未验证：{reason}")
        with self.assertRaises(console.OutputError):
            self._write("payload")
        self.assertEqual(list(outside.iterdir()), [], "链接目标目录不得被写入")

    def test_rejects_symlinked_target(self) -> None:
        outside = self.tmp / "outside.html"
        outside.write_text("original", encoding="utf-8")
        self.output_path.parent.mkdir(parents=True)
        try:
            os.symlink(outside, self.output_path)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"本机无法创建文件符号链接，覆盖防护未验证：{exc}")
        with self.assertRaises(console.OutputError):
            self._write("payload")
        self.assertEqual(outside.read_text(encoding="utf-8"), "original")
        self.assertTrue(self.output_path.is_symlink())

    def test_rejects_hardlink_target(self) -> None:
        source = self.tmp / "source.html"
        source.write_text("original", encoding="utf-8")
        self.output_path.parent.mkdir(parents=True)
        try:
            os.link(source, self.output_path)
        except OSError as exc:
            self.skipTest(f"本机无法创建硬链接，硬链接覆盖防护未验证：{exc}")
        with self.assertRaises(console.OutputError):
            self._write("payload")
        self.assertEqual(source.read_text(encoding="utf-8"), "original")
        self.assertEqual(self.output_path.read_text(encoding="utf-8"), "original")

    def test_replace_failure_keeps_old_file(self) -> None:
        self._write("old-content")
        with mock.patch.object(console.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(console.OutputError):
                self._write("new-content")
        self.assertEqual(self.output_path.read_text(encoding="utf-8"), "old-content")
        self.assertEqual([p.name for p in self.output_path.parent.iterdir()], ["index.html"])

    def test_rejects_empty_html_and_relative_path(self) -> None:
        with self.assertRaises(console.OutputError):
            self._write("")
        with self.assertRaises(console.OutputError):
            console.write_output("x", path=Path("inbox/console/index.html"),
                                 tool_root=self.tool_root)

    def test_rejects_private_component(self) -> None:
        target = self.tool_root / "private" / "console" / "index.html"
        with self.assertRaises(console.OutputError):
            console.write_output("x", path=target, tool_root=self.tool_root)


class RenderFailureTests(ConsoleTestBase):
    def test_missing_assets_reported_as_assets_error(self) -> None:
        snap = console_support.snapshot_fixture(scenario="empty")
        with self.assertRaises(console.AssetsError) as ctx:
            console.run(
                [], loader=lambda root, *, now, environment: snap,
                asset_root=self.tmp / "missing-assets",
                output_path=self.output_path, tool_root=self.tool_root,
                now=console_support.DEFAULT_NOW, environment={"inOrca": False,
                                                              "executorsAvailable": []})
        self.assertIn("资产", str(ctx.exception))
        self.assertFalse(self.output_path.exists())

    def test_render_error_keeps_old_file(self) -> None:
        console.write_output("old-content", path=self.output_path, tool_root=self.tool_root)
        snap = console_support.snapshot_fixture(scenario="empty")
        with self.assertRaises(console.AssetsError):
            console.run([], loader=lambda root, *, now, environment: snap,
                        asset_root=self.tmp / "none", output_path=self.output_path,
                        tool_root=self.tool_root, now=console_support.DEFAULT_NOW,
                        environment={"inOrca": False, "executorsAvailable": []})
        self.assertEqual(self.output_path.read_text(encoding="utf-8"), "old-content")


def _load_preview_module():
    spec = importlib.util.spec_from_file_location(
        "console_preview_under_test", REPO_ROOT / "tests" / "console_preview.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _InterruptingInput:
    def readline(self):
        raise KeyboardInterrupt


class PreviewLifecycleTests(unittest.TestCase):
    """预览生命周期：TemporaryDirectory 保持/清理，``--no-wait`` 不持久化。"""

    def setUp(self) -> None:
        self.preview = _load_preview_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="assistant-p2-preview-test-")
        self.tmp = Path(self._tmp.name)
        self.asset_root = make_standin_assets(self.tmp / "assets")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, argv, *, stdin=None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        stream = stdin if stdin is not None else io.StringIO("")
        with mock.patch.object(cr, "DEFAULT_ASSET_ROOT", self.asset_root), \
                mock.patch.object(sys, "stdin", stream), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.preview.main(argv)
        return code, out.getvalue(), err.getvalue()

    def _temp_root(self, out: str) -> Path:
        for line in out.splitlines():
            if line.startswith("临时根："):
                return Path(line.split("：", 1)[1])
        raise AssertionError("预览输出没有打印临时根")

    def test_no_wait_renders_then_cleans_up(self) -> None:
        code, out, err = self._run(
            ["--scenario", "full-orca", "--snapshot-only", "--no-wait"])
        self.assertEqual(code, 0, err)
        self.assertIn("SNAPSHOT-ONLY", out)
        self.assertIn("不代表真实数据", out)
        self.assertIn("--no-wait", out)
        self.assertIn("预览：", out)
        self.assertFalse(self._temp_root(out).exists(), "--no-wait 不得保留临时根")

    def test_default_waits_for_eof_then_cleans_up(self) -> None:
        code, out, err = self._run(
            ["--scenario", "full-orca", "--snapshot-only"], stdin=io.StringIO(""))
        self.assertEqual(code, 0, err)
        self.assertIn("按 Enter 结束并清理临时根", out)
        self.assertFalse(self._temp_root(out).exists(), "EOF 后必须清理临时根")

    def test_keyboard_interrupt_cleans_up(self) -> None:
        code, out, err = self._run(
            ["--scenario", "full-orca", "--snapshot-only"], stdin=_InterruptingInput())
        self.assertEqual(code, 0)
        self.assertIn("已中断", err)
        self.assertFalse(self._temp_root(out).exists(), "Ctrl+C 后必须清理临时根")

    def test_missing_p1_fails_clearly_and_cleans_up(self) -> None:
        if importlib.util.find_spec("console_data") is not None:
            self.skipTest("P1 已合入：缺依赖分支不再适用")
        code, out, err = self._run(["--scenario", "full-orca"])
        self.assertEqual(code, 1)
        self.assertIn("P1", err)
        self.assertFalse(self._temp_root(out).exists(), "失败也必须清理临时根")

    def test_no_wait_is_not_a_production_option(self) -> None:
        with self.assertRaises(console.UsageError):
            console.parse_args(["--no-wait"])


class ConsoleEncodingTests(unittest.TestCase):
    """本机 cp950 终端：入口要先 setup_stdio，否则打印简体中文就 UnicodeEncodeError。

    用子进程跑真实入口；工具目录复制到临时目录（目录名带简体字），输出落在临时
    ``inbox/console/``，不碰仓库里的页面。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="assistant-console-cp950-")
        self.tmp = Path(self._tmp.name)
        self.tool_root = self.tmp / "临时工具根"
        shutil.copytree(REPO_ROOT / "tools", self.tool_root / "tools",
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.env = dict(os.environ, PYTHONIOENCODING="cp950")
        self.env.pop("PYTHONUTF8", None)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_entry(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-B", str(self.tool_root / "tools" / "console.py"), *args],
            cwd=str(self.tmp), env=self.env, capture_output=True,
            encoding="utf-8", errors="replace", timeout=120)

    def test_help_under_cp950(self) -> None:
        proc = self.run_entry("--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--root", proc.stdout)
        self.assertNotIn("UnicodeEncodeError", proc.stderr)

    def test_generate_fixture_root_under_cp950(self) -> None:
        data = self.tmp / "data"
        data.mkdir()
        console_support.make_fixture(data, scenario="full-orca")
        proc = self.run_entry("--root", str(data))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("UnicodeEncodeError", proc.stderr)
        target = self.tool_root / "inbox" / "console" / "index.html"
        self.assertTrue(target.is_file())
        self.assertIn(f"已生成：{target}", proc.stdout)


class GitignoreTests(unittest.TestCase):
    def test_generated_page_is_git_ignored(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("本机没有 git，/inbox/console/ 忽略规则未验证")
        proc = subprocess.run(
            ["git", "check-ignore", "-v", "inbox/console/index.html"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0,
                         f"git check-ignore 未命中：{proc.stderr or proc.stdout}")
        self.assertIn("/inbox/console/", proc.stdout)
        self.assertIn(".gitignore", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
