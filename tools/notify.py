r"""
会话间通信 —— 派出去的会话回报 助手主会话：先记待处理清单 runs/pending.md，再推一行消息到主会话。

    python tools/notify.py send --run runs/<运行记录>.md --type 完成 --item "项目：示例事项" --text "一句话" --detail <详情路径>
    python tools/notify.py send … --no-push           # 只记清单不推送（主会话自己记一条要你拍板的事）
    python tools/notify.py list [--all]                  # 待处理清单（--all 连已处理的一起列）
    python tools/notify.py done N1020-01 --note "…"      # 主会话处理完：标为已处理
    python tools/notify.py target                        # 现在发消息会推到哪个主会话（排查用）
每条消息还会弹一条 Windows 通知；没能马上推进主会话的，后台每分钟补推一次（最多 3 小时），你一停下打字就能看到。

指挥台回复（v1.1，回复分表 runs/replies.md，和清单共用锁与编号）：
    python tools/notify.py replies [--waiting|--all]     # 列回复（--waiting：本机发的、还没送到主会话的）
    python tools/notify.py show N1021-07                 # 看一条回复的原文（派发确认 / 角色管理的多条指令按行拆开）
    python tools/notify.py forward N1021-07              # 主会话：按关联找到接收方（跟进 Agent / 执行会话）并转过去
    python tools/notify.py ack N1021-07 --text "…"       # 接收方：回执一句（只写分表、不推送；原消息随回执标已处理）
    python tools/notify.py handled N1021-07 --note "…"   # 主会话：派发确认 / 角色管理类自己执行完

只有四种消息：完成 / 要拍板 / 卡住 / 交接；进展、授权内的决定一律只记运行记录，不发。
推给谁：运行记录里的 report_to（派发它的主会话的 Orca 终端）还活着就推给它，否则推给最近登记的主会话
（tools/prompt_hook.py 在你发话时登记，登记表 inbox/logs/main-sessions.json，只在本机有效）。
推之前看一眼主会话的输入框：你正在打字、或者主会话在等你选选项，就等一会儿（最多 40 秒）；还在打字就不推，
留给 prompt_hook 在你提交那句话时转达。推不到（主会话关了、不在 Orca 里、Orca 重启过）只记清单，不报错。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import common
import console_contract as cc
from common import ROOT, setup_stdio

PENDING = ROOT / "runs" / "pending.md"
STATE = ROOT / "inbox" / "logs" / "main-sessions.json"
LOCK = ROOT / "inbox" / "logs" / "pending.lock"
TYPES = ("完成", "要拍板", "卡住", "交接")
COLS = ["编号", "时间", "类型", "来源", "事项", "一句话", "详情", "送达", "状态"]
PREFIX = "【assistant 消息"
WAIT = 40          # 你在打字时最多等多久再放弃推送（秒），保证 1 分钟内出现
DEFERRED = "待转达"  # 送达列：你在打字，等你提交时由 prompt_hook 转达
# 派发出去的会话的第一句话里会有这些：据此不把它们登记成主会话
DISPATCH_MARKS = ("【角色设定", "【本次任务】", "派发出来的", "tools/notify.py send", "无人值守")


def now(fmt="%Y-%m-%d %H:%M"):
    return datetime.now().strftime(fmt)


# ---------- 待处理清单 ----------

LOCK_WAIT = 10.0   # 拿锁最多等多久（秒）；等不到就报「正忙」，调用方重试，不无锁硬写
LOCK_STALE = 60    # 锁文件超过这么久视为残留（持有者崩了）
FORWARD_UPDATE_ATTEMPTS = 5   # 推送成功之后补记状态：拿不到锁再试几次，最后存待补记（R7）
FORWARD_UPDATE_PAUSE = 1.0


class LockBusy(RuntimeError):
    """等了 LOCK_WAIT 秒还拿不到锁：清单正被另一个会话写，稍后重试。"""


def clear_stale_lock(path: Path, stale: float) -> None:
    """锁文件超过 stale 秒视为残留：先改名挪走（只有一个等待者能挪成），再确认挪走的确实是旧锁才删。"""
    try:
        if time.time() - path.stat().st_mtime <= stale:
            return
        moved = path.with_name(f"{path.name}.stale-{os.getpid()}-{secrets.token_hex(4)}")
        os.rename(path, moved)
    except OSError:
        return
    try:
        if time.time() - moved.stat().st_mtime > stale:
            moved.unlink()
        elif not path.exists():
            os.rename(moved, path)  # 挪走的是刚建的新锁（极短的竞争窗口）：放回去
        else:
            moved.unlink()
    except OSError:
        pass


class Lock:
    """多个会话可能同时写清单：用一个排你文件当锁，锁里写持有者标记。

    - 等 LOCK_WAIT 秒拿不到就抛 LockBusy，不进入写入区（不能无锁写表，会覆盖别人刚写的行）
    - 只有持有者释放自己的锁（按标记核对）；LOCK_STALE 秒以上的视为残留，安全地清掉
    """

    def __init__(self, path: Path | None = None, wait: float | None = None):
        self.path = path
        self.wait = wait
        self.token = None

    def __enter__(self):
        path = self.path or LOCK
        path.parent.mkdir(parents=True, exist_ok=True)
        token = f"{os.getpid()}-{secrets.token_hex(8)}"
        deadline = time.monotonic() + (LOCK_WAIT if self.wait is None else self.wait)
        while True:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                clear_stale_lock(path, LOCK_STALE)
            else:
                try:
                    os.write(fd, token.encode("ascii"))
                finally:
                    os.close(fd)
                self.path, self.token = path, token
                return self
            left = deadline - time.monotonic()
            if left <= 0:
                raise LockBusy("待处理清单正被另一个会话写，稍后重试")
            time.sleep(min(0.1, left))

    def __exit__(self, *exc):
        if self.token is None:
            return
        try:
            if self.path.read_text(encoding="ascii", errors="replace") == self.token:
                self.path.unlink()
        except OSError:
            pass
        self.token = None


def write_atomic(path: Path, text: str) -> None:
    """整表写：先严格按 UTF-8 编码（编不了就抛错、原表不动），写同目录临时文件，再原子替换。调用方持锁。"""
    data = text.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}-{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def blank():
    tpl = (ROOT / "templates" / "pending.md").read_text(encoding="utf-8")
    return re.sub(r"^updated: .*$", f"updated: {now('%Y-%m-%d')}", tpl, count=1, flags=re.M)


def read_rows(text=None):
    text = text if text is not None else (PENDING.read_text(encoding="utf-8") if PENDING.exists() else blank())
    rows = []
    for l in text.splitlines():
        cells = [c.strip() for c in l.strip().strip("|").split("|")]
        if l.startswith("|") and len(cells) == len(COLS) and re.match(r"^N\d{4}-\d+$", cells[0]):
            rows.append(dict(zip(COLS, cells)))
    return text, rows


def write_rows(text, rows):
    table = "| " + " | ".join(COLS) + " |\n|" + "---|" * len(COLS) + "\n" + "".join(
        "| " + " | ".join(r.get(c, "") for c in COLS) + " |\n" for r in rows)
    head = text.split("\n| " + COLS[0], 1)[0].rstrip() + "\n\n"
    text = re.sub(r"^updated: .*$", f"updated: {now('%Y-%m-%d')}", head + table, count=1, flags=re.M)
    write_atomic(PENDING, text)


# 终端控制字符：C0（含 ESC、Ctrl-C）、DEL、C1（含 CSI）。进表、进推给终端的行之前一律换成空格
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# 孤立代理码点：编不成 UTF-8，写表会失败
SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def no_control(s):
    """去掉终端控制字符和孤立代理码点（推给 orca terminal send 的整行再过一遍，表里被手改过也不怕）。"""
    return SURROGATE_RE.sub("\ufffd", CONTROL_RE.sub(" ", str(s or "")))


def cell(s, n=160):
    """表格单元格和消息行都只能是一行：去掉换行、控制字符和竖线，@ 换成全角（Claude Code 输入框里 @ 会弹文件补全）。"""
    s = re.sub(r"\s+", " ", no_control(s)).strip().replace("|", "／").replace("@", "＠")
    return s if len(s) <= n else s[:n] + "…"


def update(mid, **changes):
    with Lock():
        text, rows = read_rows()
        hit = next((r for r in rows if r["编号"] == mid), None)
        if hit:
            hit.update({k: cell(v, 200) for k, v in changes.items()})
            write_rows(text, rows)
        return hit


def pending_count():
    return sum(1 for r in read_rows()[1] if r["状态"] == "待处理")


def line_of(r):
    """推给主会话的那一行：【assistant 消息 · 类型】项目：事项 · 一句话 · 详情 <绝对路径> · 编号 <id>"""
    return no_control(f"{PREFIX} · {r['类型']}】{r['事项']} · {r['一句话']} · 详情 {r['详情']} · 编号 {r['编号']}")


# ---------- 主会话登记 ----------

def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sessions": {}}


def save_state(state):
    items = sorted(state["sessions"].items(), key=lambda kv: kv[1].get("last_seen", ""), reverse=True)[:50]
    state["sessions"] = dict(items)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def classify(session_id, prompt, handle):
    """你（或别人）在某个会话里发话时调用：第一句话决定它是主会话还是派发出去的会话，之后不变。返回 kind。"""
    state = load_state()
    s = state["sessions"].get(session_id)
    if not s:
        if prompt.lstrip().startswith((PREFIX, "【assistant 回复")):
            return ""  # 第一句就是推来的消息（或转来的回复）：说明终端被复用了，不据此判断
        kind = "dispatched" if any(m in prompt for m in DISPATCH_MARKS) else "main"
        s = state["sessions"][session_id] = {"kind": kind, "registered": now()}
    s.update(handle=handle, path=ROOT.as_posix(), last_seen=now("%Y-%m-%d %H:%M:%S"))
    save_state(state)
    return s["kind"]


def report_to():
    """派发时调用：当前会话（派发者）就是「回报给谁」。不在 Orca 里返回空 handle。"""
    handle = os.environ.get("ORCA_TERMINAL_HANDLE", "")
    title = ""
    if handle and shutil.which("orca"):
        try:
            title = ((orca("terminal", "show", "--terminal", handle).get("result") or {}).get("terminal") or {}).get("title", "")
        except Exception:  # noqa: BLE001
            title = ""
    return {"handle": handle, "session": os.environ.get("CLAUDE_CODE_SESSION_ID", "")[:8],
            "title": re.sub(r"^[^\w\[【]+", "", title)[:40]}  # 去掉 Orca 标题前面的状态符号（✳、◐、⠹ 之类）


def dispatched_by(rt=None):
    """派发标识：哪个主会话派的（会话 ID 前 8 位 + 日期 + 终端标题），写进派发记录、Orca 工作区备注和任务说明。"""
    rt = rt or report_to()
    return f"{rt.get('session') or '?'} · {datetime.now():%m-%d} · {rt.get('title') or '主会话'}"


def report_line(rt):
    if rt["handle"]:
        return f"回报给派发它的主会话（会话 {rt['session'] or '?'}，Orca 终端 {rt['handle']}）；联系不上时推给最近登记的主会话，都不在就只记待处理清单"
    return "派发时不在 Orca 里：消息只记待处理清单 runs/pending.md，主会话启动时看「待处理 N 条」"


def report_brief(run_file, types):
    """写进任务说明的「回报」：什么时候发、用哪条命令。run_file 是运行记录或交付看板。"""
    py = (ROOT / "tools" / "notify.py").as_posix()
    return (f"回报：只在 {' / '.join(types)} 时回报派你的 助手主会话，其他（进展、授权内的决定）只记录不打扰。用这条命令发"
            f"（它先记待处理清单、再推一行消息给主会话；推不到也不会报错，不用重试）：\n"
            f'python {py} send --run {Path(run_file).as_posix()} --type <{"|".join(types)}> '
            f'--item "<项目>：<事项>" --text "<一句话；有体验链接带上>" --detail <详情的绝对路径>')


# ---------- 推送 ----------

def orca(*args, timeout=60):
    stub = common.orca_blocked(args)
    if stub is not None:
        return stub
    exe = shutil.which("orca")
    if not exe:
        return {"ok": False, "error": "没有 orca 命令"}
    try:
        r = subprocess.run([exe, *args, "--json"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return json.loads(r.stdout, strict=False)
    except (ValueError, OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)[:300]}


def alive(handle, agents=("claude",)):
    """终端还在、能写、里面跑的是 agents 之一（默认只认 Claude Code）。"""
    t = ((orca("terminal", "show", "--terminal", handle).get("result") or {}).get("terminal") or {})
    return bool(t.get("connected") and t.get("writable") and t.get("agentIdentity") in agents), t


def input_state(handle, agents=("claude",)):
    """看主会话的输入框：返回 (ok / busy / gone, 原因)。只在输入框是空的、也没有在等你选选项时才推（ok）。
    gone：终端没了，或者 Claude Code 已经退出、终端回到了命令行（Orca 的 agentIdentity 这时还会是 claude，不能信）。"""
    ok, meta = alive(handle, agents)
    if not ok:
        return "gone", "终端不在了"
    term = (orca("terminal", "read", "--terminal", handle, "--screen").get("result") or {}).get("terminal") or {}
    lines = [l.rstrip() for l in term.get("tail") or []]
    if (term.get("draft") or "").strip():  # Orca 认出来的输入框草稿（不在 tail 里）
        return "busy", "你正在输入"
    if meta.get("agentWait"):
        return "busy", "主会话在等你选选项"
    if meta.get("agentIdentity") != "claude":
        # Codex / OpenCode 的输入框长得不一样，只认 Orca 给的 draft / agentWait
        return "ok", ""
    sep = [i for i, l in enumerate(lines) if len(l.strip()) >= 20 and set(l.strip()) <= set("─━")]
    for a, b in zip(sep, sep[1:]):  # 输入框：两条横线之间、以 ❯ 开头
        box = lines[a + 1:b]
        if box and box[0].lstrip().startswith(("❯", ">")):
            draft = " ".join([box[0].lstrip()[1:]] + box[1:]).strip()
            if draft and not re.match(r'^Try "', draft):  # 新会话的灰色占位提示不算
                return "busy", "你正在输入"
            return "ok", ""
    last = next((l.strip() for l in reversed(lines) if l.strip()), "")
    if any("Resume this session with" in l for l in lines[-8:]) or re.match(r"^(PS [^>]*>|.*[$#]\s*$)", last):
        return "gone", "主会话的 Claude Code 已经退出"
    return "busy", "看不到输入框（可能在弹窗或选项里）"


def targets(run_fields):
    """候选主会话：先是派发它的那个，再是按最近发话排序的已登记主会话；不推给自己。"""
    me = os.environ.get("ORCA_TERMINAL_HANDLE", "")
    sessions = load_state()["sessions"].values()
    latest = {}  # 同一个终端可能先后跑过几个会话，以最近的为准
    for s in sorted(sessions, key=lambda s: s.get("last_seen", "")):
        if s.get("handle"):
            latest[s["handle"]] = s
    out = []
    first = run_fields.get("report_to", "")
    if first and (latest.get(first) or {}).get("kind", "main") == "main":
        out.append((first, "派发它的主会话"))
    for s in sorted(latest.values(), key=lambda s: s.get("last_seen", ""), reverse=True):
        if s["kind"] == "main" and s["handle"] not in {h for h, _ in out}:
            out.append((s["handle"], f"最近登记的主会话（{s.get('last_seen', '')[:16]}）"))
    return [(h, why) for h, why in out if h != me]


def deliver(row, run_fields, wait=WAIT, line=None):
    """推一行到主会话（line 默认是清单消息行）。返回送达列的文字。"""
    cands = targets(run_fields) if shutil.which("orca") else []
    if not cands:
        return "未推送：没有在 Orca 里登记的主会话"
    for handle, why in cands:  # 第一个还开着的主会话
        state, reason = input_state(handle)
        if state != "gone":
            break
    else:
        return "未推送：主会话都关了或联系不上"
    deadline = time.time() + wait
    while state == "busy" and time.time() < deadline:
        time.sleep(4)
        state, reason = input_state(handle)
    if state != "ok":
        if state == "busy" and ("你正在输入" in reason or "选选项" in reason):
            return f"{DEFERRED}：{reason}，等你提交时转达（{handle}）"
        return f"未推送：{reason}（{handle}）"
    res = orca("terminal", "send", "--terminal", handle, "--text", line or line_of(row), "--enter", "--wait-submit", "10")
    if not res.get("ok"):
        return f"未推送：发送失败 {cell((res.get('error') or {}), 80)}"
    return f"已推送 {now('%H:%M')} → {why} {handle}"


# ---------- 命令 ----------

def run_fields(path):
    if not path:
        return {}, None
    p = Path(path)
    p = p if p.is_absolute() else ROOT / p
    if not p.exists():
        return {}, p
    head = p.read_text(encoding="utf-8").split("\n---", 1)[0]
    return {k: v.strip().strip("'\"") for k, v in re.findall(r"^(\w+):[ \t]*(.*)$", head, re.M)}, p


def source_of(f):
    if f.get("kind") == "delivery":
        return "跟进 Agent"
    role = f.get("role", "")
    if role:
        m = re.search(r"^name:\s*(.+)$", (ROOT / "agents" / f"{role}.md").read_text(encoding="utf-8"), re.M) \
            if (ROOT / "agents" / f"{role}.md").exists() else None
        return m.group(1).strip() if m else role
    return "执行会话" if f else "派发会话"


def cmd_send(a):
    f, run_file = run_fields(a.run)
    item = a.item or (f"{f.get('project')}：{f.get('req') or f.get('task', '')}" if f else "")
    if not item:
        sys.exit("要写 --item「项目：事项」（或者给 --run 运行记录）")
    detail = a.detail or (run_file.as_posix() if run_file else "")
    if detail:
        p = Path(detail)
        detail = (p if p.is_absolute() else Path.cwd() / p).resolve().as_posix()
    src = a.source or source_of(f)
    devs = list((ROOT / "registry" / "devices").glob("*.yaml"))
    if len(devs) > 1:  # 多设备时标上是哪台设备发的（会话只能在那台设备上处理）
        import device
        src += f" · {device.load_device().get('hostname', '')}"
    row = {"时间": now(), "类型": a.type, "来源": cell(src, 40), "事项": cell(item, 60),
           "一句话": cell(a.text), "详情": cell(detail or "—", 200), "送达": "推送中", "状态": "待处理"}
    with Lock():
        text, rows = read_rows()
        dup = next((r for r in rows if r["状态"] == "待处理" and all(r[k] == row[k] for k in ("类型", "来源", "事项", "一句话"))), None)
        if dup:
            row = dup  # 同一件事重复报：合并成一条，不再记新行
        else:
            row["编号"] = next_id(rows, read_replies()[1])  # 和回复分表共用一套编号
            rows.append(row)
            write_rows(text, rows)
    print((f"同一件事已在清单里：{row['编号']}，合并成一条，不新增" if dup else f"已记进待处理清单：{row['编号']}")
          + f"（{PENDING.as_posix()}）")
    if a.no_push:
        update(row["编号"], 送达="不推送（主会话自己记的）")
        return
    if dup and dup["送达"].startswith("已推送"):
        print(f"同一条消息已经推过（{dup['送达']}），这次只合并，不再推送")
        return
    try:
        result = deliver(row, f)
    except Exception as e:  # noqa: BLE001 —— 推送失败不能让发送方停下
        result = f"未推送：{cell(e, 80)}"
    update(row["编号"], 送达=result)
    print(("✅ " if result.startswith("已推送") else "ℹ ") + result)
    print("消息：" + line_of(row))
    toast(row)
    if not result.startswith("已推送"):
        spawn_retry(row["编号"], a.run)


# ---------- 及时送达 ----------
# 1. 每条消息都弹 Windows 通知，不管主会话在不在、你在不在打字；点开是详情
# 2. 没推进主会话的（你在打字、主会话忙），后台每分钟再试一次，输入框一空就推，最多试 RETRY_MIN 分钟；
#    你先提交了话、prompt_hook 已经转达的，就不再推

RETRY_MIN = 180
TOAST_PS = r'''
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$e = { param($s) [Security.SecurityElement]::Escape([string]$s) }
$launch = if ($env:NZ_OPEN) { " activationType=`"protocol`" launch=`"$(& $e $env:NZ_OPEN)`"" } else { '' }
$xml = "<toast$launch><visual><binding template=`"ToastGeneric`"><text>$(& $e $env:NZ_TITLE)</text><text>$(& $e $env:NZ_BODY)</text></binding></visual></toast>"
$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($xml)
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($doc))
'''


def toast(row):
    """弹一条 Windows 通知（只在 Windows 上；失败不影响发送）。"""
    if os.name != "nt" or common.blocked("Windows 通知"):
        return
    try:
        import base64
        detail = row.get("详情", "")
        env = dict(os.environ, NZ_TITLE=f"assistant · {row['类型']} · {row['事项']}"[:120],
                   NZ_BODY=f"{row['一句话']}（{row['编号']}，回主会话处理）"[:300],
                   NZ_OPEN=Path(detail).as_uri() if detail and Path(detail).exists() else "")
        enc = base64.b64encode(TOAST_PS.encode("utf-16-le")).decode()
        subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", enc], env=env, capture_output=True, timeout=30)
    except Exception:  # noqa: BLE001
        pass


def spawn_retry(mid, run):
    """后台再试：不挡发送方，自己退出。"""
    if common.blocked("后台重试推送"):
        return
    args = [sys.executable, str(Path(__file__).resolve()), "retry", mid] + (["--run", run] if run else [])
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    try:
        subprocess.Popen(args, cwd=str(ROOT), creationflags=flags, start_new_session=os.name != "nt",
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass


def cmd_retry(a):
    f, _ = run_fields(a.run)
    deadline = time.time() + RETRY_MIN * 60
    while time.time() < deadline:
        time.sleep(60)
        _, rows = read_rows()
        row = next((r for r in rows if r["编号"] == a.id), None)
        if not row or row["状态"] != "待处理" or row["送达"].startswith(("已推送", "已转达")):
            return  # 处理过了，或者你提交时已经转达
        try:
            result = deliver(row, f, wait=0)
        except Exception:  # noqa: BLE001
            continue
        if result.startswith("已推送"):
            update(a.id, 送达=result + "（后台补推）")
            return
    update(a.id, 送达=f"未推送：{RETRY_MIN} 分钟内主会话一直不空闲，等你提交时转达或看待处理清单")


def cmd_list(a):
    _, rows = read_rows()
    rows = rows if a.all else [r for r in rows if r["状态"] == "待处理"]
    if not rows:
        print("待处理清单是空的")
        return
    print(f"待处理 {sum(1 for r in rows if r['状态'] == '待处理')} 条（{PENDING.as_posix()}）\n")
    for r in rows:
        print(f"- {r['编号']} [{r['状态']}] {r['时间'][5:]} {r['类型']} · {r['来源']} · {r['事项']} · {r['一句话']}\n"
              f"  详情 {r['详情']} · {r['送达']}")


def cmd_done(a):
    hit = update(a.id, 状态=f"已处理 {now('%m-%d %H:%M')}" + (f"：{a.note}" if a.note else ""))
    print(f"{a.id} 已处理" if hit else f"清单里没有 {a.id}")


def cmd_target(a):
    f, _ = run_fields(a.run)
    t = targets(f)
    if not t:
        print("没有登记的主会话：消息只会记进清单")
    for h, why in t:
        state, reason = input_state(h)
        print(f"- {h}（{why}）：" + {"ok": "能推", "busy": f"现在不能推：{reason}", "gone": f"推不到：{reason}"}[state])


# ---------- 指挥台回复（v1.1：回复分表 runs/replies.md，规则见 playbooks/comms.md） ----------
#
# 你在指挥台卡片上点「发送」→ 本地通道调 record_reply 记分表、deliver_reply 推一行给主会话；
# 主会话 forward 转给接收方（跟进 Agent / 执行会话）；接收方 ack 回执（只写分表，不推送）；
# 派发确认、角色管理两类由主会话自己执行后 handled。和 pending.md 共用一把锁、一套编号。

REPLIES = ROOT / "runs" / "replies.md"
REPLY_COLS = list(cc.REPLY_COLUMNS)
REPLY_TAG = " · 回复】"                       # 推给主会话的回复行：【assistant 消息 · 回复】…
FORWARD_PREFIX = "【assistant 回复 · 来自你】"  # 主会话转给接收方的一行
MAIN_PAGES = ("派发确认", "角色管理")         # 这两页的回复由主会话自己执行，不转
RECEIVER_AGENTS = ("claude", "codex", "opencode")
FOLLOW_LINE_RE = re.compile(r"跟进 Agent：Orca 终端 handle\s+(\S+)")


def device_id():
    """本机设备指纹（device.py，同一进程只算一次）；算不出返回空串。"""
    global _DEVICE
    if _DEVICE is None:
        try:
            import device
            _DEVICE = device.fingerprint()
        except Exception:  # noqa: BLE001 —— 设备识别失败不能挡住记回复
            _DEVICE = ""
    return _DEVICE


_DEVICE = None


def reply_text(s, n=cc.REPLY_MAX_CHARS):
    """回复正文的一行：在 cell() 之上再把【】换成［］、去掉分隔符 ⏎，正文里伪造的消息行就只是文字。"""
    s = str(s or "").replace("⏎", " ").replace("【", "［").replace("】", "］")
    return cell(s, n)


def blank_replies():
    tpl = (ROOT / "templates" / "replies.md").read_text(encoding="utf-8")
    return re.sub(r"^updated: .*$", f"updated: {now('%Y-%m-%d')}", tpl, count=1, flags=re.M)


def read_replies(text=None):
    text = text if text is not None else (REPLIES.read_text(encoding="utf-8") if REPLIES.exists() else blank_replies())
    rows = []
    for l in text.splitlines():
        cells = [c.strip() for c in l.strip().strip("|").split("|")]
        if l.startswith("|") and len(cells) == len(REPLY_COLS) and re.match(r"^N\d{4}-\d+$", cells[0]):
            rows.append(dict(zip(REPLY_COLS, cells)))
    return text, rows


def write_replies(text, rows):
    table = "| " + " | ".join(REPLY_COLS) + " |\n|" + "---|" * len(REPLY_COLS) + "\n" + "".join(
        "| " + " | ".join(r.get(c, "") for c in REPLY_COLS) + " |\n" for r in rows)
    head = text.split("\n| " + REPLY_COLS[0], 1)[0].rstrip() + "\n\n"
    text = re.sub(r"^updated: .*$", f"updated: {now('%Y-%m-%d')}", head + table, count=1, flags=re.M)
    write_atomic(REPLIES, text)


def next_id(pending_rows, reply_rows, day=None):
    """两张表共用一套编号：N<月日>-<序号>，取当天最大序号 + 1。调用方要先拿锁。"""
    day = day or now("%m%d")
    used = [int(r["编号"].split("-")[1]) for r in list(pending_rows) + list(reply_rows)
            if r["编号"].startswith(f"N{day}-")]
    return f"N{day}-{1 + max(used or [0]):02d}"


# 状态只往前走：已收到回执 / 主会话已处理之后，晚到的「已送达 / 已转给」不能把它改回去（转不到 → 重开跟进再转可以）
_STATUS_RANK = {"unknown": 0, "waiting": 0, "delivered": 1, "forwarded": 2, "lost": 2, "acked": 3, "handled": 3}


def _rank(label):
    return _STATUS_RANK.get(cc.reply_status_of(label or "")[0], 0)


def update_reply(mid, **changes):
    """持锁改一行；「状态」只在不倒退时才改（R6：快速回执先到、转发完成后写的状态不能覆盖它）。"""
    with Lock():
        text, rows = read_replies()
        hit = next((r for r in rows if r["编号"] == mid), None)
        if hit:
            if "状态" in changes and _rank(cell(changes["状态"], 520)) < _rank(hit.get("状态")):
                changes = {k: v for k, v in changes.items() if k != "状态"}
            hit.update({k: cell(v, 520) for k, v in changes.items()})
            write_replies(text, rows)
        return hit


def find_reply(mid):
    return next((r for r in read_replies()[1] if r["编号"] == mid), None)


def _said(row):
    quick, body = row.get("快捷评价", ""), row.get("正文", "")
    if row.get("页") in MAIN_PAGES:
        n = len([x for x in body.split(cc.REPLY_LINE_JOINER.strip()) if x.strip()]) if body else 0
        return f"{row.get('页')} {n} 条指令（python tools/notify.py show {row['编号']} 看原文）"
    return f"{quick}：{body}" if quick and body else (quick or body)


def reply_line(row):
    """推给主会话的一行：【assistant 消息 · 回复】项目：事项 · 快捷评价：正文 · 关联 … · 编号 …"""
    assoc = row.get("关联") or "—"
    return no_control(f"{PREFIX}{REPLY_TAG}{row['事项']} · {_said(row)} · 关联 {assoc} · 编号 {row['编号']}")


def forward_line(row):
    py = (ROOT / "tools" / "notify.py").as_posix()
    return no_control(f"{FORWARD_PREFIX}{row['事项']} · {_said(row)} · 关联 {row.get('关联') or '—'} · 编号 {row['编号']}"
                      f" · 收到后回执：python {py} ack {row['编号']} --text \"<收到了、打算怎么办>\"")


def record_reply(*, page, item, association, quick="", text="", lines=None, device=None):
    """记一条回复（等主会话）。返回 (行, 是否去重命中)：同设备同卡片同样内容 10 秒内只算一条。"""
    label = cc.REPLY_PAGE_LABELS.get(page, page)
    if label not in cc.REPLY_PAGE_LABELS.values():
        raise ValueError("未知页面")
    # 派发确认 / 角色管理的指令行已由通道按 check_console_command 校验过，保留【控制台指令】前缀
    body = (cc.REPLY_LINE_JOINER.join(cell(str(l).replace("⏎", " "), 600) for l in lines)
            if lines else reply_text(text))
    dev = device if device is not None else device_id()
    row = {"时间": now("%Y-%m-%d %H:%M:%S"), "设备": cell(dev or "—", 64), "页": label,
           "事项": cell(item, 80), "关联": cell(association or "—", 300), "快捷评价": cell(quick or "", 30),
           "正文": body, "送达": "待推送", "转给": "—", "状态": cc.REPLY_STATUS_LABELS["waiting"], "回执": "—"}
    with Lock():
        text_r, rows = read_replies()
        cutoff = datetime.now().timestamp() - cc.REPLY_DEDUPE_SECONDS
        for r in reversed(rows):
            same = all(r[k] == row[k] for k in ("设备", "页", "事项", "关联", "快捷评价", "正文"))
            try:
                fresh = datetime.strptime(r["时间"], "%Y-%m-%d %H:%M:%S").timestamp() >= cutoff
            except ValueError:
                fresh = False
            if same and fresh:
                return r, True
        _, pending_rows = read_rows()
        row["编号"] = next_id(pending_rows, rows)
        rows.append(row)
        write_replies(text_r, rows)
    return row, False


def _run_of(row):
    """回复关联的派发记录：关联是待处理编号就取那一行的详情，是路径就直接读。返回 (字段, 路径)。"""
    assoc = row.get("关联", "")
    if re.match(r"^N\d{4}-\d+$", assoc):
        hit = next((r for r in read_rows()[1] if r["编号"] == assoc), None)
        assoc = hit["详情"] if hit else ""
    if not assoc or assoc == "—":
        return {}, None
    f, p = run_fields(assoc)
    return (f, p) if p is not None and p.exists() else ({}, None)


def _pending_type(row):
    """回复关联的是待处理消息时，那条消息的类型（完成 / 要拍板 / 卡住）；关联是运行记录时为 None。"""
    assoc = row.get("关联", "")
    if not re.match(r"^N\d{4}-\d+$", assoc):
        return None
    hit = next((r for r in read_rows()[1] if r["编号"] == assoc), None)
    return hit["类型"] if hit else None


def receiver_of(row):
    """转给谁（和 console_data 给卡片的「送往」同一套规则，PRD v1.1 4.2）：

    交付看板 → follower；单任务配了跟进 → 正文里的跟进 handle；单任务没配跟进 → 执行会话 handle，
    但**待验收**（完成）没配跟进、或待处理消息找不到派发记录 → 主会话自己处理（main=True，不转）。
    """
    kind = _pending_type(row)
    f, path = _run_of(row)
    if path is None:
        if kind is not None:
            return {"handle": "", "label": cc.REPLY_RECEIVER_MAIN, "reason": "", "main": "找不到这件事的派发记录"}
        return {"handle": "", "label": "", "reason": "找不到这件事的派发记录"}
    if f.get("kind") == "delivery":
        follower, label = f.get("follower", ""), cc.REPLY_RECEIVER_FOLLOW
        missing = "看板里没有记跟进 Agent 的终端"
    else:
        m = FOLLOW_LINE_RE.search(path.read_text(encoding="utf-8"))
        if m and m.group(1) not in ("?", "''"):
            follower, label, missing = m.group(1), cc.REPLY_RECEIVER_FOLLOW, ""
        elif kind == "完成":
            return {"handle": "", "label": cc.REPLY_RECEIVER_MAIN, "reason": "", "main": "没有跟进的单任务验收", "fields": f}
        else:
            follower, label = f.get("handle", ""), "执行会话"
            missing = "记录里没有执行会话的终端"
    dev = (f.get("device") or "").strip()
    if dev and device_id() and dev != device_id():
        return {"handle": "", "label": "", "reason": f"接收方在另一台设备（{dev}）上"}
    return {"handle": follower, "label": label, "reason": "" if follower else missing, "fields": f}


def deliver_reply(row, *, wait=WAIT, quick_check=False):
    """推回复给主会话（先找派发这件事的主会话，再找最近发过话的）。返回送达列文字。"""
    f, _ = _run_of(row)
    try:
        result = deliver(row, f, wait=0 if quick_check else wait, line=reply_line(row))
    except Exception as e:  # noqa: BLE001 —— 推送失败不能让通道报错
        result = f"未推送：{cell(e, 80)}"
    changes = {"送达": result}
    if result.startswith("已推送"):
        changes["状态"] = cc.REPLY_STATUS_LABELS["delivered"]
    try:
        update_reply(row["编号"], **changes)
    except LockBusy:
        pass  # 回复已记进分表；状态没改成，主会话收到这一行时 prompt_hook 会补成「已送达」
    return result


def replies_waiting(device=None):
    """本机发的、还没送到主会话的回复（等主会话 / 待转达）。"""
    dev = device if device is not None else device_id()
    return [r for r in read_replies()[1]
            if r["设备"] == dev and r["状态"].startswith(cc.REPLY_STATUS_LABELS["waiting"])]


def _push_receiver(handle, line, wait=WAIT):
    """推一行给接收方（Claude Code / Codex / OpenCode）。返回 (ok / busy / gone, 说明)。"""
    state, reason = input_state(handle, agents=RECEIVER_AGENTS)
    deadline = time.time() + wait
    while state == "busy" and time.time() < deadline:
        time.sleep(4)
        state, reason = input_state(handle, agents=RECEIVER_AGENTS)
    if state != "ok":
        return state, reason
    res = orca("terminal", "send", "--terminal", handle, "--text", line, "--enter", "--wait-submit", "10")
    if not res.get("ok"):
        return "busy", f"发送失败 {cell(res.get('error') or {}, 80)}"
    return "ok", ""


def _forward_pending_path(mid: str) -> Path:
    """一个编号一份待补记（R8）：存、取、删都只碰自己的文件，几条同时补记失败也不会互相覆盖。"""
    if not re.fullmatch(r"N\d{4}-\d+", mid or ""):
        raise ValueError(f"不是回复编号：{mid!r}")
    return LOCK.parent / "forward-pending" / f"{mid}.json"


def _remember_forward(mid: str, changes: dict) -> bool:
    """待补记的状态存到 inbox/logs/forward-pending/<编号>.json（不进 git，原子写）：下次 --record-only 只补表，不再推送。"""
    try:
        write_atomic(_forward_pending_path(mid), json.dumps(changes, ensure_ascii=False, indent=1))
        return True
    except (OSError, ValueError):
        return False


def _recall_forward(mid: str) -> dict | None:
    try:
        data = json.loads(_forward_pending_path(mid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data else None


def _forget_forward(mid: str) -> None:
    try:
        _forward_pending_path(mid).unlink()
    except (OSError, ValueError):
        pass


def record_forward(mid: str, changes: dict) -> bool:
    """转发有了结果之后补记状态（R7）：拿不到锁就再试几次；还拿不到就存一份待补记。

    推送成功的不能再让调用方「重跑一次 forward」——那会重复推送；补记用 ``forward --record-only``。
    提示按真实结果说（R9）：转不到的没推送出去，只说「转不到」这个状态待补记。
    """
    for attempt in range(FORWARD_UPDATE_ATTEMPTS):
        try:
            update_reply(mid, **changes)
            _forget_forward(mid)
            return True
        except LockBusy:
            if attempt + 1 < FORWARD_UPDATE_ATTEMPTS:
                time.sleep(FORWARD_UPDATE_PAUSE)
    saved = _remember_forward(mid, changes)
    py = (ROOT / "tools" / "notify.py").as_posix()
    status = str(changes.get("状态", ""))
    if status.startswith(cc.REPLY_STATUS_LABELS["lost"]):
        head = f"⚠ {mid} 转不到（没有推送出去），「{status}」这个状态还没记上（清单正被另一个会话写）"
        tail = (f"：稍后跑 python {py} forward {mid} --record-only 补记" if saved
                else "，待补记也没存上：稍后再跑一次 forward（接收方还不在就会再报转不到）")
    else:
        head = (f"⚠ {mid} 已经推送给接收方，但状态没记上（清单正被另一个会话写）："
                f"**不要再跑 forward**（会重复推送）")
        tail = (f"，稍后跑 python {py} forward {mid} --record-only 只补状态" if saved
                else f"，待补记也没存上：请主会话手动把 {mid} 记成「{status}」")
    print(head + tail)
    return False


def cmd_forward(a):
    row = find_reply(a.id)
    if not row:
        sys.exit(f"回复分表里没有 {a.id}")
    if row["设备"] != (device_id() or "—"):
        sys.exit(f"{a.id} 是另一台设备（{row['设备']}）发的，只由那台设备转达（这里只显示状态）")
    if row["页"] in MAIN_PAGES:
        sys.exit(f"{a.id} 是{row['页']}的指令，由主会话自己执行：python tools/notify.py show {a.id}，执行完 handled")
    if getattr(a, "record_only", False):
        saved = _recall_forward(a.id)
        if not saved:
            print(f"{a.id} 没有待补记的转发状态（要么已经记上了，要么没转过）：python tools/notify.py show {a.id} 看状态")
            return
        if not record_forward(a.id, saved):
            print(f"{a.id} 还是没补上（清单正被另一个会话写）：过几秒再跑一次同一命令")
            return
        row = find_reply(a.id)
        print(f"{a.id} 补记完成：{row['状态']}" if row else f"{a.id} 没有对应行")
        return
    rec = receiver_of(row)
    if rec.get("main"):
        # 没有跟进的单任务验收（或找不到派发记录）：主会话自己处理，不转（PRD v1.1 4.2，R5）
        update_reply(a.id, 转给="主会话自己")
        print(f"{a.id} 由主会话自己处理（{rec['main']}）：按回复内容处理（验收就记验收结果），"
              f"做完 python tools/notify.py handled {a.id} --note \"…\"（会一并把原消息标已处理）")
        return
    if rec["reason"]:
        update_reply(a.id, 状态=f"{cc.REPLY_STATUS_LABELS['lost']}：{rec['reason']}")
        print(f"转不到：{rec['reason']}。告诉你，给「重开跟进接着做 / 我自己处理」两个选项")
        return
    if not shutil.which("orca"):
        sys.exit("没有 orca 命令：转不了，先留在分表里")
    state, reason = _push_receiver(rec["handle"], forward_line(row), wait=a.wait)
    if state == "ok":
        recorded = record_forward(a.id, {"转给": f"{rec['label']}（{rec['handle']}）",
                                         "状态": f"{cc.REPLY_STATUS_LABELS['forwarded']} {now('%H:%M')}"})
        print(f"✅ 已转给{rec['label']}（{rec['handle']}）" if recorded
              else f"✅ 已推送给{rec['label']}（{rec['handle']}）：分表还没记上，按上面的提示补记，不要再跑 forward")
    elif state == "gone":
        recorded = record_forward(a.id, {"转给": f"{rec['label']}（{rec['handle']}）",
                                         "状态": f"{cc.REPLY_STATUS_LABELS['lost']}：接收方会话不在了"})
        print(f"转不到：{rec['label']}的会话不在了（{reason}）。告诉你，给「重开跟进接着做 / 我自己处理」两个选项"
              + ("" if recorded else "（分表还没记上，按上面的提示补记）"))
    else:
        print(f"ℹ 暂时推不进去（{reason}），回复还在分表里，稍后再 forward")


def cmd_ack(a):
    row = find_reply(a.id)
    if not row:
        sys.exit(f"回复分表里没有 {a.id}")
    update_reply(a.id, 回执=f"{now('%m-%d %H:%M')} {cell(a.text, 200)}", 状态=cc.REPLY_STATUS_LABELS["acked"])
    assoc = row.get("关联", "")
    if re.match(r"^N\d{4}-\d+$", assoc):
        hit = next((r for r in read_rows()[1] if r["编号"] == assoc), None)
        if hit and hit["状态"] == "待处理":
            try:
                update(assoc, 状态=f"已处理 {now('%m-%d %H:%M')}：回执 {a.id}")
            except LockBusy:
                # 回执已经记上了：重跑一次 ack 只会重写同样的内容，没有外部副作用
                print(f"⚠ {a.id} 回执已记上，但原消息没标已处理（清单正被另一个会话写）：稍后再跑一次 ack 补上")
    print(f"{a.id} 已回执（只写分表，不推送）")


def cmd_handled(a):
    hit = update_reply(a.id, 状态=f"{cc.REPLY_STATUS_LABELS['handled']} {now('%H:%M')}" + (f"：{a.note}" if a.note else ""),
                       转给="主会话自己")
    if hit:
        # 回复关联的待处理消息随之收尾（主会话自己记验收、或「我自己处理」转不到的回复）
        assoc = hit.get("关联", "")
        if re.match(r"^N\d{4}-\d+$", assoc):
            orig = next((r for r in read_rows()[1] if r["编号"] == assoc), None)
            if orig and orig["状态"] == "待处理":
                try:
                    update(assoc, 状态=f"已处理 {now('%m-%d %H:%M')}：回复 {a.id}")
                except LockBusy:
                    print(f"⚠ {a.id} 已处理记上了，但原消息没标已处理：稍后再跑一次 handled 补上（不会重复做别的）")
    print(f"{a.id} 已处理" if hit else f"回复分表里没有 {a.id}")


def cmd_show(a):
    row = find_reply(a.id)
    if not row:
        sys.exit(f"回复分表里没有 {a.id}")
    print(f"{row['编号']} · {row['时间']} · {row['页']} · {row['事项']} · 关联 {row['关联']} · 状态 {row['状态']}")
    if row["快捷评价"]:
        print(f"快捷评价：{row['快捷评价']}")
    for line in (row["正文"].split(cc.REPLY_LINE_JOINER.strip()) if row["正文"] else []):
        if line.strip():
            print(line.strip())


def cmd_replies(a):
    rows = read_replies()[1]
    if a.waiting:
        rows = replies_waiting()
    elif not a.all:
        done = tuple(cc.REPLY_STATUS_LABELS[k] for k in ("acked", "handled"))
        rows = [r for r in rows if not r["状态"].startswith(done)]
    if not rows:
        print("没有要处理的指挥台回复")
        return
    for r in rows:
        print(f"- {r['编号']} [{r['状态']}] {r['时间'][5:16]} {r['页']} · {r['事项']} · {_said(r)} · 关联 {r['关联']}\n"
              f"  设备 {r['设备']} · {r['送达']} · 转给 {r['转给']} · 回执 {r['回执']}")


def main():
    setup_stdio()
    ap = argparse.ArgumentParser(description="会话间通信：回报主会话")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send")
    s.add_argument("--type", required=True, choices=TYPES)
    s.add_argument("--item", help="项目：事项")
    s.add_argument("--text", required=True, help="一句话")
    s.add_argument("--detail", help="详情的路径（会转成绝对路径）；默认是运行记录")
    s.add_argument("--run", help="运行记录或交付看板：从里面读回报给谁、项目和事项")
    s.add_argument("--source", help="来源，默认按运行记录里的角色")
    s.add_argument("--no-push", action="store_true", help="只记清单不推送（主会话自己记待办时用）")
    p = sub.add_parser("list"); p.add_argument("--all", action="store_true")
    p = sub.add_parser("done"); p.add_argument("id"); p.add_argument("--note")
    p = sub.add_parser("target"); p.add_argument("--run")
    p = sub.add_parser("retry"); p.add_argument("id"); p.add_argument("--run")  # 内部用：后台补推
    p = sub.add_parser("replies"); p.add_argument("--waiting", action="store_true"); p.add_argument("--all", action="store_true")
    p = sub.add_parser("show"); p.add_argument("id")
    p = sub.add_parser("forward"); p.add_argument("id"); p.add_argument("--wait", type=int, default=WAIT)
    p.add_argument("--record-only", action="store_true", help="只补记已经推送成功的转发状态，不再推送（拿不到锁时按提示用）")
    p = sub.add_parser("ack"); p.add_argument("id"); p.add_argument("--text", required=True)
    p = sub.add_parser("handled"); p.add_argument("id"); p.add_argument("--note")
    a = ap.parse_args()
    try:
        globals()[f"cmd_{a.cmd}"](a)
    except LockBusy as e:
        sys.exit(f"{e}（没有写入，过几秒再跑一次）")


if __name__ == "__main__":
    main()
