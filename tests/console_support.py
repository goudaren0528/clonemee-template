# assistant-push-guard: fake-secrets（脱敏用例里的密钥都是虚构的，推送体积闸不扫这个文件的密钥）
r"""P0 共享夹具：七组纯虚构场景的磁盘 Markdown、对应快照，以及跨语言安全文本用例。

本模块只供测试/预览入口调用，不进生产路径；不读任何真实记录，也不调用被测适配器
（``tools/console_data.py``）去算“正确答案”。磁盘夹具与纯快照由同一份**手写**数据
构建：ID 共用，路径与预期分别手写，互不推算。

公共 API
========

``make_fixture(root, *, scenario, now=DEFAULT_NOW) -> dict``
    在调用者指定的**已存在且为空**的绝对临时根里写出一组纯虚构 Markdown，返回 manifest::

        {
          "scenario":   str,                      # SCENARIOS 之一
          "environment": {"inOrca": bool, "executorsAvailable": [str]},
          "now":        str,                      # 冻结时间的 ISO 8601（带 offset）
          "paths": {
              "root": str,                        # 绝对 posix 路径
              "pending": str | None,              # 该场景没写的文件为 None
              "daily":   str | None,
              "runs":        {runId: str},        # runId 即 runs/ 下的相对路径
              "roles":       {roleId: str},
              "experiences": {name: str},
              "reviews":     {relPath: str},
              "prd":     str | None,
          },
          "files": [str],                         # 实际写出的相对路径（posix）
          "notes": [str],                         # 消费提示
          "expected": {...}                       # 见下
        }

    ``expected`` 是**独立手写**的预期，不经适配器计算：

    - ``pending``：``ids`` / ``count`` / ``columns``（三栏计数）/ ``unprocessedHandoffs`` /
      ``processedHandoffs``；
    - ``dispatch``：``ids`` / ``count`` / ``excluded``（任务号 → 排除原因）；
    - ``runs``：``ids`` / ``statuses``（id → running|waiting|done|unknown）；
    - ``roles``：``ids`` / ``statuses`` / ``completed`` / ``activeSessions`` / ``levels``；
    - ``stats``：manifest 里是六个裸数字/``None``（外加 ``statReasons``）；快照里是六个
      ``Metric``：``{"value": int|None, "sourceIds": [str], "reason": str|None}``，
      未知原因就地写 ``reason``；
    - ``diagnostics``：固定 code 列表；``unknowns``：``{"field", "reason"}``；
    - ``boundary``（仅 boundaries）：``levelCases`` / ``staleCases`` / ``dayCases`` /
      ``streakCases``，与 Snapshot 顶层解耦。

    所有 ``paths`` 都是绝对 posix 字符串（Windows 也是 ``C:/…``），不含反斜线。

``snapshot_fixture(*, scenario, now=DEFAULT_NOW) -> dict``
    同一场景的**纯快照**，字段严格按 impl-plan 143-158 与 ``tools/console_contract.py``
    的 TypedDict/enum；``Role.workspace`` 为可选键。路径用虚构绝对根（Windows
    ``C:/example-device/assistant``、POSIX ``/example-device/assistant``）：与磁盘夹具共享
    稳定 ID，但路径字符串与预期各自手写。快照可被 ``validate_snapshot`` 校验。

``TEXT_CASES: list[dict]``
    ``{"name", "input", "expected"}`` 共享用例，Python ``console_contract.safe_text``
    与 JS ``AssistantConsole.safeText`` 必须同口径通过（见下）。

场景
====

================  ==========================================================
full-orca         全量虚构数据 + 在 Orca 内：三栏各有卡、已处理/未处理交接、
                  五种体验状态、普通/团队候选、四种 run 状态、六角色
                  含 draft/active 与有无历史、首审 pass/fix/无结果
full-local        同一批数据 + 不在 Orca 内：单任务只能“新终端窗口”，
                  团队副本不可进入（``selectable=false`` + ``disabledReason``）
empty             文件都在、内容是空的：已确认空集合与 0，历史指标仍未知
missing           一个文件都不写：计数/指标 ``null`` 并带“文件缺失”原因，
                  角色为空列表（不造占位）
conflict          字段与正文冲突、已处理缺说明、事项格式坏、非首轮审查等
boundaries        等级 0/2/3/10、停滞 29:59/30:00/30:01、日/周/年窗口边界；
                  公式级边界放在 ``LEVEL_CASES``/``STALE_CASES``/``DAY_CASES``/
                  ``STREAK_CASES`` 与 manifest 的 ``boundary`` 里
hostile           磁盘放虚构敏感 sentinel（PEM、Authorization/Bearer、token=…、
                  private 路径及编码/反斜线形态、javascript: 链接、超长文本、
                  围栏代码块）；快照只留安全投影与固定诊断，sentinel 原文
                  不得出现在快照任何位置
================  ==========================================================

稳定 ID（两种夹具共用）
=======================

- 待处理：记录原编号，如 ``N1020-01``。
- 派发任务：``plans/daily/2026-10-20.md#T3``（归一计划相对路径#任务号）。
- 运行：源相对路径，如 ``runs/2026-10-20-example-alpha.md``。
- 角色：``research`` / ``product`` / ``pm`` / ``investigate`` / ``dev`` / ``accept``。
- 会话：``agent-1``、``agent-2``…（脱敏稳定编号；快照不含 handle/cmd/工作区 id）。

字段取自记录的口径
==================

- 待处理表没有独立“标题”列：``title`` 取「项目：事项」的后半段（与 ``item`` 同源），
  ``summary`` 取「一句话」；两者都来自白名单字段，不另行编造。
- ``Choice.source`` 是短来源串：``"记录：选项"`` / ``"记录：建议"`` /
  ``"PRD 4.3 固定决定"``（契约里它是 ``str``，不是 SourceRef）。
- 卡住没有独立建议字段，建议以 ``id="suggestion"`` 的单条 Choice 表示。
- ``RunAgent.state`` 用 ``active`` / ``waiting`` / ``done`` / ``problem`` / ``unknown``。
- ``stats.clearStreak`` 在**所有**场景都是 ``null``：上游不保留完整清空历史；
  可计算的 streak 证据只放在 ``STREAK_CASES``/manifest，不冒充生产快照字段。

``make_fixture`` 的写入边界
===========================

只写 ``root`` 这一个目录：必须是绝对路径、已存在、是目录、不是符号链接、内容为空，
且不在本工具仓库内、不是盘根。任一不满足抛 ``ValueError``，一个文件都不写。磁盘内容
全部虚构，不复制 ``runs/``、``plans/daily/`` 或任何真实记录；``private/`` 从不创建、
从不读取。``now`` 必须带 UTC offset。

文本口径（TEXT_CASES 的执行形式）
=================================

1. ``None``/布尔/对象/数组 → ``None``；有限 int/float 转字符串。
2. **先**对完整原文做敏感检测，命中整字段 ``None``：private 路径组件（大小写、正反
   斜线、百分号编码）、PEM、``Authorization``/``Bearer``、明确凭据字段赋值
   （``token``/``access_token``/``refresh_token``/``id_token``/``auth_token``/
   ``password``/``passwd``/``pwd``/``secret``/``client_secret``/``api-key``/
   ``apikey``/``private_key``/``aws_secret_access_key``/``secret_access_key`` 及
   ``密码``/``口令``/``令牌``/``密钥``，分隔符 ``=``/``:``/``：``）、可识别凭据
   （``ghp_`` 等）、围栏代码块。
3. 未命中：``\r\n``、``\t``、``\v``、``\f``、U+2028/U+2029 归一为空格，去首尾，
   连续空白压成一个空格。
4. Markdown 链接 ``[标签](目标)`` 只留标签，丢弃目标（目标同样先过敏感检测）。
5. 截断到 500 个 Unicode 码点；超出时追加 ``［已截断］``（结果最长 505 字符）。
6. ``【控制台指令】``/``·``/``→`` 不在 ``safeText``/``safe_text`` 处理——那是
   ``safeLine`` 的导出侧转义，因此本表期望值保持原样。

扩展跨语言验证位于 ``console_core.test.cjs``：除本表 53 条外，38 条独立期望
覆盖 private 上下文、三层解码、控制/零宽归一为空格、行内代码/命令、链接剥离、
布尔/数字和 Unicode 截断。原文超过 20000 码点返回空，截断前去除截断处尾空白。
R1 明确凭据样例同时核对角色 feedback 导出为空、``inline_json`` 不含虚构哨兵。
有限用例的一致性不代表任意自由文本无秘密；仍须最小字段投影与后续人工检查。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = 2
SAFE_TEXT_LIMIT = 500
TRUNCATION_MARKER = "［已截断］"

#: 冻结时间：2026-10-20（周二）10:00 +08:00。
DEFAULT_NOW = datetime(2026, 10, 20, 10, 0, tzinfo=timezone(timedelta(hours=8)))

SCENARIOS = (
    "full-orca",
    "full-local",
    "empty",
    "missing",
    "conflict",
    "boundaries",
    "hostile",
)

EXECUTOR_IDS = ("claude", "codex", "opencode")
EXECUTOR_LABELS = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode"}
MODE_IDS = ("worktree", "directory", "terminal")
MODE_LABELS = {"worktree": "新建工作区", "directory": "现有目录", "terminal": "新终端窗口"}
ROLE_IDS = ("research", "product", "pm", "investigate", "dev", "accept")
PAGE_IDS = ("pending", "dispatch", "runs", "roles")
PENDING_TYPES = ("完成", "要拍板", "卡住", "交接")
PENDING_COLUMNS = ("待验收", "要拍板", "卡住")
EXPERIENCE_UI_STATES = ("yes", "no", "unknown")
EXPERIENCE_STATES = ("recorded-started", "static", "expired", "unknown")
RUN_STATUSES = ("running", "waiting", "done", "unknown")
ROLE_STATUSES = ("draft", "active", "unknown")
AGENT_STATES = ("active", "waiting", "done", "problem", "unknown")

#: 等级/经验条公式的边界值（impl-plan 5 节：0–2 Lv.1，3–9 Lv.2，10–29 Lv.3，≥30 Lv.4）。
LEVEL_CASES = (
    {"completed": 0, "level": 1, "progress": {"current": 0, "span": 3, "nextRemaining": 3}},
    {"completed": 2, "level": 1, "progress": {"current": 2, "span": 3, "nextRemaining": 1}},
    {"completed": 3, "level": 2, "progress": {"current": 0, "span": 7, "nextRemaining": 7}},
    {"completed": 9, "level": 2, "progress": {"current": 6, "span": 7, "nextRemaining": 1}},
    {"completed": 10, "level": 3, "progress": {"current": 0, "span": 20, "nextRemaining": 20}},
    {"completed": 29, "level": 3, "progress": {"current": 19, "span": 20, "nextRemaining": 1}},
    {"completed": 30, "level": 4, "progress": {"current": 1, "span": 1, "nextRemaining": 0}},
    {"completed": 31, "level": 4, "progress": {"current": 1, "span": 1, "nextRemaining": 0}},
)

#: 停滞阈值：严格大于 30 分钟才标橙，正好 30:00 不橙（impl-plan 适配与展示规则）。
STALE_CASES = (
    {"name": "29:59", "elapsedSeconds": 29 * 60 + 59, "stale": False},
    {"name": "30:00", "elapsedSeconds": 30 * 60, "stale": False},
    {"name": "30:01", "elapsedSeconds": 30 * 60 + 1, "stale": True},
)

#: 自然日/自然周边界（每个 case 自带 now，和 DEFAULT_NOW 无关）。
DAY_CASES = (
    {"name": "created-today-0000", "now": "2026-10-20T10:00:00+08:00",
     "createdAt": "2026-10-20T00:00:00+08:00", "completedAt": None,
     "inToday": True, "inWeek": True},
    {"name": "created-yesterday-2359", "now": "2026-10-20T10:00:00+08:00",
     "createdAt": "2026-10-19T23:59:00+08:00", "completedAt": None,
     "inToday": False, "inWeek": True},
    {"name": "completed-today-0000", "now": "2026-10-20T10:00:00+08:00",
     "createdAt": None, "completedAt": "2026-10-20T00:00:00+08:00",
     "inToday": True, "inWeek": True},
    {"name": "completed-week-start-0000", "now": "2026-10-20T10:00:00+08:00",
     "createdAt": None, "completedAt": "2026-10-19T00:00:00+08:00",
     "inToday": False, "inWeek": True},
    {"name": "completed-sunday-2359", "now": "2026-10-20T10:00:00+08:00",
     "createdAt": None, "completedAt": "2026-10-18T23:59:00+08:00",
     "inToday": False, "inWeek": False},
    {"name": "monday-start-previous-day", "now": "2026-10-19T00:00:00+08:00",
     "createdAt": None, "completedAt": "2026-10-18T23:59:00+08:00",
     "inToday": False, "inWeek": False},
    {"name": "cross-year-0130", "now": "2026-01-01T00:30:00+08:00",
     "createdAt": None, "completedAt": "2025-12-31T23:59:00+08:00",
     "inToday": False, "inWeek": True},
    {"name": "cross-year-previous-week", "now": "2026-01-04T23:59:00+08:00",
     "createdAt": None, "completedAt": "2025-12-28T23:59:00+08:00",
     "inToday": False, "inWeek": False},
)

#: 连续清空：只有完整事件历史能证明“日终余额 0”；未知覆盖整体为 null。
STREAK_CASES = (
    {
        "name": "two-cleared-days",
        "history": [
            {"date": "2026-10-19", "endBalance": 0, "coverage": "complete"},
            {"date": "2026-10-18", "endBalance": 0, "coverage": "complete"},
        ],
        "expected": 2,
    },
    {
        "name": "today-not-counted",
        "history": [
            {"date": "2026-10-20", "endBalance": 0, "coverage": "complete"},
            {"date": "2026-10-19", "endBalance": 0, "coverage": "complete"},
            {"date": "2026-10-18", "endBalance": 0, "coverage": "complete"},
        ],
        "expected": 2,
    },
    {
        "name": "stopped-by-dirty-day",
        "history": [
            {"date": "2026-10-19", "endBalance": 0, "coverage": "complete"},
            {"date": "2026-10-18", "endBalance": 3, "coverage": "complete"},
        ],
        "expected": 1,
    },
    {
        "name": "unknown-coverage-breaks-streak",
        "history": [
            {"date": "2026-10-19", "endBalance": 0, "coverage": "complete"},
            {"date": "2026-10-18", "endBalance": None, "coverage": "unknown"},
        ],
        "expected": None,
    },
    {
        "name": "no-history",
        "history": None,
        "expected": None,
    },
    {
        "name": "yesterday-not-provable",
        "history": [
            {"date": "2026-10-18", "endBalance": 0, "coverage": "complete"},
        ],
        "expected": None,
    },
)

_TEXT_LONG = "甲" * 600
_TEXT_LONG_TAIL_SENSITIVE = "丙" * 520 + " token=abc123"


def _chinese_prefix_credential_cases() -> list[dict]:
    """R1 第 2 轮残留：自然中文前缀 + 中文凭据键，键后空白再接分隔符。

    输入按人工矩阵拼接（4 个前缀键 × 3 个分隔符 × 1 个空格），预期为人工写定的
    ``None``，不读取 Python/JS 实现的判定结果。
    """
    keys = (("新密码", "password"), ("临时令牌", "token"),
            ("接口密钥", "key"), ("旧口令", "passphrase"))
    delimiters = (("：", "fullwidth"), (":", "colon"), ("=", "equals"))
    cases: list[dict] = []
    for key, key_slug in keys:
        for delimiter, delimiter_slug in delimiters:
            cases.append({
                "name": f"credential-chinese-space-{key_slug}-{delimiter_slug}",
                "input": f"{key} {delimiter}FICTIONAL_ONLY",
                "expected": None,
            })
    return cases


#: R1 第 2 轮残留：前缀键紧贴分隔符的四条反例与无赋值叙述对照（全部人工写定）。
_CHINESE_PREFIX_FIXED_CASES = [
    {"name": "credential-chinese-prefix-password", "input": "新密码：FICTIONAL_ONLY",
     "expected": None},
    {"name": "credential-chinese-prefix-token", "input": "临时令牌=FICTIONAL_ONLY",
     "expected": None},
    {"name": "credential-chinese-prefix-key", "input": "接口密钥:FICTIONAL_ONLY",
     "expected": None},
    {"name": "credential-chinese-prefix-passphrase", "input": "旧口令：FICTIONAL_ONLY",
     "expected": None},
    {"name": "credential-narrative-password-done", "input": "密码已改好",
     "expected": "密码已改好"},
    {"name": "credential-narrative-new-password-field", "input": "新密码字段不会展示",
     "expected": "新密码字段不会展示"},
    {"name": "credential-narrative-temp-token-field", "input": "临时令牌是字段名",
     "expected": "临时令牌是字段名"},
    {"name": "credential-narrative-api-key-config", "input": "接口密钥配置说明",
     "expected": "接口密钥配置说明"},
    {"name": "credential-narrative-old-passphrase-expired", "input": "旧口令已失效",
     "expected": "旧口令已失效"},
]

#: Python ``safe_text`` 与 JS ``safeText`` 的共享用例；敏感期望值为 ``None``。
TEXT_CASES = [
    {"name": "plain-text", "input": "T2 只读排查 · 示例项目", "expected": "T2 只读排查 · 示例项目"},
    {"name": "collapse-spaces", "input": "  多个   空格\t和   制表  ", "expected": "多个 空格 和 制表"},
    {"name": "newlines-to-space", "input": "第一行\r\n第二行\u2028第三行", "expected": "第一行 第二行 第三行"},
    {"name": "none-input", "input": None, "expected": None},
    {"name": "object-input", "input": {"a": 1}, "expected": None},
    {"name": "integer-input", "input": 42, "expected": "42"},
    {"name": "private-posix-path", "input": "证据在 private/health/training-log.md",
     "expected": None},
    {"name": "private-windows-path", "input": "C:\\Users\\demo\\private\\finance\\ledger.md 已同步",
     "expected": None},
    {"name": "private-uppercase", "input": "PRIVATE/health/water-log.md", "expected": None},
    {"name": "private-backslash", "input": "private\\health\\index.md", "expected": None},
    {"name": "private-percent-encoded", "input": "private%2Fhealth%2Findex.md", "expected": None},
    {"name": "pem-block", "input": "-----BEGIN " "PRIVATE KEY-----\nMIIBdemo\n-----END PRIVATE KEY-----",
     "expected": None},
    {"name": "authorization-header", "input": "Authorization: Bearer demo-token-value",
     "expected": None},
    {"name": "bearer-token", "input": "Bearer eyJhbGciOiJIUzI1NiJ9", "expected": None},
    {"name": "token-assignment", "input": "token=abc123def456", "expected": None},
    {"name": "password-assignment", "input": "password: hunter2-demo", "expected": None},
    {"name": "secret-assignment", "input": "secret = demo-secret-value", "expected": None},
    {"name": "api-key-assignment", "input": "api-key: sk-demo-1234567890", "expected": None},
    {"name": "credential-token-fullwidth", "input": "token：FICTIONAL_ONLY", "expected": None},
    {"name": "credential-id-token", "input": "id_token=FICTIONAL_ONLY", "expected": None},
    {"name": "credential-password-chinese", "input": "密码：FICTIONAL_ONLY", "expected": None},
    {"name": "credential-access-token", "input": "access_token：FICTIONAL_ONLY", "expected": None},
    {"name": "credential-refresh-token", "input": "refresh_token=FICTIONAL_ONLY", "expected": None},
    {"name": "credential-auth-token", "input": "auth_token: FICTIONAL_ONLY", "expected": None},
    {"name": "credential-passwd", "input": "passwd：FICTIONAL_ONLY", "expected": None},
    {"name": "credential-pwd", "input": "pwd=FICTIONAL_ONLY", "expected": None},
    {"name": "credential-client-secret", "input": "client_secret=FICTIONAL_ONLY", "expected": None},
    {"name": "credential-client-secret-hyphen", "input": "client-secret：FICTIONAL_ONLY",
     "expected": None},
    {"name": "credential-api-key-underscore", "input": "api_key：FICTIONAL_ONLY", "expected": None},
    {"name": "credential-apikey", "input": "apikey=FICTIONAL_ONLY", "expected": None},
    {"name": "credential-private-key", "input": "private_key：FICTIONAL_ONLY", "expected": None},
    {"name": "credential-aws-secret-access-key", "input": "aws_secret_access_key=FICTIONAL_ONLY",
     "expected": None},
    {"name": "credential-secret-access-key", "input": "secret_access_key：FICTIONAL_ONLY",
     "expected": None},
    {"name": "credential-chinese-passphrase", "input": "口令：FICTIONAL_ONLY", "expected": None},
    {"name": "credential-chinese-token", "input": "令牌=FICTIONAL_ONLY", "expected": None},
    {"name": "credential-chinese-key", "input": "密钥：FICTIONAL_ONLY", "expected": None},
    {"name": "credential-narrative-token", "input": "token 是记录字段名",
     "expected": "token 是记录字段名"},
    {"name": "credential-narrative-id-token", "input": "id_token 是字段名",
     "expected": "id_token 是字段名"},
    {"name": "credential-narrative-password", "input": "密码字段不会展示",
     "expected": "密码字段不会展示"},
    {"name": "credential-narrative-key", "input": "密钥管理是产品话题",
     "expected": "密钥管理是产品话题"},
    {"name": "credential-narrative-passphrase", "input": "口令用于描述操作步骤",
     "expected": "口令用于描述操作步骤"},
    {"name": "github-token", "input": "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
     "expected": None},
    {"name": "fenced-code-block", "input": "结论如下\n```text\n示例命令\n```", "expected": None},
    {"name": "tilde-fence", "input": "~~~\n示例输出\n~~~", "expected": None},
    {"name": "markdown-link-safe-target", "input": "详见 [现状报告](https://example.com/report)",
     "expected": "详见 现状报告"},
    {"name": "markdown-link-relative-target", "input": "见 [示例页面](docs/demo.html#part-2)",
     "expected": "见 示例页面"},
    {"name": "markdown-link-js-target", "input": "见 [点击这里](javascript:void0)",
     "expected": "见 点击这里"},
    {"name": "markdown-link-private-target", "input": "见 [报告](private/finance/ledger.md)",
     "expected": None},
    {"name": "console-marker-kept", "input": "【控制台指令】拍板：示例项目：T1 示例事项",
     "expected": "【控制台指令】拍板：示例项目：T1 示例事项"},
    {"name": "separator-kept", "input": "示例项目 · T1 → A 继续原范围",
     "expected": "示例项目 · T1 → A 继续原范围"},
    {"name": "exactly-limit", "input": "乙" * SAFE_TEXT_LIMIT,
     "expected": "乙" * SAFE_TEXT_LIMIT},
    {"name": "over-limit", "input": _TEXT_LONG,
     "expected": "甲" * SAFE_TEXT_LIMIT + TRUNCATION_MARKER},
    {"name": "sensitive-tail-beyond-limit", "input": _TEXT_LONG_TAIL_SENSITIVE, "expected": None},
]

# 现有 53 条保持原样；第 2 轮新增独立预期追加在后。
TEXT_CASES.extend(_CHINESE_PREFIX_FIXED_CASES)
TEXT_CASES.extend(_chinese_prefix_credential_cases())

SCENARIO_NOTES = {
    "full-orca": [
        "三栏：要拍板 1 / 卡住 1 / 待验收 5；另有未处理交接 1 与已处理交接 1。",
        "五种体验：recorded-started / no-ui / unknown / static / expired 各一。",
        "候选：5 个单任务 + 1 个团队副本（含缺证据与兜底两条）。",
        "运行：running / waiting / done / unknown 四种归一状态齐备。",
        "角色：六名，dev 有历史，其余明确 0 次（pm 由流水线计）。",
    ],
    "full-local": [
        "与 full-orca 同一批记录，仅环境不同（inOrca=false、可用执行器少一个）。",
        "单任务 defaultMode 一律 terminal；团队副本 selectable=false 且给出原因。",
    ],
    "empty": [
        "六个角色文件都在、都是 draft，但没有运行历史。",
        "指标 0 与未知要分开：窗口内计数是 0，终身/历史类指标是 null。",
    ],
    "missing": [
        "一个文件都不写；root 里没有任何内容。",
        "所有计数/指标 null，原因写“文件缺失”，不得用 0 冒充。",
        "没有角色文件就没有角色项（roles 为空列表），只记 roles_source_missing 诊断。",
    ],
    "conflict": [
        "字段与正文冲突时以明确字段为准，并附诊断，不修改源记录。",
        "已处理缺说明、事项缺“项目：”、非首轮审查都不算有效证据。",
    ],
    "boundaries": [
        "13 份运行记录覆盖等级 0/2/3/10 与停滞/日周边界。",
        "等级 9/29/30/31、streak 与跨年窗口在 LEVEL_CASES/DAY_CASES/STREAK_CASES。",
    ],
    "hostile": [
        "磁盘里的 sentinel 全部虚构，只用于验证屏蔽；快照必须不含原文。",
        "被屏蔽字段以空串/None + 固定诊断表示，不保留原值。",
    ],
}

#: 虚构根：绝对且跨平台，不含真实用户目录。
_FICTION_ROOT_WINDOWS = "C:/example-device/assistant"
_FICTION_ROOT_POSIX = "/example-device/assistant"

_PENDING_REL = "runs/pending.md"
_DAILY_REL = "plans/daily/2026-10-20.md"


# ------------------------------------------------------------------ 小工具

class _Doc:
    """按行构建一份虚构 Markdown，并顺手记下行号。

    行号来自构建过程（第几行是刚写的），不从被测适配器反推；源记录与预期共用同一
    份行号，便于双向对账。
    """

    __slots__ = ("rel", "lines")

    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.lines: list[str] = []

    def add(self, line: str = "") -> int:
        self.lines.append(line)
        return len(self.lines)

    def extend(self, lines) -> tuple[int, int]:
        start = len(self.lines) + 1
        for line in lines:
            self.lines.append(line)
        return start, len(self.lines)

    @property
    def text(self) -> str:
        return "\n".join(self.lines).rstrip("\n") + "\n"


def _posix(path) -> str:
    return Path(path).as_posix()


def _abs(base, rel: str) -> str:
    return Path(base, *rel.split("/")).as_posix()


def _src(base, rel: str, start: int, field: str, end: int | None = None) -> dict:
    return {
        "path": _abs(base, rel),
        "lineStart": start,
        "lineEnd": start if end is None else end,
        "field": field,
    }


def _diag(code: str, message: str, source: str | None = None) -> dict:
    return {"code": code, "message": message, "source": source}


def _metric(value, source_ids=(), reason=None) -> dict:
    return {"value": value, "sourceIds": list(source_ids), "reason": reason}


def _ratio(numerator, denominator, source_ids=(), reason=None) -> dict:
    if numerator is None or denominator is None or denominator == 0:
        value = None
    else:
        value = int(round(numerator * 100 / denominator))
    return {
        "value": value,
        "sourceIds": list(source_ids),
        "reason": reason,
        "numerator": numerator,
        "denominator": denominator,
    }


_LEVEL_BANDS = ((1, 0, 3), (2, 3, 10), (3, 10, 30), (4, 30, None))


def _level(completed):
    """impl-plan 的等级/经验条公式；completed 未知时返回 (None, None)。"""
    if completed is None:
        return None, None
    for level, start, nxt in _LEVEL_BANDS:
        if nxt is None or completed < nxt:
            if nxt is None:
                return level, {"current": 1, "span": 1, "nextRemaining": 0}
            return level, {
                "current": completed - start,
                "span": nxt - start,
                "nextRemaining": nxt - completed,
            }
    return None, None


def _pending(*, pid, ptype, column, project, item, title, summary, sender,
             created_at, detail, run_id, choices, sources, status="待处理",
             role_id=None, processed_at=None, processed_note=None,
             experience=None, diagnostics=()) -> dict:
    return {
        "id": pid,
        "type": ptype,
        "status": status,
        "column": column,
        "project": project,
        "item": item,
        "title": title,
        "summary": summary,
        "roleId": role_id,
        "senderName": sender,
        "createdAt": created_at,
        "detailPath": detail,
        "runId": run_id,
        "choices": list(choices),
        "processedAt": processed_at,
        "processedNote": processed_note,
        "experience": experience,
        "sources": list(sources),
        "diagnostics": list(diagnostics),
    }


def _choice(cid: str, label: str, source: str) -> dict:
    return {"id": cid, "label": label, "source": source}


ACCEPT_CHOICES = (
    _choice("accept", "验收通过", "PRD 4.3 固定决定"),
    _choice("reject", "打回", "PRD 4.3 固定决定"),
    _choice("later", "稍后", "PRD 4.3 固定决定"),
)


def _exp(*, ui, state, url=None, recorded=None, expires=None, tests=None,
         focus=(), sources=()) -> dict:
    return {
        "ui": ui,
        "state": state,
        "url": url,
        "recordedAt": recorded,
        "expiresAt": expires,
        "tests": tests,
        "focus": list(focus),
        "sources": list(sources),
    }


def _dispatch(*, tid, task_no, project, title, summary, plan_rel, base, lines,
              role_id=None, declared=None, changes=None, kind="single", prd_rel=None,
              difficulty=None, stages=(), outputs=(), default_executor=None,
              default_mode, default_reason, fallback=False, selectable=True,
              disabled_reason=None) -> dict:
    sources = [_src(base, plan_rel, start, field, end) for field, start, end in lines]
    return {
        "id": f"{plan_rel}#{task_no}",
        "taskNo": task_no,
        "project": project,
        "title": title,
        "summary": summary,
        "planPath": _abs(base, plan_rel),
        "roleId": role_id,
        "declaredExecutor": declared,
        "changesCode": changes,
        "kind": kind,
        "prdPath": None if prd_rel is None else _abs(base, prd_rel),
        "difficulty": difficulty,
        "stages": list(stages),
        "outputs": list(outputs),
        "defaultExecutor": default_executor,
        "defaultMode": default_mode,
        "defaultReason": default_reason,
        "fallback": fallback,
        "selectable": selectable,
        "disabledReason": disabled_reason,
        "sources": sources,
    }


def _stage(label: str, state: str, source) -> dict:
    return {"label": label, "state": state, "source": source}


def _agent(aid, *, role_id, executor, activity, state, changed=None, package_id=None) -> dict:
    return {
        "id": aid,
        "roleId": role_id,
        "executor": executor,
        "packageId": package_id,
        "activity": activity,
        "state": state,
        "lastChangedAt": changed,
    }


def _pkg(pid, *, state, deps=(), review_round=None, updated=None, first_review=None) -> dict:
    return {
        "id": pid,
        "state": state,
        "dependencies": list(deps),
        "reviewRound": review_round,
        "updatedAt": updated,
        "firstReview": first_review,
    }


def _review(verdict: str, source) -> dict:
    return {"round": 1, "verdict": verdict, "source": source}


def _run(*, rid, kind, project, item, status, raw_status, role_id, created, completed,
         changed, stale, stages, agents, packages, progress, decisions, pending_ids,
         detail_rel, base, first_review=None, sources, diagnostics=()) -> dict:
    return {
        "id": rid,
        "kind": kind,
        "project": project,
        "item": item,
        "status": status,
        "rawStatus": raw_status,
        "roleId": role_id,
        "createdAt": created,
        "completedAt": completed,
        "lastChangedAt": changed,
        "stale": stale,
        "stages": list(stages),
        "agents": list(agents),
        "packages": list(packages),
        "progress": list(progress),
        "decisions": list(decisions),
        "pendingIds": list(pending_ids),
        "detailPath": _abs(base, detail_rel),
        "firstReview": first_review,
        "sources": list(sources),
        "diagnostics": list(diagnostics),
    }


def _role(*, rid, name, status, executors, skills, use_when, avatar, completed,
          first_pass, active, level, progress, is_new, sources, workspace=None) -> dict:
    role = {
        "id": rid,
        "name": name,
        "status": status,
        "executors": list(executors),
        "skills": list(skills),
        "useWhen": use_when,
        "avatarKey": avatar,
        "completed": completed,
        "firstPass": first_pass,
        "activeSessions": active,
        "level": level,
        "levelProgress": progress,
        "isNew": is_new,
        "sources": list(sources),
    }
    if workspace is not None:
        role["workspace"] = workspace
    return role


def _stats(pending, running, today, dispatched, week, streak, *,
           runs=(), pending_source=_PENDING_REL) -> dict:
    """六个 Metric（value/sourceIds/reason）——console_contract 的可执行口径。

    ``clearStreak`` 恒为 null：上游不保留完整清空历史，可计算的 streak 证据只在
    ``STREAK_CASES`` 里，不冒充生产字段。
    """

    def one(value, source_ids, reason=None):
        return {"value": value, "sourceIds": list(source_ids), "reason": reason}

    return {
        "pendingCount": one(pending, [pending_source] if pending_source else []),
        "runningCount": one(running, runs),
        "completedToday": one(today, runs),
        "dispatchedToday": one(dispatched, runs),
        "completedWeek": one(week, runs),
        "clearStreak": one(streak, [], "上游未保留完整清空历史，无法证明日终余额"),
    }


# ------------------------------------------------------------- 磁盘文档构建

def _pending_doc(rows, *, today: str) -> tuple[_Doc, dict]:
    d = _Doc(_PENDING_REL)
    d.add("---")
    d.add("type: pending")
    d.add("slug: pending")
    d.add("status: active")
    d.add(f"updated: {today}")
    d.add("tags: [meta, comms]")
    d.add('summary: "虚构夹具：示例待处理清单"')
    d.add("---")
    d.add("")
    d.add("# 待处理清单")
    d.add("")
    d.add("| 编号 | 时间 | 类型 | 来源 | 事项 | 一句话 | 详情 | 送达 | 状态 |")
    d.add("|---|---|---|---|---|---|---|---|---|")
    marks = {}
    for row in rows:
        cells = [row["id"], row["when"], row["type"], row["source"], row["item"],
                 row["text"], row["detail"], row["delivery"], row["status"]]
        marks[row["id"]] = d.add("| " + " | ".join(cells) + " |")
    return d, marks


def _daily_doc(tasks, *, status: str = "active", today: str = "2026-10-20",
               summary: str = "虚构夹具：今日建议") -> tuple[_Doc, dict]:
    d = _Doc(_DAILY_REL)
    d.add("---")
    d.add("type: plan")
    d.add(f"slug: daily-{today}")
    d.add(f"status: {status}")
    d.add(f"created: {today}")
    d.add(f"updated: {today}")
    d.add(f'summary: "{summary}"')
    d.add("---")
    d.add("")
    d.add(f"# 今日建议 · {today} 周二")
    d.add("")
    d.add("## 结论")
    d.add("虚构夹具：只用于验证控制台，不对应任何真实计划。")
    d.add("")
    d.add("## 今日建议（待你确认）")
    marks = {}
    for task in tasks:
        line = f"- [{task['mark']}] {task['taskNo']} {task['title']}"
        if task.get("meta"):
            line += " · " + " · ".join(task["meta"])
        start = d.add(line)
        subs = {}
        for key, value in task.get("subs", []):
            subs[key] = d.add(f"  - {key}：{value}")
        marks[task["taskNo"]] = {"start": start, "end": len(d.lines), "subs": subs}
    return d, marks


def _role_doc(role_id: str, *, status: str, name: str, default_executor: str,
              executors, workspace: str, skills, use_when: str,
              updated: str = "2026-10-20", workspace_norm=None) -> tuple[_Doc, dict]:
    # workspace_norm 只是 _ROLE_SPECS_FULL 的旁注（raw workspace → 归一值），不写进文件。
    del workspace_norm
    d = _Doc(f"agents/{role_id}.md")
    d.add("---")
    d.add("type: agent")
    d.add(f"slug: {role_id}")
    fields = {}
    fields["status"] = d.add(f"status: {status}")
    fields["updated"] = d.add(f"updated: {updated}")
    fields["name"] = d.add(f"name: {name}")
    fields["default_executor"] = d.add(f"default_executor: {default_executor}")
    fields["executors"] = d.add(f"executors: [{', '.join(executors)}]")
    fields["workspace"] = d.add(f"workspace: {workspace}")
    fields["skills"] = d.add(f"skills: [{', '.join(skills)}]")
    fields["use_when"] = d.add(f"use_when: {use_when}")
    d.add('summary: "虚构夹具：角色设定"')
    d.add("---")
    d.add("")
    d.add(f"# {name}")
    return d, fields


def _run_doc(slug: str, header, title: str, sections) -> tuple[_Doc, dict]:
    """header 是 [(key, value)]；sections 是 [(标题, [行])]，返回行号标记。"""
    d = _Doc(f"runs/{slug}.md")
    d.add("---")
    fields = {}
    for key, value in header:
        fields[key] = d.add(f"{key}: {value}")
    d.add("---")
    d.add("")
    fields["title"] = d.add(f"# {title}")
    d.add("")
    marks = {"fields": fields, "sections": {}}
    for heading, lines in sections:
        start, end = d.extend([f"## {heading}", *lines, ""])
        marks["sections"][heading] = (start, end)
    return d, marks


def _review_doc(rel: str, *, slug: str, verdict: str, summary: str,
                updated: str = "2026-10-20") -> tuple[_Doc, dict]:
    d = _Doc(rel)
    d.add("---")
    d.add("type: review-report")
    d.add(f"slug: {slug}")
    d.add("status: done")
    d.add(f"updated: {updated}")
    fields = {"verdict": d.add(f"verdict: {verdict}")}
    d.add(f'summary: "{summary}"')
    d.add("---")
    d.add("")
    d.add(f"# 审查报告：{slug}")
    d.add("")
    d.add(f"**结论**：{verdict}")
    return d, fields


def _file_uri(base, rel: str) -> str:
    path = _abs(base, rel)
    return "file://" + (path if path.startswith("/") else "/" + path)


def _json_text(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


# ------------------------------------------------------- full-orca / full-local

_RUNS_FULL = (
    "2026-10-20-example-alpha",
    "2026-10-20-example-beta",
    "2026-10-20-example-gamma",
    "2026-10-20-example-delta",
    "2026-10-20-example-epsilon",
    "2026-10-20-example-zeta",
    "2026-10-19-example-eta",
    "2026-10-20-example-theta",
    "2026-10-20-example-kappa",
)

_ROLE_SPECS_FULL = {
    "research": dict(status="draft", name="调研 Agent", default_executor="claude",
                     executors=["claude"], workspace="existing", skills=[],
                     use_when="技术调研、读源码摸清现状；只读", workspace_norm="directory"),
    "product": dict(status="active", name="产品 Agent", default_executor="claude",
                    executors=["claude", "opencode"], workspace="new",
                    skills=["pm-clarify", "pm-prd"],
                    use_when="澄清需求、写可验收的 PRD", workspace_norm="worktree"),
    "pm": dict(status="active", name="跟进 Agent", default_executor="claude",
               executors=["claude"], workspace="assistant", skills=["task-follow"],
               use_when="开发交付流水线里推进需求和开发任务", workspace_norm="directory"),
    "investigate": dict(status="draft", name="排查 Agent", default_executor="codex",
                        executors=["codex", "opencode"], workspace="existing", skills=[],
                        use_when="只读排查线上问题并出结论", workspace_norm="directory"),
    "dev": dict(status="active", name="开发 Agent", default_executor="opencode",
                executors=["opencode", "claude"], workspace="new",
                skills=["dev-plan", "dev-build", "dev-fix"],
                use_when="按 PRD 写实现计划、按包实现并自测", workspace_norm="worktree"),
    "accept": dict(status="active", name="验收 Agent", default_executor="codex",
                   executors=["codex", "claude"], workspace="existing",
                   skills=["accept-cases", "accept-review", "accept-test"],
                   use_when="出验收用例、审查交付、跑集成测试", workspace_norm="directory"),
}

_DAILY_TASKS_FULL = [
    dict(taskNo="T1", mark=" ", title="整理今日事项", meta=["@me", "exec:me", "proj:示例项目A"],
         subs=[("说明", "主会话自己整理，不派发")]),
    dict(taskNo="T2", mark=" ", title="只读排查示例", meta=["@me", "exec:codex", "proj:示例项目A"],
         subs=[("角色", "investigate"), ("说明", "只读定位示例问题根因，不改代码"),
               ("产出", "排查结论"), ("改代码", "否")]),
    dict(taskNo="T3", mark=" ", title="控制台示例修复", meta=["@me", "exec:opencode", "proj:示例项目A"],
         subs=[("角色", "dev"), ("说明", "修三栏计数与体验区"),
               ("产出", "代码提交"), ("改代码", "是")]),
    dict(taskNo="T4", mark=" ", title="示例需求交付", meta=["@me", "exec:opencode", "proj:示例项目B"],
         subs=[("工作流", "开发交付流水线"), ("角色", "dev"),
               ("PRD", "docs/delivery/demo/prd.md"), ("产出", "体验链接、测试报告"),
               ("改代码", "是")]),
    dict(taskNo="T5", mark=" ", title="示例调研（无角色）", meta=["@me", "exec:claude", "proj:示例项目B"],
         subs=[("说明", "整理现状，不改代码"), ("改代码", "否")]),
    dict(taskNo="T6", mark=" ", title="无角色无执行器", meta=["@me", "proj:示例项目B"],
         subs=[("说明", "需要改代码的小修"), ("改代码", "是")]),
    dict(taskNo="T7", mark=" ", title="证据不足示例", meta=["@me", "proj:示例项目B"],
         subs=[("说明", "等主会话补充角色与执行器")]),
    dict(taskNo="T8", mark=" ", title="示例产品澄清", meta=["@me", "exec:codex", "proj:示例项目A"],
         subs=[("角色", "product"), ("说明", "澄清文案，不改代码"), ("改代码", "否")]),
    dict(taskNo="T9", mark="x", title="已完成的示例任务", meta=["@me", "exec:opencode", "proj:示例项目A"]),
    dict(taskNo="T10", mark="-", title="已放弃的示例任务", meta=["@me", "exec:opencode", "proj:示例项目A"]),
    dict(taskNo="T11", mark=" ", title="已有运行记录的示例任务",
         meta=["@me", "exec:opencode", "proj:示例项目A",
               "run:runs/2026-10-20-example-alpha.md"]),
]


# -------------------------------------------------------------- v1.1 回复层（replies=True 才写）
#
# 只给 full-orca / full-local 叠一层：运行记录写 device、alpha 配跟进 Agent、写回复分表和两台
# 设备的 registry 文件；快照加 environment.device、每张卡片的 reply、顶层 replies。预期全部手写，
# 不经适配器计算。replies=False 时磁盘与快照和 v1.0 夹具逐字相同。

_DEVICE_LOCAL = "a1b2c3d4e5f6"
_DEVICE_LOCAL_NAME = "示例设备甲"
_DEVICE_OTHER = "0f1e2d3c4b5a"
_DEVICE_OTHER_NAME = "示例设备乙"
# delta、theta 故意不写 device：模拟 v1.1 之前的旧记录（设备未记录）
_RUN_DEVICES = {
    "2026-10-20-example-alpha": _DEVICE_LOCAL,
    "2026-10-20-example-beta": _DEVICE_OTHER,
    "2026-10-20-example-gamma": _DEVICE_LOCAL,
    "2026-10-20-example-epsilon": _DEVICE_LOCAL,
    "2026-10-20-example-zeta": _DEVICE_LOCAL,
    "2026-10-19-example-eta": _DEVICE_LOCAL,
    "2026-10-20-example-kappa": _DEVICE_LOCAL,
}
_REPLIES_REL = "runs/replies.md"
_REPLY_CHOICE_SOURCE = "PRD v1.1 4.2 快捷评价"


def _dev(replies: bool, slug: str) -> list:
    if replies and slug in _RUN_DEVICES:
        return [("device", _RUN_DEVICES[slug])]
    return []


def _follow_section(replies: bool, slug: str) -> list:
    """dispatch.py 给单任务配跟进 Agent 时写的那一行（正文，不在 frontmatter）。"""
    if replies and slug == "2026-10-20-example-alpha":
        return [("跟进说明", ["- 跟进 Agent：Orca 终端 handle demo-follow-a"])]
    return []


def _reply_device(device_id):
    if device_id is None:
        return {"id": None, "name": None, "local": None}
    name = {_DEVICE_LOCAL: _DEVICE_LOCAL_NAME, _DEVICE_OTHER: _DEVICE_OTHER_NAME}.get(device_id)
    return {"id": device_id, "name": name, "local": device_id == _DEVICE_LOCAL}


def _choices_of(labels: dict) -> list:
    return [_choice(cid, label, _REPLY_CHOICE_SOURCE) for cid, label in labels.items()]


_BLOCKED = {"follow": "按建议办", "other": "换个办法", "pause": "先停下"}
_ACCEPT = {"accept": "验收通过", "reject": "打回", "later": "稍后"}
_RUN = {"add": "补充", "ask": "问进度", "pause": "先停下"}
_FOLLOW = "跟进 Agent"
_EXECUTOR = "执行会话（没有跟进 Agent）"
_MAIN = "主会话"

# 卡片 → (kind, 接收方, 卡片所在设备)；None 表示没有回复区
_PENDING_SLOTS = {
    "N1020-01": ("forward", _FOLLOW, _DEVICE_LOCAL),       # 要拍板 · 交付看板 gamma
    "N1020-02": ("forward", _FOLLOW, _DEVICE_LOCAL),       # 卡住 · alpha 有跟进
    "N1020-03": ("forward", _FOLLOW, _DEVICE_LOCAL),       # 待验收 · alpha 有跟进
    "N1020-04": ("main", _MAIN, _DEVICE_OTHER),            # 待验收 · beta 在设备乙
    "N1020-05": ("main", _MAIN, None),                     # 待验收 · delta 旧记录
    "N1020-06": ("main", _MAIN, _DEVICE_LOCAL),            # 待验收 · 单任务没跟进
    "N1020-07": ("main", _MAIN, _DEVICE_LOCAL),
    "N1020-08": None,                                      # 交接
    "N1019-09": None,                                      # 已处理
}
_RUN_SLOTS = {
    "runs/2026-10-20-example-alpha.md": ("forward", _FOLLOW, _DEVICE_LOCAL),
    "runs/2026-10-20-example-beta.md": ("forward", _EXECUTOR, _DEVICE_OTHER),
    "runs/2026-10-20-example-gamma.md": ("forward", _FOLLOW, _DEVICE_LOCAL),
    "runs/2026-10-20-example-delta.md": ("forward", _EXECUTOR, None),
    "runs/2026-10-20-example-epsilon.md": ("forward", _EXECUTOR, _DEVICE_LOCAL),
    "runs/2026-10-20-example-zeta.md": ("forward", _EXECUTOR, _DEVICE_LOCAL),
    "runs/2026-10-19-example-eta.md": None,                # 已通关
    "runs/2026-10-20-example-theta.md": None,              # 状态未知
    "runs/2026-10-20-example-kappa.md": ("forward", _EXECUTOR, _DEVICE_LOCAL),
}


def _slot(card: str, association: str, spec, choices: list, need: list, page_only: list):
    if spec is None:
        return None
    kind, receiver, device_id = spec
    other = device_id is not None and device_id != _DEVICE_LOCAL
    return {"card": card, "allowed": not other, "reason": "other_device" if other else None,
            "kind": kind, "receiver": receiver, "association": association,
            "choices": choices, "needText": need, "pageOnly": page_only,
            "device": _reply_device(device_id)}


def _pending_slot(item: dict):
    spec = _PENDING_SLOTS.get(item["id"])
    if item["column"] == "要拍板":
        choices, need, page_only = [dict(c) for c in item["choices"]], [], []
    elif item["column"] == "卡住":
        choices, need, page_only = _choices_of(_BLOCKED), ["other"], []
    else:
        choices, need, page_only = _choices_of(_ACCEPT), ["reject"], ["later"]
    return _slot(f"pending:{item['id']}", item["id"], spec, choices, need, page_only)


def _run_slot(run: dict):
    return _slot(f"run:{run['id']}", run["detailPath"], _RUN_SLOTS.get(run["id"]),
                 _choices_of(_RUN), ["add"], [])


# 回复分表的行（文件里旧的在前）；快照里新的在前
def _reply_rows(base) -> list:
    alpha = _abs(base, "runs/2026-10-20-example-alpha.md")
    gamma = _abs(base, "runs/2026-10-20-example-gamma.md")
    lines = (
        "【控制台指令】确认派发：示例项目B：T5 示例调研（无角色） · 角色 调研 · 执行器 Claude Code · 现有目录"
        " ⏎ 【控制台指令】确认派发：示例项目A：T8 示例产品澄清 · 角色 产品 · 执行器 Claude Code · 新建工作区")
    return [
        {"编号": "N1020-10", "时间": "2026-10-20 09:30", "设备": _DEVICE_OTHER, "页": "待处理",
         "事项": "示例项目A：控制台范围", "关联": "N1020-01", "快捷评价": "B", "正文": "",
         "送达": "已推送 09:30 → 主会话 demo-master-b", "转给": "—", "状态": "已送达主会话", "回执": "—"},
        {"编号": "N1020-11", "时间": "2026-10-20 09:40", "设备": _DEVICE_LOCAL, "页": "运行监督",
         "事项": "示例项目A：控制台三栏", "关联": alpha, "快捷评价": "补充", "正文": "顺便看下窄屏",
         "送达": "已推送 09:40 → 派发它的主会话 demo-master", "转给": "跟进 Agent（demo-follow-a）",
         "状态": "已转给跟进", "回执": "—"},
        {"编号": "N1020-12", "时间": "2026-10-20 09:50", "设备": _DEVICE_LOCAL, "页": "待处理",
         "事项": "示例项目A：控制台三栏", "关联": "N1020-03", "快捷评价": "打回",
         "正文": "深色模式下按钮看不清", "送达": "已推送 09:50 → 派发它的主会话 demo-master",
         "转给": "跟进 Agent（demo-follow-a）", "状态": "跟进已收到",
         "回执": "10-20 09:52 已收到 N1020-12，按打回派修复"},
        {"编号": "N1020-13", "时间": "2026-10-20 09:55", "设备": _DEVICE_LOCAL, "页": "运行监督",
         "事项": "示例项目B：demo", "关联": gamma, "快捷评价": "问进度", "正文": "",
         "送达": "已推送 09:55 → 派发它的主会话 demo-master", "转给": "跟进 Agent（demo-follow）",
         "状态": "转不到：接收方会话不在了", "回执": "—"},
        {"编号": "N1020-14", "时间": "2026-10-20 09:58", "设备": _DEVICE_LOCAL, "页": "派发确认",
         "事项": "指挥台：派发确认", "关联": "—", "快捷评价": "", "正文": lines,
         "送达": "已推送 09:58 → 派发它的主会话 demo-master", "转给": "主会话自己",
         "状态": "主会话已处理", "回执": "—"},
        {"编号": "N1020-15", "时间": "2026-10-20 09:59", "设备": _DEVICE_LOCAL, "页": "待处理",
         "事项": "示例项目A：只读冒烟", "关联": "N1020-05", "快捷评价": "验收通过", "正文": "",
         "送达": "未推送：没有在 Orca 里登记的主会话", "转给": "—", "状态": "等主会话", "回执": "—"},
    ]


_REPLY_COLUMNS = ("编号", "时间", "设备", "页", "事项", "关联", "快捷评价", "正文", "送达", "转给", "状态", "回执")


def _replies_doc(base) -> _Doc:
    d = _Doc(_REPLIES_REL)
    for line in ("---", "type: replies", "slug: replies", "status: active", "updated: 2026-10-20",
                 "tags: [meta, comms]", 'summary: "示例回复分表（虚构）"', "---", "",
                 "# 指挥台回复（虚构）", "",
                 "| " + " | ".join(_REPLY_COLUMNS) + " |", "|" + "---|" * len(_REPLY_COLUMNS)):
        d.add(line)
    for row in _reply_rows(base):
        d.add("| " + " | ".join(row[c] for c in _REPLY_COLUMNS) + " |")
    return d


def _device_files() -> dict:
    return {f"registry/devices/{fp}.yaml":
            f"fingerprint: {fp}\nhostname: {name}\nsystem: Windows\nrepos: {{}}\n"
            for fp, name in ((_DEVICE_LOCAL, _DEVICE_LOCAL_NAME), (_DEVICE_OTHER, _DEVICE_OTHER_NAME))}


def _snapshot_replies(base) -> list:
    """快照 replies：新的在前；不投影 handle（转给只留括号前），关联 — 记为 None。"""
    alpha = _abs(base, "runs/2026-10-20-example-alpha.md")
    gamma = _abs(base, "runs/2026-10-20-example-gamma.md")
    lines = _reply_rows(base)[4]["正文"]

    def reply(rid, at, card, page, item, association, quick, text, status, *, note=None,
              to=None, ack=None, device=_DEVICE_LOCAL):
        return {"id": rid, "createdAt": f"2026-10-20T{at}:00+08:00", "card": card, "page": page,
                "item": item, "association": association, "quick": quick, "text": text,
                "status": status, "statusNote": note, "forwardedTo": to, "ack": ack,
                "device": _reply_device(device)}

    return [
        reply("N1020-15", "09:59", "pending:N1020-05", "pending", "示例项目A：只读冒烟", "N1020-05",
              "验收通过", "", "waiting"),
        reply("N1020-14", "09:58", "dispatch", "dispatch", "指挥台：派发确认", None, None, lines,
              "handled", to="主会话自己"),
        reply("N1020-13", "09:55", "run:runs/2026-10-20-example-gamma.md", "runs", "示例项目B：demo",
              gamma, "问进度", "", "lost", note="接收方会话不在了", to=_FOLLOW),
        reply("N1020-12", "09:50", "pending:N1020-03", "pending", "示例项目A：控制台三栏", "N1020-03",
              "打回", "深色模式下按钮看不清", "acked", to=_FOLLOW,
              ack={"at": "2026-10-20T09:52:00+08:00", "text": "已收到 N1020-12，按打回派修复"}),
        reply("N1020-11", "09:40", "run:runs/2026-10-20-example-alpha.md", "runs", "示例项目A：控制台三栏",
              alpha, "补充", "顺便看下窄屏", "forwarded", to=_FOLLOW),
        reply("N1020-10", "09:30", "pending:N1020-01", "pending", "示例项目A：控制台范围", "N1020-01",
              "B", "", "delivered", device=_DEVICE_OTHER),
    ]


def _apply_replies(model: dict, base) -> None:
    """把回复层叠到 full-* 的 model 上（磁盘文件、快照、manifest 预期）。"""
    model["docs"].append(_replies_doc(base))
    model["files"].update(_device_files())
    snap = model["snapshot"]
    snap["environment"]["device"] = {"id": _DEVICE_LOCAL, "name": _DEVICE_LOCAL_NAME}
    for item in snap["pending"]:
        item["reply"] = _pending_slot(item)
    for run in snap["runs"]:
        run["reply"] = _run_slot(run)
    snap["replies"] = _snapshot_replies(base)
    slots = {}
    for item in snap["pending"] + snap["runs"]:
        slot = item["reply"]
        if slot is not None:
            slots[slot["card"]] = {"allowed": slot["allowed"], "kind": slot["kind"],
                                   "reason": slot["reason"], "receiver": slot["receiver"]}
    model["expected"]["replies"] = {
        "ids": [r["id"] for r in snap["replies"]],
        "statuses": {r["id"]: r["status"] for r in snap["replies"]},
        "slots": slots,
        "noSlot": [f"pending:{p['id']}" for p in snap["pending"] if p["reply"] is None]
                  + [f"run:{r['id']}" for r in snap["runs"] if r["reply"] is None],
        "device": {"id": _DEVICE_LOCAL, "name": _DEVICE_LOCAL_NAME},
    }
    model["environment"] = snap["environment"]


def _docs_full(now: datetime, base, replies: bool = False) -> tuple[list[_Doc], dict, dict]:
    """全量磁盘记录（full-orca 与 full-local 共用；环境差异只在快照/manifest）。"""
    today = now.date().isoformat()
    docs = []
    line_marks = {"pending": {}, "daily": {}, "runs": {}, "roles": {}, "reviews": {}}

    rows = [
        {"id": "N1020-01", "when": "2026-10-20 09:12", "type": "要拍板", "source": "产品 Agent",
         "item": "示例项目A：控制台范围",
         "text": "两个方案等你选（选项：A 继续原范围；B 缩小范围）",
         "detail": _abs(base, "runs/2026-10-20-example-gamma.md"),
         "delivery": "已推送 09:12 → 主会话", "status": "待处理"},
        {"id": "N1020-02", "when": "2026-10-20 09:15", "type": "卡住", "source": "排查 Agent",
         "item": "示例项目A：示例接口 502",
         "text": "复现脚本跑不通（建议：先只读定位根因，再决定是否改代码）",
         "detail": _abs(base, "runs/2026-10-20-example-alpha.md"),
         "delivery": "已推送 09:15 → 主会话", "status": "待处理"},
        {"id": "N1020-03", "when": "2026-10-20 09:42", "type": "完成", "source": "开发 Agent",
         "item": "示例项目A：控制台三栏",
         "text": "三栏与体验区已就绪，可以验收",
         "detail": _abs(base, "runs/2026-10-20-example-alpha.md"),
         "delivery": "已推送 09:42 → 主会话", "status": "待处理"},
        {"id": "N1020-04", "when": "2026-10-20 09:52", "type": "完成", "source": "开发 Agent",
         "item": "示例项目A：导出指令",
         "text": "复制框与指令已就绪，没有界面",
         "detail": _abs(base, "runs/2026-10-20-example-beta.md"),
         "delivery": "已推送 09:52 → 主会话", "status": "待处理"},
        {"id": "N1020-05", "when": "2026-10-20 09:46", "type": "完成", "source": "验收 Agent",
         "item": "示例项目A：只读冒烟",
         "text": "冒烟跑完，体验信息未记录",
         "detail": _abs(base, "runs/2026-10-20-example-delta.md"),
         "delivery": "已推送 09:46 → 主会话", "status": "待处理"},
        {"id": "N1020-06", "when": "2026-10-20 09:51", "type": "完成", "source": "调研 Agent",
         "item": "示例项目A：现状报告",
         "text": "现状报告已出，附静态页面",
         "detail": _abs(base, "runs/2026-10-20-example-epsilon.md"),
         "delivery": "已推送 09:51 → 主会话", "status": "待处理"},
        {"id": "N1020-07", "when": "2026-10-20 09:59", "type": "完成", "source": "产品 Agent",
         "item": "示例项目A：体验说明",
         "text": "说明页已更新，可以体验",
         "detail": _abs(base, "runs/2026-10-20-example-zeta.md"),
         "delivery": "已推送 09:59 → 主会话", "status": "待处理"},
        {"id": "N1020-08", "when": "2026-10-20 09:05", "type": "交接", "source": "产品 Agent",
         "item": "示例项目A：PRD 定稿交接",
         "text": "PRD 已定稿，交给开发",
         "detail": _abs(base, "runs/2026-10-20-example-gamma.md"),
         "delivery": "已推送 09:05 → 主会话", "status": "待处理"},
        {"id": "N1019-09", "when": "2026-10-19 18:00", "type": "交接", "source": "产品 Agent",
         "item": "示例项目A：上周交接",
         "text": "交接已完成",
         "detail": _abs(base, "runs/2026-10-20-example-gamma.md"),
         "delivery": "已推送 18:00 → 主会话", "status": "已处理 10-19 18:20：已合并并更新计划"},
    ]
    pending_doc, pending_marks = _pending_doc(rows, today=today)
    docs.append(pending_doc)
    line_marks["pending"] = pending_marks

    daily_doc, daily_marks = _daily_doc(_DAILY_TASKS_FULL, today=today)
    docs.append(daily_doc)
    line_marks["daily"] = daily_marks

    # ---- 运行记录
    alpha, alpha_marks = _run_doc(
        "2026-10-20-example-alpha",
        _dev(replies, "2026-10-20-example-alpha") + [("type", "run"), ("slug", "2026-10-20-example-alpha"), ("status", "dispatched"),
         ("created", "2026-10-20 09:00"), ("updated", "2026-10-20"),
         ("task", "2026-10-20-example-plan#T3"), ("project", "示例项目A"),
         ("repo", "demo-owner/demo-repo"), ("agent", "opencode"),
         ("mode", "orca-new-worktree"), ("target", "demo-workspace-a"),
         ("handle", "demo-7f3a"), ("report_to", "demo-master"),
         ("report_session", "demo1234"), ("role", "dev"),
         ("summary", '"示例：控制台三栏与体验区"')],
        "[执行] 示例项目A · T3 控制台三栏",
        [("任务说明", ["目标：三栏与体验区可验收", "验收：打开本地页面即可操作"]),
         ("监督", ["- 2026-10-20 09:41 — 活跃：三栏计数用例通过"]),
         *_follow_section(replies, "2026-10-20-example-alpha"),
         ("决策记录", ["- 2026-10-20 09:20 — 计数口径 → 按记录统计 → 理由：不另造积分"]),
         ("体验", ["- 体验卡：inbox/experience/demo-console-started.json",
                   "- 用例：6/7 通过（1 条失败：窄屏单列）",
                   "- 重点：整体视图五项指标；三栏计数；体验区链接"])],
    )
    docs.append(alpha)
    line_marks["runs"][alpha.rel] = alpha_marks

    beta, beta_marks = _run_doc(
        "2026-10-20-example-beta",
        _dev(replies, "2026-10-20-example-beta") + [("type", "run"), ("slug", "2026-10-20-example-beta"), ("status", "dispatched"),
         ("created", "2026-10-20 08:30"), ("updated", "2026-10-20"),
         ("task", "2026-10-20-example-plan#T3"), ("project", "示例项目A"),
         ("repo", "demo-owner/demo-repo"), ("agent", "opencode"),
         ("mode", "orca-new-worktree"), ("target", "demo-workspace-b"), ("role", "dev"),
         ("summary", '"示例：导出与复制框"')],
        "[执行] 示例项目A · T3 导出与复制框",
        [("任务说明", ["目标：只读文本框与复制兜底"]),
         ("监督", ["- 2026-10-20 09:50 — 活跃：复制框完成"]),
         ("体验", ["没有界面，不需要体验环境"])],
    )
    docs.append(beta)
    line_marks["runs"][beta.rel] = beta_marks

    gamma, gamma_marks = _run_doc(
        "2026-10-20-example-gamma",
        _dev(replies, "2026-10-20-example-gamma") + [("type", "run"), ("kind", "delivery"), ("slug", "2026-10-20-example-gamma"),
         ("status", "dispatched"), ("created", "2026-10-20 09:05"),
         ("updated", "2026-10-20"), ("project", "示例项目B"), ("repo", "demo-owner/demo-repo"),
         ("req", "demo"), ("dev_branch", "develop"), ("req_branch", "req/demo"),
         ("req_worktree", "C:/example-device/worktrees/demo"),
         ("follower", "demo-follow"), ("report_to", "demo-master"),
         ("report_session", "demo1234"), ("role", "pm"),
         ("summary", '"示例：三个包的交付看板"')],
        "[交付] 示例项目B · demo",
        [("需求", ["- PRD：docs/delivery/demo/prd.md", "- 产物目录：docs/delivery/demo/"]),
         ("阶段", ["- 当前：集成测试"]),
         ("工作包", ["| 包 | 状态 | 依赖 | 分支 | 工作区 | 会话 | 审查轮次 | 更新 |",
                     "|---|---|---|---|---|---|---|---|",
                     "| P0 | 完成 | — | pkg/P0 | wt-demo-p0 | handle-p0 | 1 | 2026-10-20 |",
                     "| P1 | 完成 | P0 | pkg/P1 | wt-demo-p1 | handle-p1 | 2 | 2026-10-20 |",
                     "| P2 | 进行中 | P1 | pkg/P2 | wt-demo-p2 | handle-p2 | 1 | 2026-10-20 |"]),
         ("会话", ["- 2026-10-20 09:22 — 开发 / dev-build · Orca 工作区 demo-wt-p2 · handle demo-p2",
                   "- 2026-10-20 09:24 — 验收 / accept-test · Orca 工作区 demo-wt-acc · handle demo-acc",
                   "- 2026-10-20 09:26 — 跟进 / task-follow · assistant · handle demo-follow"]),
         ("决策记录", ["- 2026-10-20 09:30 — P1 修复范围 → 只改计数 → 理由：范围内"]),
         ("监督", ["- 2026-10-20 09:55 — 活跃：P2 集成测试 3/7 用例通过"]),
         ("体验", ["- 体验卡：无（集成测试未完成）"])],
    )
    docs.append(gamma)
    line_marks["runs"][gamma.rel] = gamma_marks

    delta, delta_marks = _run_doc(
        "2026-10-20-example-delta",
        _dev(replies, "2026-10-20-example-delta") + [("type", "run"), ("slug", "2026-10-20-example-delta"), ("status", "dispatched"),
         ("created", "2026-10-20 09:10"), ("updated", "2026-10-20"),
         ("task", "2026-10-20-example-plan#T1"), ("project", "示例项目A"),
         ("repo", "demo-owner/demo-repo"), ("agent", "codex"),
         ("mode", "orca-existing"), ("target", "demo-workspace-a"), ("role", "accept"),
         ("summary", '"示例：只读冒烟"')],
        "[执行] 示例项目A · 只读冒烟",
        [("任务说明", ["目标：按用例只读冒烟"]),
         ("监督", ["- 2026-10-20 09:45 — 活跃：冒烟完成，等待回报"]),
         ("验收", ["结果：冒烟通过；体验信息未记录"])],
    )
    docs.append(delta)
    line_marks["runs"][delta.rel] = delta_marks

    epsilon, epsilon_marks = _run_doc(
        "2026-10-20-example-epsilon",
        _dev(replies, "2026-10-20-example-epsilon") + [("type", "run"), ("slug", "2026-10-20-example-epsilon"), ("status", "dispatched"),
         ("created", "2026-10-20 09:20"), ("updated", "2026-10-20"),
         ("task", "2026-10-20-example-plan#T5"), ("project", "示例项目A"),
         ("repo", "demo-owner/demo-repo"), ("agent", "claude"),
         ("mode", "orca-existing"), ("target", "demo-workspace-a"), ("role", "research"),
         ("summary", '"示例：现状报告与静态页面"')],
        "[执行] 示例项目A · 现状报告",
        [("任务说明", ["目标：出现状报告和静态页面"]),
         ("监督", ["- 2026-10-20 09:50 — 活跃：报告已写完"]),
         ("体验", ["- 体验卡：inbox/experience/demo-static.json",
                   "- 静态页面：docs/demo/report.html",
                   "- 重点：现状结论；已知问题"])],
    )
    docs.append(epsilon)
    line_marks["runs"][epsilon.rel] = epsilon_marks

    zeta, zeta_marks = _run_doc(
        "2026-10-20-example-zeta",
        _dev(replies, "2026-10-20-example-zeta") + [("type", "run"), ("slug", "2026-10-20-example-zeta"), ("status", "dispatched"),
         ("created", "2026-10-20 09:25"), ("updated", "2026-10-20"),
         ("task", "2026-10-20-example-plan#T8"), ("project", "示例项目A"),
         ("repo", "demo-owner/demo-repo"), ("agent", "claude"),
         ("mode", "orca-new-worktree"), ("target", "demo-workspace-c"), ("role", "product"),
         ("summary", '"示例：体验说明"')],
        "[执行] 示例项目A · 体验说明",
        [("任务说明", ["目标：更新体验说明页"]),
         ("监督", ["- 2026-10-20 09:58 — 活跃：说明页已更新"]),
         ("体验", ["- 体验卡：inbox/experience/demo-expired.json",
                   "- 用例：4/5 通过（1 条未通过）",
                   "- 重点：说明文案；状态提示"])],
    )
    docs.append(zeta)
    line_marks["runs"][zeta.rel] = zeta_marks

    eta, eta_marks = _run_doc(
        "2026-10-19-example-eta",
        _dev(replies, "2026-10-19-example-eta") + [("type", "run"), ("slug", "2026-10-19-example-eta"), ("status", "done"),
         ("created", "2026-10-19 14:00"), ("updated", "2026-10-19"),
         ("task", "2026-10-19-example-plan#T2"), ("project", "示例项目A"),
         ("repo", "demo-owner/demo-repo"), ("agent", "opencode"),
         ("mode", "orca-new-worktree"), ("target", "demo-workspace-d"), ("role", "dev"),
         ("summary", '"示例：上一轮已通关"')],
        "[执行] 示例项目A · T2 上一轮修复",
        [("任务说明", ["目标：修一个示例缺陷"]),
         ("监督", ["- 2026-10-19 17:30 — 已结束：提交示例提交"]),
         ("验收", ["结果：通过", "证据：示例提交 demo0001；首轮审查通过"])],
    )
    docs.append(eta)
    line_marks["runs"][eta.rel] = eta_marks

    theta, theta_marks = _run_doc(
        "2026-10-20-example-theta",
        _dev(replies, "2026-10-20-example-theta") + [("type", "run"), ("slug", "2026-10-20-example-theta"), ("status", "failed"),
         ("created", "2026-10-20 09:30"), ("updated", "2026-10-20"),
         ("task", "2026-10-20-example-plan#T2"), ("project", "示例项目A"),
         ("repo", "demo-owner/demo-repo"), ("agent", "codex"),
         ("mode", "orca-existing"), ("target", "demo-workspace-a"), ("role", "investigate"),
         ("summary", '"示例：排查失败"')],
        "[执行] 示例项目A · 示例排查失败",
        [("任务说明", ["目标：只读定位 502"]),
         ("监督", ["- 2026-10-20 09:32 — 失败：示例接口不可达"]),
         ("验收", ["结果：未通过；原因：示例环境不可达"])],
    )
    docs.append(theta)
    line_marks["runs"][theta.rel] = theta_marks

    kappa, kappa_marks = _run_doc(
        "2026-10-20-example-kappa",
        _dev(replies, "2026-10-20-example-kappa") + [("type", "run"), ("slug", "2026-10-20-example-kappa"), ("status", "dispatched"),
         ("created", "2026-10-20 09:00"), ("updated", "2026-10-20"),
         ("task", "2026-10-20-example-plan#T2"), ("project", "示例项目A"),
         ("repo", "demo-owner/demo-repo"), ("agent", "codex"),
         ("mode", "orca-existing"), ("target", "demo-workspace-a"), ("role", "investigate"),
         ("summary", '"示例：停滞的排查"')],
        "[执行] 示例项目A · 停滞的排查",
        [("任务说明", ["目标：只读复现"]),
         ("监督", ["- 2026-10-20 09:15 — 活跃：复现脚本跑不通"]),
         ("决策记录", ["- 2026-10-20 09:10 — 是否改代码 → 先不改 → 理由：只读"])],
    )
    docs.append(kappa)
    line_marks["runs"][kappa.rel] = kappa_marks

    # ---- 角色
    for role_id, spec in _ROLE_SPECS_FULL.items():
        doc, fields = _role_doc(role_id, **spec)
        docs.append(doc)
        line_marks["roles"][role_id] = fields

    # ---- 体验卡（原始 JSON 里的 cmd/pid/handle/token 都不许进快照）
    started_url = f"http://127.0.0.1:8801/console?view=summary"
    files = {
        "inbox/experience/demo-console-started.json": _json_text({
            "url": started_url,
            "started": True,
            "startedAt": "2026-10-20T09:40:00+08:00",
            "expiresAt": "2026-10-20T11:40:00+08:00",
            "tests": {"passed": 6, "total": 7, "summary": "1 条失败：窄屏单列"},
            "focus": ["整体视图五项指标", "待处理三栏计数", "体验区链接"],
            "cmd": "python -m http.server 8801",
            "pid": 4242,
            "handle": "demo-7f3a",
        }),
        "inbox/experience/demo-static.json": _json_text({
            "url": _file_uri(base, "docs/demo/report.html"),
            "started": False,
            "focus": ["现状结论", "已知问题"],
        }),
        "inbox/experience/demo-expired.json": _json_text({
            "url": f"http://127.0.0.1:8802/focus",
            "started": True,
            "startedAt": "2026-10-20T09:10:00+08:00",
            "expiresAt": "2026-10-20T09:30:00+08:00",
            "tests": {"passed": 4, "total": 5, "summary": "1 条未通过"},
            "focus": ["说明文案", "状态提示"],
            "token": "demo-not-a-real-token",
        }),
    }

    # ---- 审查报告 + PRD + 静态页
    p0, p0_fields = _review_doc("docs/delivery/demo/reviews/P0-r1.md",
                                slug="demo-P0-r1", verdict="通过",
                                summary="示例：首轮通过")
    p1, p1_fields = _review_doc("docs/delivery/demo/reviews/P1-r1.md",
                                slug="demo-P1-r1", verdict="要修",
                                summary="示例：首轮要修")
    p2, p2_fields = _review_doc("docs/delivery/demo/reviews/P2-r2.md",
                                slug="demo-P2-r2", verdict="通过",
                                summary="示例：非首轮，不能当首轮证据")
    docs.extend([p0, p1, p2])
    line_marks["reviews"] = {
        "docs/delivery/demo/reviews/P0-r1.md": p0_fields,
        "docs/delivery/demo/reviews/P1-r1.md": p1_fields,
        "docs/delivery/demo/reviews/P2-r2.md": p2_fields,
    }
    files["docs/delivery/demo/prd.md"] = (
        "---\ntype: prd\nslug: demo\nstatus: active\nupdated: 2026-10-20\n"
        'summary: "虚构夹具 PRD"\n---\n\n# 示例需求\n\n虚构内容，只用于控制台夹具。\n'
    )
    files["docs/demo/report.html"] = "<!doctype html><title>示例静态页</title><p>虚构内容</p>\n"

    return docs, files, line_marks


def _snapshot_full(local: bool, now: datetime, base, marks) -> dict:
    a = "runs/2026-10-20-example-alpha.md"
    b = "runs/2026-10-20-example-beta.md"
    g = "runs/2026-10-20-example-gamma.md"
    d = "runs/2026-10-20-example-delta.md"
    e = "runs/2026-10-20-example-epsilon.md"
    z = "runs/2026-10-20-example-zeta.md"
    eta = "runs/2026-10-19-example-eta.md"
    th = "runs/2026-10-20-example-theta.md"
    ka = "runs/2026-10-20-example-kappa.md"

    run_marks = marks["runs"]
    role_marks = marks["roles"]

    def f(rel, key):
        return _src(base, rel, run_marks[rel]["fields"][key], key)

    def s(rel, heading, field):
        start, end = run_marks[rel]["sections"][heading]
        return _src(base, rel, start, field, end)

    def row(pid):
        return _src(base, _PENDING_REL, marks["pending"][pid], "row")

    def role_src(rid, key="status"):
        return _src(base, f"agents/{rid}.md", role_marks[rid][key], key)

    exp_started = _exp(
        ui="yes", state="recorded-started",
        url="http://127.0.0.1:8801/console?view=summary",
        recorded="2026-10-20T09:40:00+08:00",
        expires="2026-10-20T11:40:00+08:00",
        tests={"passed": 6, "total": 7, "summary": "1 条失败：窄屏单列"},
        focus=["整体视图五项指标", "待处理三栏计数", "体验区链接"],
        sources=[s(a, "体验", "体验"),
                 _src(base, "inbox/experience/demo-console-started.json", 2, "url")],
    )
    exp_no_ui = _exp(
        ui="no", state="unknown",
        sources=[s(b, "体验", "体验")],
    )
    exp_unknown = _exp(ui="unknown", state="unknown")
    exp_static = _exp(
        ui="yes", state="static",
        url=_file_uri(base, "docs/demo/report.html"),
        focus=["现状结论", "已知问题"],
        sources=[s(e, "体验", "体验"),
                 _src(base, "inbox/experience/demo-static.json", 2, "url")],
    )
    exp_expired = _exp(
        ui="yes", state="expired",
        url="http://127.0.0.1:8802/focus",
        recorded="2026-10-20T09:10:00+08:00",
        expires="2026-10-20T09:30:00+08:00",
        tests={"passed": 4, "total": 5, "summary": "1 条未通过"},
        focus=["说明文案", "状态提示"],
        sources=[s(z, "体验", "体验"),
                 _src(base, "inbox/experience/demo-expired.json", 2, "url")],
    )

    pending = [
        _pending(
            pid="N1020-01", ptype="要拍板", column="要拍板",
            project="示例项目A", item="控制台范围", title="控制台范围",
            summary="两个方案等你选（选项：A 继续原范围；B 缩小范围）",
            sender="产品 Agent", role_id="product",
            created_at="2026-10-20T09:12:00+08:00",
            detail=_abs(base, g), run_id=g,
            choices=[_choice("A", "A 继续原范围", "记录：选项"),
                     _choice("B", "B 缩小范围", "记录：选项")],
            sources=[row("N1020-01")],
        ),
        _pending(
            pid="N1020-02", ptype="卡住", column="卡住",
            project="示例项目A", item="示例接口 502", title="示例接口 502",
            summary="复现脚本跑不通（建议：先只读定位根因，再决定是否改代码）",
            sender="排查 Agent", role_id="investigate",
            created_at="2026-10-20T09:15:00+08:00",
            detail=_abs(base, a), run_id=a,
            choices=[_choice("suggestion", "先只读定位根因，再决定是否改代码", "记录：建议")],
            sources=[row("N1020-02")],
        ),
        _pending(
            pid="N1020-03", ptype="完成", column="待验收",
            project="示例项目A", item="控制台三栏", title="控制台三栏",
            summary="三栏与体验区已就绪，可以验收",
            sender="开发 Agent", role_id="dev",
            created_at="2026-10-20T09:42:00+08:00",
            detail=_abs(base, a), run_id=a,
            choices=list(ACCEPT_CHOICES), experience=exp_started,
            sources=[row("N1020-03")],
        ),
        _pending(
            pid="N1020-04", ptype="完成", column="待验收",
            project="示例项目A", item="导出指令", title="导出指令",
            summary="复制框与指令已就绪，没有界面",
            sender="开发 Agent", role_id="dev",
            created_at="2026-10-20T09:52:00+08:00",
            detail=_abs(base, b), run_id=b,
            choices=list(ACCEPT_CHOICES), experience=exp_no_ui,
            sources=[row("N1020-04")],
        ),
        _pending(
            pid="N1020-05", ptype="完成", column="待验收",
            project="示例项目A", item="只读冒烟", title="只读冒烟",
            summary="冒烟跑完，体验信息未记录",
            sender="验收 Agent", role_id="accept",
            created_at="2026-10-20T09:46:00+08:00",
            detail=_abs(base, d), run_id=d,
            choices=list(ACCEPT_CHOICES), experience=exp_unknown,
            sources=[row("N1020-05")],
            diagnostics=[_diag("experience_unknown", "记录未提供体验信息，按未知展示", "N1020-05")],
        ),
        _pending(
            pid="N1020-06", ptype="完成", column="待验收",
            project="示例项目A", item="现状报告", title="现状报告",
            summary="现状报告已出，附静态页面",
            sender="调研 Agent", role_id="research",
            created_at="2026-10-20T09:51:00+08:00",
            detail=_abs(base, e), run_id=e,
            choices=list(ACCEPT_CHOICES), experience=exp_static,
            sources=[row("N1020-06")],
        ),
        _pending(
            pid="N1020-07", ptype="完成", column="待验收",
            project="示例项目A", item="体验说明", title="体验说明",
            summary="说明页已更新，可以体验",
            sender="产品 Agent", role_id="product",
            created_at="2026-10-20T09:59:00+08:00",
            detail=_abs(base, z), run_id=z,
            choices=list(ACCEPT_CHOICES), experience=exp_expired,
            sources=[row("N1020-07")],
        ),
        _pending(
            pid="N1020-08", ptype="交接", column=None,
            project="示例项目A", item="PRD 定稿交接", title="PRD 定稿交接",
            summary="PRD 已定稿，交给开发",
            sender="产品 Agent", role_id="product",
            created_at="2026-10-20T09:05:00+08:00",
            detail=_abs(base, g), run_id=g, choices=[],
            sources=[row("N1020-08")],
        ),
        _pending(
            pid="N1019-09", ptype="交接", column=None, status="已处理",
            project="示例项目A", item="上周交接", title="上周交接",
            summary="交接已完成",
            sender="产品 Agent", role_id="product",
            created_at="2026-10-19T18:00:00+08:00",
            detail=_abs(base, g), run_id=g, choices=[],
            processed_at="2026-10-19T18:20:00+08:00",
            processed_note="已合并并更新计划",
            sources=[row("N1019-09")],
        ),
    ]

    plan_rel = _DAILY_REL

    def dsrc(task_no, key, field):
        m = marks["daily"][task_no]
        if key == "task":
            return _src(base, plan_rel, m["start"], field, m["end"])
        line = m["subs"][key]
        return _src(base, plan_rel, line, field)

    dispatch = [
        _dispatch(
            tid="T2", task_no="T2", project="示例项目A", title="只读排查示例",
            summary="只读定位示例问题根因，不改代码", plan_rel=plan_rel, base=base,
            lines=[("标题", marks["daily"]["T2"]["start"], marks["daily"]["T2"]["end"]),
                   ("角色", marks["daily"]["T2"]["subs"]["角色"], marks["daily"]["T2"]["subs"]["角色"])],
            role_id="investigate", declared="codex", changes=False, difficulty=1,
            stages=["复现", "定位根因", "出结论"], outputs=["排查结论"],
            default_executor="codex",
            default_mode="terminal" if local else "directory",
            default_reason=("任务已声明：Codex · 新终端窗口（不在 Orca 里）" if local
                            else "任务已声明：Codex · 现有目录"),
        ),
        _dispatch(
            tid="T3", task_no="T3", project="示例项目A", title="控制台示例修复",
            summary="修三栏计数与体验区", plan_rel=plan_rel, base=base,
            lines=[("标题", marks["daily"]["T3"]["start"], marks["daily"]["T3"]["end"]),
                   ("角色", marks["daily"]["T3"]["subs"]["角色"], marks["daily"]["T3"]["subs"]["角色"])],
            role_id="dev", declared="opencode", changes=True, difficulty=2,
            stages=["实现", "自测", "提交"], outputs=["代码提交"],
            default_executor="opencode",
            default_mode="terminal" if local else "worktree",
            default_reason=("任务已声明：OpenCode · 新终端窗口（不在 Orca 里）" if local
                            else "任务已声明：OpenCode · 新建工作区"),
        ),
        _dispatch(
            tid="T4", task_no="T4", project="示例项目B", title="示例需求交付",
            summary="团队副本：开发 · 验收 · 跟进", plan_rel=plan_rel, base=base,
            lines=[("标题", marks["daily"]["T4"]["start"], marks["daily"]["T4"]["end"]),
                   ("工作流", marks["daily"]["T4"]["subs"]["工作流"],
                    marks["daily"]["T4"]["subs"]["工作流"]),
                   ("PRD", marks["daily"]["T4"]["subs"]["PRD"], marks["daily"]["T4"]["subs"]["PRD"])],
            role_id="dev", declared="opencode", changes=True, kind="delivery",
            prd_rel="docs/delivery/demo/prd.md", difficulty=3,
            stages=["实现计划 + 用例", "工作包开发", "审查与修复", "集成测试",
                    "合回 develop", "你来体验"],
            outputs=["体验链接", "测试报告"],
            default_executor=None, default_mode="worktree",
            default_reason="团队副本：固定三人小队（开发 · OpenCode、验收 · Codex、跟进 · Claude Code）",
            selectable=not local,
            disabled_reason=("不在 Orca 里：团队副本不能进入" if local else None),
        ),
        _dispatch(
            tid="T5", task_no="T5", project="示例项目B", title="示例调研（无角色）",
            summary="整理现状，不改代码", plan_rel=plan_rel, base=base,
            lines=[("标题", marks["daily"]["T5"]["start"], marks["daily"]["T5"]["end"]),
                   ("说明", marks["daily"]["T5"]["subs"]["说明"], marks["daily"]["T5"]["subs"]["说明"])],
            role_id=None, declared="claude", changes=False, difficulty=1,
            stages=["收集", "整理", "出报告"], outputs=["现状报告"],
            default_executor="claude",
            default_mode="terminal" if local else "directory",
            default_reason=("任务已声明执行器 Claude Code；不改代码，地点用新终端窗口（不在 Orca 里）"
                            if local else
                            "任务已声明执行器 Claude Code；不改代码，地点用现有目录"),
        ),
        _dispatch(
            tid="T6", task_no="T6", project="示例项目B", title="无角色无执行器",
            summary="需要改代码的小修", plan_rel=plan_rel, base=base,
            lines=[("标题", marks["daily"]["T6"]["start"], marks["daily"]["T6"]["end"]),
                   ("说明", marks["daily"]["T6"]["subs"]["说明"], marks["daily"]["T6"]["subs"]["说明"])],
            role_id=None, declared=None, changes=True, difficulty=2,
            stages=[], outputs=[],
            default_executor="opencode",
            default_mode="terminal" if local else "worktree",
            default_reason=("无角色与执行器：按改代码兜底 OpenCode；不在 Orca 里只能新终端窗口（兜底）"
                            if local else
                            "无角色与执行器：按改代码兜底 OpenCode · 新建工作区（兜底）"),
            fallback=True,
        ),
        _dispatch(
            tid="T7", task_no="T7", project="示例项目B", title="证据不足示例",
            summary="等主会话补充角色与执行器", plan_rel=plan_rel, base=base,
            lines=[("标题", marks["daily"]["T7"]["start"], marks["daily"]["T7"]["end"]),
                   ("说明", marks["daily"]["T7"]["subs"]["说明"], marks["daily"]["T7"]["subs"]["说明"])],
            role_id=None, declared=None, changes=None, difficulty=None,
            stages=[], outputs=[],
            default_executor=None,
            default_mode="terminal" if local else None,
            default_reason="缺少角色、执行器与是否改代码的证据，暂不预选执行器与地点，等你选择",
        ),
        _dispatch(
            tid="T8", task_no="T8", project="示例项目A", title="示例产品澄清",
            summary="澄清文案，不改代码", plan_rel=plan_rel, base=base,
            lines=[("标题", marks["daily"]["T8"]["start"], marks["daily"]["T8"]["end"]),
                   ("角色", marks["daily"]["T8"]["subs"]["角色"], marks["daily"]["T8"]["subs"]["角色"])],
            role_id="product", declared="codex", changes=False, difficulty=1,
            stages=[], outputs=[],
            default_executor="codex",
            default_mode="terminal" if local else "worktree",
            default_reason=("任务已声明执行器 Codex；地点按产品角色默认，但不在 Orca 里只能新终端窗口"
                            if local else
                            "任务已声明执行器 Codex；地点按产品角色默认新建工作区（PRD 4.4）"),
        ),
    ]

    runs = [
        _run(
            rid=a, kind="single", project="示例项目A", item="T3 控制台三栏",
            status="waiting", raw_status="dispatched", role_id="dev",
            created="2026-10-20T09:00:00+08:00", completed=None,
            changed="2026-10-20T09:41:00+08:00", stale=False,
            stages=[_stage("实现", "done", s(a, "监督", "监督")),
                    _stage("自测", "current", s(a, "监督", "监督")),
                    _stage("提交", "unknown", s(a, "监督", "监督"))],
            agents=[_agent("agent-1", role_id="dev", executor="opencode",
                           activity="跑三栏计数用例", state="active",
                           changed="2026-10-20T09:41:00+08:00")],
            packages=[],
            progress=["三栏计数用例通过"],
            decisions=["计数口径 → 按记录统计 → 理由：不另造积分"],
            pending_ids=["N1020-03", "N1020-02"],
            detail_rel=a, base=base,
            sources=[f(a, "status"), f(a, "created"), s(a, "监督", "监督"),
                     s(a, "体验", "体验")],
        ),
        _run(
            rid=b, kind="single", project="示例项目A", item="T3 导出与复制框",
            status="waiting", raw_status="dispatched", role_id="dev",
            created="2026-10-20T08:30:00+08:00", completed=None,
            changed="2026-10-20T09:50:00+08:00", stale=False,
            stages=[_stage("实现", "done", s(b, "监督", "监督")),
                    _stage("自测", "current", s(b, "监督", "监督")),
                    _stage("提交", "unknown", s(b, "监督", "监督"))],
            agents=[_agent("agent-2", role_id="dev", executor="opencode",
                           activity="收尾复制框", state="active",
                           changed="2026-10-20T09:50:00+08:00")],
            packages=[], progress=["复制框完成"], decisions=[],
            pending_ids=["N1020-04"], detail_rel=b, base=base,
            sources=[f(b, "status"), s(b, "监督", "监督"), s(b, "体验", "体验")],
        ),
        _run(
            rid=g, kind="delivery", project="示例项目B", item="demo 交付",
            status="waiting", raw_status="dispatched", role_id="pm",
            created="2026-10-20T09:05:00+08:00", completed=None,
            changed="2026-10-20T09:55:00+08:00", stale=False,
            stages=[_stage("实现计划 + 用例", "done", s(g, "阶段", "阶段")),
                    _stage("工作包开发", "done", s(g, "工作包", "工作包")),
                    _stage("审查与修复", "done", s(g, "阶段", "阶段")),
                    _stage("集成测试", "current", s(g, "阶段", "阶段")),
                    _stage("合回 develop", "unknown", s(g, "阶段", "阶段")),
                    _stage("你来体验", "unknown", s(g, "阶段", "阶段"))],
            agents=[_agent("agent-3", role_id="dev", executor="opencode", package_id="P2",
                           activity="P2 开发", state="active",
                           changed="2026-10-20T09:55:00+08:00"),
                    _agent("agent-4", role_id="accept", executor="codex", package_id="P2",
                           activity="P2 集成测试", state="active",
                           changed="2026-10-20T09:55:00+08:00"),
                    _agent("agent-5", role_id="pm", executor="claude",
                           activity="推进集成测试", state="active",
                           changed="2026-10-20T09:55:00+08:00")],
            packages=[
                _pkg("P0", state="完成", review_round=1, updated="2026-10-20",
                     first_review=_review("pass", _src(
                         base, "docs/delivery/demo/reviews/P0-r1.md",
                         marks["reviews"]["docs/delivery/demo/reviews/P0-r1.md"]["verdict"],
                         "verdict"))),
                _pkg("P1", state="完成", deps=["P0"], review_round=2, updated="2026-10-20",
                     first_review=_review("fix", _src(
                         base, "docs/delivery/demo/reviews/P1-r1.md",
                         marks["reviews"]["docs/delivery/demo/reviews/P1-r1.md"]["verdict"],
                         "verdict"))),
                _pkg("P2", state="进行中", deps=["P1"], review_round=1, updated="2026-10-20",
                     first_review=None),
            ],
            progress=["P2 集成测试 3/7 用例通过"],
            decisions=["P1 修复范围 → 只改计数 → 理由：范围内"],
            pending_ids=["N1020-01"], detail_rel=g, base=base,
            sources=[f(g, "status"), f(g, "created"), s(g, "阶段", "阶段"),
                     s(g, "工作包", "工作包"), s(g, "监督", "监督"),
                     s(g, "决策记录", "决策记录")],
        ),
        _run(
            rid=d, kind="single", project="示例项目A", item="只读冒烟",
            status="waiting", raw_status="dispatched", role_id="accept",
            created="2026-10-20T09:10:00+08:00", completed=None,
            changed="2026-10-20T09:45:00+08:00", stale=False,
            stages=[_stage("审查", "done", s(d, "监督", "监督")),
                    _stage("测试", "current", s(d, "监督", "监督"))],
            agents=[_agent("agent-6", role_id="accept", executor="codex",
                           activity="只读冒烟", state="active",
                           changed="2026-10-20T09:45:00+08:00")],
            packages=[], progress=["只读冒烟完成"], decisions=[],
            pending_ids=["N1020-05"], detail_rel=d, base=base,
            sources=[f(d, "status"), s(d, "监督", "监督"), s(d, "验收", "验收")],
        ),
        _run(
            rid=e, kind="single", project="示例项目A", item="现状报告",
            status="waiting", raw_status="dispatched", role_id="research",
            created="2026-10-20T09:20:00+08:00", completed=None,
            changed="2026-10-20T09:50:00+08:00", stale=False,
            stages=[_stage("收集", "done", s(e, "监督", "监督")),
                    _stage("整理", "done", s(e, "监督", "监督")),
                    _stage("出报告", "current", s(e, "监督", "监督"))],
            agents=[_agent("agent-7", role_id="research", executor="claude",
                           activity="写现状报告", state="active",
                           changed="2026-10-20T09:50:00+08:00")],
            packages=[], progress=["报告已写完"], decisions=[],
            pending_ids=["N1020-06"], detail_rel=e, base=base,
            sources=[f(e, "status"), s(e, "监督", "监督"), s(e, "体验", "体验")],
        ),
        _run(
            rid=z, kind="single", project="示例项目A", item="体验说明",
            status="waiting", raw_status="dispatched", role_id="product",
            created="2026-10-20T09:25:00+08:00", completed=None,
            changed="2026-10-20T09:58:00+08:00", stale=False,
            stages=[_stage("澄清", "done", s(z, "监督", "监督")),
                    _stage("写说明", "current", s(z, "监督", "监督"))],
            agents=[_agent("agent-8", role_id="product", executor="claude",
                           activity="更新体验说明", state="active",
                           changed="2026-10-20T09:58:00+08:00")],
            packages=[], progress=["说明页已更新"], decisions=[],
            pending_ids=["N1020-07"], detail_rel=z, base=base,
            sources=[f(z, "status"), s(z, "监督", "监督"), s(z, "体验", "体验")],
        ),
        _run(
            rid=eta, kind="single", project="示例项目A", item="T2 上一轮修复",
            status="done", raw_status="done", role_id="dev",
            created="2026-10-19T14:00:00+08:00", completed="2026-10-19T17:30:00+08:00",
            changed="2026-10-19T17:30:00+08:00", stale=None,
            stages=[_stage("实现", "done", s(eta, "监督", "监督")),
                    _stage("自测", "done", s(eta, "监督", "监督")),
                    _stage("提交", "done", s(eta, "验收", "验收"))],
            agents=[_agent("agent-9", role_id="dev", executor="opencode",
                           activity="已提交", state="done",
                           changed="2026-10-19T17:30:00+08:00")],
            packages=[], progress=["提交示例提交 demo0001"], decisions=[],
            pending_ids=[], detail_rel=eta, base=base,
            first_review=_review("pass", s(eta, "验收", "验收")),
            sources=[f(eta, "status"), f(eta, "created"), s(eta, "验收", "验收")],
        ),
        _run(
            rid=th, kind="single", project="示例项目A", item="示例排查失败",
            status="unknown", raw_status="failed", role_id="investigate",
            created="2026-10-20T09:30:00+08:00", completed=None,
            changed="2026-10-20T09:32:00+08:00", stale=None,
            stages=[_stage("复现", "done", s(th, "监督", "监督")),
                    _stage("定位根因", "problem", s(th, "监督", "监督")),
                    _stage("出结论", "unknown", s(th, "监督", "监督"))],
            agents=[_agent("agent-10", role_id="investigate", executor="codex",
                           activity="失败：示例接口不可达", state="problem",
                           changed="2026-10-20T09:32:00+08:00")],
            packages=[], progress=["失败：示例接口不可达"], decisions=[],
            pending_ids=[], detail_rel=th, base=base,
            sources=[f(th, "status"), s(th, "监督", "监督"), s(th, "验收", "验收")],
            diagnostics=[_diag("run_status_unmapped",
                               "运行状态 failed 不是允许的终态，按未知展示，保留原状态说明",
                               th)],
        ),
        _run(
            rid=ka, kind="single", project="示例项目A", item="停滞的排查",
            status="running", raw_status="dispatched", role_id="investigate",
            created="2026-10-20T09:00:00+08:00", completed=None,
            changed="2026-10-20T09:15:00+08:00", stale=True,
            stages=[_stage("复现", "done", s(ka, "监督", "监督")),
                    _stage("定位根因", "current", s(ka, "监督", "监督")),
                    _stage("出结论", "unknown", s(ka, "监督", "监督"))],
            agents=[_agent("agent-11", role_id="investigate", executor="codex",
                           activity="跑复现脚本", state="active",
                           changed="2026-10-20T09:15:00+08:00")],
            packages=[], progress=["复现脚本跑不通"],
            decisions=["是否改代码 → 先不改 → 理由：只读"],
            pending_ids=[], detail_rel=ka, base=base,
            sources=[f(ka, "status"), s(ka, "监督", "监督"), s(ka, "决策记录", "决策记录")],
        ),
    ]

    def role_metric(value, source_ids, reason=None):
        return _metric(value, source_ids, reason)

    none_hist = "没有可覆盖的完成历史"
    roles = [
        _role(rid="research", name="调研 Agent", status="draft",
              executors=["claude"], skills=[], use_when="技术调研、读源码摸清现状；只读",
              avatar="research", completed=role_metric(0, ["agents/research.md"]),
              first_pass=_ratio(None, None, ["agents/research.md"], "一次通过只算开发角色"),
              active=role_metric(1, ["agents/research.md", e]), level=1,
              progress={"current": 0, "span": 3, "nextRemaining": 3}, is_new=True,
              sources=[role_src("research"), role_src("research", "name")],
              workspace="directory"),
        _role(rid="product", name="产品 Agent", status="active",
              executors=["claude", "opencode"], skills=["pm-clarify", "pm-prd"],
              use_when="澄清需求、写可验收的 PRD", avatar="product",
              completed=role_metric(0, ["agents/product.md"]),
              first_pass=_ratio(None, None, ["agents/product.md"], "一次通过只算开发角色"),
              active=role_metric(1, ["agents/product.md", z]), level=1,
              progress={"current": 0, "span": 3, "nextRemaining": 3}, is_new=True,
              sources=[role_src("product"), role_src("product", "name")],
              workspace="worktree"),
        _role(rid="pm", name="跟进 Agent", status="active", executors=["claude"],
              skills=["task-follow"], use_when="开发交付流水线里推进需求和开发任务",
              avatar="pm", completed=role_metric(0, ["agents/pm.md"]),
              first_pass=_ratio(None, None, ["agents/pm.md"], "一次通过只算开发角色"),
              active=role_metric(1, ["agents/pm.md", g]), level=1,
              progress={"current": 0, "span": 3, "nextRemaining": 3}, is_new=True,
              sources=[role_src("pm"), role_src("pm", "name")],
              workspace="directory"),
        _role(rid="investigate", name="排查 Agent", status="draft",
              executors=["codex", "opencode"], skills=[],
              use_when="只读排查线上问题并出结论", avatar="investigate",
              completed=role_metric(0, ["agents/investigate.md"]),
              first_pass=_ratio(None, None, ["agents/investigate.md"], "一次通过只算开发角色"),
              active=role_metric(1, ["agents/investigate.md", ka]), level=1,
              progress={"current": 0, "span": 3, "nextRemaining": 3}, is_new=True,
              sources=[role_src("investigate"), role_src("investigate", "name")],
              workspace="directory"),
        _role(rid="dev", name="开发 Agent", status="active",
              executors=["opencode", "claude"],
              skills=["dev-plan", "dev-build", "dev-fix"],
              use_when="按 PRD 写实现计划、按包实现并自测", avatar="dev",
              completed=role_metric(1, ["agents/dev.md", eta]),
              first_pass=_ratio(2, 3, ["agents/dev.md", eta, g], "首轮审查 verdict 样本"),
              active=role_metric(3, ["agents/dev.md", a, b, g]), level=1,
              progress={"current": 1, "span": 3, "nextRemaining": 2}, is_new=False,
              sources=[role_src("dev"), role_src("dev", "name")],
              workspace="worktree"),
        _role(rid="accept", name="验收 Agent", status="active",
              executors=["codex", "claude"],
              skills=["accept-cases", "accept-review", "accept-test"],
              use_when="出验收用例、审查交付、跑集成测试", avatar="accept",
              completed=role_metric(0, ["agents/accept.md"]),
              first_pass=_ratio(None, None, ["agents/accept.md"], "一次通过只算开发角色"),
              active=role_metric(2, ["agents/accept.md", g, d]), level=1,
              progress={"current": 0, "span": 3, "nextRemaining": 3}, is_new=True,
              sources=[role_src("accept"), role_src("accept", "name")],
              workspace="directory"),
    ]

    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now.isoformat(),
        "localDate": now.date().isoformat(),
        "environment": {
            "inOrca": not local,
            "executorsAvailable": ["claude", "codex"] if local
            else ["claude", "codex", "opencode"],
        },
        "stats": _stats(8, 7, 0, 8, 1, None,
                        runs=[f"runs/{slug}.md" for slug in _RUNS_FULL]),
        "pending": pending,
        "dispatchTasks": dispatch,
        "runs": runs,
        "roles": roles,
        "diagnostics": [],
    }


def _expected_full(local: bool) -> dict:
    mode = "terminal" if local else None

    def task_default(executor, default_mode, fallback, must, *, unknown=False,
                     fixed=False):
        # full-local 地点一律 terminal（未知也不例外）；full-orca 的未知地点必须是 null，
        # 不能拿 worktree 占位（contract 已允许 defaultMode: null）。
        # fixed=True 用于团队副本：它在本地禁用，地点不随“只能新终端窗口”改写。
        if fixed:
            resolved_mode = default_mode
        elif mode is not None:
            resolved_mode = mode
        elif unknown:
            resolved_mode = None
        else:
            resolved_mode = default_mode
        return {
            "defaultExecutor": executor,
            "defaultMode": resolved_mode,
            "fallback": fallback,
            "reasonMustContain": must,
        }

    return {
        "pending": {
            "ids": ["N1020-01", "N1020-02", "N1020-03", "N1020-04", "N1020-05",
                    "N1020-06", "N1020-07", "N1020-08", "N1019-09"],
            "count": 8,
            "columns": {"要拍板": 1, "卡住": 1, "待验收": 5},
            "unprocessedHandoffs": 1,
            "processedHandoffs": 1,
            "experiences": {
                "N1020-03": ["yes", "recorded-started"],
                "N1020-04": ["no", "unknown"],
                "N1020-05": ["unknown", "unknown"],
                "N1020-06": ["yes", "static"],
                "N1020-07": ["yes", "expired"],
            },
            "acceptChoiceIds": ["accept", "reject", "later"],
            "choiceSources": {"N1020-01": ["A", "B"], "N1020-02": ["suggestion"]},
        },
        "dispatch": {
            "ids": [f"{_DAILY_REL}#T{n}" for n in (2, 3, 4, 5, 6, 7, 8)],
            "count": 7,
            "excluded": {
                "T1": "exec:me",
                "T9": "已完成",
                "T10": "已放弃",
                "T11": "已有运行记录",
            },
            "defaults": {
                "T2": task_default("codex", "directory", False, ["Codex", "目录" if not local else "终端"]),
                "T3": task_default("opencode", "worktree", False, ["OpenCode"]),
                "T4": task_default(None, "worktree", False, ["团队"], fixed=True),
                "T5": task_default("claude", "directory", False, ["Claude Code"]),
                "T6": task_default("opencode", "worktree", True, ["兜底"]),
                "T7": task_default(None, None, False, ["证据"], unknown=True),
                "T8": task_default("codex", "worktree", False, ["声明"]),
            },
            "selectable": {"T4": not local},
            "kinds": {"T4": "delivery"},
        },
        "runs": {
            "ids": [f"runs/{slug}.md" for slug in _RUNS_FULL],
            "statuses": {
                "runs/2026-10-20-example-alpha.md": "waiting",
                "runs/2026-10-20-example-beta.md": "waiting",
                "runs/2026-10-20-example-gamma.md": "waiting",
                "runs/2026-10-20-example-delta.md": "waiting",
                "runs/2026-10-20-example-epsilon.md": "waiting",
                "runs/2026-10-20-example-zeta.md": "waiting",
                "runs/2026-10-19-example-eta.md": "done",
                "runs/2026-10-20-example-theta.md": "unknown",
                "runs/2026-10-20-example-kappa.md": "running",
            },
            "stale": {
                "runs/2026-10-20-example-kappa.md": True,
                "runs/2026-10-20-example-alpha.md": False,
            },
            "packages": {"runs/2026-10-20-example-gamma.md": ["P0", "P1", "P2"]},
            "firstReview": {
                "runs/2026-10-19-example-eta.md": ["pass", 1],
                "runs/2026-10-20-example-gamma.md#P0": ["pass", 1],
                "runs/2026-10-20-example-gamma.md#P1": ["fix", 1],
                "runs/2026-10-20-example-gamma.md#P2": None,
            },
        },
        "roles": {
            "ids": list(ROLE_IDS),
            "statuses": {"research": "draft", "product": "active", "pm": "active",
                         "investigate": "draft", "dev": "active", "accept": "active"},
            "completed": {"research": 0, "product": 0, "pm": 0,
                          "investigate": 0, "dev": 1, "accept": 0},
            "activeSessions": {"research": 1, "product": 1, "pm": 1,
                               "investigate": 1, "dev": 3, "accept": 2},
            "levels": dict.fromkeys(ROLE_IDS, 1),
            "firstPass": {"dev": {"value": 67, "numerator": 2, "denominator": 3}},
            "workspaces": {"research": "directory", "product": "worktree", "pm": "directory",
                           "investigate": "directory", "dev": "worktree",
                           "accept": "directory"},
        },
        "stats": {"pendingCount": 8, "runningCount": 7, "completedToday": 0,
                  "dispatchedToday": 8, "completedWeek": 1, "clearStreak": None},
        "diagnostics": [],
        "unknowns": [
            {"field": "roles.*.firstPass", "reason": "一次通过只统计开发角色的首轮审查样本"},
            {"field": "stats.clearStreak", "reason": "上游未保留完整清空历史，无法证明日终余额"},
        ],
    }


# ------------------------------------------------------------------- empty

def _empty_role(rid: str) -> dict:
    spec = _ROLE_SPECS_FULL[rid]
    return _role(
        rid=rid, name=spec["name"], status="draft",
        executors=spec["executors"][:1], skills=[],
        use_when=spec["use_when"], avatar=rid,
        completed=_metric(None, [], "没有可覆盖的完成历史"),
        first_pass=_ratio(None, None, [], "没有可覆盖的审查样本"),
        active=_metric(None, [], "没有可靠会话信息"),
        level=None, progress=None, is_new=None, sources=[], workspace=spec["workspace_norm"],
    )


def _docs_empty(now: datetime, base) -> tuple[list[_Doc], dict, dict]:
    today = now.date().isoformat()
    docs = []
    marks = {"pending": {}, "daily": {}, "runs": {}, "roles": {}}
    pending_doc, pending_marks = _pending_doc([], today=today)
    docs.append(pending_doc)
    marks["pending"] = pending_marks
    daily_doc, daily_marks = _daily_doc([], today=today, summary="虚构夹具：空计划")
    docs.append(daily_doc)
    marks["daily"] = daily_marks
    for rid, spec in _ROLE_SPECS_FULL.items():
        doc, fields = _role_doc(rid, **{**spec, "status": "draft"})
        docs.append(doc)
        marks["roles"][rid] = fields
    dirs = ["runs"]
    return docs, {}, marks, dirs


def _snapshot_empty(now: datetime, base, marks) -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now.isoformat(),
        "localDate": now.date().isoformat(),
        "environment": {"inOrca": True,
                        "executorsAvailable": ["claude", "codex", "opencode"]},
        "stats": _stats(0, 0, 0, 0, 0, None),
        "pending": [],
        "dispatchTasks": [],
        "runs": [],
        "roles": [_empty_role(rid) for rid in ROLE_IDS],
        "diagnostics": [],
    }


def _expected_empty() -> dict:
    return {
        "pending": {"ids": [], "count": 0,
                    "columns": {"要拍板": 0, "卡住": 0, "待验收": 0},
                    "unprocessedHandoffs": 0, "processedHandoffs": 0},
        "dispatch": {"ids": [], "count": 0, "excluded": {}},
        "runs": {"ids": [], "statuses": {}},
        "roles": {
            "ids": list(ROLE_IDS),
            "statuses": dict.fromkeys(ROLE_IDS, "draft"),
            "completed": dict.fromkeys(ROLE_IDS),
            "activeSessions": dict.fromkeys(ROLE_IDS),
            "levels": dict.fromkeys(ROLE_IDS),
        },
        "stats": {"pendingCount": 0, "runningCount": 0, "completedToday": 0,
                  "dispatchedToday": 0, "completedWeek": 0, "clearStreak": None},
        "diagnostics": [],
        "unknowns": [
            {"field": "roles.*.completed", "reason": "空目录不等于有完整历史，终身计数按未知"},
            {"field": "roles.*.activeSessions", "reason": "没有可靠会话信息"},
            {"field": "stats.clearStreak", "reason": "上游未保留完整清空历史"},
        ],
    }


# ------------------------------------------------------------------ missing

def _snapshot_missing(now: datetime) -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now.isoformat(),
        "localDate": now.date().isoformat(),
        "environment": {"inOrca": False, "executorsAvailable": ["claude", "codex"]},
        "stats": {
            "pendingCount": {"value": None, "sourceIds": [], "reason": "待处理清单缺失"},
            "runningCount": {"value": None, "sourceIds": [], "reason": "运行记录目录缺失"},
            "completedToday": {"value": None, "sourceIds": [], "reason": "运行记录目录缺失"},
            "dispatchedToday": {"value": None, "sourceIds": [], "reason": "运行记录目录缺失"},
            "completedWeek": {"value": None, "sourceIds": [], "reason": "运行记录目录缺失"},
            "clearStreak": {"value": None, "sourceIds": [],
                            "reason": "上游未保留完整清空历史"},
        },
        "pending": [],
        "dispatchTasks": [],
        "runs": [],
        # PRD 第 7 节：角色没建好时只显示已有角色；全缺就是空列表，只记诊断。
        "roles": [],
        "diagnostics": [
            _diag("pending_source_missing", "待处理清单不存在，计数按未知展示", _PENDING_REL),
            _diag("daily_source_missing", "当日计划不存在，候选按未知展示", _DAILY_REL),
            _diag("runs_source_missing", "运行记录目录不存在，运行与统计按未知展示", "runs/"),
            _diag("roles_source_missing",
                  "角色文件缺失或无法解析，仅展示可解析角色（"
                  + "、".join(ROLE_IDS) + "）", "agents/"),
        ],
    }


def _expected_missing() -> dict:
    reason = {
        "pendingCount": "待处理清单缺失",
        "runningCount": "运行记录目录缺失",
        "completedToday": "运行记录目录缺失",
        "dispatchedToday": "运行记录目录缺失",
        "completedWeek": "运行记录目录缺失",
        "clearStreak": "上游未保留完整清空历史",
    }
    return {
        "pending": {"ids": [], "count": None,
                    "columns": {"要拍板": None, "卡住": None, "待验收": None},
                    "unprocessedHandoffs": None, "processedHandoffs": None},
        "dispatch": {"ids": [], "count": None, "excluded": {}},
        "runs": {"ids": [], "statuses": {}},
        "roles": {"ids": [], "statuses": {}, "completed": {},
                  "activeSessions": {}, "levels": {}},
        "stats": {key: None for key in reason},
        "statReasons": reason,
        "diagnostics": ["pending_source_missing", "daily_source_missing",
                        "runs_source_missing", "roles_source_missing"],
        "unknowns": [{"field": f"stats.{key}", "reason": value}
                     for key, value in reason.items()],
    }


# ----------------------------------------------------------------- conflict

def _docs_conflict(now: datetime, base) -> tuple[list[_Doc], dict, dict]:
    today = now.date().isoformat()
    docs = []
    marks = {"pending": {}, "daily": {}, "runs": {}, "roles": {}, "reviews": {}}

    rows = [
        {"id": "N1020-01", "when": "2026-10-20 09:10", "type": "要拍板", "source": "产品 Agent",
         "item": "示例项目A：范围确认", "text": "字段与正文冲突示例（选项：A 保留；B 调整）",
         "detail": _abs(base, "runs/2026-10-20-conflict-r1.md"),
         "delivery": "已推送 09:10 → 主会话", "status": "待处理"},
        {"id": "N1020-02", "when": "2026-10-20 09:12", "type": "完成", "source": "开发 Agent",
         "item": "示例项目A：已处理但缺说明", "text": "已处理但没有写说明",
         "detail": _abs(base, "runs/2026-10-20-conflict-r2.md"),
         "delivery": "已推送 09:12 → 主会话", "status": "已处理"},
        {"id": "N1020-03", "when": "2026-10-20 09:14", "type": "卡住", "source": "排查 Agent",
         "item": "示例项目A 没有分隔符", "text": "事项格式不对，无法拆项目与事项",
         "detail": _abs(base, "runs/2026-10-20-conflict-r3.md"),
         "delivery": "已推送 09:14 → 主会话", "status": "待处理"},
        {"id": "N1020-04", "when": "2026-10-20 09:16", "type": "要拍板", "source": "产品 Agent",
         "item": "示例项目B：缺选项", "text": "记录里没有选项",
         "detail": _abs(base, "runs/2026-10-20-conflict-r1.md"),
         "delivery": "已推送 09:16 → 主会话", "status": "待处理"},
    ]
    pending_doc, pending_marks = _pending_doc(rows, today=today)
    docs.append(pending_doc)
    marks["pending"] = pending_marks

    tasks = [
        dict(taskNo="T1", mark=" ", title="主会话任务但写了角色",
             meta=["@me", "exec:me", "proj:示例项目A"],
             subs=[("角色", "dev"), ("说明", "证据冲突：exec:me 与角色 dev 同时出现")]),
        dict(taskNo="T2", mark=" ", title="范围冲突示例",
             meta=["@me", "exec:claude", "proj:示例项目B"],
             subs=[("角色", "accept"), ("说明", "角色配置默认 Codex，任务声明 Claude Code"),
                   ("改代码", "否")]),
    ]
    daily_doc, daily_marks = _daily_doc(tasks, today=today, summary="虚构夹具：冲突计划")
    docs.append(daily_doc)
    marks["daily"] = daily_marks

    r1_slug = "2026-10-20-conflict-r1"
    r1, r1m = _run_doc(
        r1_slug,
        [("type", "run"), ("slug", r1_slug), ("status", "done"),
         ("created", "2026-10-19 20:00"), ("updated", "2026-10-19"),
         ("task", "2026-10-20-example-plan#T1"), ("project", "示例项目A"),
         ("repo", "demo-owner/demo-repo"), ("agent", "opencode"),
         ("mode", "orca-new-worktree"), ("target", "demo-workspace-a"), ("role", "dev"),
         ("summary", '"示例：字段 done 正文还在开发中"')],
        "[执行] 示例项目A · 字段与正文冲突",
        [("任务说明", ["目标：演示字段与正文冲突"]),
         ("阶段", ["- 当前：开发"]),
         ("监督", ["- 2026-10-19 22:00 — 活跃：还在开发"]),
         ("验收", ["（空）"])],
    )
    docs.append(r1)
    marks["runs"][r1.rel] = r1m

    r2_slug = "2026-10-20-conflict-r2"
    r2, r2m = _run_doc(
        r2_slug,
        [("type", "run"), ("slug", r2_slug), ("status", "dispatched"),
         ("created", "2026-10-20 09:00"), ("updated", "2026-10-20"),
         ("task", "2026-10-20-example-plan#T2"), ("project", "示例项目A"),
         ("repo", "demo-owner/demo-repo"), ("agent", "opencode"),
         ("mode", "orca-new-worktree"), ("target", "demo-workspace-a"), ("role", "dev"),
         ("summary", '"示例：正文写完成但字段 dispatched"')],
        "[执行] 示例项目A · 正文与字段冲突",
        [("任务说明", ["目标：演示正文与字段冲突"]),
         ("监督", ["- 2026-10-20 09:20 — 活跃：进行中"]),
         ("验收", ["结果：完成，已提交示例提交"])],
    )
    docs.append(r2)
    marks["runs"][r2.rel] = r2m

    r3_slug = "2026-10-20-conflict-r3"
    r3, r3m = _run_doc(
        r3_slug,
        [("type", "run"), ("slug", r3_slug), ("status", "waiting-review"),
         ("created", "2026-10-20 09:05"), ("updated", "2026-10-20"),
         ("task", "2026-10-20-example-plan#T1"), ("project", "示例项目A"),
         ("repo", "demo-owner/demo-repo"), ("agent", "codex"),
         ("mode", "orca-existing"), ("target", "demo-workspace-a"), ("role", "investigate"),
         ("summary", '"示例：未知状态值"')],
        "[执行] 示例项目A · 未知状态",
        [("任务说明", ["目标：演示未知状态值"]),
         ("监督", ["- 2026-10-20 09:18 — 等待：等外部确认"])],
    )
    docs.append(r3)
    marks["runs"][r3.rel] = r3m

    for rid, spec in _ROLE_SPECS_FULL.items():
        if rid == "accept":
            doc, fields = _role_doc(rid, **{**spec, "status": "unknown"})
        else:
            doc, fields = _role_doc(rid, **spec)
        docs.append(doc)
        marks["roles"][rid] = fields

    review, review_fields = _review_doc(
        "docs/delivery/demo/reviews/P1-r2.md", slug="demo-P1-r2", verdict="通过",
        summary="示例：第二轮才通过，不能当首轮证据")
    docs.append(review)
    marks["reviews"]["docs/delivery/demo/reviews/P1-r2.md"] = review_fields

    return docs, {}, marks


def _snapshot_conflict(now: datetime, base, marks) -> dict:
    r1 = "runs/2026-10-20-conflict-r1.md"
    r2 = "runs/2026-10-20-conflict-r2.md"
    r3 = "runs/2026-10-20-conflict-r3.md"

    def f(rel, key):
        return _src(base, rel, marks["runs"][rel]["fields"][key], key)

    def s(rel, heading, field):
        start, end = marks["runs"][rel]["sections"][heading]
        return _src(base, rel, start, field, end)

    def row(pid):
        return _src(base, _PENDING_REL, marks["pending"][pid], "row")

    pending = [
        _pending(
            pid="N1020-01", ptype="要拍板", column="要拍板",
            project="示例项目A", item="范围确认", title="范围确认",
            summary="字段与正文冲突示例（选项：A 保留；B 调整）",
            sender="产品 Agent", role_id="product",
            created_at="2026-10-20T09:10:00+08:00",
            detail=_abs(base, r1), run_id=r1,
            choices=[_choice("A", "A 保留", "记录：选项"),
                     _choice("B", "B 调整", "记录：选项")],
            sources=[row("N1020-01")],
        ),
        _pending(
            pid="N1020-02", ptype="完成", column=None, status="已处理",
            project="示例项目A", item="已处理但缺说明", title="已处理但缺说明",
            summary="已处理但没有写说明",
            sender="开发 Agent", role_id="dev",
            created_at="2026-10-20T09:12:00+08:00",
            detail=_abs(base, r2), run_id=r2, choices=[],
            processed_at=None, processed_note=None,
            sources=[row("N1020-02")],
            diagnostics=[_diag("pending_processed_note_missing",
                               "已处理但缺少处理时间或说明，按未记录展示", "N1020-02")],
        ),
        _pending(
            pid="N1020-03", ptype="卡住", column="卡住",
            project="", item="示例项目A 没有分隔符", title="示例项目A 没有分隔符",
            summary="事项格式不对，无法拆项目与事项",
            sender="排查 Agent", role_id="investigate",
            created_at="2026-10-20T09:14:00+08:00",
            detail=_abs(base, r3), run_id=r3,
            choices=[_choice("suggestion", "无法拆分，等主会话确认", "记录：建议")],
            sources=[row("N1020-03")],
            diagnostics=[_diag("pending_item_malformed",
                               "事项不是「项目：事项」格式，项目按空值展示", "N1020-03")],
        ),
        _pending(
            pid="N1020-04", ptype="要拍板", column="要拍板",
            project="示例项目B", item="缺选项", title="缺选项",
            summary="记录里没有选项",
            sender="产品 Agent", role_id="product",
            created_at="2026-10-20T09:16:00+08:00",
            detail=_abs(base, r1), run_id=r1, choices=[],
            sources=[row("N1020-04")],
            diagnostics=[_diag("pending_choices_missing",
                               "记录未给出可切分选项，不提供猜测选项", "N1020-04")],
        ),
    ]

    dmarks = marks["daily"]
    dispatch = [
        _dispatch(
            tid="T2", task_no="T2", project="示例项目B", title="范围冲突示例",
            summary="角色配置默认 Codex，任务声明 Claude Code", plan_rel=_DAILY_REL, base=base,
            lines=[("标题", dmarks["T2"]["start"], dmarks["T2"]["end"]),
                   ("角色", dmarks["T2"]["subs"]["角色"], dmarks["T2"]["subs"]["角色"])],
            role_id="accept", declared="claude", changes=False, difficulty=1,
            stages=[], outputs=[],
            default_executor="claude", default_mode="directory",
            default_reason="任务已声明：Claude Code · 现有目录",
        ),
    ]

    runs = [
        _run(
            rid=r1, kind="single", project="示例项目A", item="字段与正文冲突",
            status="done", raw_status="done", role_id="dev",
            created="2026-10-19T20:00:00+08:00", completed="2026-10-19T23:00:00+08:00",
            changed="2026-10-19T22:00:00+08:00", stale=None,
            stages=[_stage("实现", "current", s(r1, "阶段", "阶段")),
                    _stage("自测", "unknown", s(r1, "阶段", "阶段")),
                    _stage("提交", "unknown", s(r1, "阶段", "阶段"))],
            agents=[_agent("agent-1", role_id="dev", executor="opencode",
                           activity="还在开发", state="waiting",
                           changed="2026-10-19T22:00:00+08:00")],
            packages=[], progress=["还在开发"], decisions=[],
            pending_ids=["N1020-01", "N1020-04"], detail_rel=r1, base=base,
            sources=[f(r1, "status"), f(r1, "created"), s(r1, "阶段", "阶段")],
            diagnostics=[_diag("run_status_conflict",
                               "字段 status=done 与正文“当前：开发”冲突，以字段为准并附诊断",
                               r1)],
        ),
        _run(
            rid=r2, kind="single", project="示例项目A", item="正文与字段冲突",
            status="running", raw_status="dispatched", role_id="dev",
            created="2026-10-20T09:00:00+08:00", completed=None,
            changed="2026-10-20T09:20:00+08:00", stale=False,
            stages=[_stage("实现", "current", s(r2, "监督", "监督")),
                    _stage("自测", "unknown", s(r2, "监督", "监督"))],
            agents=[_agent("agent-2", role_id="dev", executor="opencode",
                           activity="进行中", state="active",
                           changed="2026-10-20T09:20:00+08:00")],
            packages=[], progress=["进行中"], decisions=[],
            pending_ids=[], detail_rel=r2, base=base,
            sources=[f(r2, "status"), s(r2, "监督", "监督"), s(r2, "验收", "验收")],
            diagnostics=[_diag("run_status_conflict",
                               "正文“结果：完成”与字段 dispatched 冲突，按字段展示为进行中",
                               r2)],
        ),
        _run(
            rid=r3, kind="single", project="示例项目A", item="未知状态",
            status="unknown", raw_status="waiting-review", role_id="investigate",
            created="2026-10-20T09:05:00+08:00", completed=None,
            changed="2026-10-20T09:18:00+08:00", stale=None,
            stages=[_stage("复现", "unknown", s(r3, "监督", "监督")),
                    _stage("定位根因", "unknown", s(r3, "监督", "监督")),
                    _stage("出结论", "unknown", s(r3, "监督", "监督"))],
            agents=[_agent("agent-3", role_id="investigate", executor="codex",
                           activity="等外部确认", state="waiting",
                           changed="2026-10-20T09:18:00+08:00")],
            packages=[], progress=["等待：等外部确认"], decisions=[],
            pending_ids=["N1020-03"], detail_rel=r3, base=base,
            sources=[f(r3, "status"), s(r3, "监督", "监督")],
            diagnostics=[_diag("run_status_unmapped",
                               "运行状态 waiting-review 不是允许值，按未知展示并保留原状态",
                               r3)],
        ),
    ]

    roles = [
        _role(rid="research", name="调研 Agent", status="draft", executors=["claude"],
              skills=[], use_when="技术调研；只读", avatar="research",
              completed=_metric(None, [], "没有可覆盖的完成历史"),
              first_pass=_ratio(None, None, [], "一次通过只算开发角色"),
              active=_metric(None, [], "没有可靠会话信息"),
              level=None, progress=None, is_new=None, sources=[], workspace="directory"),
        _role(rid="product", name="产品 Agent", status="active",
              executors=["claude", "opencode"], skills=["pm-clarify"],
              use_when="澄清需求、写 PRD", avatar="product",
              completed=_metric(None, [], "没有可覆盖的完成历史"),
              first_pass=_ratio(None, None, [], "一次通过只算开发角色"),
              active=_metric(None, [], "没有可靠会话信息"),
              level=None, progress=None, is_new=None, sources=[], workspace="worktree"),
        _role(rid="pm", name="跟进 Agent", status="active", executors=["claude"],
              skills=["task-follow"], use_when="推进交付", avatar="pm",
              completed=_metric(None, [], "没有可覆盖的完成历史"),
              first_pass=_ratio(None, None, [], "一次通过只算开发角色"),
              active=_metric(None, [], "没有可靠会话信息"),
              level=None, progress=None, is_new=None, sources=[], workspace="directory"),
        _role(rid="investigate", name="排查 Agent", status="draft",
              executors=["codex", "opencode"], skills=[], use_when="只读排查",
              avatar="investigate",
              completed=_metric(None, [], "没有可覆盖的完成历史"),
              first_pass=_ratio(None, None, [], "一次通过只算开发角色"),
              active=_metric(None, [], "没有可靠会话信息"),
              level=None, progress=None, is_new=None, sources=[], workspace="directory"),
        _role(rid="dev", name="开发 Agent", status="active",
              executors=["opencode", "claude"], skills=["dev-build"],
              use_when="按包实现并自测", avatar="dev",
              completed=_metric(1, ["runs/2026-10-20-conflict-r1.md"]),
              first_pass=_ratio(None, 0, [], "只有轮次没有 verdict，不计样本"),
              active=_metric(1, ["runs/2026-10-20-conflict-r2.md"]), level=1,
              progress={"current": 1, "span": 3, "nextRemaining": 2}, is_new=False,
              sources=[], workspace="worktree"),
        _role(rid="accept", name="验收 Agent", status="unknown", executors=[],
              skills=[], use_when=None, avatar="accept",
              completed=_metric(None, [], "角色状态未记录"),
              first_pass=_ratio(None, None, [], "角色状态未记录"),
              active=_metric(None, [], "角色状态未记录"),
              level=None, progress=None, is_new=None, sources=[], workspace=None),
    ]

    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now.isoformat(),
        "localDate": now.date().isoformat(),
        "environment": {"inOrca": True,
                        "executorsAvailable": ["claude", "codex", "opencode"]},
        "stats": _stats(3, 1, 0, 2, 1, None,
                        runs=["runs/2026-10-20-conflict-r1.md",
                              "runs/2026-10-20-conflict-r2.md",
                              "runs/2026-10-20-conflict-r3.md"]),
        "pending": pending,
        "dispatchTasks": dispatch,
        "runs": runs,
        "roles": roles,
        "diagnostics": [
            _diag("review_round_not_first",
                  "只存在第二轮审查报告，不能当首轮 verdict 证据",
                  "docs/delivery/demo/reviews/P1-r2.md"),
        ],
    }


def _expected_conflict() -> dict:
    return {
        "pending": {
            "ids": ["N1020-01", "N1020-02", "N1020-03", "N1020-04"],
            "count": 3,
            "columns": {"要拍板": 2, "卡住": 1, "待验收": 0},
            "unprocessedHandoffs": 0,
            "processedHandoffs": 0,
        },
        "dispatch": {
            "ids": [f"{_DAILY_REL}#T2"],
            "count": 1,
            "excluded": {"T1": "exec:me 与角色声明冲突"},
            "defaults": {"T2": {"defaultExecutor": "claude", "defaultMode": "directory",
                                "fallback": False, "reasonMustContain": ["声明"]}},
        },
        "runs": {
            "ids": ["runs/2026-10-20-conflict-r1.md",
                    "runs/2026-10-20-conflict-r2.md",
                    "runs/2026-10-20-conflict-r3.md"],
            "statuses": {"runs/2026-10-20-conflict-r1.md": "done",
                         "runs/2026-10-20-conflict-r2.md": "running",
                         "runs/2026-10-20-conflict-r3.md": "unknown"},
            "stale": {"runs/2026-10-20-conflict-r2.md": False},
        },
        "roles": {
            "ids": list(ROLE_IDS),
            "statuses": {"research": "draft", "product": "active", "pm": "active",
                         "investigate": "draft", "dev": "active", "accept": "unknown"},
            "completed": {"research": None, "product": None, "pm": None,
                          "investigate": None, "dev": 1, "accept": None},
            "levels": {"research": None, "product": None, "pm": None,
                       "investigate": None, "dev": 1, "accept": None},
        },
        "stats": {"pendingCount": 3, "runningCount": 1, "completedToday": 0,
                  "dispatchedToday": 2, "completedWeek": 1, "clearStreak": None},
        "diagnostics": ["review_round_not_first", "run_status_conflict",
                        "run_status_unmapped", "pending_processed_note_missing",
                        "pending_item_malformed", "pending_choices_missing"],
        "unknowns": [
            {"field": "pending.N1020-02.processedAt", "reason": "已处理但没有可解析的处理时间"},
            {"field": "pending.N1020-03.project", "reason": "事项缺「项目：」前缀"},
            {"field": "roles.accept", "reason": "角色文件状态未记录"},
            {"field": "runs.runs/2026-10-20-conflict-r1.md.status",
             "reason": "字段 done 与正文冲突，以字段为准"},
        ],
    }


# --------------------------------------------------------------- boundaries

_BOUNDARY_DONE = (
    ("2026-10-19-boundary-r01", "2026-10-19 22:00", "2026-10-20 00:00", ("accept",)),
    ("2026-10-19-boundary-r02", "2026-10-19 20:00", "2026-10-19 23:59", ("accept",)),
    ("2026-10-18-boundary-r03", "2026-10-18 20:00", "2026-10-19 00:00", ("accept",)),
    ("2026-10-17-boundary-r04", "2026-10-17 10:00", "2026-10-18 23:59", ()),
    ("2025-12-30-boundary-r05", "2025-12-30 10:00", "2025-12-31 23:59", ()),
    ("2026-10-02-boundary-r06", "2026-10-02 10:00", "2026-10-03 10:00", ("research",)),
    ("2026-10-04-boundary-r07", "2026-10-04 10:00", "2026-10-04 18:00", ("research",)),
    ("2026-10-05-boundary-r08", "2026-10-05 10:00", "2026-10-05 18:00", ()),
    ("2026-10-06-boundary-r09", "2026-10-06 10:00", "2026-10-06 18:00", ()),
    ("2026-10-07-boundary-r10", "2026-10-07 10:00", "2026-10-07 18:00", ()),
)

_BOUNDARY_OPEN = (
    ("2026-10-20-boundary-r11", "2026-10-20 00:00", "2026-10-20 09:30:01", False),
    ("2026-10-20-boundary-r12", "2026-10-20 07:00", "2026-10-20 09:30:00", False),
    ("2026-10-20-boundary-r13", "2026-10-20 08:30", "2026-10-20 09:29:59", True),
)

_ROLE_LABELS_CN = {"dev": "开发", "accept": "验收", "pm": "跟进",
                   "research": "调研", "investigate": "排查", "product": "产品"}


def _docs_boundaries(now: datetime, base) -> tuple[list[_Doc], dict, dict]:
    today = now.date().isoformat()
    docs = []
    marks = {"pending": {}, "daily": {}, "runs": {}, "roles": {}}

    rows = [
        {"id": "N1020-01", "when": "2026-10-20 09:31", "type": "完成", "source": "开发 Agent",
         "item": "示例项目B：边界用例", "text": "边界场景待验收",
         "detail": _abs(base, "runs/2026-10-20-boundary-r11.md"),
         "delivery": "已推送 09:31 → 主会话", "status": "待处理"},
    ]
    pending_doc, pending_marks = _pending_doc(rows, today=today)
    docs.append(pending_doc)
    marks["pending"] = pending_marks

    daily_doc, daily_marks = _daily_doc([], today=today, summary="虚构夹具：边界计划")
    docs.append(daily_doc)
    marks["daily"] = daily_marks

    for slug, created, completed, extra in _BOUNDARY_DONE:
        roles = ("dev", "pm") + extra
        sessions = [f"- {created} — {_ROLE_LABELS_CN[rid]} / 示例 skill · Orca 工作区 wt-{slug[-3:]} · handle h-{slug[-3:]}-{rid}"
                    for rid in roles]
        doc, dm = _run_doc(
            slug,
            [("type", "run"), ("kind", "delivery"), ("slug", slug), ("status", "done"),
             ("created", created), ("updated", completed[:10]), ("project", "示例项目B"),
             ("repo", "demo-owner/demo-repo"), ("req", "boundary"),
             ("dev_branch", "develop"), ("req_branch", "req/boundary"),
             ("req_worktree", "C:/example-device/worktrees/boundary"),
             ("follower", "h-follow"), ("report_to", "h-master"),
             ("role", "pm"), ("summary", '"示例：边界完成记录"')],
            f"[交付] 示例项目B · 边界 {slug[-3:]}",
            [("阶段", ["- 当前：完成"]),
             ("工作包", ["| 包 | 状态 | 依赖 | 分支 | 工作区 | 会话 | 审查轮次 | 更新 |",
                         "|---|---|---|---|---|---|---|---|",
                         "| P0 | 完成 | — | pkg/P0 | wt-b | h-p0 | 1 | " + completed[:10] + " |"]),
             ("会话", sessions),
             ("监督", [f"- {completed} — 已结束：示例完成"]),
             ("验收", [f"结果：通过（{completed}）"])],
        )
        docs.append(doc)
        marks["runs"][doc.rel] = dm

    for slug, created, changed, _stale in _BOUNDARY_OPEN:
        doc, dm = _run_doc(
            slug,
            [("type", "run"), ("kind", "delivery"), ("slug", slug), ("status", "dispatched"),
             ("created", created), ("updated", "2026-10-20"), ("project", "示例项目B"),
             ("repo", "demo-owner/demo-repo"), ("req", "boundary"),
             ("dev_branch", "develop"), ("req_branch", "req/boundary"),
             ("req_worktree", "C:/example-device/worktrees/boundary"),
             ("follower", "h-follow"), ("report_to", "h-master"),
             ("role", "pm"), ("summary", '"示例：边界进行中"')],
            f"[交付] 示例项目B · 进行中 {slug[-3:]}",
            [("阶段", ["- 当前：集成测试"]),
             ("会话", [f"- {created} — 开发 / dev-build · Orca 工作区 wt-{slug[-3:]} · handle h-{slug[-3:]}-dev",
                       f"- {created} — 排查 / 示例 skill · Orca 工作区 wt-{slug[-3:]} · handle h-{slug[-3:]}-investigate"]),
             ("监督", [f"- {changed} — 活跃：集成测试进行中"])],
        )
        docs.append(doc)
        marks["runs"][doc.rel] = dm

    present = ("research", "pm", "investigate", "dev", "accept")
    for rid in present:
        doc, fields = _role_doc(rid, **_ROLE_SPECS_FULL[rid])
        docs.append(doc)
        marks["roles"][rid] = fields

    return docs, {}, marks


def _snapshot_boundaries(now: datetime, base, marks) -> dict:
    def f(rel, key):
        return _src(base, rel, marks["runs"][rel]["fields"][key], key)

    def s(rel, heading, field):
        start, end = marks["runs"][rel]["sections"][heading]
        return _src(base, rel, start, field, end)

    def row(pid):
        return _src(base, _PENDING_REL, marks["pending"][pid], "row")

    runs = []
    for idx, (slug, created, completed, extra) in enumerate(_BOUNDARY_DONE):
        rel = f"runs/{slug}.md"
        roles = ("dev", "pm") + extra
        agents = [
            _agent(f"agent-{idx * 4 + i + 1}", role_id=rid, executor="opencode",
                   activity="已结束", state="done", changed=completed.replace(" ", "T") + ":00+08:00")
            for i, rid in enumerate(roles)
        ]
        runs.append(_run(
            rid=rel, kind="delivery", project="示例项目B", item=f"边界 {slug[-3:]}",
            status="done", raw_status="done", role_id="pm",
            created=created.replace(" ", "T") + ":00+08:00",
            completed=completed.replace(" ", "T") + ":00+08:00",
            changed=completed.replace(" ", "T") + ":00+08:00", stale=None,
            stages=[_stage("实现计划 + 用例", "done", s(rel, "阶段", "阶段")),
                    _stage("工作包开发", "done", s(rel, "工作包", "工作包")),
                    _stage("集成测试", "done", s(rel, "阶段", "阶段")),
                    _stage("合回 develop", "done", s(rel, "验收", "验收")),
                    _stage("你来体验", "unknown", s(rel, "验收", "验收"))],
            agents=agents, packages=[], progress=[f"已结束（{completed}）"], decisions=[],
            pending_ids=[], detail_rel=rel, base=base,
            sources=[f(rel, "status"), f(rel, "created"), s(rel, "验收", "验收")],
        ))

    for idx, (slug, created, changed, stale) in enumerate(_BOUNDARY_OPEN):
        rel = f"runs/{slug}.md"
        runs.append(_run(
            rid=rel, kind="delivery", project="示例项目B", item=f"进行中 {slug[-3:]}",
            status="waiting" if slug.endswith("r11") else "running",
            raw_status="dispatched", role_id="pm",
            created=created.replace(" ", "T") + ":00+08:00", completed=None,
            changed=changed.replace(" ", "T") + "+08:00", stale=stale,
            stages=[_stage("实现计划 + 用例", "done", s(rel, "阶段", "阶段")),
                    _stage("工作包开发", "done", s(rel, "阶段", "阶段")),
                    _stage("集成测试", "current", s(rel, "监督", "监督")),
                    _stage("合回 develop", "unknown", s(rel, "监督", "监督"))],
            agents=[
                _agent(f"agent-{40 + idx}", role_id="dev", executor="opencode",
                       activity="集成测试", state="active",
                       changed=changed.replace(" ", "T") + "+08:00"),
                _agent(f"agent-{50 + idx}", role_id="investigate", executor="codex",
                       activity="等结论", state="waiting",
                       changed=changed.replace(" ", "T") + "+08:00"),
            ],
            packages=[], progress=["集成测试进行中"], decisions=[],
            pending_ids=["N1020-01"] if slug.endswith("r11") else [],
            detail_rel=rel, base=base,
            sources=[f(rel, "status"), s(rel, "监督", "监督")],
        ))

    def role(rid, *, status, completed, active, first_pass, is_new, present=True):
        level, progress = _level(completed)
        return _role(
            rid=rid, name=_ROLE_SPECS_FULL[rid]["name"], status=status,
            executors=_ROLE_SPECS_FULL[rid]["executors"],
            skills=_ROLE_SPECS_FULL[rid]["skills"],
            use_when=_ROLE_SPECS_FULL[rid]["use_when"], avatar=rid,
            completed=_metric(completed, [] if completed is not None else [],
                              None if completed is not None else "角色文件缺失"),
            first_pass=first_pass,
            active=_metric(active, [], None if active is not None else "角色文件缺失"),
            level=level, progress=progress, is_new=is_new, sources=[],
            workspace=_ROLE_SPECS_FULL[rid]["workspace_norm"],
        )

    roles = [
        role("research", status="draft", completed=2, active=0,
             first_pass=_ratio(None, None, [], "一次通过只算开发角色"), is_new=False),
        role("product", status="unknown", completed=None, active=None,
             first_pass=_ratio(None, None, [], "角色文件缺失"), is_new=None),
        role("pm", status="active", completed=10, active=0,
             first_pass=_ratio(None, None, [], "一次通过只算开发角色"), is_new=False),
        role("investigate", status="draft", completed=0, active=2,
             first_pass=_ratio(None, None, [], "一次通过只算开发角色"), is_new=True),
        role("dev", status="active", completed=10, active=1,
             first_pass=_ratio(None, 0, [], "有明确轮次但没有 verdict，样本为 0"), is_new=False),
        role("accept", status="active", completed=3, active=0,
             first_pass=_ratio(None, None, [], "一次通过只算开发角色"), is_new=False),
    ]

    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now.isoformat(),
        "localDate": now.date().isoformat(),
        "environment": {"inOrca": True,
                        "executorsAvailable": ["claude", "codex", "opencode"]},
        "stats": _stats(1, 3, 1, 3, 3, None,
                        runs=[f"runs/{slug}.md" for slug, *_ in _BOUNDARY_DONE]
                        + [f"runs/{slug}.md" for slug, *_ in _BOUNDARY_OPEN]),
        "pending": [
            _pending(
                pid="N1020-01", ptype="完成", column="待验收",
                project="示例项目B", item="边界用例", title="边界用例",
                summary="边界场景待验收",
                sender="开发 Agent", role_id="dev",
                created_at="2026-10-20T09:31:00+08:00",
                detail=_abs(base, "runs/2026-10-20-boundary-r11.md"),
                run_id="runs/2026-10-20-boundary-r11.md",
                choices=list(ACCEPT_CHOICES),
                sources=[row("N1020-01")],
                diagnostics=[_diag("experience_unknown",
                                   "记录未提供体验信息，按未知展示", "N1020-01")],
                experience=_exp(ui="unknown", state="unknown"),
            ),
        ],
        "dispatchTasks": [],
        "runs": runs,
        "roles": roles,
        "diagnostics": [],
    }


def _expected_boundaries() -> dict:
    return {
        "pending": {"ids": ["N1020-01"], "count": 1,
                    "columns": {"要拍板": 0, "卡住": 0, "待验收": 1},
                    "unprocessedHandoffs": 0, "processedHandoffs": 0},
        "dispatch": {"ids": [], "count": 0, "excluded": {}},
        "runs": {
            "ids": [f"runs/{slug}.md" for slug, *_ in _BOUNDARY_DONE]
            + [f"runs/{slug}.md" for slug, *_ in _BOUNDARY_OPEN],
            "statuses": {
                **{f"runs/{slug}.md": "done" for slug, *_ in _BOUNDARY_DONE},
                "runs/2026-10-20-boundary-r11.md": "waiting",
                "runs/2026-10-20-boundary-r12.md": "running",
                "runs/2026-10-20-boundary-r13.md": "running",
            },
            "stale": {
                "runs/2026-10-20-boundary-r11.md": False,
                "runs/2026-10-20-boundary-r12.md": False,
                "runs/2026-10-20-boundary-r13.md": True,
            },
        },
        "roles": {
            "ids": list(ROLE_IDS),
            "statuses": {"research": "draft", "product": "unknown", "pm": "active",
                         "investigate": "draft", "dev": "active", "accept": "active"},
            "completed": {"research": 2, "product": None, "pm": 10,
                          "investigate": 0, "dev": 10, "accept": 3},
            "activeSessions": {"research": 0, "product": None, "pm": 0,
                               "investigate": 2, "dev": 1, "accept": 0},
            "levels": {"research": 1, "product": None, "pm": 3,
                       "investigate": 1, "dev": 3, "accept": 2},
        },
        "stats": {"pendingCount": 1, "runningCount": 3, "completedToday": 1,
                  "dispatchedToday": 3, "completedWeek": 3, "clearStreak": None},
        "diagnostics": [],
        "boundary": {
            "levelCases": list(LEVEL_CASES),
            "staleCases": list(STALE_CASES),
            "dayCases": list(DAY_CASES),
            "streakCases": list(STREAK_CASES),
            "note": "等级 9/29/30/31 与跨年窗口只在本表验证公式，不额外造 31 份记录。",
        },
        "unknowns": [
            {"field": "roles.product", "reason": "角色文件缺失，六位置中该位按未记录"},
            {"field": "roles.dev.firstPass", "reason": "有轮次没有 verdict，零样本显示「—」"},
            {"field": "stats.clearStreak", "reason": "上游未保留完整清空历史"},
        ],
    }


# ------------------------------------------------------------------ hostile

HOSTILE_SENTINELS = (
    "-----BEGIN " "PRIVATE KEY-----",
    "Bearer demo-token-value",
    "token=demo-secret",
    "password: hunter2",
    "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "javascript:alert(1)",
    "private%2F",
    "../../../private/health/index.md",
    "secret: demo",
)

_H_PEM = "-----BEGIN " "PRIVATE KEY----- MIIBdemo -----END PRIVATE KEY-----"
_H_TOKEN = "排查结论 token=demo-secret 已附"
_H_AUTH = "Authorization: Bearer demo-token-value"
_H_LONG = "戊" * 620
_H_JS = "javascript:alert(1)"
_H_TRAVERSAL = "../../../private/health/index.md"
_H_ENCODED = "C:/example-device/private%2Fhealth/water-log.md"
_H_SECRET = "按 PRD 实现；secret: demo"
_H_GHP = "提交 ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def _docs_hostile(now: datetime, base) -> tuple[list[_Doc], dict, dict]:
    today = now.date().isoformat()
    docs = []
    marks = {"pending": {}, "daily": {}, "runs": {}, "roles": {}, "reviews": {}}

    rows = [
        {"id": "N1020-01", "when": "2026-10-20 09:10", "type": "完成", "source": "开发 Agent",
         "item": "示例项目A：证书配置", "text": _H_PEM,
         "detail": _abs(base, "runs/2026-10-20-hostile-r1.md"),
         "delivery": "已推送 09:10 → 主会话", "status": "待处理"},
        {"id": "N1020-02", "when": "2026-10-20 09:12", "type": "完成", "source": "排查 Agent",
         "item": "示例项目A：令牌处置", "text": _H_TOKEN,
         "detail": _H_TRAVERSAL,
         "delivery": "已推送 09:12 → 主会话", "status": "待处理"},
        {"id": "N1020-03", "when": "2026-10-20 09:14", "type": "完成", "source": "产品 Agent",
         "item": "示例项目A：长文本说明", "text": _H_LONG,
         "detail": _H_JS,
         "delivery": "已推送 09:14 → 主会话", "status": "待处理"},
        {"id": "N1020-04", "when": "2026-10-20 09:16", "type": "完成", "source": "开发 Agent",
         "item": "示例项目A：安全投影", "text": "冒烟通过，可以体验",
         "detail": _abs(base, "docs/demo/preview.html"),
         "delivery": "已推送 09:16 → 主会话", "status": "待处理"},
        {"id": "N1020-05", "when": "2026-10-20 09:18", "type": "交接", "source": "产品 Agent",
         "item": "示例项目A：交接", "text": _H_AUTH,
         "detail": _H_ENCODED,
         "delivery": "已推送 09:18 → 主会话", "status": "待处理"},
    ]
    pending_doc, pending_marks = _pending_doc(rows, today=today)
    docs.append(pending_doc)
    marks["pending"] = pending_marks

    tasks = [
        dict(taskNo="T1", mark=" ", title="示例交付",
             meta=["@me", "exec:opencode", "proj:示例项目A"],
             subs=[("角色", "dev"), ("说明", _H_TOKEN), ("改代码", "是")]),
        dict(taskNo="T2", mark=" ", title="普通示例",
             meta=["@me", "exec:claude", "proj:示例项目B"],
             subs=[("角色", "research"), ("说明", "只读查看现状"), ("改代码", "否")]),
    ]
    daily_doc, daily_marks = _daily_doc(tasks, today=today, summary="虚构夹具：对抗输入")
    docs.append(daily_doc)
    marks["daily"] = daily_marks

    r1_slug = "2026-10-20-hostile-r1"
    r1, r1m = _run_doc(
        r1_slug,
        [("type", "run"), ("slug", r1_slug), ("status", "dispatched"),
         ("created", "2026-10-20 09:00"), ("updated", "2026-10-20"),
         ("task", "2026-10-20-example-plan#T1"), ("project", "示例项目A"),
         ("repo", "demo-owner/demo-repo"), ("agent", "opencode"),
         ("mode", "orca-new-worktree"), ("target", "demo-workspace-a"), ("role", "dev"),
         ("handle", "h-hostile-dev"), ("report_to", "h-master"),
         ("summary", '"示例：对抗输入运行"')],
        "[执行] 示例项目A · 对抗输入运行",
        [("任务说明", ["目标：演示敏感内容不进快照"]),
         ("监督", [f"- 2026-10-20 09:40 — 活跃：{_H_AUTH}"]),
         ("决策记录", [f"- 2026-10-20 09:20 — 是否改 → 先不改 → {_H_GHP}"])],
    )
    docs.append(r1)
    marks["runs"][r1.rel] = r1m

    r2_slug = "2026-10-20-hostile-r2"
    r2, r2m = _run_doc(
        r2_slug,
        [("type", "run"), ("slug", r2_slug), ("status", "dispatched"),
         ("created", "2026-10-20 09:05"), ("updated", "2026-10-20"),
         ("task", "2026-10-20-example-plan#T2"), ("project", "示例项目B"),
         ("repo", "demo-owner/demo-repo"), ("agent", "claude"),
         ("mode", "orca-existing"), ("target", "demo-workspace-b"), ("role", "research"),
         ("summary", '"示例：对抗输入静态页"')],
        "[执行] 示例项目B · 对抗输入静态页",
        [("任务说明", ["目标：演示安全 file 链接"]),
         ("监督", ["- 2026-10-20 09:45 — 活跃：静态页已生成"]),
         ("体验", ["- 体验卡：inbox/experience/demo-safe.json",
                   "- 静态页面：docs/demo/preview.html"])],
    )
    docs.append(r2)
    marks["runs"][r2.rel] = r2m

    for rid, spec in _ROLE_SPECS_FULL.items():
        if rid == "dev":
            doc, fields = _role_doc(rid, **{**spec, "use_when": _H_SECRET})
        else:
            doc, fields = _role_doc(rid, **spec)
        docs.append(doc)
        marks["roles"][rid] = fields

    files = {
        "inbox/experience/demo-blocked.json": _json_text({
            "url": _H_JS,
            "started": True,
            "startedAt": "2026-10-20T09:10:00+08:00",
            "expiresAt": "2026-10-20T11:10:00+08:00",
            "focus": ["说明文案"],
        }),
        "inbox/experience/demo-safe.json": _json_text({
            "url": _file_uri(base, "docs/demo/preview.html"),
            "started": False,
            "focus": ["安全投影"],
        }),
        "docs/demo/preview.html": "<!doctype html><title>示例预览</title><p>虚构内容</p>\n",
    }
    return docs, files, marks


def _snapshot_hostile(now: datetime, base, marks) -> dict:
    r1 = "runs/2026-10-20-hostile-r1.md"
    r2 = "runs/2026-10-20-hostile-r2.md"

    def f(rel, key):
        return _src(base, rel, marks["runs"][rel]["fields"][key], key)

    def s(rel, heading, field):
        start, end = marks["runs"][rel]["sections"][heading]
        return _src(base, rel, start, field, end)

    def row(pid):
        return _src(base, _PENDING_REL, marks["pending"][pid], "row")

    blocked = _diag("text_blocked", "字段命中敏感规则，整段屏蔽，不展示原值")
    safe_exp = _exp(
        ui="yes", state="static",
        url=_file_uri(base, "docs/demo/preview.html"),
        focus=["安全投影"],
        sources=[s(r2, "体验", "体验"),
                 _src(base, "inbox/experience/demo-safe.json", 2, "url")],
    )

    pending = [
        _pending(
            pid="N1020-01", ptype="完成", column="待验收",
            project="示例项目A", item="证书配置", title="证书配置",
            summary="", sender="开发 Agent", role_id="dev",
            created_at="2026-10-20T09:10:00+08:00",
            detail=_abs(base, r1), run_id=r1, choices=list(ACCEPT_CHOICES),
            sources=[row("N1020-01")],
            diagnostics=[{**blocked, "source": "N1020-01"}],
        ),
        _pending(
            pid="N1020-02", ptype="完成", column="待验收",
            project="示例项目A", item="令牌处置", title="令牌处置",
            summary="", sender="排查 Agent", role_id="investigate",
            created_at="2026-10-20T09:12:00+08:00",
            detail=None, run_id=r1, choices=list(ACCEPT_CHOICES),
            sources=[row("N1020-02")],
            diagnostics=[
                {**blocked, "source": "N1020-02"},
                _diag("path_blocked", "详情路径无法安全解析，已屏蔽，不展示原值", "N1020-02"),
                _diag("experience_link_blocked", "体验链接未通过安全校验，不提供可点击链接",
                      "N1020-02"),
            ],
            experience=_exp(ui="yes", state="unknown"),
        ),
        _pending(
            pid="N1020-03", ptype="完成", column="待验收",
            project="示例项目A", item="长文本说明", title="长文本说明",
            summary=_H_LONG[:SAFE_TEXT_LIMIT] + TRUNCATION_MARKER,
            sender="产品 Agent", role_id="product",
            created_at="2026-10-20T09:14:00+08:00",
            detail=None, run_id=r1, choices=list(ACCEPT_CHOICES),
            sources=[row("N1020-03")],
            diagnostics=[_diag("path_blocked", "详情路径无法安全解析，已屏蔽，不展示原值",
                               "N1020-03")],
        ),
        _pending(
            pid="N1020-04", ptype="完成", column="待验收",
            project="示例项目A", item="安全投影", title="安全投影",
            summary="冒烟通过，可以体验",
            sender="开发 Agent", role_id="dev",
            created_at="2026-10-20T09:16:00+08:00",
            detail=_abs(base, "docs/demo/preview.html"), run_id=r2,
            choices=list(ACCEPT_CHOICES), experience=safe_exp,
            sources=[row("N1020-04")],
        ),
        _pending(
            pid="N1020-05", ptype="交接", column=None,
            project="示例项目A", item="交接", title="交接",
            summary="", sender="产品 Agent", role_id="product",
            created_at="2026-10-20T09:18:00+08:00",
            detail=None, run_id=r1, choices=[],
            sources=[row("N1020-05")],
            diagnostics=[{**blocked, "source": "N1020-05"},
                         _diag("path_blocked", "详情路径无法安全解析，已屏蔽，不展示原值",
                               "N1020-05")],
        ),
    ]

    runs = [
        _run(
            rid=r1, kind="single", project="示例项目A", item="对抗输入运行",
            status="waiting", raw_status="dispatched", role_id="dev",
            created="2026-10-20T09:00:00+08:00", completed=None,
            changed="2026-10-20T09:40:00+08:00", stale=False,
            stages=[_stage("实现", "current", s(r1, "监督", "监督")),
                    _stage("自测", "unknown", s(r1, "监督", "监督")),
                    _stage("提交", "unknown", s(r1, "监督", "监督"))],
            agents=[_agent("agent-1", role_id="dev", executor="opencode",
                           activity="敏感内容已屏蔽", state="active",
                           changed="2026-10-20T09:40:00+08:00")],
            packages=[], progress=[], decisions=[],
            pending_ids=["N1020-01", "N1020-02", "N1020-05"],
            detail_rel=r1, base=base,
            sources=[f(r1, "status"), s(r1, "监督", "监督")],
            diagnostics=[
                _diag("text_blocked", "监督行命中敏感规则，整行不展示", r1),
                _diag("text_blocked", "决策行命中敏感规则，整行不展示", r1),
            ],
        ),
        _run(
            rid=r2, kind="single", project="示例项目B", item="对抗输入静态页",
            status="waiting", raw_status="dispatched", role_id="research",
            created="2026-10-20T09:05:00+08:00", completed=None,
            changed="2026-10-20T09:45:00+08:00", stale=False,
            stages=[_stage("收集", "done", s(r2, "监督", "监督")),
                    _stage("整理", "current", s(r2, "监督", "监督")),
                    _stage("出报告", "unknown", s(r2, "监督", "监督"))],
            agents=[_agent("agent-2", role_id="research", executor="claude",
                           activity="静态页已生成", state="active",
                           changed="2026-10-20T09:45:00+08:00")],
            packages=[], progress=["静态页已生成"], decisions=[],
            pending_ids=["N1020-04"], detail_rel=r2, base=base,
            sources=[f(r2, "status"), s(r2, "体验", "体验")],
        ),
    ]

    def role(rid, *, status, use_when, blocked_use=False):
        return _role(
            rid=rid, name=_ROLE_SPECS_FULL[rid]["name"], status=status,
            executors=_ROLE_SPECS_FULL[rid]["executors"],
            skills=_ROLE_SPECS_FULL[rid]["skills"],
            use_when=use_when, avatar=rid,
            completed=_metric(None, [], "没有可覆盖的完成历史"),
            first_pass=_ratio(None, None, [], "一次通过只算开发角色"),
            active=_metric(None, [], "没有可靠会话信息"),
            level=None, progress=None, is_new=None, sources=[],
            workspace=_ROLE_SPECS_FULL[rid]["workspace_norm"],
        )

    roles = [
        role("research", status="draft", use_when="技术调研；只读"),
        role("product", status="active", use_when="澄清需求、写 PRD"),
        role("pm", status="active", use_when="推进交付"),
        role("investigate", status="draft", use_when="只读排查"),
        role("dev", status="active", use_when=None),
        role("accept", status="active", use_when="出用例、审查交付"),
    ]
    roles[4]["sources"] = [{"path": _abs(base, "agents/dev.md"),
                            "lineStart": marks["roles"]["dev"]["use_when"],
                            "lineEnd": marks["roles"]["dev"]["use_when"],
                            "field": "use_when"}]

    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": now.isoformat(),
        "localDate": now.date().isoformat(),
        "environment": {"inOrca": True,
                        "executorsAvailable": ["claude", "codex", "opencode"]},
        "stats": _stats(5, 2, 0, 2, 0, None,
                        runs=["runs/2026-10-20-hostile-r1.md",
                              "runs/2026-10-20-hostile-r2.md"]),
        "pending": pending,
        "dispatchTasks": [
            _dispatch(
                tid="T1", task_no="T1", project="示例项目A", title="示例交付",
                summary=None, plan_rel=_DAILY_REL, base=base,
                lines=[("标题", marks["daily"]["T1"]["start"], marks["daily"]["T1"]["end"])],
                role_id="dev", declared="opencode", changes=True, difficulty=2,
                stages=["实现", "自测", "提交"], outputs=["代码提交"],
                default_executor="opencode", default_mode="worktree",
                default_reason="任务已声明：OpenCode · 新建工作区",
            ),
            _dispatch(
                tid="T2", task_no="T2", project="示例项目B", title="普通示例",
                summary="只读查看现状", plan_rel=_DAILY_REL, base=base,
                lines=[("标题", marks["daily"]["T2"]["start"], marks["daily"]["T2"]["end"])],
                role_id="research", declared="claude", changes=False, difficulty=1,
                stages=["收集", "整理", "出报告"], outputs=[],
                default_executor="claude", default_mode="directory",
                default_reason="任务已声明：Claude Code · 现有目录",
            ),
        ],
        "runs": runs,
        "roles": roles,
        "diagnostics": [
            _diag("text_blocked", "有字段命中敏感规则，已整段屏蔽（不展示原值）", None),
            _diag("path_blocked", "有路径无法安全解析，已屏蔽（不展示原值）", None),
            _diag("experience_link_blocked", "有体验链接未通过安全校验", None),
        ],
    }


def _expected_hostile() -> dict:
    return {
        "pending": {
            "ids": ["N1020-01", "N1020-02", "N1020-03", "N1020-04", "N1020-05"],
            "count": 5,
            "columns": {"要拍板": 0, "卡住": 0, "待验收": 4},
            "unprocessedHandoffs": 1,
            "processedHandoffs": 0,
            "blocked": {
                "N1020-01": ["summary", "diagnostics"],
                "N1020-02": ["summary", "detailPath", "experience.url"],
                "N1020-03": ["detailPath"],
                "N1020-04": [],
                "N1020-05": ["summary", "detailPath"],
            },
            "kept": {"N1020-04": ["summary", "detailPath", "experience.url"]},
        },
        "dispatch": {
            "ids": [f"{_DAILY_REL}#T1", f"{_DAILY_REL}#T2"],
            "count": 2,
            "excluded": {},
            "blockedSummary": {"T1": True, "T2": False},
        },
        "runs": {
            "ids": ["runs/2026-10-20-hostile-r1.md", "runs/2026-10-20-hostile-r2.md"],
            "statuses": {"runs/2026-10-20-hostile-r1.md": "waiting",
                         "runs/2026-10-20-hostile-r2.md": "waiting"},
            "blocked": {"runs/2026-10-20-hostile-r1.md": ["progress", "decisions"]},
        },
        "roles": {
            "ids": list(ROLE_IDS),
            "blockedUseWhen": {"dev": True},
            "statuses": {"research": "draft", "product": "active", "pm": "active",
                         "investigate": "draft", "dev": "active", "accept": "active"},
        },
        "stats": {"pendingCount": 5, "runningCount": 2, "completedToday": 0,
                  "dispatchedToday": 2, "completedWeek": 0, "clearStreak": None},
        "diagnostics": ["text_blocked", "path_blocked", "experience_link_blocked"],
        "sentinels": list(HOSTILE_SENTINELS),
        "unknowns": [
            {"field": "pending.*.summary", "reason": "命中敏感规则，整段屏蔽"},
            {"field": "pending.*.detailPath", "reason": "路径含 private/穿越/非文件 URI，屏蔽"},
            {"field": "roles.dev.useWhen", "reason": "命中敏感规则，屏蔽"},
        ],
    }


# ------------------------------------------------------------------ 组装

def _paths(base, docs, files) -> dict:
    written = [doc.rel for doc in docs] + list(files)

    def pick(prefix, suffix=".md", exclude=(), contains=None):
        out = {}
        for rel in written:
            if not rel.startswith(prefix) or not rel.endswith(suffix):
                continue
            if rel in exclude:
                continue
            if contains is not None and contains not in rel:
                continue
            out[rel] = _abs(base, rel)
        return out

    return {
        "root": _posix(base),
        "pending": _abs(base, _PENDING_REL) if _PENDING_REL in written else None,
        "daily": _abs(base, _DAILY_REL) if _DAILY_REL in written else None,
        "runs": pick("runs/", exclude=(_PENDING_REL, _REPLIES_REL)),
        "roles": pick("agents/"),
        "experiences": pick("inbox/experience/", suffix=".json"),
        "reviews": pick("docs/delivery/", suffix=".md", contains="/reviews/"),
        "prd": _abs(base, "docs/delivery/demo/prd.md")
        if "docs/delivery/demo/prd.md" in written else None,
        **({"replies": _abs(base, _REPLIES_REL),
            "devices": pick("registry/devices/", suffix=".yaml")} if _REPLIES_REL in written else {}),
    }


def _build(scenario: str, now: datetime, base, replies: bool = False) -> dict:
    if replies and scenario not in ("full-orca", "full-local"):
        raise ValueError("replies=True 只支持 full-orca / full-local")
    if scenario in ("full-orca", "full-local"):
        local = scenario == "full-local"
        docs, files, marks = _docs_full(now, base, replies)
        snapshot = _snapshot_full(local, now, base, marks)
        expected = _expected_full(local)
        dirs = []
        notes = list(SCENARIO_NOTES[scenario])
    elif scenario == "empty":
        docs, files, marks, dirs = _docs_empty(now, base)
        snapshot = _snapshot_empty(now, base, marks)
        expected = _expected_empty()
        notes = list(SCENARIO_NOTES[scenario])
    elif scenario == "missing":
        docs, files, dirs = [], {}, []
        snapshot = _snapshot_missing(now)
        expected = _expected_missing()
        notes = list(SCENARIO_NOTES[scenario])
    elif scenario == "conflict":
        docs, files, marks = _docs_conflict(now, base)
        snapshot = _snapshot_conflict(now, base, marks)
        expected = _expected_conflict()
        dirs = []
        notes = list(SCENARIO_NOTES[scenario])
    elif scenario == "boundaries":
        docs, files, marks = _docs_boundaries(now, base)
        snapshot = _snapshot_boundaries(now, base, marks)
        expected = _expected_boundaries()
        dirs = []
        notes = list(SCENARIO_NOTES[scenario])
        notes.append("会话行以中文角色名开头（开发/验收/跟进/调研/排查），与 agents 的 name 对应。")
    elif scenario == "hostile":
        docs, files, marks = _docs_hostile(now, base)
        snapshot = _snapshot_hostile(now, base, marks)
        expected = _expected_hostile()
        dirs = []
        notes = list(SCENARIO_NOTES[scenario])
    else:
        raise ValueError(f"未知场景: {scenario!r}（可选 {', '.join(SCENARIOS)}）")

    model = {
        "scenario": scenario,
        "docs": docs,
        "files": files,
        "dirs": dirs,
        "snapshot": snapshot,
        "expected": expected,
        "environment": snapshot["environment"],
        "notes": notes,
    }
    if replies:
        _apply_replies(model, base)
        model["notes"].append("replies=True：叠加 v1.1 回复层（runs/replies.md、registry/devices、device 字段）。")
    model["paths"] = _paths(base, model["docs"], model["files"])
    return model


# -------------------------------------------------------------- 公开入口

def _check_now(now) -> datetime:
    if not isinstance(now, datetime):
        raise ValueError("now 必须是 datetime")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须带 UTC offset（例如 DEFAULT_NOW）")
    return now


def _prepare_root(root) -> Path:
    """只接受调用者新建的空绝对临时根；拒绝仓库内、盘根、符号链接与非空目录。"""
    try:
        path = Path(root)
    except TypeError as exc:
        raise ValueError("root 必须是路径") from exc
    if not path.is_absolute():
        raise ValueError("root 必须是绝对路径")
    if path.is_symlink():
        raise ValueError("root 不能是符号链接")
    if not path.exists() or not path.is_dir():
        raise ValueError("root 必须是已存在的目录")
    resolved = path.resolve(strict=True)
    if resolved == Path(resolved.anchor):
        raise ValueError("root 不能是盘根")
    repo = Path(__file__).resolve().parents[1]
    if resolved == repo or repo in resolved.parents:
        raise ValueError("root 不能位于工具仓库内")
    if any(resolved.iterdir()):
        raise ValueError("root 必须为空目录（不覆盖已有数据）")
    return resolved


def _write(model: dict, root: Path) -> None:
    root = Path(root)
    for rel in model["dirs"]:
        (root.joinpath(*rel.split("/"))).mkdir(parents=True, exist_ok=True)
    payload = {doc.rel: doc.text for doc in model["docs"]}
    payload.update(model["files"])
    for rel, text in sorted(payload.items()):
        target = root.joinpath(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        resolved = target.resolve(strict=False)
        if root not in resolved.parents and resolved != root:
            raise ValueError(f"拒绝写到 root 之外: {rel}")
        target.write_text(text, encoding="utf-8", newline="\n")


def _fiction_root() -> Path:
    return Path(_FICTION_ROOT_WINDOWS if os.name == "nt" else _FICTION_ROOT_POSIX)


def make_fixture(root: Path, *, scenario: str, now: datetime = DEFAULT_NOW,
                 replies: bool = False) -> dict:
    """在空绝对临时根写出一个场景的纯虚构 Markdown，返回 manifest（含期望与未知原因）。"""
    if scenario not in SCENARIOS:
        raise ValueError(f"未知场景: {scenario!r}（可选 {', '.join(SCENARIOS)}）")
    now = _check_now(now)
    base = _prepare_root(root)
    model = _build(scenario, now, base, replies)
    _write(model, base)
    manifest = {
        "scenario": scenario,
        "environment": model["environment"],
        "now": now.isoformat(),
        "paths": model["paths"],
        "files": sorted([doc.rel for doc in model["docs"]] + list(model["files"])
                        + [rel + "/" for rel in model["dirs"]]),
        "notes": model["notes"],
        "expected": model["expected"],
    }
    return manifest


def snapshot_fixture(*, scenario: str, now: datetime = DEFAULT_NOW, replies: bool = False) -> dict:
    """返回同一场景的纯快照（不写盘、不读盘），路径用跨平台虚构绝对根。"""
    if scenario not in SCENARIOS:
        raise ValueError(f"未知场景: {scenario!r}（可选 {', '.join(SCENARIOS)}）")
    now = _check_now(now)
    return _build(scenario, now, _fiction_root(), replies)["snapshot"]


__all__ = [
    "SCHEMA_VERSION",
    "SAFE_TEXT_LIMIT",
    "TRUNCATION_MARKER",
    "DEFAULT_NOW",
    "SCENARIOS",
    "EXECUTOR_IDS",
    "EXECUTOR_LABELS",
    "MODE_IDS",
    "MODE_LABELS",
    "ROLE_IDS",
    "PAGE_IDS",
    "PENDING_TYPES",
    "PENDING_COLUMNS",
    "EXPERIENCE_UI_STATES",
    "EXPERIENCE_STATES",
    "RUN_STATUSES",
    "ROLE_STATUSES",
    "AGENT_STATES",
    "LEVEL_CASES",
    "STALE_CASES",
    "DAY_CASES",
    "STREAK_CASES",
    "TEXT_CASES",
    "SCENARIO_NOTES",
    "HOSTILE_SENTINELS",
    "make_fixture",
    "snapshot_fixture",
]
