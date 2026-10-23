"""P2 渲染器测试：占位符单次替换、JSON 边界、SVG 审计、单文件与规模。

全部使用纯虚构快照与**测试替身资产**（临时目录里复制真实 core.js、自写最小
shell.html/base.css/shell.js），不写 ``tools/console_assets``，不碰真实记录。
designer 的真实壳层资产合入后，``RealAssetTests`` 会在其上再跑一遍结构检查；
资产尚未落地时明确 skip 并说明缺口，不把缺失当通过。

运行：``python -B -m unittest tests.test_console_render -v``
"""
from __future__ import annotations

import os as _os, sys as _sys  # noqa: E402
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _isolation  # noqa: E402,F401  外发总开关与凭据隔离

import copy
import hashlib
import json
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import console_contract as cc  # noqa: E402
import console_render as cr  # noqa: E402
import console_support  # noqa: E402

ROLE_IDS = cc.ROLE_IDS

BASE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 5 5" '
    'fill="none" shape-rendering="crispEdges">'
    '<mask id="viewboxMask"><rect x="0" y="0" width="5" height="5" rx="0" ry="0" '
    'fill="#fff"/></mask>'
    '<g mask="url(#viewboxMask)" fill="#445566"><path d="M0 0h1v1H0z" fill="#112233"/>'
    "</g></svg>"
)

DEFAULT_SHELL = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>assistant 指挥台</title>
<!-- shell-template-sentinel -->
<style>/* ui-tokens */@@TOKENS@@</style>
<style>/* base */@@BASE_CSS@@</style>
<style>/* pages */@@PAGE_CSS@@</style>
</head>
<body>
<div id="app"></div>
@@SNAPSHOT@@
@@AVATARS@@
@@SCRIPTS@@
</body>
</html>
"""


def make_standin_assets(
    directory: Path,
    *,
    shell: str = DEFAULT_SHELL,
    with_actions: bool = True,
    with_overview: bool = True,
    avatars: dict | None = None,
) -> Path:
    """在临时目录搭一套**测试替身**资产：只复制真实 core.js，其余最小自写。"""
    root = Path(directory) / "tools" / "console_assets"
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO_ROOT / "tools" / "console_assets" / "core.js", root / "core.js")
    (root / "shell.html").write_text(shell, encoding="utf-8", newline="\n")
    (root / "shell.js").write_text(
        "/* shell-js-sentinel */\n(function(){window.__shellLoaded=true;})();\n",
        encoding="utf-8", newline="\n")
    (root / "base.css").write_text(
        "/* base-css-sentinel */\n.card{font-size:var(--fs-14);}\n",
        encoding="utf-8", newline="\n")
    if with_actions:
        (root / "actions.js").write_text("/* actions-js-sentinel */\n", encoding="utf-8")
        (root / "actions.css").write_text("/* actions-css-sentinel */\n", encoding="utf-8")
    if with_overview:
        (root / "overview.js").write_text("/* overview-js-sentinel */\n", encoding="utf-8")
        (root / "overview.css").write_text("/* overview-css-sentinel */\n", encoding="utf-8")
    payload = avatars if avatars is not None else {role: BASE_SVG for role in ROLE_IDS}
    (root / "avatars.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return root


def snapshot(scenario: str = "full-orca") -> dict:
    return console_support.snapshot_fixture(scenario=scenario)


def _digest(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _json_node(html: str, node_id: str) -> str:
    pattern = re.compile(
        r'<script type="application/json" id="' + re.escape(node_id) + r'">(.*?)</script>',
        re.DOTALL,
    )
    matches = pattern.findall(html)
    if len(matches) != 1:
        raise AssertionError(f"{node_id} 节点数量不是 1：{len(matches)}")
    return matches[0]


class StandinBase(unittest.TestCase):
    """每个用例一套独立临时资产目录，互不污染。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="assistant-p2-render-")
        self.tmp = Path(self._tmp.name)
        self.asset_root = make_standin_assets(self.tmp / "assets")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def render(self, snap: dict | None = None, **kwargs) -> str:
        return cr.render_page(snap or snapshot(), asset_root=self.asset_root, **kwargs)


class PlaceholderTests(StandinBase):
    def test_renders_every_placeholder(self) -> None:
        html = self.render()
        self.assertIn("--fs-28", html)          # tools/ui-tokens.css 原文
        self.assertIn("base-css-sentinel", html)
        self.assertIn("actions-css-sentinel", html)
        self.assertIn("overview-css-sentinel", html)
        self.assertIn("shell-template-sentinel", html)
        self.assertNotRegex(html, r"@@(?:TOKENS|BASE_CSS|PAGE_CSS|SNAPSHOT|AVATARS|SCRIPTS)@@")

    def test_script_order_is_core_actions_overview_shell(self) -> None:
        html = self.render()
        markers = ["var PAGE_IDS", "actions-js-sentinel", "overview-js-sentinel",
                   "shell-js-sentinel"]
        positions = [html.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions), "脚本次序必须是 core→actions→overview→shell")
        self.assertGreater(positions[3], positions[0])

    def test_optional_pages_absent_do_not_break_render(self) -> None:
        asset_root = make_standin_assets(
            self.tmp / "assets-no-pages", with_actions=False, with_overview=False)
        html = cr.render_page(snapshot(), asset_root=asset_root)
        self.assertNotIn("actions-js-sentinel", html)
        self.assertNotIn("overview-js-sentinel", html)
        self.assertIn("shell-js-sentinel", html)
        self.assertIn("console-snapshot", html)

    def test_single_pass_replacement_never_touches_dynamic_text(self) -> None:
        snap = snapshot()
        marker = "@@TOKENS@@ @@SCRIPTS@@ @@SNAPSHOT@@ @@AVATARS@@"
        snap["pending"][0]["title"] = marker
        html = self.render(snap)
        tokens_source = cr.TOKENS_PATH.read_text(encoding="utf-8")
        self.assertEqual(html.count(tokens_source), 1, "tokens 只应内嵌一次")
        self.assertIn(marker, html, "用户文本里的占位符必须原样保留，不能被二次替换")
        self.assertEqual(html.count("@@TOKENS@@"), 1)

    def test_template_contract_errors(self) -> None:
        cases = {
            "缺少占位符": DEFAULT_SHELL.replace("@@SCRIPTS@@", ""),
            "出现 2 次": DEFAULT_SHELL.replace("@@SCRIPTS@@", "@@SCRIPTS@@@@SCRIPTS@@"),
            "未约定占位符": DEFAULT_SHELL.replace("@@SCRIPTS@@", "@@SCRIPTS@@@@NOPE@@"),
        }
        for expected, shell in cases.items():
            with self.subTest(case=expected):
                asset_root = make_standin_assets(self.tmp / f"tpl-{abs(hash(expected))}", shell=shell)
                with self.assertRaises(cr.RenderError) as ctx:
                    cr.render_page(snapshot(), asset_root=asset_root)
                self.assertIn(expected, str(ctx.exception))

    def test_missing_assets_fail_with_name(self) -> None:
        for name in cr.REQUIRED_ASSETS:
            with self.subTest(asset=name):
                asset_root = make_standin_assets(self.tmp / f"miss-{name.replace('.', '-')}")
                (asset_root / name).unlink()
                with self.assertRaises(cr.RenderError) as ctx:
                    cr.render_page(snapshot(), asset_root=asset_root)
                self.assertIn(name, str(ctx.exception))

    def test_missing_asset_directory(self) -> None:
        with self.assertRaises(cr.RenderError):
            cr.render_page(snapshot(), asset_root=self.tmp / "not-there")

    def test_external_resource_guards(self) -> None:
        bad_shells = {
            "link": DEFAULT_SHELL.replace("</head>", '<link rel="stylesheet" href="x.css"></head>'),
            "script-src": DEFAULT_SHELL.replace("</body>", '<script src="x.js"></script></body>'),
            "iframe": DEFAULT_SHELL.replace("</body>", "<iframe></iframe></body>"),
        }
        for name, shell in bad_shells.items():
            with self.subTest(case=name):
                asset_root = make_standin_assets(self.tmp / f"ext-{name}", shell=shell)
                with self.assertRaises(cr.RenderError):
                    cr.render_page(snapshot(), asset_root=asset_root)

    def test_css_and_js_guard(self) -> None:
        asset_root = make_standin_assets(self.tmp / "css-url", with_actions=False, with_overview=False)
        (asset_root / "base.css").write_text('body{background:url("https://x/y.png")}',
                                             encoding="utf-8")
        with self.assertRaises(cr.RenderError):
            cr.render_page(snapshot(), asset_root=asset_root)

        asset_root = make_standin_assets(self.tmp / "js-break", with_actions=False, with_overview=False)
        (asset_root / "shell.js").write_text('window.x="</script>";', encoding="utf-8")
        with self.assertRaises(cr.RenderError):
            cr.render_page(snapshot(), asset_root=asset_root)


class JsonBoundaryTests(StandinBase):
    def test_data_nodes_are_unique_and_json(self) -> None:
        snap = snapshot()
        html = self.render(snap)
        self.assertEqual(html.count('id="console-snapshot"'), 1)
        self.assertEqual(html.count('id="console-avatars"'), 1)
        self.assertEqual(html.count('<script type="application/json"'), 2)
        self.assertEqual(json.loads(_json_node(html, "console-snapshot")), snap)
        avatars = json.loads(_json_node(html, "console-avatars"))
        self.assertEqual(sorted(avatars), sorted(ROLE_IDS))
        self.assertTrue(all(isinstance(value, str) for value in avatars.values()))

    def test_json_breakout_cannot_close_data_node(self) -> None:
        snap = snapshot()
        payload = '</script><script>alert(1)</script>\u2028\u2029<!--'
        snap["pending"][0]["summary"] = payload
        html = self.render(snap)
        node = _json_node(html, "console-snapshot")
        self.assertNotIn("</script", node.lower())
        self.assertNotIn("<script", node.lower())
        self.assertIn("\\u003c/script\\u003e", node)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("\u2028", html)
        self.assertNotIn("\u2029", html)
        self.assertEqual(json.loads(node)["pending"][0]["summary"], payload)

    def test_avatar_node_is_object_of_six_svgs(self) -> None:
        html = self.render()
        node = _json_node(html, "console-avatars")
        self.assertNotIn("</svg><script", node)
        for role in ROLE_IDS:
            self.assertIn(role, node)


class AvatarAuditTests(unittest.TestCase):
    def _avatars(self, svg: str | None = None, **overrides) -> dict:
        payload = {role: BASE_SVG for role in ROLE_IDS}
        if svg is not None:
            payload["dev"] = svg
        payload.update(overrides)
        return payload

    def test_accepts_static_shapes(self) -> None:
        audited = cr.audit_avatars(self._avatars())
        self.assertEqual(sorted(audited), sorted(ROLE_IDS))

    def test_rejects_unsafe_svg(self) -> None:
        cases = {
            "script": '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            "event-attr": '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
            "event-child": '<svg xmlns="http://www.w3.org/2000/svg"><rect onclick="x()"/></svg>',
            "foreign-object": '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject/></svg>',
            "image": '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://x/y.png"/></svg>',
            "use-xlink": '<svg xmlns="http://www.w3.org/2000/svg"><use xlink:href="#x"/></svg>',
            "style-attr": '<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:red"/></svg>',
            "css-url": '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="url(#x)"/></svg>',
            "mask-external": ('<svg xmlns="http://www.w3.org/2000/svg">'
                              '<mask id="m"><rect/></mask>'
                              '<g mask="url(https://x/y)"/></svg>'),
            "mask-missing-id": ('<svg xmlns="http://www.w3.org/2000/svg">'
                                '<mask id="m"><rect/></mask>'
                                '<g mask="url(#nope)"/></svg>'),
            "mask-ambiguous": ('<svg xmlns="http://www.w3.org/2000/svg">'
                               '<mask id="a"><rect/></mask>'
                               '<mask id="b"><rect/></mask>'
                               '<g mask="url(#a)"/></svg>'),
            "js-url": '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="javascript:alert(1)"/></svg>',
            "doctype": '<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg"/>',
            "entity": '<!ENTITY x "y"><svg xmlns="http://www.w3.org/2000/svg"/>',
            "not-svg-root": '<div xmlns="http://www.w3.org/2000/svg"/>',
            "no-namespace": '<svg viewBox="0 0 1 1"/>',
            "text-node": '<svg xmlns="http://www.w3.org/2000/svg">hi</svg>',
            "metadata": '<svg xmlns="http://www.w3.org/2000/svg"><metadata/></svg>',
            "defs": '<svg xmlns="http://www.w3.org/2000/svg"><defs/></svg>',
            "circle": ('<svg xmlns="http://www.w3.org/2000/svg">'
                       '<circle cx="1" cy="1" r="1"/></svg>'),
            "attr-svg-width": ('<svg xmlns="http://www.w3.org/2000/svg" '
                               'viewBox="0 0 1 1" width="1"/>'),
            "attr-path-fill-rule": ('<svg xmlns="http://www.w3.org/2000/svg">'
                                    '<path d="M0 0h1v1H0z" fill-rule="evenodd"/></svg>'),
            "attr-rect-id": ('<svg xmlns="http://www.w3.org/2000/svg">'
                             '<rect id="r" width="1" height="1" fill="#fff"/></svg>'),
            "attr-mask-fill": ('<svg xmlns="http://www.w3.org/2000/svg">'
                               '<mask id="m2" fill="#fff"/></svg>'),
            "empty": "   ",
            "non-string": 42,
        }
        for name, svg in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(cr.RenderError) as ctx:
                    cr.audit_avatars(self._avatars(svg))  # type: ignore[arg-type]
                message = str(ctx.exception)
                self.assertIn("dev", message, "消息应指出角色，但不回显原文")
                self.assertNotIn("alert(1)", message)
                self.assertNotIn("<svg", message)

    def test_accepts_internal_mask_reference(self) -> None:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4">'
            '<mask id="m"><rect width="4" height="4" fill="#fff"/></mask>'
            '<g mask="url(#m)"><path d="M0 0h4v4H0z" fill="#123"/></g></svg>'
        )
        audited = cr.audit_avatars(self._avatars(svg))
        self.assertIn("url(#m)", audited["dev"])

    def test_accepts_per_instance_renamed_mask_id(self) -> None:
        # 壳层会按实例改名；只要 id 受控且引用与定义一致，审计就必须放行。
        renamed = BASE_SVG.replace("viewboxMask", "avatar-abc123")
        audited = cr.audit_avatars(self._avatars(renamed))
        self.assertIn("url(#avatar-abc123)", audited["dev"])
        self.assertIn('id="avatar-abc123"', audited["dev"])

    def test_rejects_missing_or_extra_roles(self) -> None:
        payload = self._avatars()
        payload.pop("accept")
        with self.assertRaises(cr.RenderError) as ctx:
            cr.audit_avatars(payload)
        self.assertIn("accept", str(ctx.exception))

        payload = self._avatars()
        payload["intern"] = BASE_SVG
        with self.assertRaises(cr.RenderError) as ctx:
            cr.audit_avatars(payload)
        self.assertIn("intern", str(ctx.exception))

    def test_avatars_manifest_must_be_object(self) -> None:
        with self.assertRaises(cr.RenderError):
            cr.audit_avatars([BASE_SVG])


class RenderIsolationTests(StandinBase):
    def test_render_is_pure(self) -> None:
        before_assets = _digest(self.asset_root)
        snap = snapshot()
        original = copy.deepcopy(snap)
        self.render(snap)
        self.assertEqual(snap, original, "render_page 不得修改传入快照")
        self.assertEqual(_digest(self.asset_root), before_assets, "render_page 不得写资产目录")

    def test_invalid_snapshot_fails_without_echoing_values(self) -> None:
        snap = snapshot()
        secret_marker = "示例标题-不应回显"
        snap["pending"][0]["title"] = secret_marker
        del snap["stats"]
        with self.assertRaises(cr.RenderError) as ctx:
            self.render(snap)
        message = str(ctx.exception)
        self.assertIn("P0 契约", message)
        self.assertIn("stats", message)
        self.assertNotIn(secret_marker, message)

    def test_output_has_no_external_or_active_resources(self) -> None:
        html = self.render()
        lowered = html.lower()
        # 数据节点里可能出现业务 URL（如 P0 快照的 experience.url）、SVG 命名空间与
        # mask 的本地 url(#id) 引用；那是数据而非资源引用。只检查数据节点以外的
        # 文档结构、CSS 与 JS 是否拉取外部资源或动态执行代码。
        structural = re.sub(
            r'<script type="application/json" id="console-(?:snapshot|avatars)">'
            r".*?</script>",
            "",
            lowered,
            flags=re.DOTALL,
        )
        forbidden = {
            "<link": "外部样式/资源标签",
            "@import": "CSS 外部导入",
            "url(": "CSS url() 资源引用",
            "<img": "外部图片",
            "<iframe": "嵌入页面",
            "<object": "嵌入对象",
            "fetch(": "网络请求",
            "xmlhttprequest": "网络请求",
            "websocket": "长连接",
            "eventsource": "长连接",
            "import(": "动态模块加载",
            "new function": "动态代码",
            "eval(": "动态执行",
        }
        for token, reason in forbidden.items():
            with self.subTest(token=token):
                self.assertNotIn(token, structural, reason)
        self.assertIsNone(re.search(r"<script\b[^>]*\bsrc\s*=", structural))
        self.assertIn("console-snapshot", lowered, "数据节点仍然必须存在")
        self.assertNotIn("http://", structural.replace("http://www.w3.org/2000/svg", ""))
        self.assertNotIn("https://", structural)

    def test_tokens_come_from_tool_repo(self) -> None:
        self.assertEqual(cr.TOKENS_PATH, REPO_ROOT / "tools" / "ui-tokens.css")
        self.assertIn(cr.TOKENS_PATH.read_text(encoding="utf-8").strip(), self.render())


class ScaleTests(StandinBase):
    def _scaled(self, count: int) -> dict:
        base = snapshot()
        scaled = copy.deepcopy(base)
        pending_row = base["pending"][0]
        task_row = base["dispatchTasks"][0]
        run_row = base["runs"][0]
        scaled["pending"] = []
        scaled["dispatchTasks"] = []
        scaled["runs"] = []
        for index in range(count):
            item = copy.deepcopy(pending_row)
            item["id"] = f"N-SCALE-{index:05d}"
            item["title"] = f"示例标题 {index}"
            scaled["pending"].append(item)

            task = copy.deepcopy(task_row)
            task["taskNo"] = f"TS{index:05d}"
            task["id"] = f"{task['planPath']}#TS{index:05d}"
            scaled["dispatchTasks"].append(task)

            run = copy.deepcopy(run_row)
            run["id"] = f"runs/scale-{index:05d}.md"
            scaled["runs"].append(run)
        return scaled

    def test_10_100_1000_records(self) -> None:
        sizes: dict[int, int] = {}
        timings: dict[int, float] = {}
        for count in (10, 100, 1000):
            snap = self._scaled(count)
            self.assertEqual(cc.validate_snapshot(snap), [])
            started = time.perf_counter()
            html = self.render(snap)
            timings[count] = time.perf_counter() - started
            sizes[count] = len(html.encode("utf-8"))
            parsed = json.loads(_json_node(html, "console-snapshot"))
            self.assertEqual(len(parsed["pending"]), count)
            self.assertEqual(len(parsed["dispatchTasks"]), count)
            self.assertEqual(len(parsed["runs"]), count)
            self.assertNotRegex(html, r"@@[A-Z_]+@@")
        self.assertLess(sizes[10], sizes[100])
        self.assertLess(sizes[100], sizes[1000])
        self.assertLess(sizes[1000], 150 * sizes[10], "体积应近线性，不应出现平方级复制")
        # 只作粗略上限，不当作性能 SLA：1000 条记录的单文件渲染必须能在数秒内完成。
        self.assertLess(timings[1000], 10.0, f"1000 条渲染耗时 {timings[1000]:.2f}s")
        # 证据输出（不是 SLA）：记录条数 / 单文件字节数 / 渲染耗时。
        # 只用 ASCII：本机 cp950 终端打印简体字会 UnicodeEncodeError。
        print("\n[scale] " + " | ".join(
            f"{count} records: {sizes[count]} B / {timings[count] * 1000:.1f} ms"
            for count in (10, 100, 1000)))


class RealAvatarsManifestTests(unittest.TestCase):
    """真实 ``avatars.json`` 一旦合入即可独立审计，不必等壳层资产。"""

    def test_shipped_avatars_pass_audit(self) -> None:
        manifest = cr.DEFAULT_ASSET_ROOT / "avatars.json"
        if not manifest.is_file():
            self.skipTest("designer 的 tools/console_assets/avatars.json 尚未合入。")
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        audited = cr.audit_avatars(payload)
        self.assertEqual(sorted(audited), sorted(ROLE_IDS))
        for role, svg in audited.items():
            self.assertIn("<svg", svg, role)
            self.assertNotIn("<script", svg.lower(), role)

    def test_shipped_avatars_render_round_trip(self) -> None:
        """真实六个形象走完整 ``render_page``；壳层用测试替身，但形象是官方资产。"""
        manifest = cr.DEFAULT_ASSET_ROOT / "avatars.json"
        if not manifest.is_file():
            self.skipTest("designer 的 tools/console_assets/avatars.json 尚未合入。")
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="assistant-p2-real-avatars-") as raw:
            asset_root = make_standin_assets(Path(raw) / "assets", avatars=payload)
            html = cr.render_page(snapshot(), asset_root=asset_root)
        rendered = json.loads(_json_node(html, "console-avatars"))
        expected = {role: svg.strip() for role, svg in payload.items()}
        self.assertEqual(rendered, expected, "真实形象必须原样进入数据节点，不得改写")
        self.assertEqual(sorted(rendered), sorted(ROLE_IDS))
        for role, svg in rendered.items():
            self.assertIn("<svg", svg, role)
            self.assertEqual(svg.count("<mask"), 1, f"{role} 必须只有一个 mask")
            self.assertIn('mask="url(#viewboxMask)"', svg, role)
            self.assertNotIn("http://", svg.replace("http://www.w3.org/2000/svg", ""), role)


class RealAssetTests(unittest.TestCase):
    """designer 的真实壳层资产合入后才有意义；缺失时明确 skip。"""

    def setUp(self) -> None:
        self.asset_root = cr.DEFAULT_ASSET_ROOT
        if not (self.asset_root / "shell.html").is_file():
            self.skipTest(
                "designer 的壳层资产（tools/console_assets/shell.html 等）尚未合入；"
                "本用例只在真实资产上验证，不用替身冒充。")

    def test_real_assets_render_contract(self) -> None:
        html = cr.render_page(snapshot(), asset_root=self.asset_root)
        self.assertEqual(html.count('id="console-snapshot"'), 1)
        self.assertEqual(html.count('id="console-avatars"'), 1)
        self.assertRegex(html, r"<script[\s>]")
        self.assertNotIn("@@" + "SNAPSHOT" + "@@", html)
        avatars = json.loads(_json_node(html, "console-avatars"))
        cr.audit_avatars(avatars)
        lowered = html.lower()
        for token in ("<link", "<iframe", "<img", "@import", "xmlhttprequest"):
            self.assertNotIn(token, lowered)
        # v1.1：只有 channel.js 会请求，而且只请求同源的 /api/replies（通道模式；本机文件页面不会发）
        calls = re.findall(r"fetch\(([^,)]*)", lowered)
        self.assertTrue(calls, "channel.js 应该在页面里")
        for target in calls:
            self.assertTrue(target.strip().startswith("'/api/replies'"), target)

    def test_real_snapshot_round_trip(self) -> None:
        snap = snapshot("empty")
        html = cr.render_page(snap, asset_root=self.asset_root)
        self.assertEqual(json.loads(_json_node(html, "console-snapshot")), snap)


if __name__ == "__main__":
    unittest.main(verbosity=2)
