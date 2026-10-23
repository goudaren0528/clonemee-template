"""assistant 指挥台 P2 生产入口：只读数据根 → 渲染单文件 → 原子写出 inbox/console/index.html。

    python -B tools/console.py                  # 默认数据根 = 工具仓库根（common.ROOT）
    python -B tools/console.py --root <目录>     # 显式数据根必须是现存本地绝对目录
    python -B tools/console.py --help

边界（impl-plan 51-52、111-114、282）：

- **只写一份生成物**：工具仓库根的 ``inbox/console/index.html``。不建服务、不写历史、
  不改任何源记录；输入全部只读。
- 输出目录逐级检查；任一级或既有目标是符号链接/junction/硬链接时拒绝覆盖。
  渲染与校验全部在内存完成后才用同目录临时文件 ``os.replace`` 原子替换，
  失败时保留旧文件、清理临时文件。
- 环境用 ``env.detect(probe=False)`` 投影成 ``{inOrca, executorsAvailable}``，
  不探测 Orca、不起子进程、不联网。
- 生产 ``main`` 不内置任何数据替身：P1（``tools/console_data.py``）未合入时明确
  失败并说明缺什么，不伪造快照。测试可通过 ``run(...)`` 的关键字参数注入替身，
  注入路径在测试里显式标注为替身，不代表完整集成。

退出码：0 成功 · 2 用法错误 · 3 缺 P1 依赖 · 4 缺资产/模板不合契约 ·
5 输出安全校验或写盘失败 · 6 快照不合契约 · 1 其它未预期错误。
"""
from __future__ import annotations

import errno
import os
import re
import stat
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import common
import console_contract as contract
import console_render
from console_render import RenderError

__all__ = [
    "ConsoleError",
    "UsageError",
    "DependencyError",
    "OutputError",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_DEPENDENCY",
    "EXIT_ASSETS",
    "EXIT_OUTPUT",
    "EXIT_SNAPSHOT",
    "OUTPUT_RELATIVE",
    "OUTPUT_PATH",
    "ASSET_ROOT",
    "parse_args",
    "resolve_root",
    "detect_environment",
    "load_snapshot_or_fail",
    "write_output",
    "run",
    "main",
]

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_USAGE = 2
EXIT_DEPENDENCY = 3
EXIT_ASSETS = 4
EXIT_OUTPUT = 5
EXIT_SNAPSHOT = 6

OUTPUT_RELATIVE = Path("inbox") / "console" / "index.html"
OUTPUT_PATH = common.ROOT / OUTPUT_RELATIVE
ASSET_ROOT = console_render.DEFAULT_ASSET_ROOT

USAGE = """用法：python -B tools/console.py [--root <绝对目录>]

  --root <目录>   业务数据根；默认是本工具仓库根。必须是现存的本地绝对目录。
  -h, --help      显示本帮助，不读数据、不写文件。

输出固定写到工具仓库根的 inbox/console/index.html（不进 git），
并打印绝对路径和 file URI。页面是单文件、离线可打开、不会自动刷新。

退出码：0 成功 · 2 用法错误 · 3 缺 P1 数据包 · 4 缺渲染资产 ·
        5 输出安全/写盘失败 · 6 快照不合契约"""


class ConsoleError(RuntimeError):
    """带退出码的入口错误；消息面向使用者，不含原始记录内容。"""

    def __init__(self, message: str, *, exit_code: int = EXIT_INTERNAL) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class UsageError(ConsoleError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=EXIT_USAGE)


class DependencyError(ConsoleError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=EXIT_DEPENDENCY)


class AssetsError(ConsoleError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=EXIT_ASSETS)


class SnapshotError(ConsoleError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=EXIT_SNAPSHOT)


class OutputError(ConsoleError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=EXIT_OUTPUT)


# ---------------------------------------------------------------- 参数与数据根

def parse_args(argv: list[str]) -> dict:
    """极简参数解析：只认 ``--root``/``--help``，未知参数一律用法错误。"""
    root: str | None = None
    index = 0
    while index < len(argv):
        item = argv[index]
        if item in ("-h", "--help"):
            return {"help": True, "root": None}
        if item == "--root":
            if index + 1 >= len(argv):
                raise UsageError("--root 需要一个目录参数")
            root = argv[index + 1]
            index += 2
            continue
        if item.startswith("--root="):
            root = item[len("--root="):]
            index += 1
            continue
        # 不回显用户原始字符串：异常参数可能很长或含控制/敏感文本。
        raise UsageError("不支持的参数；只支持 --root 与 --help")
    return {"help": False, "root": root}


_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")


def _has_windows_ads(text: str) -> bool:
    """Windows 备用数据流（``file.txt:stream``）：盘符后的任何冒号都拒绝。

    只做词法判断；与 P0 ``_abs_path_text`` 的「盘符 + 分隔符」规则一致，
    不改 P0。非 Windows 平台不套用。
    """
    if os.name != "nt":
        return False
    rest = text[2:] if _DRIVE_PREFIX_RE.match(text) else text
    return ":" in rest


def _is_network_path(path: Path) -> bool:
    """解析后的路径是否落在 UNC/网络位置（含 ``\\\\?\\UNC\\...`` 形式）。"""
    if str(path).startswith(("\\\\", "//")):
        return True
    return str(path.drive).startswith(("\\\\", "//"))


def resolve_root(raw: str) -> Path:
    """显式数据根：必须是现存的本地绝对目录；相对路径/UNC/设备/ADS 一律拒绝。"""
    if not isinstance(raw, str) or not raw.strip():
        raise UsageError("--root 不能为空")
    text = raw
    if text != text.strip():
        raise UsageError("--root 不能带首尾空白")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        raise UsageError("--root 不能包含控制字符")
    if text.startswith(("\\\\", "//")):
        raise UsageError("--root 不接受 UNC 或设备路径")
    if _has_windows_ads(text):
        raise UsageError("--root 不能包含 Windows 备用数据流（ADS）冒号")
    candidate = Path(text)
    if not candidate.is_absolute():
        raise UsageError("--root 必须是绝对路径")
    if not candidate.exists():
        raise UsageError("--root 指向的目录不存在")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise UsageError("--root 无法解析为本地目录") from None
    # junction/symlink 解析后可能落到网络位置；只拒绝网络，本地跳转仍允许。
    if _is_network_path(resolved):
        raise UsageError("--root 解析后指向网络位置，已拒绝")
    if not resolved.is_dir():
        raise UsageError("--root 必须是目录，不是文件")
    return resolved


def local_now() -> datetime:
    """生成时刻：本机当前时间，带本地 UTC offset（快照要求 offset）。"""
    return datetime.now().astimezone()


def detect_environment() -> dict:
    """``env.detect(probe=False)`` 的最小投影；不探测、不调用 Orca CLI。"""
    import env as env_module

    try:
        info = env_module.detect(probe=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ConsoleError(f"无法识别运行环境（{type(exc).__name__}），已停止生成") from None
    available = [
        item for item in (info.get("agents_available") or [])
        if item in contract.EXECUTOR_IDS
    ]
    environment = {"inOrca": bool(info.get("in_orca")), "executorsAvailable": available}
    # v1.1：本机设备取通道状态文件里记的（通道进程算过指纹）；没有就不带，不在这里起子进程
    device = (_channel_state() or {}).get("device")
    if isinstance(device, dict) and isinstance(device.get("id"), str):
        environment["device"] = {"id": device["id"], "name": device.get("name")}
    return environment


def _channel_state(tool_root: Path | None = None) -> dict | None:
    """本地通道的状态文件（inbox/logs/console-channel.json，只在本机）；读不到返回 None。"""
    import console_channel

    return console_channel.read_state(tool_root or common.ROOT)


def file_channel(tool_root: Path | None = None) -> dict:
    """本机文件页面的 console-channel 节点：通道记着地址就带上（不带令牌、不探测端口）。"""
    state = _channel_state(tool_root) or {}
    url = state.get("url")
    channel = {"mode": "file", "url": url if isinstance(url, str) else None,
               "limits": contract.reply_limits()}
    try:
        return console_render.check_channel(channel)
    except RenderError:
        return {"mode": "file", "url": None, "limits": contract.reply_limits()}


def _import_snapshot_loader():
    """P1 适配器（``tools/console_data.py``）未合入时明确失败，不伪造数据。"""
    try:
        import console_data  # noqa: F401  (P1 工作包，当前分支可能尚未合入)
    except ModuleNotFoundError as exc:
        if exc.name == "console_data":
            raise DependencyError(
                "缺少 P1 数据适配包 tools/console_data.py：本分支还不能生成真实指挥台，"
                "也不会用测试夹具冒充数据。合入 P1 后重试。"
            ) from None
        raise
    loader = getattr(console_data, "load_snapshot", None)
    if not callable(loader):
        raise DependencyError(
            "tools/console_data.py 没有 load_snapshot(root, *, now, environment)，"
            "与 impl-plan 约定不符；已停止生成，不使用替代数据。"
        )
    return loader


def load_snapshot_or_fail(root: Path, *, now: datetime, environment: dict) -> dict:
    loader = _import_snapshot_loader()
    try:
        snapshot = loader(root, now=now, environment=environment)
    except ConsoleError:
        raise
    except RenderError:
        raise
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        raise ConsoleError(
            f"读取快照失败（{type(exc).__name__}）；未写出任何文件，旧页面保持原样。"
        ) from None
    if not isinstance(snapshot, dict):
        raise SnapshotError("load_snapshot 返回值不是对象；未写出任何文件。")
    errors = contract.validate_snapshot(snapshot)
    if errors:
        # 已核对 P0 validator：错误仅含固定字段路径与固定原因，不含输入值。
        raise SnapshotError(
            f"快照不符合 P0 契约：{len(errors)} 项（首个：{errors[0]}）；未写出任何文件。"
        )
    return snapshot


# ---------------------------------------------------------------- 安全写出

def _is_link_like(info: os.stat_result) -> bool:
    """符号链接 / Windows junction / 其它重解析点。"""
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _check_existing_component(path: Path, *, kind: str) -> None:
    info = os.lstat(path)
    if _is_link_like(info):
        raise OutputError(f"输出路径的{kind}是符号链接或 junction，拒绝写入：{path.name}")
    if kind == "目录" and not stat.S_ISDIR(info.st_mode):
        raise OutputError(f"输出路径的目录位置不是目录，拒绝写入：{path.name}")


def _ensure_directory_chain(base: Path, target_dir: Path) -> None:
    """从 base 逐级创建/校验到 target_dir；任一级是链接或非目录即失败。"""
    try:
        relative = target_dir.relative_to(base)
    except ValueError:
        raise OutputError("输出目录必须位于工具根内，拒绝写入") from None
    current = base
    if target_dir.exists() and not target_dir.is_dir():
        raise OutputError("输出目录被同名文件占用，拒绝写入")
    for part in relative.parts:
        current = current / part
        try:
            if os.path.lexists(current):
                _check_existing_component(current, kind="目录")
                continue
            os.mkdir(current)
            if not current.is_dir() or _is_link_like(os.lstat(current)):
                raise OutputError("输出目录创建后又变成链接，拒绝写入")
        except OutputError:
            raise
        except OSError as exc:
            raise OutputError(
                f"输出目录不可用（{exc.strerror or type(exc).__name__}），拒绝写入"
            ) from None
    if not current.is_dir():
        raise OutputError("输出目录不是目录，拒绝写入")


def _check_output_target(target: Path) -> None:
    if not os.path.lexists(target):
        return
    info = os.lstat(target)
    if _is_link_like(info):
        raise OutputError("既有生成物是符号链接或 junction，拒绝覆盖")
    if not stat.S_ISREG(info.st_mode):
        raise OutputError("既有生成物不是普通文件，拒绝覆盖")
    if info.st_nlink and info.st_nlink > 1:
        raise OutputError("既有生成物是硬链接，拒绝覆盖（避免改写其它文件）")


def write_output(html: str, *, path: Path, tool_root: Path) -> Path:
    """原子写出 HTML；失败抛 :class:`OutputError` 且保留旧文件。"""
    if not isinstance(html, str) or not html:
        raise OutputError("渲染结果是空的，拒绝写入")
    target = Path(path)
    base = Path(tool_root)
    if not target.is_absolute() or not base.is_absolute():
        raise OutputError("输出路径必须是绝对路径")
    if not base.is_dir():
        raise OutputError("工具根不是现存目录，拒绝写入")
    base = base.resolve(strict=True)
    if ".." in target.parts:
        raise OutputError("输出路径不能含 ..，拒绝写入")
    if any(part.casefold() == "private" for part in target.parts):
        raise OutputError("输出路径含 private 组件，拒绝写入")

    target_dir = target.parent
    try:
        _ensure_directory_chain(base, target_dir)
        _check_output_target(target)
        if os.path.lexists(target):
            if target.resolve(strict=True).parent != target_dir.resolve(strict=True):
                raise OutputError("既有生成物解析后不在预期目录，拒绝覆盖")
    except OutputError:
        raise
    except OSError as exc:
        raise OutputError(
            f"输出路径检查失败（{exc.strerror or type(exc).__name__}），拒绝写入"
        ) from None

    handle = -1
    temp_path = ""
    try:
        handle, temp_path = tempfile.mkstemp(
            dir=str(target_dir), prefix=".index-", suffix=".tmp"
        )
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            handle = -1
            stream.write(html)
            stream.flush()
            os.fsync(stream.fileno())
        if _is_link_like(os.lstat(temp_path)) or os.lstat(temp_path).st_nlink > 1:
            raise OutputError("临时输出文件异常，拒绝替换")
        os.replace(temp_path, target)
        temp_path = ""
    except OutputError:
        raise
    except OSError as exc:
        code = errno.errorcode.get(exc.errno or 0, type(exc).__name__)
        raise OutputError(
            f"写出生成物失败（{code}）；旧页面未改动，临时文件已清理"
        ) from None
    finally:
        if handle >= 0:
            try:
                os.close(handle)
            except OSError:
                pass
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
    return target


# ---------------------------------------------------------------- 入口

def run(
    argv: list[str] | None = None,
    *,
    loader=None,
    now: datetime | None = None,
    environment: dict | None = None,
    asset_root: Path | str | None = None,
    output_path: Path | None = None,
    tool_root: Path | None = None,
) -> int:
    """可注入依赖的执行体；生产 ``main`` 不传任何替身。

    ``loader``/``now``/``environment``/``asset_root``/``output_path``/``tool_root``
    仅供测试注入（``tests/console_preview.py`` 与 P2 单测），注入时必须显式说明
    是替身，不能当作完整集成证据。
    """
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    if args["help"]:
        print(USAGE)
        return EXIT_OK

    root = common.ROOT if args["root"] is None else resolve_root(args["root"])
    now = now or local_now()
    environment = environment or detect_environment()

    if loader is None:
        snapshot = load_snapshot_or_fail(root, now=now, environment=environment)
    else:
        snapshot = loader(root, now=now, environment=environment)
        if not isinstance(snapshot, dict):
            raise SnapshotError("注入的 loader 返回值不是对象")
        errors = contract.validate_snapshot(snapshot)
        if errors:
            raise SnapshotError(
                f"快照不符合 P0 契约：{len(errors)} 项（首个：{errors[0]}）；未写出任何文件。"
            )

    try:
        html = console_render.render_page(
            snapshot, asset_root=asset_root or ASSET_ROOT,
            channel=file_channel(tool_root or common.ROOT),
        )
    except RenderError as exc:
        raise AssetsError(str(exc)) from None

    target = write_output(
        html,
        path=output_path or OUTPUT_PATH,
        tool_root=tool_root or common.ROOT,
    )
    print(f"已生成：{target}")
    print(f"打开：{target.as_uri()}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    common.setup_stdio()
    try:
        return run(argv)
    except ConsoleError as exc:
        print(f"指挥台生成失败：{exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("已中断，未改动旧页面。", file=sys.stderr)
        return EXIT_INTERNAL
    except OSError as exc:
        print(f"指挥台生成失败：系统错误 {type(exc).__name__}", file=sys.stderr)
        return EXIT_OUTPUT


if __name__ == "__main__":
    sys.exit(main())
