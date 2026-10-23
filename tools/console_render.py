"""assistant 指挥台 P2 离线渲染器：把快照、视觉规范、样式、脚本与固定形象内嵌成单文件 HTML。

职责与边界（对应 impl-plan 52-57、111-114、176、181）：

- ``render_page(snapshot, *, asset_root)`` 是唯一公开入口；只做字符串组装，
  **不读业务数据根、不写任何文件、无网络、无子进程、无 eval**。
- 资产只来自受版本控制的 ``asset_root``（默认 ``tools/console_assets``）；
  视觉规范固定来自工具仓库的 ``tools/ui-tokens.css``，原样内嵌、不修改。
- 模板占位符**只做一次**正则映射替换：先算出六份替换值，再用单次 ``re.sub``
  查表。用户数据、脚本或 CSS 里出现的 ``@@TOKENS@@`` 之类文本不会被二次替换。
- 快照与形象各自写进 ``type="application/json"`` 数据节点（``id=console-snapshot`` /
  ``id=console-avatars``），序列化统一走 P0 ``inline_json``；本模块不复制 P0 的
  敏感文本/凭据规则。
- 六个角色形象（role slug → SVG 字符串）在渲染前做严格的允许标签/属性审计：
  拒绝脚本、事件属性、外链、``foreignObject``、``style`` 等；唯一例外是
  ``mask="url(#id)"`` 指向同一 SVG 内唯一的本地 mask（官方资产需要），其它
  ``url()`` 一律拒绝。审计只看结构，不回显 SVG 原文。

模板契约（与 designer 约定，shell.html 只放这些一次性占位符）：

=====================================  ==============================================
占位符                                  渲染值
=====================================  ==============================================
``@@TOKENS@@``                          ``tools/ui-tokens.css`` 原文（放进 ``<style>``）
``@@BASE_CSS@@``                        ``base.css`` 原文（放进 ``<style>``）
``@@PAGE_CSS@@``                        ``actions.css`` + ``overview.css``（存在才拼，可空）
``@@SNAPSHOT@@``                        完整 ``<script id="console-snapshot">`` 数据节点
``@@AVATARS@@``                         完整 ``<script id="console-avatars">`` 数据节点
``@@SCRIPTS@@``                         完整 ``<script>`` 块：core → actions → overview → shell
=====================================  ==============================================

P3/P4 尚未合入时 ``actions.*`` / ``overview.*`` 不存在：渲染器只内嵌已有脚本，
缺页由 shell.js 给出提示，不伪造页面。缺 ``shell.html`` / ``base.css`` /
``shell.js`` / ``avatars.json`` / ``core.js`` 时明确失败，不生成半成品。
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import console_contract as contract

__all__ = [
    "RenderError",
    "DEFAULT_ASSET_ROOT",
    "TOKENS_PATH",
    "PLACEHOLDERS",
    "REQUIRED_ASSETS",
    "SCRIPT_ORDER",
    "CHANNEL_NODE_ID",
    "check_channel",
    "audit_avatars",
    "load_assets",
    "render_page",
]

_TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_ASSET_ROOT = _TOOLS_DIR / "console_assets"
TOKENS_PATH = _TOOLS_DIR / "ui-tokens.css"

PLACEHOLDERS = ("TOKENS", "BASE_CSS", "PAGE_CSS", "SNAPSHOT", "AVATARS", "SCRIPTS")
PLACEHOLDER_RE = re.compile(r"@@(TOKENS|BASE_CSS|PAGE_CSS|SNAPSHOT|AVATARS|SCRIPTS)@@")
ANY_PLACEHOLDER_RE = re.compile(r"@@[A-Za-z0-9_]+@@")

REQUIRED_ASSETS = ("core.js", "shell.html", "shell.js", "base.css", "avatars.json")
# v1.1：channel.js（本地通道的前端传输层，P3）在页面模块之前加载；缺省时跳过
SCRIPT_ORDER = ("core.js", "channel.js", "reply.js", "actions.js", "overview.js", "shell.js")
OPTIONAL_SCRIPTS = ("channel.js", "reply.js", "actions.js", "overview.js")
OPTIONAL_STYLES = ("actions.css", "overview.css")

SNAPSHOT_NODE_ID = "console-snapshot"
AVATARS_NODE_ID = "console-avatars"
CHANNEL_NODE_ID = "console-channel"
CHANNEL_KEYS = frozenset({"mode", "url", "token", "instance", "limits", "recordsVersion"})
CHANNEL_URL_RE = re.compile(r"^http://127\.0\.0\.1:([0-9]{2,5})/$")
CHANNEL_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
CHANNEL_INSTANCE_RE = re.compile(r"^[0-9a-f]{8,64}$")

MAX_SVG_CHARS = 20000
MAX_AVATARS_BYTES = 512 * 1024
MAX_SVG_ELEMENTS = 20000


class RenderError(RuntimeError):
    """渲染失败（缺资产、模板占位符不合契约、快照不合 P0 契约、SVG 审计不通过）。

    消息只含文件名/角色 slug/字段路径与固定原因，不包含被拒绝的原文。
    """


# ---------------------------------------------------------------- 资产读取

def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RenderError(f"无法读取资产 {path.name}（{type(exc).__name__}）") from None


def _read_svg_manifest(path: Path) -> dict:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RenderError(f"无法读取资产 {path.name}（{type(exc).__name__}）") from None
    if len(raw) > MAX_AVATARS_BYTES:
        raise RenderError(f"资产 {path.name} 超过 {MAX_AVATARS_BYTES} 字节上限")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise RenderError(f"资产 {path.name} 不是合法 UTF-8 JSON") from None
    return data


def _guard_css(name: str, text: str) -> None:
    """CSS 只能内嵌，不能外部拉取资源。"""
    lowered = text.lower()
    if "</style" in lowered:
        raise RenderError(f"资产 {name} 含 </style，无法安全内嵌")
    if "@import" in lowered:
        raise RenderError(f"资产 {name} 含 @import 外部样式引用")
    if "url(" in lowered:
        raise RenderError(f"资产 {name} 含 url() 资源引用")


def _guard_script(name: str, text: str) -> None:
    if "</script" in text.lower():
        raise RenderError(f"资产 {name} 含 </script，无法安全内嵌")


def _guard_shell(name: str, text: str) -> None:
    """壳层模板不允许自带外部资源标签；本工具只生成单文件页面。"""
    lowered = text.lower()
    if "<link" in lowered:
        raise RenderError(f"模板 {name} 含 <link> 引用，单文件页面不允许外部资源")
    if re.search(r"<script\b[^>]*\bsrc\s*=", lowered):
        raise RenderError(f"模板 {name} 含 <script src=>，单文件页面不允许外部脚本")
    if "<iframe" in lowered:
        raise RenderError(f"模板 {name} 含 <iframe>，单文件页面不允许嵌入外部页面")


def load_assets(asset_root: Path | str = DEFAULT_ASSET_ROOT) -> dict:
    """读取并校验受控资产；缺失时抛 :class:`RenderError`，不返回半成品。"""
    root = Path(asset_root)
    if not root.is_dir():
        raise RenderError("资产目录 tools/console_assets 不存在")
    missing = [name for name in REQUIRED_ASSETS if not (root / name).is_file()]
    if missing:
        raise RenderError("缺少渲染资产：" + "、".join(missing) + "（由 designer 的壳层包提供）")

    if not TOKENS_PATH.is_file():
        raise RenderError("缺少视觉规范 tools/ui-tokens.css")
    tokens = _read_text(TOKENS_PATH)

    shell = _read_text(root / "shell.html")
    _guard_shell("shell.html", shell)

    base_css = _read_text(root / "base.css")
    _guard_css("base.css", base_css)

    page_css_parts: list[str] = []
    for name in OPTIONAL_STYLES:
        path = root / name
        if not path.is_file():
            continue
        text = _read_text(path)
        _guard_css(name, text)
        page_css_parts.append(f"/* {name} */\n{text.strip()}")

    scripts: list[tuple[str, str]] = []
    for name in SCRIPT_ORDER:
        path = root / name
        if not path.is_file():
            if name in OPTIONAL_SCRIPTS:
                continue  # 页面模块 / 通道传输层缺省：只内嵌已有脚本，缺页交给 shell 提示。
            raise RenderError(f"缺少渲染脚本 {name}")
        text = _read_text(path)
        _guard_script(name, text)
        scripts.append((name, text.strip()))

    avatars = audit_avatars(_read_svg_manifest(root / "avatars.json"))

    return {
        "tokens": tokens,
        "shell": shell,
        "base_css": base_css,
        "page_css": "\n".join(page_css_parts),
        "scripts": scripts,
        "avatars": avatars,
    }


# ---------------------------------------------------------------- SVG 审计

_SVG_NS = "http://www.w3.org/2000/svg"
_SVG_TAG_PREFIX = "{" + _SVG_NS + "}"

#: 官方资产线 fix-2 清洗后的精确结构：
#: 只有 svg / mask / rect / g / path 五个 SVG 命名空间元素，无 metadata。
#: 每个元素再限定自己的属性，形成「元素 × 属性」双重白名单。
#: ``mask`` 是本地形状遮罩：只允许 ``url(#id)`` 引用同一 SVG 内的 id，不是外部资源。
_SVG_ELEMENT_ATTRIBUTES = {
    "svg": frozenset({"xmlns", "viewBox", "fill", "shape-rendering"}),
    "mask": frozenset({"id"}),
    "rect": frozenset({"width", "height", "rx", "ry", "x", "y", "fill"}),
    "g": frozenset({"mask", "fill"}),
    "path": frozenset({"d", "fill", "fill-opacity"}),
}
_SVG_ELEMENTS = frozenset(_SVG_ELEMENT_ATTRIBUTES)

#: 审计用禁止片段（固定字符串，不涉及业务文本规则）。协议/URL 由属性值白名单
#: 与命名空间校验兜住：任何属性值都不允许出现 ``:``，xmlns 只接受 SVG 命名空间。
#: ``url(`` 不在这里一刀切：``mask`` 属性单独要求 ``url(#本地id)``，其余属性见下。
_SVG_FORBIDDEN = (
    "@import", "<!doctype", "<!entity", "<!--", "<?", "xlink", "style=", " href",
)

_SVG_VALUE_RE = re.compile(r"^[-#A-Za-z0-9.,%() ]+$")
_SVG_REF_RE = re.compile(r"^url\(#([A-Za-z][A-Za-z0-9_-]{0,63})\)$")
_SVG_VIEWBOX_RE = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?(?:[ ,]+-?[0-9]+(?:\.[0-9]+)?){3}$")
_SVG_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def _audit_svg(role_id: str, text: object) -> None:
    """严格审计单个角色的内嵌 SVG；不通过即 ``RenderError``（消息不含原文）。"""

    def fail(reason: str) -> None:
        raise RenderError(f"角色形象 {role_id} 未通过 SVG 审计：{reason}")

    if not isinstance(text, str) or not text.strip():
        fail("不是非空 SVG 字符串")
    svg = text.strip()
    if len(svg) > MAX_SVG_CHARS:
        fail(f"超过 {MAX_SVG_CHARS} 字符上限")
    lowered = svg.lower()
    for token in _SVG_FORBIDDEN:
        if token in lowered:
            fail("含禁止片段")
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        fail("不是合法 XML")
    if root.tag != _SVG_TAG_PREFIX + "svg":
        fail("根元素必须是 SVG 命名空间的 svg")

    ids: set[str] = set()
    references: list[str] = []
    mask_ids: list[str | None] = []
    for element in root.iter():
        if not isinstance(element.tag, str) or not element.tag.startswith(_SVG_TAG_PREFIX):
            continue  # 非 SVG 命名空间节点由下面的主循环统一拒绝
        if element.tag == _SVG_TAG_PREFIX + "mask":
            mask_ids.append(element.attrib.get("id"))
        value = element.attrib.get("id")
        if value:
            ids.add(value)

    count = 0
    for element in root.iter():
        count += 1
        if count > MAX_SVG_ELEMENTS:
            fail(f"元素超过 {MAX_SVG_ELEMENTS} 个上限")
        if not isinstance(element.tag, str) or not element.tag.startswith(_SVG_TAG_PREFIX):
            fail("含非 SVG 命名空间节点")
        name = element.tag[len(_SVG_TAG_PREFIX):]
        if name not in _SVG_ELEMENTS:
            fail(f"元素 {name} 不在允许清单")
        allowed = _SVG_ELEMENT_ATTRIBUTES[name]
        if (element.text or "").strip() or (element.tail or "").strip():
            fail("含文本节点")
        for attribute, value in element.attrib.items():
            if "}" in attribute or attribute.lower().startswith("on"):
                fail("含带命名空间或事件的属性")
            if attribute not in allowed:
                fail(f"元素 {name} 的 {attribute} 不在允许清单")
            if not isinstance(value, str) or not value:
                fail(f"元素 <{name}> 的属性值为空或非字符串")
            if attribute == "xmlns":
                if value != _SVG_NS:
                    fail("xmlns 必须是 SVG 命名空间")
                continue
            if attribute == "viewBox":
                if not _SVG_VIEWBOX_RE.fullmatch(value.strip()):
                    fail("viewBox 格式不合法")
                continue
            if attribute == "id":
                if not _SVG_ID_RE.fullmatch(value):
                    fail("id 不符合受控格式")
                continue
            if attribute == "mask":
                match = _SVG_REF_RE.fullmatch(value)
                if not match:
                    fail("mask 只允许 url(#文档内id) 引用")
                references.append(match.group(1))
                continue
            if "url(" in value.lower():
                fail(f"元素 {name} 的属性值含 url() 资源引用")
            if not _SVG_VALUE_RE.fullmatch(value):
                fail(f"元素 {name} 的属性值含不允许字符")

    for reference in references:
        if reference not in ids:
            fail("mask 引用了不存在的 id")
    if references and (
        len(mask_ids) != 1
        or not mask_ids[0]
        or any(reference != mask_ids[0] for reference in references)
    ):
        fail("mask 引用必须指向同一 SVG 内唯一的 mask")


def audit_avatars(avatars: object) -> dict:
    """校验 role slug → SVG 的完整映射（六角色齐全、无多余键），逐个审计 SVG。"""
    if not isinstance(avatars, dict):
        raise RenderError("avatars.json 必须是 role slug 到 SVG 字符串的对象")
    keys = {str(key) for key in avatars}
    expected = set(contract.ROLE_IDS)
    missing = [role for role in contract.ROLE_IDS if role not in keys]
    if missing:
        raise RenderError("形象清单缺少角色：" + "、".join(missing))
    extra = sorted(keys - expected)
    if extra:
        raise RenderError("形象清单含未知角色：" + "、".join(extra))
    audited: dict[str, str] = {}
    for role in contract.ROLE_IDS:
        value = avatars[role]
        _audit_svg(role, value)
        audited[role] = value.strip()
    return audited


# ---------------------------------------------------------------- 组装

def _json_node(node_id: str, payload: dict) -> str:
    return (
        f'<script type="application/json" id="{node_id}">'
        f"{contract.inline_json(payload)}</script>"
    )


def _script_blocks(scripts: list[tuple[str, str]]) -> str:
    return "\n".join(f"<script>\n{source}\n</script>" for _name, source in scripts)


def check_channel(channel: object) -> dict:
    """``console-channel`` 节点内容（v1.1）：只认约定的键和形状，不合格抛 RenderError。

    通道给的页面 ``mode=channel`` 必须带令牌；本机文件页面 ``mode=file`` 不能带令牌，
    地址只能是 ``http://127.0.0.1:<端口>/``（或 None）。
    """
    if not isinstance(channel, dict) or not set(channel) <= CHANNEL_KEYS:
        raise RenderError("通道信息格式不对")
    mode = channel.get("mode")
    url = channel.get("url")
    if mode not in ("channel", "file"):
        raise RenderError("通道信息 mode 只能是 channel / file")
    if url is not None:
        match = CHANNEL_URL_RE.fullmatch(url) if isinstance(url, str) else None
        if match is None or not 1024 <= int(match.group(1)) <= 65535:
            raise RenderError("通道地址只能是 http://127.0.0.1:<端口>/")
    token = channel.get("token")
    if mode == "channel":
        if url is None or not isinstance(token, str) or not CHANNEL_TOKEN_RE.fullmatch(token):
            raise RenderError("通道页面缺少地址或令牌")
    elif token is not None:
        raise RenderError("本机文件页面不能带令牌")
    version = channel.get("recordsVersion")
    if version is not None and (not isinstance(version, str) or not CHANNEL_INSTANCE_RE.fullmatch(version)):
        raise RenderError("记录版本格式不对")
    instance = channel.get("instance")
    if instance is not None and (not isinstance(instance, str) or not CHANNEL_INSTANCE_RE.fullmatch(instance)):
        raise RenderError("通道实例标识格式不对")
    limits = channel.get("limits")
    if limits is not None and (not isinstance(limits, dict) or not all(
            isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) for k, v in limits.items())):
        raise RenderError("通道上限格式不对")
    return channel


def _placeholder_values(snapshot: dict, assets: dict, channel: dict | None = None) -> dict[str, str]:
    snapshot_node = _json_node(SNAPSHOT_NODE_ID, snapshot)
    if channel is not None:
        snapshot_node += "\n" + _json_node(CHANNEL_NODE_ID, channel)
    return {
        "TOKENS": assets["tokens"],
        "BASE_CSS": assets["base_css"],
        "PAGE_CSS": assets["page_css"],
        "SNAPSHOT": snapshot_node,
        "AVATARS": _json_node(AVATARS_NODE_ID, assets["avatars"]),
        "SCRIPTS": _script_blocks(assets["scripts"]),
    }


def _check_template(shell: str) -> None:
    found = PLACEHOLDER_RE.findall(shell)
    unknown = sorted(set(ANY_PLACEHOLDER_RE.findall(shell)) - {f"@@{k}@@" for k in PLACEHOLDERS})
    if unknown:
        raise RenderError("模板含未约定占位符：" + "、".join(unknown))
    for name in PLACEHOLDERS:
        hits = found.count(name)
        if hits == 0:
            raise RenderError(f"模板缺少占位符 @@{name}@@")
        if hits > 1:
            raise RenderError(f"模板占位符 @@{name}@@ 出现 {hits} 次（必须恰好一次）")


def _substitute(shell: str, values: dict[str, str]) -> str:
    """单次正则映射替换；替换结果中的任何 ``@@…@@`` 文本都不会被再次处理。"""
    return PLACEHOLDER_RE.sub(lambda match: values[match.group(1)], shell)


def render_page(snapshot: dict, *, asset_root: Path | str = DEFAULT_ASSET_ROOT,
                channel: dict | None = None) -> str:
    """渲染单文件 HTML。校验快照契约与资产，失败抛 :class:`RenderError`。

    ``channel``（v1.1）：页面要用的通道信息，拼成 ``console-channel`` 数据节点；None 不拼。
    """
    if not isinstance(snapshot, dict):
        raise RenderError("快照必须是对象")
    errors = contract.validate_snapshot(snapshot)
    if errors:
        # P0 validator 的错误只由固定字段路径/固定原因组成（已核对，不含输入值），
        # 因此可以安全带出首个错误；仍不回显任何用户文本。
        raise RenderError(
            f"快照不符合 P0 契约：{len(errors)} 项（首个：{errors[0]}）"
        )

    if channel is not None:
        check_channel(channel)
    assets = load_assets(asset_root)
    _check_template(assets["shell"])
    html = _substitute(assets["shell"], _placeholder_values(snapshot, assets, channel))

    node_ids = (SNAPSHOT_NODE_ID, AVATARS_NODE_ID) + ((CHANNEL_NODE_ID,) if channel is not None else ())
    for node_id in node_ids:
        marker = f'id="{node_id}"'
        if html.count(marker) != 1:
            raise RenderError(f"渲染结果必须恰好包含一个 {node_id} 数据节点")
    return html
