"""P2 独立测试预览入口：只用 P0 虚构场景，在临时根里生成一份可打开的指挥台。

    python -B tests/console_preview.py --scenario full-orca
    python -B tests/console_preview.py --scenario full-local --snapshot-only
    python -B tests/console_preview.py --scenario full-orca --snapshot-only --no-wait

生命周期（impl-plan 281）：

- 每次都用 ``tempfile.TemporaryDirectory`` 新建系统临时目录
  （``assistant-console-preview-*``），把 P0 虚构 Markdown 写进去，再调用**真实的**
  ``console_data.load_snapshot`` 与 ``console_render.render_page``，把 HTML 写到该
  临时根下的 ``preview/index.html``。
- 默认交互保持：渲染完成后等待操作者按 Enter（或 stdin EOF）；正常结束、EOF、
  Ctrl+C 都会在退出时删除临时根，不持久化预览产物。生产 ``tools/console.py``
  与真实 ``inbox/console/index.html`` 都不会被调用或覆盖。
- ``--no-wait`` 仅供自动验证：渲染后立即清理临时根并退出，不等待、不保留。
- ``--snapshot-only`` 是**明确标识的测试替身模式**：跳过 P1，直接用 P0
  ``snapshot_fixture`` 渲染，只用于看壳层/样式效果，不代表真实适配结果；
  该选项只存在于本测试入口，生产 CLI 不提供。
- 缺 P1（``tools/console_data.py``）时真实适配预览明确失败，不用替身冒充；
  缺 P2 资产（designer 的 shell.html 等）时明确说明缺哪些文件。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import console  # noqa: E402
import console_contract as cc  # noqa: E402
import console_render as cr  # noqa: E402
import console_support  # noqa: E402

USAGE = """用法：python -B tests/console_preview.py --scenario <场景> [选项]

  --scenario <场景>   必填；P0 虚构场景：""" + " / ".join(console_support.SCENARIOS) + """
  --snapshot-only     测试替身模式：跳过 P1，只用 P0 纯快照渲染（明确非真实数据）
  --no-wait           仅自动验证：渲染后立即清理临时根，不等待、不保留
  -h, --help          显示本帮助

默认渲染后等待 Enter/EOF/Ctrl+C，结束时删除临时根；只写系统临时目录，
不调用生产入口、不覆盖真实 inbox/console/index.html。
"""


def _parse(argv: list[str]) -> dict:
    options = {"scenario": None, "snapshot_only": False, "no_wait": False, "help": False}
    index = 0
    while index < len(argv):
        item = argv[index]
        if item in ("-h", "--help"):
            options["help"] = True
            index += 1
            continue
        if item == "--scenario":
            if index + 1 >= len(argv):
                raise SystemExit("--scenario 需要一个场景名")
            options["scenario"] = argv[index + 1]
            index += 2
            continue
        if item.startswith("--scenario="):
            options["scenario"] = item[len("--scenario="):]
            index += 1
            continue
        if item == "--snapshot-only":
            options["snapshot_only"] = True
            index += 1
            continue
        if item == "--no-wait":
            options["no_wait"] = True
            index += 1
            continue
        raise SystemExit("不支持的参数；只支持 --scenario、--snapshot-only、--no-wait、--help")
    return options


def _fail(message: str) -> int:
    print(f"预览失败：{message}", file=sys.stderr)
    return 1


class _PreviewError(RuntimeError):
    """预览可预期失败；消息面向操作者。"""


def _load_snapshot(options: dict, temp_root: Path):
    """返回 ``(snapshot, environment)``；失败时抛 ``SystemExit`` 之外由调用方处理。"""
    now = console_support.DEFAULT_NOW
    if options["snapshot_only"]:
        print("模式：SNAPSHOT-ONLY 测试替身预览（未调用 P1 适配器，不代表真实数据）")
        snapshot = console_support.snapshot_fixture(scenario=options["scenario"], now=now)
        return snapshot, snapshot["environment"]

    manifest = console_support.make_fixture(temp_root, scenario=options["scenario"], now=now)
    environment = manifest["environment"]
    try:
        import console_data
    except ModuleNotFoundError as exc:
        if exc.name == "console_data":
            raise _PreviewError(
                "P1 数据适配包未合入（缺少 tools/console_data.py），"
                "真实适配预览不可用。可用 --snapshot-only 查看壳层效果"
                "（该模式是测试替身，不代表真实数据）。"
            ) from None
        raise
    loader = getattr(console_data, "load_snapshot", None)
    if not callable(loader):
        raise _PreviewError("tools/console_data.py 缺少 load_snapshot，与约定不符。")
    try:
        snapshot = loader(temp_root, now=now, environment=environment)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        raise _PreviewError(f"P1 读取虚构夹具失败（{type(exc).__name__}）。") from None
    return snapshot, environment


def _wait_for_operator() -> None:
    print("预览已生成。按 Enter 结束并清理临时根（EOF/Ctrl+C 同样会清理）……")
    try:
        sys.stdin.readline()
    except KeyboardInterrupt:
        print("\n已中断，正在清理临时根。", file=sys.stderr)


def _main(argv: list[str] | None = None) -> int:
    options = _parse(list(sys.argv[1:] if argv is None else argv))
    if options["help"]:
        print(USAGE)
        return 0
    scenario = options["scenario"]
    if scenario not in console_support.SCENARIOS:
        return _fail(f"需要 --scenario，可选：{', '.join(console_support.SCENARIOS)}")

    with tempfile.TemporaryDirectory(prefix="assistant-console-preview-") as raw:
        temp_root = Path(raw)
        print(f"临时根：{temp_root}")
        try:
            snapshot, environment = _load_snapshot(options, temp_root)
            errors = cc.validate_snapshot(snapshot)
            if errors:
                raise _PreviewError(f"快照不符合 P0 契约：{len(errors)} 项（首个：{errors[0]}）")
            try:
                html = cr.render_page(snapshot, asset_root=cr.DEFAULT_ASSET_ROOT)
            except cr.RenderError as exc:
                raise _PreviewError(f"渲染资产不可用：{exc}") from None
            target = temp_root / "preview" / "index.html"
            try:
                path = console.write_output(html, path=target, tool_root=temp_root)
            except console.OutputError as exc:
                raise _PreviewError(f"写出预览失败：{exc}") from None
        except _PreviewError as exc:
            print(f"预览失败：{exc}", file=sys.stderr)
            return 1

        counts = {
            "pending": len(snapshot.get("pending") or []),
            "dispatchTasks": len(snapshot.get("dispatchTasks") or []),
            "runs": len(snapshot.get("runs") or []),
            "roles": len(snapshot.get("roles") or []),
        }
        print(f"场景：{scenario} · 环境 inOrca={environment.get('inOrca')}")
        print("记录数：" + " · ".join(f"{key} {value}" for key, value in counts.items()))
        print(f"预览：{path}")
        print(f"打开：{path.as_uri()}")
        if options["no_wait"]:
            print("模式：--no-wait 自动验证（退出时立即清理临时根，不保留预览）")
            return 0
        _wait_for_operator()
        return 0


def main(argv: list[str] | None = None) -> int:
    """外层兜底：渲染/写盘阶段的 Ctrl+C 也保证临时根由上下文清理。"""
    try:
        return _main(argv)
    except KeyboardInterrupt:
        print("\n已中断，临时根已清理。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
