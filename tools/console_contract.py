"""assistant 指挥台 P0 共享契约：快照 TypedDict、固定枚举、路径/文本/链接安全边界与快照校验。

只读、无网络、无子进程；不读写业务记录。P1 适配器负责把白名单字段投影成快照，
P2 渲染器负责内嵌；本模块只提供类型契约与安全纯函数。

安全定位（本机单人只读工具）：
- ``checked_source`` 先做词法校验，再 ``resolve(strict=True)`` 并复核根内与 private；
  不承诺抵御读取瞬间被并发替换的 TOCTOU。
- ``safe_text`` / ``safe_experience_url`` 使用有限规则集（private 组件、PEM、
  Authorization/Bearer、常见凭据格式、赋值形态、代码块/命令链）。有限规则不能证明
  任意文本绝无秘密，必须配合最小字段投影与人工检查；命中时一律返回 None，不返回原值。
- ``validate_snapshot`` 只报告字段路径和规则，不包含被校验的原值。

冻结口径（见 docs/delivery/assistant-console/packages/P0.md）：
- 执行器 ``claude`` / ``codex`` / ``opencode``；模式 ``worktree`` / ``directory`` / ``terminal``。
- 六角色 ``research`` / ``product`` / ``pm`` / ``investigate`` / ``dev`` / ``accept``。
- 页 ``pending`` / ``dispatch`` / ``runs`` / ``roles``；``schemaVersion = 2``。
- 缺失为 ``None``，已知空集合为 ``[]``，已知计数零为 ``0``。

v1.1 增量（见 docs/delivery/assistant-console/impl-plan-v1.1.md「接口约定」1–2，进度
docs/delivery/assistant-console/v1.1/packages/P0.md）：
- 快照 v2 加 ``environment.device``、顶层 ``replies``、``Pending.reply`` / ``Run.reply``；
  这些键缺省等同「没有」（v0 页面与旧调用方不受影响），出现时严格校验。
- 回复分表 ``runs/replies.md`` 的列、状态中文、卡片键、快捷评价、上限、错误码、
  控制台指令语法都在这里定义；notify.py、console_data.py、本地通道与 core.js 只引用。
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import NotRequired, TypedDict
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit
from urllib.request import url2pathname

SCHEMA_VERSION = 2

EXECUTOR_IDS = ("claude", "codex", "opencode")
EXECUTOR_LABELS = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}
MODE_IDS = ("worktree", "directory", "terminal")
MODE_LABELS = {"worktree": "新建工作区", "directory": "现有目录", "terminal": "新终端窗口"}
ROLE_IDS = ("research", "product", "pm", "investigate", "dev", "accept")
ROLE_LABELS = {"research": "调研", "product": "产品", "pm": "跟进",
               "investigate": "排查", "dev": "开发", "accept": "验收"}
PAGE_IDS = ("pending", "dispatch", "runs", "roles")

PENDING_TYPES = ("完成", "要拍板", "卡住", "交接")
PENDING_STATUSES = ("待处理", "已处理", "unknown")
PENDING_COLUMNS = ("待验收", "要拍板", "卡住")
ACCEPT_CHOICE_IDS = ("accept", "reject", "later")
ACCEPT_CHOICE_LABELS = {"accept": "验收通过", "reject": "打回", "later": "稍后"}

EXPERIENCE_UI_STATES = ("yes", "no", "unknown")
EXPERIENCE_STATES = ("recorded-started", "static", "expired", "unknown")
DISPATCH_KINDS = ("single", "delivery")
RUN_STATUSES = ("running", "waiting", "done", "unknown")
RUN_KINDS = ("single", "delivery")
STAGE_STATES = ("done", "current", "problem", "unknown")
ROLE_STATUSES = ("draft", "active", "unknown")
REVIEW_VERDICTS = ("pass", "fix")
ROLE_WORKSPACES = ("worktree", "directory")

SOURCE_KINDS = ("pending", "daily", "run", "role", "review", "experience", "replies", "device")

SAFE_TEXT_LIMIT = 500
TRUNCATION_MARKER = "［已截断］"
MAX_SAFE_TEXT_RAW = 20000
MAX_RELATIVE_PATH = 512
MAX_DISPLAY_PATH = 1024
MAX_URL_LENGTH = 2048

# ---------------------------------------------------------------- v1.1 回复

# 上限与页面轮询间隔（AC-18：1 分钟内更新）
REPLY_MAX_CHARS = 500
REPLY_PER_MINUTE = 10
REPLY_DEDUPE_SECONDS = 10
REPLY_POLL_SECONDS = 20
REPLY_MAX_BODY_BYTES = 16384
REPLY_MAX_LINES = 20
REPLY_MAX_ITEMS = 200            # 快照 / 接口最多带多少条回复
CHANNEL_PORTS = tuple(range(17318, 17328))

REPLY_PAGES = ("pending", "runs", "dispatch", "roles")
REPLY_PAGE_LABELS = {"pending": "待处理", "runs": "运行监督", "dispatch": "派发确认", "roles": "角色管理"}
REPLY_KINDS = ("forward", "main")
REPLY_REASONS = ("other_device",)
REPLY_STATUSES = ("waiting", "delivered", "forwarded", "acked", "handled", "lost", "unknown")
# 分表「状态」列的中文写法（开头几个字）→ 枚举；转不到后面可以跟「：原因」
REPLY_STATUS_LABELS = {"waiting": "等主会话", "delivered": "已送达主会话", "forwarded": "已转给跟进",
                       "acked": "跟进已收到", "handled": "主会话已处理", "lost": "转不到"}
# 回复分表 runs/replies.md 的列（notify.py 写，console_data.py 读，列名不要改）
REPLY_COLUMNS = ("编号", "时间", "设备", "页", "事项", "关联", "快捷评价", "正文", "送达", "转给", "状态", "回执")
REPLY_LINE_JOINER = " ⏎ "        # 派发确认 / 角色管理的多条指令在「正文」一格里的连接符

# 快捷评价。要拍板的选项来自消息本身（Pending.choices），不在这里。
BLOCKED_REPLY_CHOICES = {"follow": "按建议办", "other": "换个办法", "pause": "先停下"}
ACCEPT_REPLY_CHOICES = dict(ACCEPT_CHOICE_LABELS)
RUN_REPLY_CHOICES = {"add": "补充", "ask": "问进度", "pause": "先停下"}
REPLY_NEED_TEXT = ("other", "reject", "add")   # 选了必须写字
REPLY_PAGE_ONLY = ("later",)                    # 只在页面上标记，不发送

REPLY_RECEIVER_FOLLOW = "跟进 Agent"
REPLY_RECEIVER_EXECUTOR = "执行会话（没有跟进 Agent）"
REPLY_RECEIVER_MAIN = "主会话"

# 本地通道接口的错误码 → HTTP 状态（P3 执行，页面按 code 显示）
REPLY_ERROR_CODES = {
    "bad_host": 403, "bad_origin": 403, "stale": 401, "bad_type": 415, "too_large": 413,
    "bad_json": 400, "empty": 422, "need_text": 422, "too_long": 422, "bad_card": 422,
    "not_replyable": 422, "other_device": 422, "bad_choice": 422, "bad_lines": 422,
    "sensitive": 422, "rate_limited": 429, "not_found": 404, "method": 405, "internal": 500,
    "busy": 503,
}


# ---------------------------------------------------------------- 快照 TypedDict

class SourceRef(TypedDict):
    """一条数据在源记录中的位置；path 为安全绝对路径（展示用，不据此读文件）。"""

    path: str
    lineStart: int
    lineEnd: int
    field: str


class Metric(TypedDict):
    value: int | None
    sourceIds: list[str]
    reason: str | None


class Environment(TypedDict):
    inOrca: bool
    executorsAvailable: list[str]
    # v1.1：本机设备 {id: 指纹, name: 主机名}；生成方注入，缺省等同未知
    device: NotRequired[EnvironmentDevice | None]


class EnvironmentDevice(TypedDict):
    id: str
    name: str | None


class ReplyDevice(TypedDict):
    """回复 / 卡片所在设备；id 为 None 表示记录里没写（旧记录），local 随之为 None。"""

    id: str | None
    name: str | None
    local: bool | None


class ReplySlot(TypedDict):
    """一张卡片的回复区；None（或键缺省）表示没有回复区。"""

    card: str
    allowed: bool
    reason: str | None
    kind: str
    receiver: str
    association: str
    choices: list[Choice]
    needText: list[str]
    pageOnly: list[str]
    device: ReplyDevice


class ReplyAck(TypedDict):
    at: str | None
    text: str


class Reply(TypedDict):
    id: str
    createdAt: str | None
    card: str | None
    page: str
    item: str
    association: str | None
    quick: str | None
    text: str
    status: str
    statusNote: str | None
    forwardedTo: str | None
    ack: ReplyAck | None
    device: ReplyDevice


class Choice(TypedDict):
    id: str
    label: str
    source: str


class ExperienceTests(TypedDict):
    passed: int
    total: int
    summary: str


class Experience(TypedDict):
    ui: str
    state: str
    url: str | None
    recordedAt: str | None
    expiresAt: str | None
    tests: ExperienceTests | None
    focus: list[str]
    sources: list[SourceRef]


class Diagnostic(TypedDict):
    code: str
    message: str
    source: str | None


class Pending(TypedDict):
    id: str
    type: str
    status: str
    column: str | None
    project: str
    item: str
    title: str
    summary: str
    roleId: str | None
    senderName: str
    createdAt: str | None
    detailPath: str | None
    runId: str | None
    choices: list[Choice]
    processedAt: str | None
    processedNote: str | None
    experience: Experience | None
    sources: list[SourceRef]
    diagnostics: list[Diagnostic]
    reply: NotRequired[ReplySlot | None]


class DispatchTask(TypedDict):
    id: str
    taskNo: str
    project: str
    title: str
    summary: str | None
    planPath: str
    roleId: str | None
    declaredExecutor: str | None
    changesCode: bool | None
    kind: str
    prdPath: str | None
    difficulty: int | None
    stages: list[str]
    outputs: list[str]
    defaultExecutor: str | None
    defaultMode: str | None
    defaultReason: str
    fallback: bool
    selectable: bool
    disabledReason: str | None
    sources: list[SourceRef]


class Stage(TypedDict):
    label: str
    state: str
    source: str | SourceRef


class FirstReview(TypedDict):
    round: int
    verdict: str
    source: str | SourceRef


class RunAgent(TypedDict):
    id: str
    roleId: str | None
    executor: str | None
    packageId: str | None
    activity: str | None
    state: str
    lastChangedAt: str | None


class Package(TypedDict):
    id: str
    state: str
    dependencies: list[str]
    reviewRound: int | None
    updatedAt: str | None
    firstReview: FirstReview | None


class Run(TypedDict):
    id: str
    kind: str
    project: str
    item: str
    status: str
    rawStatus: str
    roleId: str | None
    createdAt: str | None
    completedAt: str | None
    lastChangedAt: str | None
    stale: bool | None
    stages: list[Stage]
    agents: list[RunAgent]
    packages: list[Package]
    progress: list[str]
    decisions: list[str]
    pendingIds: list[str]
    detailPath: str
    firstReview: FirstReview | None
    sources: list[SourceRef]
    diagnostics: list[Diagnostic]
    reply: NotRequired[ReplySlot | None]


class LevelProgress(TypedDict):
    current: int
    span: int
    nextRemaining: int | None


class Role(TypedDict):
    id: str
    name: str
    status: str
    executors: list[str]
    skills: list[str]
    useWhen: str | None
    avatarKey: str
    completed: Metric
    firstPass: Metric
    activeSessions: Metric
    level: int | None
    levelProgress: LevelProgress | None
    isNew: bool | None
    sources: list[SourceRef]
    # PRD 未定义的角色（如单独验收）保留配置里的地点；不产生任何真实目录。
    workspace: NotRequired[str | None]


class Stats(TypedDict):
    pendingCount: Metric
    runningCount: Metric
    completedToday: Metric
    dispatchedToday: Metric
    completedWeek: Metric
    clearStreak: Metric


class Snapshot(TypedDict):
    schemaVersion: int
    generatedAt: str
    localDate: str
    environment: Environment
    stats: Stats
    pending: list[Pending]
    dispatchTasks: list[DispatchTask]
    runs: list[Run]
    roles: list[Role]
    diagnostics: list[Diagnostic]
    replies: NotRequired[list[Reply]]


__all__ = [
    "SCHEMA_VERSION", "EXECUTOR_IDS", "EXECUTOR_LABELS", "MODE_IDS", "MODE_LABELS",
    "ROLE_IDS", "ROLE_LABELS", "PAGE_IDS", "PENDING_TYPES", "PENDING_STATUSES",
    "PENDING_COLUMNS", "ACCEPT_CHOICE_IDS", "ACCEPT_CHOICE_LABELS",
    "EXPERIENCE_UI_STATES", "EXPERIENCE_STATES", "DISPATCH_KINDS", "RUN_STATUSES",
    "RUN_KINDS", "STAGE_STATES", "ROLE_STATUSES", "REVIEW_VERDICTS", "ROLE_WORKSPACES",
    "SOURCE_KINDS", "SAFE_TEXT_LIMIT", "TRUNCATION_MARKER", "MAX_SAFE_TEXT_RAW",
    "MAX_RELATIVE_PATH", "MAX_DISPLAY_PATH", "MAX_URL_LENGTH",
    "SourceRef", "Metric", "Environment", "Choice", "ExperienceTests", "Experience",
    "Diagnostic", "Pending", "DispatchTask", "Stage", "FirstReview", "RunAgent",
    "Package", "Run", "LevelProgress", "Role", "Stats", "Snapshot",
    "checked_source", "display_path", "safe_text", "safe_experience_url",
    "inline_json", "validate_snapshot",
    # v1.1 回复
    "REPLY_MAX_CHARS", "REPLY_PER_MINUTE", "REPLY_DEDUPE_SECONDS", "REPLY_POLL_SECONDS",
    "REPLY_MAX_BODY_BYTES", "REPLY_MAX_LINES", "REPLY_MAX_ITEMS", "CHANNEL_PORTS",
    "REPLY_PAGES", "REPLY_PAGE_LABELS", "REPLY_KINDS", "REPLY_REASONS", "REPLY_STATUSES",
    "REPLY_STATUS_LABELS", "REPLY_COLUMNS", "REPLY_LINE_JOINER", "BLOCKED_REPLY_CHOICES",
    "ACCEPT_REPLY_CHOICES", "RUN_REPLY_CHOICES", "REPLY_NEED_TEXT", "REPLY_PAGE_ONLY",
    "REPLY_RECEIVER_FOLLOW", "REPLY_RECEIVER_EXECUTOR", "REPLY_RECEIVER_MAIN", "REPLY_ERROR_CODES",
    "EnvironmentDevice", "ReplyDevice", "ReplySlot", "ReplyAck", "Reply",
    "card_key", "parse_card_key", "reply_status_of", "reply_limits",
    "validate_reply_input", "check_console_command",
]


# ---------------------------------------------------------------- 文本规则

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u00ad\u180e]")
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200e\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]")
_REMOVE_CONTROLS_RE = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u00ad\u180e\u200b\u200c\u200e\u200f"
    r"\u2028-\u202e\u2060\u2066-\u2069\ufeff]"
)
_TOKEN_SPLIT_RE = re.compile(r"[\s\"'`<>()\[\]{}|,;，。；：、]+")
_PRIVATE_PATH_CONTEXT_RE = re.compile(r"(?:^|[^A-Za-z0-9_.-])private(?=/)", re.IGNORECASE)
_PRIVATE_MD_TARGET_RE = re.compile(r"\]\(\s*private(?=$|[/?#\s)])", re.IGNORECASE)
_PRIVATE_QUERY_PATH_RE = re.compile(r"/private(?=[?#])", re.IGNORECASE)

_MD_IMAGE_RE = re.compile(r"!\[([^\]\n]*)\]\((?:[^()\n]|\([^()\n]*\))*\)")
_MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((?:[^()\n]|\([^()\n]*\))*\)")
_MD_REF_RE = re.compile(r"\[([^\]\n]+)\]\[[^\]\n]*\]")
_MD_AUTOLINK_RE = re.compile(r"<((?:https?|file):[^>\s]+)>")

_PEM_RE = re.compile(r"-----BEGIN [A-Z0-9 ]{0,48}-----", re.IGNORECASE)
_AUTH_RE = re.compile(
    r"(?i)(?:\bauthori[sz]ation\b\s*[:=]|\bbearer\s+[A-Za-z0-9._~+/=-]{8,}|\bbearer\s*[:=])"
)
#: 双端统一的明确凭据字段（下划线/连字符/无分隔变体）。
#: 规则源只在本模块维护；core.js 的 CREDENTIAL_ASSIGNMENT_RE 块由
#: ``credential_assignment_js()`` 在开发时生成，禁止手改。
_ASSIGN_KEYS = (
    r"aws[-_]?secret[-_]?access[-_]?key|secret[-_]?access[-_]?key"
    r"|access[-_]?token|refresh[-_]?token|id[-_]?token|auth[-_]?token"
    r"|client[-_]?secret|private[-_]?key|api[-_]?key|apikey"
    r"|token|password|passwd|pwd|secret"
)
#: 显式跨端空白字符集：不用 \s/\S，避免 Python/JS 的 Unicode 空白定义差异。
_ASSIGN_WS_CHARS = r" \t\n\r\f\v\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000"
_ASSIGN_WS = r"[" + _ASSIGN_WS_CHARS + r"]"
_ASSIGN_NON_WS = r"[^" + _ASSIGN_WS_CHARS + r"]"
#: 英文键用受控 ASCII 边界；中文键不要求前导边界，任何前缀后明确赋值都拒绝。
def _ascii_fold(pattern: str) -> str:
    """把 ASCII 字母展开成显式 ``[aA]``，避免 Python/JS casefold 差异。"""
    folded: list[str] = []
    for ch in pattern:
        if "a" <= ch <= "z":
            folded.append("[" + ch + ch.upper() + "]")
        elif "A" <= ch <= "Z":
            folded.append("[" + ch + ch.lower() + "]")
        else:
            folded.append(ch)
    return "".join(folded)


_CREDENTIAL_ASSIGNMENT_SOURCE = (
    r"(?:(?<![A-Za-z0-9_])(?:" + _ascii_fold(_ASSIGN_KEYS) + r")(?![A-Za-z0-9_])"
    r"|(?:密码|口令|令牌|密钥))"
    + _ASSIGN_WS + r"*[:=：]" + _ASSIGN_WS
    + r"*(?:" + _ASSIGN_NON_WS + r"|(?![\s\S]))"
)
_ASSIGN_RE = re.compile(_CREDENTIAL_ASSIGNMENT_SOURCE)


def credential_assignment_js() -> str:
    """生成 core.js 内固定受控的 JS 声明块；规则源仍只有 Python 这一份。

    返回值包含 BEGIN/END 标记和两空格缩进，按原样嵌入 core.js 的 IIFE。
    这里只生成规则数据与 ``new RegExp`` 声明，不 eval、不执行用户文本。
    """
    source_literal = json.dumps(_CREDENTIAL_ASSIGNMENT_SOURCE, ensure_ascii=True)
    return (
        "  // BEGIN CREDENTIAL_ASSIGNMENT_RE "
        "(generated by console_contract.credential_assignment_js; do not edit)\n"
        "  var CREDENTIAL_ASSIGNMENT_SOURCE = " + source_literal + ";\n"
        "  var CREDENTIAL_ASSIGNMENT_RE = "
        "new RegExp(CREDENTIAL_ASSIGNMENT_SOURCE, 'i');\n"
        "  // END CREDENTIAL_ASSIGNMENT_RE"
    )
_CREDENTIAL_RES = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
)
_CODE_RE = re.compile(r"```|~~~")
_COMMAND_RE = re.compile(
    r"\$\(|`|\$\{|\|\||&&|>>|2>&1|\brm\s+-rf\b|\bsudo\s+\S"
    r"|\bpowershell(?:\.exe)?\s+-|\bcmd(?:\.exe)?\s+/c\b",
    re.IGNORECASE,
)

_SENSITIVE_COMPONENTS = {
    ".ssh", ".aws", ".azure", ".gnupg", ".netrc", "credentials",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
}
_SENSITIVE_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".kdbx")
_SENSITIVE_PARAM_NAMES = {
    "token", "access_token", "refresh_token", "id_token", "auth_token",
    "api_key", "apikey", "password", "passwd", "secret", "client_secret",
    "sig", "signature", "auth", "session", "sessionid", "code",
}


def _has_control(text: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)


def _decode_layers(text: str, max_layers: int = 3) -> list[str]:
    """按百分号编码逐层解码；两端统一最多 3 层。"""
    layers = [text]
    current = text
    for _ in range(max_layers):
        decoded = unquote(current)
        if decoded == current:
            break
        layers.append(decoded)
        current = decoded
    return layers


def _has_dotdot(text: str) -> bool:
    for variant in _decode_layers(text, 3):
        for part in re.split(r"[\\/]+", variant):
            if part == "..":
                return True
    return False


def _mentions_private_token(text: str) -> bool:
    """单个文本层里的 private 词元/路径组件判断（不再递归解码）。"""
    for token in _TOKEN_SPLIT_RE.split(text):
        token = token.strip(".-~!?*…·")
        if not token:
            continue
        for part in re.split(r"[\\/]+", token):
            if part.strip(".:").lower() == "private":
                return True
    return False


def _mentions_private(text: str) -> bool:
    """private 作为路径组件出现（含大小写、反斜线、最多 3 层百分号编码）。

    单独一个 ``private`` 词元也按组件处理：宁可少显示，不把 private 引用带进页面。
    """
    return any(_mentions_private_token(variant) for variant in _decode_layers(text, 3))


def _has_private_path_context(text: str) -> bool:
    """JS 更严格的路径上下文：中文无空格前缀、Markdown target 的 ?/#、/private?query。"""
    normalized = text.replace("\\", "/")
    return bool(
        _PRIVATE_PATH_CONTEXT_RE.search(normalized)
        or _PRIVATE_MD_TARGET_RE.search(normalized)
        or _PRIVATE_QUERY_PATH_RE.search(normalized)
    )


def _is_sensitive_component(name: str) -> bool:
    lower = name.lower()
    if lower in _SENSITIVE_COMPONENTS or lower.startswith(".env"):
        return True
    return lower.endswith(_SENSITIVE_SUFFIXES)


def _looks_sensitive_text(text: str, *, decode_private: bool = True) -> bool:
    """自由文本的有限敏感规则；命中整段屏蔽。

    ``decode_private=False`` 用于已经由 ``_sensitive_candidates`` 展开过编码层的文本，
    避免在候选循环里继续递归解码。
    """
    if _PEM_RE.search(text) or _AUTH_RE.search(text) or _ASSIGN_RE.search(text):
        return True
    if any(pattern.search(text) for pattern in _CREDENTIAL_RES):
        return True
    if _has_private_path_context(text):
        return True
    if decode_private:
        if _mentions_private(text):
            return True
    elif _mentions_private_token(text):
        return True
    return bool(_CODE_RE.search(text) or _COMMAND_RE.search(text))


def _strip_markdown_links(text: str) -> str:
    """保留安全可见标签，丢掉链接目标（目标已在原文敏感检查里过一遍）。"""
    text = _MD_IMAGE_RE.sub(lambda m: m.group(1), text)
    text = _MD_LINK_RE.sub(lambda m: m.group(1), text)
    text = _MD_REF_RE.sub(lambda m: m.group(1), text)
    text = _MD_AUTOLINK_RE.sub(lambda m: m.group(1), text)
    return text


def _normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", " ").replace("\r", " ")
    text = re.sub(r"[\t\v\f\u2028\u2029]", " ", text)
    # 普通展示也统一成空格；零宽/双向控制同样不直接删除，避免把词悄悄拼在一起。
    text = _ZERO_WIDTH_RE.sub(" ", text)
    text = _CONTROL_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _remove_controls(text: str) -> str:
    """敏感检测专用：移除控制/零宽字符，防拆词绕过；不用于普通展示。"""
    return _REMOVE_CONTROLS_RE.sub("", text)


def _deobfuscated(text: str) -> str:
    """检测用变体：去掉控制/零宽字符并最多解 3 层百分号编码。"""
    text = _remove_controls(text)
    for _ in range(3):
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    return text


def _sensitive_candidates(text: str) -> list[str]:
    """原文 + 最多 3 层解码 + 归一/移除控制变体；每条候选只扫描一次。"""
    candidates: list[str] = []
    seen: set[str] = set()
    for layer in _decode_layers(text, 3):
        for variant in (layer, _remove_controls(layer), _normalize_whitespace(layer)):
            if variant and variant not in seen:
                seen.add(variant)
                candidates.append(variant)
    return candidates


def _looks_sensitive_any(text: str) -> bool:
    return any(_looks_sensitive_text(variant, decode_private=False)
               for variant in _sensitive_candidates(text))


# ---------------------------------------------------------------- safe_text

def safe_text(value: object, *, limit: int = SAFE_TEXT_LIMIT) -> str | None:
    """把白名单字段/自由文本投影成安全的一行纯文本；敏感或不可展示时返回 None。

    - 只接受 str / int / float；None、bool、dict、list 等返回 None（不随意 str）。
      bool 一律拒绝：Python 侧不跟随 JS 接受 bool 的宽松行为，跨语言统一由父会话裁定。
    - 先对**完整原文**做敏感检查，再限长；命中 private 组件、PEM、
      Authorization/Bearer、token/password/secret/api-key 赋值、可识别凭据、
      代码块或命令链时整段返回 None（不返回原值）。
    - 换行、U+2028/U+2029、控制字符归一为空格；Markdown 链接只留可见标签。
    - 超过 ``limit`` 个码点时截断并追加 ``［已截断］``（结果最长 limit+标记长度）。
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit 必须是正整数")
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value = str(value)
    elif not isinstance(value, str):
        return None

    raw = value
    if len(raw) > MAX_SAFE_TEXT_RAW:
        return None
    # 先对完整原文（原文/最多 3 层解码/归一/移控制候选）做敏感检查，再限长。
    if _looks_sensitive_any(raw):
        return None

    text = _normalize_whitespace(_strip_markdown_links(raw))
    if not text:
        return None
    if _looks_sensitive_any(text):
        return None
    if len(text) > limit:
        text = text[:limit].rstrip() + TRUNCATION_MARKER
    return text or None


# ---------------------------------------------------------------- 路径规则

def _abs_path_text(text: str) -> bool:
    return bool(re.match(r"^(?:[A-Za-z]:[\\/]|/)", text)) and not text.startswith(("\\\\", "//"))


def _split_relative(raw: str) -> list[str] | None:
    """词法校验并切分白名单相对路径；任何可疑形态返回 None。"""
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        return None
    if len(raw) > MAX_RELATIVE_PATH:
        return None
    if _has_control(raw):
        return None
    if raw.startswith(("/", "\\")):
        return None
    if re.match(r"^[A-Za-z]:", raw):
        return None
    if ":" in raw or re.search(r"[\\/]{2}", raw):
        return None
    parts: list[str] = []
    for part in re.split(r"[\\/]+", raw):
        if part == "" or part == ".":
            if part == "":
                return None
            continue
        if part == "..":
            return None
        decoded = unquote(part)
        if decoded != part:
            if _has_control(decoded) or re.search(r"[\\/:]", decoded):
                return None
            if decoded in (".", ".."):
                return None
        if part.lower() == "private" or decoded.lower() == "private":
            return None
        parts.append(part)
    return parts or None


def _shape_ok(kind: str, parts: list[str]) -> bool:
    lower = [part.lower() for part in parts]
    if kind == "pending":
        return lower == ["runs", "pending.md"]
    if kind == "daily":
        if len(parts) != 3 or lower[:2] != ["plans", "daily"]:
            return False
        match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})\.md", lower[2])
        if not match:
            return False
        try:
            date.fromisoformat("-".join(match.groups()))
        except ValueError:
            return False
        return True
    if kind == "run":
        if len(parts) != 2 or lower[0] != "runs":
            return False
        name = lower[1]
        return (name.endswith(".md") and len(name) > 3
                and not name.startswith(".") and name not in ("pending.md", "index.md", "replies.md"))
    if kind == "replies":
        return lower == ["runs", "replies.md"]
    if kind == "device":
        return (len(parts) == 3 and lower[:2] == ["registry", "devices"]
                and bool(re.fullmatch(r"[0-9a-f]{6,64}\.yaml", lower[2])))
    if kind == "role":
        return (len(parts) == 2 and lower[0] == "agents"
                and lower[1] in {f"{role}.md" for role in ROLE_IDS})
    if kind == "review":
        if len(parts) != 5 or lower[0] != "docs" or lower[1] != "delivery" or lower[3] != "reviews":
            return False
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", parts[2]):
            return False
        return bool(re.fullmatch(r"p\d{1,3}-r1\.md", lower[4]))
    if kind == "experience":
        if len(parts) != 3 or lower[:2] != ["inbox", "experience"]:
            return False
        name = parts[2]
        if len(name) > 125 or name.startswith("."):
            return False
        return bool(re.fullmatch(r"[^\\/:*?\"<>|\x00-\x1f]+\.json", name, re.IGNORECASE))
    return False


def _resolve_root(root: Path) -> Path | None:
    try:
        base = Path(root)
        if not base.is_absolute() or not base.is_dir():
            return None
        return base.resolve(strict=True)
    except (OSError, RuntimeError, ValueError, TypeError):
        return None


def _is_within(child: Path, parent: Path) -> bool:
    """按 Path 包含关系判断（大小写不敏感），不用字符串前缀比较。"""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        pass
    except (OSError, TypeError, AttributeError):
        return False
    try:
        parent_parts = [part.casefold() for part in parent.parts]
        child_parts = [part.casefold() for part in child.parts]
    except (TypeError, AttributeError):
        return False
    return child_parts[:len(parent_parts)] == parent_parts


def _has_private_part(path: Path) -> bool:
    return any(part.casefold() == "private" for part in path.parts)


def _data_root(root: Path) -> tuple[Path, tuple[Path, ...]] | None:
    """解析数据根并收集根下 ``private``（任意大小写）的真实目标。

    只列举数据根的直接子项元数据，不递归读取 private 内容；根自身/祖先含
    private 组件、或 private 目标无法解析（权限不足、悬空链接）时返回 None，
    由调用方保守拒绝。不声称隔离恶意并发替换（TOCTOU 属独立风险）。
    """
    base = _resolve_root(root)
    if base is None:
        return None
    try:
        lexical = Path(root)
    except TypeError:
        return None
    if not lexical.is_absolute():
        return None
    if _has_private_part(lexical) or _has_private_part(base):
        return None
    targets: list[Path] = []
    try:
        with os.scandir(base) as entries:
            for entry in entries:
                if entry.name.casefold() != "private":
                    continue
                try:
                    if entry.is_dir(follow_symlinks=True):
                        targets.append(Path(entry.path).resolve(strict=True))
                    elif entry.is_symlink():
                        # 链接目标不是可确认的目录（悬空/不可读）：保守拒绝。
                        return None
                except (OSError, RuntimeError, ValueError):
                    return None
    except OSError:
        return None
    return base, tuple(targets)


def _relative_parts(resolved: Path, base: Path) -> list[str] | None:
    try:
        return list(resolved.relative_to(base).parts)
    except ValueError:
        return None


def checked_source(root: Path, relative: str, kind: str) -> Path | None:
    """按白名单形状返回 root 内一个已解析的真实文件；任何越界/可疑形态返回 None。

    kind ∈ ``SOURCE_KINDS``。先词法拒绝绝对替代、``..``、UNC/设备路径、NUL/控制字符、
    Windows ADS、百分号编码混淆与 private 组件，再 ``resolve(strict=True)`` 复核：
    目标必须是 root 内文件、形状不变、真实路径不含 private，且不在数据根/祖先或
    根下 ``private``（任意大小写）的真实目标之内。
    只做指定目标的元数据定位，不递归读取 private 目录内容；无法确认时保守拒绝。
    """
    if kind not in SOURCE_KINDS:
        return None
    parts = _split_relative(relative)
    if parts is None or not _shape_ok(kind, parts):
        return None

    boundary = _data_root(root)
    if boundary is None:
        return None
    base, private_targets = boundary
    candidate = base.joinpath(*parts)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_file():
        return None

    resolved_parts = _relative_parts(resolved, base)
    if not resolved_parts:
        return None
    if any(part.casefold() == "private" for part in resolved_parts):
        return None
    if any(_is_within(resolved, target) for target in private_targets):
        return None
    if not _shape_ok(kind, resolved_parts):
        return None
    return resolved


def display_path(root: Path, raw: str, *, base: Path | None = None) -> str | None:
    """把详情/产物路径投影成安全的绝对纯文本路径；可疑或被屏蔽时返回 None。

    相对路径按 ``base``（缺省 ``root``）解析；绝对本地路径原样规范化后展示（可位于
    数据根外，只展示不读取）。拒绝 private/敏感组件、UNC/设备路径、ADS、控制字符、
    ``..`` 与编码混淆。返回 ``as_posix()`` 形式。
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or text != raw or len(text) > MAX_DISPLAY_PATH:
        return None
    if _has_control(text) or "://" in text:
        return None
    if text.startswith(("\\\\", "//")):
        return None
    if ":" in text:
        if not re.match(r"^[A-Za-z]:[\\/]", text):
            return None
        if ":" in text[2:]:
            return None
    if re.match(r"^[A-Za-z]:(?![\\/])", text):
        return None
    if _has_dotdot(text) or _mentions_private(text):
        return None
    decoded = unquote(unquote(text))
    if decoded != text and (_has_dotdot(decoded) or _mentions_private(decoded)):
        return None

    if _abs_path_text(text):
        candidate = Path(text)
    else:
        try:
            base_dir = Path(base) if base is not None else Path(root)
        except TypeError:
            return None
        if not base_dir.is_absolute():
            return None
        candidate = base_dir / text
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_absolute():
        return None
    if _mentions_private(str(resolved)):
        return None
    if any(_is_sensitive_component(part) for part in resolved.parts):
        return None
    return resolved.as_posix()


# ---------------------------------------------------------------- 体验链接

def _encoded_separator(text: str) -> bool:
    lower = text.lower()
    return any(token in lower for token in ("%00", "%2f", "%5c", "%0a", "%0d"))


def _unsafe_query(raw: str) -> bool:
    if not raw:
        return False
    if _encoded_separator(raw) or _has_control(raw):
        return True
    if _looks_sensitive_any(unquote(raw)):
        return True
    try:
        pairs = parse_qsl(raw, keep_blank_values=True)
    except ValueError:
        return True
    return any(name.strip().lower() in _SENSITIVE_PARAM_NAMES for name, _ in pairs)


def _file_target(parts) -> Path | None:
    path = parts.path
    if not path or path.startswith("//") or _encoded_separator(path) or _has_control(path):
        return None
    decoded = unquote(path)
    if _has_control(decoded) or _has_dotdot(decoded) or _mentions_private(decoded):
        return None
    for component in re.split(r"[\\/]+", decoded):
        if _is_sensitive_component(component.strip()):
            return None
    local = None
    if re.match(r"^/[A-Za-z]:[\\/]", decoded):
        local = decoded[1:]
    else:
        try:
            local = url2pathname(decoded)
        except (OSError, ValueError):
            return None
    if not local or (not _abs_path_text(local) and not Path(local).is_absolute()):
        return None
    if ":" in local and not re.match(r"^[A-Za-z]:[\\/]", local):
        return None
    if ":" in local[2:]:
        return None
    try:
        resolved = Path(local).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_file():
        return None
    if _mentions_private(str(resolved)):
        return None
    if any(_is_sensitive_component(part) for part in resolved.parts):
        return None
    return resolved


def safe_experience_url(raw: str, *, root: Path, artifact_roots: tuple[Path, ...] = ()) -> str | None:
    """校验体验链接，返回可交给用户点击的 file/loopback URL；不探测、不加载。

    - ``file:`` 只接受本地绝对文件路径，规范化并解析后必须位于数据根或
      ``artifact_roots``（适配器从允许记录的明确产物关联产生）内；拒绝 UNC/设备路径、
      authority、ADS、``..``、编码混淆、private/敏感组件。无害 query/fragment
      （如 ``?view=summary#section-2``）保留，敏感参数名或疑似凭据整条拒绝。
    - ``http(s)`` 只接受 localhost / 127.0.0.1 / ::1，无 userinfo、无编码主机；
      无害 query/fragment 保留，敏感参数名或疑似凭据整条拒绝。
    - 其他协议、协议相对地址、控制字符、空格、反斜线一律返回 None。
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or text != raw or len(text) > MAX_URL_LENGTH:
        return None
    if _has_control(text) or " " in text or "\\" in text:
        return None
    if text.startswith("//") or "://" not in text:
        return None
    if _has_dotdot(text) or _looks_sensitive_any(text):
        return None
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    scheme = parts.scheme.lower()

    if scheme in ("http", "https"):
        if parts.username is not None or parts.password is not None or "@" in parts.netloc:
            return None
        if not parts.netloc or not parts.netloc.isascii() or "%" in parts.netloc:
            return None
        try:
            host = parts.hostname
            parts.port  # 非法端口会抛 ValueError
        except ValueError:
            return None
        if host is None or host != host.lower() or host.endswith("."):
            return None
        if host not in ("localhost", "127.0.0.1", "::1"):
            return None
        path = parts.path or "/"
        if _encoded_separator(path) or _has_control(path):
            return None
        decoded_path = unquote(path)
        if _has_control(decoded_path) or _has_dotdot(decoded_path) or _mentions_private(decoded_path):
            return None
        if any(_is_sensitive_component(part.strip()) for part in re.split(r"[\\/]+", decoded_path)):
            return None
        if _unsafe_query(parts.query) or _unsafe_query(parts.fragment):
            return None
        if _looks_sensitive_any(unquote(parts.query)) or _looks_sensitive_any(unquote(parts.fragment)):
            return None
        return urlunsplit((scheme, parts.netloc, path, parts.query, parts.fragment))

    if scheme == "file":
        if parts.netloc:
            return None
        # 计划 178-180：两类 URI 都保留可判定的无害 query/fragment，敏感参数拒绝。
        if _unsafe_query(parts.query) or _unsafe_query(parts.fragment):
            return None
        target = _file_target(parts)
        if target is None:
            return None

        roots: list[Path] = []
        boundary = _data_root(root)
        if boundary is None:
            return None
        base, private_targets = boundary
        if any(_is_within(target, private_target) for private_target in private_targets):
            return None
        roots.append(base)
        try:
            home = Path.home().resolve(strict=False)
        except (OSError, RuntimeError):
            home = None
        for extra in artifact_roots or ():
            try:
                extra_path = Path(extra)
                if not extra_path.is_absolute():
                    continue
                resolved_extra = extra_path.resolve(strict=True)
            except (OSError, RuntimeError, ValueError, TypeError):
                continue
            if not resolved_extra.is_dir() or resolved_extra.parent == resolved_extra:
                continue
            if home is not None and resolved_extra == home:
                continue
            if _mentions_private(str(resolved_extra)):
                continue
            roots.append(resolved_extra)
        if not any(_is_within(target, allowed) for allowed in roots):
            return None
        url = target.as_uri()
        if parts.query:
            url = f"{url}?{parts.query}"
        if parts.fragment:
            url = f"{url}#{parts.fragment}"
        return url

    return None


# ---------------------------------------------------------------- JSON 内嵌

def inline_json(snapshot: dict) -> str:
    """序列化快照供 ``<script type="application/json">`` 内嵌。

    ``json.dumps`` 后把 ``<`` ``>`` ``&`` 与 U+2028/U+2029 转成 Unicode 转义，
    大小写形式的 ``</script>`` 都无法终止数据节点；不做占位符二次替换。
    """
    if not isinstance(snapshot, dict):
        raise TypeError("snapshot 必须是 dict")
    text = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    escapes = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026",
               "\u2028": "\\u2028", "\u2029": "\\u2029"}
    return "".join(escapes.get(ch, ch) for ch in text)


# ---------------------------------------------------------------- v1.1 回复：卡片键、输入校验、指令语法

_PENDING_CARD_RE = re.compile(r"N\d{4}-\d+")
_MAX_CARD_KEY = 300


def card_key(page: str, ident: str | None = None) -> str:
    """卡片键：``pending:<编号>``、``run:<运行 id>``、``dispatch``、``roles``。"""
    if page == "pending":
        return f"pending:{ident}"
    if page == "runs":
        return f"run:{ident}"
    if page in ("dispatch", "roles"):
        return page
    raise ValueError("未知页面")


def parse_card_key(key: object) -> tuple[str, str | None] | None:
    """卡片键 → ``(页, id)``；不合法返回 None（不回显原值）。"""
    if not isinstance(key, str) or not key or len(key) > _MAX_CARD_KEY or _has_control(key):
        return None
    if key in ("dispatch", "roles"):
        return key, None
    if key.startswith("pending:"):
        ident = key[len("pending:"):]
        return ("pending", ident) if _PENDING_CARD_RE.fullmatch(ident) else None
    if key.startswith("run:"):
        ident = key[len("run:"):]
        if not ident or any(ch.isspace() for ch in ident) or ".." in ident.split("/"):
            return None
        return "runs", ident
    return None


def reply_status_of(cell: object) -> tuple[str, str | None]:
    """分表「状态」格 → ``(枚举, 说明)``；认不出返回 ``("unknown", None)``。

    例：``转不到：接收方会话不在了`` → ``("lost", "接收方会话不在了")``。
    """
    text = str(cell or "").strip()
    for status, label in REPLY_STATUS_LABELS.items():
        if text.startswith(label):
            rest = text[len(label):].strip()
            if rest.startswith(("：", ":")):
                rest = rest[1:].strip()
            return status, (rest or None)
    return "unknown", None


def reply_limits() -> dict:
    """页面要知道的上限（写进 console-channel 节点的 limits）。"""
    return {"maxChars": REPLY_MAX_CHARS, "perMinute": REPLY_PER_MINUTE,
            "dedupeSeconds": REPLY_DEDUPE_SECONDS, "pollSeconds": REPLY_POLL_SECONDS,
            "maxLines": REPLY_MAX_LINES}


def utf8_ok(value: object) -> bool:
    """字符串能不能严格编成 UTF-8（JSON 里转义的孤立代理码点如 \\ud800 编不了，写表会失败）；递归查 list / dict。"""
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return True
    if isinstance(value, list):
        return all(utf8_ok(v) for v in value)
    if isinstance(value, dict):
        return all(utf8_ok(k) and utf8_ok(v) for k, v in value.items())
    return True


def validate_reply_input(slot: object, quick: object, text: object) -> str | None:
    """一张卡片上的回复能不能发：能发返回 None，否则返回 ``REPLY_ERROR_CODES`` 里的 code。

    与 core.js ``validateReply`` 同口径（页面先拦，通道再按这里复核，绕过页面也拦得住）。
    正文按去掉首尾空白后的 Unicode 码点计数。
    """
    if not isinstance(slot, dict):
        return "bad_card"
    if slot.get("allowed") is not True:
        return "other_device" if slot.get("reason") == "other_device" else "not_replyable"
    if text is None:
        text = ""
    if not isinstance(text, str) or not utf8_ok(text):
        return "bad_json"
    body = text.strip()
    if len(body) > REPLY_MAX_CHARS:
        return "too_long"
    if quick is not None:
        ids = [c.get("id") for c in slot.get("choices") or [] if isinstance(c, dict)]
        if not isinstance(quick, str) or quick not in ids or quick in (slot.get("pageOnly") or []):
            return "bad_choice"
    if quick is None and not body:
        return "empty"
    if quick in (slot.get("needText") or []) and not body:
        return "need_text"
    if body and safe_text(body) is None:
        return "sensitive"
    return None


_COMMAND_DISPATCH_RE = re.compile(
    r"【控制台指令】确认派发：(?P<project>.+?)：(?P<task>\S+) (?P<title>.+?) · "
    r"(?:(?P<team>开发交付流水线)|角色 (?P<role>\S+) · 执行器 (?P<executor>Claude Code|Codex|OpenCode)"
    r" · (?P<mode>新建工作区|现有目录|新终端窗口))")
_COMMAND_ROLE_RE = re.compile(
    r"【控制台指令】角色：(?P<name>.+?) → "
    r"(?:转正（改为 active）|换主装备 → (?:Claude Code|Codex|OpenCode)|修改意见：.+)")
_MAX_COMMAND_CHARS = 600


def _neutralize_line(text: str) -> str:
    """与 core.js ``neutralizeLine`` 同口径：导出时项目名、标题里的分隔符被这样中和。"""
    text = re.sub(r"【\s*控制台指令\s*】", "《控制台指令》", text)
    return text.replace("·", "、").replace("→", "->")


def check_console_command(line: object, snapshot: object) -> bool:
    """派发确认 / 角色管理一并发送的指令行是否合法：语法对、引用的任务 / 角色在当前快照里。

    只接受 core.js ``exportLines('dispatch'|'roles')`` 产出的两种格式；夹带别的前缀、
    换行、伪造的消息行、敏感内容、引用不存在或不可进入的任务一律 False。
    """
    if not isinstance(line, str) or not isinstance(snapshot, dict):
        return False
    if not line or len(line) > _MAX_COMMAND_CHARS or _has_control(line):
        return False
    if line.count("【控制台指令】") != 1 or not line.startswith("【控制台指令】") or "【assistant" in line:
        return False
    if safe_text(line, limit=_MAX_COMMAND_CHARS) is None:
        return False
    match = _COMMAND_DISPATCH_RE.fullmatch(line)
    if match:
        role = match.group("role")
        if role is not None and role != "未指定" and role not in ROLE_LABELS.values():
            return False
        want_kind = "delivery" if match.group("team") else "single"
        for task in snapshot.get("dispatchTasks") or []:
            if not isinstance(task, dict):
                continue
            if (task.get("taskNo") == match.group("task") and task.get("kind") == want_kind
                    and task.get("selectable") is True
                    and isinstance(task.get("project"), str)
                    and _neutralize_line(task["project"]) == match.group("project")):
                return True
        return False
    match = _COMMAND_ROLE_RE.fullmatch(line)
    if match:
        for role in snapshot.get("roles") or []:
            if isinstance(role, dict) and isinstance(role.get("name"), str) \
                    and _neutralize_line(role["name"]) == match.group("name"):
                return True
    return False


# ---------------------------------------------------------------- 快照校验

_SNAPSHOT_KEYS = ("schemaVersion", "generatedAt", "localDate", "environment", "stats",
                  "pending", "dispatchTasks", "runs", "roles", "diagnostics")
_ENVIRONMENT_KEYS = ("inOrca", "executorsAvailable")
_STATS_KEYS = ("pendingCount", "runningCount", "completedToday", "dispatchedToday",
               "completedWeek", "clearStreak")
_PENDING_KEYS = ("id", "type", "status", "column", "project", "item", "title", "summary",
                 "roleId", "senderName", "createdAt", "detailPath", "runId", "choices",
                 "processedAt", "processedNote", "experience", "sources", "diagnostics")
_DISPATCH_KEYS = ("id", "taskNo", "project", "title", "summary", "planPath", "roleId",
                  "declaredExecutor", "changesCode", "kind", "prdPath", "difficulty",
                  "stages", "outputs", "defaultExecutor", "defaultMode", "defaultReason",
                  "fallback", "selectable", "disabledReason", "sources")
_RUN_KEYS = ("id", "kind", "project", "item", "status", "rawStatus", "roleId", "createdAt",
             "completedAt", "lastChangedAt", "stale", "stages", "agents", "packages",
             "progress", "decisions", "pendingIds", "detailPath", "firstReview", "sources",
             "diagnostics")
_ROLE_KEYS = ("id", "name", "status", "executors", "skills", "useWhen", "avatarKey",
              "completed", "firstPass", "activeSessions", "level", "levelProgress",
              "isNew", "sources")
_EXPERIENCE_KEYS = ("ui", "state", "url", "recordedAt", "expiresAt", "tests", "focus", "sources")
_METRIC_KEYS = ("value", "sourceIds", "reason")
_SOURCE_KEYS = ("path", "lineStart", "lineEnd", "field")
_DIAGNOSTIC_KEYS = ("code", "message", "source")
_CHOICE_KEYS = ("id", "label", "source")
_STAGE_KEYS = ("label", "state", "source")
_AGENT_KEYS = ("id", "roleId", "executor", "packageId", "activity", "state", "lastChangedAt")
_PACKAGE_KEYS = ("id", "state", "dependencies", "reviewRound", "updatedAt", "firstReview")
_REVIEW_KEYS = ("round", "verdict", "source")
_LEVEL_KEYS = ("current", "span", "nextRemaining")
_TESTS_KEYS = ("passed", "total", "summary")


def _is_str(value) -> bool:
    return isinstance(value, str)


def _is_nonempty_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_bool(value) -> bool:
    return isinstance(value, bool)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_opt_int(value) -> bool:
    return value is None or _is_int(value)


def _is_opt_str(value) -> bool:
    return value is None or isinstance(value, str)


def _is_list(value) -> bool:
    return isinstance(value, list)


def _is_opt_bool(value) -> bool:
    return value is None or isinstance(value, bool)


def _is_abs_path(value) -> bool:
    return isinstance(value, str) and _abs_path_text(value) and not _has_control(value)


def _in(value, allowed) -> bool:
    return value in allowed


def _need(errors: list[str], path: str, obj: dict, keys) -> None:
    for key in keys:
        if key not in obj:
            errors.append(f"{path}.{key} 缺失")


def _expect(errors: list[str], path: str, obj: dict, key: str, check, reason: str) -> None:
    if key not in obj:
        return
    if not check(obj[key]):
        errors.append(f"{path}.{key} {reason}")


_DATE_ONLY_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")
_ISO_TIME_RE = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.([0-9]{1,9}))?(Z|[+-][0-9]{2}:[0-9]{2})$"
)
_OFFSET_RE = re.compile(r"^[+-][0-9]{2}:[0-9]{2}$")


def _valid_calendar_date(year: str, month: str, day: str) -> bool:
    try:
        date(int(year), int(month), int(day))
    except ValueError:
        return False
    return True


def _valid_date_text(value: str) -> bool:
    """严格 ``YYYY-MM-DD``；不接受 ``date.fromisoformat`` 的周日期等扩展形式。"""
    match = _DATE_ONLY_RE.fullmatch(value)
    return bool(match) and _valid_calendar_date(*match.groups())


def _valid_offset(text: str) -> bool:
    """ISO 8601 UTC offset：``Z`` 或 ``±HH:MM``（数字仅 ASCII，时分在合法范围）。"""
    if text == "Z":
        return True
    if _OFFSET_RE.fullmatch(text) is None:
        return False
    hours, minutes = int(text[1:3]), int(text[4:6])
    if hours > 23 or minutes > 59:
        return False
    try:
        timezone(timedelta(hours=hours, minutes=minutes))
    except (ValueError, OverflowError):
        return False
    return True


def _check_time(errors: list[str], path: str, value, *, offset_required: bool = False) -> None:
    """先校验明确格式，再校验有效日历/offset；诊断只含字段路径，不回显原值。

    - 日期字段：严格 ``YYYY-MM-DD``（``offset_required`` 时不允许）。
    - 精确时间：``YYYY-MM-DDTHH:MM:SS`` + 可选 1-9 位小数秒 + ``Z``/``±HH:MM``。
      拒绝重复秒/多余冒号、缺 offset、空格分隔、基本格式与非法日期/时分秒/offset。
    - ``null`` 表示未知，直接通过。
    """
    if value is None:
        return
    if not isinstance(value, str) or _has_control(value):
        errors.append(f"{path} 必须是 ISO 时间字符串或 null")
        return
    match = _ISO_TIME_RE.fullmatch(value)
    if match is None:
        if _valid_date_text(value):
            if offset_required:
                errors.append(f"{path} 必须带 UTC offset")
            return
        errors.append(f"{path} 必须是 ISO 时间字符串或 null")
        return
    year, month, day, hour, minute, second, _fraction, offset = match.groups()
    try:
        datetime(int(year), int(month), int(day), int(hour), int(minute), int(second))
    except ValueError:
        errors.append(f"{path} 必须是 ISO 时间字符串或 null")
        return
    if not _valid_offset(offset):
        errors.append(f"{path} 必须是 ISO 时间字符串或 null")


def _check_source(errors: list[str], path: str, value) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return
    _need(errors, path, value, _SOURCE_KEYS)
    _expect(errors, path, value, "path", _is_abs_path, "必须是绝对本地路径")
    _expect(errors, path, value, "field", _is_str, "必须是字符串")
    for key in ("lineStart", "lineEnd"):
        _expect(errors, path, value, key, lambda v: _is_int(v) and v >= 1, "必须是正整数")
    start, end = value.get("lineStart"), value.get("lineEnd")
    if _is_int(start) and _is_int(end) and end < start:
        errors.append(f"{path}.lineEnd 不能小于 lineStart")


def _check_sources(errors: list[str], path: str, value) -> None:
    if not isinstance(value, list):
        errors.append(f"{path} 必须是数组")
        return
    for index, item in enumerate(value):
        _check_source(errors, f"{path}[{index}]", item)


def _check_diagnostics(errors: list[str], path: str, value) -> None:
    if not isinstance(value, list):
        errors.append(f"{path} 必须是数组")
        return
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_path} 必须是对象")
            continue
        _need(errors, item_path, item, _DIAGNOSTIC_KEYS)
        _expect(errors, item_path, item, "code", _is_nonempty_str, "必须是非空字符串")
        _expect(errors, item_path, item, "message", _is_nonempty_str, "必须是非空字符串")
        _expect(errors, item_path, item, "source", _is_opt_str, "必须是字符串或 null")


def _check_metric(errors: list[str], path: str, value) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return
    _need(errors, path, value, _METRIC_KEYS)
    _expect(errors, path, value, "value", lambda v: _is_opt_int(v) and (v is None or v >= 0),
            "必须是非负整数或 null")
    _expect(errors, path, value, "sourceIds", _is_list, "必须是数组")
    if isinstance(value.get("sourceIds"), list):
        for index, item in enumerate(value["sourceIds"]):
            if not _is_nonempty_str(item):
                errors.append(f"{path}.sourceIds[{index}] 必须是非空字符串")
    _expect(errors, path, value, "reason", _is_opt_str, "必须是字符串或 null")


def _check_choice(errors: list[str], path: str, value) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return
    _need(errors, path, value, _CHOICE_KEYS)
    _expect(errors, path, value, "id", _is_nonempty_str, "必须是非空字符串")
    _expect(errors, path, value, "label", _is_nonempty_str, "必须是非空字符串")
    _expect(errors, path, value, "source", _is_str, "必须是字符串")


def _check_experience(errors: list[str], path: str, value) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return
    _need(errors, path, value, _EXPERIENCE_KEYS)
    _expect(errors, path, value, "ui", lambda v: _in(v, EXPERIENCE_UI_STATES), "不是允许的枚举值")
    _expect(errors, path, value, "state", lambda v: _in(v, EXPERIENCE_STATES), "不是允许的枚举值")
    _expect(errors, path, value, "url", _is_opt_str, "必须是字符串或 null")
    for key in ("recordedAt", "expiresAt"):
        _check_time(errors, f"{path}.{key}", value.get(key))
    _expect(errors, path, value, "focus", _is_list, "必须是数组")
    if isinstance(value.get("focus"), list):
        for index, item in enumerate(value["focus"]):
            if not _is_str(item):
                errors.append(f"{path}.focus[{index}] 必须是字符串")
    _check_sources(errors, f"{path}.sources", value.get("sources"))
    tests = value.get("tests")
    if tests is not None:
        tests_path = f"{path}.tests"
        if not isinstance(tests, dict):
            errors.append(f"{tests_path} 必须是对象或 null")
        else:
            _need(errors, tests_path, tests, _TESTS_KEYS)
            for key in ("passed", "total"):
                _expect(errors, tests_path, tests, key, lambda v: _is_int(v) and v >= 0,
                        "必须是非负整数")
            _expect(errors, tests_path, tests, "summary", _is_str, "必须是字符串")
            passed, total = tests.get("passed"), tests.get("total")
            if _is_int(passed) and _is_int(total) and passed > total:
                errors.append(f"{tests_path}.passed 不能大于 total")


def _check_environment(errors: list[str], path: str, value) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return
    _need(errors, path, value, _ENVIRONMENT_KEYS)
    _expect(errors, path, value, "inOrca", _is_bool, "必须是布尔值")
    _expect(errors, path, value, "executorsAvailable", _is_list, "必须是数组")
    if isinstance(value.get("executorsAvailable"), list):
        for index, item in enumerate(value["executorsAvailable"]):
            if item not in EXECUTOR_IDS:
                errors.append(f"{path}.executorsAvailable[{index}] 不是允许的执行器")
    device = value.get("device")
    if device is not None:
        dpath = f"{path}.device"
        if not isinstance(device, dict):
            errors.append(f"{dpath} 必须是对象或 null")
        else:
            _need(errors, dpath, device, ("id", "name"))
            _expect(errors, dpath, device, "id", _is_device_id, "必须是设备指纹")
            _expect(errors, dpath, device, "name", _is_opt_str, "必须是字符串或 null")


_DEVICE_ID_RE = re.compile(r"[0-9a-f]{6,64}")
_REPLY_SLOT_KEYS = ("card", "allowed", "reason", "kind", "receiver", "association",
                    "choices", "needText", "pageOnly", "device")
_REPLY_KEYS = ("id", "createdAt", "card", "page", "item", "association", "quick", "text",
               "status", "statusNote", "forwardedTo", "ack", "device")
_REPLY_DEVICE_KEYS = ("id", "name", "local")


def _is_device_id(value) -> bool:
    return isinstance(value, str) and bool(_DEVICE_ID_RE.fullmatch(value))


def _check_reply_device(errors: list[str], path: str, value) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return
    _need(errors, path, value, _REPLY_DEVICE_KEYS)
    _expect(errors, path, value, "id", lambda v: v is None or _is_device_id(v), "必须是设备指纹或 null")
    _expect(errors, path, value, "name", _is_opt_str, "必须是字符串或 null")
    _expect(errors, path, value, "local", _is_opt_bool, "必须是布尔值或 null")
    if value.get("id") is None and value.get("local") is not None:
        errors.append(f"{path}.local 设备未记录时必须是 null")


def _check_reply_slot(errors: list[str], path: str, value, *, page: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象或 null")
        return
    _need(errors, path, value, _REPLY_SLOT_KEYS)
    parsed = parse_card_key(value.get("card"))
    if "card" in value and (parsed is None or parsed[0] != page):
        errors.append(f"{path}.card 不是这一页的卡片键")
    _expect(errors, path, value, "allowed", _is_bool, "必须是布尔值")
    _expect(errors, path, value, "reason", lambda v: v is None or _in(v, REPLY_REASONS),
            "必须是 null 或允许的原因")
    _expect(errors, path, value, "kind", lambda v: _in(v, REPLY_KINDS), "不是允许的枚举值")
    _expect(errors, path, value, "receiver", _is_nonempty_str, "必须是非空字符串")
    _expect(errors, path, value, "association", _is_nonempty_str, "必须是非空字符串")
    choices = value.get("choices")
    ids: list = []
    if not isinstance(choices, list):
        errors.append(f"{path}.choices 必须是数组")
    else:
        for index, choice in enumerate(choices):
            _check_choice(errors, f"{path}.choices[{index}]", choice)
            if isinstance(choice, dict):
                ids.append(choice.get("id"))
    for key in ("needText", "pageOnly"):
        items = value.get(key)
        if not isinstance(items, list):
            errors.append(f"{path}.{key} 必须是数组")
            continue
        for index, item in enumerate(items):
            if item not in ids:
                errors.append(f"{path}.{key}[{index}] 不在快捷评价里")
    if "device" in value:
        _check_reply_device(errors, f"{path}.device", value["device"])


def _check_reply(errors: list[str], path: str, value) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return
    _need(errors, path, value, _REPLY_KEYS)
    _expect(errors, path, value, "id", lambda v: isinstance(v, str) and bool(_PENDING_CARD_RE.fullmatch(v)),
            "必须是 N<月日>-<序号>")
    _check_time(errors, f"{path}.createdAt", value.get("createdAt"))
    _expect(errors, path, value, "card", lambda v: v is None or parse_card_key(v) is not None,
            "必须是卡片键或 null")
    _expect(errors, path, value, "page", lambda v: _in(v, REPLY_PAGES), "不是允许的页")
    _expect(errors, path, value, "item", _is_str, "必须是字符串")
    for key in ("association", "quick", "statusNote", "forwardedTo"):
        _expect(errors, path, value, key, _is_opt_str, "必须是字符串或 null")
    _expect(errors, path, value, "text", _is_str, "必须是字符串")
    _expect(errors, path, value, "status", lambda v: _in(v, REPLY_STATUSES), "不是允许的枚举值")
    ack = value.get("ack")
    if ack is not None:
        if not isinstance(ack, dict):
            errors.append(f"{path}.ack 必须是对象或 null")
        else:
            _need(errors, f"{path}.ack", ack, ("at", "text"))
            _check_time(errors, f"{path}.ack.at", ack.get("at"))
            _expect(errors, f"{path}.ack", ack, "text", _is_str, "必须是字符串")
    if "device" in value:
        _check_reply_device(errors, f"{path}.device", value["device"])


def _check_stats(errors: list[str], path: str, value) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return
    _need(errors, path, value, _STATS_KEYS)
    for key in _STATS_KEYS:
        if key in value:
            _check_metric(errors, f"{path}.{key}", value[key])


def _check_pending(errors: list[str], path: str, value) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return
    _need(errors, path, value, _PENDING_KEYS)
    _expect(errors, path, value, "id", _is_nonempty_str, "必须是非空字符串")
    _expect(errors, path, value, "type", lambda v: _in(v, PENDING_TYPES), "不是允许的枚举值")
    _expect(errors, path, value, "status", lambda v: _in(v, PENDING_STATUSES), "不是允许的枚举值")
    _expect(errors, path, value, "column", lambda v: v is None or _in(v, PENDING_COLUMNS),
            "必须是 null 或允许的栏位")
    for key in ("project", "item", "title", "senderName"):
        _expect(errors, path, value, key, _is_str, "必须是字符串")
    _expect(errors, path, value, "summary", _is_str, "必须是字符串")
    _expect(errors, path, value, "roleId", lambda v: v is None or _in(v, ROLE_IDS),
            "必须是 null 或允许的角色")
    _expect(errors, path, value, "runId", _is_opt_str, "必须是字符串或 null")
    _expect(errors, path, value, "detailPath", lambda v: v is None or _is_abs_path(v),
            "必须是绝对本地路径或 null")
    _expect(errors, path, value, "processedNote", _is_opt_str, "必须是字符串或 null")
    for key in ("createdAt", "processedAt"):
        _check_time(errors, f"{path}.{key}", value.get(key))

    pending_type = value.get("type")
    column = value.get("column")
    allowed_columns = {"完成": (None, "待验收"), "要拍板": (None, "要拍板"),
                       "卡住": (None, "卡住"), "交接": (None,)}
    if pending_type in allowed_columns and column not in allowed_columns[pending_type]:
        errors.append(f"{path}.column 与 type 不匹配")

    choices = value.get("choices")
    if not isinstance(choices, list):
        errors.append(f"{path}.choices 必须是数组")
        choices = []
    for index, choice in enumerate(choices):
        _check_choice(errors, f"{path}.choices[{index}]", choice)
        if column == "待验收" and isinstance(choice, dict):
            if choice.get("id") not in ACCEPT_CHOICE_IDS:
                errors.append(f"{path}.choices[{index}].id 待验收只允许 accept/reject/later")

    experience = value.get("experience")
    if experience is not None:
        _check_experience(errors, f"{path}.experience", experience)
    _check_sources(errors, f"{path}.sources", value.get("sources"))
    _check_diagnostics(errors, f"{path}.diagnostics", value.get("diagnostics"))
    if "reply" in value:
        _check_reply_slot(errors, f"{path}.reply", value["reply"], page="pending")


def _check_dispatch(errors: list[str], path: str, value) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return
    _need(errors, path, value, _DISPATCH_KEYS)
    for key in ("id", "taskNo", "project", "title"):
        _expect(errors, path, value, key, _is_nonempty_str, "必须是非空字符串")
    _expect(errors, path, value, "summary", _is_opt_str, "必须是字符串或 null")
    _expect(errors, path, value, "planPath", _is_abs_path, "必须是绝对本地路径")
    _expect(errors, path, value, "roleId", lambda v: v is None or _in(v, ROLE_IDS),
            "必须是 null 或允许的角色")
    _expect(errors, path, value, "declaredExecutor",
            lambda v: v is None or _in(v, EXECUTOR_IDS), "必须是 null 或允许的执行器")
    _expect(errors, path, value, "changesCode", _is_opt_bool, "必须是布尔值或 null")
    _expect(errors, path, value, "kind", lambda v: _in(v, DISPATCH_KINDS), "不是允许的枚举值")
    _expect(errors, path, value, "prdPath", lambda v: v is None or _is_abs_path(v),
            "必须是绝对本地路径或 null")
    _expect(errors, path, value, "difficulty",
            lambda v: v is None or (_is_int(v) and v in (1, 2, 3)), "必须是 1/2/3 或 null")
    for key in ("stages", "outputs"):
        _expect(errors, path, value, key, _is_list, "必须是数组")
        if isinstance(value.get(key), list):
            for index, item in enumerate(value[key]):
                if not _is_str(item):
                    errors.append(f"{path}.{key}[{index}] 必须是字符串")
    _expect(errors, path, value, "defaultExecutor",
            lambda v: v is None or _in(v, EXECUTOR_IDS), "必须是 null 或允许的执行器")
    _expect(errors, path, value, "defaultMode",
            lambda v: v is None or _in(v, MODE_IDS), "必须是 null 或允许的模式")
    _expect(errors, path, value, "defaultReason", _is_str, "必须是字符串")
    for key in ("fallback", "selectable"):
        _expect(errors, path, value, key, _is_bool, "必须是布尔值")
    _expect(errors, path, value, "disabledReason", _is_opt_str, "必须是字符串或 null")
    _check_sources(errors, f"{path}.sources", value.get("sources"))

    kind = value.get("kind")
    selectable = value.get("selectable")
    if selectable is False and not _is_nonempty_str(value.get("disabledReason")):
        errors.append(f"{path}.disabledReason 不可组队时必须说明原因")
    if kind == "delivery":
        # 缺 PRD 路径时以 selectable:false + disabledReason 表达“信息不完整”；
        # 只有可组队的团队副本才强制安全绝对 PRD 路径。
        if selectable is True and not _is_abs_path(value.get("prdPath")):
            errors.append(f"{path}.prdPath 可组队的团队副本必须有绝对 PRD 路径")
        if value.get("difficulty") != 3:
            errors.append(f"{path}.difficulty 团队副本必须是 3")


def _check_stage(errors: list[str], path: str, value) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return
    _need(errors, path, value, _STAGE_KEYS)
    _expect(errors, path, value, "label", _is_nonempty_str, "必须是非空字符串")
    _expect(errors, path, value, "state", lambda v: _in(v, STAGE_STATES), "不是允许的枚举值")
    source = value.get("source")
    if not isinstance(source, str) and not isinstance(source, dict):
        errors.append(f"{path}.source 必须是字符串或来源对象")
    elif isinstance(source, dict):
        _check_source(errors, f"{path}.source", source)


def _check_review(errors: list[str], path: str, value) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象或 null")
        return
    _need(errors, path, value, _REVIEW_KEYS)
    _expect(errors, path, value, "round", lambda v: v == 1, "首轮审查 round 必须是 1")
    _expect(errors, path, value, "verdict", lambda v: _in(v, REVIEW_VERDICTS), "不是允许的枚举值")
    source = value.get("source")
    if not isinstance(source, str) and not isinstance(source, dict):
        errors.append(f"{path}.source 必须是字符串或来源对象")
    elif isinstance(source, dict):
        _check_source(errors, f"{path}.source", source)


def _check_agent(errors: list[str], path: str, value) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return
    _need(errors, path, value, _AGENT_KEYS)
    _expect(errors, path, value, "id", _is_nonempty_str, "必须是非空字符串")
    _expect(errors, path, value, "roleId", lambda v: v is None or _in(v, ROLE_IDS),
            "必须是 null 或允许的角色")
    _expect(errors, path, value, "executor", lambda v: v is None or _in(v, EXECUTOR_IDS),
            "必须是 null 或允许的执行器")
    _expect(errors, path, value, "packageId", _is_opt_str, "必须是字符串或 null")
    _expect(errors, path, value, "activity", _is_opt_str, "必须是字符串或 null")
    _expect(errors, path, value, "state", _is_nonempty_str, "必须是非空字符串")
    _check_time(errors, f"{path}.lastChangedAt", value.get("lastChangedAt"))


def _check_package(errors: list[str], path: str, value) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return
    _need(errors, path, value, _PACKAGE_KEYS)
    _expect(errors, path, value, "id", _is_nonempty_str, "必须是非空字符串")
    _expect(errors, path, value, "state", _is_nonempty_str, "必须是非空字符串")
    _expect(errors, path, value, "dependencies", _is_list, "必须是数组")
    if isinstance(value.get("dependencies"), list):
        for index, item in enumerate(value["dependencies"]):
            if not _is_nonempty_str(item):
                errors.append(f"{path}.dependencies[{index}] 必须是非空字符串")
    _expect(errors, path, value, "reviewRound", _is_opt_int, "必须是整数或 null")
    _check_time(errors, f"{path}.updatedAt", value.get("updatedAt"))
    first_review = value.get("firstReview")
    if first_review is not None:
        _check_review(errors, f"{path}.firstReview", first_review)


def _check_run(errors: list[str], path: str, value) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return
    _need(errors, path, value, _RUN_KEYS)
    _expect(errors, path, value, "id", _is_nonempty_str, "必须是非空字符串")
    _expect(errors, path, value, "kind", lambda v: _in(v, RUN_KINDS), "不是允许的枚举值")
    _expect(errors, path, value, "project", _is_str, "必须是字符串")
    _expect(errors, path, value, "item", _is_str, "必须是字符串")
    _expect(errors, path, value, "status", lambda v: _in(v, RUN_STATUSES), "不是允许的枚举值")
    _expect(errors, path, value, "rawStatus", _is_str, "必须是字符串")
    _expect(errors, path, value, "roleId", lambda v: v is None or _in(v, ROLE_IDS),
            "必须是 null 或允许的角色")
    _expect(errors, path, value, "stale", _is_opt_bool, "必须是布尔值或 null")
    _expect(errors, path, value, "detailPath", _is_abs_path, "必须是绝对本地路径")
    for key in ("createdAt", "completedAt", "lastChangedAt"):
        _check_time(errors, f"{path}.{key}", value.get(key))
    for key in ("progress", "decisions", "pendingIds"):
        _expect(errors, path, value, key, _is_list, "必须是数组")
        if isinstance(value.get(key), list):
            for index, item in enumerate(value[key]):
                if not _is_str(item):
                    errors.append(f"{path}.{key}[{index}] 必须是字符串")
    _expect(errors, path, value, "stages", _is_list, "必须是数组")
    if isinstance(value.get("stages"), list):
        for index, item in enumerate(value["stages"]):
            _check_stage(errors, f"{path}.stages[{index}]", item)
    _expect(errors, path, value, "agents", _is_list, "必须是数组")
    if isinstance(value.get("agents"), list):
        for index, item in enumerate(value["agents"]):
            _check_agent(errors, f"{path}.agents[{index}]", item)
    _expect(errors, path, value, "packages", _is_list, "必须是数组")
    if isinstance(value.get("packages"), list):
        for index, item in enumerate(value["packages"]):
            _check_package(errors, f"{path}.packages[{index}]", item)
    first_review = value.get("firstReview")
    if first_review is not None:
        _check_review(errors, f"{path}.firstReview", first_review)
    _check_sources(errors, f"{path}.sources", value.get("sources"))
    _check_diagnostics(errors, f"{path}.diagnostics", value.get("diagnostics"))
    if "reply" in value:
        _check_reply_slot(errors, f"{path}.reply", value["reply"], page="runs")


def _check_level_progress(errors: list[str], path: str, value) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return
    _need(errors, path, value, _LEVEL_KEYS)
    _expect(errors, path, value, "current", lambda v: _is_int(v) and v >= 0, "必须是非负整数")
    _expect(errors, path, value, "span", lambda v: _is_int(v) and v > 0, "必须是正整数")
    _expect(errors, path, value, "nextRemaining",
            lambda v: v is None or (_is_int(v) and v >= 0), "必须是非负整数或 null")


def _check_role(errors: list[str], path: str, value) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return
    _need(errors, path, value, _ROLE_KEYS)
    _expect(errors, path, value, "id", lambda v: _in(v, ROLE_IDS), "不是允许的角色")
    _expect(errors, path, value, "name", _is_nonempty_str, "必须是非空字符串")
    _expect(errors, path, value, "status", lambda v: _in(v, ROLE_STATUSES), "不是允许的枚举值")
    _expect(errors, path, value, "executors", _is_list, "必须是数组")
    if isinstance(value.get("executors"), list):
        for index, item in enumerate(value["executors"]):
            if item not in EXECUTOR_IDS:
                errors.append(f"{path}.executors[{index}] 不是允许的执行器")
    _expect(errors, path, value, "skills", _is_list, "必须是数组")
    if isinstance(value.get("skills"), list):
        for index, item in enumerate(value["skills"]):
            if not _is_str(item):
                errors.append(f"{path}.skills[{index}] 必须是字符串")
    _expect(errors, path, value, "useWhen", _is_opt_str, "必须是字符串或 null")
    _expect(errors, path, value, "avatarKey", _is_nonempty_str, "必须是非空字符串")
    for key in ("completed", "firstPass", "activeSessions"):
        if key in value:
            _check_metric(errors, f"{path}.{key}", value[key])
    _expect(errors, path, value, "level",
            lambda v: v is None or (_is_int(v) and 1 <= v <= 4), "必须是 1–4 或 null")
    progress = value.get("levelProgress")
    if progress is not None:
        _check_level_progress(errors, f"{path}.levelProgress", progress)
    _expect(errors, path, value, "isNew", _is_opt_bool, "必须是布尔值或 null")
    _check_sources(errors, f"{path}.sources", value.get("sources"))
    if "workspace" in value:
        _expect(errors, path, value, "workspace",
                lambda v: v is None or _in(v, ROLE_WORKSPACES), "必须是 worktree/directory/null")


def _check_unique_ids(errors: list[str], path: str, items) -> None:
    if not isinstance(items, list):
        return
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            continue
        if item_id in seen:
            errors.append(f"{path}[{index}].id 重复")
        seen.add(item_id)


def validate_snapshot(snapshot: dict) -> list[str]:
    """校验快照的必填字段、关键嵌套类型与枚举；返回固定格式的字段路径诊断。

    诊断只含字段路径与规则说明，不含被校验的原值；未知额外键允许（向后兼容）。
    空列表表示结构符合 P0 契约，不代表业务语义已被验证。
    """
    errors: list[str] = []
    if not isinstance(snapshot, dict):
        return ["snapshot 必须是对象"]

    _need(errors, "snapshot", snapshot, _SNAPSHOT_KEYS)
    if "schemaVersion" in snapshot and snapshot.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"snapshot.schemaVersion 必须是 {SCHEMA_VERSION}")
    if "generatedAt" in snapshot:
        _check_time(errors, "snapshot.generatedAt", snapshot.get("generatedAt"),
                    offset_required=True)
    if "localDate" in snapshot:
        value = snapshot.get("localDate")
        if not isinstance(value, str) or not _valid_date_text(value):
            errors.append("snapshot.localDate 必须是 YYYY-MM-DD")

    if "environment" in snapshot:
        _check_environment(errors, "snapshot.environment", snapshot["environment"])
    if "stats" in snapshot:
        _check_stats(errors, "snapshot.stats", snapshot["stats"])
    if "diagnostics" in snapshot:
        _check_diagnostics(errors, "snapshot.diagnostics", snapshot["diagnostics"])

    list_checks = (("pending", _check_pending), ("dispatchTasks", _check_dispatch),
                   ("runs", _check_run), ("roles", _check_role))
    for key, checker in list_checks:
        if key not in snapshot:
            continue
        items = snapshot[key]
        if not isinstance(items, list):
            errors.append(f"snapshot.{key} 必须是数组")
            continue
        for index, item in enumerate(items):
            checker(errors, f"snapshot.{key}[{index}]", item)
        _check_unique_ids(errors, f"snapshot.{key}", items)

    if "replies" in snapshot:
        replies = snapshot["replies"]
        if not isinstance(replies, list):
            errors.append("snapshot.replies 必须是数组")
        else:
            if len(replies) > REPLY_MAX_ITEMS:
                errors.append(f"snapshot.replies 不能超过 {REPLY_MAX_ITEMS} 条")
            for index, item in enumerate(replies):
                _check_reply(errors, f"snapshot.replies[{index}]", item)
            _check_unique_ids(errors, "snapshot.replies", replies)

    return errors
