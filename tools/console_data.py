r"""assistant 指挥台 P1 数据适配：只读白名单记录，投影成 P0 定义的 JSON 快照。

边界（对应 docs/delivery/assistant-console/impl-plan.md）：

- 只经 ``console_contract.checked_source`` 读取白名单文件；每个文件最多读一次。
- 不写文件、不起子进程、不联网；时间与环境由调用方注入（``now`` / ``environment``）。
- 自由文本经 ``safe_text``，路径经 ``display_path``，体验链接经
  ``safe_experience_url``；命中敏感规则时整段屏蔽，不保留原值。
- 缺失为 ``None``，已知空集合为 ``[]``，已知零为 ``0``；不从执行器反推角色，
  不把缺失当零，不用 ``updated``/``created`` 伪造精确完成时间（无更精确证据时只保留
  日期粒度，并在窗口统计中按该本地日期归属）。
- 不投影 handle / cmd / token / pid / 工作区 id / 原始 frontmatter 全文。

公开签名（impl-plan 接口约定）：

    load_snapshot(root, *, now, environment) -> dict
    parse_pending(text, *, source) -> list[dict]
    parse_daily(text, *, source) -> list[dict]
    parse_run(text, *, source) -> dict | None
    parse_role(text, *, source) -> dict | None
    compute_stats(pending, runs, roles, *, now, history=None) -> dict
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from console_contract import (
    ACCEPT_CHOICE_IDS,
    ACCEPT_CHOICE_LABELS,
    ACCEPT_REPLY_CHOICES,
    BLOCKED_REPLY_CHOICES,
    REPLY_COLUMNS,
    REPLY_MAX_CHARS,
    REPLY_MAX_ITEMS,
    REPLY_PAGE_LABELS,
    REPLY_RECEIVER_EXECUTOR,
    REPLY_RECEIVER_FOLLOW,
    REPLY_RECEIVER_MAIN,
    RUN_REPLY_CHOICES,
    reply_status_of,
    EXECUTOR_IDS,
    EXECUTOR_LABELS,
    MODE_IDS,
    PENDING_COLUMNS,
    PENDING_STATUSES,
    PENDING_TYPES,
    ROLE_IDS,
    ROLE_LABELS,
    SCHEMA_VERSION,
    checked_source,
    display_path,
    safe_experience_url,
    safe_text,
)

TZ = datetime.now().astimezone().tzinfo

ROLE_NAMES = {
    "research": "调研 Agent",
    "product": "产品 Agent",
    "pm": "跟进 Agent",
    "investigate": "排查 Agent",
    "dev": "开发 Agent",
    "accept": "验收 Agent",
}

NAME_TO_ROLE = {name: rid for rid, name in ROLE_NAMES.items()}
NAME_TO_ROLE.update({label: rid for rid, label in ROLE_LABELS.items()})
#: tools/delivery.py 的会话行直接用 role slug（dev/accept/pm/...）。
NAME_TO_ROLE.update({rid: rid for rid in ROLE_IDS})

#: delivery.py 已确认的固定执行器；键为 skill，值要求 (角色, 执行器) 同时匹配。
SKILL_EXECUTORS = {
    "dev-plan": ("dev", "opencode"),
    "dev-build": ("dev", "opencode"),
    "dev-fix": ("dev", "opencode"),
    "accept-cases": ("accept", "codex"),
    "accept-review": ("accept", "codex"),
    "accept-test": ("accept", "codex"),
    "task-follow": ("pm", "claude"),
}

#: PRD 4.4 的固定角色默认（执行器, 地点）；只有这四个角色有 PRD 明确默认。
PRD_ROLE_DEFAULTS = {
    "investigate": ("codex", "directory"),
    "research": ("claude", "directory"),
    "dev": ("opencode", "worktree"),
    "product": ("claude", "worktree"),
}

SINGLE_STAGES = {
    "investigate": ["复现", "定位根因", "出结论"],
    "research": ["收集", "整理", "出报告"],
    "dev": ["实现", "自测", "提交"],
}
DELIVERY_STAGES = ["实现计划 + 用例", "工作包开发", "审查与修复", "集成测试",
                   "合回 develop", "你来体验"]

STAGE_ALIASES = {
    "开发": "实现",
    "实现": "实现",
    "自测": "自测",
    "提交": "提交",
    "规划": "实现计划 + 用例",
    "工作包": "工作包开发",
    "审查": "审查与修复",
    "集成测试": "集成测试",
    "合回": "合回 develop",
    "完成": None,
}

REVIEW_VERDICTS = {"通过": "pass", "要修": "fix"}

PENDING_ROW_RE = re.compile(r"^\|")
PENDING_ID_RE = re.compile(r"^N\d{4}-\d+$")
TASK_RE = re.compile(r"^- \[(.)\] (T\d+) (.+)$")
SUB_RE = re.compile(r"^\s+- [\u4e00-\u9fffA-Za-z]+[：:]\s*(.*)$")
TIME_PREFIX_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?"
)
FULLWIDTH_SPLIT_RE = re.compile(r"\s*[；;]\s*")

EXPERIENCE_CARD_RE = re.compile(r"体验卡[：:]\s*([^\s，,；;]+\.json)", re.IGNORECASE)
NO_UI_RE = re.compile(r"没有界面|不需要体验环境")

AGGREGATE_MESSAGES = {
    "text_blocked": "有字段命中敏感规则，已整段屏蔽（不展示原值）",
    "path_blocked": "有路径无法安全解析，已屏蔽（不展示原值）",
    "experience_link_blocked": "有体验链接未通过安全校验",
    "pending_processed_note_missing": "有已处理记录缺少处理时间或说明",
    "pending_item_malformed": "有事項缺少「项目：事项」格式",
    "pending_choices_missing": "有要拍板/卡住记录没有可切分选项",
}


# --------------------------------------------------------------------- 小工具

def _as_text(value) -> str:
    """frontmatter 值转字符串（YAML 会把日期解析成 date/datetime）。"""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return ""


def _safe(value, limit: int = 500) -> str | None:
    try:
        return safe_text(value, limit=limit)
    except (TypeError, ValueError):
        return None


def _safe_or_blank(value) -> str:
    return _safe(value) or ""


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except (ValueError, OSError):
        return path.name


def _root_of(source: Path, depth: int) -> Path | None:
    """从白名单源路径反推数据根（source 必须是 root/…/file）。"""
    try:
        source = Path(source)
        if depth <= 0 or len(source.parents) <= depth:
            return None
        return source.parents[depth]
    except (TypeError, OSError):
        return None


def _source_ref(path: Path, start: int, field: str,
                end: int | None = None) -> dict:
    return {
        "path": Path(path).as_posix(),
        "lineStart": max(1, int(start)),
        "lineEnd": max(1, int(start if end is None else end)),
        "field": field,
    }


def _diag(code: str, message: str, source: str | None = None) -> dict:
    text = _safe(message, limit=300) or "字段无法安全展示"
    return {"code": code, "message": text, "source": source}


def _metric(value, source_ids=None, reason=None) -> dict:
    return {"value": value, "sourceIds": list(source_ids or []), "reason": reason}


# ---------------------------------------------------------------- 时间解析

def _moment(raw, tz=TZ):
    """把记录里的时间投影成 (ISO 文本, aware datetime, 本地日期)。

    - 支持 ``YYYY-MM-DD``、``YYYY-MM-DD HH:MM[:SS]``、带 offset 的 ISO；
    - 日期字段保持日期粒度（不补时分）；
    - ``24:00`` 视作次日 00:00；无法解析返回 ``(None, None, None)``。
    """
    text = _as_text(raw)
    if not text:
        return None, None, None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T]24:00)?", text):
        day = text[:10]
        if text.endswith("24:00"):
            try:
                next_day = date.fromisoformat(day) + timedelta(days=1)
            except ValueError:
                return None, None, None
            try:
                dt = datetime(next_day.year, next_day.month, next_day.day,
                              tzinfo=tz)
            except (ValueError, OverflowError):
                return None, None, None
            return dt.isoformat(), dt, dt.astimezone(tz).date()
        try:
            parsed_date = date.fromisoformat(day)
        except ValueError:
            return None, None, None
        return parsed_date.isoformat(), None, parsed_date
    match = TIME_PREFIX_RE.match(text)
    if match is None:
        return None, None, None
    year, month, day = (int(part) for part in match.group(1).split("-"))
    hour = int(match.group(2) or 0)
    minute = int(match.group(3) or 0)
    second = int(match.group(4) or 0)
    try:
        dt = datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None, None, None
    rest = text[match.end():].strip()
    if rest:
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None, None, None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return parsed.isoformat(), parsed, parsed.astimezone(tz).date()
    dt = dt.replace(tzinfo=tz)
    return dt.isoformat(), dt, dt.date()


def _local_date(raw, tz=TZ):
    _, dt, day = _moment(raw, tz)
    if dt is not None:
        return dt.astimezone(tz).date()
    return day


def _now_parts(now: datetime):
    tz = now.tzinfo or TZ
    aware = now if now.tzinfo is not None else now.replace(tzinfo=tz)
    return aware, aware.date(), tz


# ------------------------------------------------------------------ 待处理

def _split_item(text: str):
    """「项目：事项」→ (project, item, malformed)。只切第一个全角冒号。"""
    value = _as_text(text)
    if "：" not in value:
        return "", value, bool(value)
    project, item = value.split("：", 1)
    return project.strip(), item.strip(), False


def _split_options(text: str) -> list[dict]:
    """从「选项：A …；B …」提取记录给出的选项；无法可靠切分返回 []。"""
    match = re.search(r"选项[：:]\s*[（(]?\s*(.+?)[）)]?\s*$", _as_text(text))
    if match is None:
        return []
    body = match.group(1).strip()
    parts = [part for part in FULLWIDTH_SPLIT_RE.split(body) if part.strip()]
    if len(parts) < 2:
        return []
    choices = []
    for part in parts:
        labeled = re.match(r"^([A-Za-z0-9]{1,4})[ .、:：]+(.+)$", part.strip())
        if labeled is None:
            return []
        choices.append({
            "id": labeled.group(1),
            "label": _safe(labeled.group(0)) or "",
            "source": "记录：选项",
        })
    return [choice for choice in choices if choice["label"]]


def _split_suggestion(text: str) -> list[dict]:
    match = re.search(r"建议[：:]\s*(.+?)\s*$", _as_text(text))
    if match is None:
        return []
    label = _safe(match.group(1).strip().rstrip("）)").strip())
    if not label:
        return []
    return [{"id": "suggestion", "label": label, "source": "记录：建议"}]


def _status_of(cell: str):
    """送达/状态列只解析「待处理 / 已处理 …」；返回 (status, processedAt, note, missing)。"""
    value = _as_text(cell)
    if value == "待处理":
        return "待处理", None, None, False
    if not value.startswith("已处理"):
        return "unknown", None, None, False
    rest = value[len("已处理"):].strip()
    processed_at = None
    note = None
    missing = False
    if rest:
        stamp = re.match(
            r"^(?:(\d{4})-)?(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})", rest
        )
        if stamp:
            rest = rest[stamp.end():].strip()
        note = rest.lstrip("：: ").strip() or None
    if note is not None:
        note = _safe(note)
    if processed_at is None:
        missing = True
    return "已处理", processed_at, note, missing


def _processed_time(cell: str, created_at: str | None, tz):
    """解析「已处理 MM-DD HH:MM」；只有同一记录年份可唯一确定时才归年。"""
    value = _as_text(cell)
    rest = value[len("已处理"):].strip() if value.startswith("已处理") else ""
    stamp = re.match(
        r"^(?:(\d{4})-)?(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})", rest
    )
    if stamp is None:
        return None
    year_text, month, day, hour, minute = stamp.groups()
    created_dt = None
    if created_at:
        _, created_dt, _ = _moment(created_at, tz)
    if year_text:
        year = int(year_text)
    elif created_dt is not None:
        year = created_dt.year
        try:
            candidate = datetime(year, int(month), int(day), int(hour),
                                 int(minute), tzinfo=tz)
        except ValueError:
            return None
        if created_dt - candidate > timedelta(days=1):
            return None  # 跨年歧义：不默用今年
    else:
        return None
    try:
        dt = datetime(year, int(month), int(day), int(hour), int(minute),
                      tzinfo=tz)
    except ValueError:
        return None
    if created_dt is not None and dt - created_dt > timedelta(days=1):
        return None
    return dt.isoformat()


def _parse_pending(text: str, source: Path, tz=TZ):
    """内部实现：返回 (rows, malformed_rows)；表格行损坏时计数必须按未知。"""
    source = Path(source)
    root = _root_of(source, 1)
    rows: list[dict] = []
    seen_ids: set[str] = set()
    malformed_rows = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not PENDING_ROW_RE.match(line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        first = cells[0] if cells else ""
        if first == "编号" or (first and set(first) <= set("-: ")):
            continue  # 表头 / 分隔行
        if len(cells) != 9 or not PENDING_ID_RE.match(first):
            malformed_rows = True
            continue
        pid, when, raw_type, sender, item_cell, text_cell, detail, _delivery, status_cell = cells
        if raw_type not in PENDING_TYPES:
            malformed_rows = True
            continue
        if pid in seen_ids:
            continue  # 同一编号只计一次，避免计数翻倍
        seen_ids.add(pid)
        project, item, item_malformed = _split_item(item_cell)
        diagnostics = []
        if item_malformed:
            diagnostics.append(_diag(
                "pending_item_malformed",
                "事项不是「项目：事项」格式，项目按空值展示", pid))
        summary = _safe(text_cell)
        if summary is None:
            summary = ""
            if text_cell.strip():
                diagnostics.append(_diag(
                    "text_blocked", "字段命中敏感规则，整段屏蔽，不展示原值", pid))
        status, processed_at, processed_note, _missing = _status_of(status_cell)
        if status == "已处理":
            processed_at = _processed_time(status_cell, when, tz)
            if processed_at is None or not processed_note:
                diagnostics.append(_diag(
                    "pending_processed_note_missing",
                    "已处理但缺少处理时间或说明，按未记录展示", pid))
        column = None
        if status == "待处理":
            column = {"完成": "待验收", "要拍板": "要拍板",
                      "卡住": "卡住", "交接": None}.get(raw_type)

        if raw_type == "完成" and status == "待处理":
            choices = [{"id": cid, "label": ACCEPT_CHOICE_LABELS[cid],
                        "source": "PRD 4.3 固定决定"} for cid in ACCEPT_CHOICE_IDS]
        elif raw_type == "要拍板" and status == "待处理":
            choices = _split_options(text_cell)
            if not choices:
                diagnostics.append(_diag(
                    "pending_choices_missing",
                    "记录未给出可切分选项，不提供猜测选项", pid))
        elif raw_type == "卡住" and status == "待处理":
            choices = _split_suggestion(text_cell)
            if not choices:
                diagnostics.append(_diag(
                    "pending_choices_missing",
                    "记录未给出可切分建议，不提供猜测选项", pid))
        else:
            choices = []

        detail_path = None
        run_id = None
        raw_detail = _as_text(detail)
        if raw_detail:
            if root is not None:
                detail_path = display_path(root, raw_detail, base=root)
            if detail_path is None:
                diagnostics.append(_diag(
                    "path_blocked",
                    "详情路径无法安全解析，已屏蔽，不展示原值", pid))
            else:
                run_id = _run_id_of(root, detail_path)

        created_iso, _created_dt, _created_day = _moment(when, tz)
        if created_iso is None and when:
            diagnostics.append(_diag(
                "pending_time_unparsed", "时间无法解析，按未知展示", pid))
        rows.append({
            "id": pid,
            "type": raw_type,
            "status": status if status in PENDING_STATUSES else "unknown",
            "column": column,
            "project": _safe_or_blank(project),
            "item": _safe_or_blank(item),
            "title": _safe_or_blank(item),
            "summary": summary,
            "roleId": _role_from_sender(sender),
            "senderName": _safe_or_blank(sender),
            "createdAt": created_iso,
            "detailPath": detail_path,
            "runId": run_id,
            "choices": choices,
            "processedAt": processed_at,
            "processedNote": processed_note,
            "experience": None,
            "sources": [_source_ref(source, lineno, "row")],
            "diagnostics": diagnostics,
        })
    return rows, malformed_rows


def parse_pending(text: str, *, source: Path) -> list[dict]:
    """解析 ``runs/pending.md`` 的 9 列表格为 Pending 列表。

    只读编号/时间/类型/来源/事项/一句话/详情/状态；送达列仅用于格式识别。
    经验卡等跨文件事实由 ``load_snapshot`` 关联，解析阶段保持 ``experience=None``。
    """
    rows, _malformed = _parse_pending(text, Path(source), TZ)
    return rows


def _run_id_of(root: Path | None, detail_path: str) -> str | None:
    if root is None:
        return None
    try:
        rel = Path(detail_path).relative_to(root)
    except (ValueError, TypeError):
        return None
    parts = rel.parts
    if len(parts) == 2 and parts[0] == "runs" and parts[1].endswith(".md"):
        if parts[1].lower() not in ("pending.md", "index.md", "replies.md"):
            return rel.as_posix()
    return None


def _role_from_sender(sender: str) -> str | None:
    name = _as_text(sender)
    if name in NAME_TO_ROLE:
        return NAME_TO_ROLE[name]
    return None


# -------------------------------------------------------------------- 当日计划

def _loose_frontmatter(raw: str) -> dict:
    """YAML 拒绝含「: 」的自由文本时，按首行冒号宽松拆分；不猜测嵌套结构。"""
    data: dict = {}
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_]+)[：:]\s*(.*)$", line)
        if match is None:
            continue
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        data[key] = value
    return data


def _parse_frontmatter(text: str, *, loose: bool = True):
    """返回 (frontmatter dict, 结束行号)；无法识别时返回 (None, 0)。

    loose=True 是既有运行/计划记录口径；角色文件要求严格 YAML（loose=False），
    解析失败直接排除，避免把损坏字段当成合法角色。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, 0
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            raw = "\n".join(lines[1:index])
            try:
                data = yaml.safe_load(raw) or {}
            except yaml.YAMLError:
                if not loose:
                    return None, 0
                # 真实记录的一句话/说明可能带「: 」；宽松解析仍只取顶层标量。
                data = _loose_frontmatter(raw)
            return (data if isinstance(data, dict) else {}), index + 1
    return None, 0


def _executor_of(label: str) -> str | None:
    """把「执行器：Claude Code」这类标签归一成执行器 id；无法识别返回 None。"""
    text = _as_text(label).strip().lower().replace("_", " ")
    if not text:
        return None
    if text in ("me", "@me"):
        return "me"
    if text in ("assistant", "@assistant"):
        return "assistant"
    compact = text.replace("-", " ").replace("/", " ")
    if "opencode" in compact or "open code" in compact:
        return "opencode"
    if "claude" in compact:
        return "claude"
    if "codex" in compact:
        return "codex"
    if "agent" in compact:
        return "agent"
    return None


def _daily_tasks(text: str, source: Path):
    """解析任务行与紧邻子项；返回 (tasks, plan_status)。"""
    source = Path(source)
    root = _root_of(source, 2)
    front, _end = _parse_frontmatter(text)
    plan_status = _as_text((front or {}).get("status"))
    tasks: list[dict] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = TASK_RE.match(lines[index])
        if match is None:
            index += 1
            continue
        mark, task_no, rest = match.groups()
        start = index + 1
        subs: dict[str, tuple[int, str]] = {}
        end = start
        cursor = index + 1
        while cursor < len(lines):
            line = lines[cursor]
            if TASK_RE.match(line) or line.startswith("#"):
                break
            sub = SUB_RE.match(line)
            if sub is not None:
                key_match = re.match(r"^\s+- ([\u4e00-\u9fffA-Za-z]+)[：:]", line)
                if key_match is not None:
                    subs[key_match.group(1)] = (cursor + 1, sub.group(1).strip())
            if line.strip():
                end = cursor + 1
            cursor += 1
        parts = [part.strip() for part in rest.split(" · ") if part.strip()]
        title = parts[0] if parts else ""
        meta: dict[str, str] = {}
        for part in parts[1:]:
            if ":" in part:
                key, value = part.split(":", 1)
                meta[key.strip()] = value.strip()
        if mark in ("x", "-"):
            index = cursor
            continue
        exec_raw = meta.get("exec", "").strip()
        if not exec_raw:
            exec_raw = (subs.get("执行器", (0, ""))[1]
                        or meta.get("executor", ""))
        exec_value = _executor_of(exec_raw) or exec_raw.strip().lower()
        role_value = subs.get("角色", (0, ""))[1]
        workflow = subs.get("工作流", (0, ""))[1]
        if exec_value in ("me", "assistant"):
            index = cursor
            continue
        role_id = None
        if role_value:
            role_id = role_value if role_value in ROLE_IDS else NAME_TO_ROLE.get(role_value)
        kind = "delivery" if "开发交付流水线" in workflow else "single"
        # 资格：显式执行器（含通用 agent）、明确角色或交付工作流；全部缺失不猜。
        if (exec_value not in EXECUTOR_IDS and exec_value != "agent"
                and role_id is None and kind != "delivery"):
            index = cursor
            continue
        run_ref = meta.get("run", "").strip()
        if not run_ref:
            run_ref = subs.get("运行", (0, ""))[1]
        if run_ref:
            index = cursor
            continue
        project = meta.get("proj", "").strip()
        if not project:
            project = subs.get("项目", (0, ""))[1]
        declared = exec_value if exec_value in EXECUTOR_IDS else None
        changes = None
        changes_text = subs.get("改代码", (0, ""))[1]
        if changes_text in ("是", "改", "要"):
            changes = True
        elif changes_text in ("否", "不改", "不要"):
            changes = False
        elif role_id == "dev":
            changes = True
        prd_path = None
        prd_raw = subs.get("PRD", (0, ""))[1]
        if prd_raw and root is not None:
            prd_path = display_path(root, prd_raw, base=root)
        difficulty = 3 if kind == "delivery" else (
            2 if changes is True else (1 if changes is False else None))
        stages = list(DELIVERY_STAGES) if kind == "delivery" else list(
            SINGLE_STAGES.get(role_id or "", []))
        outputs = []
        outputs_raw = subs.get("产出", (0, ""))[1]
        if outputs_raw:
            for part in re.split(r"[、,，;；]", outputs_raw):
                item = _safe(part.strip())
                if item:
                    outputs.append(item)
        summary = _safe(subs.get("说明", (0, ""))[1]) if subs.get("说明") else None
        plan_rel = None
        if root is not None:
            plan_rel = _rel(root, source)
        plan_path = source.as_posix()
        sources = [_source_ref(source, start, "标题", end)]
        for key, field in (("角色", "角色"), ("说明", "说明"), ("工作流", "工作流"),
                           ("PRD", "PRD"), ("产出", "产出"), ("改代码", "改代码"),
                           ("执行器", "执行器")):
            if key in subs:
                sources.append(_source_ref(source, subs[key][0], field))
        tasks.append({
            "id": f"{plan_rel or source.name}#{task_no}",
            "taskNo": task_no,
            "project": _safe(project) or "项目未记录",
            "title": _safe(title) or "记录不可展示",
            "summary": summary,
            "planPath": plan_path,
            "roleId": role_id,
            "declaredExecutor": declared,
            "changesCode": changes,
            "kind": kind,
            "prdPath": prd_path,
            "difficulty": difficulty,
            "stages": stages,
            "outputs": outputs,
            "defaultExecutor": None,
            "defaultMode": None,
            "defaultReason": "",
            "fallback": False,
            "selectable": True,
            "disabledReason": None,
            "sources": sources,
        })
        index = cursor
    return tasks, plan_status


def parse_daily(text: str, *, source: Path) -> list[dict]:
    """解析当日计划里可派发的 agent 任务（仅 active 计划、排除人类任务等）。"""
    tasks, plan_status = _daily_tasks(text, Path(source))
    if _as_text(plan_status).strip().lower() != "active":
        return []
    return tasks


def _plan_path_key(path) -> str:
    """计划文件的规范化完整路径键（纯词法，不读取该文件）。"""
    full = os.path.normpath(str(path)).replace("\\", "/")
    return "path:" + os.path.normcase(full).replace("\\", "/")


def _plan_task_key(value: str, root: Path | None = None):
    """把 ``<计划>#T<n>`` 归一成 (计划键, 任务号)；无法识别返回 None。

    含目录的引用（绝对或相对）只做规范化完整路径比较，相对路径按数据 root
    解析；只有真正的裸计划名（不含目录）才允许按文件名匹配。全程只做词法
    规范化，不读取被引用的计划，也不把引用写进任何输出。
    """
    text = _as_text(value)
    if not text or "#" not in text:
        return None
    plan_part, task_no = text.rsplit("#", 1)
    plan_part = plan_part.strip().replace("\\", "/")
    task_no = task_no.strip()
    if not plan_part or not task_no:
        return None
    absolute = bool(re.match(r"^[A-Za-z]:/", plan_part)) or plan_part.startswith("/")
    if "/" not in plan_part and not re.match(r"^[A-Za-z]:", plan_part):
        name = plan_part
        if name.lower().endswith(".md"):
            name = name[:-3]
        return "name:" + name.casefold(), task_no.casefold()
    if absolute:
        full = plan_part
    else:
        base = Path(root) if root is not None else Path.cwd()
        full = (base / plan_part).as_posix()
    return _plan_path_key(full), task_no.casefold()


# ---------------------------------------------------------------------- 运行

def _sections(text: str):
    """返回 {标题: (标题行号, [(行号, 文本), …])}。"""
    sections: dict[str, tuple[int, list[tuple[int, str]]]] = {}
    current = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match is not None:
            current = match.group(1).strip()
            sections.setdefault(current, (lineno, []))
            continue
        if current is not None:
            sections[current][1].append((lineno, line))
    return sections


def _title_item(text: str, title: str) -> str:
    if " · " in title:
        return title.split(" · ", 1)[1].strip()
    return title.strip()


def _clean_log_line(line: str, tz=TZ):
    """「- 2027-03-09 09:41 — 活跃：…」→ (ISO 时间或 None, 正文)。"""
    match = re.match(r"^\s*-\s+(.+?)\s+—\s+(.+)$", line)
    if match is None:
        return None, line.strip().lstrip("-").strip()
    stamp, body = match.group(1).strip(), match.group(2).strip()
    iso, dt, _day = _moment(stamp, tz)
    body = re.sub(r"^(活跃|等待|等待输入|已结束|失败|产出|进展)[：:]\s*", "", body)
    return (iso if dt is not None else None), body.strip()


def _latest_time(sections, line_no: int = 0, *, tz=TZ):
    best = None
    best_iso = None
    for _heading, (start, lines) in sections.items():
        for lineno, line in lines:
            iso, _body = _clean_log_line(line, tz)
            if iso is None:
                continue
            _text, dt, _day = _moment(iso, tz)
            if dt is None:
                continue
            if best is None or dt > best:
                best = dt
                best_iso = iso
                line_no = lineno
    return best_iso, line_no


def _stage_labels(kind: str, role_id: str | None) -> list[str]:
    if kind == "delivery":
        return list(DELIVERY_STAGES)
    return list(SINGLE_STAGES.get(role_id or "", []))


def _build_stages(kind: str, role_id: str | None, raw_status: str,
                  current: str | None, source: Path, section) -> list[dict]:
    labels = _stage_labels(kind, role_id)
    current_label = None
    if current:
        current_label = STAGE_ALIASES.get(current, current)
        if current_label not in labels and current_label is not None:
            current_label = None
    states: list[str] = []
    if labels:
        if current_label is not None:
            index = labels.index(current_label)
            states = ["done"] * index + ["current"] + ["unknown"] * (
                len(labels) - index - 1)
        elif current and current in ("完成", "已合回", "已合入"):
            states = ["done"] * len(labels)
            if labels[-1] == "你来体验":
                states[-1] = "unknown"
        else:
            # 没有明确阶段/完成事件时，status 只能说明运行态，不能给关卡着色。
            states = ["unknown"] * len(labels)
    out = []
    for label, state in zip(labels, states):
        out.append({"label": label, "state": state,
                    "source": _source_ref(source, section[0], "阶段")})
    return out


def _parse_packages(text: str, source: Path, sections, req: str, root: Path | None,
                    diagnostics: list, *, tz=TZ):
    section = sections.get("工作包")
    if section is None:
        return []
    rows = []
    for _lineno, line in section[1]:
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 8:
            continue
        if cells[0] in ("包",) or not cells[0] or set(cells[0]) <= set("-: "):
            continue
        pid = _safe(cells[0], limit=40) or ""
        if not pid:
            continue
        deps = [dep for dep in re.split(r"[，,、\s]+", cells[2])
                if dep and dep not in ("—", "-")]
        round_match = re.search(r"\d+", cells[6])
        review_round = int(round_match.group(0)) if round_match else None
        updated_iso, _dt, _day = _moment(cells[7], tz)
        first_review = None
        if req and root is not None:
            # 首轮一律取 P<n>-r1.md：当前轮次可能已是 2/3，r1 仍是首轮 verdict。
            rel = f"docs/delivery/{req}/reviews/{pid}-r1.md"
            review_path = checked_source(root, rel, "review")
            if review_path is not None:
                review_text = _read_cached(review_path)
                verdict = _review_verdict(review_text)
                if verdict is not None:
                    first_review = {
                        "round": 1,
                        "verdict": verdict,
                        "source": _source_ref(review_path, 1, "verdict"),
                    }
        if review_round is not None and review_round > 1 and first_review is None:
            # 只按运行明确关联的工作包记录判断，绝不扫目录找 r2 报告凑诊断。
            diagnostics.append(_diag(
                "review_round_not_first",
                f"工作包 {pid} 记录审查轮次 {review_round}，但读不到首轮 verdict，"
                "不能把后续轮次当首轮证据",
                _rel(root, source) if root is not None else None))
        rows.append({
            "id": pid,
            "state": _safe(cells[1], limit=40) or "unknown",
            "dependencies": deps,
            "reviewRound": review_round,
            "updatedAt": updated_iso,
            "firstReview": first_review,
        })
    return rows


def _review_verdict(text: str) -> str | None:
    """只读 review frontmatter 的 verdict；正文不参与首轮判定。"""
    front, _end = _parse_frontmatter(text)
    raw = _as_text((front or {}).get("verdict"))
    return REVIEW_VERDICTS.get(raw.strip()) if raw else None


def _session_handle(body: str, segments: list[str]) -> str | None:
    """只在内部提取会话句柄用于去重；绝不投影到快照。"""
    match = re.search(r"\bhandle\s+([^\s·]+)", body)
    if match is not None:
        return match.group(1)
    # delivery.py 的真实格式以 ``· <handle>`` 结尾（term_...）；不含目录信息。
    tail = segments[-1].strip() if segments else ""
    if re.fullmatch(r"(?:term_)?[A-Za-z][A-Za-z0-9_-]{2,}", tail):
        return tail
    return None


def _parse_sessions(text_lines, source: Path, *, run_agent: str | None = None,
                    delivery: bool = False):
    agents = []
    seen = set()
    for lineno, line in text_lines:
        match = re.match(r"^\s*-\s+(.+?)\s+—\s+(.+)$", line)
        if match is None:
            continue
        body = match.group(2).strip()
        segments = [part.strip() for part in body.split(" · ")]
        first = segments[0]
        if "/" not in first:
            continue
        role_text, skill_text = first.split("/", 1)
        role_text = role_text.strip()
        role_id = NAME_TO_ROLE.get(role_text)
        if role_id is None:
            role_id = NAME_TO_ROLE.get(role_text.casefold())
        if role_id is None:
            continue
        package_id = None
        package_match = re.search(r"@\s*(P\d{1,3})\b", skill_text)
        if package_match is not None:
            package_id = package_match.group(1)
        # activity 只保留 skill 本身；@P1 / @assistant 是包或地点，不混入活动文本。
        activity_text = skill_text.split("@", 1)[0].strip()
        activity = _safe(activity_text) or None
        handle = _session_handle(body, segments)
        session_key = f"handle:{handle}" if handle else None
        key = session_key or (role_id, first)
        if key in seen:
            continue
        seen.add(key)
        # 会话里明确写出的执行器优先；否则仅 delivery 且 skill 固定规则与角色同时匹配才采用。
        executor_match = re.search(
            r"\b(?:executor|exec)\s*[=:：]\s*(claude|codex|opencode)\b",
            body, re.IGNORECASE)
        executor = (executor_match.group(1).lower()
                    if executor_match is not None else None)
        if executor is None and run_agent in EXECUTOR_IDS:
            # 运行记录 frontmatter 的 agent 是本次运行的直接证据。
            executor = run_agent
        if executor is None and delivery:
            rule = SKILL_EXECUTORS.get(activity_text.casefold())
            if rule is not None and rule[0] == role_id:
                executor = rule[1]
        agent = {
            "roleId": role_id,
            "executor": executor,
            "packageId": package_id,
            "activity": activity,
            "state": "unknown",
            "lastChangedAt": None,
        }
        if session_key is not None:
            agent["_sessionKey"] = session_key
        agents.append(agent)
    return agents


def _package_session_handles(sections) -> dict[str, str]:
    """内部读取工作包表的“会话”列，用于按包绑定无 @P 的会话行。"""
    section = sections.get("工作包")
    if section is None:
        return {}
    handles = {}
    for _lineno, line in section[1]:
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 8 or not cells[0] or cells[0] == "包":
            continue
        handle = _safe(cells[5], limit=120)
        if handle and handle not in ("—", "-"):
            handles[cells[0]] = handle
    return handles


def _session_rows(text_lines, tz):
    """按固定会话行格式取角色/包/时间，供出勤计数；不读任何日志语义。"""
    rows = []
    for lineno, line in text_lines:
        match = re.match(r"^\s*-\s+(.+?)\s+—\s+(.+)$", line)
        if match is None:
            continue
        stamp, body = match.group(1).strip(), match.group(2).strip()
        segments = [part.strip() for part in body.split(" · ")]
        first = segments[0]
        if "/" not in first:
            continue
        role_text, skill_text = first.split("/", 1)
        role_text = role_text.strip()
        role_id = NAME_TO_ROLE.get(role_text)
        if role_id is None:
            role_id = NAME_TO_ROLE.get(role_text.casefold())
        if role_id is None:
            continue
        package_match = re.search(r"@\s*(P\d{1,3})\b", skill_text)
        package_id = package_match.group(1) if package_match is not None else None
        handle = _session_handle(body, segments)
        iso, dt, _day = _moment(stamp, tz)
        if dt is None:
            continue
        rows.append({
            "roleId": role_id,
            "packageId": package_id,
            "handle": handle,
            "time": dt,
            "order": lineno,
        })
    return rows


def _completion_time(sections, status: str, tz):
    """完成时间只取 status=done 且「验收」节里的时间戳行。

    delivery.py finish 写固定「已快进合回」行，follow.py --close 的正文可任意，
    所以这里只认生产者写入的时间戳，不判断正文语义。
    """
    if status != "done":
        return None
    best = None
    for _lineno, line in sections.get("验收", (0, []))[1]:
        iso, _body = _clean_log_line(line, tz)
        if iso is None:
            continue
        _text, dt, _day = _moment(iso, tz)
        if dt is None:
            continue
        if best is None or dt > best[0]:
            best = (dt, iso)
    return best[1] if best is not None else None


def _parse_run(text: str, source: Path, *, tz=TZ,
               root: Path | None = None):
    """内部实现：返回 (Run dict, meta)；meta 含分节原文，供 load_snapshot 关联。"""
    source = Path(source)
    root = root if root is not None else _root_of(source, 1)
    front, _end = _parse_frontmatter(text)
    if front is None:
        return None, None
    run_type = _as_text(front.get("type")).lower()
    if run_type != "run":
        return None, None
    run_id = _rel(root, source) if root is not None else source.name
    raw_status_value = _as_text(front.get("status"))
    raw_status = _safe(raw_status_value, limit=60)
    status_blocked = bool(raw_status_value) and raw_status is None
    if raw_status is None:
        raw_status = "unknown"
    kind = "delivery" if _as_text(front.get("kind")).lower() == "delivery" else "single"
    role_id = _as_text(front.get("role"))
    role_id = role_id if role_id in ROLE_IDS else None
    project = _safe_or_blank(front.get("project"))
    sections = _sections(text)
    title = ""
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    stage_section = sections.get("阶段")
    current = None
    if stage_section is not None:
        for _lineno, line in stage_section[1]:
            match = re.match(r"^\s*-\s*当前[：:]\s*(.+?)\s*$", line)
            if match is not None:
                current = match.group(1).strip()

    diagnostics = []

    if raw_status == "done":
        status = "done"
    elif raw_status == "dispatched":
        status = "running"
    else:
        status = "unknown"

    if status_blocked:
        diagnostics.append(_diag(
            "text_blocked", "原状态命中敏感规则，整字段屏蔽并保留未知状态", run_id))
    if status == "unknown" and raw_status != "planned":
        diagnostics.append(_diag(
            "run_status_unmapped",
            f"运行状态 {raw_status or '未知值'} 不是允许的终态，按未知展示，保留原状态说明",
            run_id))
    if status == "done" and current and current not in ("完成", "已合回", "已合入"):
        diagnostics.append(_diag(
            "run_status_conflict",
            f"字段 status=done 与正文“当前：{_safe(current, limit=40) or '未记录'}”冲突，以字段为准并附诊断",
            run_id))

    created_iso, created_dt, _day = _moment(front.get("created"), tz)
    completed_iso = _completion_time(sections, status, tz)

    last_changed_iso, _lc_line = _latest_time(sections, tz=tz)
    if last_changed_iso is None:
        updated_iso, _udt, _uday = _moment(front.get("updated"), tz)
        last_changed_iso = updated_iso

    stages = _build_stages(kind, role_id, status, current, source,
                           stage_section or (1, 1))

    agents = []
    run_agent = _as_text(front.get("agent")).lower()
    session_section = sections.get("会话")
    session_rows = []
    has_sessions = False
    if session_section is not None:
        agents = _parse_sessions(session_section[1], source, run_agent=run_agent,
                                 delivery=kind == "delivery")
        session_rows = _session_rows(session_section[1], tz)
        has_sessions = bool(agents)
    if not agents and role_id is not None:
        executor = run_agent
        agents = [{
            "roleId": role_id,
            "executor": executor if executor in EXECUTOR_IDS else None,
            "packageId": None,
            "activity": None,
            "state": "done" if status == "done" else "unknown",
            "lastChangedAt": last_changed_iso,
        }]
    for index, agent in enumerate(agents, start=1):
        agent["id"] = f"agent-{index}"
        if has_sessions:
            # 会话状态不从监督自由文本反推；没有状态证据就是 unknown。
            agent["state"] = "unknown"
            agent["lastChangedAt"] = None
            continue
        agent["lastChangedAt"] = agent.get("lastChangedAt") or last_changed_iso
        agent["state"] = "done" if status == "done" else "unknown"
    # 原会话标识只在内部用于跨记录去重，投影里只保留脱敏的 agent-N。
    agent_sessions = {}
    for agent in agents:
        session_key = agent.pop("_sessionKey", None)
        if session_key is not None:
            agent_sessions[agent["id"]] = session_key

    progress = []
    for lineno, line in sections.get("监督", (0, []))[1]:
        if not line.strip().startswith("-"):
            continue
        _iso, body = _clean_log_line(line, tz)
        value = _safe(body)
        if value is None:
            diagnostics.append(_diag(
                "text_blocked", "监督行命中敏感规则，整行不展示", run_id))
            continue
        if value:
            progress.append(value)
    decisions = []
    for lineno, line in sections.get("决策记录", (0, []))[1]:
        if not line.strip().startswith("-"):
            continue
        _iso, body = _clean_log_line(line, tz)
        value = _safe(body)
        if value is None:
            diagnostics.append(_diag(
                "text_blocked", "决策行命中敏感规则，整行不展示", run_id))
            continue
        if value:
            decisions.append(value)

    req = _as_text(front.get("req"))
    packages = _parse_packages(text, source, sections, req, root, diagnostics, tz=tz)

    sources = [_source_ref(source, 1, "frontmatter")]
    for key in ("status", "created", "role"):
        pattern = re.compile(rf"^{key}:", re.MULTILINE)
        match = pattern.search(text)
        if match is not None:
            lineno = text.count("\n", 0, match.start()) + 1
            sources.append(_source_ref(source, lineno, key))
    for heading in ("阶段", "工作包", "会话", "决策记录", "监督", "体验", "验收"):
        if heading in sections:
            start, lines = sections[heading]
            end = lines[-1][0] if lines else start
            sources.append(_source_ref(source, start, heading, end))

    run = {
        "id": run_id,
        "kind": kind,
        "project": project,
        "item": _safe_or_blank(_title_item(text, title)),
        "status": status,
        "rawStatus": raw_status,
        "roleId": role_id,
        "createdAt": created_iso,
        "completedAt": completed_iso,
        "lastChangedAt": last_changed_iso,
        "stale": None,
        "stages": stages,
        "agents": agents,
        "packages": packages,
        "progress": progress,
        "decisions": decisions,
        "pendingIds": [],
        "detailPath": source.as_posix(),
        "firstReview": None,
        "sources": sources,
        "diagnostics": diagnostics,
    }
    meta = {
        "sections": sections,
        "req": req,
        "task": _as_text(front.get("task")),
        "agentSessions": agent_sessions,
        "sessionRows": session_rows,
        "packageHandles": _package_session_handles(sections),
        "createdDt": created_dt,
        "completedDay": _local_date(completed_iso, tz),
        # v1.1：在哪台设备上跑、单任务有没有配跟进 Agent（指挥台据此给回复区）；不投影 handle
        "device": _device_of(front.get("device")),
        "hasFollower": bool(FOLLOW_LINE_RE.search(text)),
    }
    return run, meta


def parse_run(text: str, *, source: Path) -> dict | None:
    """解析一份 ``type: run`` 记录；非 run、坏 frontmatter 返回 ``None``。"""
    run, _meta = _parse_run(text, Path(source))
    return run


# ---------------------------------------------------------------------- 角色

def _role_status(value: str) -> str:
    text = _as_text(value).lower()
    return text if text in ("draft", "active") else "unknown"


def _workspace_of(value: str):
    text = _as_text(value).lower()
    if text in ("existing", "assistant", "directory"):
        return "directory"
    if text in ("new", "worktree"):
        return "worktree"
    return None


def _parse_role(text: str, source: Path, diagnostics: list | None = None):
    source = Path(source)
    front, _end = _parse_frontmatter(text, loose=False)
    if not front:
        # 缺 frontmatter 或 YAML 损坏都不算可解析角色，不按文件名补占位。
        return None
    slug = _as_text(front.get("slug"))
    role_id = slug if slug in ROLE_IDS else source.stem
    if role_id not in ROLE_IDS:
        return None
    status = _role_status(front.get("status"))
    name = _safe(front.get("name")) or ROLE_NAMES[role_id]
    executors = []
    default_executor = _as_text(front.get("default_executor")).lower()
    raw_executors = front.get("executors")
    if isinstance(raw_executors, str):
        raw_executors = [part.strip() for part in raw_executors.strip("[]").split(",")]
    if isinstance(raw_executors, list):
        for item in raw_executors:
            value = _as_text(item).lower()
            if value in EXECUTOR_IDS and value not in executors:
                executors.append(value)
    if default_executor in EXECUTOR_IDS:
        executors = [default_executor] + [item for item in executors
                                          if item != default_executor]
    skills = []
    raw_skills = front.get("skills")
    if isinstance(raw_skills, str):
        raw_skills = [part.strip() for part in raw_skills.strip("[]").split(",")]
    if isinstance(raw_skills, list):
        for item in raw_skills:
            value = _safe(item, limit=80)
            if value and value not in skills:
                skills.append(value)
    use_when = _safe(front.get("use_when"))
    if use_when is None and _as_text(front.get("use_when")):
        if diagnostics is not None:
            diagnostics.append(_diag(
                "text_blocked", "字段命中敏感规则，整段屏蔽，不展示原值",
                _rel(_root_of(source, 2) or source.parent, source)))
    lines = text.splitlines()
    sources = []
    for key in ("status", "name", "default_executor", "executors", "workspace",
                "skills", "use_when"):
        for index, line in enumerate(lines, start=1):
            if re.match(rf"^{key}:", line):
                sources.append(_source_ref(source, index, key))
                break
    role = {
        "id": role_id,
        "name": name,
        "status": status,
        "executors": executors,
        "skills": skills,
        "useWhen": use_when,
        "avatarKey": role_id,
        "completed": _metric(None, [], "没有可覆盖的完成历史"),
        "firstPass": _metric(None, [], "一次通过只算开发角色"),
        "activeSessions": _metric(None, [], "没有可靠会话信息"),
        "level": None,
        "levelProgress": None,
        "isNew": None,
        "sources": sources,
    }
    role["workspace"] = _workspace_of(front.get("workspace")) if status != "unknown" else None
    return role


def parse_role(text: str, *, source: Path) -> dict | None:
    """解析角色 frontmatter；只投影 name/status/执行器/skills/use_when/workspace。"""
    return _parse_role(text, Path(source))


def _level_of(completed):
    if completed is None:
        return None, None
    if completed < 3:
        return 1, {"current": completed, "span": 3, "nextRemaining": 3 - completed}
    if completed < 10:
        return 2, {"current": completed - 3, "span": 7,
                   "nextRemaining": 10 - completed}
    if completed < 30:
        return 3, {"current": completed - 10, "span": 20,
                   "nextRemaining": 30 - completed}
    return 4, {"current": 1, "span": 1, "nextRemaining": 0}


# -------------------------------------------------------------------- 统计

def _window_metrics(runs: list[dict], now: datetime) -> dict:
    aware, today, tz = _now_parts(now)
    monday = today - timedelta(days=today.weekday())
    running_ids = []
    dispatched_ids = []
    completed_today_ids = []
    completed_week_ids = []
    unknown_completion = False
    dispatched_today = 0
    for run in runs:
        if run.get("rawStatus") == "dispatched" and run.get("status") != "done":
            running_ids.append(run["id"])
        created_day = _local_date(run.get("createdAt"), tz)
        raw_status = _as_text(run.get("rawStatus")).casefold()
        # 今日出发只按 dispatch.py/delivery.py 写下的运行 frontmatter：
        # type=run、created 是今天、raw status 属于四个生产者状态。planned/未知/
        # 空值/敏感屏蔽等没有明确派出事实，不计入。
        if created_day == today and raw_status in {
                "dispatched", "done", "failed", "abandoned"}:
            dispatched_today += 1
            dispatched_ids.append(run["id"])
        if run.get("status") == "done":
            completed_day = run.get("_completedDay") or _local_date(
                run.get("completedAt"), tz)
            if completed_day is None:
                unknown_completion = True
                continue
            if completed_day == today:
                completed_today_ids.append(run["id"])
            if monday <= completed_day <= today:
                completed_week_ids.append(run["id"])
    return {
        "runningIds": running_ids,
        "dispatchedToday": dispatched_today,
        "dispatchedIds": dispatched_ids,
        "completedToday": None if unknown_completion else len(completed_today_ids),
        "completedTodayIds": completed_today_ids,
        "completedWeek": None if unknown_completion else len(completed_week_ids),
        "completedWeekIds": completed_week_ids,
        "unknownCompletion": unknown_completion,
    }


def _clear_streak(history, now: datetime):
    if not isinstance(history, list) or not history:
        return None, "上游未保留完整清空历史，无法证明日终余额"
    aware, today, _tz = _now_parts(now)
    by_day: dict[date, dict] = {}
    for entry in history:
        if not isinstance(entry, dict):
            continue
        raw = _as_text(entry.get("date"))
        if not raw:
            continue
        try:
            day = date.fromisoformat(raw[:10])
        except ValueError:
            continue
        by_day[day] = entry
    if not by_day:
        return None, "上游未保留完整清空历史，无法证明日终余额"
    # 只有走到上游列表最早一天才算「完整列表耗尽」；中间断档或最早日之前
    # 没有记录都属于未知，不能把缺日当成正常结束。
    earliest = min(by_day)
    cursor = today - timedelta(days=1)
    if cursor < earliest:
        return None, "缺少已结束自然日的完整记录，连续清空按未知"
    streak = 0
    while cursor >= earliest:
        entry = by_day.get(cursor)
        if entry is None:
            return None, "清空历史中间断档，连续清空按未知"
        if _as_text(entry.get("coverage")) != "complete":
            return None, "存在未覆盖的自然日，连续清空按未知"
        balance = entry.get("endBalance")
        if not isinstance(balance, int) or isinstance(balance, bool):
            return None, "存在未知日终余额，连续清空按未知"
        if balance != 0:
            return streak, None
        streak += 1
        cursor -= timedelta(days=1)
    return streak, None


def compute_stats(pending: list[dict], runs: list[dict], roles: list[dict],
                  *, now: datetime, history: list[dict] | None = None) -> dict:
    """按 impl-plan 第 5 节计算六个整体指标（每个都带 sourceIds 和未知原因）。"""
    _aware, _today, _tz = _now_parts(now)
    pending_unprocessed = [p for p in pending if p.get("status") == "待处理"]
    pending_metric = _metric(len(pending_unprocessed),
                             [p["sources"][0]["path"] for p in pending_unprocessed
                              if p.get("sources")] or ["runs/pending.md"])
    window = _window_metrics(runs, now)
    run_ids = [run["id"] for run in runs]

    streak, streak_reason = _clear_streak(history, now)
    return {
        "pendingCount": pending_metric,
        "runningCount": _metric(len(window["runningIds"]), window["runningIds"]
                                or run_ids),
        "completedToday": _metric(window["completedToday"],
                                  window["completedTodayIds"] or run_ids,
                                  "有完成记录缺少可确认的完成日期" if window["unknownCompletion"] else None),
        "dispatchedToday": _metric(window["dispatchedToday"],
                                   window["dispatchedIds"] or run_ids),
        "completedWeek": _metric(window["completedWeek"],
                                 window["completedWeekIds"] or run_ids,
                                 "有完成记录缺少可确认的完成日期" if window["unknownCompletion"] else None),
        "clearStreak": _metric(streak, [], streak_reason),
    }


def _unknown_stats(reason_pending: str, reason_runs: str, streak_reason: str) -> dict:
    return {
        "pendingCount": _metric(None, [], reason_pending),
        "runningCount": _metric(None, [], reason_runs),
        "completedToday": _metric(None, [], reason_runs),
        "dispatchedToday": _metric(None, [], reason_runs),
        "completedWeek": _metric(None, [], reason_runs),
        "clearStreak": _metric(None, [], streak_reason),
    }


# ------------------------------------------------------------------ 默认方案

def _defaults_for(task: dict, roles_by_id: dict, environment: dict) -> dict:
    """PRD 4.4 默认方案；与 core.js defaultsFor 同优先级（用户选择在前端叠加）。"""
    in_orca = environment.get("inOrca")
    in_orca = in_orca if isinstance(in_orca, bool) else None
    role_id = task.get("roleId")
    kind = task.get("kind")
    if kind == "delivery":
        # 团队副本固定三人小队，不预选单一执行器；地点固定新建工作区。
        return {
            "executor": None,
            "mode": "worktree",
            "reason": ("团队副本：固定三人小队（开发 · OpenCode、验收 · Codex、"
                       "跟进 · Claude Code）"),
            "fallback": False,
        }
    executor = task.get("declaredExecutor")
    changes = task.get("changesCode")
    reasons = []
    fallback = False

    if executor:
        reasons.append(f"任务已声明：{EXECUTOR_LABELS.get(executor, executor)}")
    prd_rule = PRD_ROLE_DEFAULTS.get(role_id) if role_id else None
    definition = roles_by_id.get(role_id) if role_id else None
    if not executor:
        if prd_rule is not None:
            executor = prd_rule[0]
            reasons.append(f"PRD 角色规则：{ROLE_LABELS.get(role_id, role_id)}")
        elif definition and definition.get("executors"):
            executor = definition["executors"][0]
            reasons.append(f"角色配置：{ROLE_LABELS.get(role_id, role_id)}")

    mode = None
    if prd_rule is not None:
        mode = prd_rule[1]
        reasons.append(f"地点按 PRD 4.4（{ROLE_LABELS.get(role_id, role_id)}）使用"
                       f"{'新建工作区' if mode == 'worktree' else '现有目录'}")
    elif definition and definition.get("workspace") in ("worktree", "directory"):
        mode = definition["workspace"]
        reasons.append(f"角色配置工作区：{ROLE_LABELS.get(role_id, role_id)}（"
                       f"{'新建工作区' if mode == 'worktree' else '现有目录'}）")
    elif changes is True:
        mode = "worktree"
        reasons.append("改代码使用新建工作区")
    elif changes is False:
        mode = "directory"
        reasons.append("不改代码使用现有目录")

    if not executor and kind != "delivery":
        if changes is True:
            executor = "opencode"
            fallback = True
            reasons.append("兜底：无角色与执行器，按明确改代码用 OpenCode")
        elif changes is False:
            executor = "claude"
            fallback = True
            reasons.append("兜底：无角色与执行器，按明确不改代码用 Claude Code")
        else:
            reasons.append("缺少角色、执行器与是否改代码的证据，暂不预选执行器和地点，等你选择")

    if kind != "delivery" and in_orca is False:
        if mode != "terminal":
            reasons.append("不在 Orca 里只能新终端窗口")
        mode = "terminal"
    if not reasons:
        reasons.append("沿用记录默认")
    return {
        "executor": executor if executor in EXECUTOR_IDS else None,
        "mode": mode if mode in MODE_IDS else None,
        "reason": "；".join(reasons),
        "fallback": fallback,
    }


# ------------------------------------------------------------------ 只读缓存

_READ_CACHE: dict[str, str] = {}


def _read_cached(path: Path) -> str:
    key = str(path)
    if key not in _READ_CACHE:
        _READ_CACHE[key] = Path(path).read_text(encoding="utf-8", errors="replace")
    return _READ_CACHE[key]


def _read_checked(root: Path, relative: str, kind: str):
    """经 P0 checked_source 定位并只读一次；返回 (真实路径, 文本)。"""
    path = checked_source(root, relative, kind)
    if path is None:
        return None, None
    return path, _read_cached(path)


# ---------------------------------------------------------------- 体验投影

def _experience_text_facts(section_lines, section_sources, source: Path,
                           root: Path, now: datetime):
    """只读体验分节里的固定模板行：体验链接 / 体验要点 / 用例。

    不解析自由环境行；JSON 卡缺失或损坏时才把这里的固定事实作为降级来源。
    """
    sources = list(section_sources)
    diagnostics = []
    lines = section_lines[1] if section_lines else []
    url = None
    link_line = None
    started = None
    static_claim = False
    expires_raw = None
    tests = None
    focus = []
    tz = now.tzinfo or TZ
    for lineno, line in lines:
        match = re.match(r"^\s*-\s*体验链接[：:]\s*(.+?)\s*$", line)
        if match is not None:
            rest = match.group(1).strip()
            note = ""
            raw_url = rest
            for opener, closer in (("（", "）"), ("(", ")")):
                if opener in rest:
                    raw_url, note = rest.split(opener, 1)
                    if note.endswith(closer):
                        note = note[:-1]
                    break
            safe_url = safe_experience_url(raw_url.strip(), root=root)
            if safe_url is None:
                diagnostics.append(_diag(
                    "experience_link_blocked", "体验链接未通过安全校验，不提供可点击链接",
                    source.as_posix()))
            else:
                url = safe_url
                link_line = lineno
                sources.append(_source_ref(source, lineno, "url"))
            if "已启动" in note:
                started = True
            if "未启动" in note:
                started = False
            if "静态页面" in note or "不用启动" in note:
                static_claim = True
            expiry = re.search(
                r"到期\s*(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?)", note)
            if expiry is not None:
                expires_raw = expiry.group(1)
            continue
        match = re.match(r"^\s*-\s*体验要点[：:]\s*(.+)$", line)
        if match is not None:
            for part in re.split(r"[①②③④⑤⑥⑦⑧⑨⑩；;]", match.group(1)):
                value = _safe(part.strip(), limit=200)
                if value and value not in focus:
                    focus.append(value)
            if focus:
                sources.append(_source_ref(source, lineno, "focus"))
            continue
        match = re.match(r"^\s*-\s*用例[：:]\s*(.+)$", line)
        if match is not None:
            ratio = re.search(
                r"(?:通过\s*)?(\d+)\s*/\s*(?:共\s*)?(\d+)", match.group(1))
            if ratio is not None:
                passed, total = int(ratio.group(1)), int(ratio.group(2))
                if 0 <= passed <= total:
                    tests = {"passed": passed, "total": total, "summary": ""}
                    sources.append(_source_ref(source, lineno, "tests"))
    expires_iso, expires_dt, _expires_day = _moment(expires_raw, tz)
    is_file = bool(url and url.lower().startswith("file:"))
    if is_file:
        # 本地文件始终是静态页；即使文本带了启动/到期字样也不改变。
        state = "static"
    elif expires_dt is not None and expires_dt <= now:
        state = "expired"
    elif started is True:
        state = "recorded-started"
    elif static_claim:
        state = "static"
    else:
        state = "unknown"
    ui = "yes" if (url or tests or focus or state != "unknown") else "unknown"
    exp = {"ui": ui, "state": state, "url": url, "recordedAt": None,
           "expiresAt": expires_iso, "tests": tests, "focus": focus[:5],
           "sources": sources}
    claims = {"started": started, "static_claim": static_claim}
    return exp, diagnostics, claims


def _experience_from_run(run: dict | None, meta: dict | None, root: Path, now: datetime):
    """把运行记录的「体验」分节投影成 Experience。

    JSON 卡是 url/started/expires 的权威来源；卡缺失或损坏时才使用固定模板行。
    两源冲突时取保守结论：已过期或未启动。
    """
    if run is None or meta is None:
        exp = {"ui": "unknown", "state": "unknown", "url": None,
               "recordedAt": None, "expiresAt": None, "tests": None,
               "focus": [], "sources": []}
        return exp, [], []
    section_lines = meta["sections"].get("体验")
    diagnostics = []
    section_sources = []
    if section_lines is not None:
        start, lines = section_lines
        section_sources.append(_source_ref(Path(run["detailPath"]), start, "体验",
                                           lines[-1][0] if lines else start))
    body = "\n".join(line for _lineno, line in (section_lines[1] if section_lines else []))
    text_exp, text_diags, text_claims = _experience_text_facts(
        section_lines, section_sources, Path(run["detailPath"]), root, now)
    diagnostics.extend(text_diags)
    if NO_UI_RE.search(body):
        return ({"ui": "no", "state": "unknown", "url": None, "recordedAt": None,
                 "expiresAt": None, "tests": None, "focus": text_exp["focus"],
                 "sources": section_sources}, [], diagnostics)
    card_match = EXPERIENCE_CARD_RE.search(body)
    if card_match is None:
        return text_exp, [], diagnostics
    card_ref = card_match.group(1).strip()
    if card_ref.lower().startswith("inbox/experience/"):
        relative = card_ref
    else:
        relative = f"inbox/experience/{card_ref}"
    card_path, card_text = _read_checked(root, relative, "experience")
    record = None
    if card_path is not None and card_text is not None:
        try:
            record = json.loads(card_text)
        except ValueError:
            record = None
    if not isinstance(record, dict):
        diagnostics.append(_diag(
            "experience_card_unavailable",
            "体验卡缺失或损坏，按体验分节的固定模板行降级展示",
            run["id"]))
        return text_exp, [], diagnostics

    url_line = 1
    for index, line in enumerate(card_text.splitlines(), start=1):
        if re.match(r'^\s*"url"\s*:', line):
            url_line = index
            break
    raw_url = record.get("url")
    j_url = None
    if isinstance(raw_url, str) and raw_url.strip():
        j_url = safe_experience_url(raw_url, root=root)
        if j_url is None:
            diagnostics.append(_diag(
                "experience_link_blocked", "体验链接未通过安全校验，不提供可点击链接",
                run["id"]))
    pid_raw = record.get("pid")
    has_process = (isinstance(pid_raw, int) and not isinstance(pid_raw, bool)
                   and pid_raw > 0)
    started_raw = record.get("started")
    expires_raw = record.get("expires") or record.get("expiresAt")
    tz = now.tzinfo or TZ
    j_recorded_iso = None
    if has_process:
        j_recorded_iso, _rdt, _rday = _moment(started_raw, tz)
    j_expires_iso, j_expires_dt, _eday = _moment(expires_raw, tz)
    j_tests = None
    raw_tests = record.get("tests")
    if isinstance(raw_tests, dict):
        passed = raw_tests.get("passed")
        total = raw_tests.get("total")
        if (isinstance(passed, int) and not isinstance(passed, bool)
                and isinstance(total, int) and not isinstance(total, bool)
                and 0 <= passed <= total):
            j_tests = {"passed": passed, "total": total,
                       "summary": _safe(raw_tests.get("summary"), limit=200) or ""}
    j_focus = []
    card_focus = record.get("focus")
    if isinstance(card_focus, list):
        for item in card_focus:
            value = _safe(item, limit=200)
            if value and value not in j_focus:
                j_focus.append(value)

    t_url = text_exp["url"]
    if j_url and t_url and j_url != t_url:
        diagnostics.append(_diag(
            "experience_fact_conflict", "体验卡与体验分节的链接不一致，按体验卡展示",
            run["id"]))
        url = j_url
    else:
        url = j_url or t_url
    expires_iso = j_expires_iso or text_exp["expiresAt"]
    expires_dt = j_expires_dt
    if text_exp["expiresAt"]:
        _t_iso, t_dt, _t_day = _moment(text_exp["expiresAt"], tz)
        if t_dt is not None and (expires_dt is None or t_dt < expires_dt):
            expires_dt = t_dt
            expires_iso = text_exp["expiresAt"]
    text_started = text_claims["started"]
    conflict = False
    is_file = bool(url and url.lower().startswith("file:"))
    if is_file:
        state = "static"
    elif expires_dt is not None and expires_dt <= now:
        state = "expired"
    elif has_process and text_started is False:
        # JSON 有进程，但正文明确说未启动：取保守未启动。
        state = "unknown"
        conflict = True
    elif has_process:
        state = "recorded-started"
    elif text_started is True:
        # JSON 没有进程证据，正文却说已启动：取保守未启动。
        state = "unknown"
        conflict = True
    else:
        state = "unknown"
    if conflict:
        diagnostics.append(_diag(
            "experience_fact_conflict",
            "体验卡与体验分节的启动状态冲突，按保守未启动展示", run["id"]))
    recorded_iso = None
    if state in ("recorded-started", "expired") and has_process:
        recorded_iso = j_recorded_iso
    sources = text_exp["sources"]
    if j_url:
        sources = sources + [_source_ref(card_path, url_line, "url")]
    merged_focus = []
    for value in text_exp["focus"] + j_focus:
        if value not in merged_focus:
            merged_focus.append(value)
    tests = j_tests or text_exp["tests"]
    exp = {
        "ui": "yes" if (url or tests or merged_focus
                        or state != "unknown") else "unknown",
        "state": state,
        "url": url,
        "recordedAt": recorded_iso,
        "expiresAt": expires_iso,
        "tests": tests,
        "focus": merged_focus[:5],
        "sources": sources,
    }
    return exp, [], diagnostics


# ------------------------------------------------------------------ 统计补充

def _run_attendance_map(run: dict, meta: dict):
    """单个运行按六个角色给出的出勤覆盖。

    返回 {role_id: (identities, reason)}：identities 为已知内部句柄集合
    （可为空），None 表示 unknown 并带固定原因。done 关闭本运行全部角色。
    """
    state_roles = {"开发中": "dev", "待修": "dev", "审查中": "accept"}
    inactive_states = {"已完成", "完成", "已合入", "已合回", "已取消",
                       "待派", "待审", "已通过", "放弃"}
    if run.get("status") == "done":
        # 本运行结束：只关闭本运行的全部角色，不影响其他运行。
        return {role: (set(), None) for role in ROLE_IDS}

    if run.get("kind") == "delivery":
        rows = meta.get("sessionRows") or []
        handles = meta.get("packageHandles") or {}
        packages = run.get("packages") or []
        if not packages:
            # 没有工作包表：六角色都没有结构证据。
            reason = "交付看板缺少工作包表，出勤按未知"
            return {role: (None, reason) for role in ROLE_IDS}
        result = {}
        for role_id in ROLE_IDS:
            if role_id in ("dev", "accept"):
                # 活动看板的开发/验收由包表决定；没有活动包时为空集。
                result[role_id] = (set(), None)
            else:
                # 工作包只产出开发/验收出勤，其他角色无结构规则。
                result[role_id] = (
                    None, "活动交付看板没有该角色的结构规则，出勤按未知")
        for package in packages:
            state = _as_text(package.get("state"))
            if state in inactive_states:
                continue
            role_id = state_roles.get(state)
            if role_id is None:
                # 状态未知或敏感屏蔽：开发/验收都不能判断。
                reason = "工作包状态不能识别，出勤按未知"
                result["dev"] = (None, reason)
                result["accept"] = (None, reason)
                continue
            handle = handles.get(package.get("id"))
            package_rows = [
                row for row in rows
                if row.get("packageId") == package.get("id")
                or (not row.get("packageId") and handle
                    and row.get("handle") == handle)]
            if not package_rows:
                result[role_id] = (
                    None, "工作包状态与最新会话行不能对应，出勤按未知")
                continue
            latest = max(package_rows, key=lambda row: (row["time"], row["order"]))
            if latest.get("roleId") != role_id:
                result[role_id] = (
                    None, "工作包状态与最新会话行不能对应，出勤按未知")
                continue
            identity = latest.get("handle")
            if not identity:
                result[role_id] = (
                    None, "最新会话行没有可靠会话身份，出勤按未知")
                continue
            identities = result[role_id][0]
            if identities is None:
                # 同一角色的另一个活动包已无法判定：保持 unknown。
                continue
            identities.add(identity)
        return result

    # 单任务：规则⑤没有包表证据；done 已在上面按整运行关闭。
    explicit_role = run.get("roleId") if run.get("roleId") in ROLE_IDS else None
    session_roles = {agent.get("roleId") for agent in run.get("agents", [])
                     if agent.get("roleId") in ROLE_IDS}
    if explicit_role is not None:
        # 显式角色与相矛盾会话角色保守取并集，避免漏掉真实出勤。
        run_roles = {explicit_role} | session_roles
    else:
        run_roles = session_roles
    if not run_roles:
        reason = "单任务没有角色归属与包表证据，出勤按未知"
        return {role: (None, reason) for role in ROLE_IDS}
    unknown_reason = "单任务没有规则⑤包表活动证据，出勤按未知"
    return {role: ((None, unknown_reason) if role in run_roles
                   else (set(), None))
            for role in ROLE_IDS}


def _attendance_from_packages(runs: list[dict], run_meta: dict):
    """按六个角色汇总全部运行的出勤覆盖。

    先让每个运行给出六角色的已知身份集合或 unknown，再 reduce：
    任一相关运行未知则该角色 null 并带上对应运行来源；其余按已知句柄
    去重计数（空集为 0）。按内部句柄去重，句柄不投影到快照。
    """
    known: dict[str, set[str]] = {role: set() for role in ROLE_IDS}
    known_runs: dict[str, set[str]] = {role: set() for role in ROLE_IDS}
    unknown: dict[str, dict[str, str]] = {role: {} for role in ROLE_IDS}
    for run in runs:
        run_id = run["id"]
        meta = run_meta.get(run_id) or {}
        per_run = _run_attendance_map(run, meta)
        for role_id, (identities, reason) in per_run.items():
            if identities is None:
                unknown[role_id][run_id] = reason
                continue
            known[role_id].update(identities)
            known_runs[role_id].add(run_id)
    if not runs:
        return {role: (None, [], "没有任何运行记录，出勤按未知")
                for role in ROLE_IDS}
    result = {}
    for role_id in ROLE_IDS:
        if unknown[role_id]:
            reasons = sorted(set(unknown[role_id].values()))
            result[role_id] = (
                None, sorted(unknown[role_id]), "；".join(reasons))
        else:
            result[role_id] = (
                len(known[role_id]), sorted(known_runs[role_id]), None)
    return result


def _apply_run_stats(runs: list[dict], roles: list[dict],
                     run_meta: dict | None = None):
    run_meta = run_meta or {}

    known_runs = [run for run in runs if run.get("status") in ("running", "waiting", "done")]
    completed_by_role: dict[str, list[str]] = {}
    attendance = _attendance_from_packages(runs, run_meta)
    for run in runs:
        if run.get("status") == "done":
            contributors = {agent.get("roleId") for agent in run.get("agents", [])
                            if agent.get("roleId") in ROLE_IDS}
            for role_id in contributors:
                completed_by_role.setdefault(role_id, []).append(run["id"])
    covered = set()
    for run in known_runs:
        if run.get("roleId") in ROLE_IDS:
            covered.add(run["roleId"])
        for agent in run.get("agents", []):
            if agent.get("roleId") in ROLE_IDS:
                covered.add(agent["roleId"])

    # 首轮审查样本：只取明确关联的 reviews/<包>-r1.md frontmatter verdict，
    # 按 需求+包 去重；正文、包表文字和单 run 自然语言都不参与。
    samples: list[tuple[str, str]] = []
    seen_samples: set[tuple] = set()

    def add_sample(key, review, fallback):
        verdict = review.get("verdict") if isinstance(review, dict) else None
        if verdict not in ("pass", "fix") or key in seen_samples:
            return
        seen_samples.add(key)
        source = (review.get("source") or {}).get("path") or fallback
        samples.append((verdict, source))

    for run in runs:
        req = (run_meta.get(run["id"]) or {}).get("req") or run["id"]
        for package in run.get("packages", []):
            review = package.get("firstReview")
            if review:
                add_sample(("package", req, package.get("id")), review, run["id"])

    for role in roles:
        role_id = role["id"]
        if role_id not in covered:
            role["completed"] = _metric(None, [], "没有可覆盖的完成历史")
            attendance_value, attendance_ids, attendance_reason = attendance.get(
                role_id, (None, [], "出勤只按开发/验收工作包与会话行判断"))
            role["activeSessions"] = _metric(
                attendance_value, [f"agents/{role_id}.md"] + attendance_ids,
                attendance_reason)
            role["level"] = None
            role["levelProgress"] = None
            role["isNew"] = None
            if role_id != "dev":
                role["firstPass"] = _metric(None, [], "一次通过只算开发角色")
            else:
                role["firstPass"] = {
                    "value": None, "sourceIds": [f"agents/{role_id}.md"],
                    "reason": "只有轮次没有 verdict，不计样本",
                    "numerator": None, "denominator": 0,
                }
            continue
        completed_ids = completed_by_role.get(role_id, [])
        completed_count = len(completed_ids)
        role["completed"] = _metric(completed_count,
                                    [f"agents/{role_id}.md"] + completed_ids)
        attendance_value, attendance_ids, attendance_reason = attendance.get(
            role_id, (None, [], "出勤只按开发/验收工作包与会话行判断"))
        role["activeSessions"] = _metric(
            attendance_value, [f"agents/{role_id}.md"] + attendance_ids,
            attendance_reason)
        level, progress = _level_of(completed_count)
        role["level"] = level
        role["levelProgress"] = progress
        role["isNew"] = completed_count == 0
        if role_id == "dev":
            known = [verdict for verdict, _source in samples]
            denominator = len(known)
            numerator = sum(1 for verdict in known if verdict == "pass")
            sample_sources = sorted({source for _verdict, source in samples if source})
            if denominator == 0:
                role["firstPass"] = {
                    "value": None, "sourceIds": [f"agents/{role_id}.md"],
                    "reason": "只有轮次没有 verdict，不计样本",
                    "numerator": None, "denominator": 0,
                }
            else:
                role["firstPass"] = {
                    "value": int(round(numerator * 100 / denominator)),
                    "sourceIds": [f"agents/{role_id}.md"] + sample_sources,
                    "reason": "首轮审查 verdict 样本",
                    "numerator": numerator, "denominator": denominator,
                }
        else:
            role["firstPass"] = {
                "value": None, "sourceIds": [f"agents/{role_id}.md"],
                "reason": "一次通过只算开发角色",
                "numerator": None, "denominator": None,
            }


# ------------------------------------------------------------------ 快照组装

TOP_LEVEL_BLOCKING = ("text_blocked", "path_blocked", "experience_link_blocked")


def _aggregate_diagnostics(extra: list[dict], collected=()) -> list[dict]:
    """顶层放跨记录提示、待处理卡片诊断，以及被待处理引用的运行的诊断。

    未被任何待处理卡片引用的运行，其诊断只留在运行对象上，避免把页面无关的
    记录级提示抬成全局提示。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for item in collected:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        if not isinstance(code, str) or not code or code in seen:
            continue
        if code == "experience_unknown":
            continue
        seen.add(code)
        if code in AGGREGATE_MESSAGES and code in TOP_LEVEL_BLOCKING:
            out.append({"code": code, "message": AGGREGATE_MESSAGES[code],
                        "source": None})
        else:
            out.append(item)
    for item in extra:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        if not isinstance(code, str) or not code or code in seen:
            continue
        seen.add(code)
        out.append(item)
    return out


# -------------------------------------------------------------------- v1.1 指挥台回复

FOLLOW_LINE_RE = re.compile(r"跟进 Agent：Orca 终端 handle\s+(\S+)")
DEVICE_ID_RE = re.compile(r"[0-9a-f]{6,64}")
REPLY_PAGE_BY_LABEL = {label: pid for pid, label in REPLY_PAGE_LABELS.items()}
REPLY_CHOICE_SOURCE = "PRD v1.1 4.2 快捷评价"
REPLY_ACK_RE = re.compile(r"^(\d{2})-(\d{2}) (\d{2}):(\d{2})\s+(.*)$")


def _device_of(raw) -> str | None:
    text = _as_text(raw).strip().strip("'\"").lower()
    return text if DEVICE_ID_RE.fullmatch(text) else None


def _device_names(root: Path, ids) -> dict:
    """registry/devices/<指纹>.yaml 里的 hostname（只取这一个字段，过 safe_text）。"""
    names = {}
    for device_id in sorted({i for i in ids if i}):
        _path, text = _read_checked(root, f"registry/devices/{device_id}.yaml", "device")
        if text is None:
            continue
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            continue
        if isinstance(data, dict):
            name = _safe(data.get("hostname"), limit=60)
            if name:
                names[device_id] = name
    return names


def _reply_device(device_id, local_id, names) -> dict:
    if not device_id:
        return {"id": None, "name": None, "local": None}
    return {"id": device_id, "name": names.get(device_id),
            "local": (device_id == local_id) if local_id else None}


def _reply_choices(labels: dict) -> list[dict]:
    return [{"id": cid, "label": label, "source": REPLY_CHOICE_SOURCE} for cid, label in labels.items()]


def _reply_slot(card, association, kind, receiver, choices, need, page_only, device) -> dict:
    other = device["local"] is False
    return {"card": card, "allowed": not other, "reason": "other_device" if other else None,
            "kind": kind, "receiver": receiver, "association": association,
            "choices": choices, "needText": [c for c in need if any(x["id"] == c for x in choices)],
            "pageOnly": [c for c in page_only if any(x["id"] == c for x in choices)],
            "device": device}


def _pending_reply_slot(item, run, meta, local_id, names):
    """待处理卡片的回复区：要拍板 / 卡住 / 待验收且还没处理才有（PRD v1.1 4.2）。"""
    if item.get("status") != "待处理" or item.get("type") not in ("要拍板", "卡住", "完成"):
        return None
    device = _reply_device((meta or {}).get("device"), local_id, names)
    followed = run is not None and (run["kind"] == "delivery" or (meta or {}).get("hasFollower"))
    if item["type"] == "要拍板":
        choices = [{"id": c["id"], "label": c["label"], "source": c.get("source", "")} for c in item.get("choices") or []]
        need, page_only = [], []
    elif item["type"] == "卡住":
        choices, need, page_only = _reply_choices(BLOCKED_REPLY_CHOICES), ["other"], []
    else:
        choices, need, page_only = _reply_choices(ACCEPT_REPLY_CHOICES), ["reject"], ["later"]
    if run is None:
        kind, receiver = "main", REPLY_RECEIVER_MAIN
    elif followed:
        kind, receiver = "forward", REPLY_RECEIVER_FOLLOW
    elif item["type"] == "完成":
        kind, receiver = "main", REPLY_RECEIVER_MAIN   # 没有跟进的单任务验收由主会话自己记
    else:
        kind, receiver = "forward", REPLY_RECEIVER_EXECUTOR
    return _reply_slot(f"pending:{item['id']}", item["id"], kind, receiver, choices, need, page_only, device)


def _run_reply_slot(run, meta, local_id, names):
    """运行监督卡片的回复区：进行中、等你拍板才有；已通关、状态未知没有。"""
    if run.get("status") not in ("running", "waiting"):
        return None
    device = _reply_device((meta or {}).get("device"), local_id, names)
    followed = run["kind"] == "delivery" or (meta or {}).get("hasFollower")
    return _reply_slot(f"run:{run['id']}", run["detailPath"], "forward",
                       REPLY_RECEIVER_FOLLOW if followed else REPLY_RECEIVER_EXECUTOR,
                       _reply_choices(RUN_REPLY_CHOICES), ["add"], [], device)


def _reply_cells(text: str):
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == len(REPLY_COLUMNS) and PENDING_ID_RE.match(cells[0]):
            yield dict(zip(REPLY_COLUMNS, cells))


def parse_replies(text: str, *, root: Path, tz=TZ, local_id=None, names=None):
    """回复分表 → Reply 列表（新的在前、最多 REPLY_MAX_ITEMS 条）与诊断；不投影 handle。"""
    names = names or {}
    replies, diagnostics, seen = [], [], set()
    for cells in _reply_cells(text):
        rid = cells["编号"]
        if rid in seen:
            continue
        seen.add(rid)
        page = REPLY_PAGE_BY_LABEL.get(cells["页"])
        if page is None:
            diagnostics.append(_diag("reply_page_unknown", "回复的页面无法识别，已跳过", "runs/replies.md"))
            continue
        created_iso, created_dt, _day = _moment(cells["时间"], tz)
        assoc_raw = cells["关联"]
        association, card = None, None
        if page in ("dispatch", "roles"):
            card = page
        elif PENDING_ID_RE.match(assoc_raw):
            association = assoc_raw
            card = f"pending:{assoc_raw}" if page == "pending" else None
        elif assoc_raw and assoc_raw != "—":
            association = display_path(root, assoc_raw)
            run_id = _run_id_of(root, association) if association else None
            card = f"run:{run_id}" if run_id and page == "runs" else None
        status, note = reply_status_of(cells["状态"])
        forwarded = cells["转给"].split("（", 1)[0].strip()
        ack = None
        match = REPLY_ACK_RE.match(cells["回执"])
        if match and created_dt is not None:
            at, _dt, _d = _moment(f"{created_dt.year}-{match.group(1)}-{match.group(2)} "
                                  f"{match.group(3)}:{match.group(4)}", tz)
            ack = {"at": at, "text": _safe_or_blank(match.group(5))}
        body = cells["正文"]
        text_value = _safe(body, limit=REPLY_MAX_CHARS) if body else ""
        replies.append({
            "id": rid, "createdAt": created_iso, "card": card, "page": page,
            "item": _safe_or_blank(cells["事项"]), "association": association,
            "quick": _safe(cells["快捷评价"], limit=30) if cells["快捷评价"] else None,
            "text": text_value if text_value is not None else "（内容已屏蔽）",
            "status": status, "statusNote": _safe(note, limit=200) if note else None,
            "forwardedTo": _safe(forwarded, limit=60) if forwarded and forwarded != "—" else None,
            "ack": ack, "device": _reply_device(_device_of(cells["设备"]), local_id, names),
            "_sort": created_dt,
        })
    replies.sort(key=lambda r: (r["_sort"] is not None, r["_sort"] or datetime.min.replace(tzinfo=tz)),
                 reverse=True)
    if len(replies) > REPLY_MAX_ITEMS:
        diagnostics.append(_diag("replies_truncated",
                                 f"回复超过 {REPLY_MAX_ITEMS} 条，只展示最近的 {REPLY_MAX_ITEMS} 条", "runs/replies.md"))
        replies = replies[:REPLY_MAX_ITEMS]
    for reply in replies:
        reply.pop("_sort", None)
    return replies, diagnostics


def _local_device(environment) -> str | None:
    device = environment.get("device") if isinstance(environment, dict) else None
    return _device_of(device.get("id")) if isinstance(device, dict) else None


def _environment_projection(environment, names=None) -> dict:
    in_orca = False
    available = []
    if isinstance(environment, dict):
        in_orca = environment.get("inOrca") is True
        raw = environment.get("executorsAvailable")
        if isinstance(raw, list):
            for item in raw:
                if item in EXECUTOR_IDS and item not in available:
                    available.append(item)
    out = {"inOrca": in_orca, "executorsAvailable": available}
    local_id = _local_device(environment)
    if local_id:
        given = environment["device"].get("name")
        out["device"] = {"id": local_id, "name": _safe(given, limit=60) or (names or {}).get(local_id)}
    return out


def load_snapshot(root: Path, *, now: datetime, environment: dict) -> dict:
    """只读白名单记录，组装成 P0 ``Snapshot``（不写文件、不联网、不起子进程）。"""
    root = Path(root)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("root 必须是现存的绝对目录")
    aware, today, tz = _now_parts(now)
    _READ_CACHE.clear()
    local_date = today.isoformat()

    extra_diagnostics: list[dict] = []

    pending_path, pending_text = _read_checked(root, "runs/pending.md", "pending")
    pending_available = pending_path is not None
    pending_malformed = False
    if pending_available:
        pending, pending_malformed = _parse_pending(pending_text, pending_path, tz)
    else:
        pending = []
        extra_diagnostics.append(_diag(
            "pending_source_missing", "待处理清单不存在，计数按未知展示",
            "runs/pending.md"))

    daily_rel = f"plans/daily/{local_date}.md"
    daily_path, daily_text = _read_checked(root, daily_rel, "daily")
    if daily_path is None:
        dispatch = []
        extra_diagnostics.append(_diag(
            "daily_source_missing", "当日计划不存在，候选按未知展示", daily_rel))
    else:
        dispatch = parse_daily(daily_text, source=daily_path)

    runs: list[dict] = []
    run_meta: dict[str, dict] = {}
    runs_dir = root / "runs"
    runs_available = runs_dir.is_dir()
    if not runs_available:
        extra_diagnostics.append(_diag(
            "runs_source_missing", "运行记录目录不存在，运行与统计按未知展示", "runs/"))
    else:
        try:
            names = sorted(entry.name for entry in os.scandir(runs_dir)
                           if entry.is_file(follow_symlinks=False)
                           and entry.name.lower().endswith(".md")
                           and entry.name.lower() not in ("pending.md", "index.md", "replies.md"))
        except OSError:
            names = []
            runs_available = False
            extra_diagnostics.append(_diag(
                "runs_source_missing", "运行记录目录不存在，运行与统计按未知展示", "runs/"))
        for name in names:
            path, text = _read_checked(root, f"runs/{name}", "run")
            if path is None or text is None:
                extra_diagnostics.append(_diag(
                    "run_source_blocked", "运行记录路径未通过白名单校验，已跳过",
                    f"runs/{name}"))
                continue
            run, meta = _parse_run(text, path, tz=tz, root=root)
            if run is None:
                extra_diagnostics.append(_diag(
                    "run_frontmatter_invalid", "运行记录 frontmatter 无法解析，已跳过",
                    _rel(root, path)))
                continue
            runs.append(run)
            run_meta[run["id"]] = meta

    runs_by_id = {run["id"]: run for run in runs}

    # 已有匹配运行的当日任务不再作为候选：按 run 的规范化计划路径 + 任务号判断，
    # 路径引用绝不按 basename 兜底；只有裸计划名才允许按名称匹配。
    if dispatch and daily_path is not None:
        match_root = root.resolve()
        daily_key = _plan_path_key(daily_path)
        daily_name_key = "name:" + daily_path.stem.casefold()
        used_tasks = set()
        for run in runs:
            key = _plan_task_key((run_meta.get(run["id"]) or {}).get("task"),
                                 match_root)
            if key is not None:
                used_tasks.add(key)
        dispatch = [task for task in dispatch
                    if (daily_key, task["taskNo"].casefold()) not in used_tasks
                    and (daily_name_key, task["taskNo"].casefold()) not in used_tasks]

    roles: list[dict] = []
    missing_roles: list[str] = []
    for role_id in ROLE_IDS:
        path, text = _read_checked(root, f"agents/{role_id}.md", "role")
        if path is None or text is None:
            missing_roles.append(role_id)
            continue
        role = _parse_role(text, path, extra_diagnostics)
        if role is None:
            missing_roles.append(role_id)
            continue
        roles.append(role)
    if missing_roles:
        # 只诊断，不伪造占位角色；roles 仅保留存在且能解析的定义。
        extra_diagnostics.append(_diag(
            "roles_source_missing",
            "角色文件缺失或无法解析，仅展示可解析角色（"
            + "、".join(missing_roles) + "）", "agents/"))

    roles_by_id = {role["id"]: role for role in roles}
    role_executors = {}
    for rid in ROLE_IDS:
        definition = roles_by_id.get(rid)
        role_executors[rid] = (definition["executors"][0]
                               if definition and definition["executors"] else None)

    for run in runs:
        meta = run_meta.get(run["id"])
        if meta is not None and run["kind"] == "delivery":
            req = meta.get("req")
            if req:
                for package in run["packages"]:
                    if package.get("firstReview") is None:
                        rel = f"docs/delivery/{req}/reviews/{package['id']}-r1.md"
                        review_path = checked_source(root, rel, "review")
                        if review_path is not None:
                            verdict = _review_verdict(_read_cached(review_path))
                            if verdict is not None:
                                package["firstReview"] = {
                                    "round": 1, "verdict": verdict,
                                    "source": _source_ref(review_path, 1, "verdict"),
                                }
        run["stale"] = None
        if run["status"] in ("running", "waiting") and run.get("lastChangedAt"):
            _iso, changed_dt, _day = _moment(run["lastChangedAt"], tz)
            if changed_dt is not None:
                if changed_dt > aware:
                    run["stale"] = False
                    run["diagnostics"].append(_diag(
                        "run_time_future", "最近变化时间晚于生成时间，按异常提示", run["id"]))
                else:
                    run["stale"] = (aware - changed_dt) > timedelta(minutes=30)

    pending_by_run: dict[str, list[str]] = {}
    for item in pending:
        run_id = item.get("runId")
        if run_id and item.get("status") == "待处理" and item.get("type") in (
                "完成", "要拍板", "卡住"):
            pending_by_run.setdefault(run_id, []).append(item["id"])
    for run in runs:
        linked = pending_by_run.get(run["id"], [])
        run["pendingIds"] = linked
        if linked and run["status"] == "running":
            run["status"] = "waiting"

    for item in pending:
        run = runs_by_id.get(item.get("runId"))
        if item.get("type") == "完成" and item.get("status") == "待处理":
            meta = run_meta.get(run["id"]) if run is not None else None
            experience, _ignored, exp_diags = _experience_from_run(run, meta, root, aware)
            item["experience"] = experience
            item["diagnostics"].extend(exp_diags)
            if experience["ui"] == "unknown" and experience["state"] == "unknown" \
                    and not experience["sources"]:
                item["diagnostics"].append(_diag(
                    "experience_unknown", "记录未提供体验信息，按未知展示", item["id"]))

    # v1.1 回复：分表、设备主机名、每张卡片的回复区
    local_id = _local_device(environment)
    _replies_path, replies_text = _read_checked(root, "runs/replies.md", "replies")
    device_ids = {local_id} | {meta.get("device") for meta in run_meta.values()}
    if replies_text:
        device_ids |= {_device_of(cells["设备"]) for cells in _reply_cells(replies_text)}
    names = _device_names(root, device_ids)
    replies, reply_diags = (parse_replies(replies_text, root=root, tz=tz, local_id=local_id, names=names)
                            if replies_text else ([], []))
    extra_diagnostics.extend(reply_diags)
    for item in pending:
        run = runs_by_id.get(item.get("runId"))
        item["reply"] = _pending_reply_slot(item, run, run_meta.get(run["id"]) if run else None, local_id, names)
    for run in runs:
        run["reply"] = _run_reply_slot(run, run_meta.get(run["id"]), local_id, names)

    if not runs_available:
        stats = _unknown_stats("待处理清单缺失", "运行记录目录缺失",
                               "上游未保留完整清空历史")
        if pending_available:
            stats["pendingCount"] = compute_stats(pending, [], roles, now=now)["pendingCount"]
    else:
        stats = compute_stats(pending, runs, roles, now=now)
        if not pending_available:
            stats["pendingCount"] = _metric(None, [], "待处理清单缺失")
    if pending_available and pending_malformed:
        stats["pendingCount"] = _metric(
            None, [], "待处理清单表格格式损坏，计数按未知展示")

    _apply_run_stats(runs, roles, run_meta)

    for task in dispatch:
        defaults = _defaults_for(task, roles_by_id, environment if isinstance(environment, dict) else {})
        task["defaultExecutor"] = defaults["executor"]
        task["defaultMode"] = defaults["mode"]
        task["defaultReason"] = _safe(defaults["reason"], limit=300) or ""
        task["fallback"] = defaults["fallback"]
        if task["kind"] == "delivery":
            if not task.get("prdPath"):
                task["selectable"] = False
                task["disabledReason"] = "PRD 路径未记录，信息不完整，不能组队"
            elif (environment or {}).get("inOrca") is not True:
                task["selectable"] = False
                task["disabledReason"] = "不在 Orca 里：团队副本不能进入"
            else:
                task["selectable"] = True
                task["disabledReason"] = None
        else:
            task["selectable"] = True
            task["disabledReason"] = None

    referenced_runs = {item.get("runId") for item in pending if item.get("runId")}
    collected = []
    for run in runs:
        if run["id"] in referenced_runs:
            collected.extend(run.get("diagnostics", []))
    for item in pending:
        collected.extend(item.get("diagnostics", []))
    diagnostics = _aggregate_diagnostics(list(extra_diagnostics), collected)

    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": aware.isoformat(),
        "localDate": local_date,
        "environment": _environment_projection(environment, names),
        "stats": stats,
        "pending": pending,
        "dispatchTasks": dispatch,
        "runs": runs,
        "roles": roles,
        "diagnostics": diagnostics,
        "replies": replies,
    }


__all__ = [
    "load_snapshot",
    "parse_pending",
    "parse_daily",
    "parse_run",
    "parse_role",
    "compute_stats",
    "parse_replies",
]
