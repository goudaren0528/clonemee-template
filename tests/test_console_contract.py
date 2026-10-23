# assistant-push-guard: fake-secrets（脱敏用例里的密钥都是虚构的，推送体积闸不扫这个文件的密钥）
"""P0 契约测试：路径/文本/链接安全边界、内嵌 JSON 与快照校验（标准库 unittest）。

只使用临时目录和纯虚构内容；不读真实 runs/daily，不写仓库里的任何记录。
目录链接场景在 Windows 上先试 symlink、再试 junction；都无法创建时明确 skip 并
记录未覆盖的缺口，不声称已覆盖。
"""
from __future__ import annotations

import os as _os, sys as _sys  # noqa: E402
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _isolation  # noqa: E402,F401  外发总开关与凭据隔离

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import console_contract as cc  # noqa: E402
import console_support  # noqa: E402

def _tree_digest(root: Path) -> dict[str, str]:
    digest: dict[str, str] = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            digest[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


def _level_for(completed: int) -> tuple[int, dict[str, int]]:
    if completed < 3:
        return 1, {"current": completed, "span": 3, "nextRemaining": 3 - completed}
    if completed < 10:
        return 2, {"current": completed - 3, "span": 7, "nextRemaining": 10 - completed}
    if completed < 30:
        return 3, {"current": completed - 10, "span": 20, "nextRemaining": 30 - completed}
    return 4, {"current": 1, "span": 1, "nextRemaining": 0}


def _make_dir_link(link: Path, target: Path) -> str | None:
    """创建目录链接；成功返回 None，失败返回可直接放进 skipTest 的原因。"""
    try:
        os.symlink(target, link, target_is_directory=True)
        return None
    except (OSError, NotImplementedError) as exc:
        if os.name == "nt":
            try:
                proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                      capture_output=True, text=True, errors="replace")
            except OSError as cmd_exc:  # pragma: no cover - 依赖平台
                return f"symlink 失败（{exc}），junction 也无法执行（{cmd_exc}）"
            if proc.returncode == 0:
                return None
            detail = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")
            return f"symlink 失败（{exc}），junction 失败（{detail[:120]}）"
        return str(exc)


# R3：日期/时间/offset 只允许 ASCII 数字；以下是同形 Unicode 数字的替换表。
_FULLWIDTH_DIGITS = str.maketrans("0123456789", "０１２３４５６７８９")
_ARABIC_INDIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")

_TIME_DIGIT_FIELDS = {
    "year": (0, 4), "month": (5, 7), "day": (8, 10),
    "hour": (11, 13), "minute": (14, 16), "second": (17, 19),
    "fraction": (20, 23), "offset-hour": (24, 26), "offset-minute": (27, 29),
}
_DATE_DIGIT_FIELDS = {"year": (0, 4), "month": (5, 7), "day": (8, 10)}


def _digit_variants(value: str, fields: dict) -> list[tuple[str, str]]:
    """整串与单字段替换为全角/阿拉伯印度数字，返回 (标签, 文本)。"""
    variants: list[tuple[str, str]] = []
    for script, table in (("fullwidth", _FULLWIDTH_DIGITS),
                          ("arabic-indic", _ARABIC_INDIC_DIGITS)):
        variants.append((f"whole-{script}", value.translate(table)))
        for field, (start, end) in fields.items():
            variants.append((f"{field}-{script}",
                             value[:start] + value[start:end].translate(table) + value[end:]))
    return variants


class TempRootCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "root"
        self.root.mkdir()
        self._write("runs/pending.md", "| N1020-01 |\n")
        self._write("runs/2026-10-20-demo.md", "---\ntype: run\n---\n")
        self._write("plans/daily/2026-10-20.md", "- [ ] T1 示例任务\n")
        self._write("agents/dev.md", "---\nname: 开发 Agent\n---\n")
        self._write("docs/delivery/demo/reviews/P1-r1.md", "verdict: pass\n")
        self._write("inbox/experience/demo.json", "{}\n")
        self._write("private/health.md", "虚构的私有内容\n")

    def _write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class TestConstants(unittest.TestCase):
    def test_frozen_contract_constants(self) -> None:
        self.assertEqual(cc.SCHEMA_VERSION, 2)
        self.assertEqual(cc.EXECUTOR_IDS, ("claude", "codex", "opencode"))
        self.assertEqual(cc.EXECUTOR_LABELS["codex"], "Codex")
        self.assertEqual(cc.MODE_IDS, ("worktree", "directory", "terminal"))
        self.assertEqual(cc.MODE_LABELS["terminal"], "新终端窗口")
        self.assertEqual(cc.ROLE_IDS, ("research", "product", "pm", "investigate", "dev", "accept"))
        self.assertEqual(cc.PAGE_IDS, ("pending", "dispatch", "runs", "roles"))
        self.assertEqual(cc.PENDING_TYPES, ("完成", "要拍板", "卡住", "交接"))
        self.assertEqual(cc.PENDING_STATUSES, ("待处理", "已处理", "unknown"))
        self.assertEqual(cc.PENDING_COLUMNS, ("待验收", "要拍板", "卡住"))
        self.assertEqual(cc.ACCEPT_CHOICE_IDS, ("accept", "reject", "later"))
        self.assertEqual(cc.ACCEPT_CHOICE_LABELS["later"], "稍后")
        self.assertEqual(cc.SOURCE_KINDS,
                         ("pending", "daily", "run", "role", "review", "experience",
                          "replies", "device"))
        self.assertEqual(cc.SAFE_TEXT_LIMIT, 500)
        self.assertEqual(cc.TRUNCATION_MARKER, "［已截断］")


class TestSafeText(unittest.TestCase):
    def test_plain_text_and_whitespace_normalization(self) -> None:
        self.assertEqual(cc.safe_text("普通标题：修复导出"), "普通标题：修复导出")
        self.assertEqual(cc.safe_text("第一行\r\n第二行\t第三行\u2028第四行\u2029第五行"),
                         "第一行 第二行 第三行 第四行 第五行")
        self.assertEqual(cc.safe_text("  前后空白  "), "前后空白")
        self.assertEqual(cc.safe_text("a\x00b\x07c\u200bd\ufeffe"), "a b c d e")
        self.assertEqual(cc.safe_text("    "), None)
        self.assertEqual(cc.safe_text(""), None)
        self.assertEqual(cc.safe_text("\n\t\u2028"), None)

    def test_non_string_values(self) -> None:
        self.assertIsNone(cc.safe_text(None))
        self.assertIsNone(cc.safe_text(True))
        self.assertIsNone(cc.safe_text(False))
        self.assertIsNone(cc.safe_text({"title": "x"}))
        self.assertIsNone(cc.safe_text(["x"]))
        self.assertIsNone(cc.safe_text(b"bytes"))
        self.assertEqual(cc.safe_text(42), "42")
        self.assertEqual(cc.safe_text(0), "0")
        self.assertEqual(cc.safe_text(3.5), "3.5")

    def test_markdown_links_keep_label_drop_target(self) -> None:
        self.assertEqual(cc.safe_text("[看这里](https://example.com/a?x=1)"), "看这里")
        self.assertEqual(cc.safe_text("![示意图](file:///C:/tmp/demo.png)"), "示意图")
        self.assertEqual(cc.safe_text("[甲][ref]"), "甲")
        self.assertEqual(cc.safe_text("<https://example.com/a>"), "https://example.com/a")
        self.assertEqual(cc.safe_text("打开 [体验页](http://127.0.0.1:5173/) 看看"),
                         "打开 体验页 看看")
        self.assertEqual(cc.safe_text("[看这个](https://example.com/a(b)c)"), "看这个")
        self.assertNotIn("example.com", cc.safe_text("[看这个](https://example.com/a)") or "")

    def test_sensitive_credentials_blocked(self) -> None:
        cases = [
            "-----BEGIN " "RSA PRIVATE KEY-----",
            "-----BEGIN " "OPENSSH PRIVATE KEY-----",
            "Authorization: Bearer abcdefghijklmn",
            "bearer abcdefghijklmn",
            "token=abc123",
            "access_token: abc123",
            "api-key = abc123",
            "password=hunter2",
            "client_secret: xyz",
            "密码：123456",
            "AKIAABCDEFGHIJKLMNOP",
            "ghp_aaaaaaaaaaaaaaaaaaaaaaaa",
            "github_pat_aaaaaaaaaaaaaaaaaaaa",
            "sk-aaaaaaaaaaaaaaaaaaaa",
            "xoxb-1234567890-abc",
            "eyJhbGciOiJIUzI1NiJ9." "eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghij",
        ]
        for value in cases:
            with self.subTest(value=value):
                self.assertIsNone(cc.safe_text(value))

    def test_private_paths_blocked_in_all_spellings(self) -> None:
        cases = [
            "private/health/2026.md",
            "runs/private/x.md",
            r"private\health\2026.md",
            r"C:\Users\demo\private\health.md",
            "/home/demo/private/health.md",
            "PRIVATE/health.md",
            "private%2Fhealth%2F2026.md",
            "private%5Chealth.md",
            "private%255Chealth.md",
            "见 private/health/2026.md 里的记录",
            "private",
        ]
        for value in cases:
            with self.subTest(value=value):
                self.assertIsNone(cc.safe_text(value))
        # 不是路径组件的普通词不误伤
        self.assertEqual(cc.safe_text("公开内容，没有私有引用"), "公开内容，没有私有引用")
        self.assertEqual(cc.safe_text("privateer 是另一个词"), "privateer 是另一个词")

    def test_markdown_private_relative_link_blocked(self) -> None:
        cases = [
            "见 [报告](private/finance/ledger.md)",
            "见 [报告](private%2Ffinance%2Fledger.md)",
            "见 [报告](../private/finance/ledger.md)",
            "见 [报告](PRIVATE\\finance\\ledger.md)",
            "见 ![截图](private/health/chart.png)",
        ]
        for value in cases:
            with self.subTest(value=value):
                self.assertIsNone(cc.safe_text(value))

    def test_obfuscated_sensitive_text_blocked(self) -> None:
        cases = [
            "to\u200bken=abc123",
            "pass\x00word=hunter2",
            "%74oken%3Dabc123",
            "secret%3Dabc123",
            "Bear\u200ber abcdefghijkl",
            "-----BEGIN\u200b PRIVATE KEY-----",
            "Authorization\u200b: Bearer abcdefghijkl",
        ]
        for value in cases:
            with self.subTest(value=value):
                self.assertIsNone(cc.safe_text(value))

    def test_sensitive_after_truncation_boundary_blocked(self) -> None:
        # 敏感检查基于完整原文，不能先截断再判断。
        self.assertIsNone(cc.safe_text("丙" * 520 + " token=abc123"))
        self.assertIsNone(cc.safe_text("丁" * 498 + " password=hunter2"))
        self.assertIsNone(cc.safe_text("戊" * 600 + " PRIVATE/health/x.md"))
        self.assertIsNone(cc.safe_text("己" * 510 + " ghp_aaaaaaaaaaaaaaaaaaaaaaaa"))

    def test_code_blocks_and_commands_blocked(self) -> None:
        cases = [
            "```\nrm -rf /\n```",
            "运行 `python tools/x.py` 即可",
            "a && b",
            "a || b",
            "值 $(whoami)",
            "值 ${HOME}",
            "输出 2>&1",
            "sudo shutdown",
            "powershell -c whoami",
            "cmd.exe /c dir",
        ]
        for value in cases:
            with self.subTest(value=value):
                self.assertIsNone(cc.safe_text(value))
        self.assertEqual(cc.safe_text("运行 python tools/console.py 生成页面"),
                         "运行 python tools/console.py 生成页面")

    def test_truncation_adds_visible_marker(self) -> None:
        text = "字" * 600
        result = cc.safe_text(text)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 500 + len(cc.TRUNCATION_MARKER))
        self.assertTrue(result.endswith(cc.TRUNCATION_MARKER))
        self.assertTrue(result.startswith("字" * 10))
        exact = "字" * 500
        self.assertEqual(cc.safe_text(exact), exact)
        custom = cc.safe_text("a" * 20, limit=10)
        self.assertEqual(custom, "a" * 10 + cc.TRUNCATION_MARKER)

    def test_invalid_limit_rejected(self) -> None:
        for limit in (0, -1, 1.5, True, "500"):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    cc.safe_text("x", limit=limit)  # type: ignore[arg-type]

    def test_oversized_raw_text_rejected(self) -> None:
        self.assertIsNone(cc.safe_text("x" * (cc.MAX_SAFE_TEXT_RAW + 1)))

    def test_safe_text_leaves_export_separators_to_safe_line(self) -> None:
        """safe_text 不负责导出分隔符；JS safeLine 再处理 · 和 →。"""
        value = "标题 · 决定 → 下一步"

        self.assertEqual(cc.safe_text(value), value)

    def test_plain_url_without_credentials_kept(self) -> None:
        self.assertEqual(cc.safe_text("详见 https://example.com/docs/guide"),
                         "详见 https://example.com/docs/guide")


class TestCheckedSource(TempRootCase):
    def test_accepts_whitelisted_shapes(self) -> None:
        cases = [
            ("runs/pending.md", "pending"),
            ("plans/daily/2026-10-20.md", "daily"),
            ("runs/2026-10-20-demo.md", "run"),
            ("agents/dev.md", "role"),
            ("docs/delivery/demo/reviews/P1-r1.md", "review"),
            ("inbox/experience/demo.json", "experience"),
        ]
        for relative, kind in cases:
            with self.subTest(relative=relative):
                result = cc.checked_source(self.root, relative, kind)
                self.assertIsNotNone(result)
                self.assertTrue(result.is_absolute())
                self.assertEqual(result.resolve(), (self.root / relative).resolve())

    def test_backslash_and_relative_dot_accepted(self) -> None:
        self.assertIsNotNone(cc.checked_source(self.root, r"runs\pending.md", "pending"))
        self.assertIsNotNone(cc.checked_source(self.root, "./runs/pending.md", "pending"))

    def test_missing_file_returns_none(self) -> None:
        self.assertIsNone(cc.checked_source(self.root, "runs/2026-01-01-none.md", "run"))
        self.assertIsNone(cc.checked_source(self.root, "agents/unknown.md", "role"))

    def test_rejects_lexical_attacks(self) -> None:
        cases = [
            ("../root/runs/pending.md", "pending"),
            ("runs/../../runs/pending.md", "pending"),
            ("/etc/passwd", "run"),
            (r"C:\Windows\win.ini", "run"),
            (r"\\server\share\pending.md", "pending"),
            (r"\\?\C:\Windows\win.ini", "run"),
            (r"\\.\C:\Windows\win.ini", "run"),
            ("runs/pending.md\x00.txt", "pending"),
            ("runs/pending.md\n", "pending"),
            ("runs/pending.md:secret", "pending"),
            ("runs//pending.md", "pending"),
            (" runs/pending.md", "pending"),
            ("runs/../pending.md", "pending"),
            ("runs/%2e%2e/pending.md", "pending"),
            ("runs/%2E%2E%2Fpending.md", "pending"),
        ]
        for relative, kind in cases:
            with self.subTest(relative=relative):
                self.assertIsNone(cc.checked_source(self.root, relative, kind))

    def test_rejects_wrong_shapes(self) -> None:
        cases = [
            ("runs/pending.md", "daily"),
            ("runs/pending.md", "role"),
            ("plans/daily/2026-13-40.md", "daily"),
            ("plans/daily/2026-10-20.txt", "daily"),
            ("plans/weekly/2026-W43.md", "daily"),
            ("runs/INDEX.md", "run"),
            ("runs/pending.md", "run"),
            ("runs/nested/demo.md", "run"),
            ("agents/unknown.md", "role"),
            ("agents/dev.markdown", "role"),
            ("docs/delivery/demo/reviews/P2-r1.md", "review"),
            ("docs/delivery/demo/reviews/P1-r2.md", "review"),
            ("docs/delivery/demo/P1-r1.md", "review"),
            ("inbox/experience/demo.txt", "experience"),
            ("inbox/experience/.demo.json", "experience"),
        ]
        for relative, kind in cases:
            with self.subTest(relative=relative):
                self.assertIsNone(cc.checked_source(self.root, relative, kind))

    def test_rejects_private_components(self) -> None:
        self._write("private/runs/pending.md", "x")
        cases = [
            ("private/runs/pending.md", "run"),
            ("PRIVATE/runs/pending.md", "run"),
            ("Private/runs/pending.md", "run"),
            ("private%2Fruns%2Fpending.md", "run"),
            ("private%5Cruns%5Cpending.md", "run"),
        ]
        for relative, kind in cases:
            with self.subTest(relative=relative):
                self.assertIsNone(cc.checked_source(self.root, relative, kind))

    def test_invalid_kind_or_root(self) -> None:
        self.assertIsNone(cc.checked_source(self.root, "runs/pending.md", "other"))
        self.assertIsNone(cc.checked_source(Path("relative/dir"), "runs/pending.md", "pending"))
        self.assertIsNone(cc.checked_source(self.tmp / "missing", "runs/pending.md", "pending"))

    def test_link_escaping_root_rejected(self) -> None:
        outside = self.tmp / "outside"
        (outside / "runs").mkdir(parents=True)
        (outside / "runs" / "pending.md").write_text("x", encoding="utf-8")
        root = self.tmp / "root-escape"
        root.mkdir()
        reason = _make_dir_link(root / "runs", outside / "runs")
        if reason:
            self.skipTest(f"本机无法创建目录链接，未覆盖 link 越界场景：{reason}")
        self.assertIsNone(cc.checked_source(root, "runs/pending.md", "pending"))

    def test_link_into_private_rejected(self) -> None:
        root = self.tmp / "root-private-link"
        private_runs = root / "private" / "runs"
        private_runs.mkdir(parents=True)
        (private_runs / "pending.md").write_text("x", encoding="utf-8")
        reason = _make_dir_link(root / "runs", private_runs)
        if reason:
            self.skipTest(f"本机无法创建目录链接，未覆盖 link->private 场景：{reason}")
        self.assertIsNone(cc.checked_source(root, "runs/pending.md", "pending"))

    def test_link_alias_inside_root_still_rejected(self) -> None:
        root = self.tmp / "root-inner-link"
        real_runs = root / "real-runs"
        real_runs.mkdir(parents=True)
        (real_runs / "pending.md").write_text("x", encoding="utf-8")
        reason = _make_dir_link(root / "runs", real_runs)
        if reason:
            self.skipTest(f"本机无法创建目录链接，未覆盖 link 别名场景：{reason}")
        # 解析后形状不再是 runs/pending.md，按“解析后仍符合白名单”拒绝
        self.assertIsNone(cc.checked_source(root, "runs/pending.md", "pending"))

    def test_private_data_root_rejected(self) -> None:
        root = self.tmp / "private"
        (root / "runs").mkdir(parents=True)
        (root / "runs" / "pending.md").write_text("x", encoding="utf-8")
        self.assertIsNone(cc.checked_source(root, "runs/pending.md", "pending"))

    def test_private_ancestor_of_root_rejected(self) -> None:
        root = self.tmp / "PRIVATE" / "data"
        (root / "runs").mkdir(parents=True)
        (root / "runs" / "pending.md").write_text("x", encoding="utf-8")
        self.assertIsNone(cc.checked_source(root, "runs/pending.md", "pending"))

    def test_root_alias_resolving_into_private_rejected(self) -> None:
        actual = self.tmp / "private" / "actual"
        (actual / "runs").mkdir(parents=True)
        (actual / "runs" / "pending.md").write_text("x", encoding="utf-8")
        alias = self.tmp / "safe-root-name"
        reason = _make_dir_link(alias, actual)
        if reason:
            self.skipTest(f"本机无法创建根别名链接，未覆盖 root->private 场景：{reason}")
        self.assertIsNone(cc.checked_source(alias, "runs/pending.md", "pending"))

    def test_private_link_target_and_descendants_rejected(self) -> None:
        for index, alias_name in enumerate(("private", "PrIvAtE"), start=1):
            with self.subTest(alias=alias_name):
                # Windows 文件名大小写不敏感，每个变体用独立根目录。
                root = self.tmp / f"data-{index}"
                (root / "runs").mkdir(parents=True)
                (root / "runs" / "pending.md").write_text("x", encoding="utf-8")
                (root / "runs" / "2026-10-20-demo.md").write_text(
                    "---\ntype: run\n---\n", encoding="utf-8")
                # 计划 173：根下 private（含大小写）的真实目标及其后代整体拒绝。
                reason = _make_dir_link(root / alias_name, root / "runs")
                if reason:
                    self.skipTest(
                        f"本机无法创建 private 目录链接，未覆盖 {alias_name}->runs 场景：{reason}")
                self.assertIsNone(cc.checked_source(root, "runs/pending.md", "pending"))
                self.assertIsNone(cc.checked_source(root, "runs/2026-10-20-demo.md", "run"))

    def test_normal_root_with_private_sibling_not_overblocked(self) -> None:
        # setUp 已写入真实的 private/health.md；你白名单目标不能被误拒。
        self.assertIsNotNone(cc.checked_source(self.root, "runs/pending.md", "pending"))
        self.assertIsNotNone(cc.checked_source(self.root, "inbox/experience/demo.json", "experience"))


class TestDisplayPath(TempRootCase):
    def test_relative_and_absolute_paths(self) -> None:
        expected = (self.root / "runs" / "pending.md").resolve().as_posix()
        self.assertEqual(cc.display_path(self.root, "runs/pending.md"), expected)
        outside = self.tmp / "outside.md"
        outside.write_text("x", encoding="utf-8")
        self.assertEqual(cc.display_path(self.root, str(outside)), outside.resolve().as_posix())

    def test_base_override(self) -> None:
        base = self.root / "plans" / "daily"
        expected = (base / "2026-10-20.md").resolve().as_posix()
        self.assertEqual(cc.display_path(self.root, "2026-10-20.md", base=base), expected)

    def test_rejects_suspicious_paths(self) -> None:
        cases = [
            None,
            "",
            "   ",
            "../outside.md",
            "runs/../../outside.md",
            "private/health/2026.md",
            r"runs\private\x.md",
            "/home/demo/private/x.md",
            r"\\server\share\x.md",
            r"\\?\C:\Windows\win.ini",
            "//server/share/x.md",
            "runs/pending.md:secret",
            "runs/pending.md\x07",
            "file:///C:/tmp/x.md",
            "C:relative.md",
            "%2e%2e/%2e%2e/outside.md",
            "private%2Fx.md",
            "config/.env",
            "keys/id_rsa",
            "certs/server.pem",
        ]
        for value in cases:
            with self.subTest(value=value):
                self.assertIsNone(cc.display_path(self.root, value))

    def test_relative_base_must_be_absolute(self) -> None:
        self.assertIsNone(cc.display_path(self.root, "x.md", base=Path("relative")))

    def test_absolute_windows_and_posix_shape(self) -> None:
        if os.name == "nt":
            self.assertIsNotNone(cc.display_path(self.root, r"C:\Users\demo\notes.md"))
        else:
            self.assertIsNotNone(cc.display_path(self.root, "/home/demo/notes.md"))


class TestSafeExperienceUrl(TempRootCase):
    def setUp(self) -> None:
        super().setUp()
        self.page = self._write("ui/index.html", "<!doctype html>\n")
        self.page_uri = self.page.resolve().as_uri()

    def test_file_uri_inside_root(self) -> None:
        self.assertEqual(cc.safe_experience_url(self.page_uri, root=self.root), self.page_uri)

    def test_file_uri_with_harmless_query_and_fragment(self) -> None:
        for suffix in ("#section-2", "?view=summary", "?view=summary#section-2",
                       "?tab=runs&mode=full#part-2"):
            with self.subTest(suffix=suffix):
                self.assertEqual(cc.safe_experience_url(self.page_uri + suffix, root=self.root),
                                 self.page_uri + suffix)

    def test_file_uri_outside_root_needs_artifact_root(self) -> None:
        outside = self.tmp / "artifacts" / "index.html"
        outside.parent.mkdir(parents=True)
        outside.write_text("<!doctype html>\n", encoding="utf-8")
        uri = outside.resolve().as_uri()
        self.assertIsNone(cc.safe_experience_url(uri, root=self.root))
        self.assertEqual(
            cc.safe_experience_url(uri, root=self.root, artifact_roots=(outside.parent,)), uri)

    def test_artifact_root_cannot_be_drive_or_home(self) -> None:
        outside = self.tmp / "artifact-2" / "index.html"
        outside.parent.mkdir(parents=True)
        outside.write_text("x", encoding="utf-8")
        uri = outside.resolve().as_uri()
        drive_root = Path(outside.resolve().anchor)
        self.assertIsNone(cc.safe_experience_url(uri, root=self.root, artifact_roots=(drive_root,)))
        self.assertIsNone(cc.safe_experience_url(uri, root=self.root,
                                                 artifact_roots=(Path.home(),)))

    def test_file_uri_requires_existing_regular_file(self) -> None:
        missing = (self.root / "ui" / "missing.html").as_uri()
        self.assertIsNone(cc.safe_experience_url(missing, root=self.root))
        directory = (self.root / "ui").as_uri()
        self.assertIsNone(cc.safe_experience_url(directory, root=self.root))

    def test_file_uri_rejects_unsafe_shapes(self) -> None:
        cases = [
            "file://localhost" + self.page_uri[len("file://"):],
            "file:///%2e%2e/%2e%2e/secret.html",
            self.page_uri.replace("/ui/", "/%70rivate/"),
            "file:///C:/%5C%5Cserver%5Cshare%5Cx.html",
            "file:///" + "x" * 3000,
        ]
        for url in cases:
            with self.subTest(url=url[:80]):
                self.assertIsNone(cc.safe_experience_url(url, root=self.root))

    def test_file_uri_sensitive_query_or_fragment_rejected(self) -> None:
        cases = [
            "?token=abc",
            "?access_token=abc",
            "?api_key=abc",
            "?password=abc",
            "?secret=abc",
            "?q=ghp_aaaaaaaaaaaaaaaaaaaaaaaa",
            "?q=%00",
            "?a=%2f",
            "?a=%5c",
            "#access_token=abc",
            "#token=abc",
        ]
        for suffix in cases:
            with self.subTest(suffix=suffix):
                self.assertIsNone(cc.safe_experience_url(self.page_uri + suffix, root=self.root))

    def test_private_file_inside_root_rejected(self) -> None:
        private_page = self._write("private/ui/index.html", "<html></html>\n")
        self.assertIsNone(cc.safe_experience_url(private_page.resolve().as_uri(), root=self.root))

    def test_root_private_link_target_rejected(self) -> None:
        root = self.tmp / "exp-root"
        page = root / "ui" / "index.html"
        page.parent.mkdir(parents=True)
        page.write_text("<!doctype html>\n", encoding="utf-8")
        # 根下 private junction -> ui：其真实目标（含后代）不能作为体验链接。
        reason = _make_dir_link(root / "private", root / "ui")
        if reason:
            self.skipTest(f"本机无法创建 private 目录链接，未覆盖体验链接真实目标场景：{reason}")
        self.assertIsNone(cc.safe_experience_url(page.resolve().as_uri(), root=root))

    def test_ads_file_uri_rejected_on_windows(self) -> None:
        if os.name != "nt":
            self.skipTest("ADS 是 Windows 语义；当前平台未覆盖")
        self.assertIsNone(cc.safe_experience_url(self.page_uri + ":secret", root=self.root))

    def test_loopback_http_urls(self) -> None:
        cases = [
            "http://127.0.0.1:5173/",
            "http://localhost:3000/app",
            "http://[::1]:8080/view?view=summary#section-2",
            "https://127.0.0.1/",
            "http://LOCALHOST:8080/x",
        ]
        for url in cases:
            with self.subTest(url=url):
                result = cc.safe_experience_url(url, root=self.root)
                self.assertIsNotNone(result)
                self.assertIn("://", result)

    def test_http_rejects_non_loopback_and_confusion(self) -> None:
        cases = [
            "http://localhost.evil.example/app",
            "http://127.0.0.1.evil.example/app",
            "http://192.168.1.10:8080/",
            "http://10.0.0.1/",
            "http://user:pass@127.0.0.1:8080/",
            "http://127.0.0.1:99999/",
            "http://%6cocalhost:8080/",
            "http://localhost.:8080/",
            "//127.0.0.1:8080/",
            "javascript:alert(1)",
            "data:text/html,<b>x</b>",
            "ftp://127.0.0.1/x",
            "http://127.0.0.1:8080/a b",
            "http://127.0.0.1:8080/a\nb",
            r"http:\\127.0.0.1:8080\a",
            "http://127.0.0.1:8080/docs/private/x",
            "http://127.0.0.1:8080/%2e%2e/secret",
            "http://127.0.0.1:8080/%70rivate/x",
        ]
        for url in cases:
            with self.subTest(url=url):
                self.assertIsNone(cc.safe_experience_url(url, root=self.root))

    def test_http_sensitive_query_and_fragment_rejected(self) -> None:
        cases = [
            "http://127.0.0.1:8080/?token=abc",
            "http://127.0.0.1:8080/?access_token=abc",
            "http://127.0.0.1:8080/?api_key=abc",
            "http://127.0.0.1:8080/?password=abc",
            "http://127.0.0.1:8080/?secret=abc",
            "http://127.0.0.1:8080/#access_token=abc",
            "http://127.0.0.1:8080/?q=ghp_aaaaaaaaaaaaaaaaaaaaaaaa",
            "http://127.0.0.1:8080/?q=%00",
            "http://127.0.0.1:8080/a%2Fb",
        ]
        for url in cases:
            with self.subTest(url=url):
                self.assertIsNone(cc.safe_experience_url(url, root=self.root))

    def test_http_harmless_query_and_fragment_preserved(self) -> None:
        url = "http://127.0.0.1:5173/view?view=summary&tab=runs#section-2"
        self.assertEqual(cc.safe_experience_url(url, root=self.root), url)

    def test_non_string_and_oversized(self) -> None:
        self.assertIsNone(cc.safe_experience_url(None, root=self.root))  # type: ignore[arg-type]
        self.assertIsNone(cc.safe_experience_url(123, root=self.root))  # type: ignore[arg-type]
        self.assertIsNone(cc.safe_experience_url(" http://127.0.0.1/", root=self.root))
        self.assertIsNone(cc.safe_experience_url(
            "http://127.0.0.1/" + "a" * cc.MAX_URL_LENGTH, root=self.root))


class TestInlineJson(unittest.TestCase):
    def test_round_trip_preserves_chinese(self) -> None:
        snapshot = {"标题": "控制台 v0", "nested": {"items": [1, None, True]}}
        text = cc.inline_json(snapshot)
        self.assertIn("控制台 v0", text)
        self.assertEqual(json.loads(text), snapshot)

    def test_script_terminators_and_html_chars_escaped(self) -> None:
        text = cc.inline_json({"x": "</script><!-- & <b>"})
        self.assertNotIn("<", text)
        self.assertNotIn(">", text)
        self.assertNotIn("&", text)
        self.assertIn("\\u003c", text)
        self.assertEqual(json.loads(text), {"x": "</script><!-- & <b>"})

    def test_line_and_paragraph_separators_escaped(self) -> None:
        text = cc.inline_json({"x": "a\u2028b\u2029c"})
        self.assertNotIn("\u2028", text)
        self.assertNotIn("\u2029", text)
        self.assertIn("\\u2028", text)
        self.assertEqual(json.loads(text), {"x": "a\u2028b\u2029c"})

    def test_non_dict_rejected(self) -> None:
        for value in (None, [], "x", 1):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    cc.inline_json(value)  # type: ignore[arg-type]


def _source(field: str = "标题") -> dict:
    return {"path": (REPO_ROOT / "runs" / "pending.md").as_posix(),
            "lineStart": 1, "lineEnd": 1, "field": field}


def _metric(value: int | None = 0) -> dict:
    return {"value": value, "sourceIds": ["N1020-01"], "reason": None}


def _base_snapshot() -> dict:
    pending = {
        "id": "N1020-01",
        "type": "完成",
        "status": "已处理",
        "column": "待验收",
        "project": "示例项目",
        "item": "T1 示例事项",
        "title": "示例待验收",
        "summary": "一句话说明",
        "roleId": "dev",
        "senderName": "开发 Agent",
        "createdAt": "2026-10-20T09:00:00+08:00",
        "detailPath": (REPO_ROOT / "runs" / "2026-10-20-demo.md").as_posix(),
        "runId": "runs/2026-10-20-demo.md",
        "choices": [
            {"id": "accept", "label": "验收通过", "source": "PRD"},
            {"id": "reject", "label": "打回", "source": "PRD"},
            {"id": "later", "label": "稍后", "source": "PRD"},
        ],
        "processedAt": "2026-10-20T09:30:00+08:00",
        "processedNote": "已处理说明",
        "experience": {
            "ui": "yes",
            "state": "recorded-started",
            "url": "http://127.0.0.1:5173/",
            "recordedAt": "2026-10-20T09:20:00+08:00",
            "expiresAt": "2026-10-20T23:59:00+08:00",
            "tests": {"passed": 3, "total": 4, "summary": "3/4 通过"},
            "focus": ["重点一"],
            "sources": [_source("体验")],
        },
        "sources": [_source()],
        "diagnostics": [{"code": "example", "message": "示例诊断", "source": None}],
    }
    dispatch = {
        "id": "plans/daily/2026-10-20.md#T1",
        "taskNo": "T1",
        "project": "示例项目",
        "title": "示例开发任务",
        "summary": "一句话说明",
        "planPath": (REPO_ROOT / "plans" / "daily" / "2026-10-20.md").as_posix(),
        "roleId": "dev",
        "declaredExecutor": "opencode",
        "changesCode": True,
        "kind": "single",
        "prdPath": None,
        "difficulty": 2,
        "stages": ["实现", "自测", "提交"],
        "outputs": ["代码提交"],
        "defaultExecutor": "opencode",
        "defaultMode": "worktree",
        "defaultReason": "开发默认 OpenCode / 新建工作区",
        "fallback": False,
        "selectable": True,
        "disabledReason": None,
        "sources": [_source()],
    }
    run = {
        "id": "runs/2026-10-20-demo.md",
        "kind": "single",
        "project": "示例项目",
        "item": "T1 示例事项",
        "status": "done",
        "rawStatus": "done",
        "roleId": "dev",
        "createdAt": "2026-10-20T08:00:00+08:00",
        "completedAt": "2026-10-20T09:30:00+08:00",
        "lastChangedAt": "2026-10-20T09:30:00+08:00",
        "stale": False,
        "stages": [{"label": "实现", "state": "done", "source": "runs/2026-10-20-demo.md#L10"}],
        "agents": [{
            "id": "agent-1", "roleId": "dev", "executor": "opencode", "packageId": None,
            "activity": "已完成", "state": "active", "lastChangedAt": None,
        }],
        "packages": [{
            "id": "P1", "state": "done", "dependencies": [], "reviewRound": 1,
            "updatedAt": "2026-10-20", "firstReview": {
                "round": 1, "verdict": "pass", "source": "docs/delivery/demo/reviews/P1-r1.md",
            },
        }],
        "progress": ["完成实现"],
        "decisions": ["按计划推进"],
        "pendingIds": [],
        "detailPath": (REPO_ROOT / "runs" / "2026-10-20-demo.md").as_posix(),
        "firstReview": {"round": 1, "verdict": "pass", "source": "runs/2026-10-20-demo.md#L20"},
        "sources": [_source()],
        "diagnostics": [],
    }
    role = {
        "id": "dev",
        "name": "开发 Agent",
        "status": "active",
        "executors": ["opencode", "codex"],
        "skills": ["dev-build"],
        "useWhen": "改代码",
        "avatarKey": "dev",
        "completed": _metric(4),
        "firstPass": _metric(2),
        "activeSessions": _metric(1),
        "level": 2,
        "levelProgress": {"current": 1, "span": 7, "nextRemaining": 6},
        "isNew": False,
        "sources": [_source("角色")],
        "workspace": "worktree",
    }
    return {
        "schemaVersion": 2,
        "generatedAt": "2026-10-20T10:00:00+08:00",
        "localDate": "2026-10-20",
        "environment": {"inOrca": True, "executorsAvailable": ["claude", "codex", "opencode"]},
        "stats": {"pendingCount": _metric(1), "runningCount": _metric(1),
                  "completedToday": _metric(1), "dispatchedToday": _metric(1),
                  "completedWeek": _metric(1), "clearStreak": _metric(None)},
        "pending": [pending],
        "dispatchTasks": [dispatch],
        "runs": [run],
        "roles": [role],
        "diagnostics": [],
    }


class TestValidateSnapshot(unittest.TestCase):
    def test_valid_snapshot_has_no_errors(self) -> None:
        self.assertEqual(cc.validate_snapshot(_base_snapshot()), [])

    def test_empty_or_non_dict(self) -> None:
        self.assertEqual(cc.validate_snapshot(None), ["snapshot 必须是对象"])  # type: ignore[arg-type]
        errors = cc.validate_snapshot({})
        self.assertGreater(len(errors), 5)
        self.assertIn("snapshot.schemaVersion 缺失", errors)

    def test_top_level_field_rules(self) -> None:
        cases = []
        snapshot = _base_snapshot()
        snapshot["schemaVersion"] = 1  # v1.1 起快照版本是 2，旧版本号要报
        cases.append((snapshot, "snapshot.schemaVersion"))
        snapshot = _base_snapshot()
        snapshot["generatedAt"] = "2026-10-20T10:00:00"
        cases.append((snapshot, "snapshot.generatedAt"))
        snapshot = _base_snapshot()
        snapshot["localDate"] = "2026-13-40"
        cases.append((snapshot, "snapshot.localDate"))
        snapshot = _base_snapshot()
        del snapshot["stats"]["clearStreak"]
        cases.append((snapshot, "snapshot.stats.clearStreak"))
        snapshot = _base_snapshot()
        snapshot["stats"]["pendingCount"] = 1
        cases.append((snapshot, "snapshot.stats.pendingCount"))
        snapshot = _base_snapshot()
        snapshot["stats"]["pendingCount"]["value"] = -1
        cases.append((snapshot, "snapshot.stats.pendingCount.value"))
        snapshot = _base_snapshot()
        snapshot["environment"]["executorsAvailable"] = ["vim"]
        cases.append((snapshot, "snapshot.environment.executorsAvailable[0]"))
        snapshot = _base_snapshot()
        snapshot["pending"] = {}
        cases.append((snapshot, "snapshot.pending"))
        for snapshot, needle in cases:
            with self.subTest(needle=needle):
                errors = cc.validate_snapshot(snapshot)
                self.assertTrue(any(needle in error for error in errors), errors)

    def test_time_fields_reject_malformed_iso(self) -> None:
        invalid = [
            "2026-10-20T09:30:01:00+08:00",   # 重复秒
            "2026-10-20T09:30:01+08:00:00",   # offset 多余冒号/秒
            "2026-10-20T09:30:01",            # 缺 offset
            "2026-10-20T09:30:01+0800",       # offset 无冒号
            "2026-10-20T09:30:01+08",         # offset 只有小时
            "20261020T093001+0800",           # 基本格式
            "2026-10-20 09:30:01+08:00",      # 空格分隔
            "2026-10-20T25:00:00+08:00",      # 非法小时
            "2026-10-20T09:60:00+08:00",      # 非法分钟
            "2026-10-20T09:30:60+08:00",      # 非法秒
            "2026-13-01T00:00:00+08:00",      # 非法月份
            "2026-02-30T00:00:00+08:00",      # 非法日历日
            "2026-10-20T09:30:01+25:00",      # 非法 offset 小时
            "2026-10-20T09:30:01+08:60",      # 非法 offset 分钟
            "2026-10-20T09:30:01+08:00\n",    # 控制字符
        ]
        for value in invalid:
            with self.subTest(value=value):
                snapshot = _base_snapshot()
                snapshot["runs"][0]["agents"][0]["lastChangedAt"] = value
                errors = cc.validate_snapshot(snapshot)
                self.assertTrue(
                    any("snapshot.runs[0].agents[0].lastChangedAt" in error for error in errors),
                    errors)
                # 诊断只含字段路径与规则，不回显被拒原值。
                self.assertNotIn(value.strip(), " ".join(errors))

    def test_time_fields_accept_valid_iso(self) -> None:
        valid = [
            "2026-10-20T09:30:01+08:00",
            "2026-10-20T09:30:01Z",
            "2026-10-20T09:30:01.1+08:00",
            "2026-10-20T09:30:01.123+08:00",
            "2026-10-20T09:30:01.123456+08:00",
            "2026-10-20T09:30:01-05:30",
            "2026-10-20T00:00:00+00:00",
        ]
        for value in valid:
            with self.subTest(value=value):
                snapshot = _base_snapshot()
                snapshot["runs"][0]["agents"][0]["lastChangedAt"] = value
                self.assertEqual(cc.validate_snapshot(snapshot), [])

    def test_record_fields_allow_date_and_unknown(self) -> None:
        # 记录里仅有日期的字段保留 YYYY-MM-DD；未知仍用 null。
        snapshot = _base_snapshot()
        snapshot["runs"][0]["createdAt"] = "2026-10-20"
        snapshot["runs"][0]["completedAt"] = None
        snapshot["runs"][0]["agents"][0]["lastChangedAt"] = None
        self.assertEqual(cc.validate_snapshot(snapshot), [])

    def test_generated_at_requires_datetime_with_offset(self) -> None:
        for value in ("2026-10-20", "2026-10-20T09:30:01",
                      "2026-10-20T09:30:01:00+08:00"):
            with self.subTest(value=value):
                snapshot = _base_snapshot()
                snapshot["generatedAt"] = value
                errors = cc.validate_snapshot(snapshot)
                self.assertTrue(any("snapshot.generatedAt" in error for error in errors), errors)
                self.assertNotIn(value, " ".join(errors))

    def test_local_date_is_strict_calendar_date(self) -> None:
        for value in ("2026-W43-1", "20261020", "2026-02-30",
                      "2026-10-20T00:00:00+08:00"):
            with self.subTest(value=value):
                snapshot = _base_snapshot()
                snapshot["localDate"] = value
                self.assertTrue(
                    any("snapshot.localDate" in error for error in cc.validate_snapshot(snapshot)))

    def test_non_ascii_digits_rejected_in_time_fields(self) -> None:
        # R3：日期/时间/小数秒/offset 只能 ASCII；整串与单字段替换都必须拒绝。
        base_time = "2026-10-20T09:30:01.123+08:00"
        for label, value in _digit_variants(base_time, _TIME_DIGIT_FIELDS):
            with self.subTest(field="Agent.lastChangedAt", variant=label):
                snapshot = _base_snapshot()
                snapshot["runs"][0]["agents"][0]["lastChangedAt"] = value
                errors = cc.validate_snapshot(snapshot)
                self.assertTrue(
                    any("snapshot.runs[0].agents[0].lastChangedAt" in error for error in errors),
                    f"{label}: {errors}")
                self.assertNotIn(value, " ".join(errors))
            with self.subTest(field="generatedAt", variant=label):
                snapshot = _base_snapshot()
                snapshot["generatedAt"] = value
                errors = cc.validate_snapshot(snapshot)
                self.assertTrue(any("snapshot.generatedAt" in error for error in errors),
                                f"{label}: {errors}")
                self.assertNotIn(value, " ".join(errors))

    def test_non_ascii_digits_rejected_in_date_fields(self) -> None:
        base_date = "2026-10-20"
        for label, value in _digit_variants(base_date, _DATE_DIGIT_FIELDS):
            with self.subTest(field="localDate", variant=label):
                snapshot = _base_snapshot()
                snapshot["localDate"] = value
                errors = cc.validate_snapshot(snapshot)
                self.assertTrue(any("snapshot.localDate" in error for error in errors),
                                f"{label}: {errors}")
                self.assertNotIn(value, " ".join(errors))
            with self.subTest(field="package.updatedAt", variant=label):
                snapshot = _base_snapshot()
                snapshot["runs"][0]["packages"][0]["updatedAt"] = value
                errors = cc.validate_snapshot(snapshot)
                self.assertTrue(
                    any("snapshot.runs[0].packages[0].updatedAt" in error for error in errors),
                    f"{label}: {errors}")
                self.assertNotIn(value, " ".join(errors))

    def test_ascii_time_controls_still_valid(self) -> None:
        # 合法 ASCII、仅日期、未知 null 与重复秒拒绝的对照。
        snapshot = _base_snapshot()
        snapshot["generatedAt"] = "2026-10-20T10:00:00.5+08:00"
        snapshot["localDate"] = "2026-10-20"
        snapshot["runs"][0]["agents"][0]["lastChangedAt"] = "2026-10-20T09:30:01.123+08:00"
        snapshot["runs"][0]["packages"][0]["updatedAt"] = "2026-10-20"
        self.assertEqual(cc.validate_snapshot(snapshot), [])
        snapshot["runs"][0]["agents"][0]["lastChangedAt"] = None
        snapshot["runs"][0]["createdAt"] = "2026-10-20"
        self.assertEqual(cc.validate_snapshot(snapshot), [])
        snapshot = _base_snapshot()
        snapshot["runs"][0]["agents"][0]["lastChangedAt"] = "2026-10-20T09:30:01:00+08:00"
        self.assertTrue(
            any("lastChangedAt" in error for error in cc.validate_snapshot(snapshot)))

    def test_pending_rules(self) -> None:
        cases = [
            (lambda s: s["pending"][0].__setitem__("type", "未知类型"), "pending[0].type"),
            (lambda s: s["pending"][0].__setitem__("status", "处理中"), "pending[0].status"),
            (lambda s: s["pending"][0].__setitem__("column", "第四栏"), "pending[0].column"),
            (lambda s: s["pending"][0].__setitem__("column", "要拍板"), "column 与 type 不匹配"),
            (lambda s: s["pending"][0].__setitem__("roleId", "hacker"), "pending[0].roleId"),
            (lambda s: s["pending"][0].__setitem__("detailPath", "runs/x.md"),
             "pending[0].detailPath"),
            (lambda s: s["pending"][0]["choices"][0].__setitem__("id", "approve"),
             "choices[0].id"),
            (lambda s: s["pending"][0]["experience"].__setitem__("state", "alive"),
             "experience.state"),
            (lambda s: s["pending"][0]["experience"]["tests"].__setitem__("passed", 9),
             "experience.tests.passed"),
            (lambda s: s["pending"][0]["sources"][0].__setitem__("lineEnd", 0),
             "sources[0].lineEnd"),
            (lambda s: s["pending"][0]["sources"][0].__setitem__("path", "runs/x.md"),
             "sources[0].path"),
            (lambda s: s["pending"][0]["diagnostics"].append("字符串诊断"),
             "diagnostics[1]"),
        ]
        for mutate, needle in cases:
            snapshot = _base_snapshot()
            mutate(snapshot)
            with self.subTest(needle=needle):
                errors = cc.validate_snapshot(snapshot)
                self.assertTrue(any(needle in error for error in errors), errors)

    def test_dispatch_rules(self) -> None:
        cases = [
            (lambda s: s["dispatchTasks"][0].__setitem__("kind", "team"), "kind"),
            (lambda s: s["dispatchTasks"][0].__setitem__("difficulty", 4), "difficulty"),
            (lambda s: s["dispatchTasks"][0].__setitem__("defaultMode", "cloud"), "defaultMode"),
            (lambda s: s["dispatchTasks"][0].__setitem__("defaultExecutor", "vim"),
             "defaultExecutor"),
            (lambda s: s["dispatchTasks"][0].__setitem__("planPath", "plans/x.md"), "planPath"),
            (lambda s: s["dispatchTasks"][0].__setitem__("selectable", "yes"), "selectable"),
        ]
        for mutate, needle in cases:
            snapshot = _base_snapshot()
            mutate(snapshot)
            with self.subTest(needle=needle):
                errors = cc.validate_snapshot(snapshot)
                self.assertTrue(any(needle in error for error in errors), errors)

    def test_delivery_prd_required_only_when_selectable(self) -> None:
        snapshot = _base_snapshot()
        task = snapshot["dispatchTasks"][0]
        task.update({"kind": "delivery", "difficulty": 3, "selectable": True, "prdPath": None})
        errors = cc.validate_snapshot(snapshot)
        self.assertTrue(any("dispatchTasks[0].prdPath" in error for error in errors), errors)

        # 信息不完整时用 selectable:false + disabledReason 表达，而不是让快照非法。
        snapshot = _base_snapshot()
        task = snapshot["dispatchTasks"][0]
        task.update({"kind": "delivery", "difficulty": 3, "selectable": False,
                     "prdPath": None, "disabledReason": "PRD 路径缺失：信息不完整，不可组队"})
        self.assertEqual(cc.validate_snapshot(snapshot), [])

    def test_delivery_difficulty_must_be_three(self) -> None:
        snapshot = _base_snapshot()
        task = snapshot["dispatchTasks"][0]
        task.update({"kind": "delivery", "difficulty": 2, "selectable": False,
                     "prdPath": None, "disabledReason": "信息不完整"})
        errors = cc.validate_snapshot(snapshot)
        self.assertTrue(any("dispatchTasks[0].difficulty" in error for error in errors), errors)

    def test_selectable_false_requires_reason(self) -> None:
        snapshot = _base_snapshot()
        snapshot["dispatchTasks"][0]["selectable"] = False
        snapshot["dispatchTasks"][0]["disabledReason"] = None
        errors = cc.validate_snapshot(snapshot)
        self.assertTrue(any("dispatchTasks[0].disabledReason" in error for error in errors), errors)

    def test_default_mode_can_be_null(self) -> None:
        snapshot = _base_snapshot()
        snapshot["dispatchTasks"][0]["defaultMode"] = None
        self.assertEqual(cc.validate_snapshot(snapshot), [])

    def test_run_rules(self) -> None:
        cases = [
            (lambda s: s["runs"][0].__setitem__("status", "failed"), "runs[0].status"),
            (lambda s: s["runs"][0].__setitem__("kind", "batch"), "runs[0].kind"),
            (lambda s: s["runs"][0]["stages"][0].__setitem__("state", "green"),
             "stages[0].state"),
            (lambda s: s["runs"][0]["agents"][0].__setitem__("executor", "vim"),
             "agents[0].executor"),
            (lambda s: s["runs"][0]["packages"][0]["firstReview"].__setitem__("verdict", "ok"),
             "firstReview.verdict"),
            (lambda s: s["runs"][0]["packages"][0].__setitem__("dependencies", [1]),
             "dependencies[0]"),
            (lambda s: s["runs"][0].__setitem__("detailPath", "runs/x.md"), "detailPath"),
            (lambda s: s["runs"][0].__setitem__("stale", "no"), "runs[0].stale"),
        ]
        for mutate, needle in cases:
            snapshot = _base_snapshot()
            mutate(snapshot)
            with self.subTest(needle=needle):
                errors = cc.validate_snapshot(snapshot)
                self.assertTrue(any(needle in error for error in errors), errors)

    def test_role_rules(self) -> None:
        cases = [
            (lambda s: s["roles"][0].__setitem__("id", "hacker"), "roles[0].id"),
            (lambda s: s["roles"][0].__setitem__("status", "probation"), "roles[0].status"),
            (lambda s: s["roles"][0].__setitem__("level", 5), "roles[0].level"),
            (lambda s: s["roles"][0].__setitem__("executors", ["vim"]), "executors[0]"),
            (lambda s: s["roles"][0]["completed"].__setitem__("value", -1),
             "completed.value"),
            (lambda s: s["roles"][0]["levelProgress"].__setitem__("span", 0),
             "levelProgress.span"),
            (lambda s: s["roles"][0].__setitem__("workspace", "cloud"), "workspace"),
        ]
        for mutate, needle in cases:
            snapshot = _base_snapshot()
            mutate(snapshot)
            with self.subTest(needle=needle):
                errors = cc.validate_snapshot(snapshot)
                self.assertTrue(any(needle in error for error in errors), errors)

    def test_duplicate_ids_detected(self) -> None:
        snapshot = _base_snapshot()
        snapshot["pending"].append(copy.deepcopy(snapshot["pending"][0]))
        errors = cc.validate_snapshot(snapshot)
        self.assertTrue(any("pending[1].id 重复" in error for error in errors), errors)

    def test_diagnostics_do_not_leak_values(self) -> None:
        snapshot = _base_snapshot()
        snapshot["pending"][0]["title"] = "LEAKME-TITLE"
        snapshot["pending"][0]["type"] = "LEAKME-TYPE"
        snapshot["pending"][0]["sources"][0]["path"] = "LEAKME-PATH"
        errors = cc.validate_snapshot(snapshot)
        joined = " ".join(errors)
        self.assertTrue(any("pending[0].type" in error for error in errors), errors)
        self.assertNotIn("LEAKME", joined)
        self.assertNotIn("traceback", joined.lower())

    def test_unknown_extra_keys_allowed(self) -> None:
        snapshot = _base_snapshot()
        snapshot["extra"] = {"forward": "compatible"}
        snapshot["pending"][0]["extraField"] = 1
        self.assertEqual(cc.validate_snapshot(snapshot), [])

    def test_nested_required_keys(self) -> None:
        snapshot = _base_snapshot()
        del snapshot["roles"][0]["firstPass"]["reason"]
        del snapshot["runs"][0]["agents"][0]["state"]
        errors = cc.validate_snapshot(snapshot)
        self.assertTrue(any("roles[0].firstPass.reason" in error for error in errors), errors)
        self.assertTrue(any("agents[0].state" in error for error in errors), errors)


class TestSharedFixtures(unittest.TestCase):
    """七场景共享夹具的持久自测；只读导入，不修改 tests/console_support.py。"""

    maxDiff = None

    def test_snapshot_validate_and_json_roundtrip(self) -> None:
        for scenario in console_support.SCENARIOS:
            with self.subTest(scenario=scenario):
                snapshot = console_support.snapshot_fixture(
                    scenario=scenario, now=console_support.DEFAULT_NOW)
                self.assertEqual(cc.validate_snapshot(snapshot), [])
                self.assertEqual(json.loads(cc.inline_json(snapshot)), snapshot)
                self.assertEqual(json.loads(json.dumps(snapshot, ensure_ascii=False)), snapshot)

    def test_make_fixture_matches_manifest_and_snapshot(self) -> None:
        for scenario in console_support.SCENARIOS:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(
                    ignore_cleanup_errors=True) as tmp:
                root = Path(tmp)
                manifest = console_support.make_fixture(
                    root, scenario=scenario, now=console_support.DEFAULT_NOW)
                snapshot = console_support.snapshot_fixture(
                    scenario=scenario, now=console_support.DEFAULT_NOW)
                self.assertEqual(json.loads(cc.inline_json(manifest)), manifest)
                self._assert_manifest(scenario, root, manifest, snapshot)

    def _assert_manifest(self, scenario: str, root: Path, manifest: dict,
                         snapshot: dict) -> None:
        expected = manifest["expected"]
        self.assertEqual(manifest["scenario"], scenario)
        self.assertEqual(snapshot["generatedAt"], manifest["now"])
        self.assertEqual(snapshot["environment"], manifest["environment"])
        self.assertEqual(Path(manifest["paths"]["root"]), root.resolve())

        listed = sorted(rel for rel in manifest["files"] if not rel.endswith("/"))
        self.assertEqual(listed, sorted(_tree_digest(root)))

        self.assertEqual([item["id"] for item in snapshot["pending"]],
                         expected["pending"]["ids"])
        self.assertEqual([task["id"] for task in snapshot["dispatchTasks"]],
                         expected["dispatch"]["ids"])
        # runId 的正式形态就是 runs/<slug>.md（相对路径），expected 与快照同形直接比对。
        self.assertEqual([run["id"] for run in snapshot["runs"]],
                         expected["runs"]["ids"])
        self.assertEqual([role["id"] for role in snapshot["roles"]],
                         expected["roles"]["ids"])

        if expected["pending"]["count"] is not None:
            self.assertEqual(
                sum(1 for item in snapshot["pending"] if item["status"] == "待处理"),
                expected["pending"]["count"])
        if expected["dispatch"]["count"] is not None:
            self.assertEqual(len(snapshot["dispatchTasks"]),
                             expected["dispatch"]["count"])
        if all(value is not None
               for value in expected["pending"]["columns"].values()):
            columns = {name: 0 for name in expected["pending"]["columns"]}
            for item in snapshot["pending"]:
                if item["status"] == "待处理" and item["column"] in columns:
                    columns[item["column"]] += 1
            self.assertEqual(columns, expected["pending"]["columns"])

        for key, value in expected["stats"].items():
            self.assertEqual(snapshot["stats"][key]["value"], value, key)

        statuses = expected["runs"].get("statuses", {})
        for run in snapshot["runs"]:
            if run["id"] in statuses:
                self.assertEqual(run["status"], statuses[run["id"]], run["id"])
        for run_id, stale in expected["runs"].get("stale", {}).items():
            run = next(item for item in snapshot["runs"] if item["id"] == run_id)
            self.assertEqual(run["stale"], stale, run_id)

        roles = expected["roles"]
        for role in snapshot["roles"]:
            role_id = role["id"]
            actuals = {
                "status": role["status"],
                "completed": role["completed"]["value"],
                "activeSessions": role["activeSessions"]["value"],
                "level": role["level"],
            }
            for field, actual in actuals.items():
                if field in roles and role_id in roles[field]:
                    self.assertEqual(actual, roles[field][role_id],
                                     f"{role_id}.{field}")

        defaults = expected["dispatch"].get("defaults", {})
        for task in snapshot["dispatchTasks"]:
            spec = defaults.get(task["taskNo"])
            if spec is None:
                continue
            self.assertEqual(task["defaultExecutor"], spec["defaultExecutor"],
                             task["taskNo"])
            self.assertEqual(task["defaultMode"], spec["defaultMode"], task["taskNo"])
        for task_no, selectable in expected["dispatch"].get("selectable", {}).items():
            task = next(item for item in snapshot["dispatchTasks"]
                        if item["taskNo"] == task_no)
            self.assertIs(task["selectable"], selectable, task_no)

    def test_hostile_sentinels_stay_out_of_inline_json(self) -> None:
        snapshot = console_support.snapshot_fixture(
            scenario="hostile", now=console_support.DEFAULT_NOW)
        payload = cc.inline_json(snapshot)
        plain = json.dumps(snapshot, ensure_ascii=False)
        for sentinel in console_support.HOSTILE_SENTINELS:
            with self.subTest(sentinel=sentinel):
                self.assertNotIn(sentinel, payload)
                self.assertNotIn(sentinel, plain)

    def test_non_empty_root_is_rejected_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            keep = root / "keep.txt"
            keep.write_text("keep", encoding="utf-8")
            before = _tree_digest(root)
            with self.assertRaises(ValueError):
                console_support.make_fixture(
                    root, scenario="empty", now=console_support.DEFAULT_NOW)
            self.assertEqual(_tree_digest(root), before)
            self.assertEqual(keep.read_text(encoding="utf-8"), "keep")

    def test_built_root_is_rejected_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            console_support.make_fixture(
                root, scenario="full-orca", now=console_support.DEFAULT_NOW)
            before = _tree_digest(root)
            with self.assertRaises(ValueError):
                console_support.make_fixture(
                    root, scenario="full-orca", now=console_support.DEFAULT_NOW)
            self.assertEqual(_tree_digest(root), before)

    def test_contract_checks_leave_inputs_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            console_support.make_fixture(
                root, scenario="full-orca", now=console_support.DEFAULT_NOW)
            before = _tree_digest(root)
            snapshot = console_support.snapshot_fixture(
                scenario="full-orca", now=console_support.DEFAULT_NOW)
            self.assertEqual(cc.validate_snapshot(snapshot), [])
            cc.inline_json(snapshot)
            for item in snapshot["pending"]:
                for field in ("project", "item", "title", "summary", "sender"):
                    cc.safe_text(item.get(field))
                experience = item.get("experience") or {}
                url = experience.get("url")
                if isinstance(url, str):
                    cc.safe_experience_url(url, root=root)
            for run in snapshot["runs"]:
                for field in ("project", "item"):
                    cc.safe_text(run.get(field))
            for role in snapshot["roles"]:
                for field in ("name", "useWhen"):
                    cc.safe_text(role.get(field))
            self.assertEqual(_tree_digest(root), before)

    def test_timezone_aware_now_is_required(self) -> None:
        naive = datetime(2026, 10, 20, 10, 0)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                console_support.snapshot_fixture(scenario="empty", now=naive)
            with self.assertRaises(ValueError):
                console_support.make_fixture(root, scenario="empty", now=naive)
            self.assertEqual(_tree_digest(root), {})
        with self.assertRaises(ValueError):
            console_support.snapshot_fixture(
                scenario="empty", now="2026-10-20T10:00:00+08:00")

    def test_level_table_matches_plan_bands(self) -> None:
        covered = set()
        for case in console_support.LEVEL_CASES:
            completed = case["completed"]
            covered.add(completed)
            level, progress = _level_for(completed)
            self.assertEqual(case["level"], level, completed)
            self.assertEqual(case["progress"], progress, completed)
        self.assertTrue({0, 2, 3, 9, 10, 29, 30, 31} <= covered)

    def test_boundaries_levels_match_table(self) -> None:
        snapshot = console_support.snapshot_fixture(
            scenario="boundaries", now=console_support.DEFAULT_NOW)
        for role in snapshot["roles"]:
            completed = role["completed"]["value"]
            if completed is None:
                self.assertIsNone(role["level"], role["id"])
                self.assertIsNone(role["levelProgress"], role["id"])
                continue
            level, progress = _level_for(completed)
            self.assertEqual(role["level"], level, role["id"])
            self.assertEqual(role["levelProgress"], progress, role["id"])

    def test_stale_table_uses_strict_threshold(self) -> None:
        self.assertEqual(
            {case["name"]: case["stale"] for case in console_support.STALE_CASES},
            {"29:59": False, "30:00": False, "30:01": True})
        for case in console_support.STALE_CASES:
            self.assertEqual(case["stale"], case["elapsedSeconds"] > 30 * 60,
                             case["name"])
        snapshot = console_support.snapshot_fixture(
            scenario="boundaries", now=console_support.DEFAULT_NOW)
        runs = {run["id"]: run for run in snapshot["runs"]}
        self.assertFalse(runs["runs/2026-10-20-boundary-r11.md"]["stale"])
        self.assertFalse(runs["runs/2026-10-20-boundary-r12.md"]["stale"])
        self.assertTrue(runs["runs/2026-10-20-boundary-r13.md"]["stale"])

    def test_day_table_covers_cross_year(self) -> None:
        cases = {case["name"]: case for case in console_support.DAY_CASES}
        cross_year = [
            case for case in console_support.DAY_CASES
            if (case["completedAt"] or case["createdAt"])[:4] != case["now"][:4]
        ]
        self.assertTrue(cross_year)
        self.assertTrue(cases["cross-year-0130"]["inWeek"])
        self.assertFalse(cases["cross-year-previous-week"]["inWeek"])
        for case in console_support.DAY_CASES:
            timestamp = case["completedAt"] or case["createdAt"]
            self.assertEqual(case["inToday"], case["now"][:10] == timestamp[:10],
                             case["name"])

    def test_streak_table_keeps_unprovable_as_null(self) -> None:
        cases = {case["name"]: case for case in console_support.STREAK_CASES}
        self.assertEqual(cases["two-cleared-days"]["expected"], 2)
        self.assertEqual(cases["today-not-counted"]["expected"], 2)
        self.assertIsNone(cases["unknown-coverage-breaks-streak"]["expected"])
        self.assertIsNone(cases["no-history"]["expected"])
        self.assertIsNone(cases["yesterday-not-provable"]["expected"])


class TestSharedTextCases(unittest.TestCase):
    """共享 TEXT_CASES 是 P0 必需产物：直接导入，缺失即失败，不静默 skip。"""

    #: R1 的明确凭据反例（第 1 轮三条 + 第 2 轮中文前缀四条）；预期值只从共享表读取。
    R1_COUNTEREXAMPLES = (
        "token：FICTIONAL_ONLY",
        "密码：FICTIONAL_ONLY",
        "id_token=FICTIONAL_ONLY",
        "新密码：FICTIONAL_ONLY",
        "临时令牌=FICTIONAL_ONLY",
        "接口密钥:FICTIONAL_ONLY",
        "旧口令：FICTIONAL_ONLY",
    )

    #: 无赋值叙述必须原样保留（人工写定预期，不由实现推导）。
    R1_NARRATIVES = {
        "密码已改好": "密码已改好",
        "新密码字段不会展示": "新密码字段不会展示",
        "临时令牌是字段名": "临时令牌是字段名",
        "接口密钥配置说明": "接口密钥配置说明",
        "旧口令已失效": "旧口令已失效",
    }

    def test_shared_text_cases(self) -> None:
        cases = console_support.TEXT_CASES
        self.assertIsInstance(cases, list)
        self.assertTrue(cases)
        for case in cases:
            self.assertTrue({"name", "input", "expected"} <= set(case))
            with self.subTest(name=case["name"]):
                self.assertEqual(cc.safe_text(case["input"]), case["expected"])

    def test_credential_counterexamples_follow_shared_expectations(self) -> None:
        cases = console_support.TEXT_CASES
        for text in self.R1_COUNTEREXAMPLES:
            with self.subTest(text=text):
                matched = [case for case in cases if case["input"] == text]
                self.assertEqual(len(matched), 1, "共享表缺少唯一的 R1 凭据反例")
                expected = matched[0]["expected"]
                self.assertIsNone(expected)
                self.assertEqual(cc.safe_text(text), expected)

    def test_shared_sentinels_never_reach_inline_json(self) -> None:
        cases = console_support.TEXT_CASES
        sentinel_cases = [case for case in cases if "FICTIONAL_ONLY" in str(case["input"])]
        self.assertGreaterEqual(len(sentinel_cases), 34)
        for case in sentinel_cases:
            with self.subTest(name=case["name"]):
                self.assertIsNone(case["expected"])
                projected = cc.safe_text(case["input"])
                self.assertIsNone(projected)
                # 清洗投影后内嵌数据不得保留虚构敏感值。
                embedded = cc.inline_json({
                    "pending": [{"summary": projected, "title": projected,
                                 "processedNote": projected}],
                    "runs": [{"progress": [projected]}],
                })
                self.assertNotIn("FICTIONAL_ONLY", embedded)

    def test_chinese_prefix_narratives_kept(self) -> None:
        cases = console_support.TEXT_CASES
        for text, expected in self.R1_NARRATIVES.items():
            with self.subTest(text=text):
                matched = [case for case in cases if case["input"] == text]
                self.assertEqual(len(matched), 1, "共享表缺少叙述对照")
                self.assertEqual(matched[0]["expected"], expected)
                self.assertEqual(cc.safe_text(text), expected)


# ---------------------------------------------------------------- v1.1 回复契约

def _reply_snapshot(scenario: str = "full-orca") -> dict:
    return console_support.snapshot_fixture(scenario=scenario, replies=True)


def _slot_of(snapshot: dict, card: str) -> dict:
    for item in snapshot["pending"] + snapshot["runs"]:
        slot = item.get("reply")
        if slot and slot["card"] == card:
            return slot
    raise KeyError(card)


class TestReplyConstants(unittest.TestCase):
    def test_limits_ports_and_columns(self) -> None:
        self.assertEqual(cc.reply_limits(), {"maxChars": 500, "perMinute": 10, "dedupeSeconds": 10,
                                             "pollSeconds": 20, "maxLines": 20})
        self.assertEqual(cc.REPLY_MAX_BODY_BYTES, 16384)
        self.assertEqual(cc.REPLY_MAX_ITEMS, 200)
        self.assertEqual(cc.CHANNEL_PORTS, tuple(range(17318, 17328)))
        self.assertEqual(len(cc.CHANNEL_PORTS), 10)
        self.assertEqual(cc.REPLY_COLUMNS, ("编号", "时间", "设备", "页", "事项", "关联", "快捷评价",
                                            "正文", "送达", "转给", "状态", "回执"))
        self.assertEqual(cc.REPLY_STATUSES[-1], "unknown")
        self.assertEqual(set(cc.REPLY_STATUS_LABELS), set(cc.REPLY_STATUSES) - {"unknown"})
        self.assertEqual(set(cc.REPLY_NEED_TEXT), {"other", "reject", "add"})
        self.assertEqual(cc.REPLY_PAGE_ONLY, ("later",))
        for code, status in cc.REPLY_ERROR_CODES.items():
            self.assertIn(status, (400, 401, 403, 404, 405, 413, 415, 422, 429, 500, 503), code)  # 503 = busy（P0-P5-r1 R3）

    def test_status_labels_have_no_prefix_overlap(self) -> None:
        labels = list(cc.REPLY_STATUS_LABELS.values())
        for a in labels:
            for b in labels:
                if a != b:
                    self.assertFalse(b.startswith(a), (a, b))

    def test_shared_with_core_js(self) -> None:
        """Python 与 core.js 的回复常量必须一致（两份手写，靠这个测试对齐）。"""
        script = (
            "const NC = require(process.argv[1]);"
            "process.stdout.write(JSON.stringify({limits: NC.REPLY_LIMITS, choices: NC.REPLY_CHOICES,"
            " need: NC.REPLY_NEED_TEXT, pageOnly: NC.REPLY_PAGE_ONLY, statuses: NC.REPLY_STATUSES,"
            " schema: NC.SCHEMA_VERSION}));"
        )
        try:
            out = subprocess.run(["node", "-e", script, str(REPO_ROOT / "tools" / "console_assets" / "core.js")],
                                 capture_output=True, text=True, encoding="utf-8", timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            self.skipTest(f"没有 node，跨语言常量比对未覆盖：{exc}")
        self.assertEqual(out.returncode, 0, out.stderr)
        js = json.loads(out.stdout)
        self.assertEqual(js["schema"], cc.SCHEMA_VERSION)
        self.assertEqual(js["limits"], cc.reply_limits())
        self.assertEqual(js["choices"]["blocked"], cc.BLOCKED_REPLY_CHOICES)
        self.assertEqual(js["choices"]["accept"], cc.ACCEPT_REPLY_CHOICES)
        self.assertEqual(js["choices"]["run"], cc.RUN_REPLY_CHOICES)
        self.assertEqual(js["need"], list(cc.REPLY_NEED_TEXT))
        self.assertEqual(js["pageOnly"], list(cc.REPLY_PAGE_ONLY))
        statuses = dict(js["statuses"])
        statuses.pop("unknown")
        self.assertEqual(statuses, cc.REPLY_STATUS_LABELS)


class TestCardKey(unittest.TestCase):
    def test_round_trip(self) -> None:
        self.assertEqual(cc.card_key("pending", "N1021-07"), "pending:N1021-07")
        self.assertEqual(cc.card_key("runs", "runs/2026-10-20-a.md"), "run:runs/2026-10-20-a.md")
        self.assertEqual(cc.card_key("dispatch"), "dispatch")
        self.assertEqual(cc.card_key("roles"), "roles")
        for key, parsed in (("pending:N1021-07", ("pending", "N1021-07")),
                            ("run:runs/2026-10-20-a.md", ("runs", "runs/2026-10-20-a.md")),
                            ("dispatch", ("dispatch", None)), ("roles", ("roles", None))):
            self.assertEqual(cc.parse_card_key(key), parsed)
        with self.assertRaises(ValueError):
            cc.card_key("other", "x")

    def test_rejects_bad_keys(self) -> None:
        for key in (None, 1, "", "pending:", "pending:1021-07", "pending:N1021", "pending:N1021-07 ",
                    "run:", "run:a b", "run:runs/../x.md", "run:a" + chr(10), "roles:x", "Dispatch",
                    "run:" + "a" * 400):
            with self.subTest(key=key):
                self.assertIsNone(cc.parse_card_key(key))


class TestReplyStatusOf(unittest.TestCase):
    def test_labels(self) -> None:
        for status, label in cc.REPLY_STATUS_LABELS.items():
            self.assertEqual(cc.reply_status_of(label), (status, None))
        self.assertEqual(cc.reply_status_of("转不到：接收方会话不在了"), ("lost", "接收方会话不在了"))
        self.assertEqual(cc.reply_status_of("  已转给跟进 10:02  "), ("forwarded", "10:02"))
        self.assertEqual(cc.reply_status_of(""), ("unknown", None))
        self.assertEqual(cc.reply_status_of(None), ("unknown", None))
        self.assertEqual(cc.reply_status_of("待处理"), ("unknown", None))


class TestValidateReplyInput(unittest.TestCase):
    def setUp(self) -> None:
        self.snap = _reply_snapshot()
        self.accept = _slot_of(self.snap, "pending:N1020-03")
        self.blocked = _slot_of(self.snap, "pending:N1020-02")
        self.decision = _slot_of(self.snap, "pending:N1020-01")
        self.run = _slot_of(self.snap, "run:runs/2026-10-20-example-alpha.md")
        self.other = _slot_of(self.snap, "pending:N1020-04")

    def test_accept_card(self) -> None:
        v = cc.validate_reply_input
        self.assertIsNone(v(self.accept, "accept", ""))
        self.assertIsNone(v(self.accept, "accept", None))
        self.assertEqual(v(self.accept, "reject", ""), "need_text")
        self.assertEqual(v(self.accept, "reject", "   "), "need_text")
        self.assertIsNone(v(self.accept, "reject", "深色模式下按钮看不清"))
        self.assertEqual(v(self.accept, "later", ""), "bad_choice")   # 稍后只在页面，通道不收
        self.assertEqual(v(self.accept, None, ""), "empty")
        self.assertIsNone(v(self.accept, None, "只写一句也行"))
        self.assertEqual(v(self.accept, "B", "x"), "bad_choice")

    def test_other_cards(self) -> None:
        v = cc.validate_reply_input
        self.assertIsNone(v(self.decision, "B", ""))
        self.assertEqual(v(self.decision, "follow", ""), "bad_choice")
        self.assertIsNone(v(self.blocked, "follow", ""))
        self.assertEqual(v(self.blocked, "other", ""), "need_text")
        self.assertIsNone(v(self.run, "ask", ""))
        self.assertEqual(v(self.run, "add", ""), "need_text")
        self.assertIsNone(v(self.run, "pause", ""))

    def test_length_boundary_counts_code_points(self) -> None:
        v = cc.validate_reply_input
        self.assertIsNone(v(self.run, "add", "甲" * 500))
        self.assertEqual(v(self.run, "add", "甲" * 501), "too_long")
        self.assertIsNone(v(self.run, "add", "  " + "😀" * 500 + "  "))
        self.assertEqual(v(self.run, "add", "😀" * 501), "too_long")

    def test_device_card_and_type_errors(self) -> None:
        v = cc.validate_reply_input
        self.assertEqual(v(self.other, "accept", ""), "other_device")
        closed = dict(self.accept, allowed=False, reason=None)
        self.assertEqual(v(closed, "accept", ""), "not_replyable")
        self.assertEqual(v(None, "accept", ""), "bad_card")
        self.assertEqual(v("slot", "accept", ""), "bad_card")
        self.assertEqual(v(self.accept, "accept", 12), "bad_json")
        self.assertEqual(v(self.accept, 3, ""), "bad_choice")

    def test_sensitive_text(self) -> None:
        v = cc.validate_reply_input
        self.assertEqual(v(self.run, "add", "token=abc123"), "sensitive")
        self.assertEqual(v(self.run, "add", "看 private/health.md"), "sensitive")
        self.assertIsNone(v(self.run, "add", "改完跑一下 node 测试"))


class TestCheckConsoleCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.snap = _reply_snapshot()
        tasks = self.snap["dispatchTasks"]
        self.single = next(t for t in tasks if t["kind"] == "single" and t["selectable"])
        self.team = next(t for t in tasks if t["kind"] == "delivery" and t["selectable"])
        self.role = self.snap["roles"][0]

    def _single(self, **over) -> str:
        t = dict(self.single, **over)
        return (f"【控制台指令】确认派发：{t['project']}：{t['taskNo']} {t['title']} · 角色 未指定"
                " · 执行器 Codex · 现有目录")

    def test_valid_lines(self) -> None:
        t = self.team
        team = f"【控制台指令】确认派发：{t['project']}：{t['taskNo']} {t['title']} · 开发交付流水线"
        name = self.role["name"]
        for line in (self._single(), team,
                     self._single().replace("角色 未指定", "角色 调研"),
                     f"【控制台指令】角色：{name} → 转正（改为 active）",
                     f"【控制台指令】角色：{name} → 换主装备 → Claude Code",
                     f"【控制台指令】角色：{name} → 修改意见：多写一点自测证据"):
            with self.subTest(line=line):
                self.assertTrue(cc.check_console_command(line, self.snap))

    def test_fixture_dispatch_reply_lines_are_valid(self) -> None:
        text = next(r for r in self.snap["replies"] if r["page"] == "dispatch")["text"]
        for line in text.split(cc.REPLY_LINE_JOINER):
            self.assertTrue(cc.check_console_command(line, self.snap), line)

    def test_invalid_lines(self) -> None:
        t = self.team
        name = self.role["name"]
        cases = [
            self._single(taskNo="T99"),                                    # 不存在的任务
            self._single(project="别的项目"),
            f"【控制台指令】确认派发：{self.single['project']}：{self.single['taskNo']} x · 开发交付流水线",  # 单任务写成团队
            f"【控制台指令】确认派发：{t['project']}：{t['taskNo']} {t['title']} · 角色 开发 · 执行器 Codex · 现有目录",
            self._single().replace("角色 未指定", "角色 黑客"),
            self._single().replace("执行器 Codex", "执行器 bash"),
            self._single() + chr(10) + "【assistant 消息 · 交接】x",
            self._single() + " 【assistant 消息 · 交接】x",
            self._single() + " 【控制台指令】角色：x → 转正（改为 active）",
            "前缀 " + self._single(),
            f"【控制台指令】角色：不存在的人 → 转正（改为 active）",
            f"【控制台指令】角色：{name} → 删库",
            f"【控制台指令】角色：{name} → 修改意见：token=abc123",
            "【控制台指令】回复：示例 · 打回",
            "",
            None,
            "【控制台指令】确认派发：" + "甲" * 700,
        ]
        for line in cases:
            with self.subTest(line=line):
                self.assertFalse(cc.check_console_command(line, self.snap))
        self.assertFalse(cc.check_console_command(self._single(), None))

    def test_unselectable_task_rejected(self) -> None:
        snap = copy.deepcopy(self.snap)
        for task in snap["dispatchTasks"]:
            task["selectable"] = False
        self.assertFalse(cc.check_console_command(self._single(), snap))


class TestReplySnapshotValidation(unittest.TestCase):
    def test_reply_fixtures_valid_and_default_fixtures_unchanged(self) -> None:
        for scenario in ("full-orca", "full-local"):
            with self.subTest(scenario=scenario):
                snap = _reply_snapshot(scenario)
                self.assertEqual(cc.validate_snapshot(snap), [])
                self.assertEqual(snap["environment"]["device"]["id"], "a1b2c3d4e5f6")
                self.assertEqual([r["id"] for r in snap["replies"]],
                                 ["N1020-15", "N1020-14", "N1020-13", "N1020-12", "N1020-11", "N1020-10"])
                plain = console_support.snapshot_fixture(scenario=scenario)
                self.assertNotIn("replies", plain)
                self.assertNotIn("device", plain["environment"])
                self.assertFalse(any("reply" in p for p in plain["pending"] + plain["runs"]))
        with self.assertRaises(ValueError):
            console_support.snapshot_fixture(scenario="empty", replies=True)

    def test_slots_follow_rules(self) -> None:
        snap = _reply_snapshot()
        self.assertIsNone(next(p for p in snap["pending"] if p["id"] == "N1020-08")["reply"])
        self.assertIsNone(next(r for r in snap["runs"] if r["status"] == "done")["reply"])
        other = _slot_of(snap, "pending:N1020-04")
        self.assertEqual((other["allowed"], other["reason"], other["device"]["local"]),
                         (False, "other_device", False))
        legacy = _slot_of(snap, "pending:N1020-05")
        self.assertEqual((legacy["allowed"], legacy["kind"], legacy["device"]),
                         (True, "main", {"id": None, "name": None, "local": None}))
        self.assertEqual(_slot_of(snap, "run:runs/2026-10-20-example-kappa.md")["receiver"],
                         cc.REPLY_RECEIVER_EXECUTOR)
        # 快照不投影 handle
        self.assertNotIn("demo-follow", json.dumps(snap["replies"], ensure_ascii=False))

    def test_mutations_reported(self) -> None:
        def mutated(fn):
            snap = _reply_snapshot()
            fn(snap)
            return cc.validate_snapshot(snap)

        def slot(snap, card):
            return _slot_of(snap, card)

        cases = {
            "pending.reply 卡片键页不对": (lambda s: slot(s, "pending:N1020-03").update(card="run:x"), ".reply.card"),
            "needText 不在选项里": (lambda s: slot(s, "pending:N1020-03").update(needText=["zzz"]), ".needText"),
            "原因不合法": (lambda s: slot(s, "pending:N1020-03").update(reason="nope"), ".reason"),
            "kind 不合法": (lambda s: slot(s, "pending:N1020-03").update(kind="push"), ".kind"),
            "设备未记录却有 local": (lambda s: slot(s, "pending:N1020-05")["device"].update(local=True), ".local"),
            "reply 不是对象": (lambda s: s["pending"][0].update(reply=3), ".reply"),
            "回复编号": (lambda s: s["replies"][0].update(id="R1"), "replies[0].id"),
            "回复状态": (lambda s: s["replies"][0].update(status="已送达"), "replies[0].status"),
            "回复页": (lambda s: s["replies"][0].update(page="待处理"), "replies[0].page"),
            "回执时间": (lambda s: s["replies"][3]["ack"].update(at="09:52"), "replies[3].ack.at"),
            "回复重复编号": (lambda s: s["replies"].append(copy.deepcopy(s["replies"][0])), "重复"),
            "回复太多": (lambda s: s.update(replies=[dict(s["replies"][0], id=f"N1020-{i + 100}")
                                                  for i in range(201)]), "不能超过"),
            "本机设备指纹": (lambda s: s["environment"]["device"].update(id="DEVICE-1"), "environment.device.id"),
            "replies 不是数组": (lambda s: s.update(replies={}), "snapshot.replies"),
        }
        for name, (fn, needle) in cases.items():
            with self.subTest(case=name):
                errors = mutated(fn)
                self.assertTrue(any(needle in e for e in errors), errors)

    def test_missing_new_keys_are_allowed(self) -> None:
        snap = _base_snapshot()
        self.assertEqual(cc.validate_snapshot(snap), [])
        snap["environment"]["device"] = None
        snap["pending"][0]["reply"] = None
        snap["replies"] = []
        self.assertEqual(cc.validate_snapshot(snap), [])


class TestReplySourceShapes(TempRootCase):
    def test_replies_and_device_kinds(self) -> None:
        self._write("runs/replies.md", "| 编号 |\n")
        self._write("registry/devices/a1b2c3d4e5f6.yaml", "hostname: 示例\n")
        self._write("registry/devices/BAD.yaml", "hostname: x\n")
        self.assertIsNotNone(cc.checked_source(self.root, "runs/replies.md", "replies"))
        self.assertIsNone(cc.checked_source(self.root, "runs/replies.md", "run"))
        self.assertIsNone(cc.checked_source(self.root, "runs/pending.md", "replies"))
        self.assertIsNotNone(cc.checked_source(self.root, "registry/devices/a1b2c3d4e5f6.yaml", "device"))
        for rel in ("registry/devices/BAD.yaml", "registry/devices/../devices/a1b2c3d4e5f6.yaml",
                    "registry/a1b2c3d4e5f6.yaml", "registry/devices/a1b2c3d4e5f6.yml"):
            with self.subTest(rel=rel):
                self.assertIsNone(cc.checked_source(self.root, rel, "device"))


if __name__ == "__main__":
    unittest.main()
